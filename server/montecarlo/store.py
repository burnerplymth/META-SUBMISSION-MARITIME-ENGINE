"""One-JSON-file-per-job store for Monte Carlo jobs: the ensemble-statistics
store (traversal-safe ids, atomic tmp+replace writes, corrupt files read as
absent) plus ``delete``, a retention cap and a cheap listing. Its own
directory (data/montecarlo/ by default).

Retention. At most ``max_jobs`` job files are kept (default 200,
``SUPERMAP_MC_MAX_JOBS``); ``prune`` removes the oldest by modification time,
never a protected job (the manager protects every job that is still running or
queued). A pruned job is gone like a cancelled one: its id reads back as unknown
and the same request would run again under the same id.

Listing. A job file holds every simulation row and the place table (up to ~0.4 MB),
so listing by loading each file does not scale. Beside each job ``save`` writes a
small head (the record without rows, results, place table and series) under
``heads/<id>.json``; ``list_heads`` reads only those. A store written before heads
existed is backfilled on the first listing. Heads are derived data: losing them
costs one backfill, never a job.
"""
from __future__ import annotations

import json
import os
import uuid

try:  # dual-import convention
    from ensstats.store import EnsembleStatsStore
except Exception:  # imported as server.montecarlo.* from the repo root
    from server.ensstats.store import EnsembleStatsStore  # type: ignore

MAX_JOBS = 200
HEAVY_KEYS = ("rows", "results", "place_table", "series_raw")


def _env_cap(default: int) -> int:
    try:
        return max(1, int(os.environ.get("SUPERMAP_MC_MAX_JOBS", default)))
    except ValueError:
        return default


def _head_of(record: dict) -> dict:
    return {k: v for k, v in record.items() if k not in HEAVY_KEYS}


class MonteCarloStore(EnsembleStatsStore):
    def __init__(self, jobs_dir, max_jobs: int | None = None):
        super().__init__(jobs_dir)
        self.max_jobs = max_jobs if max_jobs is not None else _env_cap(MAX_JOBS)
        self.heads_dir = self.dir / "heads"

    # ------------------------------------------------------------ writes
    def _write_head(self, record: dict) -> None:
        try:
            self.heads_dir.mkdir(parents=True, exist_ok=True)
            path = self.heads_dir / f"{record['id']}.json"
            tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(_head_of(record), fh, sort_keys=True)
            tmp.replace(path)
        except OSError:
            pass                                   # heads are derived; the listing falls back to the job file

    def save(self, record: dict) -> dict:
        super().save(record)
        self._write_head(record)
        return record

    def delete(self, job_id: str) -> bool:
        try:
            path = self._path(job_id)
        except (ValueError, OSError):
            return False
        try:
            (self.heads_dir / f"{job_id}.json").unlink()
        except OSError:
            pass
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return False
        except OSError:
            return False

    def prune(self, protect=frozenset()) -> list[str]:
        """Remove the oldest unprotected jobs until at most ``max_jobs`` remain; returns the ids removed."""
        try:
            files = [(p.stat().st_mtime, p.stem) for p in self.dir.glob("*.json")]
        except OSError:
            return []
        extra = len(files) - self.max_jobs
        if extra <= 0:
            return []
        removed = []
        for _mtime, job_id in sorted(files):
            if extra <= 0:
                break
            if job_id in protect:
                continue
            if self.delete(job_id):
                removed.append(job_id)
                extra -= 1
        return removed

    # ------------------------------------------------------------- reads
    def list_heads(self) -> list[dict]:
        """The heads of every stored job (no rows, results or place table), newest file first.
        Reads the small head files; a job without one is loaded once and its head written."""
        out = []
        for job_id in self.list_ids():
            head = None
            try:
                with open(self.heads_dir / f"{job_id}.json", "r", encoding="utf-8") as fh:
                    head = json.load(fh)
            except (OSError, ValueError):
                head = None
            if not isinstance(head, dict) or head.get("id") != job_id:
                full = self.load(job_id)
                if full is None:
                    continue
                self._write_head(full)
                head = _head_of(full)
            out.append(head)
        return out
