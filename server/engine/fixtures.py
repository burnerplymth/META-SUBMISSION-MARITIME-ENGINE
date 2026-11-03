"""Fixture graph + Suez scenario (small hand-built graph in the graph file shape).

Used when data/graph/graph.json is absent; the Suez scenario can also be
run against the real graph.

Fixture topology (flow direction):
  shanghai -> singapore -> suez -> port_said -> rotterdam -> hamburg
  singapore -> rotterdam  (Cape bypass of the singapore->suez leg)
  shanghai  -> pvg_air -> rotterdam   (intermodal / air leg)
"""
from __future__ import annotations

SRC = "fixture (hand-computed)"


def _node(id, name, mode, kind, lat, lon, capacity, unit, queue_cap, inv, **kw):
    n = {"id": id, "name": name, "mode": mode, "kind": kind, "lat": lat, "lon": lon,
         "capacity": capacity, "capacity_unit": unit, "queue_cap": queue_cap,
         "inventory_days": inv, "sources": [SRC]}
    n.update(kw)
    return n


def _edge(frm, to, mode, transit, share, **kw):
    e = {"from": frm, "to": to, "mode": mode, "base_transit_d": transit,
         "capacity_share": share, "calibration": {"volume_source": SRC}}
    e.update(kw)
    return e


def fixture_graph() -> dict:
    return {
        "version": "fixture-1",
        "nodes": [
            _node("shanghai", "Shanghai", "sea", "port", 31.23, 121.49, 100000, "TEU/day", 50000, 2),
            _node("singapore", "Singapore", "sea", "port", 1.26, 103.82, 80000, "TEU/day", 40000, 3),
            _node("suez", "Suez Canal", "sea", "chokepoint", 30.42, 32.34, 51000, "TEU/day", 420, 0),
            _node("port_said", "Port Said", "sea", "port", 31.26, 32.30, 20000, "TEU/day", 10000, 2),
            _node("rotterdam", "Rotterdam", "sea", "port", 51.95, 4.14, 90000, "TEU/day", 30000, 9),
            _node("pvg_air", "Shanghai Pudong Air Cargo", "air", "air_hub", 31.14, 121.81,
                  5000, "tonnes/day", 2000, 0, hit_threshold=0.10),
            _node("hamburg", "Hamburg (ground)", "ground", "ground_node", 53.55, 9.99,
                  30000, "trucks/day", 9000, 0),
        ],
        "edges": [
            _edge("shanghai", "singapore", "sea", 6.0, 0.5),
            _edge("shanghai", "pvg_air", "ground", 0.5, 0.5),
            _edge("singapore", "suez", "sea", 12.0, 0.6),
            _edge("singapore", "rotterdam", "sea", 38.0, 0.1,
                  bypass_of="singapore->suez:sea"),
            _edge("suez", "port_said", "sea", 1.0, 0.7),
            _edge("port_said", "rotterdam", "sea", 11.0, 0.6),
            _edge("pvg_air", "rotterdam", "air", 1.0, 0.3),
            _edge("rotterdam", "hamburg", "ground", 1.0, 0.4),
        ],
    }


SUEZ_DISRUPTION = {
    "node_id": "suez", "kind": "closure", "capacity_loss": 1.0,
    "duration_d": 14, "shape": "step", "started_at": "2026-10-26T00:00:00Z",
}
