"""Graph loading + validation (engine-side, read-only consumer of the graph data)."""
from __future__ import annotations

import json
from pathlib import Path

from .canonical import graph_hash

NODE_MODES = {"sea", "air", "ground"}
NODE_KINDS = {"port", "chokepoint", "air_hub", "ground_node"}
EDGE_MODES = {"sea", "air", "ground"}


class GraphError(ValueError):
    pass


def edge_key(edge: dict) -> str:
    return f"{edge['from']}->{edge['to']}:{edge['mode']}"


class Graph:
    """Validated, index-built view of a graph dict.

    Deterministic iteration everywhere: nodes sorted by id, outbound /
    inbound edge lists sorted by edge key.
    """

    def __init__(self, data: dict):
        if not isinstance(data, dict) or "nodes" not in data or "edges" not in data:
            raise GraphError("graph must be an object with 'nodes' and 'edges'")
        self.raw = data
        self.version = data.get("version")
        self.hash = graph_hash(data)

        self.nodes: dict[str, dict] = {}
        for n in data["nodes"]:
            self._validate_node(n)
            if n["id"] in self.nodes:
                raise GraphError(f"duplicate node id: {n['id']}")
            self.nodes[n["id"]] = n

        self.edges: list[dict] = []
        seen_keys: set[str] = set()
        for e in data["edges"]:
            self._validate_edge(e)
            k = edge_key(e)
            if k in seen_keys:
                raise GraphError(f"duplicate edge key: {k}")
            seen_keys.add(k)
            self.edges.append(e)
        for e in self.edges:
            if e.get("bypass_of") and e["bypass_of"] not in seen_keys:
                raise GraphError(f"bypass_of references unknown edge: {e['bypass_of']}")

        self._out: dict[str, list[dict]] = {nid: [] for nid in self.nodes}
        self._in: dict[str, list[dict]] = {nid: [] for nid in self.nodes}
        self._bypass: dict[str, list[dict]] = {}  # primary edge key -> bypass edges
        for e in sorted(self.edges, key=edge_key):
            self._out[e["from"]].append(e)
            self._in[e["to"]].append(e)
            if e.get("bypass_of"):
                self._bypass.setdefault(e["bypass_of"], []).append(e)

    # -- validation ------------------------------------------------------
    def _validate_node(self, n) -> None:
        if not isinstance(n, dict):
            raise GraphError("node must be an object")
        for f in ("id", "name", "mode", "kind", "lat", "lon", "capacity",
                  "capacity_unit", "queue_cap", "inventory_days", "sources"):
            if f not in n:
                raise GraphError(f"node missing required field {f!r}: {n.get('id')}")
        if n["mode"] not in NODE_MODES:
            raise GraphError(f"node {n['id']}: bad mode {n['mode']!r}")
        if n["kind"] not in NODE_KINDS:
            raise GraphError(f"node {n['id']}: bad kind {n['kind']!r}")
        if not (isinstance(n["capacity"], (int, float)) and n["capacity"] > 0):
            raise GraphError(f"node {n['id']}: capacity must be > 0")
        if not (isinstance(n["queue_cap"], (int, float)) and n["queue_cap"] >= 0):
            raise GraphError(f"node {n['id']}: queue_cap must be >= 0")
        if not (isinstance(n["inventory_days"], (int, float)) and n["inventory_days"] >= 0):
            raise GraphError(f"node {n['id']}: inventory_days must be >= 0")
        if "hit_threshold" in n and n["hit_threshold"] is not None:
            if not (0.0 <= float(n["hit_threshold"]) <= 1.0):
                raise GraphError(f"node {n['id']}: hit_threshold out of 0..1")
        if not (isinstance(n["sources"], list) and len(n["sources"]) >= 1):
            raise GraphError(f"node {n['id']}: sources must be a non-empty list")

    def _validate_edge(self, e) -> None:
        if not isinstance(e, dict):
            raise GraphError("edge must be an object")
        for f in ("from", "to", "mode", "base_transit_d", "capacity_share", "calibration"):
            if f not in e:
                raise GraphError(f"edge missing required field {f!r}")
        if e["from"] not in self.nodes:
            raise GraphError(f"edge from unknown node: {e['from']}")
        if e["to"] not in self.nodes:
            raise GraphError(f"edge to unknown node: {e['to']}")
        if e["from"] == e["to"]:
            raise GraphError(f"self-loop edge: {edge_key(e)}")
        if e["mode"] not in EDGE_MODES:
            raise GraphError(f"edge {edge_key(e)}: bad mode {e['mode']!r}")
        if not (isinstance(e["base_transit_d"], (int, float)) and e["base_transit_d"] > 0):
            raise GraphError(f"edge {edge_key(e)}: base_transit_d must be > 0")
        if not (0.0 <= float(e["capacity_share"]) <= 1.0):
            raise GraphError(f"edge {edge_key(e)}: capacity_share out of 0..1")
        cal = e["calibration"]
        if not isinstance(cal, dict) or not cal.get("volume_source"):
            raise GraphError(f"edge {edge_key(e)}: calibration.volume_source required")

    # -- accessors ---------------------------------------------------------
    def outbound(self, node_id: str) -> list[dict]:
        return self._out.get(node_id, [])

    def inbound(self, node_id: str) -> list[dict]:
        return self._in.get(node_id, [])

    def bypasses_of(self, primary_key: str) -> list[dict]:
        return self._bypass.get(primary_key, [])

    def outbound_share_total(self, node_id: str) -> float:
        return sum(float(e["capacity_share"]) for e in self.outbound(node_id))


def load_graph(path: str | Path) -> Graph:
    with open(path, "r", encoding="utf-8") as fh:
        return Graph(json.load(fh))
