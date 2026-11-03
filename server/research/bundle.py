"""The committed research bundle (data/research/): manifest-verified, read-only, offline.

Every file is checked against ``manifest.json`` (sha256) the first time it is read. A file that does
not match is treated as absent: the datasets it carries become ``not_served`` with the chip
``cache_integrity_failed``; nothing from a bad file is ever returned.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import threading
from datetime import date, timedelta
from pathlib import Path

from .util import ResearchError

FILES = ("episodes.json", "series.json.gz", "scenes.json.gz", "tracks.json.gz", "sam_recorded.json")


class BundleError(Exception):
    pass


class Bundle:
    def __init__(self, root: Path):
        self.root = Path(root)
        self._lock = threading.Lock()
        self._cache: dict[str, object] = {}
        self._errors: dict[str, str] = {}
        self._manifest = None

    # -------------------------------------------------------------- manifest
    def manifest(self) -> dict | None:
        if self._manifest is None:
            try:
                self._manifest = json.loads((self.root / "manifest.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._manifest = {}
        return self._manifest or None

    def available(self) -> bool:
        return self.manifest() is not None

    def verify_all(self) -> dict:
        """{ok, files: {name: 'ok'|'missing'|'mismatch'}} - used by the catalog."""
        man = self.manifest() or {}
        out = {}
        for name, meta in (man.get("files") or {}).items():
            p = self.root / name
            if not p.is_file():
                out[name] = "missing"
                continue
            out[name] = "ok" if hashlib.sha256(p.read_bytes()).hexdigest() == meta.get("sha256") else "mismatch"
        return {"ok": bool(out) and all(v == "ok" for v in out.values()), "files": out}

    # ------------------------------------------------------------------ load
    def _load(self, name: str):
        with self._lock:
            if name in self._cache:
                return self._cache[name]
            if name in self._errors:
                raise BundleError(self._errors[name])
            man = self.manifest()
            meta = ((man or {}).get("files") or {}).get(name)
            p = self.root / name
            try:
                if meta is None:
                    raise BundleError(f"{name} is not in the manifest")
                data = p.read_bytes()
                if hashlib.sha256(data).hexdigest() != meta["sha256"]:
                    raise BundleError(f"{name} does not match the manifest sha256")
                if name.endswith(".gz"):
                    data = gzip.decompress(data)
                doc = json.loads(data.decode("utf-8"))
            except BundleError as exc:
                self._errors[name] = str(exc)
                raise
            except (OSError, ValueError) as exc:
                self._errors[name] = f"{name} unreadable ({type(exc).__name__})"
                raise BundleError(self._errors[name]) from None
            self._cache[name] = doc
            return doc

    def _safe(self, name: str, default):
        try:
            return self._load(name)
        except BundleError:
            return default

    def integrity_ok(self, name: str) -> bool:
        try:
            self._load(name)
            return True
        except BundleError:
            return False

    # -------------------------------------------------------------- episodes
    def episodes(self) -> list[dict]:
        doc = self._safe("episodes.json", {})
        return list(doc.get("episodes", []))

    def episode(self, ep_id: str) -> dict | None:
        for e in self.episodes():
            if e["id"] == ep_id:
                return e
        return None

    # ---------------------------------------------------------------- series
    def _series_doc(self) -> dict:
        return self._safe("series.json.gz", {}).get("datasets", {})

    def series_places(self, dataset: str) -> list[str]:
        return sorted((self._series_doc().get(dataset) or {}).get("places", {}))

    def series_coverage(self, dataset: str) -> dict | None:
        segs = [s for segs in (self._series_doc().get(dataset) or {}).get("places", {}).values() for s in segs]
        if not segs:
            return None
        lo = min(s["from"] for s in segs)
        hi = max((date.fromisoformat(s["from"]) + timedelta(days=len(s["values"]) - 1)).isoformat()
                 for s in segs)
        return {"from": lo, "to": hi}

    def series_values(self, dataset: str, place: str, start: date, end: date):
        """(values aligned to [start, end] inclusive, retrieved_at) or None when the bundle holds
        nothing for the place. Days outside every segment are None."""
        segs = ((self._series_doc().get(dataset) or {}).get("places", {}) or {}).get(place)
        if not segs:
            return None
        n = (end - start).days + 1
        out: list = [None] * n
        retrieved = None
        hit = False
        for s in segs:
            s0 = date.fromisoformat(s["from"])
            for i, v in enumerate(s["values"]):
                d = s0 + timedelta(days=i)
                if start <= d <= end and v is not None:
                    out[(d - start).days] = v
                    hit = True
            if retrieved is None or s["retrieved_at"] > retrieved:
                retrieved = s["retrieved_at"]
        if not hit:
            return None
        return out, retrieved

    # ---------------------------------------------------------------- scenes
    def scenes(self, place: str, sensor: str, start: date, end: date) -> list[dict]:
        doc = self._safe("scenes.json.gz", {}).get("places", {})
        block = (doc.get(place) or {}).get(sensor)
        if not block:
            return []
        s, e = start.isoformat(), end.isoformat()
        return [dict(x) for x in block["scenes"] if s <= x["date"] <= e]

    def scene_bbox(self, place: str, sensor: str) -> list | None:
        block = ((self._safe("scenes.json.gz", {}).get("places", {}).get(place)) or {}).get(sensor)
        return list(block["bbox"]) if block else None

    def scene_by_id(self, scene_id: str) -> dict | None:
        doc = self._safe("scenes.json.gz", {}).get("places", {})
        for place, sensors in doc.items():
            for sensor, block in sensors.items():
                for x in block["scenes"]:
                    if x["scene_id"] == scene_id:
                        return {**x, "place": place}
        return None

    # ---------------------------------------------------------------- tracks
    def tracks(self, place: str, start: date, end: date) -> tuple[list[dict], dict | None]:
        doc = self._safe("tracks.json.gz", {}).get("episodes", {})
        out, prov = [], None
        for ep_id, t in sorted(doc.items()):
            if t["place"] != place:
                continue
            d = date.fromisoformat(t["date"])
            if start <= d <= end:
                out += [dict(x) for x in t["tracks"]]
                prov = {**t["provenance"], "episode": ep_id, "date": t["date"],
                        "bbox": t["bbox"], "vessel_filter": t["vessel_filter"]}
        return out, prov

    def tracks_info(self) -> dict:
        doc = self._safe("tracks.json.gz", {}).get("episodes", {})
        return {k: {"place": v["place"], "date": v["date"], "n_tracks": len(v["tracks"])}
                for k, v in doc.items()}

    # ------------------------------------------------------------------- sam
    def sam_recorded(self) -> list[dict]:
        return list(self._safe("sam_recorded.json", {}).get("entries", []))


def require(cond: bool, code: str, msg: str, status: int = 422) -> None:
    if not cond:
        raise ResearchError(code, msg, status)
