"""Scales and axes shared by the cartesian drawers."""
from __future__ import annotations

from .fmt import day_label, nice_ticks, tick_label, to_days


class XAxis:
    """number / time / category x axis mapped onto pixels r0..r1."""

    def __init__(self, kind, series_points, r0, r1, spec_axis=None,
                 target=None):
        self.kind, self.r0, self.r1 = kind, r0, r1
        spec_axis = spec_axis or {}
        self.cats = []
        self.step = None
        self.with_time = False
        if kind == "category":
            seen = set()
            for pts in series_points:
                for p in pts:
                    if p["x"] not in seen:
                        seen.add(p["x"])
                        self.cats.append(p["x"])
            self.band = (r1 - r0) / max(1, len(self.cats))
            self.index = {c: i for i, c in enumerate(self.cats)}
            self.d0 = self.d1 = 0.0
            return
        vals = [self.conv(p["x"]) for pts in series_points for p in pts]
        vals = [v for v in vals if v is not None]
        lo = spec_axis.get("min", min(vals) if vals else 0.0)
        hi = spec_axis.get("max", max(vals) if vals else 1.0)
        if kind == "time":
            lo = min(vals) if vals else 0.0
            hi = max(vals) if vals else 1.0
        tgt = target or max(2, int((r1 - r0) / 90))
        ticks, step, nlo, nhi = nice_ticks(lo, hi, tgt)
        self.ticks_v, self.step = ticks, step
        self.d0, self.d1 = nlo, nhi
        self.with_time = kind == "time" and step < 1

    def conv(self, x):
        if self.kind == "time":
            return to_days(x) if isinstance(x, str) else float(x)
        if self.kind == "number":
            return None if isinstance(x, str) else float(x)
        return None

    def pos(self, x):
        if self.kind == "category":
            i = self.index.get(x)
            return None if i is None else self.r0 + (i + 0.5) * self.band
        v = self.conv(x)
        if v is None:
            return None
        if self.d1 == self.d0:
            return (self.r0 + self.r1) / 2
        return self.r0 + (v - self.d0) / (self.d1 - self.d0) * (
            self.r1 - self.r0)

    def ticks(self):
        """[(px, label)]"""
        if self.kind == "category":
            return [(self.r0 + (i + 0.5) * self.band, c)
                    for i, c in enumerate(self.cats)]
        out = []
        for t in self.ticks_v:
            lab = day_label(t, self.with_time) if self.kind == "time" \
                else tick_label(t, self.step)
            out.append((self.pos(t if self.kind == "number" else t), lab))
        return out

    def pos_value(self, v):
        """pixel of an already-converted numeric domain value"""
        if self.d1 == self.d0:
            return (self.r0 + self.r1) / 2
        return self.r0 + (v - self.d0) / (self.d1 - self.d0) * (
            self.r1 - self.r0)


class YAxis:
    def __init__(self, values, r_bottom, r_top, spec_axis=None,
                 include_zero=False, extra=(), target=5):
        spec_axis = spec_axis or {}
        vals = [v for v in list(values) + list(extra) if v is not None]
        lo = min(vals) if vals else 0.0
        hi = max(vals) if vals else 1.0
        if include_zero:
            lo, hi = min(lo, 0.0), max(hi, 0.0)
        if "min" in spec_axis:
            lo = spec_axis["min"]
        if "max" in spec_axis:
            hi = spec_axis["max"]
        ticks, step, nlo, nhi = nice_ticks(lo, hi, target)
        if "min" in spec_axis:
            nlo = spec_axis["min"]
        if "max" in spec_axis:
            nhi = spec_axis["max"]
        ticks = [t for t in ticks if nlo - 1e-9 <= t <= nhi + 1e-9]
        self.ticks_v, self.step = ticks, step
        self.d0, self.d1 = nlo, nhi
        self.rb, self.rt = r_bottom, r_top

    def pos(self, v):
        if self.d1 == self.d0:
            return (self.rb + self.rt) / 2
        return self.rb - (v - self.d0) / (self.d1 - self.d0) * (
            self.rb - self.rt)

    def ticks(self):
        return [(self.pos(t), tick_label(t, self.step))
                for t in self.ticks_v]


def zero_heuristic(values) -> bool:
    """A line chart of non-negative values starts at zero unless the data
    sit in a narrow band far above it."""
    vs = [v for v in values if v is not None]
    if not vs:
        return True
    lo, hi = min(vs), max(vs)
    return lo >= 0 and lo <= 0.5 * hi if hi > 0 else lo <= 0 <= hi


def spread(ys, min_gap, lo, hi):
    """Nudge label baselines apart (greedy from the top, then pulled back
    inside [lo, hi]); the order by y is preserved."""
    order = sorted(range(len(ys)), key=lambda i: (ys[i], i))
    out = list(ys)
    prev = None
    for i in order:
        y = max(out[i], lo)
        if prev is not None and y < prev + min_gap:
            y = prev + min_gap
        out[i] = y
        prev = y
    over = (out[order[-1]] - hi) if order else 0
    if over > 0:
        nxt = None
        for i in reversed(order):
            out[i] -= over
            if nxt is not None and out[i] > nxt - min_gap:
                out[i] = nxt - min_gap
            nxt = out[i]
    return out
