"""Frankfurter adapter — no key. ECB-reference FX rates.

Endpoint: https://api.frankfurter.dev/v1/latest?base=USD&symbols=EUR,...
Answers with JSON such as
{"amount":1.0,"base":"USD","date":"2026-10-20","rates":{...}}.
Rates are reference rates for the returned `date` (the ECB fixing day),
which is carried on every record — never relabelled as intraday data.
"""
from __future__ import annotations

from .common import AdapterError, health_dict, http_get_json, provenance

SOURCE = "Frankfurter (ECB reference rates)"
API = "https://api.frankfurter.dev/v1/latest"
USER_AGENT = "SupermAP/0.1 (supply-chain research tool)"
DEFAULT_SYMBOLS = ("EUR", "DKK", "GBP", "CNY", "JPY", "SGD", "HKD",
                   "KRW", "INR", "AED", "SAR", "QAR", "TRY", "BRL", "MXN")


def parse_latest(payload: dict) -> list[dict]:
    """Pure parser: Frankfurter /latest JSON -> FX records."""
    if not isinstance(payload, dict) or "rates" not in payload \
            or "base" not in payload or "date" not in payload:
        raise AdapterError(f"Frankfurter payload missing base/date/rates: "
                           f"{sorted(payload) if isinstance(payload, dict) else type(payload)}")
    records = []
    for symbol, rate in sorted(payload["rates"].items()):
        records.append({
            "pair": f"{payload['base']}/{symbol}",
            "base": payload["base"],
            "quote": symbol,
            "rate": float(rate),
            "rate_date": payload["date"],
        })
    if not records:
        raise AdapterError("Frankfurter payload carried zero rates")
    return records


class FrankfurterAdapter:
    name = "frankfurter"
    source = SOURCE

    def __init__(self) -> None:
        self._last_success_at: str | None = None
        self._note: str | None = None
        self._state = "DOWN"

    def fetch_latest(self, base: str = "USD",
                     symbols: tuple[str, ...] = DEFAULT_SYMBOLS) -> dict:
        url = f"{API}?base={base}&symbols={','.join(symbols)}"
        status, payload, _ = http_get_json(url, user_agent=USER_AGENT, timeout=30)
        if status != 200:
            self._state = "DOWN"
            self._note = f"Frankfurter HTTP {status}"
            raise AdapterError(self._note)
        records = parse_latest(payload)
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
