"""NOAA / NWS alerts poller — no key. Mints schema-valid events.

Endpoint: https://api.weather.gov/alerts/active
  * Requires a descriptive User-Agent (sent by http_util).
  * NOTE: ``limit`` is NOT a recognised parameter on this endpoint (the API
    itself answered 400 "Query parameter \\"limit\\" is not recognized");
    the national feed is read in one request and the freight gate runs
    BEFORE the MAX_ALERTS cap, so a freight alert is never pushed out of
    the window by unrelated ones.

Minting rule: an alert becomes an event only when it (a) passes
the freight-relevance gate on its NWS event class AND (b) grounds to a
graph node — by alert geometry centroid (nearest node <= 250 km) or by
a phrase match (nodes.NodeIndex.match_text) in the headline/areaDesc.
Severity: NWS severity Extreme/Severe/Moderate/Minor -> 5/4/3/2.
All minted events are kind=weather.

Freight-relevance gate: before this gate existed,
ANY grounded alert minted — an inland-Arizona Severe Thunderstorm
Warning became a SEV-4 disruption at Phoenix airport. Only the event
classes in FREIGHT_RELEVANT_STEMS below may mint; everything else
(Severe Thunderstorm, Tornado, Extreme Wind, Heat, Fire Weather,
Rip Current, ... warnings/watches/advisories) is DROPPED — not minted
and not recorded as a non-disruption record (the smaller honest
behaviour: the events store stays a disruption store). NWS `event`
strings are "<Class> <Warning|Watch|Advisory|...>"; the feed actually
carries, for the allowed classes: "Hurricane Warning", "Hurricane
Watch", "Hurricane Local Statement", "Tropical Storm Warning",
"Tropical Storm Watch", "Storm Surge Warning", "Storm Surge Watch",
"Blizzard Warning", "Ice Storm Warning", "Winter Storm Warning",
"Winter Storm Watch", "Dense Fog Advisory", "Flood Warning",
"Flood Watch", "Flash Flood Warning", "Flash Flood Watch",
"Coastal Flood Warning", "Coastal Flood Watch", "Coastal Flood
Advisory", "River Flood Warning", "River Flood Watch",
"Areal Flood Warning", "Areal Flood Watch", "Areal Flood Advisory",
"Lake Wind..."-style variants aside, every one of these contains one
of the class stems below, which is what the gate matches on.
"""
from __future__ import annotations

import urllib.parse
from datetime import datetime, timezone

from .base import AdapterError, BaseAdapter, FetchResult, provenance
from .c3 import mint_event
from .http_util import http_get_json
from .nodes import NodeIndex

try:  # same dual-import convention as run_once.py
    from ..store.citations import nws_office_id
except ImportError:  # server/ on sys.path as a top-level package
    from store.citations import nws_office_id  # type: ignore

API = "https://api.weather.gov/alerts/active"
SOURCE = "NOAA/NWS"
SEVERITY = {"Extreme": 5, "Severe": 4, "Moderate": 3, "Minor": 2, "Unknown": 2}
MAX_NODE_DISTANCE_KM = 250.0
MAX_ALERTS = 200

# Freight-relevant NWS event classes (see module docstring for the
# enumerated feed strings). Matched as stems against the lowercased
# NWS `event` value so Warning/Watch/Advisory variants all count.
FREIGHT_RELEVANT_STEMS = (
    "hurricane",       # Hurricane Warning/Watch/Local Statement
    "tropical storm",  # Tropical Storm Warning/Watch
    "storm surge",     # Storm Surge Warning/Watch
    "blizzard",        # Blizzard Warning
    "ice storm",       # Ice Storm Warning
    "winter storm",    # Winter Storm Warning/Watch
    "dense fog",       # Dense Fog Advisory
    "flood",           # Flood/Flash Flood/Coastal Flood/River Flood/
                       # Areal Flood Warning/Watch/Advisory
)


def _freight_relevant(event_name: str | None) -> bool:
    e = (event_name or "").lower()
    return any(stem in e for stem in FREIGHT_RELEVANT_STEMS)


def _to_utc_iso(value: str | None) -> str | None:
    """Parse an NWS ISO-8601 timestamp HONOURING its UTC offset and
    return UTC 'YYYY-MM-DDTHH:MM:SSZ' (the old code sliced
    [:19] and appended 'Z', silently shifting every alert by its
    local offset — an EDT-evening alert landed 4 h early and on the
    wrong UTC day, and the event-id day + engine base time inherited
    the shift). A naive value (no offset) is read as already-UTC."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _centroid(geom: dict | None) -> tuple[float, float] | None:
    if not geom:
        return None
    coords = geom.get("coordinates") or []
    pts: list[tuple[float, float]] = []

    def walk(c):
        if isinstance(c, list) and c and isinstance(c[0], (int, float)):
            pts.append((float(c[1]), float(c[0])))
        elif isinstance(c, list):
            for x in c:
                walk(x)
    walk(coords)
    if not pts:
        return None
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


class NOAAAdapter(BaseAdapter):
    name = "noaa"
    source = SOURCE
    health_max_age_s = 3600  # stale-LIVE threshold
    # No freight-relevant, node-grounded alert in a quiet period is a
    # healthy answer, not a shrink for the snapshot guard to override.
    empty_is_expected = True

    def _fetch_live(self) -> FetchResult:
        idx = NodeIndex.load()
        url = API + "?" + urllib.parse.urlencode(
            {"status": "actual", "message_type": "alert,update"})
        status, payload, _ = http_get_json(
            url, headers={"Accept": "application/geo+json"}, timeout=45)
        if status != 200 or not isinstance(payload, dict) or "features" not in payload:
            raise AdapterError(f"NWS HTTP {status}")
        prov = provenance(SOURCE, "LIVE")
        records = []
        gated = [f for f in payload.get("features", [])
                 if isinstance(f, dict) and _freight_relevant(
                     (f.get("properties") or {}).get("event"))]
        for feat in gated[:MAX_ALERTS]:  # gate first, then cap
            props = feat.get("properties", {})
            node_id = None
            centroid = _centroid(feat.get("geometry"))
            if centroid:
                hit = idx.nearest(centroid[0], centroid[1],
                                  max_km=MAX_NODE_DISTANCE_KM,
                                  prefixes=("sea_", "chk_", "air_", "gnd_"))
                if hit:
                    node_id = hit[0]
            if not node_id:
                text = f"{props.get('headline') or ''} {props.get('areaDesc') or ''}"
                node_id = idx.match_text(text)
            if not node_id:
                continue  # unmappable -> stays out of events.jsonl
            alert_id = props.get("id") or feat.get("id") or ""
            # Timestamp mapping: NWS `effective` is when the
            # alert takes effect (its issued start) -> started_at, in
            # UTC; NWS `onset` is the forecast start of the weather
            # itself (may lie in the future) -> stored separately as
            # onset_at. `effective` absent -> fall back to `onset` for
            # started_at. Both converted via _to_utc_iso, so the
            # event-id day (c3 derives it from started_at) and the
            # engine's base time inherit correct UTC.
            started_at = _to_utc_iso(props.get("effective")) \
                or _to_utc_iso(props.get("onset"))
            onset_at = _to_utc_iso(props.get("onset"))
            # capture the alert's expiry (NWS `expires`,
            # falling back to `ends`) as a top-level UTC event field —
            # the store's lifecycle fold reads an event whose expiry
            # has passed as resolved instead of `active` forever.
            expires_at = _to_utc_iso(props.get("expires")) \
                or _to_utc_iso(props.get("ends"))
            ev = mint_event(
                node_id=node_id,
                kind="weather",
                severity=SEVERITY.get(props.get("severity") or "Unknown", 2),
                source=SOURCE,
                evidence_url="https://api.weather.gov/alerts/" + alert_id.rsplit("/", 1)[-1] if alert_id else API,
                dedupe_key=f"nws:{alert_id}",
                started_at=started_at,
            )
            if onset_at:
                ev["onset_at"] = onset_at
            if expires_at:
                ev["expires_at"] = expires_at
            ev["nws"] = {
                "event": props.get("event"), "headline": props.get("headline"),
                "severity": props.get("severity"), "certainty": props.get("certainty"),
                "area": props.get("areaDesc"),
                "onset": props.get("onset"), "effective": props.get("effective"),
                "expires": props.get("expires"), "ends": props.get("ends"),
            }
            # Source citations: what a human-readable citation needs.
            # office = issuing Weather Forecast Office id from the alert's
            # own WMOidentifier; sender = the feed's senderName;
            # sent = when it was sent. Absent when the feed omits them.
            params = props.get("parameters") if isinstance(props.get("parameters"), dict) else {}
            office = nws_office_id(params.get("WMOidentifier"))
            if office:
                ev["nws"]["office"] = office
            if isinstance(props.get("senderName"), str) and props["senderName"].strip():
                ev["nws"]["sender"] = props["senderName"].strip()
            sent_at = _to_utc_iso(props.get("sent"))
            if sent_at:
                ev["nws"]["sent"] = sent_at
            ev["provenance"] = dict(prov)
            records.append(ev)
        return FetchResult(records=records, provenance=prov)
