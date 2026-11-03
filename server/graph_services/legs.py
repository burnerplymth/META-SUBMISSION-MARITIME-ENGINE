"""Service legs and origin-destination routes from selected services.

Two views of the same rotations:

  LEG view (sim)    consecutive mapped calls -> one leg each, with the
                    chokepoints the source (or the documented placement
                    rule) puts on it. Only THROUGH legs (>= 1 chokepoint)
                    are consumed by derive(); local legs are not.
  ROUTE view (rev 2) every ordered call pair (i, j) of a rotation with no
                    gap between them -> the ordered chokepoints on the
                    legs between, so a graph leg origin -> destination
                    can be bound to the way the services really reach it.

Nothing here reads or writes the graph; ``graph_data`` is only consulted
for coordinates (zones).
"""
from __future__ import annotations

from .zones import crosses_cape, crosses_suez, zone

SUEZ = "chk_suez"
BAB = "chk_babelmandeb"
CAPE = "chk_capeofgoodhope"
PANAMA = "chk_panama"


def _coords(graph_data: dict) -> dict:
    return {n["id"]: (float(n["lat"]), float(n["lon"])) for n in graph_data["nodes"]}


def _leg_via(e: dict, i: int, a: str, b: str, positioned: dict, zones: dict) -> tuple[list, list, str]:
    """(via, implied, placement) for the leg calls[i] -> b of entry ``e``."""
    za, zb = zones[a], zones[b]
    stated: list[str] = []
    placement = "none"
    if i in positioned:
        stated = list(positioned[i])
        placement = "marker"
    else:
        for chk, tag in ([(c, "service_level") for c in e.get("via_service_level", [])]
                         + [(c, "era_default") for c in e.get("via_era_default", [])]):
            if chk == SUEZ and crosses_suez(za, zb) and chk not in stated:
                stated.append(chk)
                placement = tag
            elif chk == CAPE and crosses_cape(za, zb) and chk not in stated:
                stated.append(chk)
                placement = tag
    implied: list[str] = []
    via = list(stated)
    if SUEZ in stated and BAB not in stated:
        if za == "E":                      # eastern end is the origin: Bab first
            idx = via.index(SUEZ)
            via.insert(idx, BAB)
            implied.append(BAB)
        elif zb == "E":                    # eastern end is the destination: Bab last
            idx = via.index(SUEZ)
            via.insert(idx + 1, BAB)
            implied.append(BAB)
    return via, implied, placement


def entry_legs(e: dict, zones: dict) -> list[dict]:
    """All legs of one entry (local legs included, ``via`` empty)."""
    calls = e["calls"]
    n = len(calls)
    gaps = set(e.get("gaps", []))
    positioned: dict[int, list] = {}
    for v in e.get("via", []):
        positioned.setdefault(v["after_call"], []).append(v["chk"])
    caveat = bool(e.get("sequence_caveat"))
    legs: list[dict] = []
    idx_pairs = [(i, i + 1) for i in range(n - 1) if i not in gaps]
    if e.get("loop") and (n - 1) in positioned and calls[n - 1] != calls[0]:
        idx_pairs.append((n - 1, 0))
    for i, j in idx_pairs:
        a, b = calls[i], calls[j]
        if a == b:
            continue
        if caveat and crosses_suez(zones[a], zones[b]):
            continue                       # sequence_caveat: adjacency is not proof of a leg
        via, implied, placement = _leg_via(e, i, a, b, positioned, zones)
        legs.append({"from": a, "to": b, "i": i, "via": via, "implied": implied,
                     "placement": placement, "wrap": j == 0 and i == n - 1})
    return legs


def build_leg_table(selected: list[dict], graph_data: dict) -> dict:
    """Merge the selected entries' THROUGH legs by (from, to)."""
    coords = _coords(graph_data)
    zones = {pid: zone(*ll) for pid, ll in coords.items()}
    variants: dict[tuple, dict[tuple, dict]] = {}
    n_legs = n_through = 0
    for e in selected:
        for lg in entry_legs(e, zones):
            n_legs += 1
            if not lg["via"]:
                continue
            n_through += 1
            key = (lg["from"], lg["to"])
            v = variants.setdefault(key, {}).setdefault(tuple(lg["via"]), {
                "services": [], "placements": set(), "implied": set(), "published": ""})
            if e["service_id"] not in v["services"]:
                v["services"].append(e["service_id"])
            v["placements"].add(lg["placement"])
            v["implied"].update(lg["implied"])
            v["published"] = max(v["published"], e["published"])
    legs: dict[tuple, dict] = {}
    for key in sorted(variants):
        vs = variants[key]
        order = sorted(vs.items(), key=lambda kv: (-len(kv[1]["services"]),
                                                    _neg(kv[1]["published"]), kv[0]))
        via, win = order[0]
        legs[key] = {
            "from": key[0], "to": key[1], "via": list(via),
            "implied": sorted(win["implied"]),
            "placement": sorted(win["placements"]),
            "services": sorted(win["services"]),
            "alternatives": [{"via": list(v), "services": sorted(d["services"])}
                             for v, d in order[1:]],
            "conflict": len(order) > 1,
        }
    return {"legs": legs,
            "stats": {"legs_total_occurrences": n_legs, "through_leg_occurrences": n_through,
                      "through_legs_distinct": len(legs),
                      "conflicts": sum(1 for v in legs.values() if v["conflict"])}}


def _neg(s: str) -> tuple:
    """Sort key making later publication dates sort first."""
    return tuple(-ord(c) for c in s)


def _plausible_route(a: str, b: str, via: list, zones: dict) -> bool:
    """Cargo from ``a`` to ``b`` does not go round the loop: a segment that
    passes the same chokepoint twice, or crosses Suez / the Cape between two
    places on the SAME side of it, is a rotation artefact (e.g. Singapore on
    the outbound leg and Port Klang on the return leg), not an O/D route."""
    if len(set(via)) != len(via):
        return False
    za, zb = zones[a], zones[b]
    if SUEZ in via and not crosses_suez(za, zb):
        return False
    if CAPE in via and not crosses_cape(za, zb):
        return False
    return True


def od_routes(selected: list[dict], graph_data: dict) -> dict:
    """{(origin, destination): {via_tuple: {"services": [...], "n": int}}}
    over every ordered call pair of every selected rotation whose legs are
    all present (no gap, no sequence-caveat cut). ``via`` is the ordered,
    de-duplicated chokepoint list on the legs between the two calls."""
    coords = _coords(graph_data)
    zones = {pid: zone(*ll) for pid, ll in coords.items()}
    out: dict[tuple, dict[tuple, dict]] = {}
    for e in selected:
        calls = e["calls"]
        n = len(calls)
        legs = {lg["i"]: lg for lg in entry_legs(e, zones) if not lg["wrap"]}
        # cargo between two calls takes the SHORTEST segment of the rotation
        # that joins them (fewest legs; first on a tie), never the whole loop
        best: dict[tuple, tuple] = {}
        for i in range(n - 1):
            via: list[str] = []
            for j in range(i, n - 1):
                lg = legs.get(j)
                if lg is None:
                    if calls[j] == calls[j + 1]:
                        pass               # a repeated call is not a break
                    else:
                        break
                else:
                    for c in lg["via"]:
                        if not via or via[-1] != c:
                            via.append(c)
                a, b = calls[i], calls[j + 1]
                if a == b or not _plausible_route(a, b, via, zones):
                    continue
                cur = best.get((a, b))
                if cur is None or (j + 1 - i) < cur[0]:
                    best[(a, b)] = (j + 1 - i, tuple(via))
        for pair, (_len, via_t) in best.items():
            d = out.setdefault(pair, {}).setdefault(via_t, {"services": [], "n": 0})
            d["n"] += 1
            d["services"].append(e["service_id"])
    return out
