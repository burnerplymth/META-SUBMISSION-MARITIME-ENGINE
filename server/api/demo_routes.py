"""``/demo/*``: the unlock link for the demo-token gate (see llm/demo_gate.py).

    GET  /demo/status                 {enabled, unlocked}
    GET  /demo/unlock?token=...       sets the unlock cookie (SameSite=Strict,
                                      HttpOnly, Secure over https) and answers
                                      303 to "/" -- always "/", never a
                                      caller-supplied target (no open redirect)
    POST /demo/lock                   clears the cookie

With the gate off (``SUPERMAP_DEMO_TOKEN`` unset) ``/demo/unlock`` is a 404:
there is nothing to unlock. A wrong token is a 403 and sets nothing.
"""
from __future__ import annotations

import logging

from fastapi import Request
from fastapi.responses import JSONResponse, Response

try:
    from llm import demo_gate
except ImportError:  # imported as server.api.* from the repo root
    from server.llm import demo_gate  # type: ignore

_NO_STORE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


def _err(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, headers=_NO_STORE,
                        content={"error": {"code": code, "message": message}})


def _secure(request: Request) -> bool:
    from .security import cookie_secure
    return cookie_secure(request)


def register_demo_routes(app) -> None:
    demo_gate.check_token_strength()      # raises DemoTokenError: a short token never starts live mode
    demo_gate.check_gate_present()        # raises DemoTokenError: live transport with no token, unless opted out
    tok = demo_gate.configured_token()
    if tok and len(tok) < 16:
        logging.getLogger(__name__).warning(
            "the demo token is only %d characters: use at least 16 random characters "
            "(the unlock link is guessable otherwise)", len(tok))

    @app.get("/demo/status", include_in_schema=False)
    def demo_status(request: Request):
        return JSONResponse(demo_gate.status_block(request), headers=_NO_STORE)

    @app.get("/demo/unlock", include_in_schema=False)
    def demo_unlock(request: Request, token: str = ""):
        if not demo_gate.gate_enabled():
            return _err(404, "not_found", "resource not found")
        if not demo_gate.token_ok(token):
            return _err(403, "forbidden", "that demo token is not valid")
        resp = Response(status_code=303, headers={**_NO_STORE, "Location": "/"})
        resp.set_cookie(demo_gate.COOKIE, demo_gate.cookie_value(),
                        max_age=demo_gate.COOKIE_MAX_AGE_S, httponly=True,
                        samesite="strict", path="/", secure=_secure(request))
        return resp

    @app.post("/demo/lock", include_in_schema=False)
    def demo_lock():
        resp = JSONResponse({"unlocked": False}, headers=_NO_STORE)
        resp.delete_cookie(demo_gate.COOKIE, path="/")
        return resp
