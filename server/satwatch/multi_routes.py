"""Additive /satwatch routes for the multi-source desk.

Registered from ``routes.register_satwatch_routes`` (one call); nothing
in app.py or the existing routes changes.

    GET  /satwatch/sources                 source registry (resolution, revisit,
                                           licence, ship-detection grade)
    GET  /satwatch/sources/{place}         per-place availability for each source
                                           (live catalogue lookup, cached 10 min)
    GET  /satwatch/multi/crops/{file}.png  crop image for any stored multi-source product
    GET  /satwatch/s1/{place}              stored Sentinel-1 scan (CFAR targets)
    POST /satwatch/s1/scan                 admin: scan one place (fetch + detect)
    GET  /satwatch/naip/{place}            stored NAIP crop (US places)
    POST /satwatch/naip/crop               admin: fetch a NAIP crop
    GET  /satwatch/landsat/{place}         stored Landsat crop
    POST /satwatch/landsat/crop            admin: fetch a Landsat crop

Auth follows the existing satwatch pattern: reads are public; anything
that spends upstream bandwidth or CPU (the POSTs) needs
``Authorization: Bearer $SUPERMAP_ADMIN_TOKEN`` and fails closed when the
token is unset. Handlers are plain ``def`` (threadpooled). Errors use the
house shape ``{error: {code, message}}``. Imagery is a SNAPSHOT and any
detector output is MODELED; a feed that is down answers NOT SERVED state,
never an invented value.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Optional

from fastapi import Request
from fastapi.responses import FileResponse

from . import multiscan as multi_mod
from . import multistac as ms
from . import sources as src_mod
from .routes import NODE_RE, _admin_ok, _err
from .scan import AOIS_PATH, load_aois
from .stac import StacError

AVAILABILITY_TTL_S = 600.0
FAILURE_TTL_S = 30.0       # a NOT SERVED answer is remembered this long, not re-asked per request
POST_BODY_KEYS = {"place", "days"}
ERROR_STATUS = {"feed_down": 502, "crop_not_served": 502, "no_scene": 404,
                "outside_coverage": 422, "empty_crop": 502, "bad_source": 422}


class AvailabilityCache:
    """Small TTL cache of per-place source availability (catalogue calls
    cost ~1 s each; the chip row asks on every place change)."""

    def __init__(self, ttl: float = AVAILABILITY_TTL_S,
                 clock: Callable[[], float] = time.monotonic):
        self.ttl, self.clock = ttl, clock
        self._items: dict = {}
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            hit = self._items.get(key)
            if hit and self.clock() - hit[0] < (hit[2] if hit[2] is not None else self.ttl):
                return hit[1]
        return None

    def put(self, key, value, ttl: Optional[float] = None) -> None:
        with self._lock:
            self._items[key] = (self.clock(), value, ttl)


def _scene_view(scene: Optional[dict]) -> Optional[dict]:
    if not scene:
        return None
    out = {"id": scene["id"], "date": scene["date"], "platform": scene.get("platform")}
    if scene.get("cloud_pct_tile") is not None:
        out["cloud_pct_tile"] = scene["cloud_pct_tile"]
    return out


def _s2_stored(satwatch_dir: Path, place: str) -> Optional[dict]:
    import json
    path = Path(satwatch_dir) / f"{place}.json"
    if not (NODE_RE.match(place) and path.is_file()):
        return None
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(rec, dict) or rec.get("kind") != "satwatch_scan":
        return None
    return {"date": (rec.get("imagery_dates") or {}).get("now"),
            "retrieved_at": rec.get("retrieved_at"),
            "targets_floor": None,
            "detector_status": (rec.get("detection") or {}).get("status")}


def availability(place: str, aoi: dict, satwatch_dir: Path, http,
                 cache: AvailabilityCache, catalogue: bool = True) -> dict:
    """One entry per source: AVAILABLE / NO SCENE / OUT OF COVER / NOT SERVED
    plus the stored product summary. A source whose catalogue call fails is
    NOT SERVED on its own; the others still answer."""
    cached = cache.get(place) if catalogue else None
    if not catalogue:
        # Stored products only, no catalogue call: answers at once, so the desk can show every
        # stored snapshot while the live lookup is still running.
        cached = {s["id"]: {"state": "NOT SERVED", "scene": None, "error": "not_checked"} for s in src_mod.SOURCES}
    elif cached is None:
        bbox = list(aoi["core_bbox"])

        def one(source_id: str) -> dict:
            try:
                got = ms.latest_covering(source_id, bbox, http=http)
                return {"state": got["state"], "scene": _scene_view(got["scene"]),
                        "error": None}
            except StacError:
                return {"state": "NOT SERVED", "scene": None, "error": "feed_down"}
            except Exception:       # a malformed answer is also not served
                return {"state": "NOT SERVED", "scene": None, "error": "bad_response"}

        with ThreadPoolExecutor(max_workers=len(src_mod.SOURCE_IDS)) as pool:
            cached = dict(zip(src_mod.SOURCE_IDS, pool.map(one, src_mod.SOURCE_IDS)))
        # A down feed is remembered only briefly (FAILURE_TTL_S): the route is
        # public and every miss is up to four live catalogue calls.
        down = any(v["state"] == "NOT SERVED" for v in cached.values())
        cache.put(place, cached, ttl=FAILURE_TTL_S if down else None)
    rows = []
    for s in src_mod.SOURCES:
        a = cached[s["id"]]
        if s["id"] == "sentinel-2":
            stored = _s2_stored(satwatch_dir, place)
        else:
            stored = multi_mod.summarise(multi_mod.load_record(satwatch_dir, s["id"], place))
        rows.append({
            "id": s["id"], "chip": s["chip"], "label": s["label"],
            "state": a["state"], "error": a["error"], "scene": a["scene"],
            "stored": stored,
            "ship_detection_grade": s["ship_detection_grade"],
            "ship_detection_label": s["ship_detection_label"],
            "attribution_chip": s["attribution_chip"],
        })
    return {"contract": "satwatch-sources/1", "place": place, "name": aoi.get("name"),
            "mode": "SNAPSHOT", "catalogue": bool(catalogue), "sources": rows}


def register_multi_routes(app, ctx: dict) -> None:
    satwatch_dir = Path(ctx["satwatch_dir"])
    http = ctx.get("http")
    aois_fn = ctx.get("aois_fn") or (lambda: load_aois(AOIS_PATH))
    cache = AvailabilityCache(clock=ctx.get("clock") or time.monotonic)
    busy = threading.Lock()
    kwargs_http = {"http": http} if http is not None else {}

    def _aoi(place: str):
        if not isinstance(place, str) or not NODE_RE.match(place):
            return None
        return aois_fn().get(place)

    @app.get("/satwatch/sources", tags=["satwatch multi-source"], summary="Imagery source registry: resolution, revisit, licence, ship-detection grade")
    def get_sources():
        return src_mod.describe_sources()

    @app.get("/satwatch/sources/{place}", tags=["satwatch multi-source"], summary="Per-place availability of each source (live catalogue lookup, cached 10 min)")
    def get_source_availability(place: str, catalogue: bool = True):
        aoi = _aoi(place)
        if aoi is None:
            return _err("place_not_found", f"unknown place {place!r}", 404)
        return availability(place, aoi, satwatch_dir, http or _default_http(), cache, catalogue=catalogue)

    @app.get("/satwatch/multi/crops/{filename:path}", tags=["satwatch multi-source"],
             summary="Crop PNG of a stored multi-source product (basename, .png only)")
    def get_multi_crop(filename: str):
        if (not filename or filename != Path(filename).name or "/" in filename
                or "\\" in filename or Path(filename).suffix != ".png"):
            return _err("crop_not_found", f"unknown crop: {filename!r}", 404)
        path = satwatch_dir / multi_mod.MULTI_DIR / filename
        if not path.is_file():
            return _err("crop_not_found", f"unknown crop: {filename!r}", 404)
        return FileResponse(path, media_type="image/png")

    def _stored(source_id: str, place: str):
        if _aoi(place) is None:
            return _err("place_not_found", f"unknown place {place!r}", 404)
        rec = multi_mod.load_record(satwatch_dir, source_id, place)
        if rec is None:
            return _err("scan_not_found",
                        f"no stored {source_id} product for {place!r}", 404)
        return rec

    def _run(request: Request, body, runner):
        if not _admin_ok(request):
            return _err("unauthorized", "this route requires the admin bearer token", 401)
        if not isinstance(body, dict):
            return _err("invalid_request", 'body must be {"place": "<place id>"}', 422)
        extra = set(body) - POST_BODY_KEYS
        if extra:
            return _err("invalid_request", f"unknown field(s): {sorted(extra)}", 422)
        place, days = body.get("place"), body.get("days")
        aoi = _aoi(place)
        if aoi is None:
            return _err("unknown_place", f"unknown place {place!r}", 422)
        if days is not None and (not isinstance(days, int) or isinstance(days, bool)
                                 or not 7 <= days <= 365):
            return _err("invalid_request", "days must be an integer in 7..365", 422)
        if not busy.acquire(blocking=False):
            return _err("scan_busy", "another multi-source fetch is running", 429)
        try:
            rec = runner(place, aoi, days)
        except multi_mod.MultiError as exc:
            return _err(exc.code, str(exc), ERROR_STATUS.get(exc.code, 502))
        finally:
            busy.release()
        return rec

    @app.post("/satwatch/s1/scan", tags=["satwatch multi-source"], summary="Admin: scan a place with Sentinel-1 GRD VV and run the CFAR detector (MODELED, floor count)")
    def post_s1_scan(request: Request, body: dict | None = None):
        return _run(request, body,
                    lambda p, a, d: multi_mod.scan_s1(p, a, satwatch_dir, days=d, **kwargs_http))

    @app.get("/satwatch/s1/{place}", tags=["satwatch multi-source"], summary="Stored Sentinel-1 scan for a place (points use the satwatch-overlay/1 contract)")
    def get_s1(place: str):
        return _stored("sentinel-1", place)

    @app.post("/satwatch/naip/crop", tags=["satwatch multi-source"], summary="Admin: fetch a NAIP aerial crop for a US place (reference imagery, no detection)")
    def post_naip(request: Request, body: dict | None = None):
        return _run(request, body,
                    lambda p, a, d: multi_mod.crop_optical("naip", p, a, satwatch_dir, **kwargs_http))

    @app.get("/satwatch/naip/{place}", tags=["satwatch multi-source"], summary="Stored NAIP crop record for a place")
    def get_naip(place: str):
        return _stored("naip", place)

    @app.post("/satwatch/landsat/crop", tags=["satwatch multi-source"], summary="Admin: fetch a Landsat true-colour crop (30 m context, no detection)")
    def post_landsat(request: Request, body: dict | None = None):
        return _run(request, body,
                    lambda p, a, d: multi_mod.crop_optical("landsat", p, a, satwatch_dir, days=d, **kwargs_http))

    @app.get("/satwatch/landsat/{place}", tags=["satwatch multi-source"], summary="Stored Landsat crop record for a place")
    def get_landsat(place: str):
        return _stored("landsat", place)

    app.state.satwatch_multi_routes = True
    app.state.satwatch_multi_busy = busy      # one fetch at a time


def _default_http():
    from .stac import default_http
    return default_http
