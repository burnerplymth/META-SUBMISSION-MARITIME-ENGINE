"""GDACS event list by date range - HISTORY-CAPABLE (verified).

``geteventlist/SEARCH?fromdate=&todate=&eventlist=&alertlevel=`` returns
every Orange/Red alert (earthquake, tropical cyclone, flood, volcano,
drought, wildfire) overlapping the range, with dates, severity and the
epicentre. The live adapter (adapters/gdacs.py) reads the last 7 days of
the same endpoint; this provider reads any past range.

Minting rule mirrors the live adapter exactly (so a re-fetched
occurrence gets the SAME id as the live store would have minted):
  * an alert becomes a DisruptionEvent only when its epicentre grounds to
    a graph node within 250 km - unmappable alerts are COUNTED, not
    invented onto a node;
  * kind: TC/FL/VO/WF -> weather, EQ -> accident, DR -> other;
  * severity: Red 5, Orange 3.
Historical differences, stated: ``mode`` is SNAPSHOT (a recorded fetch,
never LIVE); ``detected_at`` is the event's own start (history has no
detection instant); ``status`` is resolved once the event's end date has
passed.
"""
from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta

from ..transport import HttpRequest
from ..util import iso_day, parse_day
from .base import CAPABLE, HistoryProvider, NotServed, not_served

API = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"
SOURCE_LABEL = "GDACS"
KIND_BY_TYPE = {"TC": "weather", "FL": "weather", "VO": "weather",
                "WF": "weather", "EQ": "accident", "DR": "other"}
EVENT_LIST = "EQ,TC,FL,VO,DR,WF"
ALERT_LEVELS = "Orange,Red"
MAX_NODE_DISTANCE_KM = 250.0
PAGE_SIZE = 100
MAX_PAGES = 5


def _ts(value) -> str | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(str(value)[:19], fmt).strftime(
                "%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            continue
    return None


def _slim(feature: dict) -> dict | None:
    props = feature.get("properties") or {}
    geom = (feature.get("geometry") or {}).get("coordinates") or []
    if len(geom) < 2:
        return None
    keep = {k: props.get(k) for k in (
        "eventtype", "eventid", "episodeid", "name", "alertlevel",
        "alertscore", "country", "iso3", "fromdate", "todate", "severitydata")
        if k in props}
    keep["lon"], keep["lat"] = float(geom[0]), float(geom[1])
    return keep


class GdacsProvider(HistoryProvider):
    name = "gdacs"
    label = SOURCE_LABEL
    capability = CAPABLE
    budget_key = "gdacs"
    supports_events = True
    history_window = "1982-> (Orange/Red alert list, any past range)"
    verified = {
        "checked_on": "2026-10-25", "result": "OK",
        "how": ("GET geteventlist/SEARCH?fromdate=2023-10-01&todate=2024-03-31"
                "&alertlevel=Orange,Red returned 47 alerts (HTTP 200)"),
    }
    notes = ("Events ground to a graph node within 250 km or are counted as "
             "unmapped (never invented onto a node). One feature per event "
             "(latest episode).")

    def _features(self, start: date, end: date):
        query = {"series": f"{EVENT_LIST}|{ALERT_LEVELS}",
                 "from": iso_day(start), "to": iso_day(end)}

        def fetcher():
            feats: list[dict] = []
            last = None
            for page in range(1, MAX_PAGES + 1):
                req = HttpRequest("GET", API, params={
                    "eventlist": EVENT_LIST, "fromdate": iso_day(start),
                    "todate": iso_day(end), "alertlevel": ALERT_LEVELS,
                    "pagesize": str(PAGE_SIZE), "pagenumber": str(page)})
                last = req
                doc = self.json_body(self.http(req), "GDACS event list")
                if not isinstance(doc, dict) or "features" not in doc:
                    raise NotServed("bad_payload", "GDACS: no features key")
                batch = doc["features"]
                feats.extend(batch)
                if len(batch) < PAGE_SIZE:
                    break
            slim, seen = [], set()
            for s in (_slim(f) for f in feats):
                if s and (s.get("eventtype"), s.get("eventid")) not in seen:
                    seen.add((s.get("eventtype"), s.get("eventid")))
                    slim.append(s)
            slim.sort(key=lambda s: (str(s.get("fromdate")), s.get("eventtype") or "",
                                     int(s.get("eventid") or 0)))
            status = "OK" if slim else "EMPTY"
            note = f"{len(slim)} alerts ({len(feats) - len(slim)} without a point)"
            return slim, (last.public() if last else {}), status, note

        return self.through_cache(query, fetcher)

    def events(self, start: date, end: date, places=None) -> dict:
        try:
            doc, hit = self._features(start, end)
            book = self.svc.book()
        except NotServed as ns:
            return not_served(self.name, ns.reason, ns.detail)
        retrieved_at = doc["provenance"]["retrieved_at"]
        want = set(places) if places else None
        events, unmapped, outside = [], 0, 0
        for f in doc["payload"]:
            fromdate, todate = _ts(f.get("fromdate")), _ts(f.get("todate"))
            if not fromdate:
                continue
            # overlap with [start, end]
            if fromdate[:10] > iso_day(end) or (todate and todate[:10] < iso_day(start)):
                outside += 1
                continue
            hit_node = book.nearest(f["lat"], f["lon"], MAX_NODE_DISTANCE_KM)
            if not hit_node:
                unmapped += 1
                continue
            node_id, dist = hit_node
            if want is not None and node_id not in want:
                continue
            etype = (f.get("eventtype") or "").upper()
            level = (f.get("alertlevel") or "").lower()
            sev = 5 if level == "red" else 3 if level == "orange" else 2
            kind = KIND_BY_TYPE.get(etype, "other")
            dedupe = f"gdacs:{etype}:{f.get('eventid')}"
            day = re.sub(r"\D", "", fromdate)[:8]
            ended = bool(todate) and todate[:10] < retrieved_at[:10]
            ev = {
                "id": "evt_%s_%s_%s_%s" % (
                    day, node_id, kind,
                    hashlib.sha256(dedupe.encode()).hexdigest()[:8]),
                "node_id": node_id, "kind": kind, "severity": sev,
                "started_at": fromdate, "detected_at": fromdate,
                "retrieved_at": retrieved_at, "source": SOURCE_LABEL,
                "evidence_url": ("https://www.gdacs.org/report.aspx?"
                                 f"eventtype={etype}&eventid={f.get('eventid')}"),
                "status": "resolved" if ended else "active",
                "mode": "SNAPSHOT", "dedupe_key": dedupe, "historical": True,
                "gdacs": {"eventtype": etype, "eventid": f.get("eventid"),
                          "name": f.get("name"), "alertlevel": f.get("alertlevel"),
                          "country": f.get("country"),
                          "fromdate": f.get("fromdate"), "todate": f.get("todate"),
                          "distance_to_node_km": round(dist, 1)},
                "provenance": dict(doc["provenance"]),
            }
            if todate:
                ev["expires_at"] = todate
                try:
                    d0 = datetime.strptime(fromdate, "%Y-%m-%dT%H:%M:%SZ")
                    d1 = datetime.strptime(todate, "%Y-%m-%dT%H:%M:%SZ")
                    ev["expected_duration_d"] = round(
                        max((d1 - d0).total_seconds() / 86400.0, 0.1), 2)
                except ValueError:
                    pass
            events.append(ev)
        events.sort(key=lambda e: (e["started_at"], e["id"]))
        return {"served": True, "source": self.name, "events": events,
                "unmapped_count": unmapped, "outside_window_count": outside,
                "provenance": dict(doc["provenance"]),
                "cache": self.cache_info(doc, hit)}
