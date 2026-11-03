"""Segmented file store for ensemble-statistics TREE jobs.

Layout per job, under the store root:

    <root>/<job_id>/meta.json                    the job record
                                                 (summary fields,
                                                 quote, accounting,
                                                 level_counts)
    <root>/<job_id>/segments/level_{k}.jsonl     the full stored
                                                 variant records for
                                                 level k (level 0 is
                                                 the root variant
                                                 alone), one JSON
                                                 object per line, in
                                                 member order
    <root>/<job_id>/nodes/level_{k}.jsonl        the light tree
                                                 entries for level k,
                                                 same order
    <root>/<job_id>/aggregates/{index}.json      one member's served
                                                 aggregates

Why segmented: outcome vectors live in per-level segment files and
per-member aggregates are stored separately, so serving one
member's aggregates never loads the whole tree's vectors — the
aggregate file is read on its own, and locating a member reads only
that member's level file (one line of it, via read_level_row). The
flat jobs' single-record store (server/ensstats/store.py) is
untouched: tree jobs and flat jobs never share a store shape or a
store directory.

Discipline mirrors server/ensstats/store.py: traversal-safe ids,
atomic tmp+replace writes, and corrupt or missing files read as
absent rather than fatal (a mis-shaped store file
is never a 500).
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

_KINDS = ("segments", "nodes")


class TreeJobStore:
    def __init__(self, root_dir: str | Path):
        self.dir = Path(root_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------ paths

    def job_dir(self, job_id: str) -> Path:
        if (not isinstance(job_id, str) or not job_id
                or "/" in job_id or "\\" in job_id
                or ".." in job_id or "\x00" in job_id):
            raise ValueError("bad job_id")
        p = self.dir / job_id
        if p.resolve().parent != self.dir.resolve():
            raise ValueError("bad job_id")
        return p

    def _meta_path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "meta.json"

    def _level_path(self, job_id: str, kind: str, level: int) -> Path:
        if kind not in _KINDS:
            raise ValueError(f"bad level kind: {kind!r}")
        if isinstance(level, bool) or not isinstance(level, int) \
                or level < 0:
            raise ValueError(f"bad level: {level!r}")
        return self.job_dir(job_id) / kind / f"level_{level}.jsonl"

    def _aggregate_path(self, job_id: str, index: int) -> Path:
        if isinstance(index, bool) or not isinstance(index, int) \
                or index < 0:
            raise ValueError(f"bad index: {index!r}")
        return self.job_dir(job_id) / "aggregates" / f"{index}.json"

    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Per-writer unique temp name (pid + uuid), never the
        # shared deterministic "<name>.tmp".
        tmp = path.with_name(
            f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
        tmp.replace(path)

    # -------------------------------------------------------------- meta

    def save_meta(self, record: dict) -> dict:
        path = self._meta_path(record["id"])
        self._atomic_write(path, json.dumps(record, sort_keys=True))
        return record

    def load_meta(self, job_id: str) -> dict | None:
        try:
            p = self._meta_path(job_id)
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
            return sorted(
                p.name for p in self.dir.iterdir()
                if p.is_dir() and (p / "meta.json").exists())
        except OSError:
            return []

    # ------------------------------------------------------------ levels

    def write_level(self, job_id: str, kind: str, level: int,
                    rows) -> None:
        path = self._level_path(job_id, kind, level)
        text = "".join(json.dumps(row, sort_keys=True) + "\n"
                       for row in rows)
        self._atomic_write(path, text)

    def read_level(self, job_id: str, kind: str,
                   level: int) -> list[dict]:
        try:
            p = self._level_path(job_id, kind, level)
        except (ValueError, OSError):
            return []
        try:
            if not p.exists():
                return []
            out = []
            with open(p, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(row, dict):
                        out.append(row)
            return out
        except OSError:
            return []

    def read_level_row(self, job_id: str, kind: str, level: int,
                       offset: int) -> dict | None:
        """The offset-th line (0-based) of ONE level file, or None.

        Only that level's file is opened — serving one member never
        loads any other level's rows."""
        if isinstance(offset, bool) or not isinstance(offset, int) \
                or offset < 0:
            return None
        try:
            p = self._level_path(job_id, kind, level)
        except (ValueError, OSError):
            return None
        try:
            if not p.exists():
                return None
            with open(p, "r", encoding="utf-8") as fh:
                for i, line in enumerate(fh):
                    if i != offset:
                        continue
                    line = line.strip()
                    if not line:
                        return None
                    try:
                        row = json.loads(line)
                    except ValueError:
                        return None
                    return row if isinstance(row, dict) else None
            return None
        except OSError:
            return None

    # -------------------------------------------------------- aggregates

    def save_aggregate(self, job_id: str, index: int,
                       payload: dict) -> dict:
        path = self._aggregate_path(job_id, index)
        self._atomic_write(path, json.dumps(payload, sort_keys=True))
        return payload

    def load_aggregate(self, job_id: str, index: int) -> dict | None:
        try:
            p = self._aggregate_path(job_id, index)
        except (ValueError, OSError):
            return None
        try:
            if not p.exists():
                return None
            with open(p, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None
