"""GDELT DOC 2.0 date-range queries - BLOCKED in practice
(observed: HTTP 429 "Please limit requests to one every 5
seconds" on every attempt, spaced 7 s and 40 s apart - GDELT
rate-limits shared IPs).

In principle ``startdatetime``/``enddatetime`` give a news-volume
timeline (``mode=TimelineVol``) for any past range. The provider is
wired through an injectable transport; when the real service rate-limits
it answers NOT SERVED (rate_limited) honestly. A 429 is
never retried and its budget is 4 calls/day. The TimelineVol payload
shape here follows GDELT's published format and has NOT been observed
(``verified.payload_shape_observed`` is false).

GDELT is a feed, never an event source: it never mints events (the
feed rule); it serves a ``news_volume`` observation series only.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from ..transport import HttpRequest
from ..util import iso_day
from .base import BLOCKED, HistoryProvider, NotServed, not_served

API = "https://api.gdeltproject.org/api/v2/doc/doc"
SOURCE_LABEL = "GDELT"


class GdeltProvider(HistoryProvider):
    name = "gdelt"
    label = SOURCE_LABEL
    capability = BLOCKED
    budget_key = "gdelt"
    supports_observations = True
    history_window = "2017-> (DOC 2.0 date-range queries; rolling 3 months for ArtList)"
    verified = {
        "checked_on": "2026-10-25", "result": "BLOCKED (HTTP 429)",
        "how": ("GET /api/v2/doc/doc?mode=TimelineVol with startdatetime/"
                "enddatetime: 429 on four attempts, 7 s and 40 s apart"),
        "payload_shape_observed": False,
    }
    notes = ("Capable in principle; GDELT may rate-limit the calling IP. "
             "Re-verify from another network before relying on it. Never "
             "mints events.")

    def metrics(self) -> list[dict]:
        return [{"metric": "news_volume", "applies_to": "any graph place",
                 "unit": "% of monitored coverage (TimelineVol)",
                 "label": "news volume mentioning the place and a "
                          "disruption word", "native_field": "value"}]

    def observations(self, place: str, metric: str, start: date,
                     end: date) -> dict:
        if metric != "news_volume":
            return not_served(self.name, "unsupported_metric",
                              "only metric=news_volume", place=place,
                              metric=metric)
        try:
            book = self.svc.book()
            if place not in book:
                raise NotServed("unknown_place", f"{place!r} is not a graph place")
            q = (f'"{book.name(place)}" (closure OR disruption OR strike OR '
                 'blocked OR delay OR congestion)')
            query = {"series": f"timelinevol|{place}", "from": iso_day(start),
                     "to": iso_day(end)}

            def fetcher():
                req = HttpRequest("GET", API, params={
                    "query": q, "mode": "TimelineVol", "format": "json",
                    "startdatetime": start.strftime("%Y%m%d") + "000000",
                    "enddatetime": end.strftime("%Y%m%d") + "235959"})
                resp = self.http(req)
                text = resp.body.decode("utf-8", "replace")
                if resp.status == 200 and not text.lstrip().startswith("{"):
                    raise NotServed("rate_limited",
                                    "GDELT returned its plain-text notice")
                doc = self.json_body(resp, "GDELT TimelineVol")
                tl = (doc.get("timeline") or [{}])[0].get("data") or []
                per_day: dict = defaultdict(list)
                for pt in tl:
                    d = str(pt.get("date", ""))[:8]
                    if len(d) == 8 and d.isdigit():
                        per_day[f"{d[:4]}-{d[4:6]}-{d[6:8]}"].append(
                            float(pt.get("value", 0.0)))
                rows = [{"date": k, "value": round(sum(v) / len(v), 6)}
                        for k, v in sorted(per_day.items())]
                status = "OK" if rows else "EMPTY"
                return ({"query": q, "rows": rows}, req.public(), status,
                        f"{len(rows)} days")

            doc, hit = self.through_cache(query, fetcher)
        except NotServed as ns:
            return not_served(self.name, ns.reason, ns.detail, place=place,
                              metric=metric)
        s, e = iso_day(start), iso_day(end)
        series = [r for r in doc["payload"]["rows"] if s <= r["date"] <= e]
        return {"served": True, "source": self.name, "place": place,
                "metric": "news_volume", "unit": "% of coverage",
                "label": "GDELT news volume", "frequency": "daily",
                "series": series,
                "coverage": {"from": s, "to": e, "n": len(series),
                             "missing_dates": []},
                "provenance": dict(doc["provenance"]),
                "cache": self.cache_info(doc, hit)}
