"""NTAD (BTS National Transportation Atlas Database) adapter.

NTAD is BTS *reference geodata*: published layers change on a yearly
cadence, so this adapter is a SNAPSHOT loader by design (like PortWatch)
— its honest health state is SNAPSHOT, never LIVE. ``snapshot_by_design``
is set and every record's provenance carries ``mode: "SNAPSHOT"`` with
``retrieved_at`` = the fetch time (staleness stays visible).

Source: the public USDOT_BTS ArcGIS organisation
(``services.arcgis.com/xOi1kZaI0eWDREZv``). Layers chosen for the
graph's ground/sea nodes, from what the service actually offers:

  * ``NTAD_Intermodal_Freight_Facilities_Rail_TOFC_COFC`` (241
    facilities) — rail intermodal (TOFC/COFC) terminals: the graph's
    ``gnd_`` nodes *are* intermodal terminals (Chicago, Dallas-Fort
    Worth, Atlanta, Kansas City…), so this is the ground-node layer.
  * ``NTAD_Intermodal_Freight_Facilities_Marine_Roll_on_Roll_off``
    (84 facilities) — marine Ro/Ro terminals, each tied to a named
    port: grounds to ``sea_`` port nodes.

Not chosen, deliberately: the Air-to-Truck layer (404 per-building
records across 60 airports — no facility identity beyond operator and
floor area; low reference value per record) and the FAF network layers
(348k network nodes — a road graph, not facilities). There is no NTAD
border-crossing FeatureService in the USDOT_BTS organisation (checked
via the ArcGIS search API); border crossings stay CBP's job.

Grounding discipline (same as GDACS): a facility becomes a record only
when it grounds to a graph node — nearest node of the layer's prefix
within ``MAX_NODE_DISTANCE_KM`` (50 km, same-metro grounding). Ungrounded
facilities are dropped, never assigned an invented node. If a fetch
grounds zero records the adapter raises instead of overwriting the
last-good pack with an empty one.
"""
from __future__ import annotations

import urllib.parse

from .base import AdapterError, BaseAdapter, FetchResult, provenance
from .http_util import http_get_json
from .nodes import NodeIndex

SOURCE = "NTAD (BTS)"
BASE = "https://services.arcgis.com/xOi1kZaI0eWDREZv/arcgis/rest/services"
MAX_NODE_DISTANCE_KM = 50.0
PAGE_SIZE = 1000

# layer key -> (service name, node-id prefix, display name)
LAYERS = {
    "rail_tofc_cofc": (
        "NTAD_Intermodal_Freight_Facilities_Rail_TOFC_COFC", "gnd_",
        "Intermodal Freight Facilities Rail TOFC/COFC"),
    "marine_roro": (
        "NTAD_Intermodal_Freight_Facilities_Marine_Roll_on_Roll_off", "sea_",
        "Intermodal Freight Facilities Marine Roll-on/Roll-off"),
}

# Attribute fields kept per layer (besides OBJECTID/LAT/LON handled apart).
_FIELDS = {
    "rail_tofc_cofc": ("TERMINAL", "CITY", "STATE", "PORT", "RAIL_CO",
                       "EQUIPMENT", "SPLC"),
    "marine_roro": ("TERMINAL", "PORT", "OPERATED_BY", "CITY", "STATE",
                    "RAIL_CO", "EQUIPMENT", "NAV_UNIT_ID"),
}


def _clean(value):
    """NTAD pads some strings with spaces; '' -> None."""
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def parse_layer_features(layer: str, features: list[dict], prov: dict,
                         idx: NodeIndex) -> list[dict]:
    """ArcGIS features of one NTAD layer -> grounded records."""
    _, prefix, _ = LAYERS[layer]
    records = []
    for feat in features:
        attrs = feat.get("attributes") or {}
        lat, lon = attrs.get("LAT"), attrs.get("LON")
        if lat is None or lon is None:
            geom = feat.get("geometry") or {}
            lon, lat = geom.get("x"), geom.get("y")
        if lat is None or lon is None:
            continue  # no coordinates — cannot ground, cannot place
        hit = idx.nearest(float(lat), float(lon),
                          max_km=MAX_NODE_DISTANCE_KM, prefixes=(prefix,))
        if not hit:
            continue  # not a graph node — no record (never invent a mapping)
        node_id, dist_km = hit
        rec = {
            "record_type": "ntad_facility",
            "node_id": node_id,
            "layer": layer,
            "facility_name": _clean(attrs.get("TERMINAL")),
            "city": _clean(attrs.get("CITY")),
            "state": _clean(attrs.get("STATE")),
            "lat": float(lat),
            "lon": float(lon),
            "distance_to_node_km": round(dist_km, 1),
            "dedupe_key": f"ntad:{layer}:{attrs.get('OBJECTID')}",
            "provenance": dict(prov),
        }
        for field in _FIELDS[layer]:
            if field in ("TERMINAL", "CITY", "STATE"):
                continue  # already normalised above
            rec[field.lower()] = _clean(attrs.get(field))
        records.append(rec)
    return records


class NTADAdapter(BaseAdapter):
    name = "ntad"
    source = SOURCE
    health_max_age_s = 2592000  # stale-LIVE threshold
    snapshot_by_design = True   # reference geodata: SNAPSHOT is the target

    def __init__(self, *, transport=None) -> None:
        super().__init__()
        self._transport = transport or http_get_json

    def _query_layer(self, service: str) -> list[dict]:
        features: list[dict] = []
        offset = 0
        while True:
            params = {
                "where": "1=1", "outFields": "*",
                "returnGeometry": "false", "f": "json",
                "resultRecordCount": PAGE_SIZE, "resultOffset": offset,
            }
            url = f"{BASE}/{service}/FeatureServer/0/query?" + \
                urllib.parse.urlencode(params)
            status, payload, _ = self._transport(url, timeout=45)
            if status != 200 or not isinstance(payload, dict) \
                    or "features" not in payload:
                raise AdapterError(f"NTAD query HTTP {status} at {service}")
            page = payload["features"]
            features.extend(page)
            if not payload.get("exceededTransferLimit") or not page:
                return features
            offset += len(page)

    def _fetch_live(self) -> FetchResult:
        idx = NodeIndex.load()
        prov = provenance(SOURCE, "SNAPSHOT")
        records: list[dict] = []
        per_layer = {}
        for layer, (service, _, _) in LAYERS.items():
            grounded = parse_layer_features(
                layer, self._query_layer(service), prov, idx)
            per_layer[layer] = len(grounded)
            records.extend(grounded)
        if not records:
            raise AdapterError(
                "NTAD queries returned no graph-grounded facilities")
        self.health.note = (
            f"NTAD reference geodata (static by nature; SNAPSHOT by "
            f"design) — {len(records)} facilities grounded to graph "
            f"nodes within {MAX_NODE_DISTANCE_KM:.0f} km "
            f"(rail_tofc_cofc: {per_layer['rail_tofc_cofc']}, "
            f"marine_roro: {per_layer['marine_roro']})")
        return FetchResult(records=records, provenance=prov)
