"""Ports tracker routes: GET /ports, /ports/summary, /ports/rule,
/ports/{place_id}. Registered from api.app.create_app via
register_ports_routes(app); read-only; errors in the house shape
{"error": {"code", "message"}}.
"""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from . import rules
from .board import Board, BoardError
from .cells import assert_cells_honest
from .feeds import iso
from .sources import ctx_from_app

MAX_LIMIT = 500
DEFAULT_LIMIT = 50


def error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status,
                        content={"error": {"code": code,
                                           "message": str(message)[:300]}})


def _multi(req: Request, name: str) -> list[str] | None:
    vals = []
    for raw in req.query_params.getlist(name):
        vals += [v.strip() for v in raw.split(",") if v.strip()]
    return vals or None


def _int(req: Request, name: str, default: int, lo: int, hi: int):
    raw = req.query_params.get(name)
    if raw is None:
        return default
    try:
        v = int(raw)
    except ValueError:
        raise BoardError("invalid_request", f"{name} must be an integer")
    if str(v) != raw.strip() or v < lo or v > hi:
        raise BoardError("invalid_request",
                         f"{name} must be an integer in {lo}..{hi}")
    return v


def _bool(req: Request, name: str):
    raw = req.query_params.get(name)
    if raw is None:
        return None
    if raw.lower() in ("1", "true", "yes"):
        return True
    if raw.lower() in ("0", "false", "no"):
        return False
    raise BoardError("invalid_request", f"{name} must be true or false")


def _board(app, req: Request) -> Board:
    ctx = ctx_from_app(app)
    tick = None
    if req.query_params.get("tick") is not None:
        tick = _int(req, "tick", 0, 0, 10 ** 9)
    run_id = req.query_params.get("run_id") or None
    sim_run_id = req.query_params.get("sim_run_id") or None
    if tick is not None and sim_run_id is None:
        raise BoardError("invalid_request", "tick needs a sim_run_id")
    return Board(ctx, run_id=run_id, sim_run_id=sim_run_id, tick=tick)


def register_ports_routes(app) -> None:
    @app.get("/ports/summary")
    def ports_summary(request: Request):
        try:
            board = _board(app, request)
            return {"schema": "ports/1", **board.summary()}
        except BoardError as exc:
            return error(exc.status, exc.code, exc.message)

    @app.get("/ports/rule")
    def ports_rule():
        return {"schema": "ports/1", "rule": rules.RULE}

    @app.get("/ports")
    def ports_list(request: Request):
        try:
            board = _board(app, request)
            limit = _int(request, "limit", DEFAULT_LIMIT, 0, MAX_LIMIT)
            offset = _int(request, "offset", 0, 0, 10 ** 9)
            filters = {k: _multi(request, k) for k in
                       ("mode", "kind", "tier", "region", "country",
                        "status")}
            q = request.query_params.get("q")
            has_events = _bool(request, "has_events")
            sort = request.query_params.get("sort", "name")
            order = request.query_params.get("order", "asc")
            res = board.list_rows(
                **filters, q=q, has_events=has_events, sort=sort,
                order=order, limit=limit, offset=offset)
        except BoardError as exc:
            return error(exc.status, exc.code, exc.message)
        body = {
            "schema": "ports/1", "as_of": iso(board.now),
            "run_id": board.run_id, "sim_run_id": board.sim_run_id,
            "tick": board.tick,
            "filters": {**{k: v for k, v in filters.items() if v},
                        **({"q": q} if q else {}),
                        **({"has_events": has_events}
                           if has_events is not None else {})},
            "sort": sort, "order": order,
            "rule": rules.RULE,
            "doctrine": DOCTRINE,
            **res,
        }
        assert_cells_honest(body)
        return body

    @app.get("/ports/{place_id}")
    def ports_detail(place_id: str, request: Request):
        try:
            board = _board(app, request)
            out = board.detail(place_id)
        except BoardError as exc:
            return error(exc.status, exc.code, exc.message)
        if out is None:
            return error(404, "unknown_place",
                         f"unknown place_id: {place_id!r}")
        return {"schema": "ports/1", "as_of": iso(board.now),
                "run_id": board.run_id, "sim_run_id": board.sim_run_id,
                "tick": board.tick, "doctrine": DOCTRINE, **out}

    # PORT TWIN (server/portsim): /ports/{place_id}/sim*. Additive and
    # defensive: a broken package leaves the tracker routes untouched.
    try:
        try:
            from portsim.routes import register_portsim_routes
        except ImportError:  # imported as server.* from the repo root
            from server.portsim.routes import register_portsim_routes
        register_portsim_routes(app)
    except Exception:
        import logging
        logging.getLogger(__name__).warning(
            "portsim mount skipped", exc_info=True)

    # Port Twin: /ports/geo/index, /ports/{id}/geo[/imagery],
    # /ports/{id}/profile. Additive; absent data is a 404 NOT SERVED.
    try:
        from .geo_routes import register_geo_routes
        register_geo_routes(app)
    except Exception:  # pragma: no cover - never take the ports API down
        import logging
        logging.getLogger(__name__).warning("port twin routes skipped",
                                            exc_info=True)


DOCTRINE = [
    "Every cell carries its own provenance chip and as-of; a cell with no "
    "data is NOT SERVED with a reason, never a zero.",
    "No total across places and no cross-class aggregate is computed: "
    "chain exposure is per place, in the place's own unit.",
    "Only CORE places are calibration-grade; SHELL places carry a labelled "
    "ESTIMATE or NOT PUBLISHED capacity.",
    "Status is the published PORTS-STATUS rule applied to served data; it "
    "is not a forecast.",
]
