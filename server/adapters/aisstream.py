"""AISStream adapter — KEY-GATED.

Real API: wss://stream.aisstream.io/v0/stream — subscribe with
``{"APIKey": ..., "BoundingBoxes": [...], "FilterMessageTypes":
["PositionReport"]}`` (boxes are ``[[lat1, lon1], [lat2, lon2]]``). The
websocket client is ws_client.py (stdlib RFC 6455, tunnelled through
HTTPS_PROXY with CONNECT). Key: env ``AISSTREAM_API_KEY`` — read from the
environment only, never logged, never written to a snapshot pack.

Handling of "stream failed: The read operation timed out": a world-box
subscription returns SubscriptionConfirmation + live PositionReports
within seconds, so the cause is the sampling, not the network: the watch boxes are small,
so the stream is sparse, and the 200-message target was never reached
inside the 25 s window — the socket read timeout then raised and threw
away every message already received. Cures: (1) a sampling collect
returns what arrived when the window ends (ws_client ``partial_ok``);
(2) the Panama box covers the canal and Colon (lon -79.9); (3) the watch set is every graph chokepoint
(see WATCH_AREAS for the measured coverage — free AISStream is
terrestrial-only, so Suez/Panama/Hormuz/Bab el-Mandeb send nothing);
(4) positions are de-duplicated per vessel (latest report) and capped
per watch area so one busy strait cannot crowd out the others.
It never fabricates positions: zero PositionReports in the window is a
failed fetch (snapshot fallback, truthful note), not an empty LIVE.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from .base import AdapterError, BaseAdapter, FetchResult, provenance
from .ws_client import ws_collect

SOURCE = "AISStream"
STREAM_URL = "wss://stream.aisstream.io/v0/stream"
# per-cycle sample cap — with a dedupe key per vessel
# position (below), a cycle can add at most this many new records,
# so records.jsonl cannot grow unboundedly from re-sampled positions.
MAX_POSITIONS_PER_CYCLE = 200
COLLECT_WINDOW_S = 40.0
# Watch areas: (graph node id, label, ((lat1, lon1), (lat2, lon2))).
# One entry per box; a chokepoint may have several boxes.
#
# COVERAGE (measured over 40-75 s windows, free terrestrial AIS):
# positions arrive for Singapore Strait (14 vessels/40 s), Danish Straits
# (24), Dover (13), Gibraltar (9), Cape Town (8), Taiwan Strait (7),
# Bosphorus (2) and the northern Malacca Strait (2) — and NONE for Suez,
# Panama, Hormuz or Bab el-Mandeb in any window (terrestrial receivers
# only; no satellite AIS on this tier). Those four stay in the
# subscription (free; they report the moment a receiver covers them) and
# the health note names whichever watched chokepoints sent no report.
WATCH_AREAS = (
    ("chk_malacca", "Singapore Strait", ((0.8, 103.0), (1.8, 104.6))),
    ("chk_malacca", "Northern Malacca Strait", ((2.0, 100.0), (4.5, 102.5))),
    ("chk_dover", "Dover Strait", ((50.5, 0.8), (51.5, 2.0))),
    ("chk_danish", "Danish Straits (Oresund / Great Belt)",
     ((55.0, 10.5), (56.4, 13.0))),
    ("chk_gibraltar", "Strait of Gibraltar", ((35.7, -6.0), (36.3, -5.0))),
    ("chk_turkish", "Bosphorus", ((40.8, 28.7), (41.4, 29.4))),
    ("chk_taiwan", "Taiwan Strait", ((22.0, 118.0), (26.0, 122.0))),
    ("chk_capeofgoodhope", "Cape Town approaches",
     ((-34.5, 18.0), (-33.5, 19.0))),
    ("chk_suez", "Suez Canal (Gulf of Suez to Port Said)",
     ((27.0, 32.0), (31.8, 34.0))),
    ("chk_panama", "Panama Canal (Colon to Balboa)",
     ((8.5, -80.2), (9.7, -79.2))),
    ("chk_hormuz", "Strait of Hormuz", ((25.5, 55.0), (27.2, 57.5))),
    ("chk_babelmandeb", "Bab el-Mandeb", ((11.8, 42.6), (13.2, 44.2))),
)
WATCHED_NODES = tuple(dict.fromkeys(a[0] for a in WATCH_AREAS))
DEFAULT_BOXES = [[list(a[2][0]), list(a[2][1])] for a in WATCH_AREAS]
PER_AREA_CAP = 25

# Continuous-collector hand-off (server/live/ais_collector.py). AISStream
# allows ONE connection per key; while the live hub's continuous collector
# holds it, this sampling adapter must not open a second one. The live
# package registers a callable returning the latest report per vessel it
# already holds; the adapter then builds its per-chokepoint sample from
# that (real reports, no network, no second connection).
_SAMPLE_PROVIDER = None


def set_sample_provider(fn) -> None:
    global _SAMPLE_PROVIDER
    _SAMPLE_PROVIDER = fn


def collector_expected() -> bool:
    """True when this process is configured to run the continuous AIS
    collector (live twin flag on AND SUPERMAP_LIVE_COLLECT includes ais).
    The first ingestion cycle can start before the collector has
    registered itself; opening a second AISStream connection in that
    gap would break the one-connection rule."""
    twin = os.environ.get("SUPERMAP_LIVE_TWIN", "").strip().lower() in (
        "1", "true", "yes")
    raw = os.environ.get("SUPERMAP_LIVE_COLLECT", "").strip().lower()
    parts = {x.strip() for x in raw.split(",")}
    return twin and bool(parts & {"1", "yes", "true", "on", "all", "ais",
                                  "aisstream", "vessels"})


def _area_for(lat, lon):
    """(node_id, label) of the watch area containing the point, else
    (None, None)."""
    try:
        la, lo = float(lat), float(lon)
    except (TypeError, ValueError):
        return None, None
    for node_id, label, ((la1, lo1), (la2, lo2)) in WATCH_AREAS:
        if min(la1, la2) <= la <= max(la1, la2) and \
                min(lo1, lo2) <= lo <= max(lo1, lo2):
            return node_id, label
    return None, None


def _iso_utc(value):
    """AISStream ``time_utc`` ("2024-05-01 00:02:50.589901106 +0000
    UTC") -> ISO 8601 Z, seconds precision; unparseable -> the raw
    string is kept (never invented)."""
    if not value:
        return value
    text = str(value).strip()
    if text.endswith(" UTC"):
        text = text[:-4].strip()
    try:
        date_part, time_part = text.split(" ", 2)[0:2]
        if "." in time_part:
            head, frac = time_part.split(".", 1)
            time_part = head
        dt = datetime.strptime(f"{date_part} {time_part}", "%Y-%m-%d %H:%M:%S")
        return dt.replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, IndexError):
        return value


class AISStreamAdapter(BaseAdapter):
    name = "aisstream"
    source = SOURCE
    health_max_age_s = 3600  # stale-LIVE threshold
    key_env = "AISSTREAM_API_KEY"

    def __init__(self) -> None:
        super().__init__()
        self._uncovered: list = []

    def fetch(self):
        self._uncovered = []
        result = super().fetch()
        if self.health.state == "LIVE" and self._uncovered:
            self.health.note = (
                "LIVE position sample (terrestrial AIS; a sample of "
                "reports received, not a vessel census). No reports this "
                "window from: " + ", ".join(self._uncovered))
        return result

    def _fetch_from_collector(self, rows) -> FetchResult:
        prov = provenance(SOURCE, "LIVE")
        per_area: dict = {}
        records: list = []
        for r in rows:
            node_id, area = _area_for(r.get("lat"), r.get("lon"))
            if node_id is None:
                continue          # the collector holds the whole world;
            #                       this adapter reports the watch areas
            if per_area.get(node_id, 0) >= PER_AREA_CAP:
                continue
            per_area[node_id] = per_area.get(node_id, 0) + 1
            records.append({
                "record_type": "ais_position", "mmsi": r.get("mmsi"),
                "ship_name": r.get("ship_name"), "lat": r.get("lat"),
                "lon": r.get("lon"), "sog": r.get("sog"),
                "cog": r.get("cog"), "nav_status": r.get("nav_status"),
                "reported_at": r.get("reported_at"), "node_id": node_id,
                "watch_area": area,
                "dedupe_key": (f"aisstream:{r.get('mmsi')}:"
                               f"{r.get('reported_at')}:{r.get('lat')}:"
                               f"{r.get('lon')}"),
                "provenance": dict(prov)})
            if len(records) >= MAX_POSITIONS_PER_CYCLE:
                break
        if not records:
            raise AdapterError(
                "AISStream continuous collector is running but holds no "
                "recent vessel inside the watch areas")
        self._uncovered = [n for n in WATCHED_NODES if n not in per_area]
        return FetchResult(records=records, provenance=prov)

    def _fetch_live(self) -> FetchResult:  # only reached when a key exists
        provider = _SAMPLE_PROVIDER
        if provider is not None:
            return self._fetch_from_collector(provider())
        if collector_expected():
            raise AdapterError(
                "AISStream sampling deferred: the live hub's continuous "
                "collector owns the single AISStream connection (starting)")
        key = os.environ[self.key_env]
        subscribe = {
            "APIKey": key,
            "BoundingBoxes": DEFAULT_BOXES,
            "FilterMessageTypes": ["PositionReport"],
        }
        try:
            messages = ws_collect(STREAM_URL, subscribe,
                                  max_messages=MAX_POSITIONS_PER_CYCLE * 3,
                                  timeout_s=COLLECT_WINDOW_S,
                                  partial_ok=True)
        except Exception as exc:
            # str(exc) of ws/ssl errors carries no request data; the key
            # lives only in the subscribe frame body.
            raise AdapterError(f"AISStream stream failed: {exc}") from exc
        prov = provenance(SOURCE, "LIVE")
        latest: dict = {}      # one record per vessel: its latest report
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            if msg.get("error"):
                raise AdapterError(
                    "AISStream rejected the subscription: "
                    f"{str(msg['error'])[:120]}")
            meta = msg.get("MetaData") or {}
            report = (msg.get("Message") or {}).get("PositionReport") or {}
            if not report:
                continue  # SubscriptionConfirmation etc.
            lat = report.get("Latitude", meta.get("latitude"))
            lon = report.get("Longitude", meta.get("longitude"))
            reported_at = _iso_utc(meta.get("time_utc"))
            # a position IS an occurrence of (vessel, time,
            # place) — key it as such, so the same position re-sampled
            # in a later cycle dedupes in the RecordStore instead of
            # appending forever.
            dedupe_key = (
                f"aisstream:{meta.get('MMSI') or 'unknown'}:"
                f"{reported_at or ''}:{lat}:{lon}")
            node_id, area = _area_for(lat, lon)
            latest[meta.get("MMSI") or dedupe_key] = {
                "record_type": "ais_position",
                "mmsi": meta.get("MMSI"),
                "ship_name": (meta.get("ShipName") or "").strip() or None,
                "lat": lat,
                "lon": lon,
                "sog": report.get("Sog"),
                "cog": report.get("Cog"),
                "nav_status": report.get("NavigationalStatus"),
                "reported_at": reported_at,
                "node_id": node_id,
                "watch_area": area,
                "dedupe_key": dedupe_key,
                "provenance": dict(prov),
            }
        if not latest:
            raise AdapterError(
                "AISStream connected but delivered no PositionReports in "
                f"the {COLLECT_WINDOW_S:g}s window")
        records: list = []
        per_area: dict = {}
        for rec in latest.values():
            area_key = rec["node_id"]
            if per_area.get(area_key, 0) >= PER_AREA_CAP:
                continue
            per_area[area_key] = per_area.get(area_key, 0) + 1
            records.append(rec)
            if len(records) >= MAX_POSITIONS_PER_CYCLE:
                break
        self._uncovered = [n for n in WATCHED_NODES if n not in per_area]
        return FetchResult(records=records, provenance=prov)
