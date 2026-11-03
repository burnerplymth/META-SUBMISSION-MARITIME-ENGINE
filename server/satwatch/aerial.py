"""Aerial (sub-metre) scenes and the SAM 3.1 tile pipeline.

Why this exists, in one line: SAM 3.1 returned nothing on every 10 m
Sentinel-2 ship crop and on a 1.3 m NAIP crop with three small boats, but it
found 3 of 3 berthed container ships and 5 cranes on a 0.6 m NAIP tile. A ship
is a few pixels at 10 m and hundreds of pixels at 0.25 to 0.6 m, so the
product path for SAM is aerial survey imagery of ports.

What this module does (pure logic; HTTP is ``aerial_routes.py``):

* ``plan_scene`` / ``fetch_scene``: a scene is ONE axis-aligned window of an
  aerial survey (NAIP through the Planetary Computer titiler, or a WMS of a
  national open ortho), requested at exactly the source's declared ground
  sampling distance. Pixels are stored as the service returned them (no
  conversion, no resampling); no-data (black or transparent) is measured.
* ``plan_tiles``: the scene is cut into integer-pixel tiles of at most
  ``TILE_PX`` with ``OVERLAP_PX`` overlap. A tile that is almost all no-data
  is skipped (no call, no cost). A one-tile scene sends the stored bytes
  unchanged.
* one SAM call per (tile, phrase); replies are parsed by ``sam31``; every
  object becomes scene-pixel geometry, a metres length from the ground
  sampling distance (from the mask's oriented extent when there is a mask),
  and the objects of all tiles are merged: duplicates in the overlap are
  dropped, and an object cut by a tile border is stitched or replaced by the
  complete one.
* results say "SAM found N". They are MODELLED detections, display-only,
  never a census, and never feed signals. GeoJSON and CSV exports carry the
  same objects with the scene's georeference.

Native pixels only: nothing here resamples an image that is sent to SAM.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
import time
from pathlib import Path
from typing import Callable, Optional

from . import aerial_geo as geo
from . import multistac as ms
from . import overlay as overlay_mod
from . import sam31 as sam31_mod
from . import sam_meter as sam_meter_mod
from . import sources as src_mod
from .stac import StacError, default_http

CONTRACT = "satwatch-aerial/1"
SCENE_CONTRACT = "satwatch-aerial-scene/1"
LABEL_MODELLED = "MODELLED"
FRAMING = "Detections, not a census"
VERIFICATION = "Detected, not verified"      # every count wears this until a human reviews that place (FIX-AISAT rule)
IMAGE_BASIS = "native survey pixels as served (no resampling, no enhancement)"
AERIAL_DIR = "aerial"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "aerial"
SCENE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_]{2,63}$")

TILE_PX = 1008
OVERLAP_PX = 128
MAX_TILES = 9
MAX_PHRASES = 3
NODATA_SKIP_PCT = 95.0
# Phrases offered. "quay" and "boat" found nothing in experiments and are
# not offered.
PHRASES = {
    "ship": "Ship",
    "container ship": "Container ship",
    "vessel": "Vessel",
    "crane": "Crane",
    "container stack": "Container stack",
}
DEFAULT_PHRASES = ("ship", "crane")
VESSEL_PHRASES = ("ship", "container ship", "vessel")
PRICE_PER_IMAGE_USD = sam_meter_mod.PRICE_PER_IMAGE_USD
MIN_COUNTABLE_LONG_SIDE_PX = 15

# Curated port windows: the only places an admin can fetch (no free-text
# coordinates reach an upstream service). ``size_px`` is the requested window;
# NAIP presets may pin ``bbox_ll`` instead. ``desk_place`` is the satellite desk anchorage the scene
# belongs to (None: a port the desk does not offer as an anchorage).
AERIAL_PLACES: list[dict] = [
    {"id": "lalb_pier_t", "name": "Long Beach container terminal", "source": "naip",
     "bbox_ll": [-118.2497, 33.7301, -118.2432, 33.7355], "desk_place": "sea_longbeach"},
    {"id": "rotterdam_maasvlakte", "name": "Rotterdam Maasvlakte container berths", "source": "pdok-nl",
     "lon": 4.0495, "lat": 51.9475, "size_px": [2016, 1008], "desk_place": "sea_rotterdam"},
    {"id": "valencia_container", "name": "Valencia container berths", "source": "pnoa-es",
     "lon": -0.3262, "lat": 39.4352, "size_px": [1008, 1008]},
]
PLACE_BY_ID = {p["id"]: p for p in AERIAL_PLACES}


class AerialError(Exception):
    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code, self.status = code, status


# ---------------------------------------------------------------------------
# Scene geometry and fetch
# ---------------------------------------------------------------------------

def _epsg_for(src: dict, lon: float) -> int:
    if src.get("epsg_fixed"):
        return int(src["epsg_fixed"])
    by = src.get("epsg_by_lon") or {}
    return int(by["lt0"] if lon < 0 else by["ge0"])


def _zone_of_epsg(epsg: int) -> int:
    if 25828 <= epsg <= 25838:
        return epsg - 25800
    if 32601 <= epsg <= 32660:
        return epsg - 32600
    raise AerialError("bad_crs", f"EPSG:{epsg} is not a UTM zone")


def plan_scene(place: dict) -> dict:
    """The request geometry for one curated place: window, size, georef.
    Pure; raises AerialError when the window breaks the size caps."""
    src = src_mod.get_aerial_source(place["source"])
    gsd = float(place.get("gsd_m") or src["gsd_m"])
    if src["access"] == "naip_titiler":
        bbox = place.get("bbox_ll")
        if bbox is None:
            lon, lat = place["lon"], place["lat"]
            w, h = place["size_px"]
            mx, my = geo.metres_per_degree(lat)
            bbox = [lon - w * gsd / 2 / mx, lat - h * gsd / 2 / my, lon + w * gsd / 2 / mx, lat + h * gsd / 2 / my]
        w, h, _eff = ms.crop_size_px(bbox, gsd, max_px=10 ** 6)
        georef = {"kind": "ll", "bbox": [round(v, 7) for v in bbox]}
        out = {"epsg": 4326, "bbox_req": bbox, "bbox_ll": bbox}
    else:
        lon, lat = place["lon"], place["lat"]
        w, h = place["size_px"]
        epsg = _epsg_for(src, lon)
        zone = _zone_of_epsg(epsg)
        e, n = geo.utm_forward(lon, lat, zone)
        e0, n1 = e - w * gsd / 2, n + h * gsd / 2
        bbox_utm = [e0, n1 - h * gsd, e0 + w * gsd, n1]
        corners = [geo.utm_inverse(x, y, zone) for x in (bbox_utm[0], bbox_utm[2]) for y in (bbox_utm[1], bbox_utm[3])]
        bbox_ll = [min(c[0] for c in corners), min(c[1] for c in corners), max(c[0] for c in corners), max(c[1] for c in corners)]
        georef = {"kind": "utm", "zone": zone, "epsg": epsg, "e0": round(e0, 3), "n1": round(n1, 3), "gsd_m": gsd}
        out = {"epsg": epsg, "bbox_req": bbox_utm, "bbox_ll": bbox_ll}
    if max(w, h) > src_mod.AERIAL_MAX_SIDE_PX or w * h > src_mod.AERIAL_MAX_SCENE_PX:
        raise AerialError("scene_too_large", f"window {w}x{h} px exceeds the per-request cap "
                          f"({src_mod.AERIAL_MAX_SIDE_PX} px a side)", 422)
    if w < 64 or h < 64:
        raise AerialError("scene_too_small", "window under 64 px", 422)
    out.update(size_px=[int(w), int(h)], gsd_m=gsd, georef=georef, source=src["id"])
    return out


def wms_url(src: dict, epsg: int, bbox: list, w: int, h: int) -> str:
    from urllib.parse import urlencode
    q = urlencode({
        "service": "WMS", "version": "1.3.0", "request": "GetMap", "layers": src["layer"], "styles": "",
        "crs": f"EPSG:{epsg}", "bbox": ",".join(f"{v:.3f}" for v in bbox), "width": w, "height": h,
        "format": src["format"], "transparent": "false",
    })
    return f"{src['wms_url']}?{q}"


def badge(year: Optional[int], gsd: float) -> str:
    g = f"{gsd:g}"
    return f"Aerial {g} m, {year}" if year else f"Aerial {g} m"


def acquisition_label(year: Optional[int]) -> str:
    return f"Aerial survey {year}" if year else "Aerial mosaic, year varies"


def nodata_pct(raw: bytes) -> float:
    """Percent of pixels that are no-data: transparent, or pure black in all
    three bands (an aerial tile edge). Measured, never guessed."""
    from PIL import Image, ImageChops
    im = Image.open(io.BytesIO(raw))
    im.load()
    bands = im.convert("RGBA").split()
    dark = ImageChops.lighter(ImageChops.lighter(bands[0], bands[1]), bands[2])
    nd = ImageChops.darker(dark, bands[3])
    total = im.size[0] * im.size[1]
    return round(100.0 * nd.histogram()[0] / total, 2) if total else 100.0


def fetch_scene(place_id: str, *, http: Callable = default_http) -> tuple[bytes, dict]:
    """Fetch one curated window. Returns (image bytes as served, scene meta).
    Raises AerialError('feed_down' ...) carrying the upstream status."""
    place = PLACE_BY_ID.get(place_id)
    if place is None:
        raise AerialError("unknown_place", f"place must be one of {sorted(PLACE_BY_ID)}", 422)
    src = src_mod.get_aerial_source(place["source"])
    plan = plan_scene(place)
    w, h = plan["size_px"]
    year = src["year"]
    item = None
    if src["access"] == "naip_titiler":
        bb = plan["bbox_ll"]
        cx, cy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
        try:
            found = ms.covering(ms.search("naip", [cx - 1e-4, cy - 1e-4, cx + 1e-4, cy + 1e-4], days=None, http=http), [cx, cy, cx, cy])
        except StacError as exc:
            raise AerialError("feed_down", f"NAIP catalogue not served: {exc}", 502)
        if not found:
            raise AerialError("no_scene", "no NAIP item covers this window", 404)
        item = found[0]
        year = int(item["date"][:4]) if item.get("date") else None
        url = ms.titiler_url("naip", bb, item["id"], w, h)
    else:
        url = wms_url(src, plan["epsg"], plan["bbox_req"], w, h)
    status, headers, raw = http("GET", url, timeout=120)
    ctype = (headers or {}).get("Content-Type", (headers or {}).get("content-type", ""))
    if status != 200 or "image" not in ctype:
        raise AerialError("feed_down", f"{src['id']} imagery not served: HTTP {status}", 502)
    nd = nodata_pct(raw)
    ext = "jpg" if "jpeg" in ctype else "png"
    meta = {
        "contract": SCENE_CONTRACT, "scene_id": place_id, "name": place["name"], "source": src["id"],
        "source_label": src["label"], "gsd_m": plan["gsd_m"], "size_px": [w, h],
        "year": year, "acquisition_label": acquisition_label(year), "year_basis": src["year_basis"],
        "badge": badge(year, plan["gsd_m"]), "mode": "SNAPSHOT",
        "georef": plan["georef"], "bbox_ll": [round(v, 6) for v in plan["bbox_ll"]],
        "item": item["id"] if item else None, "item_date": item["date"] if item else None,
        "crop": {"file": f"{place_id}.{ext}", "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw),
                 "content_type": ctype.split(";")[0]},
        "nodata_pct": nd, "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "attribution": {"chip": src["attribution_chip"], "text": src["attribution"], "licence": src["licence"]},
        "recorded": [],
    }
    return raw, meta


# ---------------------------------------------------------------------------
# Tiling
# ---------------------------------------------------------------------------

def _starts(length: int, tile: int, overlap: int) -> list[int]:
    """Start offsets along one axis: the fewest tiles that cover ``length`` with
    at least ``overlap`` shared pixels, spread evenly (all integer)."""
    if length <= tile:
        return [0]
    n = -(-(length - overlap) // (tile - overlap))        # ceil
    return [round(i * (length - tile) / (n - 1)) for i in range(n)]


def plan_tiles(w: int, h: int, tile: int = TILE_PX, overlap: int = OVERLAP_PX) -> list[dict]:
    """Integer-pixel tiles of at most ``tile`` covering the scene with
    ``overlap``; the last row and column are shifted back so no tile is
    smaller than it must be. A scene no larger than one tile is one tile."""
    if not (64 <= tile <= 1024) or not (0 <= overlap < tile // 2):
        raise AerialError("bad_tiling", "tile must be 64..1024 px and overlap under half a tile", 422)
    out = []
    for yi, y0 in enumerate(_starts(h, tile, overlap)):
        for xi, x0 in enumerate(_starts(w, tile, overlap)):
            out.append({"i": len(out), "x0": x0, "y0": y0, "w": min(tile, w - x0), "h": min(tile, h - y0), "row": yi, "col": xi})
    return out


def _crop_png(raw: bytes, tile: dict) -> bytes:
    from PIL import Image
    im = Image.open(io.BytesIO(raw))
    im.load()
    c = im.crop((tile["x0"], tile["y0"], tile["x0"] + tile["w"], tile["y0"] + tile["h"]))
    buf = io.BytesIO()
    c.save(buf, "PNG")
    return buf.getvalue()


def tile_bytes(raw: bytes, content_type: str, tiles: list[dict], tile: dict, size) -> tuple[bytes, str]:
    """Bytes of one tile: the stored bytes unchanged for a whole-scene tile,
    otherwise an exact integer-pixel slice written as lossless PNG."""
    if len(tiles) == 1 and (tile["w"], tile["h"]) == tuple(size):
        return raw, content_type
    return _crop_png(raw, tile), "image/png"


def usable_tiles(raw: Optional[bytes], tiles: list[dict]) -> list[dict]:
    """Tiles worth a call: no-data share under NODATA_SKIP_PCT. Without the
    stored image (a recorded scene) every tile is kept."""
    out = []
    for t in tiles:
        if raw is None:
            out.append(dict(t, nodata_pct=None, skipped=False))
        else:
            nd = nodata_pct(_crop_png(raw, t))
            out.append(dict(t, nodata_pct=nd, skipped=nd >= NODATA_SKIP_PCT))
    return out


def estimate_cost(n_tiles: int, n_phrases: int) -> float:
    return round(n_tiles * n_phrases * PRICE_PER_IMAGE_USD, 6)


# ---------------------------------------------------------------------------
# Object geometry
# ---------------------------------------------------------------------------

def _hull(points: list) -> list:
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lo, up = [], []
    for p in pts:
        while len(lo) >= 2 and cross(lo[-2], lo[-1], p) <= 0:
            lo.pop()
        lo.append(p)
    for p in reversed(pts):
        while len(up) >= 2 and cross(up[-2], up[-1], p) <= 0:
            up.pop()
        up.append(p)
    return lo[:-1] + up[:-1]


def oriented_extent(raster: bytes, mh: int, mw: int, max_points: int = 6000) -> Optional[tuple[float, float]]:
    """(long, short) side in pixels of the minimum-area rotated rectangle
    around a binary mask (pixel extents: +1 px). None for an empty mask."""
    import math
    pts = [(x, y) for y in range(mh) for x in range(mw) if raster[y * mw + x]]
    if not pts:
        return None
    if len(pts) > max_points:                      # interior pixels never change the hull
        fg = set(pts)
        pts = [(x, y) for (x, y) in pts
               if not ((x - 1, y) in fg and (x + 1, y) in fg and (x, y - 1) in fg and (x, y + 1) in fg)]
    hull = _hull(pts)
    if len(hull) == 1:
        return 1.0, 1.0
    best = None
    n = len(hull)
    for i in range(n):
        x1, y1 = hull[i]
        x2, y2 = hull[(i + 1) % n]
        ang = math.atan2(y2 - y1, x2 - x1)
        c, s = math.cos(-ang), math.sin(-ang)
        xs = [c * x - s * y for x, y in hull]
        ys = [s * x + c * y for x, y in hull]
        a, b = max(xs) - min(xs) + 1.0, max(ys) - min(ys) + 1.0
        if best is None or a * b < best[0]:
            best = (a * b, max(a, b), min(a, b))
    return best[1], best[2]


def _box_iou(a, b) -> float:
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def _contain(a, b) -> float:
    """Intersection over the SMALLER box."""
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    sm = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return (ix * iy) / sm if sm > 0 else 0.0


def objects_from_reply(reply, phrase: str, tile: dict, scene_wh: tuple, gsd_m: float) -> list[dict]:
    """Scene-pixel objects for one (tile, phrase) reply. Raises
    SamResponseError when the reply frame is not the tile that was sent."""
    sw, sh = scene_wh
    out = []
    for rec in reply.records:
        if rec.frame != 0 or (rec.w, rec.h) != (tile["w"], tile["h"]):
            raise sam31_mod.SamResponseError(
                f"reply frame {rec.w}x{rec.h} does not match the {tile['w']}x{tile['h']} tile that was sent")
        x1, y1, x2, y2 = rec.x1 + tile["x0"], rec.y1 + tile["y0"], rec.x2 + 1 + tile["x0"], rec.y2 + 1 + tile["y0"]
        long_px, short_px, basis, rings = float(max(x2 - x1, y2 - y1)), float(min(x2 - x1, y2 - y1)), "box", None
        mask = rec.mask
        if mask is not None and mask.encoding == "one_bit" and (mask.height, mask.width) == (rec.box_h, rec.box_w):
            try:
                raster = sam31_mod.decode_mask(mask)
                ext = oriented_extent(raster, mask.height, mask.width)
                if ext:
                    long_px, short_px, basis = ext[0], ext[1], "mask"
                    rr = sam31_mod.raster_to_rings(raster, mask.height, mask.width, eps=0.7)
                    rings = [overlay_mod.ring_from_px([(min(max(x + x1, 0), sw), min(max(y + y1, 0), sh)) for x, y in ring], sw, sh)
                             for ring in rr] or None
            except sam31_mod.SamMaskError:
                pass
        edge = []
        if rec.x1 == 0 and tile["x0"] > 0:
            edge.append("l")
        if rec.y1 == 0 and tile["y0"] > 0:
            edge.append("t")
        if rec.x2 + 1 >= tile["w"] and tile["x0"] + tile["w"] < sw:
            edge.append("r")
        if rec.y2 + 1 >= tile["h"] and tile["y0"] + tile["h"] < sh:
            edge.append("b")
        scene_edge = x1 <= 0 or y1 <= 0 or x2 >= sw or y2 >= sh
        out.append({
            "phrase": phrase, "score": round(float(rec.confidence or 0.0), 4), "box_px": [x1, y1, x2, y2],
            "scene_edge": scene_edge,
            "tile": tile["i"], "cut_edges": edge, "long_px": round(long_px, 1), "short_px": round(short_px, 1),
            "gsd_m": gsd_m, "length_m": round(long_px * gsd_m, 1), "width_m": round(short_px * gsd_m, 1),
            "length_basis": basis, "rings": rings, "stitched": False,
        })
    return out


def _stitch_ok(a: dict, b: dict) -> bool:
    for p, q in ((a, b), (b, a)):
        pb, qb = p["box_px"], q["box_px"]
        if "r" in p["cut_edges"] and "l" in q["cut_edges"] and pb[2] > qb[0]:
            ov = min(pb[3], qb[3]) - max(pb[1], qb[1])
            if ov >= 0.7 * min(pb[3] - pb[1], qb[3] - qb[1]):
                return True
        if "b" in p["cut_edges"] and "t" in q["cut_edges"] and pb[3] > qb[1]:
            ov = min(pb[2], qb[2]) - max(pb[0], qb[0])
            if ov >= 0.7 * min(pb[2] - pb[0], qb[2] - qb[0]):
                return True
    return False


def merge_objects(objs: list[dict]) -> list[dict]:
    """De-duplicate across tiles, per phrase. Order: complete objects first,
    then by confidence. A candidate is dropped when it overlaps a kept object
    by IoU >= 0.5 or by >= 80% of the smaller box. Two pieces of one object,
    each cut by the SAME tile border (one at its left, one at its right, or
    above and below) and overlapping along it, are stitched into one box (the
    outline is dropped: the box is the union)."""
    kept: list[dict] = []
    for o in sorted(objs, key=lambda o: (bool(o["cut_edges"]), -o["score"])):
        if any(k["phrase"] == o["phrase"] and (_box_iou(k["box_px"], o["box_px"]) >= 0.5
                                               or _contain(k["box_px"], o["box_px"]) >= 0.8) for k in kept):
            continue
        kept.append(dict(o))
    merged = True
    while merged:
        merged = False
        for i, a in enumerate(kept):
            for b in kept[i + 1:]:
                if a["phrase"] == b["phrase"] and a["cut_edges"] and b["cut_edges"] and _stitch_ok(a, b):
                    u = [min(a["box_px"][0], b["box_px"][0]), min(a["box_px"][1], b["box_px"][1]),
                         max(a["box_px"][2], b["box_px"][2]), max(a["box_px"][3], b["box_px"][3])]
                    lp, sp = float(max(u[2] - u[0], u[3] - u[1])), float(min(u[2] - u[0], u[3] - u[1]))
                    a.update(box_px=u, score=max(a["score"], b["score"]), rings=None, stitched=True, length_basis="box",
                             scene_edge=a["scene_edge"] or b["scene_edge"],
                             cut_edges=[], long_px=lp, short_px=sp,
                             length_m=round(lp * a["gsd_m"], 1), width_m=round(sp * a["gsd_m"], 1))
                    kept.remove(b)
                    merged = True
                    break
            if merged:
                break
    return kept


# ---------------------------------------------------------------------------
# Result, exports
# ---------------------------------------------------------------------------

def px_to_lonlat(georef: dict, size_px, x: float, y: float, gsd_m: float) -> tuple[float, float]:
    if georef["kind"] == "utm":
        return geo.utm_inverse(georef["e0"] + x * gsd_m, georef["n1"] - y * gsd_m, georef["zone"])
    b = georef["bbox"]
    return b[0] + (b[2] - b[0]) * x / size_px[0], b[3] - (b[3] - b[1]) * y / size_px[1]


def build_result(scene: dict, by_phrase: dict, *, mode: str, images: int, cost_usd: float,
                 tiles: list[dict], response_ids: list, recorded_at: Optional[str] = None) -> dict:
    """``by_phrase``: {phrase: merged objects}. Object ids are stable per
    phrase (confidence order)."""
    w, h = scene["size_px"]
    gsd = scene["gsd_m"]
    objects, found = [], {}
    for phrase, objs in by_phrase.items():
        objs = sorted(objs, key=lambda o: -o["score"])
        found[phrase] = len(objs)
        vessel = phrase in VESSEL_PHRASES
        for i, o in enumerate(objs, 1):
            x1, y1, x2, y2 = o["box_px"]
            lon, lat = px_to_lonlat(scene["georef"], scene["size_px"], (x1 + x2) / 2, (y1 + y2) / 2, gsd)
            objects.append({
                "id": f"aerial:{phrase}:{i:03d}", "phrase": phrase, "class": PHRASES.get(phrase, phrase),
                "score": o["score"], "box_px": o["box_px"],
                "box_norm1000": [round(x1 / w * 1000, 2), round(y1 / h * 1000, 2), round(x2 / w * 1000, 2), round(y2 / h * 1000, 2)],
                "rings": o.get("rings"), "localization": "mask" if o.get("rings") else "bbox",
                "length_m": o["length_m"], "width_m": o["width_m"], "length_basis": o["length_basis"],
                "long_px": o["long_px"], "stitched": bool(o.get("stitched")), "tile": o["tile"],
                # a box that stops at the scene border is only part of the object: its length is a floor
                "length_is_floor": bool(o.get("scene_edge")),
                "below_floor": (o["long_px"] < MIN_COUNTABLE_LONG_SIDE_PX) if vessel else None,
                "centre_lonlat": [round(lon, 6), round(lat, 6)],
            })
    live = mode == "LIVE"
    return {
        "contract": CONTRACT, "scene_id": scene["scene_id"], "name": scene["name"], "source": scene["source"],
        "badge": scene["badge"], "acquisition_label": scene["acquisition_label"], "gsd_m": gsd, "size_px": [w, h],
        "mode": mode, "phrases": list(by_phrase), "found": found,
        "found_words": {p: ("SAM found none" if n == 0 else f"SAM found {n}") for p, n in found.items()},
        "objects": objects, "label": LABEL_MODELLED, "framing": FRAMING, "image_basis": IMAGE_BASIS,
        "verification": VERIFICATION, "human_reviewed": False,
        "tiles": [{k: t.get(k) for k in ("i", "x0", "y0", "w", "h", "nodata_pct", "skipped")} for t in tiles],
        "counts_feed_signals": False,
        "provenance": {
            "model": sam31_mod.MODEL if live else "recorded real sam-3.1 reply (replayed, not a call made now)",
            "transport": "LIVE" if live else "RECORDED", "live": live, "mode": "MODELED", "ground_truth": False,
            "recall_validated": False, "images_processed": images, "cost_usd": cost_usd if live else 0.0,
            "cost_basis": "ESTIMATED: images x $2.50 per 1,000 (published price)",
            "response_ids": response_ids, "recorded_at": recorded_at,
            "source": sam31_mod.SOURCE_LABEL,
        },
        "attribution": scene.get("attribution"),
    }


def to_geojson(result: dict, scene: dict) -> dict:
    feats = []
    gsd = scene["gsd_m"]
    for o in result["objects"]:
        x1, y1, x2, y2 = o["box_px"]
        ring = [(x1, y1), (x2, y1), (x2, y2), (x1, y2), (x1, y1)]
        ll = [px_to_lonlat(scene["georef"], scene["size_px"], x, y, gsd) for x, y in ring]
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [[[round(a, 7), round(b, 7)] for a, b in ll]]},
            "properties": {"id": o["id"], "class": o["class"], "phrase": o["phrase"], "confidence": o["score"],
                           "length_m": o["length_m"], "width_m": o["width_m"], "length_basis": o["length_basis"],
                           "geometry_is": "detection box", "label": LABEL_MODELLED},
        })
    return {"type": "FeatureCollection", "features": feats,
            "properties": {"scene": scene["scene_id"], "source": scene["source"], "badge": scene["badge"],
                           "gsd_m": gsd, "mode": result["mode"], "framing": FRAMING, "verification": VERIFICATION,
                           "note": "model detections of one scene, not a census; coordinates from the scene georeference",
                           "attribution": (scene.get("attribution") or {}).get("text")}}


def _csv_cell(v):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return str(v)                       # numbers (negative longitudes) are not formulas
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def to_csv(result: dict, scene: dict) -> str:
    buf = io.StringIO()
    wr = csv.writer(buf, lineterminator="\n")
    wr.writerow(["id", "class", "confidence", "length_m", "width_m", "length_basis", "lon", "lat", "x1_px", "y1_px", "x2_px", "y2_px",
                 "scene", "source", "gsd_m", "mode", "label"])
    for o in result["objects"]:
        wr.writerow([_csv_cell(v) for v in [o["id"], o["class"], o["score"], o["length_m"], o["width_m"], o["length_basis"],
                                            o["centre_lonlat"][0], o["centre_lonlat"][1], *o["box_px"],
                                            scene["scene_id"], scene["source"], scene["gsd_m"], result["mode"], LABEL_MODELLED]])
    return buf.getvalue()
