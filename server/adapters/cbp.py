"""CBP Border Wait Times adapter — no key.

Records are wait observations for ground nodes:
``{node_id, port_name, crossing, lanes: "commercial", wait_min}`` —
``wait_min`` is null when CBP reports lanes closed / update pending
(unknown is NOT zero — CBP's own discipline, kept here).

ENDPOINT NOTES:
  * Legacy endpoint ``https://apps.cbp.gov/bwt/bwt.xml``: the origin
    closes the connection without any response (``RemoteDisconnected``)
    and offers no redirect. The apps.cbp.gov BWT host does not serve
    the feed.
  * Current endpoint: the Border Wait Times service lives at
    ``bwt.cbp.gov`` and its public API ``GET /api/waittimes`` returns the
    SAME BWT data model as one JSON array (about 85 ports, ~95 KB).
    This is the primary
    endpoint; the legacy XML feed is kept as a fallback and its parser
    is kept (see below).
  * XML schema note: the legacy XML parser read
    ``<delay_minutes>`` as a direct child of
    ``<commercial_vehicle_lanes>``; the documented BWT XML schema nests
    it under ``<standard_lanes>`` (checked against an archived
    capture of bwt.xml, Wayback 2019-12-08).
    ``parse_bwt_xml`` descends into ``standard_lanes``/``FAST_lanes``
    and honours ``operational_status`` exactly like the JSON parser.

Wait semantics (both parsers): a wait is numeric only when the lane's
operational_status is "delay" or "no delay" (CBP posts a numeric
delay_minutes in both cases — "no delay" can still carry a small posted
number); "Lanes Closed" / "Update Pending" / "N/A" -> null.
Only crossings with commercial lanes (maximum_lanes > 0) produce
records; passenger-only crossings are not commercial waits.
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET

from .base import AdapterError, BaseAdapter, FetchResult, provenance
from .http_util import http_get
from .nodes import NodeIndex

API_URL = "https://bwt.cbp.gov/api/waittimes"
FEED_URL = "https://apps.cbp.gov/bwt/bwt.xml"  # legacy XML fallback
SOURCE = "CBP Border Wait Times"

# CBP port_name (lowercased substring) -> graph ground node id.
PORT_TO_NODE = {
    "laredo": "gnd_laredo",
    "el paso": "gnd_elpaso",
    "otay mesa": "gnd_otaymesa",
    "nogales": "gnd_nogales",
    "detroit": "gnd_detroit",
    "buffalo": "gnd_buffalo",
    "port huron": "gnd_porthuron",
    "blaine": "gnd_blaine",
}

_KNOWN_STATUSES = ("delay", "no delay")


def _num(value) -> int | None:
    """CBP numeric-as-string -> int; ''/'N/A'/None -> None."""
    if value is None:
        return None
    m = re.search(r"\d+", str(value))
    return int(m.group()) if m else None


def _lane_wait(lanes: dict | None) -> int | None:
    """One lane group (standard_lanes / FAST_lanes dict) -> wait minutes.
    Numeric only for known statuses; closed/pending/N/A -> None."""
    if not lanes:
        return None
    status = str(lanes.get("operational_status") or "").strip().lower()
    if status not in _KNOWN_STATUSES:
        return None
    return _num(lanes.get("delay_minutes"))


def _node_for(port_name: str, idx: NodeIndex) -> str | None:
    node_id = next((nid for key, nid in PORT_TO_NODE.items()
                    if key in (port_name or "").lower()), None)
    return node_id if node_id and node_id in idx else None


def _record(node_id, port_name, crossing, lanes_group, date, time_, prov):
    std = (lanes_group or {}).get("standard_lanes") or {}
    fast = (lanes_group or {}).get("FAST_lanes") or {}
    return {
        "record_type": "border_wait",
        "node_id": node_id,
        "port_name": port_name,
        "crossing": crossing or None,
        "lanes": "commercial",
        "wait_min": _lane_wait(std),
        "fast_wait_min": _lane_wait(fast),
        "lanes_open": _num(std.get("lanes_open")),
        "date": date or None,
        "time": time_ or None,
        "dedupe_key": f"cbp:{node_id}:{crossing}:{date}:{time_}",
        "provenance": dict(prov),
    }


def parse_bwt_json(payload, prov: dict, idx: NodeIndex) -> list[dict]:
    """bwt.cbp.gov /api/waittimes payload (list of port objects) -> records."""
    records = []
    for port in payload if isinstance(payload, list) else []:
        if not isinstance(port, dict):
            continue
        node_id = _node_for(port.get("port_name") or "", idx)
        if not node_id:
            continue
        lanes_group = port.get("commercial_vehicle_lanes") or {}
        if not (_num(lanes_group.get("maximum_lanes")) or 0) > 0:
            continue  # no commercial lanes at this crossing
        records.append(_record(
            node_id, (port.get("port_name") or "").strip(),
            (port.get("crossing_name") or "").strip(), lanes_group,
            (port.get("date") or "").strip(), (port.get("time") or "").strip(),
            prov))
    return records


def _xml_lane(lanes_el) -> dict | None:
    """XML lane group element -> the dict shape _lane_wait consumes."""
    if lanes_el is None:
        return None
    return {
        "operational_status": lanes_el.findtext("operational_status"),
        "delay_minutes": lanes_el.findtext("delay_minutes"),
        "lanes_open": lanes_el.findtext("lanes_open"),
    }


def parse_bwt_xml(body: bytes, prov: dict, idx: NodeIndex) -> list[dict]:
    """Documented BWT XML (root <border_wait_time>) -> records.

    Schema (verified against a real archived capture, 2019-12-08):
    <port> carries <commercial_vehicle_lanes> with <maximum_lanes> and
    nested <standard_lanes>/<FAST_lanes>, each holding
    <operational_status>, <delay_minutes>, <lanes_open>.
    """
    root = ET.fromstring(body)
    records = []
    for port in root.iter("port"):
        port_name = (port.findtext("port_name") or "").strip()
        node_id = _node_for(port_name, idx)
        if not node_id:
            continue
        lanes_el = port.find("commercial_vehicle_lanes")
        if lanes_el is None or not (_num(lanes_el.findtext("maximum_lanes")) or 0) > 0:
            continue
        lanes_group = {
            "standard_lanes": _xml_lane(lanes_el.find("standard_lanes")),
            "FAST_lanes": _xml_lane(lanes_el.find("FAST_lanes")),
        }
        records.append(_record(
            node_id, port_name,
            (port.findtext("crossing_name") or "").strip(), lanes_group,
            (port.findtext("date") or "").strip(),
            (port.findtext("time") or "").strip(), prov))
    return records


def _wait_minutes(lanes_el) -> int | None:
    """Wait from a <commercial_vehicle_lanes> element, read from its
    nested <standard_lanes>."""
    if lanes_el is None:
        return None
    return _lane_wait(_xml_lane(lanes_el.find("standard_lanes")))


class CBPAdapter(BaseAdapter):
    name = "cbp"
    source = SOURCE
    health_max_age_s = 3600  # stale-LIVE threshold

    def __init__(self, *, transport=None) -> None:
        super().__init__()
        self._transport = transport or http_get

    def _fetch_live(self) -> FetchResult:
        idx = NodeIndex.load()
        prov = provenance(SOURCE, "LIVE")
        errors = []
        # Primary: the current BWT JSON API at bwt.cbp.gov.
        try:
            status, body, headers = self._transport(API_URL, timeout=30)
            if status != 200:
                raise AdapterError(f"HTTP {status}")
            records = parse_bwt_json(
                json.loads(body.decode("utf-8", "replace")), prov, idx)
            if not records:
                raise AdapterError("JSON parsed but no graph-mapped commercial ports found")
            return FetchResult(records=records, provenance=prov)
        except Exception as exc:
            errors.append(f"bwt.cbp.gov/api/waittimes: {exc}")
        # Fallback: the legacy documented XML feed.
        try:
            status, body, headers = self._transport(FEED_URL, timeout=30)
            ctype = (headers.get("Content-Type") or "").lower()
            if status != 200 or b"<border_wait_time" not in body[:2000]:
                raise AdapterError(
                    f"feed not XML (HTTP {status}, content-type {ctype or 'n/a'})")
            records = parse_bwt_xml(body, prov, idx)
            if not records:
                raise AdapterError("XML parsed but no graph-mapped commercial ports found")
            return FetchResult(records=records, provenance=prov)
        except Exception as exc:
            errors.append(f"apps.cbp.gov/bwt/bwt.xml: {exc}")
        raise AdapterError("CBP BWT unreachable — " + "; ".join(errors))
