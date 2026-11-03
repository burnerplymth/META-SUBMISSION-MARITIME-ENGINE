#!/usr/bin/env python3
"""Build data/graph/sea_routes.json: real sea-lane distances for EVERY sea leg of the graph.

SIDECAR to data/graph/graph.json (which is never edited; its sha256 is pinned here).
Source network: MARNET as shipped in the `searoute` Python package (searoute-py 1.6.0,
Apache-2.0; the network and algorithm descend from Eurostat's SeaRoute Java library,
EUPL-1.2, https://github.com/eurostat/searoute). The package is a GENERATION-TIME
dependency only: the engine reads the sidecar, never the package.

What is stored (deterministic, no RNG):
  nodes   every sea place (ports and chokepoints, CORE and SHELL): coordinates, region tag,
          nearest network node, approach distance (ports only), whether it is usable
  legs    every sea leg of graph.json (all tiers): lane nautical miles, passages traversed,
          simplified waypoints, and ``method`` = "network" (real lane geometry) or
          "fallback" (the endpoint is too far from the network or no path exists; the
          engine then keeps the graph's own transit days for that leg). Both directions
          of an unordered pair share one route.
  pairs   lane nautical miles between every unordered pair of CORE sea places, the
          chokepoints each route passes (proximity to the lane), and, for every pair
          that passes a gate with a network passage, the route that AVOIDS that passage
          (``alt``): nm and the chokepoints it passes.

Usage (searoute must be importable; it is NOT in the repo venv):
    pip install --target /tmp/sr searoute
    PYTHONPATH=/tmp/sr:server python scripts/build_sea_routes.py [--workers 4]
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from multiprocessing import Pool
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "server"))

from engine.graph import load_graph  # noqa: E402

OUT = REPO / "data" / "graph" / "sea_routes.json"
OUT_SHA = REPO / "data" / "graph" / "sea_routes.sha256"
BASE_RESTRICTIONS = ["northwest", "bering"]
SCHEMA = "supermap.sea_routes/1"
DP_TOL_DEG = 0.35            # waypoint simplification tolerance (Douglas-Peucker)
SNAP_LIMIT_NM = 150.0        # a port further than this from the network has no real geometry
CHOKE_RADIUS_NM = 150.0      # a chokepoint is "passed" if the lane comes within this distance
KM_PER_NM = 1.852
# graph chokepoint -> network passage name (searoute Passage vocabulary)
# a gate alternative that must not use another interoceanic canal ("<passage>+canals" variants)
CANAL_PASSAGES = ["suez", "panama"]
GATE_PASSAGES = {"chk_suez": "suez", "chk_babelmandeb": "babalmandab", "chk_panama": "panama",
                 "chk_malacca": "malacca", "chk_gibraltar": "gibraltar", "chk_hormuz": "ormuz",
                 "chk_turkish": "bosporus"}

_G = {}


def _init():
    import searoute as sr
    from searoute.searoute import get_graphs
    _G["sr"] = sr
    _G["M"], _G["P"] = get_graphs()


def _route(job):
    """job = (key, (lon,lat), (lon,lat), extra_restrictions) -> (key, nm, passages, coords | None)."""
    key, pa, pb, extra = job
    sr = _G["sr"]
    try:
        f = sr.searoute(pa, pb, units="naut", restrictions=BASE_RESTRICTIONS + list(extra),
                        return_passages=True, M=_G["M"], P=_G["P"])
    except Exception:
        return (key, None, [], None)
    nm = f.properties["length"]
    if not (nm == nm) or nm == float("inf") or not f.geometry["coordinates"]:
        return (key, None, [], None)
    return (key, nm, sorted(f.properties.get("traversed_passages") or []), f.geometry["coordinates"])


def _dp(points, tol):
    if len(points) < 3:
        return list(points)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        i, j = stack.pop()
        (x1, y1), (x2, y2) = points[i], points[j]
        dx, dy = x2 - x1, y2 - y1
        den = dx * dx + dy * dy
        best, bi = -1.0, -1
        for k in range(i + 1, j):
            x0, y0 = points[k]
            if den == 0:
                d = math.hypot(x0 - x1, y0 - y1)
            else:
                t = max(0.0, min(1.0, ((x0 - x1) * dx + (y0 - y1) * dy) / den))
                d = math.hypot(x0 - (x1 + t * dx), y0 - (y1 + t * dy))
            if d > best:
                best, bi = d, k
        if best > tol:
            keep[bi] = True
            stack.append((i, bi))
            stack.append((bi, j))
    return [p for p, k in zip(points, keep) if k]


def _near_nm(lon, lat, coords):
    """Minimum distance in nm from a point to a polyline (local equirectangular)."""
    best = float("inf")
    k = math.cos(math.radians(lat))
    for (x1, y1), (x2, y2) in zip(coords, coords[1:]):
        # unwrap along the segment so a trans-Pacific hop is not read the long way round
        dx1 = ((x1 - lon + 180) % 360 - 180) * k
        dx2 = dx1 + (((x2 - x1) + 180) % 360 - 180) * k
        ax, ay, bx, by = dx1, y1 - lat, dx2, y2 - lat
        vx, vy = bx - ax, by - ay
        den = vx * vx + vy * vy
        t = 0.0 if den == 0 else max(0.0, min(1.0, -(ax * vx + ay * vy) / den))
        d = math.hypot(ax + t * vx, ay + t * vy) * 60.0
        best = min(best, d)
    return best


def _region(lat, lon):
    """Coarse region tag for the coverage report only (never used by the engine)."""
    if lon <= -30:
        if lat >= 15:
            return "North America East" if lon > -105 else "North America West"
        return "Latin America"
    if lon <= 40 and lat >= 35:
        return "Europe (North and Baltic)" if lat >= 45 else "Mediterranean and Black Sea"
    if lon <= 40 and lat >= 12:
        return "Mediterranean and Black Sea" if lon <= 36 and lat >= 30 else "Middle East and Red Sea"
    if lon <= 52 and lat >= -35 and lat < 12:
        return "Africa"
    if lon <= 20:
        return "Africa" if lat < 35 else "Europe (North and Baltic)"
    if lon <= 75 and lat >= 12:
        return "Middle East and Red Sea" if lon <= 63 else "South Asia"
    if lon <= 100 and lat >= 5:
        return "South Asia"
    if lat >= 20 and lon >= 100:
        return "East Asia"
    if lat >= -12 and lon >= 95:
        return "Southeast Asia"
    if lat < -12 and lon >= 110:
        return "Oceania"
    return "Africa"


def _sha_obj(doc):
    return hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    workers = int(argv[argv.index("--workers") + 1]) if "--workers" in argv else 4
    from searoute.searoute import get_graphs
    from searoute.utils import haversine

    graph_path = REPO / "data" / "graph" / "graph.json"
    graph_sha = hashlib.sha256(graph_path.read_bytes()).hexdigest()
    graph = load_graph(graph_path)
    sea = {i: n for i, n in graph.nodes.items() if n["mode"] == "sea"}
    ids = sorted(sea)
    core_ids = [i for i in ids if sea[i].get("tier", "core") == "core"]
    M, _P = get_graphs()
    nodes = {}
    for i in ids:
        n = sea[i]
        q = (float(n["lon"]), float(n["lat"]))
        nn = M.kdtree.query(q)
        snap = haversine(q, nn) / KM_PER_NM
        appr = 0.0 if n["kind"] == "chokepoint" else snap
        nodes[i] = {"lat": float(n["lat"]), "lon": float(n["lon"]), "kind": n["kind"],
                    "tier": n.get("tier", "core"), "region": _region(float(n["lat"]), float(n["lon"])),
                    "network_node": [nn[0], nn[1]], "approach_nm": round(appr, 1),
                    "snap_nm": round(snap, 1), "on_network": bool(snap <= SNAP_LIMIT_NM or n["kind"] == "chokepoint")}
    chokes = {i: (nodes[i]["lon"], nodes[i]["lat"]) for i in core_ids if nodes[i]["kind"] == "chokepoint"}

    def pkey(a, b):
        return f"{a}|{b}" if a < b else f"{b}|{a}"

    leg_keys = {}
    for e in graph.edges:
        if e["mode"] == "sea" and e["from"] in sea and e["to"] in sea:
            leg_keys[f'{e["from"]}->{e["to"]}:sea'] = (e["from"], e["to"])
    need = set()
    for a, b in leg_keys.values():
        if nodes[a]["on_network"] and nodes[b]["on_network"]:
            need.add(pkey(a, b))
    for x in range(len(core_ids)):
        for y in range(x + 1, len(core_ids)):
            need.add(pkey(core_ids[x], core_ids[y]))
    jobs = []
    for k in sorted(need):
        a, b = k.split("|")
        jobs.append((k, (nodes[a]["lon"], nodes[a]["lat"]), (nodes[b]["lon"], nodes[b]["lat"]), []))
    print(f"routing {len(jobs)} pairs", file=sys.stderr)
    with Pool(workers, initializer=_init) as pool:
        res = pool.map(_route, jobs, chunksize=32)
    routes = {}
    for key, nm, via, co in res:
        if nm is None:
            routes[key] = None
            continue
        a, b = key.split("|")
        routes[key] = {"nm": round(nm + nodes[a]["approach_nm"] + nodes[b]["approach_nm"], 1),
                       "passages": via, "coords": co}

    def passed(co):
        return sorted(c for c, (lo, la) in chokes.items() if _near_nm(lo, la, co) <= CHOKE_RADIUS_NM)

    # ---- restricted (avoid-the-gate) alternatives for core pairs
    ajobs, akeys = [], {}
    for k, r in routes.items():
        a, b = k.split("|")
        if r is None or nodes[a]["tier"] != "core" or nodes[b]["tier"] != "core":
            continue
        for chk, pas in GATE_PASSAGES.items():
            if pas in r["passages"]:
                ak = f"{k}|{pas}"
                akeys[ak] = chk
                ajobs.append((ak, (nodes[a]["lon"], nodes[a]["lat"]), (nodes[b]["lon"], nodes[b]["lat"]), [pas]))
                if pas in ("suez", "babalmandab"):
                    ck = f"{k}|{pas}+canals"
                    ajobs.append((ck, (nodes[a]["lon"], nodes[a]["lat"]), (nodes[b]["lon"], nodes[b]["lat"]),
                                  sorted({pas, *CANAL_PASSAGES})))
    print(f"routing {len(ajobs)} avoid-the-gate alternatives", file=sys.stderr)
    with Pool(workers, initializer=_init) as pool:
        ares = pool.map(_route, ajobs, chunksize=32)
    alts = {}
    for ak, nm, via, co in ares:
        a, b, pas = ak.split("|")
        if nm is None:
            alts[ak] = None
            continue
        alts[ak] = {"nm": round(nm + nodes[a]["approach_nm"] + nodes[b]["approach_nm"], 1),
                    "passages": via, "chokepoints": passed(co)}

    pairs = {}
    for k in sorted(routes):
        a, b = k.split("|")
        r = routes[k]
        if r is None or nodes[a]["tier"] != "core" or nodes[b]["tier"] != "core":
            continue
        ent = {"nm": r["nm"], "chokepoints": passed(r["coords"])}
        if r["passages"]:
            ent["passages"] = r["passages"]
        al = {}
        for pas in GATE_PASSAGES.values():
            for suffix in ("", "+canals"):
                if f"{k}|{pas}{suffix}" in alts:
                    al[pas + suffix] = alts[f"{k}|{pas}{suffix}"]
        if al:
            ent["alt"] = al
        pairs[k] = ent
    legs, fallback = {}, {}
    for key, (a, b) in sorted(leg_keys.items()):
        k = pkey(a, b)
        r = routes.get(k)
        if r is None:
            why = ("endpoint farther than %g nm from the network" % SNAP_LIMIT_NM
                   if not (nodes[a]["on_network"] and nodes[b]["on_network"]) else "no path on the network")
            fallback[key] = {"reason": why}
            continue
        co = r["coords"]
        if a > b:
            co = list(reversed(co))
        wp = [[round(x, 3), round(y, 3)] for x, y in _dp([(c[0], c[1]) for c in co], DP_TOL_DEG)]
        legs[key] = {"nm": r["nm"], "passages": r["passages"], "waypoints": wp}
    refs = []
    sg, rt, sh = "sea_singapore", "sea_rotterdam", "sea_shanghai"
    _init()

    def _ref(a, b, extra, label):
        k, nm, via, _co = _route(("ref", (nodes[a]["lon"], nodes[a]["lat"]), (nodes[b]["lon"], nodes[b]["lat"]), extra))
        return {"label": label, "from": a, "to": b, "avoid": extra,
                "nm": round(nm + nodes[a]["approach_nm"] + nodes[b]["approach_nm"], 1), "passages": via}

    refs.append(_ref(sg, rt, [], "Singapore-Rotterdam, shortest (Suez)"))
    refs.append(_ref(sg, rt, ["suez", "panama"], "Singapore-Rotterdam, Cape (Suez and Panama avoided)"))
    refs.append(_ref(sh, rt, [], "Shanghai-Rotterdam, shortest (Suez)"))
    refs.append(_ref(sh, rt, ["suez", "panama"], "Shanghai-Rotterdam, Cape (Suez and Panama avoided)"))
    refs.append(_ref(sh, "sea_newyork", ["suez"], "Shanghai-New York, Suez avoided (Panama)"))
    refs.append(_ref(sh, "sea_newyork", ["panama"], "Shanghai-New York, Panama avoided (Suez)"))
    doc = {
        "schema": SCHEMA,
        "status": ("SIDECAR to data/graph/graph.json (additive, opt-in; the sidecar never edits the "
                   "graph file). Distances are MODELLED from a public maritime route network; "
                   "they are lane distances, not routing advice."),
        "graph_pin": {"file_sha256": graph_sha, "canonical_hash": graph.hash},
        "provenance": {
            "network": ("MARNET maritime route network as shipped in searoute-py 1.6.0 "
                        "(https://github.com/genthalili/searoute-py, PyPI 'searoute', licence Apache-2.0), "
                        "descended from Eurostat SeaRoute (https://github.com/eurostat/searoute, EUPL-1.2)"),
            "package_note": ("the installed package metadata and "
                             "LICENCE.txt say Apache-2.0 (since v1.3.0). The network's upstream licence is EUPL-1.2; "
                             "distances derived from it are attributed here"),
            "restrictions": BASE_RESTRICTIONS,
            "restriction_reason": "container services do not use the Arctic routes",
            "units": "nautical miles (1 nm = 1.852 km)",
            "pair_rule": ("shortest path between the network nodes nearest to the two places "
                          "+ port approach distance at both ends (great-circle, ports only)"),
            "snap_limit_nm": SNAP_LIMIT_NM, "chokepoint_radius_nm": CHOKE_RADIUS_NM,
            "gate_passages": GATE_PASSAGES, "canal_passages": CANAL_PASSAGES,
            "waypoint_simplification": f"Douglas-Peucker, tolerance {DP_TOL_DEG} degrees",
            "not_for_routing": ("the package states it is for realistic-looking routes, not for mariners; "
                                "used here only to replace straight chords by lane distances"),
        },
        "coverage": {"sea_places": len(ids), "core_places": len(core_ids),
                     "sea_legs": len(leg_keys), "legs_network": len(legs), "legs_fallback": len(fallback),
                     "core_pairs": len(pairs), "alt_routes": sum(len(p.get("alt", {})) for p in pairs.values())},
        "reference_voyages_computed": refs,
        "nodes": nodes,
        "pairs": pairs,
        "legs": legs,
        "fallback_legs": fallback,
    }
    doc["content_sha256"] = _sha_obj({k: v for k, v in doc.items() if k != "content_sha256"})
    OUT.write_text(json.dumps(doc, sort_keys=True, indent=None, separators=(",", ":")) + "\n",
                   encoding="utf-8")
    sha = hashlib.sha256(OUT.read_bytes()).hexdigest()
    OUT_SHA.write_text(sha + "  data/graph/sea_routes.json\n", encoding="utf-8")
    print(json.dumps({"file_sha256": sha, "content_sha256": doc["content_sha256"],
                      "coverage": doc["coverage"], "bytes": OUT.stat().st_size, "refs": refs}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
