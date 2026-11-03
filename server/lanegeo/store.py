"""Reader of data/graph/lane_geometry.json: smoothed, land-checked lane polylines per sea leg.

Read only and content-addressed. ``load_lane_geometry`` refuses a file whose content hash differs from the
one ``pin.py`` records, or whose recorded sea_routes content hash differs from the sidecar the engine
registry pins (``engine2.sea_routes``), so a run is never silently drawn or timed on other geometry.

A leg has one of three states:

  lane      a polyline exists and meets no land (apart from the Suez, Panama, Bosphorus and Dardanelles
            cuts and the two port approaches); drawn as a lane, sailed by lane-geometry sim vessels
  flagged   a polyline exists but crosses land or disagrees with the sidecar's lane nautical miles; it is
            returned on request with its flags and is never drawn as a lane
  none      the sea_routes sidecar holds no geometry for the leg (land-separated, off the network) or the
            leg is not a sea leg; callers draw the straight chord and label it approximate
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path

from . import smooth
from .pin import LANE_GEOMETRY_CONTENT_SHA256

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = REPO_ROOT / "data" / "graph" / "lane_geometry.json"
SCHEMA = "supermap.lane_geometry/1"
COARSE_TOL_DEG = 0.25            # world-zoom level of detail (Douglas-Peucker)
KM_PER_NM = 1.852


class LaneGeometryError(ValueError):
    """The lane geometry sidecar is missing, unreadable, or differs from its pin."""


def content_hash(doc: dict) -> str:
    body = {k: v for k, v in doc.items() if k != "content_sha256"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def pair_key(a: str, b: str) -> str:
    return f"{a}|{b}" if a < b else f"{b}|{a}"


def leg_ends(key: str) -> tuple[str, str] | None:
    """('u', 'v') of a leg id 'u->v:mode'; None when it is not a well-formed sea leg id."""
    if not isinstance(key, str) or "->" not in key or not key.endswith(":sea"):
        return None
    u, rest = key.split("->", 1)
    v = rest.rsplit(":", 1)[0]
    return (u, v) if u and v else None


def split_antimeridian(pts: list) -> list:
    """Split an unwrapped (lon, lat) polyline into parts that each lie in [-180, 180]; the crossing
    point is inserted at +-180 on both sides, so no part is ever drawn across the map. Returns
    [[[lon, lat], ...], ...]."""
    if not pts:
        return []

    def frame(lon: float) -> int:
        return int(math.floor((lon + 180.0) / 360.0))

    parts: list = []
    k = frame(pts[0][0])
    cur = [[round(pts[0][0] - 360.0 * k, 5), pts[0][1]]]
    for (alon, alat), (blon, blat) in zip(pts, pts[1:]):
        kb = frame(blon)
        while k != kb:
            step = 1 if kb > k else -1
            bound = 180.0 + 360.0 * (k if step > 0 else k - 1)        # unwrapped longitude of the boundary
            t = (bound - alon) / (blon - alon) if blon != alon else 0.0
            lat = round(alat + t * (blat - alat), 5)
            cur.append([180.0 if step > 0 else -180.0, lat])
            parts.append(cur)
            k += step
            cur = [[-180.0 if step > 0 else 180.0, lat]]
        cur.append([round(blon - 360.0 * k, 5), blat])
    parts.append(cur)
    return [p for p in parts if len(p) >= 2]


class LaneGeometry:
    def __init__(self, doc: dict, file_sha256: str):
        self.doc = doc
        self.file_sha256 = file_sha256
        self.content_sha256 = doc["content_sha256"]
        self.pairs: dict = doc["pairs"]
        self._lod: dict = {}
        self._adj: dict | None = None

    # ---- identity ---------------------------------------------------------
    def digest(self) -> dict:
        return {"content_sha256": self.content_sha256, "file_sha256": self.file_sha256}

    def stats(self) -> dict:
        return dict(self.doc["stats"])

    # ---- legs ---------------------------------------------------------------
    def record(self, u: str, v: str):
        return self.pairs.get(pair_key(u, v))

    def status(self, u: str, v: str) -> str:
        r = self.record(u, v)
        return "none" if r is None else r["status"]

    def polyline(self, u: str, v: str, detail: str = "full", flagged_ok: bool = False):
        """Unwrapped [(lon, lat), ...] from u to v, or None (no geometry, or flagged and not asked for)."""
        r = self.record(u, v)
        if r is None or (r["status"] != "lane" and not flagged_ok):
            return None
        pts = [(p[0], p[1]) for p in r["v"]]
        if r["a"] != u:
            pts.reverse()
        if detail == "coarse":
            key = (pair_key(u, v), u == r["a"])
            got = self._lod.get(key)
            if got is None:
                got = smooth.dp_simplify(pts, COARSE_TOL_DEG)
                self._lod[key] = got
            return list(got)
        return pts

    def leg(self, key: str, detail: str = "full", flagged_ok: bool = False) -> dict:
        """The served row of one leg id (always a dict; ``status`` says what it holds)."""
        ends = leg_ends(key)
        if ends is None:
            return {"id": key, "status": "none", "reason": "not_a_sea_leg_id"}
        u, v = ends
        r = self.record(u, v)
        if r is None:
            return {"id": key, "from": u, "to": v, "status": "none", "reason": "no_geometry"}
        out = {"id": key, "from": u, "to": v, "status": r["status"], "nm": r["lane_nm"],
               "geometry_nm": r["geometry_nm"], "passages": r["passages"], "smoothing": r["smoothing"],
               "approach_nm": r["approach_nm"] if r["a"] == u else list(reversed(r["approach_nm"]))}
        if r.get("canals"):
            out["canals"] = r["canals"]
        if r["status"] != "lane":
            out["flags"] = r["flags"]
        pts = self.polyline(u, v, detail, flagged_ok)
        if pts is not None:
            out["parts"] = split_antimeridian(pts)
            out["vertices"] = sum(len(p) for p in out["parts"])
        return out

    # ---- place pairs ----------------------------------------------------------
    def _graph(self) -> dict:
        if self._adj is None:
            adj: dict = {}
            for k, r in self.pairs.items():
                if r["status"] != "lane":
                    continue
                adj.setdefault(r["a"], []).append((r["b"], r["lane_nm"]))
                adj.setdefault(r["b"], []).append((r["a"], r["lane_nm"]))
            self._adj = {u: sorted(v) for u, v in adj.items()}
        return self._adj

    def route(self, a: str, b: str):
        """Shortest path of lane legs between two places by lane nm: (nodes, nm) or None."""
        import heapq
        adj = self._graph()
        if a not in adj or b not in adj:
            return None
        best = {a: 0.0}
        prev: dict = {}
        heap = [(0.0, a)]
        done = set()
        while heap:
            d, u = heapq.heappop(heap)
            if u in done:
                continue
            done.add(u)
            if u == b:
                break
            for v, w in adj.get(u, []):
                nd = d + w
                if nd < best.get(v, float("inf")) - 1e-9:
                    best[v] = nd
                    prev[v] = u
                    heapq.heappush(heap, (nd, v))
        if b not in done:
            return None
        path = [b]
        while path[-1] != a:
            path.append(prev[path[-1]])
        path.reverse()
        return path, best[b]


_CACHE: dict = {}


def default_path() -> Path:
    env = os.environ.get("SUPERMAP_LANE_GEOMETRY_PATH")
    if env:
        return Path(env)
    data_dir = os.environ.get("SUPERMAP_DATA_DIR")
    if data_dir:
        p = Path(data_dir) / "graph" / "lane_geometry.json"
        if p.exists():
            return p
    return DEFAULT_PATH


def load_lane_geometry(path: Path | str | None = None, verify: bool = True) -> LaneGeometry:
    p = Path(path) if path else default_path()
    key = (str(p), verify)
    if key in _CACHE:
        return _CACHE[key]
    try:
        raw = p.read_bytes()
        doc = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise LaneGeometryError(f"lane geometry sidecar unavailable: {exc}") from exc
    if doc.get("schema") != SCHEMA:
        raise LaneGeometryError("lane geometry sidecar has an unknown schema")
    if content_hash(doc) != doc.get("content_sha256"):
        raise LaneGeometryError("lane geometry content hash does not match its body")
    if verify:
        if doc["content_sha256"] != LANE_GEOMETRY_CONTENT_SHA256:
            raise LaneGeometryError("lane geometry sidecar differs from the one pin.py records")
        try:
            from engine2.sea_routes import load_sea_routes
        except Exception:  # pragma: no cover - imported as server.* from the repo root
            from server.engine2.sea_routes import load_sea_routes  # type: ignore
        sea = load_sea_routes()
        if doc["sea_routes"]["content_sha256"] != sea.content_sha256:
            raise LaneGeometryError("lane geometry was built on other sea_routes geometry than the pinned one")
    obj = LaneGeometry(doc, hashlib.sha256(raw).hexdigest())
    _CACHE[key] = obj
    return obj
