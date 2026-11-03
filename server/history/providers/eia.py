"""U.S. EIA Open Data API v2 - HISTORY-CAPABLE (verified).

Daily spot prices (Brent, WTI, NY Harbor ULSD, Gulf jet fuel, Henry Hub
gas) with full history via ``start``/``end``. These are MARKET CONTEXT
series, not graph-place series: place is ``market``. The key is read from
``EIA_API_KEY`` at call time and sent in the ``X-Api-Key`` header; it is
never put in a URL, a cache entry, a record or a log line.
"""
from __future__ import annotations

import urllib.parse
from datetime import date

from ..transport import HttpRequest
from ..util import iso_day
from .base import CAPABLE, HistoryProvider, NotServed, not_served

BASE = "https://api.eia.gov/v2"
SOURCE_LABEL = "U.S. Energy Information Administration (EIA) Open Data API v2"
# (series id, label, route, units)
SERIES = {
    "RBRTE": ("Brent crude oil spot (Europe)", "petroleum/pri/spt", "$/bbl"),
    "RWTC": ("WTI crude oil spot (Cushing, OK)", "petroleum/pri/spt", "$/bbl"),
    "EER_EPD2DXL0_PF4_Y35NY_DPG": ("New York Harbor ULSD diesel spot",
                                   "petroleum/pri/spt", "$/gal"),
    "EER_EPJK_PF4_RGC_DPG": ("US Gulf Coast jet fuel spot",
                             "petroleum/pri/spt", "$/gal"),
    "RNGWHHD": ("Henry Hub natural gas spot", "natural-gas/pri/fut", "$/MMBtu"),
}
MARKET_PLACE = "market"
LENGTH = 5000


class EiaProvider(HistoryProvider):
    name = "eia"
    label = SOURCE_LABEL
    capability = CAPABLE
    budget_key = "eia"
    supports_observations = True
    history_window = "daily spot prices from the 1980s (series-dependent)"
    verified = {
        "checked_on": "2026-10-25", "result": "OK",
        "how": ("GET petroleum/pri/spt/data?frequency=daily&facets[series][]"
                "=RBRTE&start=2023-11-01&end=2024-03-01 with the X-Api-Key "
                "header returned HTTP 200 and 84 daily rows"),
    }
    notes = ("Market context only (never an engine input). place=market; "
             "metric=spot:<series_id>.")

    def credential_state(self):
        return "present" if self.svc.getenv("EIA_API_KEY") else "missing"

    def metrics(self) -> list[dict]:
        return [{"metric": f"spot:{sid}", "applies_to": MARKET_PLACE,
                 "unit": units, "label": label, "native_field": "value"}
                for sid, (label, _route, units) in SERIES.items()]

    def observations(self, place: str, metric: str, start: date,
                     end: date) -> dict:
        sid = metric.split(":", 1)[1] if metric.startswith("spot:") else metric
        if place != MARKET_PLACE or sid not in SERIES:
            return not_served(self.name, "unsupported_metric",
                              f"EIA serves place=market metric=spot:<id> with "
                              f"id in {sorted(SERIES)}", place=place,
                              metric=metric)
        label, route, units = SERIES[sid]
        query = {"series": f"spot|{sid}", "from": iso_day(start),
                 "to": iso_day(end)}

        def fetcher():
            key = self.svc.getenv("EIA_API_KEY")
            if not key:
                raise NotServed("no_credential", "EIA_API_KEY is not set")
            params = {"frequency": "daily", "data[0]": "value",
                      "facets[series][]": sid, "start": iso_day(start),
                      "end": iso_day(end), "sort[0][column]": "period",
                      "sort[0][direction]": "asc", "length": str(LENGTH)}
            req = HttpRequest("GET", f"{BASE}/{route}/data/", params=params,
                              headers={"X-Api-Key": key})
            doc = self.json_body(self.http(req), "EIA data")
            try:
                rows = doc["response"]["data"]
                total = int(doc["response"].get("total") or len(rows))
            except (KeyError, TypeError, ValueError):
                raise NotServed("bad_payload", "EIA: no response.data") from None
            if total > len(rows):
                raise NotServed("range_too_large",
                                f"{total} rows exceed one {LENGTH}-row page")
            slim = []
            for r in rows:
                try:
                    slim.append({"period": str(r["period"]),
                                 "value": float(r["value"])})
                except (KeyError, TypeError, ValueError):
                    continue
            slim.sort(key=lambda r: r["period"])
            status = "OK" if slim else "EMPTY"
            return ({"series_id": sid, "route": route, "rows": slim},
                    req.public(), status, f"{len(slim)} daily rows")

        try:
            doc, hit = self.through_cache(query, fetcher)
        except NotServed as ns:
            return not_served(self.name, ns.reason, ns.detail, place=place,
                              metric=metric)
        s, e = iso_day(start), iso_day(end)
        series = [{"date": r["period"], "value": r["value"]}
                  for r in doc["payload"]["rows"] if s <= r["period"] <= e]
        return {"served": True, "source": self.name, "place": place,
                "metric": f"spot:{sid}", "unit": units, "label": label,
                "frequency": "daily (trading days only)", "series": series,
                "coverage": {"from": s, "to": e, "n": len(series),
                             "missing_dates": []},
                "provenance": dict(doc["provenance"]),
                "cache": self.cache_info(doc, hit)}
