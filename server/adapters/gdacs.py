"""GDACS event poller — no key. Mints schema-valid events.

Endpoint (GeoJSON):
  https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH
      ?eventlist=EQ,TC,FL,VO,DR,WF&fromdate=..&todate=..&alertlevel=Orange,Red

Minting rule: an alert becomes a DisruptionEvent only when its
epicentre grounds to a graph node (nearest sea/air/ground node within
250 km). Unmappable alerts stay out of events.jsonl — no invented nodes.
Kind mapping: TC/FL/VO/WF -> weather; EQ -> accident; DR -> other.
Severity: GDACS alertscore/alertlevel -> Red 5..4, Orange 3, else 2.
"""
from __future__ import annotations

import urllib.parse
from datetime import datetime, timedelta, timezone

from .base import AdapterError, BaseAdapter, FetchResult, provenance
from .c3 import mint_event
from .http_util import http_get_json
from .nodes import NodeIndex

API = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"
SOURCE = "GDACS"
KIND_BY_TYPE = {"TC": "weather", "FL": "weather", "VO": "weather",
                "WF": "weather", "EQ": "accident", "DR": "other"}
MAX_NODE_DISTANCE_KM = 250.0


def _iso_from_gdacs(value: str | None) -> str | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(value[:19], fmt).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            continue
    return None


class GDACSAdapter(BaseAdapter):
    name = "gdacs"
    source = SOURCE
    health_max_age_s = 21600  # stale-LIVE threshold
    # No qualifying alert in a quiet period is a healthy answer, not a
    # shrink for the snapshot guard to override.
    empty_is_expected = True

    def _fetch_live(self) -> FetchResult:
        idx = NodeIndex.load()
        today = datetime.now(timezone.utc).date()
        params = {
            "eventlist": "EQ,TC,FL,VO,DR,WF",
            "fromdate": (today - timedelta(days=7)).isoformat(),
            "todate": today.isoformat(),
            "alertlevel": "Orange,Red",
        }
        status, payload, _ = http_get_json(
            API + "?" + urllib.parse.urlencode(params), timeout=45)
        if status != 200 or not isinstance(payload, dict) or "features" not in payload:
            raise AdapterError(f"GDACS HTTP {status}")
        prov = provenance(SOURCE, "LIVE")
        records = []
        for feat in payload.get("features", []):
            props = feat.get("properties", {})
            geom = (feat.get("geometry") or {}).get("coordinates") or []
            if len(geom) < 2:
                continue
            lon, lat = float(geom[0]), float(geom[1])
            hit = idx.nearest(lat, lon, max_km=MAX_NODE_DISTANCE_KM)
            if not hit:
                continue  # cannot ground to a graph node -> not an event
            node_id, dist_km = hit
            etype = (props.get("eventtype") or "").upper()
            level = (props.get("alertlevel") or "").lower()
            severity = 5 if level == "red" else 3 if level == "orange" else 2
            event_id = props.get("eventid")
            started = _iso_from_gdacs(props.get("fromdate"))
            ev = mint_event(
                node_id=node_id,
                kind=KIND_BY_TYPE.get(etype, "other"),
                severity=severity,
                source=SOURCE,
                evidence_url=f"https://www.gdacs.org/report.aspx?eventtype={etype}&eventid={event_id}",
                dedupe_key=f"gdacs:{etype}:{event_id}",
                started_at=started,
            )
            # capture the event's end (GDACS `todate`) as a
            # top-level UTC expiry field — the store's lifecycle fold
            # reads an event whose expiry has passed as resolved.
            expires_at = _iso_from_gdacs(props.get("todate"))
            if expires_at:
                ev["expires_at"] = expires_at
            ev["gdacs"] = {
                "eventtype": etype, "eventid": event_id,
                "name": props.get("name"), "alertlevel": props.get("alertlevel"),
                "country": props.get("country"),
                "fromdate": props.get("fromdate"),
                "todate": props.get("todate"),
                "distance_to_node_km": round(dist_km, 1),
            }
            # The feed's own human report page (properties.url.report)
            # and episode id, kept so the served citation can point at it.
            urls = props.get("url") if isinstance(props.get("url"), dict) else {}
            if isinstance(urls.get("report"), str):
                ev["gdacs"]["report_url"] = urls["report"]
            if props.get("episodeid") is not None:
                ev["gdacs"]["episodeid"] = props.get("episodeid")
            ev["provenance"] = dict(prov)
            records.append(ev)
        return FetchResult(records=records, provenance=prov)
