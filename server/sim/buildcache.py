"""Build-side machinery for ``runner.build_record`` (sim build
speed): a content-addressed, bounded, single-flight on-disk cache, and a
bounded, memory-aware helper that runs the undisrupted twin in a second
process while the disrupted run simulates in this one.

Nothing here can change a byte of any record: every cached value is the
stored copy of what the pure computation returned, every cache key
carries a fingerprint of the model source and of every input, and every
failure (a corrupt file, an unwritable directory, a worker that dies or
runs out of memory, a box without spare memory or cores) degrades to the
plain in-process computation.

Cache files
-----------
``<cache_dir>/<kind>-<key>.json`` where ``key`` is the first 32 hex of
SHA-256 over the canonical key payload (derive and twin; see runner).
Writes go to a unique temp name and are renamed into place (readers see
the old file or the new one, never half of one). A file that fails to
parse or whose envelope does not match its name is a miss. The cache is
bounded in entries and in bytes per kind (least recently used out; a hit
refreshes the mtime). Concurrent builders of the same key take an
exclusive ``flock`` on ``<file>.lock``: one computes, the others wait
and then read its result (single flight across threads and processes).

Parallel twin
-------------
``TwinProcess`` runs ``twin_worker`` in a child interpreter (never
``fork``: the API process is multi-threaded). It is used only when
cores and memory allow, at most ``SUPERMAP_SIM_PARALLEL_MAX`` at a time
process-wide, and the parent falls back to computing the twin itself on
any failure, so the outputs are identical either way.

Environment (all optional)
--------------------------
SUPERMAP_SIM_PARALLEL        "0" disables the child process (default on)
SUPERMAP_SIM_PARALLEL_MAX    concurrent child processes (default
                             min(2, cpus - 1), 0 disables)
SUPERMAP_SIM_MEM_CEILING_MB  spare memory that must remain AFTER the
                             child's estimated peak (default 2048)
SUPERMAP_SIM_CACHE_MAX_MB    byte budget per cache kind (default 512)
"""
from __future__ import annotations

import json
import os
import pickle
import subprocess
import sys
import threading
import uuid
from pathlib import Path

try:  # POSIX advisory locks; without them single flight is per process
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX
    fcntl = None  # type: ignore[assignment]

CACHE_KEEP = {"derive": 6, "twin": 6}
DEFAULT_CACHE_MAX_MB = 512


# --- Environment knobs -------------------------------------------------

def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def cache_max_bytes() -> int:
    return max(1, _env_int("SUPERMAP_SIM_CACHE_MAX_MB",
                           DEFAULT_CACHE_MAX_MB)) * 1024 * 1024


def cpu_count() -> int:
    try:
        return len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        return os.cpu_count() or 1


def parallel_max() -> int:
    if os.environ.get("SUPERMAP_SIM_PARALLEL", "1") == "0":
        return 0
    return max(0, _env_int("SUPERMAP_SIM_PARALLEL_MAX",
                           min(2, cpu_count() - 1)))


def mem_ceiling_mb() -> int:
    return max(0, _env_int("SUPERMAP_SIM_MEM_CEILING_MB", 2048))


def available_memory_mb() -> float | None:
    """MiB this process tree could still allocate: the smaller of the
    host's MemAvailable and the cgroup headroom. None when unknown."""
    best = None
    try:
        with open("/proc/meminfo", "r", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    best = int(line.split()[1]) / 1024.0
                    break
    except (OSError, ValueError, IndexError):
        pass
    for base in ("/sys/fs/cgroup",):
        try:
            with open(f"{base}/memory.max", "r", encoding="ascii") as fh:
                raw = fh.read().strip()
            if raw != "max":
                with open(f"{base}/memory.current", "r",
                          encoding="ascii") as fh:
                    cur = int(fh.read().strip())
                head = (int(raw) - cur) / (1024.0 * 1024.0)
                best = head if best is None else min(best, head)
        except (OSError, ValueError):
            pass
    return best


# Replaceable: () -> MiB available, or None.
memory_probe = available_memory_mb


# --- Cache files ---------------------------------------------------------

def read_json(path: Path):
    """The parsed JSON file, or None for a missing/partial/corrupt one."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError, RecursionError):
        return None


def touch(path: Path) -> None:
    try:
        os.utime(path, None)
    except OSError:
        pass


def write_json_atomic(path: Path, kind: str, text: str) -> bool:
    """Write ``text`` to ``path`` atomically, then enforce the bounds.
    Returns False when the file could not be written."""
    tmp = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(
            f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
        tmp.replace(path)
    except OSError:
        if tmp is not None:
            try:
                tmp.unlink()
            except OSError:
                pass
        return False
    evict(path.parent, kind, keep=path)
    return True


def evict(cache_dir: Path, kind: str, keep: Path | None = None) -> None:
    """Bound ``kind`` entries: at most CACHE_KEEP[kind] files and
    cache_max_bytes() bytes, least recently used first. The file just
    written (``keep``) is never the victim. Stale temp files from dead
    writers (older than an hour) and orphaned locks are removed."""
    try:
        entries = []
        for p in cache_dir.glob(f"{kind}-*.json"):
            try:
                st = p.stat()
            except OSError:
                continue
            entries.append((st.st_mtime, st.st_size, p))
        entries.sort(key=lambda e: e[0], reverse=True)
        budget = cache_max_bytes()
        kept, used = 0, 0
        for _mt, size, p in entries:
            fits = kept < CACHE_KEEP.get(kind, 6) and \
                (used + size <= budget or p == keep)
            if fits or p == keep:
                kept += 1
                used += size
                continue
            try:
                p.unlink()
            except OSError:
                pass
            try:
                p.with_name(p.name + ".lock").unlink()
            except OSError:
                pass
        _sweep_stale(cache_dir, kind)
    except OSError:
        pass


STALE_AFTER_S = 3600


def _sweep_stale(cache_dir: Path, kind: str) -> None:
    """Remove temp files left by dead writers and lock files with no entry
    behind them, once they are older than an hour (a live writer or builder
    touches them far more recently)."""
    import time
    cutoff = time.time() - STALE_AFTER_S
    for pat in (f"{kind}-*.tmp", f"{kind}-*.json.lock"):
        for p in cache_dir.glob(pat):
            try:
                if p.stat().st_mtime >= cutoff:
                    continue
                if p.name.endswith(".lock") and p.with_name(p.name[:-5]).exists():
                    continue
                p.unlink()
            except OSError:
                pass


# --- Single flight ---------------------------------------------------------

_THREAD_LOCKS: dict = {}
_THREAD_LOCKS_GUARD = threading.Lock()


class FlightLock:
    """Exclusive lock on one cache entry. Blocks until the previous
    holder (any thread or process) releases it. ``release`` may be
    called from any thread and is idempotent."""

    def __init__(self, path: Path):
        self.path = path.with_name(path.name + ".lock")
        self._fd = None
        self._tlock = None
        self._held = False
        self._guard = threading.Lock()

    def acquire(self) -> "FlightLock":
        if fcntl is not None:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
                fcntl.flock(self._fd, fcntl.LOCK_EX)
                self._held = True
                return self
            except OSError:
                if self._fd is not None:
                    try:
                        os.close(self._fd)
                    except OSError:
                        pass
                    self._fd = None
        with _THREAD_LOCKS_GUARD:
            lock = _THREAD_LOCKS.setdefault(str(self.path),
                                            threading.Lock())
        lock.acquire()
        self._tlock = lock
        self._held = True
        return self

    def release(self) -> None:
        with self._guard:
            if not self._held:
                return
            self._held = False
            if self._fd is not None:
                try:
                    if fcntl is not None:
                        fcntl.flock(self._fd, fcntl.LOCK_UN)
                finally:
                    try:
                        os.close(self._fd)
                    except OSError:
                        pass
                    self._fd = None
            if self._tlock is not None:
                self._tlock.release()
                self._tlock = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()
        return False


# --- Parallel twin -----------------------------------------------------------

_SLOTS = threading.Lock()
_ACTIVE = [0]


def _take_slot() -> bool:
    with _SLOTS:
        if _ACTIVE[0] >= parallel_max():
            return False
        _ACTIVE[0] += 1
        return True


def _give_slot() -> None:
    with _SLOTS:
        _ACTIVE[0] = max(0, _ACTIVE[0] - 1)


def estimate_child_mb(horizon_days: float) -> float:
    """Peak resident set of the twin worker: about 400 MiB plus 25 MiB
    per simulated day (measured: 30 day-ticks peak about 500 MiB)."""
    return 400.0 + 25.0 * max(0.0, horizon_days)


def parallel_refusal(horizon_days: float) -> str | None:
    """None when a child process may start now, else the reason it may
    not (reported in ``stats``; the caller then runs sequentially)."""
    if parallel_max() <= 0:
        return "disabled"
    if cpu_count() < 2:
        return "single_cpu"
    avail = memory_probe()
    if avail is None:
        return "memory_unknown"
    need = estimate_child_mb(horizon_days) + mem_ceiling_mb()
    if avail < need:
        return "memory"
    return None


def _package_root_and_name() -> tuple[str, str]:
    """(directory to put on PYTHONPATH, dotted package of this module)."""
    pkg = __package__ or "sim"
    depth = len(pkg.split("."))
    # <root>/<pkg as a path>/buildcache.py: parents[0] is the package
    # directory, parents[depth] the directory holding its top package.
    return str(Path(__file__).resolve().parents[depth]), pkg


class TwinProcess:
    """The undisrupted twin, running in a child interpreter.

    ``start`` returns None (and runs nothing) unless a slot, cores and
    memory are available. ``result`` blocks for the child's
    ``arrival_events`` and returns None on any failure (the caller then
    computes the twin itself). ``on_done`` (optional) is called from
    the waiting thread with the result as soon as the child finishes,
    even if nobody has asked yet - the runner uses it to write the
    cache and release the single-flight lock early."""

    def __init__(self, proc, thread, box):
        self._proc, self._thread, self._box = proc, thread, box

    @classmethod
    def start(cls, payload: dict, horizon_days: float, on_done=None):
        if parallel_refusal(horizon_days) is not None:
            return None
        if not _take_slot():
            return None
        try:
            root, pkg = _package_root_and_name()
            env = dict(os.environ)
            env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")
            env["SUPERMAP_TWIN_AS_MB"] = str(int(
                estimate_child_mb(horizon_days) * 4 + 2048))
            data = pickle.dumps(payload, protocol=5)
            proc = subprocess.Popen(
                [sys.executable, "-m", f"{pkg}.twin_worker"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, env=env, cwd=root,
                close_fds=True)
        except Exception:
            _give_slot()
            return None
        box: dict = {"value": None}

        def wait() -> None:
            try:
                out, _ = proc.communicate(data)
                if proc.returncode == 0 and out:
                    value = pickle.loads(out)
                    if isinstance(value, dict):
                        box["value"] = value
            except Exception:
                box["value"] = None
            finally:
                _give_slot()
            if on_done is not None:
                try:
                    on_done(box["value"])
                except Exception:
                    pass

        thread = threading.Thread(target=wait, daemon=True,
                                  name="sim-twin-wait")
        thread.start()
        return cls(proc, thread, box)

    def result(self):
        self._thread.join()
        return self._box["value"]

    def cancel(self) -> None:
        try:
            self._proc.kill()
        except Exception:
            pass
        self._thread.join()
