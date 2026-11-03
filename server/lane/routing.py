"""LANE routing machinery (shared by LANE and REROUTE).

Method: paths are shortest paths over served legs,
costed by served base_transit_d (Dijkstra). The primary path is the
shortest origin->destination path. Single points of failure are found
honestly: for each place on the primary path (endpoints excluded), the
shortest path is re-searched with that place removed; a place whose
removal leaves NO path is a single point of failure, and a place whose
removal lengthens the path is a pinch (its added time is served).
Alternates are the distinct re-search results, ranked by added time
then by spare capacity.

Spare capacity of a path is served, not invented: for each leg, the
leg's capacity_share of its origin place's served capacity, converted
with the origin's capacity_unit as-is; the path figure is the minimum
over its legs (the bottleneck leg), with the unit carried through.
Legs whose origin capacity/unit are missing yield spare = None
(NOT SERVED), never zero.
"""
from __future__ import annotations

import heapq


def _adjacency(graph, banned: frozenset[str] = frozenset()):
    adj: dict[str, list[dict]] = {}
    for e in graph.edges:
        if e["from"] in banned or e["to"] in banned:
            continue
        adj.setdefault(e["from"], []).append(e)
    for lst in adj.values():
        lst.sort(key=lambda e: (e["to"], e["mode"]))
    return adj


def shortest_path(graph, origin: str, dest: str,
                  banned: frozenset[str] = frozenset()) -> dict | None:
    """Dijkstra over base_transit_d. Returns {places, legs, total_transit_d}
    or None when no path exists."""
    if origin in banned or dest in banned:
        return None
    adj = _adjacency(graph, banned)
    dist = {origin: 0.0}
    prev: dict[str, tuple[str, dict]] = {}
    pq = [(0.0, origin)]
    seen = set()
    while pq:
        d, u = heapq.heappop(pq)
        if u in seen:
            continue
        seen.add(u)
        if u == dest:
            break
        for e in adj.get(u, []):
            nd = d + float(e["base_transit_d"])
            v = e["to"]
            if v not in dist or nd < dist[v] - 1e-12:
                dist[v] = nd
                prev[v] = (u, e)
                heapq.heappush(pq, (nd, v))
    if dest not in dist:
        return None
    places = [dest]
    legs = []
    u = dest
    while u != origin:
        p, e = prev[u]
        legs.append(e)
        places.append(p)
        u = p
    places.reverse()
    legs.reverse()
    return {"places": places, "legs": legs,
            "total_transit_d": round(dist[dest], 6)}


def leg_view(graph, e: dict) -> dict:
    origin = graph.nodes.get(e["from"], {})
    cap = origin.get("capacity")
    share = e.get("capacity_share")
    spare = None
    if isinstance(cap, (int, float)) and isinstance(share, (int, float)):
        spare = round(float(cap) * float(share), 6)
    return {
        "from": e["from"], "to": e["to"], "mode": e["mode"],
        "transit_d": e["base_transit_d"],
        "capacity_share": share,
        "spare_capacity": spare,
        "spare_capacity_unit": origin.get("capacity_unit") if spare is not None else None,
        "bypass_of": e.get("bypass_of"),
    }


def path_view(graph, path: dict) -> dict:
    legs = [leg_view(graph, e) for e in path["legs"]]
    spares = [l["spare_capacity"] for l in legs]
    spare = min(spares) if legs and all(s is not None for s in spares) else None
    units = {l["spare_capacity_unit"] for l in legs}
    return {
        "places": path["places"],
        "legs": legs,
        "total_transit_d": path["total_transit_d"],
        "spare_capacity": spare,
        "spare_capacity_unit": units.pop() if len(units) == 1 else None,
    }


def stress_test(graph, origin: str, dest: str) -> dict:
    """LANE: primary path + per-place removal re-search."""
    primary = shortest_path(graph, origin, dest)
    if primary is None:
        return {"origin": origin, "destination": dest, "primary": None,
                "removals": [], "single_points_of_failure": [],
                "alternates": []}
    base = primary["total_transit_d"]
    removals = []
    alternates: dict[tuple, dict] = {}
    spofs = []
    for place in primary["places"][1:-1]:
        alt = shortest_path(graph, origin, dest, banned=frozenset({place}))
        if alt is None:
            removals.append({"place_id": place, "result": "no_path",
                             "added_transit_d": None, "alt_transit_d": None})
            spofs.append(place)
            continue
        added = round(alt["total_transit_d"] - base, 6)
        removals.append({"place_id": place, "result": "rerouted",
                         "added_transit_d": added,
                         "alt_transit_d": alt["total_transit_d"]})
        key = tuple(alt["places"])
        if key != tuple(primary["places"]) and key not in alternates:
            view = path_view(graph, alt)
            view["added_transit_d"] = added
            alternates[key] = view
    ranked = sorted(alternates.values(),
                    key=lambda a: (a["added_transit_d"],
                                   -(a["spare_capacity"] or 0),
                                   a["places"]))
    return {"origin": origin, "destination": dest,
            "primary": path_view(graph, primary),
            "removals": removals,
            "single_points_of_failure": spofs,
            "alternates": ranked}


def bypass_explore(graph, place_id: str, dest: str | None = None) -> dict:
    """REROUTE: for a chosen place, the paths that avoid it.

    With a destination: alternates are the re-searched shortest paths
    from each inbound neighbour of the place to the destination with
    the place removed, ranked as in LANE. Without a destination: the
    served bypass legs (edges carrying bypass_of pointing at a leg
    touching the place) plus, per inbound neighbour, the shortest
    onward path avoiding the place to the neighbour's primary target —
    reported as served bypass legs only (no invented destination)."""
    node = graph.nodes.get(place_id)
    if node is None:
        return {"place_id": place_id, "error": "unknown_place"}
    inbound = sorted({e["from"] for e in graph.edges if e["to"] == place_id})
    outbound = sorted({e["to"] for e in graph.edges if e["from"] == place_id})
    bypass_legs = []
    # Bypass legs are matched by their served bypass_of pointer
    # (engine.graph.edge_key format) against legs touching the place.
    from engine.graph import edge_key
    touching_keys = {edge_key(e) for e in graph.edges
                     if e["from"] == place_id or e["to"] == place_id}
    for e in graph.edges:
        if e.get("bypass_of") in touching_keys:
            bypass_legs.append(leg_view(graph, e))
    result = {"place_id": place_id, "name": node.get("name"),
              "inbound_places": inbound, "outbound_places": outbound,
              "bypass_legs": bypass_legs, "alternates": []}
    if dest:
        alts: dict[tuple, dict] = {}
        for src in inbound:
            alt = shortest_path(graph, src, dest, banned=frozenset({place_id}))
            if alt is None:
                continue
            key = tuple(alt["places"])
            if key not in alts:
                view = path_view(graph, alt)
                view["from_place"] = src
                alts[key] = view
        result["alternates"] = sorted(
            alts.values(),
            key=lambda a: (a["total_transit_d"],
                           -(a["spare_capacity"] or 0), a["places"]))
    return result
