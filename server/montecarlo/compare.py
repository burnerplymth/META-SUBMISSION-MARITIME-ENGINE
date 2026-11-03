"""Compare two finished Monte Carlo jobs (A against B).

Pure and deterministic. Differences are B minus A. When the two jobs share
seed, n and input names they are PAIRED: simulation i of A pairs with
simulation i of B (and for every input whose distribution is the same in
both, the sampled value is identical: common random numbers).
"""
from __future__ import annotations

import random

from . import LABEL, stats
from .metrics import METRIC_NAMES, UNITS

BOOT_RESAMPLES = 1000
BOOT_SEED = 1
BOOT_MIN_N = 50
BOOT_LEVEL = 0.95
TOP_PLACES = 20
TOP_SERIES = 5
PRIMARY = "worst_added_transit_d"

_SPEC_KEYS = ("dist", "low", "mode", "high")


def _band(vals_sorted: list, vals: list) -> dict:
    return {"p10": stats.r6(stats.percentile(vals_sorted, 10)),
            "p50": stats.r6(stats.percentile(vals_sorted, 50)),
            "p90": stats.r6(stats.percentile(vals_sorted, 90)),
            "mean": stats.r6(stats.mean(vals))}


def joint_histogram(va: list, vb: list, bins: int = stats.HIST_BINS) -> dict:
    """Counts of A and B on shared equal-width edges spanning both ranges."""
    lo, hi = min(min(va), min(vb)), max(max(va), max(vb))
    if lo == hi:
        return {"edges": [stats.r6(lo), stats.r6(hi)], "a_counts": [len(va)],
                "b_counts": [len(vb)]}
    width = (hi - lo) / bins
    edges = [stats.r6(lo + width * i) for i in range(bins)] + [stats.r6(hi)]

    def count(vals):
        out = [0] * bins
        for v in vals:
            out[min(max(int((v - lo) / width), 0), bins - 1)] += 1
        return out
    return {"edges": edges, "a_counts": count(va), "b_counts": count(vb)}


def overlap_coefficient(h: dict, na: int, nb: int) -> float:
    """Sum over bins of the smaller bin share; 1 = coincide, 0 = disjoint."""
    if len(h["a_counts"]) == 1:
        return 1.0
    total = 0.0
    for ca, cb in zip(h["a_counts"], h["b_counts"]):
        total += min(ca / na, cb / nb)
    return max(0.0, min(1.0, stats.r6(total)))


def _spec(inp: dict) -> tuple:
    return tuple(inp[k] for k in _SPEC_KEYS)


def _median(vals: list) -> float:
    return stats.percentile(sorted(vals), 50)


def bootstrap_shift(diffs_by_metric: dict, n: int) -> dict:
    """Paired bootstrap interval of the median difference, per metric. The
    resample indices are drawn once (fixed seed) and shared by all metrics."""
    rng = random.Random(BOOT_SEED)
    meds = {m: [] for m in diffs_by_metric}
    for _ in range(BOOT_RESAMPLES):
        idx = [int(rng.random() * n) for _ in range(n)]
        for m, d in diffs_by_metric.items():
            meds[m].append(_median([d[i] for i in idx]))
    alpha = (1.0 - BOOT_LEVEL) / 2.0 * 100.0
    out = {}
    for m, vals in meds.items():
        s = sorted(vals)
        out[m] = {"low": stats.r6(stats.percentile(s, alpha)),
                  "high": stats.r6(stats.percentile(s, 100.0 - alpha)),
                  "level": BOOT_LEVEL, "resamples": BOOT_RESAMPLES, "seed": BOOT_SEED}
    return out


def _fan(pairs: list, n: int, grid: list) -> dict:
    cols = {10: [], 50: [], 90: []}
    for t in grid:
        vals = sorted([0.0] * (n - len(pairs)) + [d if tf <= t else 0.0 for tf, d in pairs])
        for q in cols:
            cols[q].append(stats.r6(stats.percentile(vals, q)))
    return {"p10": cols[10], "p50": cols[50], "p90": cols[90]}


def _side(rec: dict) -> dict:
    return {"job_id": rec["id"], "request": rec["request"], "engine": rec["engine"],
            "inputs": rec["inputs"]}


def compare(ra: dict, rb: dict, threshold: float = 0.0) -> dict:
    na, nb = len(ra["rows"]), len(rb["rows"])
    names_a = [i["name"] for i in ra["inputs"]]
    names_b = [i["name"] for i in rb["inputs"]]
    paired = (ra["request"]["seed"] == rb["request"]["seed"] and na == nb
              and names_a == names_b)
    spec_b = {i["name"]: _spec(i) for i in rb["inputs"]}
    common = [i["name"] for i in ra["inputs"] if spec_b.get(i["name"]) == _spec(i)]

    metrics, hist_by, diffs = {}, {}, {}
    for m in METRIC_NAMES:
        va = [float(r["outputs"][m]) for r in ra["rows"]]
        vb = [float(r["outputs"][m]) for r in rb["rows"]]
        sa, sb = sorted(va), sorted(vb)
        ba, bb = _band(sa, va), _band(sb, vb)
        h = joint_histogram(va, vb)
        hist_by[m] = h
        entry = {"unit": UNITS[m], "a": ba, "b": bb,
                 "delta_p50": stats.r6(bb["p50"] - ba["p50"]),
                 "delta_p90": stats.r6(bb["p90"] - ba["p90"]),
                 "overlap": overlap_coefficient(h, na, nb),
                 "share_b_worse": None, "shift_ci": None}
        if paired:
            d = [y - x for x, y in zip(va, vb)]
            k = sum(1 for x in d if x > threshold)
            entry["share_b_worse"] = {"k": k, "n": na, "share": stats.r6(k / na)}
            diffs[m] = d
        metrics[m] = entry
    if paired and na >= BOOT_MIN_N:
        for m, ci in bootstrap_shift(diffs, na).items():
            metrics[m]["shift_ci"] = ci

    pta, ptb = ra.get("place_table") or {}, rb.get("place_table") or {}
    zero = [0.0, 0.0, 0.0, 0]
    name_of = {p["place"]: p["name"] for rec in (ra, rb)
               for p in rec["results"]["per_place"]}
    for rec in (ra, rb):
        for s in rec["results"]["series"]["places"]:
            name_of.setdefault(s["place"], s["name"])
    rows = []
    for place in sorted(set(pta) | set(ptb)):
        a, b = pta.get(place, zero), ptb.get(place, zero)
        rows.append({"place": place, "name": name_of.get(place, place),
                     "a": {"p50": a[1], "p90": a[2]}, "b": {"p50": b[1], "p90": b[2]},
                     "delta_p50": stats.r6(b[1] - a[1]), "delta_p90": stats.r6(b[2] - a[2])})
    rows.sort(key=lambda r: (-abs(r["delta_p90"]), r["place"]))
    per_place = rows[:TOP_PLACES]

    raw_a, raw_b = ra.get("series_raw") or {}, rb.get("series_raw") or {}
    shared = sorted(set(raw_a) & set(raw_b),
                    key=lambda p: (-max(pta.get(p, zero)[2], ptb.get(p, zero)[2]), p))[:TOP_SERIES]
    t_max = max((t for p in shared for src in (raw_a, raw_b) for t, _d in src[p]), default=0.0)
    steps = stats.SERIES_POINTS - 1
    grid = [0.0] if t_max <= 0 else [stats.r6(t_max * i / steps) for i in range(steps + 1)]
    series = {"t_days": grid, "places": [
        {"place": p, "name": name_of.get(p, p),
         "a": _fan([tuple(x) for x in raw_a[p]], na, grid),
         "b": _fan([tuple(x) for x in raw_b[p]], nb, grid)} for p in shared]}

    return {
        "label": LABEL, "a": _side(ra), "b": _side(rb), "paired": paired,
        "threshold": threshold, "n_a": na, "n_b": nb, "common_inputs": common,
        "metrics": metrics, "per_place": per_place,
        "histograms": {"metric": PRIMARY, **hist_by[PRIMARY]},
        "histograms_by_metric": hist_by, "series": series,
    }
