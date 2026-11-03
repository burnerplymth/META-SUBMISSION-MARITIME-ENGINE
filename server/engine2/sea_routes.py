"""Sea-lane geometry for ENGINE REV 2 profile d: reader of data/graph/sea_routes.json.

The sidecar (built by scripts/build_sea_routes.py from the MARNET network in searoute-py, see
its provenance block) holds, for EVERY sea leg of the graph (all tiers), the lane distance in
nautical miles, the waypoint list and the passages traversed, or a ``fallback`` mark when an
end of the leg is too far from the network; for every pair of CORE sea places the lane
distance, the chokepoints the lane passes, and the lane that AVOIDS each gate passage on it.
This module only READS it: the engine never imports searoute. The file is content-addressed:
``load_sea_routes`` refuses a file whose hash differs from the one the plug-in registry
(plugins_d.py) records, so a run is never silently re-derived against other geometry.

Transit days of a sea route = nautical miles / (service speed x hours per day). Speeds are
registry entries (plugins_d.py), not written here.
"""
from __future__ import annotations

import hashlib
import heapq
import json
import math
from pathlib import Path

from engine.canonical import canonical_json

from .plugins_d import D_PLUG

EARTH_R_NM = 3440.065          # mean Earth radius in nautical miles (1 nm = 1 arc-minute of a great circle)
REPO_ROOT = Path(__file__).resolve().parents[2]
SEA_ROUTES_PATH = REPO_ROOT / "data" / "graph" / "sea_routes.json"
KM_PER_NM = 1.852


class SeaRoutesError(ValueError):
    """The sea-routes sidecar is missing, unreadable or differs from the registry pin."""


def _content_hash(doc: dict) -> str:
    body = {k: v for k, v in doc.items() if k != "content_sha256"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


class SeaRoutes:
    def __init__(self, doc: dict, file_sha256: str):
        self.doc = doc
        self.file_sha256 = file_sha256
        self.content_sha256 = doc["content_sha256"]
        self._pairs = doc["pairs"]
        self.nodes = doc["nodes"]
        # A leg whose lane is more than ``land_separation_ratio`` times its straight line is not a
        # plausible direct sea link (the two ports are joined overland or by an unmodelled canal and
        # the sea route goes round a peninsula): it keeps the graph's own transit like an off-network leg.
        ratio = float(D_PLUG("land_separation_ratio"))
        self._legs = {}
        self.fallback_legs = dict(doc["fallback_legs"])
        for key, lg in doc["legs"].items():
            u, rest = key.split("->")
            v = rest.rsplit(":", 1)[0]
            chord = self._chord_nm(u, v)
            if lg["nm"] > ratio * chord:
                self.fallback_legs[key] = {"reason": "land-separated: lane %.0f nm vs straight line %.0f nm "
                                                     "(more than %g x)" % (lg["nm"], chord, ratio)}
            else:
                self._legs[key] = lg
        spd = D_PLUG("service_speed_kn")
        self.v_long = float(spd["long_haul"])
        self.v_regional = float(spd["regional"])
        self.boundary_nm = float(D_PLUG("speed_class_boundary_nm"))
        self.hours = float(D_PLUG("leg_transit_rule")["hours_per_day"])
        self.gate_passages = dict(doc["provenance"]["gate_passages"])
        adj: dict = {}
        for key, lg in self._legs.items():
            u, rest = key.split("->")
            v = rest.rsplit(":", 1)[0]
            for a, b in ((u, v), (v, u)):
                w = adj.setdefault(a, {})
                if b not in w or lg["nm"] < w[b]:
                    w[b] = lg["nm"]
        self._adj = adj
        self._memo: dict = {}

    def _chord_nm(self, a: str, b: str) -> float:
        na, nb = self.nodes[a], self.nodes[b]
        la1, lo1, la2, lo2 = (math.radians(x) for x in (na["lat"], na["lon"], nb["lat"], nb["lon"]))
        h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
        return 2.0 * EARTH_R_NM * math.asin(min(1.0, math.sqrt(h)))

    # ---- distances -------------------------------------------------------
    @staticmethod
    def _pk(a: str, b: str) -> str:
        return f"{a}|{b}" if a < b else f"{b}|{a}"

    def pair(self, a: str, b: str):
        """The CORE-pair record {nm, chokepoints, passages?, alt?} or None."""
        return self._pairs.get(self._pk(a, b))

    def nm(self, a: str, b: str):
        """Lane distance in nautical miles between two sea places: the network pair when both are
        CORE, otherwise the shortest path over the sidecar's real leg distances; None if neither."""
        if a == b:
            return 0.0
        p = self.pair(a, b)
        if p is not None:
            return p["nm"]
        k = self._pk(a, b)
        if k in self._memo:
            return self._memo[k]
        best = {a: 0.0}
        heap = [(0.0, a)]
        out = None
        while heap:
            d, u = heapq.heappop(heap)
            if d > best.get(u, float("inf")):
                continue
            if u == b:
                out = d
                break
            for v, w in sorted(self._adj.get(u, {}).items()):
                nd = d + w
                if nd < best.get(v, float("inf")) - 1e-9:
                    best[v] = nd
                    heapq.heappush(heap, (nd, v))
        self._memo[k] = out
        return out

    def km(self, a: str, b: str):
        n = self.nm(a, b)
        return None if n is None else n * KM_PER_NM

    def chokepoints(self, a: str, b: str) -> list:
        p = self.pair(a, b)
        return list(p["chokepoints"]) if p else []

    def alt(self, a: str, b: str, passage: str):
        """The lane that avoids ``passage`` between two CORE places: {nm, chokepoints} or None."""
        p = self.pair(a, b)
        if not p:
            return None
        return (p.get("alt") or {}).get(passage)

    def leg(self, key: str):
        return self._legs.get(key)

    # ---- time ------------------------------------------------------------
    def speed_kn(self, route_nm: float) -> float:
        """Service speed class of a route by its total length."""
        return self.v_long if route_nm >= self.boundary_nm else self.v_regional

    def days(self, nm: float, route_nm: float | None = None) -> float:
        """Sailing days for ``nm`` at the speed class of the whole route ``route_nm``."""
        v = self.speed_kn(nm if route_nm is None else route_nm)
        return nm / (v * self.hours)

    def digest(self) -> dict:
        return {"file_sha256": self.file_sha256, "content_sha256": self.content_sha256}

    # ---- coverage --------------------------------------------------------
    def coverage(self) -> dict:
        """Legs with real route geometry vs the graph's own transit (fallback), by region of
        the origin and in total; the fallback is split into land-separated and off-network."""
        by: dict = {}
        for key in list(self._legs) + list(self.fallback_legs):
            u = key.split("->")[0]
            reg = self.nodes[u]["region"]
            row = by.setdefault(reg, {"legs": 0, "network": 0, "fallback": 0, "land_separated": 0,
                                      "off_network": 0})
            row["legs"] += 1
            if key in self._legs:
                row["network"] += 1
            else:
                row["fallback"] += 1
                row["land_separated" if self.fallback_legs[key]["reason"].startswith("land") else "off_network"] += 1
        tot = {k: sum(r[k] for r in by.values())
               for k in ("legs", "network", "fallback", "land_separated", "off_network")}
        return {"total": tot, "by_region": dict(sorted(by.items()))}


class SeaKm:
    """Drop-in for routes._Km: ``km(x, y)`` over the sea-lane network."""

    def __init__(self, sea: SeaRoutes):
        self.sea = sea

    def km(self, x: str, y: str):
        return self.sea.km(x, y)


_CACHE: dict = {}


def load_sea_routes(path: Path | None = None, verify: bool = True) -> SeaRoutes:
    p = Path(path) if path else SEA_ROUTES_PATH
    key = (str(p), verify)
    if key in _CACHE:
        return _CACHE[key]
    try:
        raw = p.read_bytes()
        doc = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise SeaRoutesError(f"sea routes sidecar unavailable: {exc}") from exc
    sha = hashlib.sha256(raw).hexdigest()
    if doc.get("schema") != "supermap.sea_routes/1":
        raise SeaRoutesError("sea routes sidecar has an unknown schema")
    if _content_hash(doc) != doc.get("content_sha256"):
        raise SeaRoutesError("sea routes sidecar content hash does not match its body")
    if verify:
        pin = D_PLUG("sea_route_network")
        if doc["content_sha256"] != pin["content_sha256"]:
            raise SeaRoutesError("sea routes sidecar differs from the one the registry pins")
    obj = SeaRoutes(doc, sha)
    _CACHE[key] = obj
    return obj


def retime_graph(graph, sea: SeaRoutes):
    """A copy of the graph whose sea legs sail at lane distance / service speed.

    Only legs with an entry in the sidecar change (their ``base_transit_d``); a fallback leg, every
    other field and every non-sea leg is carried over as is. Pure function of (graph, sidecar)."""
    from engine.graph import Graph, edge_key
    raw = graph.raw
    edges = []
    for e in raw["edges"]:
        if e["mode"] == "sea":
            lg = sea.leg(edge_key(e))
            if lg is not None:
                e = {**e, "base_transit_d": round(sea.days(lg["nm"]), 6)}
        edges.append(e)
    return Graph({"version": raw.get("version"), "nodes": list(raw["nodes"]), "edges": edges})


def canonical_digest(obj) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()
