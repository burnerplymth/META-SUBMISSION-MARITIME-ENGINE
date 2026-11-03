"""UN Comtrade v1 (annual goods trade) - HISTORY-CAPABLE (verified),
at ANNUAL resolution only.

Annual reporter-level trade values (USD, ``primaryValue``) for the
gateway economies the daily-trade adapter already reads: USA, China,
Singapore, Netherlands, Egypt, Panama. A year is a context point, not a
daily series - it is served as an annual observation and labelled so;
it can never be sliced to a day. Key: ``UN_COMTRADE_API_KEY`` in the
``Ocp-Apim-Subscription-Key`` header (never stored or logged). Free tier
~1 call/s, ~500 calls/day: this provider's own cap is 10/day, and a 429
is surfaced as NOT SERVED (never hammered).
"""
from __future__ import annotations

import re
from datetime import date

from ..transport import HttpRequest
from ..util import iso_day
from .base import CAPABLE, HistoryProvider, NotServed, not_served

API = "https://comtradeapi.un.org/data/v1/get/C/A/HS"
MIN_CALL_GAP_S = 2.5     # free tier: ~1 call/s; stay well clear (observed
                         # HTTP 429 on two calls 0.4 s apart)
SOURCE_LABEL = "UN Comtrade (UN Statistics Division) v1 API"
# ISO3 -> (reporter code, name, graph places the reporter gates)
REPORTERS = {
    "USA": (842, "United States", ("sea_losangeles", "sea_newyork")),
    "CHN": (156, "China", ("sea_shanghai", "sea_ningbo", "chk_taiwan")),
    "SGP": (702, "Singapore", ("sea_singapore", "chk_malacca")),
    "NLD": (528, "Netherlands", ("sea_rotterdam", "chk_dover")),
    "EGY": (818, "Egypt", ("chk_suez", "sea_portsaid")),
    "PAN": (591, "Panama", ("chk_panama", "sea_balboa")),
}
FLOWS = {"trade_exports_usd": "X", "trade_imports_usd": "M"}


def resolve_reporter(place: str) -> str | None:
    if place in REPORTERS:
        return place
    for iso, (_c, _n, places) in REPORTERS.items():
        if place in places:
            return iso
    return None


class ComtradeProvider(HistoryProvider):
    name = "un_comtrade"
    label = SOURCE_LABEL
    capability = CAPABLE
    budget_key = "un_comtrade"
    supports_observations = True
    history_window = "annual totals, 1962-> (most recent year lags ~1 year)"
    verified = {
        "checked_on": "2026-10-25", "result": "OK (annual only)",
        "how": ("GET /data/v1/get/C/A/HS?reporterCode=818&partnerCode=0"
                "&period=2022,2023,2024&cmdCode=TOTAL&flowCode=X,M with the "
                "subscription-key header returned HTTP 200 and 6 rows"),
    }
    notes = ("Annual resolution only - a year can never be sliced to a "
             "day. metric=trade_exports_usd[:CMD] | trade_imports_usd[:CMD] "
             "(CMD = HS code, default TOTAL). place = reporter ISO3 or a "
             "graph place that reporter gates (e.g. chk_suez -> EGY).")

    def credential_state(self):
        return "present" if self.svc.getenv("UN_COMTRADE_API_KEY") else "missing"

    def metrics(self) -> list[dict]:
        return [{"metric": m + "[:CMD]", "applies_to": "reporter",
                 "unit": "USD/year", "label": m.replace("_", " "),
                 "native_field": "primaryValue"} for m in FLOWS]

    def observations(self, place: str, metric: str, start: date,
                     end: date) -> dict:
        base, _, cmd = metric.partition(":")
        cmd = cmd or "TOTAL"
        iso = resolve_reporter(place)
        if not re.fullmatch(r"[0-9A-Za-z]{1,8}", cmd):
            return not_served(self.name, "unsupported_metric",
                              "the HS code after ':' must be 1-8 letters/digits",
                              place=place, metric=metric)
        if base not in FLOWS or iso is None:
            return not_served(self.name, "unsupported_metric",
                              f"metric in {sorted(FLOWS)} and place a "
                              f"reporter ({sorted(REPORTERS)}) or a place one "
                              "gates", place=place, metric=metric)
        code, name, _places = REPORTERS[iso]
        years = list(range(start.year, end.year + 1))
        query = {"series": f"{iso}|{FLOWS[base]}|{cmd}", "from": f"{years[0]}-01-01",
                 "to": f"{years[-1]}-12-31"}

        def fetcher():
            key = self.svc.getenv("UN_COMTRADE_API_KEY")
            if not key:
                raise NotServed("no_credential", "UN_COMTRADE_API_KEY is not set")
            params = {"reporterCode": str(code), "partnerCode": "0",
                      "period": ",".join(str(y) for y in years),
                      "cmdCode": cmd, "flowCode": FLOWS[base], "motCode": "0",
                      "customsCode": "C00", "partner2Code": "0"}
            req = HttpRequest("GET", API, params=params, headers={
                "Ocp-Apim-Subscription-Key": key})
            last = getattr(self.svc, "_comtrade_last", None)
            if last is not None:
                gap = MIN_CALL_GAP_S - (self.svc.clock() - last)
                if gap > 0:
                    self.svc.sleep(gap)
            self.svc._comtrade_last = self.svc.clock()
            doc = self.json_body(self.http(req), "Comtrade data")
            rows = doc.get("data") if isinstance(doc, dict) else None
            if rows is None:
                raise NotServed("bad_payload", "Comtrade: no data key")
            slim = []
            for r in rows:
                try:
                    slim.append({"year": int(r["period"]),
                                 "value": float(r["primaryValue"])})
                except (KeyError, TypeError, ValueError):
                    continue
            slim.sort(key=lambda r: r["year"])
            status = "OK" if slim else "EMPTY"
            return ({"reporter": iso, "flow": FLOWS[base], "cmd": cmd,
                     "rows": slim}, req.public(), status,
                    f"{len(slim)} annual rows")

        try:
            doc, hit = self.through_cache(query, fetcher)
        except NotServed as ns:
            return not_served(self.name, ns.reason, ns.detail, place=place,
                              metric=metric)
        series = [{"date": f"{r['year']}-01-01", "period": str(r["year"]),
                   "value": r["value"]}
                  for r in doc["payload"]["rows"]
                  if years[0] <= r["year"] <= years[-1]]
        return {"served": True, "source": self.name, "place": place,
                "reporter": iso, "reporter_name": name,
                "metric": f"{base}:{cmd}", "unit": "USD/year",
                "label": f"{name} {base.replace('_', ' ')} (HS {cmd})",
                "frequency": "annual", "series": series,
                "coverage": {"from": iso_day(start), "to": iso_day(end),
                             "n": len(series),
                             "note": "annual points; each stands for its whole year"},
                "provenance": dict(doc["provenance"]),
                "cache": self.cache_info(doc, hit)}
