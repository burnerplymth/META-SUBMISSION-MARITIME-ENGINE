#!/usr/bin/env python3
"""Build the bundled research cache under data/research/ (run ONCE, ONLINE).

    python scripts/build_research_cache.py [--tracks TRACKS.json] [--only EP_ID ...]

Reads the episode library (scripts/research_episode_spec.py), then for every episode place fetches,
through the repo's own history layer (server/history: cache-first, budgeted, provenance-stamped):

* IMF PortWatch daily rows (port calls / transits / capacity);
* Open-Meteo archive (wind max, gust max, precipitation) and the Open-Meteo Marine API (wave height);
* Sentinel-2 L2A and Sentinel-1 GRD acquisitions from the Element84 Earth Search STAC archive;
* EIA spot prices from the stored history cache where its windows overlap an episode.

Writes small gzip-compressed JSON (deterministic: mtime 0, sorted keys) plus a manifest with the
sha256 of every file. The server never fetches at request time in offline mode; it only reads these
files. Total size is asserted <= 8 MB.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "server"))
sys.path.insert(0, str(REPO / "scripts"))

from history.budget import RequestBudget  # noqa: E402
from history.service import HistoryService  # noqa: E402
from history.transport import HttpRequest, default_transport  # noqa: E402
from research_episode_spec import EPISODES  # noqa: E402
from research import datasets as ds  # noqa: E402

OUT = REPO / "data" / "research"
MAX_TOTAL_BYTES = 8 * 1024 * 1024
STAC = "https://earth-search.aws.element84.com/v1/search"
PC_STAC = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
MARINE ="https://marine-api.open-meteo.com/v1/marine"
PW_METRIC = {"portwatch_port_calls": "port_calls", "portwatch_port_calls_cargo": "port_calls_cargo",
             "portwatch_transits": "transits", "portwatch_transits_cargo": "transits_cargo",
             "portwatch_transit_capacity": "transit_capacity"}
OM_METRIC = {"weather_wind_max_kmh": "wind_speed_10m_max", "weather_gust_max_kmh": "wind_gusts_10m_max",
             "weather_precip_mm": "precipitation_sum"}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def day(s: str) -> date:
    return date.fromisoformat(s)


def aligned(rows: list[dict], start: date, end: date) -> list:
    by = {r["date"]: r["value"] for r in rows}
    out, d = [], start
    while d <= end:
        out.append(by.get(d.isoformat()))
        d += timedelta(days=1)
    return out


def write_gz(path: Path, obj) -> None:
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    with open(path, "wb") as fh:
        with gzip.GzipFile(filename="", mode="wb", fileobj=fh, mtime=0, compresslevel=9) as gz:
            gz.write(raw)


def stac_scenes(svc: HistoryService, lat: float, lon: float, start: str, end: str, sensor: str):
    half = 0.04
    bbox = [round(lon - half, 4), round(lat - half, 4), round(lon + half, 4), round(lat + half, 4)]
    # Sentinel-2 ids come from Planetary Computer (the ids its titiler crops by, so a recorded or live
    # SAM run can fetch the same scene); Sentinel-1 ids from Earth Search, as satwatch/multistac does.
    coll = "sentinel-2-l2a" if sensor == "sentinel-2" else "sentinel-1-grd"
    url = PC_STAC if sensor == "sentinel-2" else STAC
    body = {"collections": [coll], "bbox": bbox, "limit": 200,
            "datetime": f"{start}T00:00:00Z/{end}T23:59:59Z",
            "sortby": [{"field": "properties.datetime", "direction": "asc"}]}
    if sensor == "sentinel-1":
        body["query"] = {"sar:instrument_mode": {"eq": "IW"}}
    import urllib.request
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": "SupermAP-research-build/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        doc = json.loads(resp.read().decode("utf-8"))
    from satwatch import multistac
    out = []
    for feat in doc.get("features", []):
        if not multistac.covering(multistac.parse_scenes(sensor, {"features": [feat]}), bbox):
            continue
        p = feat.get("properties") or {}
        dt = p.get("datetime") or ""
        rec = {"scene_id": feat["id"], "t": dt[:19] + "Z", "date": dt[:10], "source": sensor,
               "platform": p.get("platform"), "cloud_pct": p.get("eo:cloud_cover"),
               "nodata_pct": p.get("s2:nodata_pixel_percentage")}
        if sensor == "sentinel-2":
            rec["tile"] = p.get("grid:code")
        else:
            rec["orbit_state"] = p.get("sat:orbit_state")
            rec["relative_orbit"] = p.get("sat:relative_orbit")
            rec["polarizations"] = p.get("sar:polarizations")
        out.append(rec)
    return {"bbox": bbox, "scenes": out, "retrieved_at": now_iso()}


def marine_wave(svc: HistoryService, lat: float, lon: float, start: date, end: date):
    prov = svc.openmeteo
    req = HttpRequest("GET", MARINE, params={
        "latitude": f"{lat:.3f}", "longitude": f"{lon:.3f}", "start_date": start.isoformat(),
        "end_date": end.isoformat(), "daily": "wave_height_max", "timezone": "GMT"})
    doc = prov.json_body(prov.http(req), "Open-Meteo marine")
    t = doc["daily"]["time"]
    v = doc["daily"]["wave_height_max"]
    return [{"date": a, "value": b} for a, b in zip(t, v) if b is not None]


def eia_rows(repo_cache: Path, sid: str):
    """All cached EIA rows for a series id, merged (later files win)."""
    rows = {}
    for p in sorted((repo_cache / "eia").glob(f"spot_{sid}__*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        for r in d["payload"]["rows"]:
            rows[r["period"]] = r["value"]
        retrieved = d["provenance"]["retrieved_at"]
    return rows, (retrieved if rows else None)


def build_tracks_doc(args) -> dict:
    tracks_doc = {"schema": "supermap.research.tracks.v1", "episodes": {}}
    if args.tracks:
        t = json.loads(Path(args.tracks).read_text(encoding="utf-8"))
        tracks_doc["episodes"][args.tracks_episode] = {
            "place": t["place"], "date": args.tracks_date, "bbox": t["bbox"], "step_min": t["step_min"],
            "vessel_filter": t["vessel_filter"], "rows_scanned": t["rows_scanned"], "tracks": t["tracks"],
            "provenance": {"source": ds.TRACKS["source"], "mode": "SNAPSHOT",
                           "retrieved_at": now_iso(),
                           "origin_file": f"AIS_{args.tracks_date.replace('-', '_')}.zip",
                           "origin_url": "https://coast.noaa.gov/htdata/CMSP/AISDataHandler/2021/AIS_2021_11_10.zip"}}
    return tracks_doc


def retry_obs(fn, tries: int = 5):
    """Call a provider ``observations`` function again when the upstream is flaky (HTTP 400 'Invalid URL',
    TLS EOF, resets and timeouts all pass in a minute; a real 'no data' answer is not retried)."""
    import time
    r = None
    for i in range(tries):
        try:
            r = fn()
        except Exception as exc:  # noqa: BLE001
            r = {"served": False, "reason": "unreachable", "detail": type(exc).__name__}
        if r.get("served") or r.get("reason") not in ("bad_payload", "unreachable"):
            return r
        time.sleep(4 * (i + 1))
    return r


def retry_call(fn, tries: int = 3, wait: float = 3.0):
    import time
    for i in range(tries):
        try:
            return fn()
        except Exception:  # noqa: BLE001
            if i == tries - 1:
                raise
            time.sleep(wait * (i + 1))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache-dir", help="persistent history-layer cache (outside the repo); makes re-runs cheap")
    ap.add_argument("--tracks", help="JSON written by research_ais_extract.py")
    ap.add_argument("--tracks-episode", default="la_lb_congestion_2021")
    ap.add_argument("--tracks-date", default="2021-11-10")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--no-scenes", action="store_true")
    ap.add_argument("--tracks-only", action="store_true",
                    help="only (re)write tracks.json.gz from --tracks, mark the episode, rewrite the manifest")
    ap.add_argument("--manifest-only", action="store_true",
                    help="rewrite manifest.json for the files now on disk (after adding a recorded file)")
    args = ap.parse_args()
    if args.manifest_only:
        write_manifest()
        return
    if args.tracks_only:
        doc = build_tracks_doc(args)
        write_gz(OUT / "tracks.json.gz", doc)
        ep_path = OUT / "episodes.json"
        eps = json.loads(ep_path.read_text(encoding="utf-8"))
        for e in eps["episodes"]:
            e["has_tracks"] = e["id"] in doc["episodes"]
        ep_path.write_text(json.dumps(eps, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        write_manifest()
        return
    if os.environ.get("SUPERMAP_BLOCK_NETWORK") == "1":
        sys.exit("SUPERMAP_BLOCK_NETWORK=1: this script needs the network")

    tmp = Path(args.cache_dir) if args.cache_dir else Path(tempfile.mkdtemp(prefix="research_build_"))
    tmp.mkdir(parents=True, exist_ok=True)
    caps = {k: 10000 for k in ("imf_portwatch", "open_meteo_archive", "gdacs", "eia", "un_comtrade",
                              "gdelt", "opensky")}
    svc = HistoryService(data_dir=tmp, graph_path=REPO / "data" / "graph" / "graph.json",
                         transport=default_transport, fetch_enabled=True,
                         budget=RequestBudget(None, caps=caps), repo_data_dir=REPO / "data")
    book = svc.book()
    series: dict[str, dict] = {}       # dataset -> {"places": {place: [segments]}}
    scenes: dict[str, dict] = {}
    notes: list[str] = []

    def put(dsid: str, place: str, start: date, end: date, rows, retrieved_at: str):
        if not rows:
            return
        vals = aligned(rows, start, end)
        if all(v is None for v in vals):
            return
        d = series.setdefault(dsid, {"places": {}})
        d["places"].setdefault(place, []).append(
            {"from": start.isoformat(), "values": vals, "retrieved_at": retrieved_at})

    episodes_out = []
    for ep in EPISODES:
        if args.only and ep["id"] not in args.only:
            continue
        a, b = day(ep["fetch"]["from"]), day(ep["fetch"]["to"])
        print(f"== {ep['id']} {a}..{b}", flush=True)
        for place in ep["places"]:
            if place not in book:
                notes.append(f"{ep['id']}: {place} not a graph place")
                continue
            node = book.nodes[place]
            lat, lon = float(node["lat"]), float(node["lon"])
            spec = book.portwatch_series(place)
            if spec is None:
                notes.append(f"{ep['id']}: no PortWatch series for {place}")
            else:
                layer = spec["layer"]
                for dsid, metric in PW_METRIC.items():
                    if (ds.SERIES[dsid]["applies_to"] != layer):
                        continue
                    r = retry_obs(lambda: svc.portwatch.observations(place, metric, a, b))
                    if r.get("served"):
                        put(dsid, place, a, b, r["series"], r["provenance"]["retrieved_at"])
                    else:
                        notes.append(f"{ep['id']} {place} {dsid}: {r.get('reason')} {r.get('detail')}")
            for dsid, metric in OM_METRIC.items():
                r = retry_obs(lambda: svc.openmeteo.observations(place, metric, a, b))
                if r.get("served"):
                    put(dsid, place, a, b, r["series"], r["provenance"]["retrieved_at"])
                else:
                    notes.append(f"{ep['id']} {place} {dsid}: {r.get('reason')} {r.get('detail')}")
            try:
                put("weather_wave_height_m", place, a, b, retry_call(lambda: marine_wave(svc, lat, lon, a, b), tries=2, wait=2.0), now_iso())
            except Exception as exc:  # noqa: BLE001 - recorded, not hidden
                notes.append(f"{ep['id']} {place} wave: {type(exc).__name__} {str(exc)[:80]}")
            if not args.no_scenes:
                for sensor in ("sentinel-2", "sentinel-1"):
                    try:
                        sc = retry_call(lambda: stac_scenes(svc, lat, lon, a.isoformat(), b.isoformat(), sensor))
                        slot = scenes.setdefault(place, {})
                        slot.setdefault(sensor, {"bbox": sc["bbox"], "scenes": [],
                                                 "retrieved_at": sc["retrieved_at"]})
                        have = {s["scene_id"] for s in slot[sensor]["scenes"]}
                        slot[sensor]["scenes"] += [s for s in sc["scenes"] if s["scene_id"] not in have]
                        print(f"   {place} {sensor}: {len(sc['scenes'])} scenes", flush=True)
                    except Exception as exc:  # noqa: BLE001
                        notes.append(f"{ep['id']} {place} scenes {sensor}: {type(exc).__name__} {str(exc)[:80]}")
        # EIA spot prices from the stored cache, clipped to the episode fetch window
        for dsid, sid in (("economic_eia_spot_brent", "RBRTE"), ("economic_eia_spot_wti", "RWTC")):
            rows, ret = eia_rows(REPO / "data" / "history_cache", sid)
            sel = [{"date": k, "value": v} for k, v in sorted(rows.items())
                   if ep["fetch"]["from"] <= k <= ep["fetch"]["to"]]
            if sel:
                s0, s1 = day(sel[0]["date"]), day(sel[-1]["date"])
                put(dsid, "global", s0, s1, sel, ret)

    # sort + merge segments, drop scene ordering noise
    for d in series.values():
        for place, segs in d["places"].items():
            segs.sort(key=lambda s: s["from"])
    for place in scenes:
        for sensor in scenes[place]:
            scenes[place][sensor]["scenes"].sort(key=lambda s: (s["t"], s["scene_id"]))

    OUT.mkdir(parents=True, exist_ok=True)
    series_doc = {"schema": "supermap.research.series.v1", "datasets": series}
    write_gz(OUT / "series.json.gz", series_doc)
    write_gz(OUT / "scenes.json.gz", {"schema": "supermap.research.scenes.v1", "places": scenes})

    tracks_doc = build_tracks_doc(args)
    write_gz(OUT / "tracks.json.gz", tracks_doc)

    # episodes.json: the spec resolved against the actuals files + computed coverage
    sys.path.insert(0, str(REPO / "scripts"))
    from research_resolve import resolve_episode  # noqa: E402
    for ep in EPISODES:
        if args.only and ep["id"] not in args.only:
            continue
        r = resolve_episode(ep, REPO)
        r["series"] = sorted(k for k, v in series.items()
                             if any(p in v["places"] for p in ep["places"]) or
                             (k.startswith("economic_") and "global" in v["places"]))
        r["scenes"] = {s: sum(len([x for x in scenes.get(p, {}).get(s, {}).get("scenes", [])
                                   if ep["fetch"]["from"] <= x["date"] <= ep["fetch"]["to"]])
                              for p in ep["places"]) for s in ("sentinel-2", "sentinel-1")}
        r["has_tracks"] = ep["id"] in tracks_doc["episodes"]
        episodes_out.append(r)
    (OUT / "episodes.json").write_text(json.dumps(
        {"schema": "supermap.research.episodes.v1", "episodes": episodes_out}, indent=1,
        sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")

    write_manifest(notes)


def write_manifest(notes: list[str] | None = None) -> None:
    files = {}
    for name in ("episodes.json", "series.json.gz", "scenes.json.gz", "tracks.json.gz",
                 "sam_recorded.json"):
        if (OUT / name).exists():
            data = (OUT / name).read_bytes()
            files[name] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    total = sum(f["bytes"] for f in files.values())
    old = {}
    if (OUT / "manifest.json").exists():
        old = json.loads((OUT / "manifest.json").read_text(encoding="utf-8"))
    manifest = {"schema": "supermap.research.manifest.v1",
                "built_at": old.get("built_at") if notes is None and old else now_iso(),
                "files": files, "total_bytes": total, "max_total_bytes": MAX_TOTAL_BYTES,
                "build_notes": notes if notes is not None else old.get("build_notes", [])}
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n",
                                       encoding="utf-8")
    print(f"manifest: {len(files)} files, {total} bytes")
    for n in manifest["build_notes"]:
        print("  note:", n)
    if total > MAX_TOTAL_BYTES:
        sys.exit(f"bundle is {total} bytes, over the {MAX_TOTAL_BYTES} cap")


if __name__ == "__main__":
    main()
