"""HTTP surface for the Muse Spark vision read of a stored crop.
Registered from ``create_app`` beside the satellite routes (satwatch itself
imports no llm module), at the satellite desk's own paths:

    GET  /satwatch/vision/plan?source=&place=&role=
         what a read would do and cost: recorded read available, live state
         (AWAITING_META_KEY / READY / NOT_SERVED), estimate at Meta's rates,
         LLM daily cap and what is left. Never calls a model.
    POST /satwatch/vision/run {source, place, role?, mode, confirm_cost_usd?}
         ``recorded`` replays a stored real read (free, public); ``live``
         spends and needs the admin bearer, the explicit http transport and
         ``confirm_cost_usd`` equal to the plan's estimate.

Errors: {error: {code, message}}. Results are MODELLED and display-only.
"""
from __future__ import annotations

import hmac
import os
from pathlib import Path
from typing import Optional

from fastapi import Request
from fastapi.responses import JSONResponse

try:
    from ..satwatch import sat_ai as ai
    from . import satvision as sv
except ImportError:                      # server/ on sys.path
    from satwatch import sat_ai as ai    # type: ignore
    from llm import satvision as sv      # type: ignore

ADMIN_TOKEN_ENV = "SUPERMAP_ADMIN_TOKEN"
RUN_KEYS = {"source", "place", "role", "mode", "confirm_cost_usd"}


def _err(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def _admin_ok(request: Request) -> bool:
    expected = os.environ.get(ADMIN_TOKEN_ENV) or ""
    header = request.headers.get("authorization", "")
    presented = header[len("Bearer "):] if header.startswith("Bearer ") else ""
    # bytes, not str: compare_digest raises TypeError on a non-ASCII str (a
    # client can send one: headers are latin-1 decoded) and that was a 500.
    return bool(expected and presented and hmac.compare_digest(
        presented.encode("utf-8"), expected.encode("utf-8")))


def register_satvision_routes(app, ctx: dict) -> None:
    satwatch_dir = Path(ctx["satwatch_dir"])

    def _crop(source: Optional[str], place: Optional[str], role: Optional[str]) -> dict:
        return ai.resolve_crop(satwatch_dir, source or "", place or "", role)

    @app.get("/satwatch/vision/plan", tags=["satwatch meta ai"],
             summary="Vision-read plan for one stored crop: recorded or live, estimate, remaining cap")
    def get_vision_plan(source: str = "", place: str = "", role: Optional[str] = None):
        try:
            return sv.vision_plan(_crop(source, place, role))
        except ai.SatAiError as exc:
            return _err(exc.code, str(exc), exc.status)

    @app.post("/satwatch/vision/run", tags=["satwatch meta ai"],
              summary="Muse Spark vision read of one stored native crop (recorded free; live needs admin + cost confirmation)")
    def post_vision_run(request: Request, body: dict | None = None):
        try:
            if not isinstance(body, dict):
                raise ai.SatAiError("invalid_request", "body must be a JSON object", 422)
            extra = set(body) - RUN_KEYS
            if extra:
                raise ai.SatAiError("invalid_request", f"unknown field(s): {sorted(extra)}", 422)
            if body.get("mode") not in ("recorded", "live"):
                raise ai.SatAiError("invalid_request", "mode must be 'recorded' or 'live'", 422)
            c = _crop(body.get("source"), body.get("place"), body.get("role"))
            if body["mode"] == "recorded":
                return sv.vision_run(c, mode="recorded")
            if not _admin_ok(request):
                return _err("unauthorized", "a live vision read requires the admin bearer token", 401)
            return sv.vision_run(c, mode="live", confirm_cost_usd=body.get("confirm_cost_usd"))
        except ai.SatAiError as exc:
            return _err(exc.code, str(exc), exc.status)

    app.state.llm_satvision_routes = True
