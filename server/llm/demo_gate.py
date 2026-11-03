"""Demo-token gate for the public link.

Problem: a public URL with a live Meta key lets any anonymous visitor spend
credits (bounded by per-IP buckets and the daily cap, but still spend). With
``SUPERMAP_DEMO_TOKEN`` set the product serves RECORDED answers to everyone
who does not present the token, on every route that can reach Meta:

    /llm/*  (classify, explain, ask, query, status)    LLMProvider
    /agent/*  (a session is created live or recorded)  LLMProvider.respond
    /voice/*  (transcribe, status)                     VoiceService
    background jobs (ledger autolock)                  LLMProvider
    SAM / vision live runs                             already admin-bearer only

A visitor presents the token as the ``X-Demo-Token`` header (scripts, smoke
checks) or by opening ``/demo/unlock?token=...`` once, which sets a
``SameSite=Strict`` HttpOnly cookie (a link the operator can hand out; no
web-app change is needed). A wrong or missing token is NOT an error: the
visitor simply gets the recorded answer, labelled RECORDED everywhere.

Fail-closed rules:
  * gate on (non-empty ``SUPERMAP_DEMO_TOKEN``) and no valid token -> recorded;
  * gate on and a code path that never looked at the request (a background
    thread) -> recorded;
  * gate off (unset or empty) -> unchanged behaviour (live when the
    transport is http).

The module is stateless (env + request only), so it behaves the same under
both import identities of the ``llm`` package.
"""
from __future__ import annotations

import hashlib
import hmac
import os

ENV_TOKEN = "SUPERMAP_DEMO_TOKEN"
HEADER = "x-demo-token"
COOKIE = "supermap_demo"
# Cookie lifetime: one week.
COOKIE_MAX_AGE_S = 7 * 24 * 3600
_COOKIE_CONTEXT = b"supermap-demo-cookie-v1"


def configured_token() -> str:
    return (os.environ.get(ENV_TOKEN) or "").strip()


MIN_TOKEN_LEN = 16


class DemoTokenError(RuntimeError):
    """The demo token is too short to protect a live deploy; the app refuses to start."""


def live_transport_selected(env=None) -> bool:
    """True when the deploy would reach Meta: the LLM or voice transport is http, or an LLM api key is set with no
    explicit transport (the provider then selects http by itself)."""
    env = os.environ if env is None else env
    llm = (env.get("SUPERMAP_LLM_TRANSPORT") or "").strip().lower()
    voice = (env.get("SUPERMAP_VOICE_TRANSPORT") or "").strip().lower()
    return (llm == "http" or voice == "http"
            or (not llm and bool((env.get("SUPERMAP_LLM_API_KEY") or "").strip())))


def check_token_strength(env=None) -> None:
    """Raise DemoTokenError when a demo token shorter than MIN_TOKEN_LEN is set and live mode is selected. The
    unlock link takes the token in a query string, so a short one is guessable; with live transport that means
    spend. Fixture-only deploys keep the warning (api/demo_routes.py). The message never contains the token."""
    env = os.environ if env is None else env
    tok = (env.get(ENV_TOKEN) or "").strip()
    if tok and len(tok) < MIN_TOKEN_LEN and live_transport_selected(env):
        raise DemoTokenError(
            f"{ENV_TOKEN} is only {len(tok)} characters and a live model transport is selected: refusing to start. "
            f"Use at least {MIN_TOKEN_LEN} random characters (for example `openssl rand -hex 16`), or run with "
            "SUPERMAP_LLM_TRANSPORT=fixture.")


ENV_ALLOW_OPEN = "SUPERMAP_ALLOW_OPEN_LIVE"


def check_gate_present(env=None) -> None:
    """Raise DemoTokenError when a live model transport is selected and no demo token is set:
    one forgotten secret would let every anonymous visitor spend the daily cap. An operator who really wants an
    open live deploy (a private network, a demo with no public link) sets SUPERMAP_ALLOW_OPEN_LIVE=1 (or
    true/yes/on). Fixture deploys never reach this."""
    env = os.environ if env is None else env
    if not live_transport_selected(env) or (env.get(ENV_TOKEN) or "").strip():
        return
    if _truthy((env.get(ENV_ALLOW_OPEN) or "")):
        return
    raise DemoTokenError(
        f"a live model transport is selected but {ENV_TOKEN} is not set: refusing to start, because every "
        "anonymous visitor would spend the daily cap. Set a demo token of at least "
        f"{MIN_TOKEN_LEN} random characters (for example `openssl rand -hex 16`), run with "
        f"SUPERMAP_LLM_TRANSPORT=fixture, or set {ENV_ALLOW_OPEN}=1 to run open on purpose.")


def _truthy(v: str) -> bool:
    return v.strip().lower() in ("1", "true", "yes", "on")


def gate_enabled() -> bool:
    return bool(configured_token())


def _eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8", "replace"),
                               b.encode("utf-8", "replace"))


def token_ok(presented) -> bool:
    """True iff ``presented`` equals the configured token (constant time).
    False when the gate is off: there is nothing to match."""
    expected = configured_token()
    if not expected or not isinstance(presented, str) or not presented:
        return False
    return _eq(presented.strip(), expected)


def cookie_value() -> str:
    """What the unlock cookie holds: an HMAC of the token, never the token,
    so a copied cookie value cannot be replayed as the header and rotating
    the token invalidates every cookie."""
    expected = configured_token()
    if not expected:
        return ""
    return hmac.new(expected.encode("utf-8"), _COOKIE_CONTEXT,
                    hashlib.sha256).hexdigest()


def cookie_ok(presented) -> bool:
    mine = cookie_value()
    return bool(mine) and isinstance(presented, str) and _eq(presented, mine)


def headers_unlocked(headers, cookies=None) -> bool:
    """``headers``: a case-insensitive mapping with ``.get``;
    ``cookies``: a plain mapping (or None)."""
    if not gate_enabled():
        return True
    if token_ok(headers.get(HEADER)):
        return True
    return cookie_ok((cookies or {}).get(COOKIE))


def request_unlocked(request) -> bool:
    """True when this request may reach LIVE Meta calls: the gate is off, or
    it carries the demo token (header) or the unlock cookie."""
    if not gate_enabled():
        return True
    return headers_unlocked(request.headers, getattr(request, "cookies", None))


def status_block(request) -> dict:
    return {"enabled": gate_enabled(),
            "unlocked": request_unlocked(request),
            "how": ("send X-Demo-Token, or open /demo/unlock?token=..."
                    if gate_enabled() else None)}


def internal_headers() -> list[tuple[bytes, bytes]]:
    """Header pair for an in-process (ASGI) call made on behalf of an
    UNLOCKED visitor, e.g. an agent session's tool calls. The token never
    leaves the process. Empty when the gate is off."""
    tok = configured_token()
    return [(HEADER.encode(), tok.encode("utf-8"))] if tok else []
