"""HTTP surface for aerial (sub-metre) scenes and the SAM tile pipeline
Registered from ``routes.register_satwatch_routes``.

    GET  /satwatch/aerial/sources           the aerial source registry (GSD, year basis, licence, attribution)
    GET  /satwatch/aerial/scenes            curated ports: stored / recorded scenes and NOT FETCHED ones
    GET  /satwatch/aerial/plan?scene=&phrases=a,b
                                            tiles, calls, estimate, recorded availability, live state (never calls a model)
    GET  /satwatch/aerial/image/{scene}     the picture: the native crop when stored, else the committed reduced preview
    GET  /satwatch/aerial/export?scene=&phrases=&fmt=geojson|csv
                                            the recorded detections of a scene as GeoJSON or CSV
    POST /satwatch/aerial/fetch {place}     admin: fetch the curated window (native pixels as served) and store it
    POST /satwatch/aerial/run {scene, phrases?, mode, confirm_cost_usd?}
                                            ``recorded`` replays stored real replies (free, public); ``live`` spends:
                                            admin bearer, key, confirm_cost_usd equal to the plan's estimate, SAM meter cap

Reads are public. Results are MODELLED, display-only, "Detections, not a
census"; ``counts_feed_signals`` is always false and nothing here writes a
scan record or an event. Errors: {error: {code, message}}.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Optional

from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse, Response

from . import aerial as ae
from . import aerial_run as run
from . import sam_meter as sam_meter_mod
from . import sources as src_mod
from .routes import _admin_ok, _err

RUN_KEYS = {"scene", "phrases", "mode", "confirm_cost_usd"}
FETCH_KEYS = {"place"}


def register_aerial_routes(app, ctx: dict) -> None:
    sd = Path(ctx["satwatch_dir"])
    fixtures: Optional[Path] = ctx.get("aerial_fixtures")
    http = ctx.get("http")
    busy = threading.Lock()

    def _fail(exc: ae.AerialError):
        return _err(exc.code, str(exc), exc.status)

    def _scene(scene_id) -> dict:
        sc = run.load_scene(sd, scene_id, fixtures)
        if sc is None:
            raise ae.AerialError("scene_not_found", "no such aerial scene (fetch it first, or pick a listed one)", 404)
        return sc

    def _phrases(raw: Optional[str]):
        return run.parse_phrases([p.strip() for p in raw.split(",") if p.strip()] if raw else None)

    @app.get("/satwatch/aerial/sources", tags=["satwatch aerial"], summary="Aerial (sub-metre) source registry")
    def get_aerial_sources():
        return src_mod.describe_aerial_sources()

    @app.get("/satwatch/aerial/scenes", tags=["satwatch aerial"], summary="Aerial port scenes: stored, recorded, not fetched")
    def get_aerial_scenes():
        return {"contract": "satwatch-aerial-scenes/1", "mode": "SNAPSHOT", "framing": ae.FRAMING,
                "phrases": [{"phrase": p, "label": lbl} for p, lbl in ae.PHRASES.items()],
                "default_phrases": list(ae.DEFAULT_PHRASES), "scenes": run.list_scenes(sd, fixtures)}

    @app.get("/satwatch/aerial/plan", tags=["satwatch aerial"],
             summary="SAM plan for one aerial scene: tiles, calls, estimate, recorded or live")
    def get_aerial_plan(scene: str = "", phrases: Optional[str] = None):
        try:
            sc = _scene(scene)
            usage = sam_meter_mod.SamMeter().status()
            u = {k: usage.get(k) for k in ("calls_today", "cap_calls_per_day", "remaining_calls_today", "cost_usd_today",
                                           "cap_reached", "disabled", "ledger_ok")}
            return run.plan(sd, sc, _phrases(phrases), fixtures, usage=u)
        except ae.AerialError as exc:
            return _fail(exc)

    @app.get("/satwatch/aerial/image/{scene}", tags=["satwatch aerial"], summary="The scene picture (native crop or reduced preview)")
    def get_aerial_image(scene: str):
        try:
            sc = _scene(scene)
        except ae.AerialError as exc:
            return _fail(exc)
        found = run.image_path(sd, sc, fixtures)
        if found is None:
            return _err("image_not_served", "no picture is stored for this scene", 404)
        path, ctype, kind = found
        return FileResponse(path, media_type=ctype, headers={"X-Image-Kind": kind, "Cache-Control": "public, max-age=300"})

    @app.get("/satwatch/aerial/export", tags=["satwatch aerial"], summary="Recorded detections of a scene as GeoJSON or CSV")
    def get_aerial_export(scene: str = "", phrases: Optional[str] = None, fmt: str = "geojson"):
        try:
            if fmt not in ("geojson", "csv"):
                raise ae.AerialError("invalid_request", "fmt must be geojson or csv", 422)
            sc = _scene(scene)
            res = run.run_recorded(sd, sc, _phrases(phrases), fixtures)
            name = f"{sc['scene_id']}_detections"
            if fmt == "csv":
                return Response(ae.to_csv(res, sc), media_type="text/csv; charset=utf-8",
                                headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})
            return Response(json.dumps(ae.to_geojson(res, sc)), media_type="application/geo+json",
                            headers={"Content-Disposition": f'attachment; filename="{name}.geojson"'})
        except ae.AerialError as exc:
            return _fail(exc)

    @app.post("/satwatch/aerial/fetch", tags=["satwatch aerial"], summary="Admin: fetch one curated aerial window")
    def post_aerial_fetch(request: Request, body: dict | None = None):
        if not _admin_ok(request):
            return _err("unauthorized", "fetching imagery requires the admin bearer token", 401)
        try:
            if not isinstance(body, dict) or set(body) - FETCH_KEYS or not isinstance(body.get("place"), str):
                raise ae.AerialError("invalid_request", "body must be {place}", 422)
            if not busy.acquire(blocking=False):
                return _err("scan_busy", "another aerial fetch or run is in progress", 429)
            try:
                raw, meta = ae.fetch_scene(body["place"], **({"http": http} if http else {}))
                run.save_fetched(sd, raw, meta)
            finally:
                busy.release()
            return run.scene_summary(run.load_scene(sd, meta["scene_id"], fixtures))
        except ae.AerialError as exc:
            return _fail(exc)

    @app.post("/satwatch/aerial/run", tags=["satwatch aerial"],
              summary="Run SAM 3.1 on an aerial scene (recorded free; live needs admin + cost confirmation)")
    def post_aerial_run(request: Request, body: dict | None = None):
        try:
            if not isinstance(body, dict):
                raise ae.AerialError("invalid_request", "body must be a JSON object", 422)
            extra = set(body) - RUN_KEYS
            if extra:
                raise ae.AerialError("invalid_request", f"unknown field(s): {sorted(extra)}", 422)
            if body.get("mode") not in ("recorded", "live"):
                raise ae.AerialError("invalid_request", "mode must be 'recorded' or 'live'", 422)
            sc = _scene(body.get("scene"))
            phrases = body.get("phrases")
            if body["mode"] == "recorded":
                return run.run_recorded(sd, sc, phrases, fixtures)
            if not _admin_ok(request):
                return _err("unauthorized", "a live SAM call requires the admin bearer token", 401)
            if not busy.acquire(blocking=False):
                return _err("scan_busy", "another aerial fetch or run is in progress", 429)
            try:
                return run.run_live(sd, sc, phrases, body.get("confirm_cost_usd"), **({"http": ctx["sam_http"]} if ctx.get("sam_http") else {}))
            finally:
                busy.release()
        except ae.AerialError as exc:
            return _fail(exc)

    app.state.satwatch_aerial_routes = True
