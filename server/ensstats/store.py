"""One-JSON-file-per-job store for ensemble-statistics jobs.

Mirrors server/sim/store.py's discipline: traversal-safe ids,
atomic tmp+replace writes, corrupt files read as absent rather than
fatal (a mis-shaped store file is never a 500).
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path


class EnsembleStatsStore:
    def __init__(self, jobs_dir: str | Path):
        self.dir = Path(jobs_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, job_id: str) -> Path:
        if (not isinstance(job_id, str) or not job_id
                or "/" in job_id or "\\" in job_id
                or ".." in job_id or "\x00" in job_id):
            raise ValueError("bad job_id")
        p = self.dir / f"{job_id}.json"
        if p.resolve().parent != self.dir.resolve():
            raise ValueError("bad job_id")
        return p

    def exists(self, job_id: str) -> bool:
        try:
            return self._path(job_id).exists()
        except (ValueError, OSError):
            return False

    def save(self, record: dict) -> dict:
        path = self._path(record["id"])
        # Per-writer unique temp name (pid + uuid) — the shared
        # deterministic .json.tmp let concurrent writers consume each
        # other's temp file (FileNotFoundError on replace).
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(record, fh, sort_keys=True)
        tmp.replace(path)
        return record

    def load(self, job_id: str) -> dict | None:
        try:
            p = self._path(job_id)
        except (ValueError, OSError):
            return None
        try:
            if not p.exists():
                return None
            with open(p, "r", encoding="utf-8") as fh:
                record = json.load(fh)
        except (OSError, ValueError):
            return None
        return record if isinstance(record, dict) else None

    def list_ids(self) -> list[str]:
        try:
            return sorted(p.stem for p in self.dir.glob("*.json"))
        except OSError:
            return []
