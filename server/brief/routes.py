"""Brief routes (additive, mounted defensively from create_app):

  GET  /brief/status                 writer transport, model, caps, PDF renderer
  POST /brief/estimate               cost estimate for a request; no model call
  POST /brief                        write (or fetch) a brief from a run
  GET  /brief/{id}                   the stored brief
  GET  /brief/{id}/export?format=    html | pdf | json

Errors use the house shape {"error": {"code", "message"}}.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import json
import logging
import os
import threading

try:
    import envflag as _flags
except ImportError:  # imported as server.* from the repo root
    from server import envflag as _flags  # type: ignore

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from . import document, pdfout, render, writer
from .facts import AsgiFetch
from .model import PROMPT_NAME, BriefError, parse_request
from .store import ID_RE, BriefStore

log = logging.getLogger("supermap.brief")

_POOL: concurrent.futures.ThreadPoolExecutor | None = None
_POOL_LOCK = threading.Lock()
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_LOCK = threading.Lock()

DEFAULT_MAX_BRIEF_USD = 0.03


def _pool() -> concurrent.futures.ThreadPoolExecutor:
    global _POOL
    with _POOL_LOCK:
        if _POOL is None:
            try:
                n = max(1, int(os.environ.get("SUPERMAP_BRIEF_THREADS", "4")))
            except ValueError:
                n = 4
            _POOL = concurrent.futures.ThreadPoolExecutor(n, thread_name_prefix="brief")
        return _POOL


def _id_lock(brief_id: str) -> threading.Lock:
    with _LOCKS_LOCK:
        if len(_LOCKS) > 512:
            _LOCKS.clear()
        return _LOCKS.setdefault(brief_id, threading.Lock())


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status,
                        content={"error": {"code": code, "message": message}})


def _max_brief_usd() -> float:
    try:
        return float(os.environ.get("SUPERMAP_BRIEF_MAX_USD", DEFAULT_MAX_BRIEF_USD))
    except ValueError:
        return DEFAULT_MAX_BRIEF_USD


def _disabled() -> bool:
    return _flags.env_truthy("SUPERMAP_BRIEF_DISABLED")


def _llm_errors():
    try:
        from llm.provider import CallTooLarge, CapExceeded, ProviderDisabled, ProviderError
    except ImportError:  # imported as server.* from the repo root
        from server.llm.provider import (CallTooLarge, CapExceeded,  # type: ignore
                                         ProviderDisabled, ProviderError)
    return CallTooLarge, CapExceeded, ProviderDisabled, ProviderError


def _provider_for(request):
    try:
        from llm.router import provider_for_request
    except ImportError:  # imported as server.* from the repo root
        from server.llm.router import provider_for_request  # type: ignore
    return provider_for_request(request)


def register_brief_routes(app, ctx_in: dict | None = None, *, fetch=None, provider_factory=None) -> None:
    """``fetch`` and ``provider_factory`` are injection seams; production uses the
    app's own routes and the demo-gated provider."""
    try:
        from ports.sources import ctx_from_app
        from reports.data import ReportError, RunBundle
    except ImportError:  # imported as server.* from the repo root
        from server.ports.sources import ctx_from_app  # type: ignore
        from server.reports.data import ReportError, RunBundle  # type: ignore
    fetcher = fetch if fetch is not None else AsgiFetch(app)
    providers = provider_factory or _provider_for

    def store() -> BriefStore:
        return BriefStore(app.state.data_root)

    def _prepare(req: dict, provider):
        ctx = ctx_from_app(app)
        try:
            bundle = RunBundle(ctx, req["run_id"])
        except ReportError as exc:
            raise BriefError(exc.code, exc.message, exc.status)
        pv = provider.prompt_version(PROMPT_NAME)
        built, section_ids, brief_id = document.prepare(
            ctx, bundle, req, getattr(app.state, "brief_fetch", None) or fetcher, pv)
        return ctx, bundle, built, section_ids, brief_id

    def _estimate(req: dict, provider) -> dict:
        ctx, bundle, built, section_ids, brief_id = _prepare(req, provider)
        served = document.served_sections(built, section_ids)
        payload = writer.build_payload(served, built.facts.items)
        est = writer.estimate(provider, payload)
        cached = store().get(brief_id)
        live = provider.transport_name == "http"
        reuse = cached is not None and not req["regenerate"] and (
            cached.get("transport") == "LIVE" or not live)
        return {"brief_id": brief_id, "transport": "LIVE" if live else "RECORDED",
                "model": provider.model if live else None,
                "estimated_cost_usd": est["estimated_cost_usd"] if live else 0.0,
                "estimated_if_live_usd": est["estimated_cost_usd"],
                "ceiling_usd": min(_max_brief_usd(), provider.max_call_usd or _max_brief_usd()),
                "will_call_model": bool(live and not reuse), "cached": bool(reuse),
                "is_estimate": True}

    def _make(req: dict, provider) -> tuple[dict, bool]:
        ctx, bundle, built, section_ids, brief_id = _prepare(req, provider)
        live = provider.transport_name == "http"
        with _id_lock(brief_id):
            have = store().get(brief_id)
            if have is not None and not req["regenerate"] and (have.get("transport") == "LIVE" or not live):
                return have, True
            served = document.served_sections(built, section_ids)
            payload = writer.build_payload(served, built.facts.items)
            est = writer.estimate(provider, payload)
            CallTooLarge, CapExceeded, ProviderDisabled, ProviderError = _llm_errors()
            fallback = None
            draft = None
            res = None
            if live and est["estimated_cost_usd"] > _max_brief_usd():
                fallback = "call_too_large"
            else:
                try:
                    res = writer.call(provider, payload)
                except CapExceeded:
                    fallback = "spend_cap_reached"
                except CallTooLarge:
                    fallback = "call_too_large"
                except ProviderDisabled:
                    fallback = "llm_disabled"
                except ProviderError:
                    fallback = "provider_error"
                except Exception:
                    log.warning("brief writer failed", exc_info=True)
                    fallback = "provider_error"
            source = "model"
            unreadable = False
            if res is not None:
                from_text = res["text"]
                try:
                    from llm import provenance as prov
                except ImportError:
                    from server.llm import provenance as prov  # type: ignore
                pblock = prov.from_result(res, PROMPT_NAME)
                from .assemble import parse_draft
                draft, why = parse_draft(from_text)
                if draft is None:
                    fallback = why
                    source = "template"
                    unreadable = True
                if provider.transport_name == "fixture":
                    source = "template"
            else:
                try:
                    from llm import provenance as prov
                except ImportError:
                    from server.llm import provenance as prov  # type: ignore
                pblock = prov.make(model=None, transport_raw="fixture", prompt_name=PROMPT_NAME,
                                   prompt_version=provider.prompt_version(PROMPT_NAME),
                                   cost_usd=0.0, estimated_cost_usd=est["estimated_cost_usd"])
                pblock["model"] = None
                source = "template"
            if source == "template" and draft is None and not unreadable:
                from . import compose
                ids_map = {f["key"]: f["id"] for f in built.facts.items}
                draft = compose.compose_all([s["id"] for s in served], compose.Ids(ids_map))
            info = {"source": source, "provenance": pblock, "fallback_reason": fallback}
            doc = document.build_document(ctx, bundle, req, built, section_ids, brief_id,
                                          draft=draft, writer_info=info, now=ctx.now)
            store().put(doc)
            return doc, False

    async def _run(fn, *args):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(_pool(), contextvars.copy_context().run, fn, *args)

    async def _body(request: Request):
        try:
            return await request.json(), None
        except Exception:
            return None, _error(400, "bad_json", "request body must be JSON")

    def _translate(exc: Exception) -> JSONResponse:
        if isinstance(exc, BriefError):
            return _error(exc.status, exc.code, exc.message)
        CallTooLarge, CapExceeded, ProviderDisabled, ProviderError = _llm_errors()
        if isinstance(exc, CapExceeded):
            return _error(429, "spend_cap_reached", "The daily call limit is reached.")
        log.error("brief unexpected error", exc_info=exc)
        return _error(500, "internal_error", "internal error")

    @app.get("/brief/status")
    def brief_status(request: Request):
        provider = providers(request)
        ok, why = pdfout.available()
        live = provider.transport_name == "http"
        return {"enabled": not _disabled(), "transport": "LIVE" if live else "RECORDED",
                "model": provider.model if live else None,
                "prompt_id": f"{PROMPT_NAME}@v{provider.prompt_version(PROMPT_NAME)}",
                "ceiling_usd": _max_brief_usd(),
                "pdf": {"available": ok, "reason": None if ok else why}}

    @app.post("/brief/estimate")
    async def brief_estimate(request: Request):
        if _disabled():
            return _error(503, "brief_disabled", "Briefs are switched off.")
        body, bad = await _body(request)
        if bad is not None:
            return bad
        try:
            req = parse_request(body)
            provider = providers(request)
            return await _run(_estimate, req, provider)
        except Exception as exc:
            return _translate(exc)
    brief_estimate.__offloaded__ = True  # type: ignore[attr-defined]

    @app.post("/brief")
    async def brief_post(request: Request):
        if _disabled():
            return _error(503, "brief_disabled", "Briefs are switched off.")
        body, bad = await _body(request)
        if bad is not None:
            return bad
        try:
            req = parse_request(body)
            provider = providers(request)
            doc, cached = await _run(_make, req, provider)
        except Exception as exc:
            return _translate(exc)
        out = dict(doc)
        out["cached"] = cached
        return JSONResponse(status_code=200 if cached else 201, content=out,
                            headers={"Cache-Control": "no-store"})
    brief_post.__offloaded__ = True  # type: ignore[attr-defined]

    def _stored(brief_id: str):
        if not ID_RE.match(brief_id or ""):
            return None
        return store().get(brief_id)

    @app.get("/brief/{brief_id}")
    def brief_get(brief_id: str):
        doc = _stored(brief_id)
        if doc is None:
            return _error(404, "brief_not_found", "No such brief.")
        return JSONResponse(content=doc, headers={"Cache-Control": "no-store"})

    @app.get("/brief/{brief_id}/export")
    def brief_export(brief_id: str, request: Request):
        doc = _stored(brief_id)
        if doc is None:
            return _error(404, "brief_not_found", "No such brief.")
        fmt_ = request.query_params.get("format", "html")
        if fmt_ not in ("html", "pdf", "json"):
            return _error(422, "invalid_request", "format must be html, pdf or json")
        short = brief_id
        if fmt_ == "json":
            data = (json.dumps(doc, sort_keys=True, indent=1, ensure_ascii=False) + "\n").encode("utf-8")
            return Response(content=data, media_type="application/json",
                            headers={"Content-Disposition": f'attachment; filename="{short}.json"',
                                     "X-Content-Sha256": doc["sha256"]})
        page = render.render_html(doc)
        if fmt_ == "html":
            return Response(content=page, media_type="text/html; charset=utf-8",
                            headers={"Content-Disposition": f'attachment; filename="{short}.html"',
                                     "X-Content-Sha256": doc["sha256"],
                                     "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:"})
        ok, why = pdfout.available()
        if not ok:
            return _error(501, "renderer_unavailable", "No PDF renderer on this server.")
        cached = store().pdf_get(brief_id, doc["sha256"])
        if cached is None:
            try:
                cached = pdfout.render(page)
            except pdfout.Busy:
                return _error(429, "busy", "The PDF renderer is busy. Try again shortly.")
            except pdfout.PdfError:
                return _error(502, "pdf_failed", "The PDF could not be rendered.")
            store().pdf_put(brief_id, doc["sha256"], cached)
        return Response(content=cached, media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="{short}.pdf"',
                                 "X-Content-Sha256": doc["sha256"]})
