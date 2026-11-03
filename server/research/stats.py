"""Descriptive statistics for the research workbench: pure Python, stdlib
only (numpy is not a runtime dependency), deterministic.

Every function takes plain lists; ``None`` marks a missing observation and
is handled explicitly by the callers that accept it. Randomness comes only
from a private ``random.Random(seed)`` created per call site, never from the
global generator, so results are identical under any ``PYTHONHASHSEED``.

Nothing here tests a hypothesis or returns a p-value. Intervals are
percentile bootstrap intervals that describe sampling variability of the
reported number in this sample; they are not forecasts.
"""
from __future__ import annotations

import math
import random
from typing import Callable, Sequence

SEED = 20261029
RESAMPLES = 1000
LEVEL = 0.95
BLOCK_DAYS = 7

# Minimum sample sizes at which a bootstrap interval is offered at all.
MIN_N_MEAN_CI = 14          # per side, before/after
MIN_N_EVENTS_CI = 5         # events, event study
MIN_N_MEDIAN_CI = 20        # distribution groups
MIN_N_CORR_CI = 30          # lagged correlation pairs


def rng(seed: int = SEED, *salt) -> random.Random:
    """A private generator; ``salt`` separates independent draws."""
    key = seed
    for s in salt:
        key = (key * 1000003 + _stable_int(s)) & 0xFFFFFFFFFFFF
    return random.Random(key)


def _stable_int(v) -> int:
    if isinstance(v, int):
        return v
    h = 1469598103934665603
    for ch in str(v).encode("utf-8"):
        h = ((h ^ ch) * 1099511628211) & 0xFFFFFFFFFFFFFFFF
    return h


# ------------------------------------------------------------- basics
def present(xs: Sequence) -> list[float]:
    return [float(x) for x in xs if x is not None and not (isinstance(x, float) and math.isnan(x))]


def mean(xs: Sequence[float]) -> float | None:
    return math.fsum(xs) / len(xs) if xs else None


def variance(xs: Sequence[float]) -> float | None:
    """Sample variance (n - 1 denominator)."""
    n = len(xs)
    if n < 2:
        return None
    m = math.fsum(xs) / n
    return math.fsum((x - m) ** 2 for x in xs) / (n - 1)


def sd(xs: Sequence[float]) -> float | None:
    v = variance(xs)
    return math.sqrt(v) if v is not None else None


def quantile(xs: Sequence[float], q: float) -> float | None:
    """Linear interpolation between order statistics (R type 7)."""
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    if n == 1:
        return s[0]
    h = (n - 1) * q
    lo = math.floor(h)
    hi = min(lo + 1, n - 1)
    return s[lo] + (h - lo) * (s[hi] - s[lo])


def median(xs: Sequence[float]) -> float | None:
    return quantile(xs, 0.5)


def summary(xs: Sequence[float]) -> dict:
    n = len(xs)
    if n == 0:
        return {"n": 0, "mean": None, "sd": None, "min": None, "p5": None, "q1": None,
                "median": None, "q3": None, "p95": None, "max": None}
    return {"n": n, "mean": _r(mean(xs)), "sd": _r(sd(xs)), "min": _r(min(xs)),
            "p5": _r(quantile(xs, 0.05)), "q1": _r(quantile(xs, 0.25)),
            "median": _r(median(xs)), "q3": _r(quantile(xs, 0.75)),
            "p95": _r(quantile(xs, 0.95)), "max": _r(max(xs))}


def _r(x, nd: int = 6):
    if x is None:
        return None
    if isinstance(x, float):
        if math.isnan(x) or math.isinf(x):
            return None
        return round(x, nd)
    return x


# --------------------------------------------------------------- ranks
def ranks(xs: Sequence[float]) -> list[float]:
    """Average ranks (1-based); ties share the mean of their positions."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    out = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            out[order[k]] = avg
        i = j + 1
    return out


def pearson(x: Sequence[float], y: Sequence[float]) -> float | None:
    n = len(x)
    if n != len(y) or n < 3:
        return None
    mx, my = math.fsum(x) / n, math.fsum(y) / n
    sxx = math.fsum((a - mx) ** 2 for a in x)
    syy = math.fsum((b - my) ** 2 for b in y)
    if sxx <= 0 or syy <= 0:
        return None
    sxy = math.fsum((a - mx) * (b - my) for a, b in zip(x, y))
    return sxy / math.sqrt(sxx * syy)


def spearman(x: Sequence[float], y: Sequence[float]) -> float | None:
    """Spearman rank correlation = Pearson on average ranks (tie-correct)."""
    if len(x) != len(y) or len(x) < 3:
        return None
    return pearson(ranks(x), ranks(y))


# ----------------------------------------------------------- bootstrap
def bootstrap_ci(values: Sequence[float], stat: Callable[[list], float | None],
                 r: random.Random, resamples: int = RESAMPLES,
                 level: float = LEVEL) -> tuple[float, float] | None:
    """Percentile bootstrap of ``stat`` over iid resamples of ``values``."""
    n = len(values)
    if n < 2:
        return None
    out = []
    for _ in range(resamples):
        sample = [values[r.randrange(n)] for _ in range(n)]
        s = stat(sample)
        if s is not None:
            out.append(s)
    return _percentile_interval(out, level)


def _percentile_interval(draws: list, level: float):
    if len(draws) < 10:
        return None
    a = (1.0 - level) / 2.0
    return (quantile(draws, a), quantile(draws, 1.0 - a))


def block_resample(n: int, block: int, r: random.Random) -> list[int]:
    """Moving-block bootstrap indices of length n."""
    block = max(1, min(block, n))
    out: list[int] = []
    while len(out) < n:
        start = r.randrange(n - block + 1)
        out.extend(range(start, start + block))
    return out[:n]


def block_bootstrap_ci(n: int, stat_of_indices: Callable[[list], float | None],
                       r: random.Random, block: int = BLOCK_DAYS,
                       resamples: int = RESAMPLES, level: float = LEVEL):
    draws = []
    for _ in range(resamples):
        s = stat_of_indices(block_resample(n, block, r))
        if s is not None:
            draws.append(s)
    return _percentile_interval(draws, level)


def ci_dict(interval, *, method: str = "bootstrap", extra: dict | None = None) -> dict | None:
    if interval is None:
        return None
    out = {"level": LEVEL, "method": method, "resamples": RESAMPLES, "seed": SEED,
           "low": _r(interval[0]), "high": _r(interval[1])}
    if extra:
        out.update(extra)
    return out


# -------------------------------------------------------- before / after
def before_after(before: Sequence[float], after: Sequence[float]) -> dict:
    mb, ma = mean(before), mean(after)
    diff = (ma - mb) if (mb is not None and ma is not None) else None
    pct = (100.0 * (ma - mb) / mb) if (mb not in (None, 0) and ma is not None) else None
    return {"before": {"n": len(before), "mean": _r(mb), "median": _r(median(before)),
                       "sd": _r(sd(before))},
            "after": {"n": len(after), "mean": _r(ma), "median": _r(median(after)),
                      "sd": _r(sd(after))},
            "diff_mean": _r(diff), "pct_change": _r(pct)}


def diff_mean_block_ci(before: Sequence[float], after: Sequence[float], seed_salt="ba"):
    """Moving-block bootstrap of (mean after - mean before): each side is
    resampled in 7-day blocks to respect day-to-day dependence."""
    r = rng(SEED, seed_salt, len(before), len(after))
    draws = []
    nb, na = len(before), len(after)
    for _ in range(RESAMPLES):
        ib = block_resample(nb, BLOCK_DAYS, r)
        ia = block_resample(na, BLOCK_DAYS, r)
        draws.append(math.fsum(after[i] for i in ia) / na - math.fsum(before[i] for i in ib) / nb)
    return _percentile_interval(draws, LEVEL)


# ------------------------------------------------------- lagged correlation
def lagged_pairs(x: Sequence, y: Sequence, lag: int) -> tuple[list[float], list[float]]:
    """Pairs (x[t], y[t+lag]) with both present. lag > 0: x leads y."""
    xs, ys = [], []
    n = len(x)
    for t in range(n):
        u = t + lag
        if 0 <= u < n and x[t] is not None and y[u] is not None:
            xs.append(float(x[t]))
            ys.append(float(y[u]))
    return xs, ys


def lagged_spearman(x: Sequence, y: Sequence, max_lag: int, min_pairs: int = 10) -> list[dict]:
    out = []
    for lag in range(-max_lag, max_lag + 1):
        a, b = lagged_pairs(x, y, lag)
        rho = spearman(a, b) if len(a) >= min_pairs else None
        out.append({"lag": lag, "rho": _r(rho), "n": len(a)})
    return out


def peak_lag(rows: list[dict]) -> dict | None:
    best = None
    for row in rows:
        if row["rho"] is None:
            continue
        key = (abs(row["rho"]), -abs(row["lag"]), -row["lag"])
        if best is None or key > best[0]:
            best = (key, row)
    return best[1] if best else None


def lag_peak_block_ci(x: Sequence, y: Sequence, lag: int):
    """Block bootstrap of the Spearman rho at ``lag`` (resamples time indices
    in 7-day blocks, keeps pairs together)."""
    n = len(x)
    r = rng(SEED, "lagcorr", lag, n)

    def stat(idx):
        xs, ys = [], []
        for t in idx:
            u = t + lag
            if 0 <= u < n and x[t] is not None and y[u] is not None:
                xs.append(float(x[t]))
                ys.append(float(y[u]))
        return spearman(xs, ys) if len(xs) >= 10 else None

    return block_bootstrap_ci(n, stat, r)


# --------------------------------------------------------- change points
def _mad_sigma(xs: Sequence[float]) -> float:
    """Robust noise scale from first differences: 1.4826 * MAD(diff) / sqrt(2)."""
    d = [xs[i + 1] - xs[i] for i in range(len(xs) - 1)]
    if not d:
        return 0.0
    m = median(d)
    mad = median([abs(v - m) for v in d])
    return 1.4826 * mad / math.sqrt(2.0)


def pelt_l2(xs: Sequence[float], min_size: int, penalty: float | None = None) -> tuple[list[int], float]:
    """PELT (Killick et al. 2012) with a squared-error cost for a change in
    the mean. Returns (change point indices = first index of each new
    segment, penalty used). Default penalty 2 * sigma^2 * ln(n) with sigma
    from ``_mad_sigma`` (a BIC-style rule); 'lite': one cost model, no
    tuning, deterministic."""
    n = len(xs)
    if n < 2 * min_size:
        return [], penalty or 0.0
    if penalty is None:
        s = _mad_sigma(xs)
        penalty = 2.0 * (s * s) * math.log(n)
        if penalty <= 0:
            penalty = 1e-9
    cs = [0.0] * (n + 1)
    cs2 = [0.0] * (n + 1)
    for i, v in enumerate(xs):
        cs[i + 1] = cs[i] + v
        cs2[i + 1] = cs2[i] + v * v

    def cost(a: int, b: int) -> float:       # segment [a, b)
        m = b - a
        s1 = cs[b] - cs[a]
        return (cs2[b] - cs2[a]) - s1 * s1 / m

    F = [0.0] * (n + 1)
    F[0] = -penalty
    last = [0] * (n + 1)
    cands = [0]
    for t in range(min_size, n + 1):
        s_new = t - min_size
        if s_new >= min_size:
            cands.append(s_new)
        best, best_s = None, 0
        vals = []
        for s in cands:
            c = F[s] + cost(s, t)
            vals.append((s, c))
            v = c + penalty
            if best is None or v < best:
                best, best_s = v, s
        F[t] = best
        last[t] = best_s
        # PELT pruning (K = 0 for a sum-of-squares cost)
        cands = [s for s, c in vals if c <= F[t] + 1e-9]
    cps = []
    t = n
    while t > 0:
        s = last[t]
        if s > 0:
            cps.append(s)
        t = s
    return sorted(cps), penalty


def cusum_stat(seg: Sequence[float]) -> tuple[int, float]:
    """(split index k, scaled statistic). S_k = sum_{i<k}(x_i - mean);
    statistic = max_k |S_k| / (sd * sqrt(n)). The split is the first index
    of the 'after' part."""
    n = len(seg)
    m = math.fsum(seg) / n
    s = 0.0
    best_k, best = 0, 0.0
    for k in range(1, n):
        s += seg[k - 1] - m
        if abs(s) > best:
            best, best_k = abs(s), k
    sdv = sd(seg) or 0.0
    if sdv <= 0:
        return best_k, 0.0
    return best_k, best / (sdv * math.sqrt(n))


def cusum_changes(xs: Sequence[float], min_size: int, perms: int = 200,
                  level: float = 0.95) -> tuple[list[int], float]:
    """Binary segmentation with a CUSUM split. A split is kept when its scaled
    statistic exceeds the ``level`` quantile of the same statistic over seeded
    permutations of the segment (a data-driven threshold; no distribution
    assumed). Both parts must hold at least ``min_size`` points."""
    cps: list[int] = []
    thresholds: list[float] = []

    def rec(lo: int, hi: int) -> None:
        seg = xs[lo:hi]
        n = len(seg)
        if n < 2 * min_size:
            return
        k, stat = cusum_stat(seg)
        if k < min_size or n - k < min_size:
            # the CUSUM maximum sits too near an edge; look inside the allowed range
            best = None
            m = math.fsum(seg) / n
            s = 0.0
            for i in range(1, n):
                s += seg[i - 1] - m
                if min_size <= i <= n - min_size and (best is None or abs(s) > best[0]):
                    best = (abs(s), i)
            if best is None:
                return
            k = best[1]
            sdv = sd(seg) or 0.0
            stat = best[0] / (sdv * math.sqrt(n)) if sdv > 0 else 0.0
        r = rng(SEED, "cusum", lo, hi)
        pool = list(seg)
        draws = []
        for _ in range(perms):
            r.shuffle(pool)
            draws.append(cusum_stat(pool)[1])
        thr = quantile(draws, level)
        thresholds.append(thr)
        if stat > thr and stat > 0:
            cps.append(lo + k)
            rec(lo, lo + k)
            rec(lo + k, hi)

    rec(0, len(xs))
    return sorted(cps), (max(thresholds) if thresholds else 0.0)


# -------------------------------------------------------------- shape
def histogram(xs: Sequence[float], bins: int | None = None) -> dict:
    n = len(xs)
    if n == 0:
        return {"edges": [], "counts": []}
    lo, hi = min(xs), max(xs)
    if bins is None:
        bins = max(5, min(30, int(math.ceil(math.sqrt(n)))))
    if hi == lo:
        return {"edges": [_r(lo), _r(hi)], "counts": [n]}
    w = (hi - lo) / bins
    counts = [0] * bins
    for v in xs:
        i = min(bins - 1, int((v - lo) / w))
        counts[i] += 1
    return {"edges": [_r(lo + i * w) for i in range(bins + 1)], "counts": counts}


def acf(xs: Sequence, max_lag: int) -> list[dict]:
    """Sample autocorrelation of a series with missing days: lag-k pairs use
    only days where both ends are present, around the overall mean."""
    vals = present(xs)
    if len(vals) < 3:
        return []
    m = math.fsum(vals) / len(vals)
    denom = math.fsum((v - m) ** 2 for v in vals)
    if denom <= 0:
        return []
    out = []
    n = len(xs)
    for k in range(1, max_lag + 1):
        num = 0.0
        cnt = 0
        for t in range(n - k):
            a, b = xs[t], xs[t + k]
            if a is not None and b is not None:
                num += (a - m) * (b - m)
                cnt += 1
        out.append({"lag": k, "r": _r(num / denom) if cnt >= 3 else None, "n": cnt})
    return out
