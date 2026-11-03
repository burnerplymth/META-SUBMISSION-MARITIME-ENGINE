"""Multi-source STAC layer: search, scene selection, crops.

One catalogue (Earth Search, AWS Open Data, no auth) answers the search
for every source in ``sources.py``; crops come from the Planetary
Computer titiler (the same renderer the Sentinel-2 pipeline uses), which
serves Earth Search item ids unchanged for these collections.

Selection is by bbox and date. A cloud filter is applied only where the
source has one (optical: Sentinel-2, Landsat); SAR and aerial sources get
none. A scene is eligible only if its footprint polygon fully covers the
requested bbox (the Port Said guard from ``stac.py``, applied to the real
polygon because a SAR swath's bbox is far larger than its footprint).

HTTP is stdlib ``urllib`` through the injectable ``http`` callable that
``stac.py`` defines, so every pure function here is testable offline.
Nothing in this module invents an answer: a failed search raises
``StacError`` and callers show NOT SERVED.
"""
from __future__ import annotations

import io
import json
import sys
import urllib.parse
from array import array
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from . import sources as src_mod
from .stac import HttpFn, StacError, default_http, footprint_covers

SEARCH_URL = src_mod.EARTH_SEARCH_API + "/search"
SEARCH_LIMIT = 25
MAX_CROP_PX = 1600          # longest crop side requested from the renderer
MIN_CROP_PX = 64
# NAIP ships as ~7 km quarter-quad tiles: a place is looked up by a small
# window about its centre, never by its whole (tens of km) core box.
NAIP_WINDOW_M = 1600.0


# ---------------------------------------------------------------------------
# Query construction (pure)
# ---------------------------------------------------------------------------

def build_search_body(source_id: str, bbox: list, start: Optional[date],
                      end: Optional[date], max_cloud: Optional[float] = None,
                      limit: int = SEARCH_LIMIT) -> dict:
    """STAC search body for ``source_id`` over ``bbox`` and ``[start, end]``.

    ``max_cloud`` is honoured only for sources that have a cloud filter;
    for SAR and aerial sources it is ignored (there is nothing to filter).
    Newest first.
    """
    src = src_mod.get_source(source_id)
    body: dict = {
        "collections": [src["collection"]],
        "bbox": list(bbox),
        "limit": limit,
        "sortby": [{"field": "properties.datetime", "direction": "desc"}],
    }
    if start is not None and end is not None:
        body["datetime"] = (f"{start.isoformat()}T00:00:00Z/"
                            f"{end.isoformat()}T23:59:59Z")
    query: dict = {}
    if src["cloud_filter"] and max_cloud is not None:
        query["eo:cloud_cover"] = {"lte": float(max_cloud)}
    if source_id == "sentinel-1":
        # Interferometric wide swath, dual or single VV: the mode ships are
        # looked for in over open water. Wave / extra-wide modes are skipped.
        query["sar:instrument_mode"] = {"eq": "IW"}
    if query:
        body["query"] = query
    return body


def _ring_contains(ring: list, x: float, y: float) -> bool:
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def geometry_covers_bbox(geometry: Optional[dict], bbox: list) -> bool:
    """True when the GeoJSON polygon contains all four corners and the
    centre of ``bbox``. Polygon and MultiPolygon only; anything else is
    not trusted to cover."""
    if not isinstance(geometry, dict):
        return False
    kind = geometry.get("type")
    coords = geometry.get("coordinates")
    if kind == "Polygon":
        polys = [coords]
    elif kind == "MultiPolygon":
        polys = coords
    else:
        return False
    pts = [(bbox[0], bbox[1]), (bbox[2], bbox[1]), (bbox[2], bbox[3]),
           (bbox[0], bbox[3]), ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)]
    for poly in polys:
        if not poly:
            continue
        outer = poly[0]
        if all(_ring_contains(outer, x, y) for x, y in pts) and not any(
                _ring_contains(hole, x, y) for hole in poly[1:] for x, y in pts):
            return True
    return False


def parse_scenes(source_id: str, feature_collection: dict) -> list[dict]:
    """Normalise STAC features into scene dicts, newest first, deduped."""
    scenes: list[dict] = []
    seen: set = set()
    for feat in (feature_collection or {}).get("features", []) or []:
        sid = feat.get("id")
        if not sid or sid in seen:
            continue
        seen.add(sid)
        props = feat.get("properties") or {}
        dt = props.get("datetime") or props.get("start_datetime") or ""
        scene = {
            "id": sid,
            "datetime": dt,
            "date": dt[:10],
            "platform": props.get("platform"),
            "cloud_pct_tile": props.get("eo:cloud_cover"),
            "footprint_bbox": feat.get("bbox"),
            "geometry": feat.get("geometry"),
            "gsd_m": props.get("gsd"),
        }
        if source_id == "sentinel-1":
            scene["polarizations"] = props.get("sar:polarizations")
            scene["orbit_state"] = props.get("sat:orbit_state")
            scene["instrument_mode"] = props.get("sar:instrument_mode")
        if source_id == "naip":
            scene["year"] = props.get("naip:year")
            scene["state"] = props.get("naip:state")
        scenes.append(scene)
    scenes.sort(key=lambda s: s["datetime"], reverse=True)
    return scenes


def covering(scenes: list[dict], bbox: list) -> list[dict]:
    """Scenes whose footprint fully covers ``bbox``. Polygon geometry is
    used when present, the footprint bbox otherwise."""
    out = []
    for s in scenes:
        if s.get("geometry"):
            ok = geometry_covers_bbox(s["geometry"], bbox)
        elif s.get("footprint_bbox"):
            ok = footprint_covers(s["footprint_bbox"], bbox)
        else:
            ok = False
        if ok:
            out.append(s)
    return out


def usable_s1(scenes: list[dict]) -> list[dict]:
    """Scenes carrying a VV measurement (the band the detector reads)."""
    return [s for s in scenes
            if not s.get("polarizations") or "VV" in s["polarizations"]]


def window(days: Optional[int], end: Optional[date] = None
           ) -> tuple[Optional[date], Optional[date]]:
    if days is None:
        return None, None
    end = end or datetime.now(timezone.utc).date()
    return end - timedelta(days=days), end


# ---------------------------------------------------------------------------
# Live search
# ---------------------------------------------------------------------------

def search(source_id: str, bbox: list, days: Optional[int] = None,
           max_cloud: Optional[float] = None, http: HttpFn = default_http,
           end: Optional[date] = None, timeout: int = 25) -> list[dict]:
    """Scenes for a source over ``bbox``, newest first. Raises StacError."""
    if days is None:
        days = src_mod.DEFAULT_WINDOW_DAYS[source_id]
    start, stop = window(days, end)
    cloud = src_mod.max_cloud_for(source_id, max_cloud)
    body = build_search_body(source_id, bbox, start, stop, cloud)
    status, _h, raw = http("POST", SEARCH_URL, body=body, timeout=timeout)
    if status != 200:
        raise StacError(f"{source_id} search failed: HTTP {status}")
    try:
        doc = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise StacError(f"{source_id} search returned a non-JSON body") from exc
    return parse_scenes(source_id, doc)


def centre_window(bbox: list, window_m: float) -> list:
    """A ``window_m`` square about the centre of ``bbox``, clipped to it."""
    cx, cy = (bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0
    mx, my = metres_per_degree(cy)
    hx, hy = window_m / 2.0 / mx, window_m / 2.0 / my
    return [max(bbox[0], cx - hx), max(bbox[1], cy - hy),
            min(bbox[2], cx + hx), min(bbox[3], cy + hy)]


def latest_covering(source_id: str, bbox: list, days: Optional[int] = None,
                    max_cloud: Optional[float] = None,
                    http: HttpFn = default_http) -> dict:
    """Availability of a source over a bbox: what the chip row needs.

    ``state`` is one of ``AVAILABLE`` (a covering scene exists),
    ``NO SCENE`` (the catalogue answered, nothing covers the bbox in the
    window) or ``OUT OF COVER`` (a US-only source outside the US). A feed
    failure raises StacError - the caller maps it to NOT SERVED.

    NAIP is judged on its centre window (see ``NAIP_WINDOW_M``): a tile
    must contain the place centre.
    """
    if not src_mod.covered_by_region(source_id, bbox):
        return {"state": "OUT OF COVER", "scene": None, "scenes_seen": 0}
    if source_id == "naip":
        win = centre_window(bbox, NAIP_WINDOW_M)
        scenes = search("naip", win, days=None, http=http)
        centre = [(bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0]
        cov = covering(scenes, centre + centre)
    else:
        scenes = search(source_id, bbox, days=days, max_cloud=max_cloud, http=http)
        if source_id == "sentinel-1":
            scenes = usable_s1(scenes)
        cov = covering(scenes, bbox)
    if not cov:
        return {"state": "NO SCENE", "scene": None, "scenes_seen": len(scenes)}
    return {"state": "AVAILABLE", "scene": cov[0], "scenes_seen": len(scenes)}


# ---------------------------------------------------------------------------
# Crop geometry and URLs
# ---------------------------------------------------------------------------

def metres_per_degree(lat: float) -> tuple[float, float]:
    import math
    return (111_320.0 * math.cos(math.radians(lat)), 110_540.0)


def crop_size_px(bbox: list, target_m: float,
                 max_px: int = MAX_CROP_PX) -> tuple[int, int, float]:
    """(width, height, effective metres per pixel) for a native-resolution
    crop of ``bbox``, capped so the longest side is at most ``max_px``."""
    lat = (bbox[1] + bbox[3]) / 2.0
    mx, my = metres_per_degree(lat)
    w_m = abs(bbox[2] - bbox[0]) * mx
    h_m = abs(bbox[3] - bbox[1]) * my
    w = max(MIN_CROP_PX, round(w_m / target_m))
    h = max(MIN_CROP_PX, round(h_m / target_m))
    scale = min(1.0, max_px / float(max(w, h)))
    w, h = max(MIN_CROP_PX, round(w * scale)), max(MIN_CROP_PX, round(h * scale))
    eff = max(w_m / w, h_m / h)
    return int(w), int(h), round(eff, 2)


def _bbox_str(bbox: list) -> str:
    return ",".join(f"{v:.6f}".rstrip("0").rstrip(".") for v in bbox)


def titiler_url(source_id: str, bbox: list, item_id: str, width: int,
                height: int, fmt: str = "png") -> str:
    """Titiler bbox-crop URL for one item of one source.

    sentinel-1: raw ``vv`` amplitude as a GeoTIFF (``return_mask=false``
    keeps it a single 16-bit band) - the detector reads these values.
    naip: RGB of the 4-band COG as PNG. landsat: true-colour SR as PNG
    with a fixed reflectance stretch.
    """
    src = src_mod.get_source(source_id)
    q: list[tuple[str, str]] = [("collection", src["titiler_collection"]),
                                ("item", item_id)]
    if source_id == "sentinel-1":
        q += [("assets", "vv"), ("return_mask", "false")]
        fmt = "tif"
    elif source_id == "naip":
        q += [("assets", "image"), ("asset_bidx", "image|1,2,3")]
        # The data API answers NAIP item/bbox with 501 ("operation is not
        # supported"); the /crop/ form of the same
        # renderer answers 200, size in the path.
        return (f"{src_mod.PC_TITILER}/crop/{_bbox_str(bbox)}/{width}x{height}.{fmt}?"
                + urllib.parse.urlencode(q, safe="|,"))
    elif source_id == "landsat":
        q += [("assets", "red"), ("assets", "green"), ("assets", "blue"),
              ("rescale", "7000,16000")]
    elif source_id == "sentinel-2":
        q += [("assets", "visual")]
    q += [("width", str(width)), ("height", str(height))]
    return (f"{src_mod.PC_TITILER}/bbox/{_bbox_str(bbox)}.{fmt}?"
            + urllib.parse.urlencode(q, safe="|,"))


def fetch_crop_bytes(source_id: str, bbox: list, item_id: str, width: int,
                     height: int, http: HttpFn = default_http,
                     timeout: int = 180) -> bytes:
    url = titiler_url(source_id, bbox, item_id, width, height)
    status, headers, raw = http("GET", url, timeout=timeout)
    ctype = (headers or {}).get("Content-Type", (headers or {}).get("content-type", ""))
    if status != 200 or "image" not in ctype:
        raise StacError(f"{source_id} crop failed for {item_id}: HTTP {status}")
    return raw


# ---------------------------------------------------------------------------
# Decoding a raw single-band crop (Pillow only)
# ---------------------------------------------------------------------------

def decode_gray(raw: bytes) -> tuple[int, int, "array"]:
    """Decode a single-band 16-bit / float GeoTIFF or PNG into (w, h, values).

    Values stay in the source's own units (amplitude DN for Sentinel-1
    GRD); nothing is rescaled or filtered. Pillow is the only dependency.
    Raises StacError for anything that is not a single band.
    """
    try:
        from PIL import Image  # type: ignore
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise StacError("Pillow unavailable - cannot decode the crop") from exc
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception as exc:
        raise StacError(f"crop is not a readable raster: {exc}") from exc
    mode = img.mode
    if mode == "I;16":
        vals = array("H")
        vals.frombytes(img.tobytes())
        if sys.byteorder == "big":  # pragma: no cover
            vals.byteswap()
    elif mode == "F":
        vals = array("f")
        vals.frombytes(img.tobytes())
    elif mode == "L":
        vals = array("B")
        vals.frombytes(img.tobytes())
    elif mode == "I":
        vals = array("i")
        vals.frombytes(img.tobytes())
    else:
        raise StacError(f"unexpected raster mode {mode!r} (single band expected)")
    w, h = img.size
    if len(vals) != w * h:
        raise StacError("raster size does not match its pixel count")
    return w, h, vals
