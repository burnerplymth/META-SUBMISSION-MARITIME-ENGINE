"""Deterministic arrival schedules.

The arrival curve of each PortWatch class is N(t) = integral of
lambda_class x arrival-multiplier(t) x shape(hour) dt (a cumulative
curve, Newell). The j-th call of the class arrives when N(t) reaches
j + phase_class: equal spacing at a constant rate, bunching when the
multiplier or the shape moves. No random numbers. The size class of each
call comes from a fixed largest-deficit interleave of the class mix.
"""
from __future__ import annotations

import math

from .registry import KIND_ORDER, normalised_classes, plug

PHASES = (0.5, 0.2, 0.8, 0.35, 0.65)   # one per kind in KIND_ORDER
HOUR = 3600


def arrival_times(rate_per_day: float, t0: int, t1: int, arr_fn, shape: list,
                  breaks: list, phase: float, daily_scale=None) -> list:
    """Integer-second arrival times in [t0, t1) for one class."""
    if rate_per_day <= 0 or t1 <= t0:
        return []
    cuts = {t0, t1}
    h = (t0 // HOUR + 1) * HOUR
    while h < t1:
        cuts.add(h)
        h += HOUR
    for b in breaks:
        if t0 < b < t1:
            cuts.add(b)
    pts = sorted(cuts)
    out = []
    n_cum = 0.0
    nxt = phase
    base = rate_per_day / 86400.0
    for a, b in zip(pts, pts[1:]):
        r = base * arr_fn.at(a) * shape[(a // HOUR) % 24]
        if daily_scale is not None:
            r *= daily_scale(a)
        if r <= 0:
            continue
        gain = r * (b - a)
        while nxt <= n_cum + gain:
            t = a + int(math.floor((nxt - n_cum) / r))
            t = min(max(t, a), b - 1)
            out.append(t)
            nxt += 1.0
        n_cum += gain
    return out


def size_sequence(kind: str, n: int) -> list:
    """Size-class index of the first n calls of a kind: the class whose
    cumulative target (share x calls so far) most exceeds its count wins;
    ties go to the lowest index. Fixed mix, no RNG."""
    classes = normalised_classes(kind)
    counts = [0] * len(classes)
    seq = []
    for j in range(n):
        best, bd = 0, -1e18
        for i, c in enumerate(classes):
            d = c["share"] * (j + 1) - counts[i]
            if d > bd + 1e-12:
                best, bd = i, d
        counts[best] += 1
        seq.append(best)
    return seq


def build_schedule(calls_per_day: dict, t0: int, t1: int, arr_fn, shape_name: str,
                   breaks: list, daily_calls=None):
    """All arrivals as a sorted list of (t, kind, size_index). `daily_calls`
    (optional) maps kind -> list of per-day absolute calls/day starting at
    t = 0; it replaces the constant rate on those days."""
    shape = plug("arrival_shapes")[shape_name]
    mean = sum(shape) / len(shape)
    shape = [s / mean for s in shape]
    rows = []
    for ki, kind in enumerate(KIND_ORDER):
        lam = calls_per_day.get(kind, 0.0)
        scale = None
        if daily_calls and kind in daily_calls:
            series = daily_calls[kind]
            lam_ref = lam if lam > 0 else 1.0

            def scale(t, series=series, lam_ref=lam_ref):
                d = t // 86400
                if d < 0 or d >= len(series):
                    return 1.0
                return series[d] / lam_ref
            lam = lam if lam > 0 else 1.0
        times = arrival_times(lam, t0, t1, arr_fn, shape, breaks,
                              PHASES[ki], scale)
        sizes = size_sequence(kind, len(times)) if times else []
        for j, (t, s) in enumerate(zip(times, sizes)):
            rows.append((t, ki, j, kind, s))
    rows.sort(key=lambda r: (r[0], r[1], r[2]))
    return [(t, kind, s) for (t, _ki, _j, kind, s) in rows]
