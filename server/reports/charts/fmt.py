"""Deterministic number, text and tick helpers for the chart engine.

Nothing here reads a locale, a clock or a font: the same inputs give the
same strings on every machine (the figure-bytes law)."""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep",
          "Oct", "Nov", "Dec")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_NARROW = set("iljtfIr.,:;'|!()[] /-")
_WIDE = set("mwMW@%")


def clean(s) -> str:
    """Text safe for XML 1.0 (control characters dropped), whitespace
    collapsed to single spaces."""
    return " ".join(_CTRL.sub("", str(s)).split())


def esc(s) -> str:
    return (clean(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def coord(v: float) -> str:
    """SVG coordinate: at most 2 decimals, no trailing zeros, no '-0'."""
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _commas(int_part: str) -> str:
    neg = int_part.startswith("-")
    d = int_part.lstrip("-")
    out = []
    while len(d) > 3:
        out.insert(0, d[-3:])
        d = d[:-3]
    out.insert(0, d)
    return ("-" if neg else "") + ",".join(out)


def num(v, decimals: int | None = None) -> str:
    """Format a value for a label: 3 significant digits for small values,
    whole numbers with thousands separators for large ones, never an
    exponent."""
    if v is None:
        return "-"
    if isinstance(v, int) and not isinstance(v, bool) and decimals is None:
        return _commas(str(v))
    v = float(v)
    if decimals is None:
        a = abs(v)
        if a == 0:
            return "0"
        decimals = max(0, 2 - int(math.floor(math.log10(a))))
        decimals = min(decimals, 8)
    s = f"{v:.{decimals}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    if s in ("-0", ""):
        s = "0"
    head, dot, tail = s.partition(".")
    return _commas(head) + dot + tail


def step_decimals(step: float) -> int:
    for d in range(0, 8):
        x = step * 10 ** d
        if abs(x - round(x)) < 1e-6:
            return d
    return 6


def tick_label(v: float, step: float) -> str:
    return num(round(v, 9), step_decimals(step))


def _nice(x: float, round_: bool) -> float:
    e = math.floor(math.log10(x))
    f = x / 10 ** e
    if round_:
        n = 1 if f < 1.5 else 2 if f < 3.5 else 5 if f < 7.5 else 10
    else:
        n = 1 if f <= 1 else 2 if f <= 2 else 5 if f <= 5 else 10
    return n * 10 ** e


def nice_ticks(lo: float, hi: float, target: int = 5):
    """-> (ticks, step, lo, hi) covering [lo, hi] on 1-2-5 steps."""
    if hi < lo:
        lo, hi = hi, lo
    if hi == lo:
        if lo == 0:
            lo, hi = 0.0, 1.0
        else:
            pad = abs(lo) * 0.1
            lo, hi = lo - pad, hi + pad
    rng = _nice(hi - lo, False)
    step = _nice(rng / max(1, target - 1), True)
    nlo = math.floor(lo / step + 1e-9) * step
    nhi = math.ceil(hi / step - 1e-9) * step
    ticks, k = [], 0
    while True:
        t = round(nlo + k * step, 10)
        ticks.append(t)
        if t >= nhi - step * 1e-9 or k > 200:
            break
        k += 1
    return ticks, step, ticks[0], ticks[-1]


def text_w(s, size: float) -> float:
    w = 0.0
    for ch in clean(s):
        if ch in _NARROW:
            w += 0.3
        elif ch in _WIDE:
            w += 0.85
        elif ch.isdigit():
            w += 0.58
        elif ch.isupper():
            w += 0.66
        else:
            w += 0.55
    return w * size


def trunc(s, max_px: float, size: float) -> str:
    s = clean(s)
    if text_w(s, size) <= max_px:
        return s
    while s and text_w(s + "...", size) > max_px:
        s = s[:-1]
    return s.rstrip() + "..."


def wrap(s, max_px: float, size: float, max_lines: int = 2) -> list[str]:
    words = clean(s).split(" ")
    lines, cur = [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if cur and text_w(trial, size) > max_px:
            lines.append(cur)
            cur = w
        else:
            cur = trial
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = trunc(lines[-1] + " ...", max_px, size)
    return [trunc(l, max_px, size) for l in lines] or [""]


# ------------------------------------------------------------------ time
def parse_iso(s):
    """ISO-8601 date / timestamp -> aware UTC datetime, or None."""
    if not isinstance(s, str):
        return None
    t = s.strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}", t):
        return None
    try:
        d = datetime.fromisoformat(t.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def to_days(s) -> float | None:
    d = parse_iso(s)
    return None if d is None else d.timestamp() / 86400.0


def day_label(days: float, with_time: bool = False) -> str:
    d = datetime.fromtimestamp(round(days * 86400), tz=timezone.utc)
    base = f"{d.day:02d} {MONTHS[d.month - 1]}"
    if with_time:
        base += f" {d.hour:02d}:{d.minute:02d}"
    return base
