"""POST /lanes/plan: the lane planner route (additive, read-only).

Registered from create_app through one defensive hook. The route writes nothing: the plan is a pure
function of the request and the sidecars on disk, and its response carries the
journal (normalised inputs, their digest, the file and table hashes) from which
it can be re-derived byte for byte.
"""
from __future__ import annotations

import json

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from .planner import LaneRefusal, plan, to_bytes

_MEMO: dict = {}
_MEMO_MAX = 48


def _scenarios(ctx) -> list:
    got = ctx.get("scenarios")
    if got is not None:
        return got() if callable(got) else got
    try:
        from api.chain_loop import _SCENARIOS
    except Exception:  # pragma: no cover - imported as server.api.* from the repo root
        try:
            from server.api.chain_loop import _SCENARIOS  # type: ignore
        except Exception:
            return []
    return list(_SCENARIOS)


def register_lane_plan_routes(app, ctx: dict) -> None:
    graph = ctx["graph"]
    err = ctx["err"]

    @app.post("/lanes/plan")
    async def lanes_plan(req: Request):
        try:
            body = await req.json()
        except Exception:
            return err("bad_json", "request body must be JSON")
        try:
            key = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
        except Exception:
            return err("invalid_request", "request body must be JSON")
        hit = _MEMO.get((id(graph), key))
        if hit is not None:
            return Response(content=hit, media_type="application/json")
        try:
            doc = plan(graph, body, _scenarios(ctx))
        except LaneRefusal as exc:
            content = {"error": {"code": exc.code, "message": exc.message},
                       "refusal": {"reason": exc.code, **exc.extra}}
            return JSONResponse(status_code=422, content=content)
        data = to_bytes(doc)
        if len(_MEMO) >= _MEMO_MAX:
            _MEMO.clear()
        _MEMO[(id(graph), key)] = data
        return Response(content=data, media_type="application/json")
