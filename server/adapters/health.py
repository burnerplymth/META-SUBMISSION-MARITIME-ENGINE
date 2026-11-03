"""Adapter health endpoint — FastAPI router for the API app to mount.

    from server.adapters.health import router as adapters_health_router
    app.include_router(adapters_health_router)   # exposes GET /health

Response shape: a list of per-adapter entries
``{adapter, state: LIVE|SNAPSHOT|DOWN, last_success_at, mode, note?}``
plus a summary count. States are *observed* from real fetch attempts
(registry health objects are mutated by BaseAdapter.fetch), never asserted.

Key-gated adapters: the state enum has no NEEDS-KEY value, so a
key-gated adapter with no key reports ``state=DOWN`` with
``note="NEEDS-KEY: ..."`` (documented choice).
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from . import registry

router = APIRouter(tags=["adapters-health"])


@router.get("/health")
def get_health() -> dict:
    adapters = registry.health_report()
    summary = {"LIVE": 0, "SNAPSHOT": 0, "DOWN": 0}
    for entry in adapters:
        summary[entry["state"]] = summary.get(entry["state"], 0) + 1
    return {"adapters": adapters, "summary": summary}


@router.get("/health/{adapter_name}")
def get_adapter_health(adapter_name: str):
    """One adapter's health entry.

    Unknown adapter contract: HTTP 404 with the standard
    error shape ``{"error": {"code": "unknown_adapter", "message":
    ...}}``. The 404 is returned as a JSONResponse (not a bare dict
    with a 200, as before) so the semantics hold however the router
    is mounted — including inside server/api/app.py, whose
    Starlette exception handler maps HTTP 404 to the same standard
    shape (code ``not_found``) for framework-raised errors; returning
    the response directly keeps this endpoint's more specific
    ``unknown_adapter`` code."""
    for entry in registry.health_report():
        if entry["adapter"] == adapter_name:
            return entry
    return JSONResponse(
        status_code=404,
        content={"error": {"code": "unknown_adapter",
                           "message": f"no adapter named {adapter_name!r}"}})
