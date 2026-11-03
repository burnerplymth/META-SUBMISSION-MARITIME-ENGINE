"""Sentinel-2 true-colour underlay for the Port Twin.

For each port rectangle: STAC search (Planetary Computer, Earth Search
failover) over the last ~6 months, footprint-coverage + SCL cloud
screening reusing server/satwatch/stac.py, then ONE true-colour crop
saved as a small palette PNG with its bounds, scene id and date.

HONESTY: Sentinel-2 is 10 m per pixel. The crop shows basins, terminal
land cover, breakwaters and large structures. It does NOT resolve ships
or berth slots, and says so in its metadata.  Network code: only used by
scripts/build_port_geo.py imagery.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import geo_catalog

try:                                   # dual import convention of the repo
    from satwatch import stac
except ImportError:                    # pragma: no cover
    from server.satwatch import stac   # type: ignore

UA = ("supermap-port-twin/1.0 (SupermAP; Sentinel-2 "
      "port underlay build; low volume)")
SCHEMA = "port-imagery/1"
WINDOW_DAYS = 183
MAX_SCL_CHECKS = 8
AOI_CLOUD_MAX = 8.0          # percent of the port rectangle (stricter than satwatch 35)
NODATA_MAX = 1.0
MAX_EDGE_PX = 900
SCL_PX = 128
NOTE = ("Sentinel-2 L2A true colour at 10 m per pixel. It shows basins, "
        "terminals and land cover; it cannot resolve ships, containers or "
        "berth slots. Scene date is the acquisition date, not today.")
ATTRIBUTION = "Contains modified Copernicus Sentinel data, processed via Microsoft Planetary Computer"


def http(method, url, body=None, timeout=90):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"User-Agent": UA}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as exc:
            if exc.code < 500 and exc.code != 429:
                return exc.code, dict(exc.headers or {}), exc.read()
            last = exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
        time.sleep(5 * (attempt + 1))
    return 599, {}, str(last).encode()


def crop_size(bbox) -> tuple[int, int]:
    lat0 = (bbox[1] + bbox[3]) / 2
    dx = (bbox[2] - bbox[0]) * 111320.0 * math.cos(math.radians(lat0))
    dy = (bbox[3] - bbox[1]) * 110574.0
    w = int(min(MAX_EDGE_PX, max(64, round(dx / 10.0))))
    h = int(max(32, round(w * dy / dx)))
    if h > MAX_EDGE_PX:
        w, h = int(round(w * MAX_EDGE_PX / h)), MAX_EDGE_PX
    return w, h


def to_palette_png(raw: bytes, colors: int = 128) -> bytes:
    from PIL import Image
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    q = img.quantize(colors=colors, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    buf = io.BytesIO()
    q.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def pick_scene(bbox, today=None, call=http):
    """-> (scene_dict, screen_dict, evaluated_list) or (None, None, reason)."""
    today = today or datetime.now(timezone.utc).date()
    start = today - timedelta(days=WINDOW_DAYS)
    fc, _cat, _st = stac.query_stac(bbox, start, today, http=call)
    scenes = stac.parse_items(fc)
    cover = [s for s in scenes if s.get("footprint_bbox")
             and stac.coverage_fraction(s["footprint_bbox"], bbox) >= 0.98]
    cover = stac.dedupe_by_date(cover)
    if not cover:
        return None, None, f"no Sentinel-2 L2A scene covers the rectangle in {start}..{today} ({len(scenes)} scenes intersect)"
    # The tile-level cloud figure overstates AOI cloud (see satwatch/stac.py)
    # but orders scenes well enough: screen the MAX_SCL_CHECKS least cloudy
    # covering scenes at AOI level, then take the most recent clear one.
    ranked = sorted(cover, key=lambda s: (s["cloud_pct_tile"]
                                          if s["cloud_pct_tile"] is not None else 999.0,
                                          s["id"]))[:MAX_SCL_CHECKS]
    evaluated, passing = [], []
    for s in ranked:
        try:
            raw, _ = stac.fetch_crop(bbox, s["id"], asset="SCL", width=SCL_PX,
                                     height=SCL_PX, http=call)
            scr = stac.scl_cloud_from_png(raw)
        except Exception as exc:       # noqa: BLE001 - recorded, not hidden
            evaluated.append({"id": s["id"], "date": s["date"], "error": str(exc)[:120]})
            continue
        evaluated.append({"id": s["id"], "date": s["date"],
                          "cloud_pct_aoi": scr["cloud_pct_aoi"],
                          "nodata_pct_aoi": scr["nodata_pct_aoi"]})
        if scr["cloud_pct_aoi"] <= AOI_CLOUD_MAX and scr["nodata_pct_aoi"] <= NODATA_MAX:
            passing.append((s, scr))
    if not passing:
        return None, None, ("no clear scene (cloud <= %.0f%% over the rectangle) among the %d "
                            "least cloudy of %d covering scenes" % (AOI_CLOUD_MAX, len(ranked), len(cover)))
    passing.sort(key=lambda t: t[0]["datetime"], reverse=True)
    return passing[0][0], passing[0][1], evaluated


def build_port_imagery(place_id, out_dir: Path, today=None, call=http, log=print):
    bbox = geo_catalog.PORTS[place_id]["bbox"]
    scene, scr, ev = pick_scene(bbox, today, call)
    if scene is None:
        log(f"{place_id}: SKIPPED - {ev}")
        return {"served": False, "reason": ev}
    w, h = crop_size(bbox)
    raw, _ = stac.fetch_crop(bbox, scene["id"], asset="visual", width=w, height=h, http=call)
    png = to_palette_png(raw)
    img_dir = Path(out_dir) / "imagery"
    img_dir.mkdir(parents=True, exist_ok=True)
    (img_dir / f"{place_id}.png").write_bytes(png)
    meta = {
        "schema": SCHEMA, "place_id": place_id, "basis": "SOURCED",
        "label": "Sentinel-2 true colour underlay (SOURCED)",
        "bounds": bbox, "width_px": w, "height_px": h,
        "resolution_m": 10,
        "scene_id": scene["id"], "scene_datetime": scene["datetime"],
        "scene_date": scene["date"], "platform": scene.get("platform"),
        "cloud_pct_tile": scene.get("cloud_pct_tile"),
        "cloud_pct_aoi": scr["cloud_pct_aoi"],
        "source": stac.SOURCE_LABEL, "attribution": ATTRIBUTION,
        "note": NOTE,
        "bytes": len(png), "sha256": hashlib.sha256(png).hexdigest(),
        "candidates_screened": ev,
    }
    (img_dir / f"{place_id}.json").write_bytes(
        json.dumps(meta, sort_keys=True, indent=1).encode() + b"\n")
    log(f"{place_id}: {scene['date']} cloud {scr['cloud_pct_aoi']}% {len(png)//1024} KB {w}x{h}")
    return {"served": True, "scene_id": scene["id"], "scene_date": scene["date"],
            "cloud_pct_aoi": scr["cloud_pct_aoi"], "bytes": len(png)}


def build_imagery(out_dir: Path, ids, log=print):
    out_dir = Path(out_dir)
    idx_path = out_dir / "imagery" / "index.json"
    idx = json.loads(idx_path.read_text()) if idx_path.exists() else {
        "schema": "port-imagery-index/1", "ports": {}}
    for pid in ids:
        if idx["ports"].get(pid, {}).get("served"):
            log(f"{pid}: cached")
            continue
        try:
            idx["ports"][pid] = build_port_imagery(pid, out_dir, log=log)
        except Exception as exc:       # noqa: BLE001
            log(f"{pid}: FAILED {exc}")
            idx["ports"][pid] = {"served": False, "reason": f"fetch failed: {str(exc)[:150]}"}
        idx["ports"] = {k: idx["ports"][k] for k in sorted(idx["ports"])}
        idx_path.parent.mkdir(parents=True, exist_ok=True)
        idx_path.write_bytes(json.dumps(idx, sort_keys=True, indent=1).encode() + b"\n")
        time.sleep(1.0)
    return idx
