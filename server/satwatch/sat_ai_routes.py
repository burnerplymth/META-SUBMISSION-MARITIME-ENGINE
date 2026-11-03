"""HTTP surface for SAM on a stored native crop. The Muse Spark
vision read of the same crop is served beside the LLM provider
(llm/satvision_routes.py); satwatch imports no llm module.

    GET  /satwatch/sam/plan?source=&place=&role=&prompt=
         what a SAM run would do and cost: recorded reply available, live
         state (AWAITING_META_KEY / READY / NOT_SERVED), estimate, remaining
         daily cap. Never calls a model.
    POST /satwatch/sam/run   {source, place, role?, prompt?, mode, confirm_cost_usd?}

``mode: "recorded"`` replays a stored real reply (free, public). ``mode:
"live"`` spends: it needs the admin bearer, a key, and ``confirm_cost_usd``
equal to the plan's estimate. Results are MODELLED and display-only; no
route here writes a scan record or an event. Errors: {error: {code, message}}.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import Request

from . import sat_ai as ai
from .routes import _admin_ok, _err

RUN_KEYS = {"source", "place", "role", "prompt", "mode", "confirm_cost_usd"}


def register_sat_ai_routes(app, ctx: dict) -> None:
    satwatch_dir = Path(ctx["satwatch_dir"])

    def _crop(source: Optional[str], place: Optional[str], role: Optional[str]) -> dict:
        return ai.resolve_crop(satwatch_dir, source or "", place or "", role)

    def _run_body(body) -> dict:
        if not isinstance(body, dict):
            raise ai.SatAiError("invalid_request", "body must be a JSON object", 422)
        extra = set(body) - RUN_KEYS
        if extra:
            raise ai.SatAiError("invalid_request", f"unknown field(s): {sorted(extra)}", 422)
        if body.get("mode") not in ("recorded", "live"):
            raise ai.SatAiError("invalid_request", "mode must be 'recorded' or 'live'", 422)
        return body

    def _fail(exc: ai.SatAiError):
        return _err(exc.code, str(exc), exc.status)

    @app.get("/satwatch/sam/plan", tags=["satwatch meta ai"],
             summary="SAM run plan for one stored crop: recorded or live, estimate, remaining cap")
    def get_sam_plan(source: str = "", place: str = "", role: Optional[str] = None,
                     prompt: Optional[str] = None):
        try:
            return ai.sam_plan(_crop(source, place, role), prompt)
        except ai.SatAiError as exc:
            return _fail(exc)

    @app.post("/satwatch/sam/run", tags=["satwatch meta ai"],
              summary="Run SAM 3.1 on one stored native crop (recorded free; live needs admin + cost confirmation)")
    def post_sam_run(request: Request, body: dict | None = None):
        try:
            body = _run_body(body)
            c = _crop(body.get("source"), body.get("place"), body.get("role"))
            if body["mode"] == "recorded":
                return ai.sam_run_recorded(c, body.get("prompt"))
            if not _admin_ok(request):
                return _err("unauthorized", "a live SAM call requires the admin bearer token", 401)
            return ai.sam_run_live(c, body.get("prompt"), body.get("confirm_cost_usd"))
        except ai.SatAiError as exc:
            return _fail(exc)

    app.state.satwatch_sat_ai_routes = True
