"""Pure, deterministic series arithmetic shared by event inference and
the realism grader. No I/O, no randomness, no LLM.

A "series" here is a ``{date(YYYY-MM-DD) -> float}`` mapping (missing
days are simply absent - never zero-filled).
"""
from __future__ import annotations

import math
from datetime import date, timedelta

from .util import iso_day, parse_day


def mean(values) -> float | None:
    vals = [float(v) for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def window_values(series: dict, start: date, end: date) -> list[float]:
    """Values with start <= day <= end, in day order."""
    out = []
    d = start
    while d <= end:
        v = series.get(iso_day(d))
        if v is not None:
            out.append(float(v))
        d += timedelta(days=1)
    return out


def baseline(series: dict, before: date, days: int = 28,
             min_days: int = 14) -> tuple[float | None, int]:
    """Mean over the ``days`` days ending the day BEFORE ``before``;
    (None, n) when fewer than ``min_days`` observations exist."""
    vals = window_values(series, before - timedelta(days=days),
                         before - timedelta(days=1))
    if len(vals) < min_days:
        return None, len(vals)
    return sum(vals) / len(vals), len(vals)


def pct_change(new: float | None, base: float | None) -> float | None:
    """100 * (new - base) / base; None when base is 0/None."""
    if new is None or base is None or base == 0:
        return None
    return 100.0 * (new - base) / base


def onset_day(series: dict, start: date, end: date, base: float,
              threshold: float = -0.20, sustain: int = 3) -> date | None:
    """First day d in [start, end] such that the next ``sustain`` days
    (d .. d+sustain-1, all present) each deviate from ``base`` by at
    least ``|threshold|`` in the direction of ``threshold`` (negative =
    below, positive = above). ``None`` when never."""
    if base is None or base <= 0:
        return None
    d = start
    while d <= end:
        ok = True
        for k in range(sustain):
            v = series.get(iso_day(d + timedelta(days=k)))
            if v is None:
                ok = False
                break
            dev = (float(v) - base) / base
            if (threshold < 0 and dev > threshold) or \
                    (threshold > 0 and dev < threshold):
                ok = False
                break
        if ok:
            return d
        d += timedelta(days=1)
    return None


def direction(change_pct: float | None, flat_band_pct: float = 10.0) -> str | None:
    """'down' | 'up' | 'flat' (|change| < flat_band_pct) | None."""
    if change_pct is None:
        return None
    if change_pct <= -flat_band_pct:
        return "down"
    if change_pct >= flat_band_pct:
        return "up"
    return "flat"


def _ranks(values: list[float]) -> list[float]:
    """Average ranks (1-based), ties share the mean rank."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(x: list[float], y: list[float]) -> float | None:
    """Spearman rank correlation (Pearson over average ranks); None when
    fewer than 3 pairs or either side has no variance."""
    if len(x) != len(y) or len(x) < 3:
        return None
    rx, ry = _ranks(x), _ranks(y)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    sxx = sum((a - mx) ** 2 for a in rx)
    syy = sum((b - my) ** 2 for b in ry)
    if sxx == 0 or syy == 0:
        return None
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    return round(sxy / math.sqrt(sxx * syy), 6)


def infer_episodes(series: dict, start: date, end: date, *,
                   baseline_days: int = 28, smooth_days: int = 7,
                   open_ratio: float = 0.70, onset_ratio: float = 0.85,
                   close_ratio: float = 0.90, close_sustain: int = 3,
                   min_baseline_days: int = 14) -> list[dict]:
    """Rule ``portwatch_drop_v1`` (MODELED inference, published):

    Let S_t be the trailing ``smooth_days``-day mean at day t and B the
    mean of the ``baseline_days`` days that end the day before the
    smoothing window opens (needs >= ``min_baseline_days`` days). An
    episode OPENS at the first t in [start, end] with S_t <= open_ratio*B;
    its onset is the first day of that window whose value is already
    <= onset_ratio*B. B is frozen while the episode is open. It CLOSES
    when S_t >= close_ratio*B for ``close_sustain`` consecutive days (the
    last low day ends it) or stays open to ``end``.

    Returns episodes ``{onset, detected, last_low, closed, baseline,
    mean_loss_fraction}`` in date order.
    """
    episodes: list[dict] = []
    t = start
    cur: dict | None = None
    recovered = 0
    while t <= end:
        window = window_values(series, t - timedelta(days=smooth_days - 1), t)
        have_full = len(window) >= max(3, smooth_days - 2)
        s_t = (sum(window) / len(window)) if window else None
        if cur is None:
            if have_full and s_t is not None:
                b, _n = baseline(series, t - timedelta(days=smooth_days - 1),
                                 baseline_days, min_baseline_days)
                if b and b > 0 and s_t <= open_ratio * b:
                    onset = t
                    for k in range(smooth_days):
                        dd = t - timedelta(days=smooth_days - 1 - k)
                        v = series.get(iso_day(dd))
                        if v is not None and float(v) <= onset_ratio * b:
                            onset = dd
                            break
                    cur = {"onset": onset, "detected": t, "last_low": t,
                           "closed": False, "baseline": b, "_vals": []}
                    recovered = 0
        else:
            b = cur["baseline"]
            if have_full and s_t is not None and s_t >= close_ratio * b:
                recovered += 1
                if recovered >= close_sustain:
                    cur["closed"] = True
                    episodes.append(cur)
                    cur = None
                    recovered = 0
            else:
                recovered = 0
                if s_t is not None:
                    cur["last_low"] = t
        if cur is not None:
            v = series.get(iso_day(t))
            if v is not None:
                cur["_vals"].append(float(v))
        t += timedelta(days=1)
    if cur is not None:
        episodes.append(cur)
    out = []
    for ep in episodes:
        vals = [v for v in ep.pop("_vals")]
        # loss over onset..last_low (the low stretch), not the whole tail
        low = window_values(series, ep["onset"], ep["last_low"])
        m = (sum(low) / len(low)) if low else (sum(vals) / len(vals) if vals else None)
        ep["mean_loss_fraction"] = (round(max(0.0, 1.0 - m / ep["baseline"]), 4)
                                    if m is not None and ep["baseline"] else None)
        out.append(ep)
    return out
