"""In-memory views of graph.json + the validated extra sidecar.

Nothing here writes a file or edits the graph dict it is given: every view is a
new dict that shares unchanged nodes and edges by reference. Order is
deterministic (graph.json order, then sidecar entries sorted by id / key).

sim view      the graph the simulation derives from. Every sidecar place enters
              as ``shell``. A sidecar leg enters only when it touches a shell
              place; a ``core_ext`` leg (both ends core) is WITHHELD, because
              derive() sizes the headway bands of core lanes from the population
              of core-core lanes, so adding one would move the bands of existing
              lanes. The ``activity`` census block is not copied into the view:
              graph.json serves no observed activity on any place and the sim
              reads one only when served.
engine2 view  what engine rev 2 runs on: graph.json unchanged plus the
              ``core_ext`` places (labelled core inside the view) and ``core_ext``
              legs. Shell places and legs never reach it (core_subgraph would drop
              them), so they are not added. The dormant lanes become reroute-lane
              specs.
"""
from __future__ import annotations

from .loader import Extra

_NODE_DROP = ("priority", "why", "provenance", "activity")


def _node_for(p: dict, tier: str) -> dict:
    n = {k: v for k, v in p.items() if k not in _NODE_DROP}
    n["tier"] = tier
    n["graph_extra"] = True
    if p["tier"] != tier:
        n["tier_ext"] = p["tier"]
    return n


def _edge_for(e: dict) -> dict:
    cal = dict(e["calibration"])
    cal["graph_extra"] = True
    cal.setdefault("basis", "MODELED")
    return {"from": e["from"], "to": e["to"], "mode": e["mode"],
            "base_transit_d": e["base_transit_d"],
            "capacity_share": e["capacity_share"], "calibration": cal}


def sim_view(graph_data: dict, extra: Extra) -> tuple[dict, dict]:
    """(raw graph dict for derive / simulate, meta)."""
    place_ids = {p["id"] for p in extra.places}
    # An untiered graph (e.g. a small fixture graph) is all core to the sim; once shell
    # places join it, the sim's tier contract needs every place tiered.
    if any("tier" in n for n in graph_data["nodes"]):
        base_nodes = list(graph_data["nodes"])
    else:
        base_nodes = [dict(n, tier="core") for n in graph_data["nodes"]]
    nodes = base_nodes + [_node_for(p, "shell") for p in extra.places]
    tier = {n["id"]: n.get("tier", "core") for n in nodes}
    added, withheld = [], []
    for e in extra.legs:
        (added if "shell" in (tier[e["from"]], tier[e["to"]]) else withheld).append(e)
    edges = list(graph_data["edges"]) + [_edge_for(e) for e in added]
    raw = dict(graph_data)
    raw["nodes"], raw["edges"] = nodes, edges
    meta = {"places_added": len(place_ids), "place_ids": sorted(place_ids),
            "legs_added": len(added), "legs_withheld_core_ext": len(withheld),
            "withheld_leg_keys": sorted(e["key"] for e in withheld),
            "withheld_reason": "core-core legs would move the headway bands of existing core lanes "
                               "(derive quartile populations); visible to engine rev 2 instead"}
    return raw, meta


def engine2_view(graph_data: dict, extra: Extra, lane_families=None) -> tuple[dict, list, dict]:
    """(raw graph dict, reroute-lane specs, meta)."""
    ext_places = [p for p in extra.places if p["tier"] == "core_ext"]
    nodes = list(graph_data["nodes"]) + [_node_for(p, "core") for p in ext_places]
    ids = {n["id"] for n in nodes if n.get("tier", "core") == "core"}
    legs = [e for e in extra.legs if e["tier"] == "core_ext" and e["from"] in ids and e["to"] in ids]
    edges = list(graph_data["edges"]) + [_edge_for(e) for e in legs]
    raw = dict(graph_data)
    raw["nodes"], raw["edges"] = nodes, edges
    prim = {f"{e['from']}->{e['to']}:{e['mode']}" for e in edges
            if not e.get("bypass_of") and e["from"] in ids and e["to"] in ids}
    specs, unrouted = [], []
    for ln in extra.select_lanes(lane_families):
        if ln["leg"] in prim and ln["from"] in ids and ln["to"] in ids:
            specs.append({"key": ln["key"], "kind": ln["kind"], "from": ln["from"], "to": ln["to"],
                          "leg": ln["leg"], "tau": ln["tau"], "dtau": ln["dtau"], "family": ln["family"]})
        else:
            unrouted.append(ln["key"])
    specs.sort(key=lambda s: s["key"])
    meta = {"core_ext_places_added": len(ext_places), "legs_added": len(legs),
            "legs_dropped_shell": sum(1 for e in extra.legs if e["tier"] != "core_ext"),
            "lanes": len(specs), "lanes_unrouted": sorted(unrouted),
            "lane_families": sorted({s["family"] for s in specs})}
    return raw, specs, meta
