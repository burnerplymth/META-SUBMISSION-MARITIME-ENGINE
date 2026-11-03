"""Graph places for the history layer: lookup, nearest-node grounding,
graph hops (for the k-hop baseline), and the place -> PortWatch series
mapping.

Read-only over the graph file; the PortWatch mapping reuses the daily
adapter's own constants (chokepoint layer names, printed portid
overrides) and the graph build's pinned join input
(``data/graph/portwatch_join_input.json``) - never a second, divergent
copy. A place with no PortWatch series is NAMED as such by callers
(``portwatch_series`` returns None), never given a substitute.
"""
from __future__ import annotations

import json
import math
from collections import deque
from pathlib import Path

try:  # server/ on sys.path (house convention)
    from adapters.portwatch_daily import (CHOKEPOINT_LAYER_NAMES,
                                          PORTID_OVERRIDES)
except Exception:  # pragma: no cover - alternate import root / failure
    try:
        from server.adapters.portwatch_daily import (  # type: ignore
            CHOKEPOINT_LAYER_NAMES, PORTID_OVERRIDES)
    except Exception:
        CHOKEPOINT_LAYER_NAMES = {
            "chk_suez": "Suez Canal", "chk_panama": "Panama Canal",
            "chk_turkish": "Bosporus Strait",
            "chk_babelmandeb": "Bab el-Mandeb Strait",
            "chk_malacca": "Malacca Strait", "chk_hormuz": "Strait of Hormuz",
            "chk_capeofgoodhope": "Cape of Good Hope",
            "chk_gibraltar": "Gibraltar Strait", "chk_dover": "Dover Strait",
            "chk_danish": "Oresund Strait", "chk_taiwan": "Taiwan Strait"}
        PORTID_OVERRIDES = {}


# Places the daily adapter resolves by LOCODE at fetch time (so neither the
# overrides nor the pinned join file carry them) but whose portid is fixed
# and documented: PortWatch serves the San Pedro Bay complex only as the
# combined "Los Angeles-Long Beach" entry, port664 (portwatch_daily.py
# comment on sea_longbeach; data/sim_backtest/la_lb/portwatch_lalb_2021-2022.json
# notes). sea_longbeach is deliberately absent: it has no series of its own.
EXTRA_PORTID = {"sea_losangeles": "port664", "sea_baltimore": "port103"}


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class PlaceBook:
    def __init__(self, graph_doc: dict, join_records: list | None = None):
        self.nodes: dict[str, dict] = {n["id"]: n for n in graph_doc.get("nodes", [])
                                       if isinstance(n, dict) and "id" in n}
        self.graph_version = graph_doc.get("version")
        out: dict[str, set] = {}
        for e in graph_doc.get("edges", []):
            a, b = e.get("from"), e.get("to")
            if a in self.nodes and b in self.nodes:
                out.setdefault(a, set()).add(b)
        self._out = {k: sorted(v) for k, v in out.items()}
        self._portid: dict[str, str] = {}
        for nid, pid in EXTRA_PORTID.items():
            if nid in self.nodes:
                self._portid[nid] = pid
        for nid, pid in PORTID_OVERRIDES.items():
            if nid in self.nodes:
                self._portid[nid] = pid
        for rec in join_records or []:
            if rec.get("node_id") in self.nodes and rec.get("portid"):
                self._portid[rec["node_id"]] = rec["portid"]

    @classmethod
    def load(cls, graph_path: str | Path,
             join_path: str | Path | None = None) -> "PlaceBook":
        doc = json.loads(Path(graph_path).read_text(encoding="utf-8"))
        join = []
        if join_path and Path(join_path).exists():
            try:
                join = json.loads(Path(join_path).read_text(
                    encoding="utf-8")).get("records", [])
            except (OSError, ValueError):
                join = []
        return cls(doc, join)

    # ------------------------------------------------------------ basics
    def __contains__(self, node_id) -> bool:
        return node_id in self.nodes

    def name(self, node_id: str) -> str:
        n = self.nodes.get(node_id) or {}
        return n.get("name") or node_id

    def kind(self, node_id: str) -> str | None:
        return (self.nodes.get(node_id) or {}).get("kind")

    def tier(self, node_id: str) -> str:
        return (self.nodes.get(node_id) or {}).get("tier", "core")

    def nearest(self, lat: float, lon: float, max_km: float,
                core_only: bool = False) -> tuple[str, float] | None:
        best = None
        for nid in sorted(self.nodes):
            n = self.nodes[nid]
            if core_only and n.get("tier", "core") != "core":
                continue
            if "lat" not in n or "lon" not in n:
                continue
            d = haversine_km(lat, lon, float(n["lat"]), float(n["lon"]))
            if d <= max_km and (best is None or d < best[1]):
                best = (nid, d)
        return best

    def hops_from(self, seed: str, max_hops: int) -> dict[str, int]:
        """Directed BFS hop count over the graph's directed edges (the
        same definition the scorecard's k-hop baseline uses)."""
        if seed not in self.nodes:
            return {}
        dist = {seed: 0}
        q = deque([seed])
        while q:
            cur = q.popleft()
            if dist[cur] >= max_hops:
                continue
            for nxt in self._out.get(cur, ()):
                if nxt not in dist:
                    dist[nxt] = dist[cur] + 1
                    q.append(nxt)
        return dist

    # --------------------------------------------------------- PortWatch
    def portwatch_series(self, node_id: str) -> dict | None:
        """{layer: 'chokepoint', portname} | {layer: 'port', portid} |
        None when PortWatch has no series joined to this place."""
        if node_id in CHOKEPOINT_LAYER_NAMES and node_id in self.nodes:
            return {"layer": "chokepoint",
                    "portname": CHOKEPOINT_LAYER_NAMES[node_id]}
        pid = self._portid.get(node_id)
        if pid:
            return {"layer": "port", "portid": pid}
        return None
