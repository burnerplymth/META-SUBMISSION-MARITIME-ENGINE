"""Planetary Computer STAC access for Satellite Watch.

Pipeline:
  * Microsoft Planetary Computer STAC search works with ZERO auth.
  * Native-resolution bbox crops come from the PC titiler endpoint
    (no multi-hundred-MB SAFE downloads).
  * Earth Search (AWS Open Data) is the failover catalog; it returned
    identical matched counts.
  * Copernicus Data Space is NOT a route (WAF 403s + downloads need an
    account) and is deliberately absent here.

Two rules are enforced in code:
  1. Footprint-coverage filtering: a scene is eligible only if its tile
     footprint FULLY covers the AOI ``core_bbox``. The lowest-cloud Port
     Said scene sat on tile T36SVA, whose footprint starts
     at 31.54N and misses the anchorage - it rendered a black crop.
  2. AOI-level cloud screening: tile ``eo:cloud_cover`` is computed over
     a whole 110x110 km tile and badly overstates anchorage cloud at
     e.g. Singapore (median tile cloud 53%). We screen the SCL (scene
     classification) crop over the core bbox itself and count cloud
     classes 8/9/10. The titiler renders SCL class values verbatim as
     greyscale pixels, so a PNG pixel count IS the AOI cloud fraction.

HTTP is stdlib ``urllib`` only (no requests/httpx dependency). An injectable
``http`` callable keeps the pure logic testable offline.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Optional

PC_STAC_SEARCH = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
EARTH_SEARCH_STAC = "https://earth-search.aws.element84.com/v1/search"
PC_TITILER = "https://planetarycomputer.microsoft.com/api/data/v1/item"
COLLECTION = "sentinel-2-l2a"
SOURCE_LABEL = "Microsoft Planetary Computer / Sentinel-2 L2A"
USER_AGENT = "supermap-satwatch/1.0 (Satellite Watch)"

# SCL classes counted as cloud for AOI screening: 8 cloud medium
# probability, 9 cloud high probability, 10 thin cirrus. Class 3 (cloud
# shadow) is reported separately - shadow means cloud nearby, not overhead.
SCL_CLOUD_CLASSES = (8, 9, 10)
SCL_SHADOW_CLASS = 3

# An AOI scene counts as usable when at most this fraction of the core
# bbox is cloud-classified. Deliberately more lenient than a tile-cloud
# gate: vessels at anchor are large, and partial cloud still leaves a
# countable majority of the anchorage.
AOI_CLOUD_USABLE_MAX = 35.0


class StacError(RuntimeError):
    """A STAC / titiler request failed on every catalog tried."""


# ---------------------------------------------------------------------------
# HTTP (injectable)
# ---------------------------------------------------------------------------

def default_http(method: str, url: str, body: Optional[dict] = None,
                 timeout: int = 90) -> tuple[int, dict, bytes]:
    """Return (status, headers, raw_bytes). Never raises for HTTP status."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"User-Agent": USER_AGENT}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read()
    except (urllib.error.URLError, OSError, ValueError) as exc:
        # An unreachable catalogue (DNS, refused, timed out) is a StacError every caller maps to
        # feed_down / crop_not_served.
        raise StacError("the imagery service could not be reached") from exc


HttpFn = Callable[..., tuple[int, dict, bytes]]


# ---------------------------------------------------------------------------
# Query construction (pure functions)
# ---------------------------------------------------------------------------

def build_search_body(bbox: list, start: date, end: date,
                      limit: int = 200) -> dict:
    """STAC search body for Sentinel-2 L2A over ``bbox`` in [start, end]."""
    return {
        "collections": [COLLECTION],
        "bbox": list(bbox),
        "datetime": f"{start.isoformat()}T00:00:00Z/{end.isoformat()}T23:59:59Z",
        "limit": limit,
    }


def footprint_covers(footprint_bbox: list, target_bbox: list) -> bool:
    """True iff the tile footprint fully contains ``target_bbox``.

    bboxes are [min_lon, min_lat, max_lon, max_lat]. This is the
    Port Said guard: intersects-the-search-box is NOT good enough.
    """
    return (footprint_bbox[0] <= target_bbox[0]
            and footprint_bbox[1] <= target_bbox[1]
            and footprint_bbox[2] >= target_bbox[2]
            and footprint_bbox[3] >= target_bbox[3])


def coverage_fraction(footprint_bbox: list, target_bbox: list) -> float:
    """Fraction (0..1) of the target bbox's area inside the footprint."""
    ix0 = max(footprint_bbox[0], target_bbox[0])
    iy0 = max(footprint_bbox[1], target_bbox[1])
    ix1 = min(footprint_bbox[2], target_bbox[2])
    iy1 = min(footprint_bbox[3], target_bbox[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    target_area = (target_bbox[2] - target_bbox[0]) * (target_bbox[3] - target_bbox[1])
    if target_area <= 0:
        return 0.0
    return ((ix1 - ix0) * (iy1 - iy0)) / target_area


def parse_items(feature_collection: dict) -> list[dict]:
    """Normalise STAC features into scene dicts (newest first, deduped)."""
    scenes: list[dict] = []
    seen: set[str] = set()
    for feat in feature_collection.get("features", []):
        props = feat.get("properties", {})
        scene_id = feat.get("id")
        if not scene_id or scene_id in seen:
            continue
        seen.add(scene_id)
        dt_str = props.get("datetime") or ""
        scenes.append({
            "id": scene_id,
            "datetime": dt_str,
            "date": dt_str[:10],
            "cloud_pct_tile": props.get("eo:cloud_cover"),
            "mgrs_tile": props.get("s2:mgrs_tile") or props.get("grid:code"),
            "platform": props.get("platform"),
            "footprint_bbox": feat.get("bbox"),
        })
    scenes.sort(key=lambda s: s["datetime"], reverse=True)
    return scenes


def filter_covering(scenes: list[dict], core_bbox: list) -> list[dict]:
    """Keep only scenes whose footprint fully covers ``core_bbox``."""
    return [s for s in scenes
            if s.get("footprint_bbox") and footprint_covers(s["footprint_bbox"], core_bbox)]


def dedupe_by_date(scenes: list[dict]) -> list[dict]:
    """One scene per acquisition date: the lowest tile-cloud covering one.

    Port Said's two tiles share datetimes; when both cover the core we
    keep the clearer one. A mosaic would double-count the overlap band,
    so a single covering tile is preferred over mosaicking (see README).
    """
    best: dict[str, dict] = {}
    for scene in scenes:
        day = scene["date"]
        cur = best.get(day)
        if cur is None:
            best[day] = scene
            continue
        cur_cloud = cur["cloud_pct_tile"] if cur["cloud_pct_tile"] is not None else 999.0
        new_cloud = scene["cloud_pct_tile"] if scene["cloud_pct_tile"] is not None else 999.0
        if new_cloud < cur_cloud:
            best[day] = scene
    return sorted(best.values(), key=lambda s: s["datetime"], reverse=True)


# ---------------------------------------------------------------------------
# Live queries
# ---------------------------------------------------------------------------

def query_stac(bbox: list, start: date, end: date,
               http: HttpFn = default_http) -> tuple[dict, str, int]:
    """Search PC STAC, failing over to Earth Search.

    Returns (feature_collection, catalog_url, http_status).
    """
    body = build_search_body(bbox, start, end)
    errors = []
    for url in (PC_STAC_SEARCH, EARTH_SEARCH_STAC):
        status, _headers, raw = http("POST", url, body=body, timeout=60)
        if status == 200:
            return json.loads(raw.decode("utf-8")), url, status
        errors.append(f"{url} -> HTTP {status}")
    raise StacError("STAC search failed on all catalogs: " + "; ".join(errors))


def titiler_crop_url(bbox: list, item_id: str, asset: str = "visual",
                     width: int = 1200, height: int = 1200) -> str:
    bbox_str = ",".join(str(x) for x in bbox)
    query = urllib.parse.urlencode({
        "collection": COLLECTION, "item": item_id, "assets": asset,
        "width": width, "height": height,
    })
    return f"{PC_TITILER}/bbox/{bbox_str}.png?{query}"


def fetch_crop(bbox: list, item_id: str, asset: str = "visual",
               width: int = 1200, height: int = 1200,
               http: HttpFn = default_http) -> tuple[bytes, int]:
    """Fetch a titiler bbox crop PNG. Returns (png_bytes, http_status)."""
    url = titiler_crop_url(bbox, item_id, asset=asset, width=width, height=height)
    status, headers, raw = http("GET", url, timeout=180)
    if status != 200 or "image" not in headers.get("Content-Type", ""):
        raise StacError(f"titiler crop failed for {item_id}: HTTP {status}")
    return raw, status


# ---------------------------------------------------------------------------
# AOI-level cloud screening via the SCL band
# ---------------------------------------------------------------------------

def scl_cloud_from_png(png_bytes: bytes) -> dict:
    """Count SCL classes in a titiler SCL crop PNG.

    The PC titiler renders the SCL asset with class values verbatim as
    greyscale pixels (pixel (6,6,6) = class
    6 water, etc.), so the cloud fraction is a direct pixel count.
    Requires Pillow; raises StacError when it is unavailable so callers
    degrade honestly (cloud_pct_aoi = null) instead of guessing.
    """
    try:
        from PIL import Image  # type: ignore
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise StacError("Pillow unavailable - AOI SCL screening cannot run") from exc
    import io
    img = Image.open(io.BytesIO(png_bytes)).convert("L")
    pixels = list(img.getdata())
    total = len(pixels)
    if total == 0:
        raise StacError("empty SCL crop")
    cloud = sum(1 for p in pixels if p in SCL_CLOUD_CLASSES)
    shadow = sum(1 for p in pixels if p == SCL_SHADOW_CLASS)
    nodata = sum(1 for p in pixels if p == 0)
    return {
        "cloud_pct_aoi": round(100.0 * cloud / total, 2),
        "shadow_pct_aoi": round(100.0 * shadow / total, 2),
        "nodata_pct_aoi": round(100.0 * nodata / total, 2),
        "method": "SCL crop pixel count over core_bbox (classes 8/9/10 = cloud)",
    }


def screen_aoi_cloud(core_bbox: list, item_id: str,
                     http: HttpFn = default_http) -> dict:
    """Fetch the SCL crop for a scene and return its AOI cloud screen."""
    raw, _status = fetch_crop(core_bbox, item_id, asset="SCL",
                              width=512, height=512, http=http)
    return scl_cloud_from_png(raw)


# ---------------------------------------------------------------------------
# Pair selection
# ---------------------------------------------------------------------------

def select_pair(covering_by_date: list[dict],
                cloud_fn: Callable[[dict], Optional[float]],
                baseline_gap_days: tuple[int, int] = (14, 60),
                usable_max: float = AOI_CLOUD_USABLE_MAX,
                max_now_evals: int = 8,
                max_baseline_evals: int = 6) -> dict:
    """Pick the (now, baseline) scene pair from covering scenes.

    ``covering_by_date`` must already be footprint-filtered and deduped
    by date, newest first. ``cloud_fn(scene)`` returns the AOI cloud %
    (or None when screening is unavailable); it is called lazily, newest
    first, and budgeted - SCL crops cost a request each.

    now      = newest scene whose AOI cloud <= usable_max; if none of
               the evaluated scenes pass, the clearest evaluated scene
               (flagged unusable) - never an invented date.
    baseline = scene closest to 30 days before ``now`` inside
               baseline_gap_days that also passes the gate, else the
               clearest in-window scene, else None.
    """
    evaluated: list[tuple[dict, Optional[float]]] = []
    now_entry: Optional[tuple[dict, Optional[float]]] = None
    for scene in covering_by_date[:max_now_evals]:
        pct = cloud_fn(scene)
        evaluated.append((scene, pct))
        if pct is not None and pct <= usable_max:
            now_entry = (scene, pct)
            break
    if now_entry is None:
        if not evaluated:
            return {"now": None, "baseline": None, "evaluated": [],
                    "usable_pair": False,
                    "notes": ["no footprint-covering scenes in window"]}
        known = [(s, p) for s, p in evaluated if p is not None]
        if known:
            now_entry = min(known, key=lambda sp: sp[1])
        else:  # screening unavailable: fall back to newest, flagged
            now_entry = evaluated[0]
    now_scene, now_pct = now_entry
    now_date = date.fromisoformat(now_scene["date"])

    baseline_entry: Optional[tuple[dict, Optional[float]]] = None
    candidates = []
    for scene in covering_by_date:
        if scene["id"] == now_scene["id"]:
            continue
        gap = (now_date - date.fromisoformat(scene["date"])).days
        if baseline_gap_days[0] <= gap <= baseline_gap_days[1]:
            candidates.append(scene)
    # closest to a 30-day gap first
    candidates.sort(key=lambda s: abs(
        (now_date - date.fromisoformat(s["date"])).days - 30))
    baseline_evals: list[tuple[dict, Optional[float]]] = []
    for scene in candidates[:max_baseline_evals]:
        pct = cloud_fn(scene)
        baseline_evals.append((scene, pct))
        if pct is not None and pct <= usable_max:
            baseline_entry = (scene, pct)
            break
    if baseline_entry is None and baseline_evals:
        known = [(s, p) for s, p in baseline_evals if p is not None]
        if known:
            baseline_entry = min(known, key=lambda sp: sp[1])
    usable = bool(
        baseline_entry is not None
        and now_pct is not None and now_pct <= usable_max
        and baseline_entry[1] is not None and baseline_entry[1] <= usable_max)
    return {
        "now": {"scene": now_scene, "cloud_pct_aoi": now_pct},
        "baseline": ({"scene": baseline_entry[0], "cloud_pct_aoi": baseline_entry[1]}
                     if baseline_entry else None),
        "evaluated": [{"id": s["id"], "date": s["date"], "cloud_pct_aoi": p}
                      for s, p in evaluated + baseline_evals],
        "usable_pair": usable,
        "notes": [],
    }


def window(days: int = 90, end: Optional[date] = None) -> tuple[date, date]:
    # The UTC date, like the rest of the codebase — local
    # date.today() shifts the scan window by a day near midnight for
    # any non-UTC host timezone.
    end = end or datetime.now(timezone.utc).date()
    return end - timedelta(days=days), end
