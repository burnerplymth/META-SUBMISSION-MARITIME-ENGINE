"""Open-Meteo historical weather archive - HISTORY-CAPABLE but
INTERMITTENT.

``archive-api.open-meteo.com/v1/archive`` serves ERA5 reanalysis daily
aggregates for any past date at a lat/lon. The host sometimes answers
HTTP 200 (2021-03-22..24 at Suez) and sometimes fails the TLS
handshake (``SSL: UNEXPECTED_EOF`` / handshake timeout) - a
connection-level flake, not a policy denial. A failure is NOT SERVED
(``unreachable``) and is not retried inside a request. Reanalysis is
MODELLED atmosphere, not an observation of the place: it is labelled so.

metric: wind_speed_10m_max | wind_gusts_10m_max | precipitation_sum |
temperature_2m_max (units as Open-Meteo reports them: km/h, mm, degC).
"""
from __future__ import annotations

from datetime import date

from ..transport import HttpRequest
from ..util import iso_day
from .base import CAPABLE, HistoryProvider, NotServed, not_served

API = "https://archive-api.open-meteo.com/v1/archive"
SOURCE_LABEL = "Open-Meteo Historical Weather API (ERA5 reanalysis, CC BY 4.0)"
METRICS = {
    "wind_speed_10m_max": "km/h",
    "wind_gusts_10m_max": "km/h",
    "precipitation_sum": "mm",
    "temperature_2m_max": "degC",
}


class OpenMeteoArchiveProvider(HistoryProvider):
    name = "open_meteo_archive"
    label = SOURCE_LABEL
    capability = CAPABLE
    budget_key = "open_meteo_archive"
    supports_observations = True
    history_window = "1940-> daily reanalysis, any lat/lon"
    verified = {
        "checked_on": "2026-10-25",
        "result": "INTERMITTENT (2 of 4 attempts HTTP 200; others TLS EOF/timeout)",
        "how": ("GET archive-api.open-meteo.com/v1/archive?latitude=30&"
                "longitude=32.5&start_date=2021-03-22&end_date=2021-03-24&"
                "daily=wind_speed_10m_max: HTTP 200 twice (the provider's "
                "own call returned 32.8/39.9/35.9 km/h at Suez); SSL EOF / "
                "handshake timeout on two other attempts. The "
                "historical-forecast-api host answered 200."),
    }
    notes = ("Reanalysis is modelled weather at the node's coordinates, "
             "labelled as such. Unreachable attempts are NOT SERVED, "
             "never retried inside a request, never replaced by anything.")

    def metrics(self) -> list[dict]:
        return [{"metric": m, "applies_to": "any graph place", "unit": u,
                 "label": m.replace("_", " ") + " (ERA5 reanalysis)",
                 "native_field": m} for m, u in METRICS.items()]

    def observations(self, place: str, metric: str, start: date,
                     end: date) -> dict:
        if metric not in METRICS:
            return not_served(self.name, "unsupported_metric",
                              f"one of {sorted(METRICS)}", place=place,
                              metric=metric)
        try:
            book = self.svc.book()
            if place not in book:
                raise NotServed("unknown_place", f"{place!r} is not a graph place")
            node = book.nodes[place]
            lat, lon = float(node["lat"]), float(node["lon"])
            query = {"series": f"era5|{metric}|{lat:.3f},{lon:.3f}",
                     "from": iso_day(start), "to": iso_day(end)}

            def fetcher():
                req = HttpRequest("GET", API, params={
                    "latitude": f"{lat:.3f}", "longitude": f"{lon:.3f}",
                    "start_date": iso_day(start), "end_date": iso_day(end),
                    "daily": metric, "timezone": "GMT"})
                doc = self.json_body(self.http(req), "Open-Meteo archive")
                try:
                    times = doc["daily"]["time"]
                    vals = doc["daily"][metric]
                except (KeyError, TypeError):
                    raise NotServed("bad_payload",
                                    "Open-Meteo: no daily block") from None
                rows = [{"date": t, "value": v} for t, v in zip(times, vals)
                        if v is not None]
                status = "OK" if rows else "EMPTY"
                return ({"lat": lat, "lon": lon, "rows": rows}, req.public(),
                        status, f"{len(rows)} days")

            doc, hit = self.through_cache(query, fetcher)
        except NotServed as ns:
            return not_served(self.name, ns.reason, ns.detail, place=place,
                              metric=metric)
        s, e = iso_day(start), iso_day(end)
        series = [r for r in doc["payload"]["rows"] if s <= r["date"] <= e]
        return {"served": True, "source": self.name, "place": place,
                "metric": metric, "unit": METRICS[metric],
                "label": f"{metric} (ERA5 reanalysis - MODELLED weather)",
                "frequency": "daily", "series": series,
                "coverage": {"from": s, "to": e, "n": len(series),
                             "missing_dates": []},
                "provenance": dict(doc["provenance"]),
                "cache": self.cache_info(doc, hit)}
