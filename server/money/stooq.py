"""Stooq adapter — no key. Daily-history CSV per ticker.

Endpoint: https://stooq.com/q/d/l/?s=<ticker>.us&i=d
  -> CSV "Date,Open,High,Low,Close,Volume" (one row per trading day).

LIVE-FETCH REALITY: stooq.com may answer some hosts with a JavaScript
browser-verification challenge (HTTP 200 HTML,
"This site requires JavaScript to verify your browser") or drops the
connection (RemoteDisconnected). The parser therefore REJECTS any
non-CSV payload: a challenge page must never become a price. With no
valid CSV the adapter reports DOWN with the failure in its note — it
never fabricates a quote.
"""
from __future__ import annotations

import csv
import io

from .common import (AdapterError, health_dict, http_get, provenance,
                     sanitize_text, utcnow_iso)

SOURCE = "Stooq"
DAILY_URL = "https://stooq.com/q/d/l/?s={symbol}&i=d"
USER_AGENT = "SupermAP/0.1 (supply-chain research tool)"


def stooq_symbol(ticker: str) -> str:
    """US listings on Stooq are '<ticker>.us' (lowercase)."""
    return f"{ticker.strip().lower()}.us"


def parse_daily_csv(text: str, ticker: str) -> list[dict]:
    """Parse Stooq daily-history CSV -> quote records (oldest first).

    Raises AdapterError on anything that is not the expected CSV —
    including Stooq's JS-challenge HTML and its 'No data' placeholder.
    Pure function: it parses recorded responses offline.
    """
    if not text or "<html" in text[:200].lower() or "<!doctype" in text[:200].lower():
        raise AdapterError(
            "Stooq returned an HTML page, not CSV (bot-verification "
            "challenge or error page); refusing to parse it as prices")
    reader = csv.DictReader(io.StringIO(text.strip()))
    if reader.fieldnames is None or "Close" not in reader.fieldnames \
            or "Date" not in (reader.fieldnames or []):
        raise AdapterError(
            f"Stooq payload lacks the expected CSV header "
            f"(got {reader.fieldnames!r}); refusing to parse")
    records = []
    for row in reader:
        try:
            close = float(row["Close"])
            records.append({
                "ticker": ticker.upper(),
                "date": row["Date"],
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": close,
                "volume": int(float(row["Volume"])) if row.get("Volume") else None,
            })
        except (TypeError, ValueError):
            continue  # a malformed row is skipped, never repaired by guessing
    if not records:
        raise AdapterError("Stooq CSV parsed to zero valid rows")
    return records


class StooqAdapter:
    name = "stooq"
    source = SOURCE

    def __init__(self) -> None:
        self._last_success_at: str | None = None
        self._note: str | None = None
        self._state = "DOWN"

    def fetch_history(self, ticker: str) -> dict:
        """-> {records: [...], provenance: {...}} (adapter-shaped FetchResult)."""
        symbol = stooq_symbol(ticker)
        url = DAILY_URL.format(symbol=symbol)
        try:
            status, body, _ = http_get(url, user_agent=USER_AGENT, timeout=40)
        except AdapterError as exc:
            # The note is a health surface: keep it scrubbed even though
            # common.py already sanitizes its own errors.
            self._state = "DOWN"
            self._note = f"live fetch failed ({sanitize_text(exc)}); no snapshot"
            raise
        if status != 200:
            self._state = "DOWN"
            self._note = f"Stooq HTTP {status} for {symbol}"
            raise AdapterError(self._note)
        text = body.decode("utf-8", "replace")
        try:
            records = parse_daily_csv(text, ticker)
        except AdapterError as exc:
            self._state, self._note = "DOWN", sanitize_text(exc)
            raise
        prov = provenance(SOURCE, "LIVE")
        for rec in records:
            rec["provenance"] = dict(prov)
        self._last_success_at = prov["retrieved_at"]
        self._state, self._note = "LIVE", None
        return {"records": records, "provenance": prov}

    def health(self) -> dict:
        return health_dict(self.name, self._state, self._last_success_at,
                           "LIVE" if self._state == "LIVE" else None,
                           self._note)


def latest_quote(records: list[dict]) -> dict | None:
    """Mechanical panel quote from a history series: last close plus the
    change vs the previous trading day's close. No modelling."""
    if not records:
        return None
    last = records[-1]
    prev = records[-2] if len(records) > 1 else None
    change_pct = None
    if prev and prev["close"]:
        change_pct = round((last["close"] / prev["close"] - 1.0) * 100.0, 4)
    return {
        "ticker": last["ticker"],
        "last_price": last["close"],
        "price_date": last["date"],
        "prev_close": prev["close"] if prev else None,
        "price_change_pct": change_pct,
        "sparkline": [r["close"] for r in records[-30:]],
        "provenance": last.get("provenance"),
    }
