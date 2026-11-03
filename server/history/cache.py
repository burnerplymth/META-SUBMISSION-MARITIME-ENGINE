"""On-disk history cache keyed by (source, query).

A repeat call is answered from disk - offline and deterministic: the file
stores the raw payload exactly as first fetched plus the provenance
(``retrieved_at`` is the ORIGINAL fetch time, never "now"), so the same
request returns byte-identical normalised output forever.

Layout (under the data dir, ``<data>/history_cache/``)::

    <source>/<series-slug>__<from>__<to>__<hash12>.json      ranged query
    <source>/<series-slug>__<hash12>.json                    un-ranged query

Entry file (tracked fixtures carry this header so a reader can see what
they are without opening the code)::

    {"schema": "supermap.history_cache.v1",
     "source": ..., "query": {...}, "request": {...},
     "provenance": {source, retrieved_at, mode: "SNAPSHOT"},
     "status": "OK" | "EMPTY", "note": ..., "payload_sha256": ...,
     "payload": ...}

A ranged query is a dict with ``series`` (everything that identifies the
question except the dates), ``from`` and ``to`` (YYYY-MM-DD, inclusive).
``find_covering`` answers a request from any cached entry for the same
series whose range contains it, so a fixture fetched for a whole event
window serves every sub-window offline. Errors are never cached; an
EMPTY answer is (the upstream said "nothing in this range").

No credential is ever written: a request's headers are not stored.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import date
from pathlib import Path

from .util import DAY_RE, canonical, sha256_hex, slug

SCHEMA = "supermap.history_cache.v1"


class HistoryCache:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    # ------------------------------------------------------------- keys
    @staticmethod
    def query_key(query: dict) -> str:
        return sha256_hex(canonical(query))[:12]

    def path_for(self, source: str, query: dict) -> Path:
        h = self.query_key(query)
        series = slug(str(query.get("series", "q")))
        frm, to = query.get("from"), query.get("to")
        if frm and to:
            name = f"{series}__{frm}__{to}__{h}.json"
        else:
            name = f"{series}__{h}.json"
        return self.root / slug(source) / name

    # ------------------------------------------------------------- read
    def _load(self, path: Path) -> dict | None:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
            return None
        return doc

    def get(self, source: str, query: dict) -> dict | None:
        """Exact-key hit."""
        doc = self._load(self.path_for(source, query))
        if doc is not None and canonical(doc.get("query")) == canonical(query):
            return doc
        return None

    def find_covering(self, source: str, series: str, start: date,
                      end: date) -> dict | None:
        """The cached entry for ``series`` whose [from, to] contains
        [start, end]; ties break on the narrowest range, then file name,
        so the choice is deterministic."""
        folder = self.root / slug(source)
        if not folder.is_dir():
            return None
        prefix = slug(series) + "__"
        best: tuple | None = None
        for path in sorted(folder.glob(prefix + "*.json")):
            parts = path.stem.split("__")
            if len(parts) != 4 or not (DAY_RE.match(parts[1])
                                       and DAY_RE.match(parts[2])):
                continue
            try:
                a, b = date.fromisoformat(parts[1]), date.fromisoformat(parts[2])
            except ValueError:
                continue
            if a <= start and end <= b:
                doc = self._load(path)
                if doc is None or doc.get("query", {}).get("series") != series:
                    continue
                key = ((b - a).days, path.name)
                if best is None or key < best[0]:
                    best = (key, doc)
        return best[1] if best else None

    def entries(self, source: str | None = None) -> list[dict]:
        """Header-only listing (payload omitted), sorted by path."""
        out = []
        base = self.root
        if not base.is_dir():
            return out
        folders = [base / slug(source)] if source else sorted(
            p for p in base.iterdir() if p.is_dir())
        for folder in folders:
            if not folder.is_dir():
                continue
            for path in sorted(folder.glob("*.json")):
                doc = self._load(path)
                if doc is None:
                    continue
                out.append({
                    "file": f"{folder.name}/{path.name}",
                    "source": doc.get("source"), "query": doc.get("query"),
                    "provenance": doc.get("provenance"),
                    "status": doc.get("status"),
                    "payload_sha256": doc.get("payload_sha256"),
                })
        return out

    # ------------------------------------------------------------ write
    def put(self, source: str, query: dict, payload, *, retrieved_at: str,
            provenance_source: str, request: dict | None = None,
            status: str = "OK", note: str | None = None) -> dict:
        doc = {
            "schema": SCHEMA, "source": source, "query": query,
            "request": request or {},
            "provenance": {"source": provenance_source,
                           "retrieved_at": retrieved_at, "mode": "SNAPSHOT"},
            "status": status, "note": note,
            "payload_sha256": sha256_hex(canonical(payload)),
            "payload": payload,
        }
        path = self.path_for(source, query)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Readable provenance header (indent=1), compact payload on one
        # line: tracked fixtures stay small and diff cleanly.
        head = json.dumps({k: v for k, v in doc.items() if k != "payload"},
                          sort_keys=True, indent=1, ensure_ascii=False)
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False)
        text = head[:-2] + ',\n "payload": ' + body + "\n}\n"
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.chmod(tmp, 0o644)
            os.replace(tmp, path)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return doc
