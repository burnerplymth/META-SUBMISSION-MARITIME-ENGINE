"""GET /research/catalog and GET /research/episodes."""
from __future__ import annotations

from . import datasets as D
from .assemble import fetch_allowed
from .util import EXCLUDED_CATEGORIES, LABEL_ASSEMBLE, LIBRARY_CATEGORIES

LIVE_CAPABLE = {"portwatch_port_calls", "portwatch_port_calls_cargo", "portwatch_transits",
                "portwatch_transits_cargo", "portwatch_transit_capacity", "weather_wind_max_kmh",
                "weather_gust_max_kmh", "weather_precip_mm"}


def _entry(did, d, state, coverage, places, **extra):
    return {"id": did, "title": d["title"], "kind": d["kind"], "unit": d.get("unit"), "coverage": coverage,
            "resolution": d["resolution"], "source": d["source"], "licence": d["licence"],
            "attribution": d["attribution"], "state": state, "places": places, **extra}


def build_catalog(bundle, history_available: bool) -> dict:
    online = fetch_allowed() and history_available
    out = []
    for did, d in D.SERIES.items():
        cov = bundle.series_coverage(did) if bundle.integrity_ok("series.json.gz") else None
        places = bundle.series_places(did) if cov else []
        if online and did in LIVE_CAPABLE:
            out.append(_entry(did, d, "live", cov, "any"))
        elif cov:
            out.append(_entry(did, d, "cached", cov, places))
        else:
            out.append(_entry(did, d, "not_served", None, [], reason="not in the bundled cache"))
    eps = bundle.episodes()
    ev_places = sorted({p for e in eps for p in e["places"]})
    ev_cov = None
    if eps:
        ts = [x["t"][:10] for e in eps for x in e["events"]]
        ev_cov = {"from": min(ts), "to": max(ts)} if ts else None
    out.append(_entry("events_curated", D.EVENTS_CURATED, "cached" if eps else "not_served", ev_cov, ev_places))
    g = D.NOT_SERVED["events_gdelt"]
    out.append(_entry("events_gdelt", g, "not_served", None, [], reason=g["reason"]))
    for did, d in D.SCENES.items():
        sensor = d["sensor"]
        scenes_ok = bundle.integrity_ok("scenes.json.gz")
        places, lo, hi = [], None, None
        if scenes_ok:
            for p, sensors in (bundle._safe("scenes.json.gz", {}).get("places", {})).items():
                blk = sensors.get(sensor)
                if blk and blk["scenes"]:
                    places.append(p)
                    a, b = blk["scenes"][0]["date"], blk["scenes"][-1]["date"]
                    lo, hi = (a if lo is None or a < lo else lo), (b if hi is None or b > hi else hi)
        out.append(_entry(did, d, "cached" if places else "not_served",
                          {"from": lo, "to": hi} if places else None, sorted(places)))
    info = bundle.tracks_info()
    t = D.TRACKS
    if info:
        ds = sorted(v["date"] for v in info.values())
        out.append(_entry(t["id"], t, "cached", {"from": ds[0], "to": ds[-1]},
                          sorted({v["place"] for v in info.values()}),
                          note="a small sample for one non-political episode; elsewhere No open track data for this window"))
    else:
        out.append(_entry(t["id"], t, "not_served", None, [], reason="No open track data for this window"))
    for did, d in D.NOT_SERVED.items():
        if d["kind"] == "economic":
            out.append(_entry(did, d, "not_served", None, [], reason=d["reason"]))
    return {"label": LABEL_ASSEMBLE, "fetch_mode": "online (cache first, budgeted)" if online
            else "offline (bundled cache only)",
            "event_categories": {"library": list(LIBRARY_CATEGORIES), "excluded": list(EXCLUDED_CATEGORIES),
                                 "excluded_reason": "content scope rule: no war or political content"},
            "bundle": {"verified": bundle.verify_all()["ok"] if bundle.available() else False},
            "datasets": out}


def build_episodes(bundle) -> dict:
    eps = []
    for e in bundle.episodes():
        if any(c in EXCLUDED_CATEGORIES for c in e["categories"]):
            continue
        eps.append({k: e[k] for k in ("id", "title", "places", "window", "categories", "events", "series",
                                       "scenes", "has_tracks", "sim_runs", "notes_chip")})
    return {"label": LABEL_ASSEMBLE, "episodes": eps}
