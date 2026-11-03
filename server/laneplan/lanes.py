"""Lane-geometry fields of the lane planner: additive, MODELLED.

Per route the planner already returns chord-based ``km`` and days (the engine's time basis, unchanged).
This module adds beside them:

  geometry    the route as lane polylines, leg by leg (state ``lane`` = a land-checked lane from
              data/graph/lane_geometry.json; ``approx`` = the straight chord, drawn dashed and labelled
              Approx.: the leg has no lane geometry, or its polyline is flagged)
  length_km   lane nautical miles x 1.852 for every leg that has them (the hash-pinned sea_routes
              sidecar), the chord for the others
  lane        sail days computed from those nautical miles at a named speed per variant, the unchanged
              queue block, the total, the voyage timeline and the difference to the baseline route

Days = nautical miles / (speed x 24 h); no port time is added (dwell is the place's queue). Speeds are the
engine-rev-2d registry's: BASE is its service speed for the route's length class (15.6 kn from 3,000 nm,
13.8 kn below; both SOURCED there), SLOW is the lower of the two, FAST is the sim's fastest sea class
(23 kn, MODELLED). LOW <= MEDIAN <= HIGH holds by construction (FAST >= BASE >= SLOW).
"""
from __future__ import annotations

import math

FAST_KN = 23.0
KM_PER_NM = 1.852


class LaneContext:
    def __init__(self, sea, lg):
        self.sea = sea
        self.lg = lg


def context():
    """(sea_routes, lane geometry) or None when either sidecar is not usable (the plan is then as before)."""
    try:
        try:
            from engine2.sea_routes import load_sea_routes
            from lanegeo.store import load_lane_geometry
        except Exception:  # pragma: no cover - imported as server.* from the repo root
            from server.engine2.sea_routes import load_sea_routes  # type: ignore
            from server.lanegeo.store import load_lane_geometry  # type: ignore
        return LaneContext(load_sea_routes(), load_lane_geometry())
    except Exception:
        return None


def digest(ctx):
    if ctx is None:
        return None
    return {"sea_routes_content_sha256": ctx.sea.content_sha256,
            "lane_geometry_content_sha256": ctx.lg.content_sha256}


def basis(ctx) -> dict:
    if ctx is None:
        return {"state": "NOT SERVED", "label": "MODELLED"}
    sea = ctx.sea
    return {"state": "SERVED", "label": "MODELLED",
            "sea_routes": sea.digest(), "lane_geometry": ctx.lg.digest(),
            "rule": "days = lane nautical miles / (speed x 24 h), no port time",
            "speeds_kn": {"fast": FAST_KN, "base_long_haul": sea.v_long, "base_regional": sea.v_regional,
                          "boundary_nm": sea.boundary_nm, "slow": sea.v_regional},
            "variants": [{"id": "SPEED-FAST", "label": "MODELLED"},
                         {"id": "SPEED-BASE", "label": "MODELLED"},
                         {"id": "SPEED-SLOW", "label": "MODELLED"}],
            "stats": ctx.lg.stats()["pairs_by_status"]}


def _leg_nm(sea, a: str, b: str):
    for k in (f"{a}->{b}:sea", f"{b}->{a}:sea"):
        lg = sea.leg(k)
        if lg is not None:
            return float(lg["nm"])
    return None


def _r(x, nd=3):
    return None if x is None else round(float(x), nd)


def attach(r: dict, net, ctx, dis, waits_by_variant, base_vid, timeline_fn, arrival_fn=None) -> None:
    try:
        from lanegeo.store import split_antimeridian
    except Exception:  # pragma: no cover
        from server.lanegeo.store import split_antimeridian  # type: ignore
    sea, lg = ctx.sea, ctx.lg
    ids = r["_ids"]
    legs, km_list, geo_legs = [], [], []
    for a, b in zip(ids, ids[1:]):
        nm = _leg_nm(sea, a, b)
        poly = lg.polyline(a, b)
        if nm is not None and poly is not None:
            state = "lane"
        elif nm is not None:
            state = "lane_nm"
        else:
            state = "approx"
        chord_km = net.chord(a, b)
        km = nm * KM_PER_NM if nm is not None else chord_km
        km_list.append(km)
        legs.append({"from": a, "to": b, "state": state, "nm": _r(km / KM_PER_NM, 1), "km": _r(km, 1)})
        if state == "lane":
            parts = split_antimeridian(poly)
        else:
            pa, pb = net.nodes[a], net.nodes[b]
            parts = split_antimeridian([(float(pa["lon"]), float(pa["lat"])),
                                        (float(pb["lon"]) + 360.0 * round((float(pa["lon"]) - float(pb["lon"])) / 360.0),
                                         float(pb["lat"]))])
        geo_legs.append({"from": a, "to": b, "state": "lane" if state == "lane" else "approx", "parts": parts})
    total_km = sum(km_list)
    total_nm = total_km / KM_PER_NM
    v_base = sea.speed_kn(total_nm)
    v_slow = min(v_base, sea.v_regional)
    v_fast = max(FAST_KN, v_base)
    d = {c: total_nm / (v * 24.0) for c, v in (("low", v_fast), ("median", v_base), ("high", v_slow))}
    sail = {"low": _r(d["low"]), "median": _r(d["median"]), "high": _r(d["high"]), "unit": "d", "label": "MODELLED",
            "sources": ["sea_routes", "lane_geometry", "sim.speeds"]}
    if dis is None or r.get("queue") is None:
        total = dict(sail)
    else:
        q = r["queue"]
        total = {"low": _r(d["low"] + q["low"]), "median": _r(d["median"] + q["median"]),
                 "high": _r(d["high"] + q["high"]), "unit": "d", "label": "SIMULATED" if q["high"] > 0 else "MODELLED",
                 "sources": sorted(set(sail["sources"]) | set(q["sources"]))}
    dpk = 1.0 / (v_base * 24.0 * KM_PER_NM)
    tl_r = {**r, "_legs_km": km_list}
    timeline = timeline_fn(tl_r, net, dpk, (v_base / v_fast, 1.0, v_base / v_slow), dis, waits_by_variant, base_vid)
    n_lane = sum(1 for x in legs if x["state"] == "lane")
    arrival = None
    if arrival_fn is not None and dis is not None and r.get("queue") is not None:
        # the wait a vessel meets on reaching each place, on lane days (nautical miles / speed per variant)
        leg_c = {c: [(k / KM_PER_NM) / (v * 24.0) for k in km_list]
                 for c, v in (("low", v_fast), ("median", v_base), ("high", v_slow))}
        arrival = arrival_fn(ids, leg_c, d)
    r["length_km"] = _r(total_km, 1)
    r["geometry"] = {"label": "MODELLED", "detail": "full", "legs": geo_legs,
                     "lane_legs": n_lane, "approx_legs": len(legs) - n_lane}
    r["lane"] = {"label": "MODELLED",
                 "km": {"value": _r(total_km, 1), "label": "MODELLED", "sources": ["sea_routes", "lane_geometry", "legs"]},
                 "nm": _r(total_nm, 1), "legs": legs, "lane_legs": n_lane, "approx_legs": len(legs) - n_lane,
                 "speed_kn": {"fast": v_fast, "base": v_base, "slow": v_slow,
                              "class": "long_haul" if total_nm >= sea.boundary_nm else "regional"},
                 "sail": sail, "queue": r.get("queue"), "total": total, "timeline": timeline}
    if arrival is not None:
        r["lane"]["queue_at_arrival"], r["lane"]["queue_at_arrival_by_place"], r["lane"]["total_at_arrival"] = arrival


def vs_baseline(routes: list, baseline: dict) -> None:
    base = baseline["lane"]["sail"]
    for r in routes:
        t = r["lane"]["total"]
        r["lane"]["vs_baseline"] = {"unit": "d", "label": t["label"], "low": _r(t["low"] - base["low"]),
                                    "median": _r(t["median"] - base["median"]), "high": _r(t["high"] - base["high"])}


def check(s: dict, c: dict, ref, scope: str, refs_mod, sea=None, p=None, q=None) -> dict | None:
    """The cape-vs-suez distance check on lane kilometres (never tuned: it reports what the lanes give)."""
    if "lane" not in s or "lane" not in c:
        return None
    s_km, c_km = s["lane"]["km"]["value"], c["lane"]["km"]["value"]
    cut = (c_km - s_km) / c_km * 100.0 if c_km else None
    row = {"suez_km": s_km, "cape_km": c_km, "detour_km": _r(c_km - s_km, 1), "ratio": _r(c_km / s_km, 4),
           "cut_pct": _r(cut, 2), "label": "MODELLED",
           "approx_legs": {"suez": s["lane"]["approx_legs"], "cape": c["lane"]["approx_legs"]},
           "reference": None, "state": "NO CITED RANGE", "flag": False, "sources": ["sea_routes", "lane_geometry"]}
    # the sidecar's own CORE-pair lanes between the same two places (Suez, and the lane that avoids Suez):
    # shown beside the planner's chain of graph legs, never used for the state below
    if sea is not None and p is not None:
        pr = sea.pair(p, q)
        if pr is not None:
            alt = (pr.get("alt") or {}).get("suez")
            if alt is not None and pr["nm"] and alt["nm"]:
                row["sea_routes_pair"] = {"suez_nm": pr["nm"], "cape_nm": alt["nm"],
                                          "cut_pct": _r((alt["nm"] - pr["nm"]) / alt["nm"] * 100.0, 2), "label": "MODELLED"}
    if ref is not None:
        lo, hi = ref["cut_pct"]["lo"], ref["cut_pct"]["hi"]
        outside = cut is not None and not (lo <= cut <= hi)
        row["reference"] = {"id": ref["id"], "scope": scope, "label": ref["label"],
                            "cut_pct": {"lo": _r(lo, 2), "hi": _r(hi, 2)}, "label_state": "SOURCED",
                            "source": refs_mod.UNCTAD_ID}
        row["state"] = "OUTSIDE CITED RANGE" if outside else "WITHIN CITED RANGE"
        row["flag"] = bool(outside)
        row["sources"] = ["sea_routes", "lane_geometry", refs_mod.UNCTAD_ID]
    return row
