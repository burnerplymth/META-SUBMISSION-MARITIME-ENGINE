"""Display formatting for facts: one decimal rule, units always, ranges as
"13 to 28 d", thousands separators. Pure and locale-free, so a brief renders
to the same bytes on every machine."""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep",
          "Oct", "Nov", "Dec")


def _group(int_part: str) -> str:
    neg = int_part.startswith("-")
    d = int_part.lstrip("-")
    out = []
    while len(d) > 3:
        out.insert(0, d[-3:])
        d = d[:-3]
    out.insert(0, d)
    return ("-" if neg else "") + ",".join(out)


def num(v, decimals: int = 1) -> str:
    """Number with thousands separators and at most ``decimals`` decimals
    (trailing zeros dropped). Never an exponent, never '-0'."""
    if v is None or isinstance(v, bool) or not isinstance(v, (int, float)) \
            or not math.isfinite(v):
        return "-"
    r = round(float(v), decimals)
    if r == 0:
        r = 0.0
    s = f"{r:.{decimals}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    head, _, tail = s.partition(".")
    return _group(head) + ("." + tail if tail else "")


def days(v) -> str:
    return num(v, 1) + " d"


def rng(lo, hi, unit: str = "d", decimals: int = 1) -> str:
    a, b = num(lo, decimals), num(hi, decimals)
    return f"{a} to {b} {unit}".strip() if a != b else f"{a} {unit}".strip()


def pct(v, decimals: int = 0) -> str:
    return num(v, decimals) + "%"


def usd(v) -> str:
    return "USD " + num(v, 0)


def parse_iso(text):
    if not isinstance(text, str) or not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def date(text) -> str:
    dt = parse_iso(text)
    if dt is None:
        return "-"
    dt = dt.astimezone(timezone.utc)
    return f"{dt.day} {MONTHS[dt.month - 1]} {dt.year}"


def stamp(text) -> str:
    """UTC date and minute: '24 Oct 2026 23:56 UTC'."""
    dt = parse_iso(text)
    if dt is None:
        return "-"
    dt = dt.astimezone(timezone.utc)
    return f"{dt.day} {MONTHS[dt.month - 1]} {dt.year} {dt:%H:%M} UTC"


_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def numbers_in(text: str) -> list[float]:
    """Every numeral in a string (thousands separators removed)."""
    out = []
    for m in _NUM_RE.findall(text or ""):
        try:
            out.append(float(m.replace(",", "")))
        except ValueError:
            pass
    return out


def clean_text(value, limit: int = 160) -> str:
    """A label from outside the engine: control characters dropped,
    whitespace collapsed, capped."""
    s = re.sub(r"[\x00-\x1f\x7f‪-‮⁦-⁩]", " ", str(value or ""))
    s = " ".join(s.split())
    return s[:limit]


def safe_url(value) -> str | None:
    """https only, no userinfo, no whitespace, at most 2048 characters."""
    if not isinstance(value, str):
        return None
    v = value.strip()
    if not v.startswith("https://") or len(v) > 2048 or re.search(r"\s", v):
        return None
    rest = v[len("https://"):].split("/", 1)[0]
    if "@" in rest or not rest or "." not in rest:
        return None
    return v
