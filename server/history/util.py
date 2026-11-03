"""Small shared helpers: UTC timestamps, day arithmetic, canonical JSON."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone

TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class HistoryError(ValueError):
    """A request the history layer refuses (bad dates, unknown place...).
    ``code`` follows the house error shape's ``error.code``."""

    def __init__(self, message: str, code: str = "invalid_request"):
        super().__init__(message)
        self.code = code


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today_utc() -> date:
    return datetime.now(timezone.utc).date()


def parse_day(value, field: str = "date") -> date:
    """YYYY-MM-DD (or a full ISO timestamp, whose date part is used)."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise HistoryError(f"{field} must be a YYYY-MM-DD string")
    text = value.strip()
    try:
        if DAY_RE.match(text):
            return date.fromisoformat(text)
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        raise HistoryError(f"{field} is not a valid date: {value!r:.40}") from None


def iso_day(d: date) -> str:
    return d.isoformat()


def day_range(start: date, end: date) -> list[date]:
    """Inclusive list of days."""
    n = (end - start).days
    return [start + timedelta(days=i) for i in range(n + 1)]


def add_days(d: date, n: int) -> date:
    return d + timedelta(days=n)


def day_to_ts(d: date, end_of_day: bool = False) -> str:
    return d.isoformat() + ("T23:59:59Z" if end_of_day else "T00:00:00Z")


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def sha256_hex(text: str | bytes) -> str:
    if isinstance(text, str):
        text = text.encode("utf-8")
    return hashlib.sha256(text).hexdigest()


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_")[:80] or "x"


def prov(source: str, retrieved_at: str, mode: str = "SNAPSHOT") -> dict:
    """The provenance envelope. History is never LIVE."""
    if mode not in ("SNAPSHOT", "MODELED"):
        raise ValueError(f"history provenance mode must be SNAPSHOT or "
                         f"MODELED, got {mode!r}")
    return {"source": source, "retrieved_at": retrieved_at, "mode": mode}
