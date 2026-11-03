"""GET /exposure/sample, POST /exposure/evaluate, POST /exposure/export (additive, stateless, read-only).

Registered from create_app through one defensive hook. The routes write
nothing: the user's shipment list is read from the request, evaluated and returned (or turned into a file
and returned). Nothing is stored, and the only thing logged is a count.
"""
from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from . import export as export_mod
from . import model
from .evaluate import LABEL, evaluate, mc_state, place_index
from .sample import sample_document

log = logging.getLogger(__name__)
_MC_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="exposure-mc")


def _error(status: int, code: str, message: str, **extra) -> JSONResponse:
    body = {"error": {"code": code, "message": message}}
    if extra:
        body["detail"] = extra
    return JSONResponse(status_code=status, content=body)


def _fetch_mc(app, job_id: str):
    """The finished Monte Carlo job document through the app's own route, or None (not served)."""
    def _go():
        try:
            from agent.apicall import AsgiApi
        except ImportError:  # pragma: no cover
            from server.agent.apicall import AsgiApi  # type: ignore
        res = AsgiApi(app).get(f"/montecarlo/{job_id}")
        return res.body if res.status == 200 and isinstance(res.body, dict) else None
    try:
        return _MC_POOL.submit(_go).result(timeout=10)
    except Exception:
        return None


def register_exposure_routes(app, ctx: dict) -> None:
    graph = ctx["graph"]

    def _run(body, allow_format=False):
        req = model.parse_body(body, allow_format=allow_format)
        mc = None
        if req["mc_job_id"]:
            mc = mc_state(req["mc_job_id"], _fetch_mc(app, req["mc_job_id"]))
        doc = evaluate(graph, req, mc=mc)
        return req, doc

    async def _body(request: Request):
        try:
            return await request.json(), None
        except Exception:
            return None, _error(400, "bad_json", "request body must be JSON")

    @app.get("/exposure/sample")
    def exposure_sample():
        return JSONResponse(content=sample_document())

    @app.post("/exposure/resolve")
    async def exposure_resolve(request: Request):
        body, bad = await _body(request)
        if bad is not None:
            return bad
        try:
            items = model.parse_resolve(body)
        except model.Invalid as exc:
            return _error(exc.status, exc.code, exc.message, **exc.extra)
        idx = place_index(graph)
        results = []
        for it in items:
            got = idx.resolve(it["text"], it["mode"])
            if got["status"] == "resolved":
                results.append({"text": it["text"], "mode": it["mode"], "status": "resolved", "quality": got["quality"],
                                "place": idx.row(got["id"]), "suggestions": []})
            else:
                results.append({"text": it["text"], "mode": it["mode"], "status": "unresolved",
                                "reason": got.get("reason", "no_match"), "quality": None, "place": None,
                                "suggestions": got.get("suggestions", [])})
        return Response(content=json.dumps({"results": results}, separators=(",", ":"), ensure_ascii=False).encode("utf-8"),
                        media_type="application/json", headers={"Cache-Control": "no-store"})

    @app.post("/exposure/evaluate")
    async def exposure_evaluate(request: Request):
        body, bad = await _body(request)
        if bad is not None:
            return bad
        try:
            req, doc = _run(body)
        except model.Invalid as exc:
            return _error(exc.status, exc.code, exc.message, **exc.extra)
        log.info("exposure evaluate: %d shipments, %d lanes", doc["counts"]["shipments"], doc["counts"]["unique_lanes"])
        return Response(content=json.dumps(doc, separators=(",", ":"), ensure_ascii=False).encode("utf-8"),
                        media_type="application/json", headers={"Cache-Control": "no-store"})

    @app.post("/exposure/export")
    async def exposure_export(request: Request):
        body, bad = await _body(request)
        if bad is not None:
            return bad
        try:
            req, doc = _run(body, allow_format=True)
        except model.Invalid as exc:
            return _error(exc.status, exc.code, exc.message, **exc.extra)
        try:
            if req["format"] == "csv":
                data, ctype, ext = export_mod.build_csv(req, doc), "text/csv; charset=utf-8", "csv"
            else:
                data, ctype, ext = (export_mod.build_xlsx(req, doc),
                                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx")
        except Exception:
            log.warning("exposure export failed")
            return _error(500, "export_unavailable", "The file could not be built.")
        return Response(content=data, media_type=ctype,
                        headers={"Content-Disposition": f'attachment; filename="shipment-exposure.{ext}"',
                                 "Cache-Control": "no-store", "X-Exposure-Label": LABEL})
