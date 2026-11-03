"""IMF PortWatch snapshot loader — no key.

Real endpoints:
  * Ports registry (ArcGIS FeatureServer):
    https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/
        PortWatch_ports_database/FeatureServer/0/query
    Fields include portid, portname, LOCODE, lat/lon, vessel_count_*.
  * Disruptions view (mirrors the PortWatch/GDACS disruption database):
    .../disruptions_view/FeatureServer/0/query
    Fields include eventid, eventtype, alertlevel, fromdate/todate, lat/long.

By design this is a *snapshot loader*: it fetches the
source, writes the last-good pack to data/snapshots/portwatch.json, and
serves records labelled mode=SNAPSHOT with retrieved_at = fetch time.
Records: ``port_activity`` (one per graph-matched port/chokepoint, with
vessel counts) — activity snapshots are evidence records, not events;
minting events from a static registry count would fabricate disruptions.
"""
from __future__ import annotations

import urllib.parse

from .base import AdapterError, BaseAdapter, FetchResult, provenance
from .http_util import http_get_json
from .nodes import NodeIndex

BASE = "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services"
PORTS_URL = f"{BASE}/PortWatch_ports_database/FeatureServer/0/query"
DISRUPTIONS_URL = f"{BASE}/disruptions_view/FeatureServer/0/query"
SOURCE = "IMF PortWatch"


PAGE = 1000  # the service caps a page at 1,000 rows


def _query(url: str, params: dict) -> list[dict]:
    full = url + "?" + urllib.parse.urlencode(params)
    status, payload, _ = http_get_json(full, timeout=45)
    if status != 200 or not isinstance(payload, dict) or "features" not in payload:
        raise AdapterError(f"PortWatch query HTTP {status} at {url}")
    return [f.get("attributes", {}) for f in payload["features"]]


def _query_paged(url: str, params: dict) -> list[dict]:
    """All rows for a query, following resultOffset pagination like
    portwatch_daily._paged. The old ports query requested
    2,000 rows in one call while the service silently caps a page at
    1,000, so the tail of the registry was dropped without a signal."""
    out: list[dict] = []
    offset = 0
    while True:
        batch = _query(url, dict(params, resultRecordCount=PAGE,
                                 resultOffset=offset))
        out.extend(batch)
        if len(batch) < PAGE:
            return out
        offset += len(batch)


class PortWatchAdapter(BaseAdapter):
    name = "portwatch"
    source = SOURCE
    health_max_age_s = 86400  # stale-LIVE threshold
    snapshot_by_design = True

    def _fetch_live(self) -> FetchResult:
        idx = NodeIndex.load()
        prov = provenance(SOURCE, "SNAPSHOT")
        records: list[dict] = []

        ports = _query_paged(PORTS_URL, {
            "where": "1=1", "outFields": "*",
            "returnGeometry": "false", "f": "json",
        })
        for p in ports:
            node_id = idx.by_locode(p.get("LOCODE") or "") or idx.by_name(p.get("portname") or "")
            if not node_id:
                continue  # not a graph node — no record (never invent a mapping)
            records.append({
                "record_type": "port_activity",
                "node_id": node_id,
                "portid": p.get("portid"),
                "portname": p.get("portname"),
                "locode": p.get("LOCODE"),
                "vessel_count_total": p.get("vessel_count_total"),
                "vessel_count_container": p.get("vessel_count_container"),
                "vessel_count_dry_bulk": p.get("vessel_count_dry_bulk"),
                "vessel_count_general_cargo": p.get("vessel_count_general_cargo"),
                "vessel_count_tanker": p.get("vessel_count_tanker"),
                # window-dated key (the portwatch_daily
                # pattern) — the old static key made every refreshed
                # activity value a permanent dupe in the RecordStore.
                # Same-day refreshes are handled by the store's
                # port_activity upsert; a new day is a new record.
                "dedupe_key": f"portwatch:port:{p.get('portid')}:{prov['retrieved_at'][:10]}",
                "provenance": dict(prov),
            })

        disruptions = _query(DISRUPTIONS_URL, {
            "where": "1=1", "outFields": "*",
            "returnGeometry": "false", "f": "json",
            "resultRecordCount": 200,
            "orderByFields": "fromdate DESC",
        })
        for d in disruptions:
            records.append({
                "record_type": "portwatch_disruption",
                "eventid": d.get("eventid"),
                "eventtype": d.get("eventtype"),
                "eventname": d.get("eventname"),
                "alertlevel": d.get("alertlevel"),
                "country": d.get("country"),
                "fromdate": d.get("fromdate"),
                "todate": d.get("todate"),
                "lat": d.get("lat"),
                "lon": d.get("long"),
                "affected_ports": d.get("affectedpo"),
                "dedupe_key": f"portwatch:disruption:{d.get('eventid')}",
                "provenance": dict(prov),
            })
        return FetchResult(records=records, provenance=prov)
