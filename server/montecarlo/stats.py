"""Summary statistics for the Monte Carlo layer: pure functions, numpy-free.

Every function is a deterministic function of its arguments (math.fsum for
sums, a total order for ties); none reads a clock or a random source.
Percentiles use linear interpolation between order statistics (position
p x (n - 1) in the sorted values, the common "type 7" rule).
"""
from __future__ import annotations

import math

ND = 6          # decimals every reported number is rounded to
HIST_BINS = 20
MAX_THRESHOLDS = 6
SERIES_POINTS = 25


def r6(x):
    return None if x is None else round(float(x), ND)


def percentile(sorted_vals: list, p: float) -> float:
    """p in [0, 100]; ``sorted_vals`` ascending and non-empty."""
    n = len(sorted_vals)
    if n == 0:
        raise ValueError("percentile of an empty sample")
    if n == 1:
        return float(sorted_vals[0])
    pos = (p / 100.0) * (n - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    return float(sorted_vals[lo]) + (float(sorted_vals[hi]) - float(sorted_vals[lo])) * frac


def mean(vals: list) -> float:
    return math.fsum(vals) / len(vals)


def pstd(vals: list) -> float:
    """Population standard deviation."""
    m = mean(vals)
    return math.sqrt(math.fsum((v - m) ** 2 for v in vals) / len(vals))


def histogram(vals: list, bins: int = HIST_BINS) -> dict:
    lo, hi = min(vals), max(vals)
    if lo == hi:
        return {"edges": [r6(lo), r6(hi)], "counts": [len(vals)]}
    width = (hi - lo) / bins
    edges = [r6(lo + width * i) for i in range(bins)] + [r6(hi)]
    counts = [0] * bins
    for v in vals:
        k = int((v - lo) / width)
        counts[min(max(k, 0), bins - 1)] += 1
    return {"edges": edges, "counts": counts}


def thresholds(lo: float, hi: float) -> list:
    """Up to MAX_THRESHOLDS values of the 1-2-5 progression strictly inside (lo, hi)."""
    if not hi > lo:
        return []
    cands = sorted({r6(m * 10.0 ** e) for e in range(-3, 6) for m in (1, 2, 5)})
    inside = [c for c in cands if lo < c < hi]
    if len(inside) > MAX_THRESHOLDS:
        idx = sorted({round(i * (len(inside) - 1) / (MAX_THRESHOLDS - 1))
                      for i in range(MAX_THRESHOLDS)})
        inside = [inside[i] for i in idx]
    return inside


def exceedance(vals: list) -> list:
    n = len(vals)
    out = []
    for th in thresholds(min(vals), max(vals)):
        k = sum(1 for v in vals if v > th)
        out.append({"threshold": th, "k": k, "n": n, "share": r6(k / n)})
    return out


def metric_block(vals: list, unit: str) -> dict:
    s = sorted(vals)
    return {
        "unit": unit, "n": len(vals), "mean": r6(mean(vals)), "std": r6(pstd(vals)),
        "min": r6(s[0]), "max": r6(s[-1]),
        "p05": r6(percentile(s, 5)), "p10": r6(percentile(s, 10)),
        "p25": r6(percentile(s, 25)), "p50": r6(percentile(s, 50)),
        "p75": r6(percentile(s, 75)), "p90": r6(percentile(s, 90)),
        "p95": r6(percentile(s, 95)),
        "histogram": histogram(vals), "exceedance": exceedance(vals),
    }


def ranks(vals: list) -> list:
    """Average ranks (1-based; ties share the mean of their positions)."""
    order = sorted(range(len(vals)), key=lambda i: (vals[i], i))
    out = [0.0] * len(vals)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            out[order[k]] = avg
        i = j + 1
    return out


def spearman(xs: list, ys: list):
    """Spearman rank correlation, or None when either column is constant."""
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    rx, ry = ranks(xs), ranks(ys)
    mx, my = mean(rx), mean(ry)
    sxx = math.fsum((a - mx) ** 2 for a in rx)
    syy = math.fsum((b - my) ** 2 for b in ry)
    if sxx == 0.0 or syy == 0.0:
        return None
    sxy = math.fsum((a - mx) * (b - my) for a, b in zip(rx, ry))
    return max(-1.0, min(1.0, sxy / math.sqrt(sxx * syy)))


def swing(xs: list, ys: list):
    """Mean output of the highest-input tenth minus that of the lowest-input
    tenth (at least one simulation each; ties broken by simulation index)."""
    n = len(xs)
    if n < 2 or min(xs) == max(xs):
        return None
    k = max(1, math.ceil(n * 0.1))
    order = sorted(range(n), key=lambda i: (xs[i], i))
    low = [ys[i] for i in order[:k]]
    high = [ys[i] for i in order[-k:]]
    return mean(high) - mean(low)


def convergence(vals: list, steps: int = 8) -> list:
    n = len(vals)
    out, seen = [], set()
    for i in range(1, steps + 1):
        m = min(n, math.ceil(i * n / steps))
        if m in seen:
            continue
        seen.add(m)
        s = sorted(vals[:m])
        out.append({"n": m, "p50": r6(percentile(s, 50)), "p90": r6(percentile(s, 90))})
    return out
