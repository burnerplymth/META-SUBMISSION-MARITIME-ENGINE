"""Turn the per-simulation rows into the contract's ``results`` block.

Pure and deterministic: a function of (n, seed, inputs, rows, place pairs).
``rows`` is the ordered per-simulation record ({i, inputs, outputs,
stream_hash}); ``place_pairs`` is {place: [(first_arrival_day, delay_d), ...]}
holding one pair per simulation in which the place appears (a simulation
where it does not appear counts as a delay of 0).
"""
from __future__ import annotations

import math

from . import stats
from .metrics import METRIC_NAMES, NOT_DEFINED, UNITS

try:  # dual-import convention
    from engine.canonical import sha256_hex
except Exception:  # imported as server.montecarlo.* from the repo root
    from server.engine.canonical import sha256_hex  # type: ignore

TOP_PLACES = 20
TOP_SERIES = 5


def runs_digest(hashes: list) -> str:
    return sha256_hex("".join(hashes))


def _place_dist(pairs: list, n: int) -> list:
    return sorted([0.0] * (n - len(pairs)) + [d for _t, d in pairs])


def finalize(n: int, seed: int, inputs: list, rows: list, place_pairs: dict, names: dict):
    metrics = {}
    for name in METRIC_NAMES:
        vals = [float(r["outputs"][name]) for r in rows]
        metrics[name] = stats.metric_block(vals, UNITS[name])

    ranked = []
    for place, pairs in place_pairs.items():
        dist = _place_dist(pairs, n)
        p10, p50, p90 = (stats.percentile(dist, p) for p in (10, 50, 90))
        ranked.append((-p90, -p50, -len(pairs), place, dist, len(pairs)))
    ranked.sort(key=lambda t: t[:4])
    per_place = []
    for _a, _b, _c, place, dist, k in ranked[:TOP_PLACES]:
        per_place.append({
            "place": place, "name": names.get(place, place),
            "added_delay_d": {"p10": stats.r6(stats.percentile(dist, 10)),
                              "p50": stats.r6(stats.percentile(dist, 50)),
                              "p90": stats.r6(stats.percentile(dist, 90))},
            "affected_share": stats.r6(k / n), "affected_k": k})

    top = [p["place"] for p in per_place[:TOP_SERIES]]
    t_max = max((t for p in top for t, _d in place_pairs[p]), default=0.0)
    steps = stats.SERIES_POINTS - 1
    grid = [0.0] if t_max <= 0 else [stats.r6(t_max * i / steps) for i in range(steps + 1)]
    series_places = []
    for p in top:
        pairs = place_pairs[p]
        cols = {10: [], 50: [], 90: []}
        for t in grid:
            vals = sorted([0.0] * (n - len(pairs)) + [d if tf <= t else 0.0 for tf, d in pairs])
            for q in cols:
                cols[q].append(stats.r6(stats.percentile(vals, q)))
        series_places.append({"place": p, "name": names.get(p, p),
                              "p10": cols[10], "p50": cols[50], "p90": cols[90]})

    worst = [float(r["outputs"]["worst_added_transit_d"]) for r in rows]
    sens = []
    for spec in inputs:
        xs = [float(r["inputs"][spec["name"]]) for r in rows]
        rc, sw = stats.spearman(xs, worst), stats.swing(xs, worst)
        sens.append({"input": spec["name"],
                     "rank_corr": None if rc is None else stats.r6(rc),
                     "swing_p10_p90": None if sw is None else stats.r6(sw),
                     # the same number under a name that says what it is (a difference of two
                     # group means, not of two percentiles); swing_p10_p90 stays for older readers
                     "swing_top_vs_bottom_tenth": None if sw is None else stats.r6(sw)})

    place_table = {}
    for _a, _b, _c, place, dist, k in ranked:
        place_table[place] = [stats.r6(stats.percentile(dist, 10)),
                              stats.r6(stats.percentile(dist, 50)),
                              stats.r6(stats.percentile(dist, 90)), k]
    series_raw = {}
    for _a, _b, _c, place, _dist, _k in ranked[:TOP_PLACES]:
        series_raw[place] = [[t, d] for t, d in place_pairs[place]]
    results = {
        "n": n, "seed": seed,
        "runs_digest": runs_digest([r["stream_hash"] for r in rows]),
        "metrics": metrics,
        "metrics_not_defined": [dict(x) for x in NOT_DEFINED],
        "per_place": per_place,
        "series": {"t_days": grid, "places": series_places},
        "sensitivity": sens,
        "convergence": stats.convergence(worst),
    }
    # kept beside the results (not part of the contract's results block): the
    # compare route needs every place's p10/p50/p90 and the top places' raw pairs
    return results, {"place_table": place_table, "series_raw": series_raw}
