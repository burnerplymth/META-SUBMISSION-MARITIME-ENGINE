"""SimStore — JSON run records under data/sim_runs/.

Mirrors the RunStore discipline (server/api/store.py): one file per
run, atomic tmp+replace writes, traversal-safe ids (an id is an opaque
name, never a path — anything that could traverse resolves to "no such
run" on reads, never a bare 500). A record is journal-complete: journal
core + derivation + segments + node_series + pinned weather snapshot
replay the run byte-identically with no other input.

``sim_run_id = "sim_" + sha256(canonical journal core)[:16]`` where the
journal core is exactly {graph_hash, graph_version, disruptions
(effective specs), tick, ticks, seed, start_at,
weather_snapshot_hash|null}.

In-process LRU over loaded records. A stored
record is IMMUTABLE for its id: the id is a content hash of the
journal core, and ``build_record`` is a deterministic function of
that core plus the graph it pins (by ``graph_hash``) and the weather
snapshot it pins (by ``weather_snapshot_hash``) — so an id can never
name a different record, a re-POST of an existing id returns the
stored record verbatim, and nothing ever rewrites a record file
after creation. ``load`` therefore serves repeats from memory
instead of re-reading + re-parsing the (75-177 MB) JSON file on
every request. The cache is bounded at RECORD_CACHE_MAX records:
a parsed record costs roughly 4-5x its file size in RAM (measured
~330 MB for the 75 MB demo record), so 4 slots bound
the worst case at ~1.3 GB for records of that size (~3 GB if all
four slots held 177 MB records) — 4 covers the working
set the UI actually alternates between (live run, demo run, a
comparison/fork) without the thrash of a 1-2 slot cache. Returned
records are shared objects: callers must treat them as read-only,
exactly as they already treat the stored record.
"""
from __future__ import annotations

import hashlib
import json
import mmap
import os
import shutil
import threading
import time
import uuid
from collections import OrderedDict
from pathlib import Path

try:
    from engine.canonical import canonical_json, sha256_hex
except Exception:  # imported as server.sim.* from the repo root instead
    from server.engine.canonical import canonical_json, sha256_hex  # type: ignore


def derive_sim_run_id(journal_core: dict) -> str:
    return "sim_" + sha256_hex(canonical_json(journal_core))[:16]


# Loaded-record LRU bound (see module docstring for the
# memory arithmetic behind this number).
RECORD_CACHE_MAX = 4

# The count bound alone would let four
# 182 MB records (about 3 GB parsed) sit in a 2 GB machine, so the cache
# is ALSO bounded in bytes. A parsed record costs RECORD_RAM_FACTOR times
# its file; the budget is SUPERMAP_SIM_RECORD_CACHE_MB (default 1536, the
# four-slot worst case for the 75 MB demo-scale record; render.yaml sets
# a smaller figure). The newest record is always kept, so one record
# larger than the budget is still served (it is just the only one held).
RECORD_RAM_FACTOR = 4
DEFAULT_RECORD_CACHE_MB = 1536

# Eviction of old runs: when a new run would not fit, the least recently
# used UNPINNED runs are removed. The reserve is the head-room one new
# record needs (a run is 75-330 MB).
DEFAULT_EVICT_RESERVE_BYTES = 400 << 20
ACCESS_DIR = "_access"
PINNED_DIR = "_pinned"
_ACCESS_THROTTLE_S = 60.0

# Disk bounds for NEW records (see SimStore.storage_refusal).
DEFAULT_STORE_MAX_BYTES = 16 << 30
DEFAULT_MIN_FREE_BYTES = 64 << 20


def _env_bytes(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ[name]))
    except (KeyError, ValueError):
        return default


# ``save`` writes the record in chunks (no
# 178 MB transient string) and a small byte-offset index beside it.
INDEX_DIR = "_index"
INDEX_VERSION = 1
_FLUSH_BYTES = 1 << 20


def _json_text(value) -> str:
    return json.dumps(value, sort_keys=True)


def write_record(fh, record: dict):
    """Write ``json.dumps(record, sort_keys=True)`` to the binary file
    ``fh`` piece by piece. Returns (sha256 hex, size, sections,
    segments): the digest and length of the bytes written, the
    [start, end) byte span of each top-level value, and the span of
    each entity's list inside ``segments``. The output is exactly the
    one-shot encoding: same key order, same separators, same ASCII
    escapes (a dict is written as ``{`` + ``"key": value`` joined by
    ``, `` + ``}``, which is what the encoder emits); a record with
    non-string keys falls back to the one-shot encoder."""
    h = hashlib.sha256()
    buf: list[str] = []
    size = [0, 0]  # bytes written to fh, bytes pending in buf

    def put(text: str) -> None:
        buf.append(text)
        size[1] += len(text)
        if size[1] >= _FLUSH_BYTES:
            flush()

    def flush() -> None:
        if buf:
            data = "".join(buf).encode("ascii")
            fh.write(data)
            h.update(data)
            size[0] += len(data)
            buf.clear()
            size[1] = 0

    def pos() -> int:
        return size[0] + size[1]

    sections: dict = {}
    segments: dict = {}
    keys = list(record)
    if not all(type(k) is str for k in keys):
        put(_json_text(record))
        flush()
        return h.hexdigest(), size[0], {}, {}
    put("{")
    for i, key in enumerate(sorted(keys)):
        put(("" if i == 0 else ", ") + json.dumps(key) + ": ")
        start = pos()
        value = record[key]
        if key == "segments" and isinstance(value, dict) \
                and all(type(k) is str for k in value):
            put("{")
            for j, eid in enumerate(sorted(value)):
                put(("" if j == 0 else ", ") + json.dumps(eid) + ": ")
                a = pos()
                put(_json_text(value[eid]))
                segments[eid] = [a, pos()]
            put("}")
        else:
            put(_json_text(value))
        sections[key] = [start, pos()]
    put("}")
    flush()
    return h.hexdigest(), size[0], sections, segments


class SimStore:
    def __init__(self, runs_dir: str | Path):
        self.dir = Path(runs_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._record_cache: OrderedDict[str, dict] = OrderedDict()
        self._record_weight: dict[str, int] = {}
        self._touched: dict[str, float] = {}
        # Handlers run on worker threads (event-loop
        # offload): the LRU is guarded, and a cold load is single-flight
        # so N concurrent requests parse a 75-177 MB record once.
        self._cache_lock = threading.Lock()
        self._load_lock = threading.Lock()
        self._usage_lock = threading.Lock()
        self._usage: tuple[float, int] | None = None
        # Optional post-save callback (writes the per-run
        # metadata sidecar). Never part of the record; a failing hook
        # cannot fail a save.
        self.on_saved = None

    def cache_budget_bytes(self) -> int:
        try:
            mb = int(os.environ.get("SUPERMAP_SIM_RECORD_CACHE_MB",
                                    DEFAULT_RECORD_CACHE_MB))
        except (TypeError, ValueError):
            mb = DEFAULT_RECORD_CACHE_MB
        return max(1, mb) << 20

    def cache_bytes(self) -> int:
        """Estimated RAM held by the record cache (file size x factor)."""
        with self._cache_lock:
            return sum(self._record_weight.get(k, 0)
                       for k in self._record_cache)

    def _cache_put(self, sim_run_id: str, record: dict,
                   weight: int = 0) -> None:
        budget = self.cache_budget_bytes()
        with self._cache_lock:
            cache = self._record_cache
            cache[sim_run_id] = record
            self._record_weight[sim_run_id] = weight
            cache.move_to_end(sim_run_id)
            while len(cache) > 1 and (
                    len(cache) > RECORD_CACHE_MAX
                    or sum(self._record_weight.get(k, 0)
                           for k in cache) > budget):
                old, _ = cache.popitem(last=False)
                self._record_weight.pop(old, None)

    def _cache_get(self, sim_run_id: str) -> dict | None:
        with self._cache_lock:
            cached = self._record_cache.get(sim_run_id)
            if cached is not None:
                self._record_cache.move_to_end(sim_run_id)
        if cached is not None:
            self._touch_access(sim_run_id)
        return cached

    def clear_cache(self) -> None:
        """Drop every cached record. A cold build is the memory peak, so
        the caller frees the cache first; reads simply re-parse."""
        with self._cache_lock:
            self._record_cache.clear()
            self._record_weight.clear()

    # -- last access + pinning (eviction support) -------------------------
    def _access_path(self, sim_run_id: str) -> Path:
        return self.dir / ACCESS_DIR / sim_run_id

    def _touch_access(self, sim_run_id: str, force: bool = False) -> None:
        """Mark the run as used now (an empty marker file's mtime), at
        most once a minute per run unless forced. Never raises."""
        now = time.monotonic()
        last = self._touched.get(sim_run_id)
        if not force and last is not None and now - last < _ACCESS_THROTTLE_S:
            return
        self._touched[sim_run_id] = now
        try:
            p = self._access_path(sim_run_id)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.touch()
            os.utime(p, None)
        except (OSError, ValueError):
            pass

    def _pinned_dir(self) -> Path:
        return self.dir / PINNED_DIR

    def pin(self, sim_run_id: str) -> None:
        """Never evict this run (the demo run). A marker file, so it
        survives a restart; pinning an id that is not stored yet is fine
        (it protects the record the moment it is saved)."""
        self._path(sim_run_id)  # id validation
        d = self._pinned_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / sim_run_id).touch()

    def pinned_ids(self) -> set[str]:
        out = {x.strip() for x in
               os.environ.get("SUPERMAP_SIM_PINNED", "").split(",")
               if x.strip()}
        try:
            out.update(p.name for p in self._pinned_dir().iterdir())
        except OSError:
            pass
        return out

    def is_pinned(self, sim_run_id: str) -> bool:
        return sim_run_id in self.pinned_ids()

    def _last_access(self, sim_run_id: str, record_path: Path) -> float:
        best = 0.0
        for p in (self._access_path(sim_run_id), record_path):
            try:
                best = max(best, p.stat().st_mtime)
            except OSError:
                pass
        return best

    def evict_lru(self, need_bytes: int) -> int:
        """Remove least-recently-used unpinned runs (record, index and
        every sidecar named after the id) until at least ``need_bytes``
        are freed or none is left. Returns the bytes freed."""
        pinned = self.pinned_ids()
        try:
            entries = [p for p in self.dir.iterdir()
                       if p.is_file() and p.suffix == ".json"]
        except OSError:
            return 0
        runs = sorted((self._last_access(p.stem, p), p.stem, p)
                      for p in entries if p.stem not in pinned)
        freed = 0
        for _t, rid, p in runs:
            if freed >= need_bytes:
                break
            freed += self._remove_run(rid, p)
        if freed:
            with self._usage_lock:
                self._usage = None
        return freed

    def _remove_run(self, rid: str, record_path: Path) -> int:
        freed = 0
        with self._cache_lock:
            self._record_cache.pop(rid, None)
            self._record_weight.pop(rid, None)
        self._touched.pop(rid, None)
        victims = [record_path]
        try:
            for sub in self.dir.iterdir():
                if sub.is_dir() and sub.name not in ("_cache", PINNED_DIR):
                    victims.extend(sub.glob(f"{rid}.*"))
                    victims.append(sub / rid)
        except OSError:
            pass
        for v in victims:
            try:
                size = v.stat().st_size
                v.unlink()
                freed += size
            except OSError:
                pass
        return freed

    def _path(self, sim_run_id: str) -> Path:
        if (not isinstance(sim_run_id, str) or not sim_run_id
                or "/" in sim_run_id or "\\" in sim_run_id
                or ".." in sim_run_id or "\x00" in sim_run_id):
            raise ValueError("bad sim_run_id")
        p = self.dir / f"{sim_run_id}.json"
        if p.resolve().parent != self.dir.resolve():
            raise ValueError("bad sim_run_id")
        return p

    def exists(self, sim_run_id: str) -> bool:
        try:
            return self._path(sim_run_id).exists()
        except (ValueError, OSError):
            return False

    def storage_refusal(self) -> str | None:
        """None when a NEW record may be built, else the reason it may
        not. A stored record is 75-330 MB and any request body can name
        a new id (change the seed), so without a bound the unauthenticated
        POST /sim/run (and fork) could fill the volume the run store,
        ledger and alerts share. Two bounds, both env-tunable: the
        store's own bytes (SUPERMAP_SIM_STORE_MAX_BYTES, default 16 GiB)
        and the free space left on its volume
        (SUPERMAP_SIM_MIN_FREE_BYTES, default 64 MiB). Reads and
        existing ids are never refused.

        When a bound is reached the least recently used UNPINNED runs
        are evicted to make room for one more record
        (SUPERMAP_SIM_EVICT_RESERVE_BYTES, default 400 MiB); a pinned
        run (the demo run) is never evicted. The refusal is only for a
        store whose remaining runs are all pinned or that cannot be
        shrunk."""
        max_bytes = _env_bytes("SUPERMAP_SIM_STORE_MAX_BYTES",
                               DEFAULT_STORE_MAX_BYTES)
        min_free = _env_bytes("SUPERMAP_SIM_MIN_FREE_BYTES",
                              DEFAULT_MIN_FREE_BYTES)
        reserve = min(_env_bytes("SUPERMAP_SIM_EVICT_RESERVE_BYTES",
                                 DEFAULT_EVICT_RESERVE_BYTES),
                      max_bytes // 2)

        def measure() -> tuple[int, int, int | None]:
            try:
                free = shutil.disk_usage(self.dir).free
            except OSError:
                free = None
            used = self.stored_bytes()
            need = 0
            if used + reserve > max_bytes:
                need = used + reserve - max_bytes
            if free is not None and free < min_free + reserve:
                need = max(need, min_free + reserve - free)
            return need, used, free

        need, used, free = measure()
        if need > 0:
            self.evict_lru(need)
            need, used, free = measure()
        if free is not None and free < min_free:
            return (f"the volume holding the sim store has {free} bytes "
                    f"free; at least {min_free} are required to build a "
                    "new run" + self._pinned_note())
        if used >= max_bytes:
            return (f"the sim store holds {used} bytes, at its "
                    f"{max_bytes}-byte limit; existing runs are still "
                    "served" + self._pinned_note())
        return None

    def _pinned_note(self) -> str:
        n = len(self.pinned_ids())
        return (f"; the {n} pinned run(s) are never removed to make room"
                if n else "")

    def stored_bytes(self, ttl_s: float = 5.0) -> int:
        """Total bytes under the store directory (records, sidecars,
        caches), cached for ``ttl_s`` seconds so a burst of POSTs does
        not rescan the tree."""
        now = time.monotonic()
        with self._usage_lock:
            if self._usage is not None and now - self._usage[0] < ttl_s:
                return self._usage[1]
        total = 0
        for root, _dirs, files in os.walk(self.dir):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                except OSError:
                    pass
        with self._usage_lock:
            self._usage = (now, total)
        return total

    def save(self, record: dict) -> dict:
        # Per-writer unique temp name (pid + uuid), never the
        # shared deterministic .json.tmp.
        _path = self._path(record["sim_run_id"])
        tmp = _path.with_name(f"{_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        # ``dumps`` runs the C encoder; ``dump`` streams through the
        # pure-Python chunk encoder (~2.5x slower on a 178 MB record).
        # The record is written one top-level value (and one
        # entity's segment list) at a time with ``dumps`` on each piece,
        # which is the C encoder's own output for the whole dict (same
        # separators, key order and ASCII escapes: the bytes are
        # identical), without the
        # 178 MB transient string.
        try:
            with open(tmp, "wb") as fh:
                sha, size, sections, segments = write_record(fh, record)
        except BaseException:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise
        tmp.replace(self._path(record["sim_run_id"]))
        self._touch_access(record["sim_run_id"], force=True)
        with self._usage_lock:
            self._usage = None  # a new record changes the byte count now
        self._write_index(record["sim_run_id"], sha, size, sections,
                          segments)
        # Deliberately NOT cached here: save() writes with
        # sort_keys=True, so a record re-parsed from disk carries
        # sorted key order, while the caller's in-memory object
        # carries builder insertion order. Responses serialise dict
        # order, so caching the builder object would make this
        # process's GET bytes differ from a fresh process's — the
        # first load() after creation parses + caches the disk
        # object instead, keeping every process byte-identical.
        # Popping also covers a (contract-breaking) overwrite of an
        # already-cached id: the next load re-reads the new file.
        with self._cache_lock:
            self._record_cache.pop(record["sim_run_id"], None)
            self._record_weight.pop(record["sim_run_id"], None)
        if self.on_saved is not None:
            try:
                self.on_saved(record)
            except Exception:
                pass
        return record

    # -- Byte-offset index (lazy section reads) -------------------------
    def _index_path(self, sim_run_id: str) -> Path:
        return self.dir / INDEX_DIR / f"{sim_run_id}.json"

    def _write_index(self, sim_run_id: str, sha: str, size: int,
                     sections: dict, segments: dict) -> None:
        """Sidecar next to the record (never part of it): the SHA-256 and
        length of the file just written, the byte span of every
        top-level value and of every entity's segment list. Written
        after the record is in place; a failure only costs the lazy
        path (readers then parse the whole record, as before)."""
        try:
            p = self._index_path(sim_run_id)
            p.parent.mkdir(parents=True, exist_ok=True)
            st = self._path(sim_run_id).stat()
            tmp = p.with_name(f"{p.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
            tmp.write_text(json.dumps({
                "v": INDEX_VERSION, "sha256": sha, "size": size,
                "mtime_ns": st.st_mtime_ns, "sections": sections,
                "segments": segments}), encoding="utf-8")
            tmp.replace(p)
        except OSError:
            pass

    def index(self, sim_run_id: str) -> dict | None:
        """The record's index, or None when absent, unreadable or not
        describing the file now on disk (size and mtime must match)."""
        try:
            st = self._path(sim_run_id).stat()
            with open(self._index_path(sim_run_id), "r",
                      encoding="utf-8") as fh:
                idx = json.load(fh)
        except (ValueError, OSError):
            return None
        if not isinstance(idx, dict) or idx.get("v") != INDEX_VERSION \
                or idx.get("size") != st.st_size \
                or idx.get("mtime_ns") != st.st_mtime_ns \
                or not isinstance(idx.get("sections"), dict):
            return None
        return idx

    def ensure_index(self, record: dict) -> dict | None:
        """The index of a stored run, building it for a run saved without
        one. ``record`` is the loaded record of that run. The index
        is only written when re-encoding the record reproduces the file
        byte for byte (same length and SHA-256), so every offset in it
        is true; a file that is not the canonical encoding of its own
        content gets no index and callers keep reading it whole."""
        sim_run_id = record["sim_run_id"]
        idx = self.index(sim_run_id)
        if idx is not None:
            return idx
        try:
            path = self._path(sim_run_id)
            sink = NullSink()
            sha, size, sections, segments = write_record(sink, record)
            h = hashlib.sha256()
            with open(path, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            if size != path.stat().st_size or h.hexdigest() != sha:
                return None
            self._write_index(sim_run_id, sha, size, sections, segments)
        except (OSError, ValueError, TypeError):
            return None
        return self.index(sim_run_id)

    def read_sections(self, sim_run_id: str, keys) -> dict | None:
        """{key: parsed top-level value} for the named keys, reading
        only those byte ranges; None when there is no usable index (or
        a key is missing from it), so callers can fall back to
        ``load``. Values are fresh objects, not the shared cached
        record."""
        idx = self.index(sim_run_id)
        if idx is None:
            return None
        spans = idx["sections"]
        if any(k not in spans for k in keys):
            return None
        try:
            out = {}
            with open(self._path(sim_run_id), "rb") as fh:
                for k in keys:
                    a, b = spans[k]
                    fh.seek(a)
                    out[k] = json.loads(fh.read(b - a))
            return out
        except (ValueError, OSError):
            return None

    def segment_view(self, sim_run_id: str) -> "SegmentView | None":
        """Byte-level access to each entity's segment list, or None
        without a usable index."""
        idx = self.index(sim_run_id)
        if idx is None or not isinstance(idx.get("segments"), dict):
            return None
        try:
            return SegmentView(self._path(sim_run_id), idx["segments"])
        except (ValueError, OSError):
            return None

    def load(self, sim_run_id: str) -> dict | None:
        try:
            p = self._path(sim_run_id)
        except (ValueError, OSError):
            return None
        cached = self._cache_get(sim_run_id)
        if cached is not None:
            return cached
        try:  # absent ids answer at once, never queued behind a parse
            if not p.exists():
                return None
        except OSError:
            return None
        with self._load_lock:
            cached = self._cache_get(sim_run_id)
            if cached is not None:
                return cached
            with open(p, "r", encoding="utf-8") as fh:
                record = json.load(fh)
            try:
                weight = p.stat().st_size * RECORD_RAM_FACTOR
            except OSError:
                weight = 0
            self._cache_put(sim_run_id, record, weight)
            self._touch_access(sim_run_id)
            return record


class SegmentView:
    """Read-only, memory-mapped view of one record's per-entity segment
    lists. ``blob(eid)`` is the exact text the record holds for that
    entity (``bytes``), so two runs' entities can be compared without
    parsing either: equal text is equal data. Close when done."""

    def __init__(self, path: Path, spans: dict):
        self._fh = open(path, "rb")
        try:
            self._mm = mmap.mmap(self._fh.fileno(), 0,
                                 access=mmap.ACCESS_READ)
        except (ValueError, OSError):
            self._fh.close()
            raise
        self.spans = spans

    def eids(self):
        return self.spans.keys()

    def blob(self, eid: str):
        span = self.spans.get(eid)
        if span is None:
            return None
        return self._mm[span[0]:span[1]]

    def events(self, eid: str):
        blob = self.blob(eid)
        return None if blob is None else json.loads(blob)

    def close(self) -> None:
        try:
            self._mm.close()
        finally:
            self._fh.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class NullSink:
    """Binary file stand-in that keeps nothing (write_record hashes)."""

    def write(self, data: bytes) -> int:
        return len(data)
