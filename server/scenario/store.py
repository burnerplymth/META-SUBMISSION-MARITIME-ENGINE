"""Hash-addressed, bounded store for saved scenarios.

One JSON file per scenario under ``<data_root>/scenarios/<id>.json``. The id
is the sha256 prefix of the canonical normalised scenario, so saving the same
content twice is one record. Bounded: at most ``MAX_SAVED`` files; saving past
the cap evicts the oldest by modification time (a scenario is cheap to
re-create, and re-saving restores it under the same id). Reads re-hash the
body and refuse a file whose content does not match its id.

Ownership. The app has no session cookie, only a per-peer client
key (``api.security.request_client``, an IP), so the store does not hide scenarios
per client. Instead: a save records the creator's token (sha256 of the random token
the route hands back once) and a salted-free client digest beside the scenario;
``delete`` needs that token; and one client may hold at most ``max_per_client``
scenarios (past it that client's OWN oldest goes first), so one visitor cannot push
everybody else's out. A scenario saved without an owner cannot
be deleted through the API (the global cap still retires it). Saving identical
content again leaves the first owner in place. The extra keys sit beside
``scenario`` in the file, so the id (a hash of the scenario alone) is unchanged.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
from pathlib import Path

try:
    from engine.canonical import canonical_json
except ImportError:  # imported as server.scenario.* from the repo root
    from server.engine.canonical import canonical_json  # type: ignore

from .model import SCENARIO_ID_RE, scenario_id

MAX_SAVED = 200
MAX_PER_CLIENT = 25
MAX_BYTES = 32 * 1024


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ScenarioStore:
    def __init__(self, root: Path, max_saved: int = MAX_SAVED, max_per_client: int = MAX_PER_CLIENT):
        self.dir = Path(root)
        self.max_saved = max_saved
        self.max_per_client = max_per_client
        self._lock = threading.Lock()

    def _path(self, sid: str) -> Path:
        return self.dir / f"{sid}.json"

    def save(self, normalised: dict, owner: str = "", client: str = "") -> tuple[str, bool]:
        """(id, created). Idempotent for identical content. ``owner`` is the creator's secret token
        (only its digest is stored) and ``client`` the caller's client key (only a digest is stored)."""
        sid = scenario_id(normalised)
        blob = canonical_json(normalised)
        if len(blob.encode("utf-8")) > MAX_BYTES:
            raise ValueError("scenario too large")
        with self._lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            p = self._path(sid)
            created = not p.exists()
            if created:
                tmp = p.with_suffix(".tmp")
                doc = {"id": sid, "scenario": normalised}
                if owner:
                    doc["owner"] = _digest(owner)
                if client:
                    doc["client"] = _digest(client)
                tmp.write_text(json.dumps(doc, sort_keys=True), encoding="utf-8")
                os.replace(tmp, p)
                if client:
                    self._evict_client_locked(_digest(client), keep=sid)
                self._evict_locked(keep=sid)
            else:
                os.utime(p, None)
        return sid, created

    def _files(self) -> list[Path]:
        if not self.dir.is_dir():
            return []
        return [p for p in self.dir.glob("scn_*.json") if SCENARIO_ID_RE.match(p.stem)]

    def exists(self, sid: str) -> bool:
        return isinstance(sid, str) and bool(SCENARIO_ID_RE.match(sid)) and self._path(sid).exists()

    def _meta(self, p: Path) -> dict:
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return doc if isinstance(doc, dict) else {}

    def _evict_client_locked(self, client_digest: str, keep: str) -> None:
        """One client's own oldest scenarios go first once it holds more than ``max_per_client``."""
        mine = [p for p in self._files() if self._meta(p).get("client") == client_digest]
        extra = len(mine) - self.max_per_client
        if extra <= 0:
            return
        mine.sort(key=lambda p: (p.stat().st_mtime, p.name))
        for p in mine:
            if extra <= 0:
                break
            if p.stem == keep:
                continue
            try:
                p.unlink()
            except OSError:
                continue
            extra -= 1

    def _evict_locked(self, keep: str) -> None:
        files = self._files()
        extra = len(files) - self.max_saved
        if extra <= 0:
            return
        files.sort(key=lambda p: (p.stat().st_mtime, p.name))
        for p in files:
            if extra <= 0:
                break
            if p.stem == keep:
                continue
            try:
                p.unlink()
            except OSError:
                continue
            extra -= 1

    def load(self, sid: str) -> dict | None:
        if not isinstance(sid, str) or not SCENARIO_ID_RE.match(sid):
            return None
        p = self._path(sid)
        try:
            if p.stat().st_size > MAX_BYTES * 2:
                return None
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        scn = doc.get("scenario") if isinstance(doc, dict) else None
        if not isinstance(scn, dict) or scenario_id(scn) != sid:
            return None
        return scn

    def delete(self, sid: str, owner: str = "") -> bool:
        """Remove a scenario. Needs the creator token returned at save; a scenario with no recorded
        owner (saved before ownership existed) cannot be deleted here."""
        if not isinstance(sid, str) or not SCENARIO_ID_RE.match(sid) or not owner:
            return False
        with self._lock:
            stored = self._meta(self._path(sid)).get("owner")
            if not isinstance(stored, str) or not hmac.compare_digest(stored, _digest(owner)):
                return False
            try:
                self._path(sid).unlink()
                return True
            except OSError:
                return False

    def list(self) -> list[dict]:
        """Newest first: [{id, scenario}]."""
        with self._lock:
            files = sorted(self._files(), key=lambda p: (p.stat().st_mtime, p.name), reverse=True)
        out = []
        for p in files:
            scn = self.load(p.stem)
            if scn is not None:
                out.append({"id": p.stem, "scenario": scn})
        return out
