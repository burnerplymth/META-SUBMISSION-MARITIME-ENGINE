"""Brief store: one JSON file per brief under <data root>/briefs/, written
atomically, newest MAX_BRIEFS kept. A brief id is a content address
(inputs + prompt version), so a repeat request finds the file instead of
calling the model again."""
from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path

MAX_BRIEFS = 200
ID_RE = re.compile(r"^brf_[0-9a-f]{16}$")


class BriefStore:
    def __init__(self, root: Path) -> None:
        self.dir = Path(root) / "briefs"

    def _path(self, brief_id: str, ext: str = "json") -> Path | None:
        if not isinstance(brief_id, str) or not ID_RE.match(brief_id):
            return None
        return self.dir / f"{brief_id}.{ext}"

    def get(self, brief_id: str) -> dict | None:
        p = self._path(brief_id)
        if p is None or not p.is_file():
            return None
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return doc if isinstance(doc, dict) else None

    def put(self, doc: dict) -> None:
        p = self._path(doc["brief_id"])
        if p is None:
            raise ValueError("bad brief id")
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.dir / f".{p.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        tmp.write_text(json.dumps(doc, sort_keys=True, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
        pdf = self._path(doc["brief_id"], "pdf")
        if pdf is not None and pdf.exists():  # a rewritten brief invalidates its PDF
            try:
                pdf.unlink()
            except OSError:
                pass
        self._evict()

    def pdf_get(self, brief_id: str, sha: str) -> bytes | None:
        p = self._path(brief_id, "pdf")
        meta = self._path(brief_id, "pdf.sha")
        if p is None or meta is None or not p.is_file() or not meta.is_file():
            return None
        try:
            if meta.read_text(encoding="utf-8").strip() != sha:
                return None
            return p.read_bytes()
        except OSError:
            return None

    def pdf_put(self, brief_id: str, sha: str, data: bytes) -> None:
        p = self._path(brief_id, "pdf")
        meta = self._path(brief_id, "pdf.sha")
        if p is None or meta is None:
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.dir / f".{p.name}.{uuid.uuid4().hex}.tmp"
        tmp.write_bytes(data)
        os.replace(tmp, p)
        meta.write_text(sha, encoding="utf-8")

    def _evict(self) -> None:
        try:
            files = sorted(self.dir.glob("brf_*.json"), key=lambda q: q.stat().st_mtime)
        except OSError:
            return
        for old in files[:-MAX_BRIEFS]:
            for ext in ("json", "pdf", "pdf.sha"):
                try:
                    (self.dir / f"{old.stem}.{ext}").unlink()
                except OSError:
                    pass
