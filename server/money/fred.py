"""FRED adapter — KEY-GATED behind env FRED_API_KEY.

State without a key: NEEDS-KEY. The adapter does not connect at all
without a key and never fakes a series. (The FRED API is reachable and answers a keyless request
with HTTP 400 {"error_code":400,"error_message":"Bad Request. Variable
api_key is not set..."} — reachability is not a licence to invent data.)

The user adds keys later: set FRED_API_KEY and fetch_series() goes live
against https://api.stlouisfed.org/fred/series/observations with no
code change. Note on the key in URLs: FRED's API takes the key as a
query parameter by design; it is never written to disk, logs, or
provenance (records carry source/retrieved_at/mode only) — and it must
never reach error text either: money.common reduces every URL
in an error to scheme+host+path and redacts secret-style parameters,
and this adapter scrubs upstream error messages the same way.
"""
from __future__ import annotations

import os
import urllib.parse

from .common import (AdapterError, NeedsKeyError, health_dict, http_get_json,
                     provenance, sanitize_text)

SOURCE = "FRED (Federal Reserve Bank of St. Louis)"
API = "https://api.stlouisfed.org/fred/series/observations"
KEY_ENV = "FRED_API_KEY"
USER_AGENT = "SupermAP/0.1 (supply-chain research tool)"


def parse_observations(payload: dict, series_id: str) -> list[dict]:
    """Pure parser: FRED observations JSON -> records. FRED encodes
    missing values as '.'; those rows are dropped, never zero-filled."""
    if not isinstance(payload, dict) or "observations" not in payload:
        raise AdapterError("FRED payload missing 'observations'")
    records = []
    for obs in payload["observations"]:
        if obs.get("value") in (".", "", None):
            continue
        records.append({
            "series_id": series_id,
            "date": obs["date"],
            "value": float(obs["value"]),
        })
    return records


class FredAdapter:
    name = "fred"
    source = SOURCE
    key_env = KEY_ENV
    last_success_at: str | None = None   # set by a completed fetch only

    def has_key(self) -> bool:
        return bool(os.environ.get(KEY_ENV))

    def state(self) -> str:
        """Money-layer status word: NEEDS-KEY without a key; LIVE only
        after a fetch completed (a key is not a live feed); otherwise
        DOWN."""
        if not self.has_key():
            return "NEEDS-KEY"
        return "LIVE" if self.last_success_at else "DOWN"

    def fetch_series(self, series_id: str, limit: int = 250) -> dict:
        if not self.has_key():
            raise NeedsKeyError(
                f"NEEDS-KEY: set {KEY_ENV} to enable FRED live fetch; "
                "no series is fabricated in its absence")
        params = {"series_id": series_id, "api_key": os.environ[KEY_ENV],
                  "file_type": "json", "sort_order": "desc", "limit": limit}
        url = API + "?" + urllib.parse.urlencode(params)
        status, payload, _ = http_get_json(url, user_agent=USER_AGENT, timeout=30)
        if status != 200:
            msg = (payload.get("error_message") if isinstance(payload, dict)
                   else None) or f"FRED HTTP {status}"
            # Upstream text is untrusted for secrets: scrub it the same
            # way common.py scrubs transport errors.
            raise AdapterError(sanitize_text(msg))
        records = parse_observations(payload, series_id)
        prov = provenance(SOURCE, "LIVE")
        for rec in records:
            rec["provenance"] = dict(prov)
        self.last_success_at = prov["retrieved_at"]
        return {"records": records, "provenance": prov}

    def health(self) -> dict:
        if not self.has_key():
            # The shared adapter state enum has no NEEDS-KEY; the registry convention is
            # state=DOWN + a NEEDS-KEY note. This money-layer health entry
            # keeps that note AND the explicit state word NEEDS-KEY when the key is absent.
            return health_dict(
                self.name, "NEEDS-KEY", None, None,
                f"NEEDS-KEY: set {KEY_ENV} to enable live fetch; "
                "no data is served without a key")
        if not self.last_success_at:
            return health_dict(self.name, "DOWN", None, None,
                               "key present; no fetch has completed yet")
        return health_dict(self.name, "LIVE", self.last_success_at, "LIVE")
