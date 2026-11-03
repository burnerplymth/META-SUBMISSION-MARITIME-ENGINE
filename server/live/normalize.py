"""Record normalisers: adapter records -> compact live-hub rows.

Pure functions. A record that cannot be normalised honestly (missing or
out-of-range position, no id) returns None and is counted as rejected
by the hub; nothing is repaired, guessed or defaulted into existence.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone

try:  # source citations
    from store.citations import derive_citation
except ImportError:  # imported as server.live.* from the repo root
    from server.store.citations import derive_citation  # type: ignore

MS_TO_KN = 1.943844

_AIRCRAFT_TYPES = {"aircraft_state", "aircraft_position", "air_position",
                   "air_state", "aircraft"}
_ICAO_RE = re.compile(r"^[0-9a-f]{6}$")
_MMSI_RE = re.compile(r"^\d{1,9}$")


def parse_iso(value) -> float | None:
    """ISO 8601 (Z or offset) -> epoch seconds, else None."""
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def iso_z(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _num(v):
    if isinstance(v, bool) or v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def prov_tuple(rec: dict, default: dict | None) -> tuple | None:
    """(source, retrieved_at, mode) from the record's own provenance,
    else the envelope's; None when neither is complete."""
    for cand in (rec.get("provenance"), default):
        if isinstance(cand, dict) and cand.get("source") \
                and cand.get("retrieved_at") \
                and cand.get("mode") in ("LIVE", "SNAPSHOT", "MODELED"):
            return (str(cand["source"]), str(cand["retrieved_at"]),
                    str(cand["mode"]))
    return None


def _valid_pos(lat, lon):
    lat, lon = _num(lat), _num(lon)
    if lat is None or lon is None:
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None   # also rejects AIS "not available" 91 / 181
    return lat, lon


def is_aircraft_position(rec: dict) -> bool:
    if rec.get("record_type") in _AIRCRAFT_TYPES:
        return True
    return bool(rec.get("icao24")) and ("lat" in rec or "latitude" in rec)


def norm_vessel(rec: dict, default_prov: dict | None):
    mmsi = rec.get("mmsi")
    mmsi = str(mmsi).strip() if mmsi is not None else ""
    if not _MMSI_RE.match(mmsi):
        return None
    pos = _valid_pos(rec.get("lat"), rec.get("lon"))
    prov = prov_tuple(rec, default_prov)
    if pos is None or prov is None:
        return None
    t = _num(rec.get("reported_at_epoch"))
    if t is None:
        t = parse_iso(rec.get("reported_at")) or parse_iso(prov[1])
    if t is None:
        return None
    sog, cog = _num(rec.get("sog")), _num(rec.get("cog"))
    if sog is not None and (sog < 0 or sog >= 102.3):
        sog = None            # 102.3 kn = AIS "not available"
    if cog is not None and (cog < 0 or cog >= 360.0):
        cog = None            # 360 = AIS "not available"
    name = rec.get("ship_name")
    name = name.strip() if isinstance(name, str) and name.strip() else None
    attrs = {}
    if rec.get("nav_status") is not None:
        attrs["nav_status"] = rec["nav_status"]
    if rec.get("node_id"):
        attrs["node_id"] = rec["node_id"]
    if rec.get("watch_area"):
        attrs["watch_area"] = rec["watch_area"]
    return {"id": mmsi, "t": t, "lat": pos[0], "lon": pos[1], "spd": sog,
            "hdg": cog, "alt": None, "name": name, "attrs": attrs,
            "prov": prov}


_STATIC_INT = ("ship_type", "length_m", "beam_m", "imo")
_STATIC_STR = ("callsign", "destination")


def norm_vessel_static(rec: dict):
    """AIS static data (ShipStaticData / StaticDataReport / Extended
    Class B) -> {id, name, attrs}. Static data has no position and no
    provenance of its own: it only decorates a vessel the hub holds (or
    will hold) from a position report. Unknown / zero values are
    dropped, never defaulted."""
    mmsi = rec.get("mmsi")
    mmsi = str(mmsi).strip() if mmsi is not None else ""
    if not _MMSI_RE.match(mmsi):
        return None
    name = rec.get("ship_name")
    name = name.strip() if isinstance(name, str) and name.strip() else None
    attrs = {}
    for k in _STATIC_INT:
        v = rec.get(k)
        if isinstance(v, int) and not isinstance(v, bool) and v > 0:
            attrs[k] = v
    for k in _STATIC_STR:
        v = rec.get(k)
        if isinstance(v, str) and v.strip():
            attrs[k] = v.strip()[:24]
    d = _num(rec.get("draught_m"))
    if d is not None and d > 0:
        attrs["draught_m"] = round(d, 1)
    if not name and not attrs:
        return None
    return {"id": mmsi, "name": name, "attrs": attrs}


def norm_aircraft(rec: dict, default_prov: dict | None):
    icao = str(rec.get("icao24") or "").strip().lower()
    if not _ICAO_RE.match(icao):
        return None
    pos = _valid_pos(rec.get("lat", rec.get("latitude")),
                     rec.get("lon", rec.get("longitude")))
    prov = prov_tuple(rec, default_prov)
    if pos is None or prov is None:
        return None
    t = _num(rec.get("time_position"))
    if t is None:
        t = _num(rec.get("observed_at_epoch"))
    if t is None:
        t = _num(rec.get("last_contact"))
    if t is None:
        t = parse_iso(rec.get("observed_at")) or parse_iso(prov[1])
    if t is None:
        return None
    spd = _num(rec.get("spd_kn"))
    if spd is None:
        v = _num(rec.get("velocity"))      # OpenSky m/s
        spd = v * MS_TO_KN if v is not None else None
    hdg = _num(rec.get("true_track", rec.get("hdg")))
    if hdg is not None and (hdg < 0 or hdg >= 360.0):
        hdg = None
    alt = _num(rec.get("baro_altitude", rec.get("alt_m")))
    if alt is None:
        alt = _num(rec.get("geo_altitude"))     # OpenSky: baro may be null
    cs = rec.get("callsign")
    cs = cs.strip() if isinstance(cs, str) and cs.strip() else None
    attrs = {}
    if rec.get("on_ground") is not None:
        attrs["on_ground"] = bool(rec["on_ground"])
    if rec.get("origin_country"):
        attrs["origin_country"] = rec["origin_country"]
    cat = rec.get("category")
    if isinstance(cat, int) and not isinstance(cat, bool) and cat > 1:
        attrs["category"] = cat     # 0/1 = no information
    if rec.get("node_id"):
        attrs["node_id"] = rec["node_id"]
    return {"id": icao, "t": t, "lat": pos[0], "lon": pos[1],
            "spd": None if spd is None else round(spd, 1), "hdg": hdg,
            "alt": alt, "name": cs, "attrs": attrs, "prov": prov}


def norm_hub_count(rec: dict, default_prov: dict | None):
    node = rec.get("node_id")
    count = rec.get("aircraft_in_bbox")
    prov = prov_tuple(rec, default_prov)
    if not node or isinstance(count, bool) or not isinstance(count, int) \
            or prov is None:
        return None
    t = _num(rec.get("observed_at_epoch"))
    if t is None:
        t = parse_iso(prov[1])
    if t is None:
        return None
    return {"node_id": str(node), "count": count, "t": t, "prov": prov}


def norm_event(rec: dict, default_prov: dict | None):
    ev_id = rec.get("id")
    if not isinstance(ev_id, str) or not ev_id.startswith("evt_"):
        return None
    prov = prov_tuple(rec, default_prov)
    if prov is None and rec.get("source") and rec.get("retrieved_at") \
            and rec.get("mode") in ("LIVE", "SNAPSHOT", "MODELED"):
        prov = (str(rec["source"]), str(rec["retrieved_at"]),
                str(rec["mode"]))
    if prov is None:
        return None
    nws = rec.get("nws") if isinstance(rec.get("nws"), dict) else {}
    gd = rec.get("gdacs") if isinstance(rec.get("gdacs"), dict) else {}
    title = nws.get("event") or gd.get("name") or rec.get("title") \
        or rec.get("headline")
    if isinstance(title, str) and len(title) > 160:
        title = title[:157] + "..."
    t = parse_iso(rec.get("detected_at")) or parse_iso(prov[1]) or 0.0
    out = {"id": ev_id, "node_id": rec.get("node_id"),
           "kind": rec.get("kind"), "severity": rec.get("severity"),
           "status": rec.get("status"), "title": title,
           "detected_at": rec.get("detected_at"),
           "started_at": rec.get("started_at"),
           "expires_at": rec.get("expires_at"),
           "evidence_url": rec.get("evidence_url"), "t": t, "prov": prov}
    cit = derive_citation(rec)  # only when a human-readable page exists
    if cit:
        out["citation"] = cit
    return out


def norm_border(rec: dict, default_prov: dict | None):
    node = rec.get("node_id")
    prov = prov_tuple(rec, default_prov)
    if not node or prov is None:
        return None
    crossing = rec.get("crossing") or ""
    lanes = rec.get("lanes") or ""
    return {"key": f"{node}|{crossing}|{lanes}", "node_id": node,
            "port_name": rec.get("port_name"), "crossing": crossing or None,
            "lanes": lanes or None, "wait_min": rec.get("wait_min"),
            "fast_wait_min": rec.get("fast_wait_min"),
            "lanes_open": rec.get("lanes_open"),
            "observed": " ".join(
                x for x in (rec.get("date"), rec.get("time")) if x) or None,
            "prov": prov}


def norm_port(rec: dict, default_prov: dict | None):
    pid = rec.get("portid")
    prov = prov_tuple(rec, default_prov)
    if not pid or prov is None:
        return None
    return {"key": str(pid), "name": rec.get("portname"),
            "locode": rec.get("locode"), "node_id": rec.get("node_id"),
            "vessel_count_total": rec.get("vessel_count_total"),
            "vessel_count_container": rec.get("vessel_count_container"),
            "vessel_count_dry_bulk": rec.get("vessel_count_dry_bulk"),
            "vessel_count_general_cargo":
                rec.get("vessel_count_general_cargo"),
            "vessel_count_tanker": rec.get("vessel_count_tanker"),
            "prov": prov}
