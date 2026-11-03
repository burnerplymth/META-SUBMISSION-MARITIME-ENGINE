"""FastAPI routers for the LLM surfaces (classify, explain, ask, ledger and burn status).

The API app mounts these in server/api/app.py. Mount points — add to the app factory:

    from llm.router import router as llm_router   # server/ on sys.path
    app.include_router(llm_router)                 # prefix already "/llm"

Endpoints:
    POST /llm/classify        {text, source, retrieved_at}      -> candidate
    POST /llm/classify/preview{text, source, retrieved_at}      -> cost preview
    POST /llm/query           {text}                            -> {text, filters}
                              (NL -> structured filters only, closed schema)
    POST /llm/explain         {run_id}                          -> narrative
    POST /llm/ask             {question, scope_node_ids,        -> answer
                               run_id?}      (run_id: chain-grounded, post-checked)
    POST /llm/ask/preview     same body                         -> cost estimate
                                                (or the free refusal; no model call)
    GET  /llm/status                                            -> burn meter
    POST /llm/ledger/lock     {disruption_id, run_id,           -> locked entry
                               supersedes?}        (admin auth)
    POST /llm/ledger/grade    {entry_id, observation_ids}       -> grade (rule v1
                               (admin auth)               +)
    GET  /llm/ledger                                            -> merged entries
    GET  /llm/ledger/stats                                      -> counts, oldest unresolved
    GET  /llm/ledger/public                                     -> public HTML page

The two ledger WRITE routes
require an admin bearer token — `Authorization: Bearer <token>` where
the token is the SUPERMAP_ADMIN_TOKEN environment variable, compared
with hmac.compare_digest; a missing/wrong token (or the env var being
unset — fail closed) is answered 401 `unauthorized`. Lock does not
accept a caller-supplied chain: the request carries (disruption_id,
run_id) and the server re-derives predicted_chain from its own run
store; a request still carrying a predicted_chain / run / disruption
field is rejected 422, as are an unknown run_id and a
disruption_id that does not resolve in the server's event store.
Grade accepts only server-side records referenced by id:
observation_ids must resolve in the server's event store (the
records the server itself ingested — the adapter records store
carries no observed-delay quantification, so it cannot grade), and
every grading field (node, observed delay, observed_at, provenance)
is taken from the resolved record via ledger.observation_from_event;
an unknown observation id is 422. Server-side jobs that call
LedgerStore directly (autolock, seed_ledger) are unaffected — they
never went through these routes.

Error contract (standard shape, one shape
everywhere — {error: {code, message}}, never a bare 500 plain-text):

    This router produces the standard shape ITSELF: every handler runs
    through _respond(), which validates the request body and translates
    domain failures into JSONResponses carrying the shape. That single
    mechanism works identically when the router is mounted in the API
    app and when it is used standalone on a bare FastAPI app (no
    app-level exception handler is required; the API additionally
    registers handlers for LLMRequestError/LedgerError as a safety net
    for any llm exception raised outside these handlers).

    Code mapping (stable codes; HTTP status in parentheses):
      bad_json                (400) body is not valid JSON
      run_not_found           (404) /llm/explain names no stored run
      entry_not_found         (404) /llm/ledger/grade names no entry
      invalid_request         (422) missing / wrong-typed body fields,
                                    or a ValueError/KeyError escaping a
                                    domain call on malformed input
      ledger_invalid          (422) LedgerError not otherwise mapped
                                    (no disruption id, empty/gap chain,
                                    chain/run_id mismatch, ...)
      grade_without_evidence  (422) grade carries no evidence-backed
                                    observation with full provenance
      entry_already_exists    (409) lock reuses an existing entry_id
      already_graded          (409) grade-once rule: entry already graded
      lock_before_knowable    (409) grading evidence retrieved at or
                                    before the entry's locked_at
      spend_cap_reached       (429) provider daily-cap guardrail hit
      provider_error          (502) the LLM provider/transport failed
      llm_disabled            (503) the kill switch (SUPERMAP_LLM_DISABLED) is on
      spend_store_unreadable  (503) the spend ledger cannot be read (fail closed)
      unauthorized            (401) ledger write route without the
                                    admin bearer token
      disruption_already_locked (409) second lock for one disruption_id
                                    without supersedes
      internal_error          (500) anything else — still standard shape

Runs-dir seam: build_router(runs_dir=...) returns a router
whose Explainer resolves run records from THAT runs directory FIRST
(via an injected chain fetcher, falling back to the package default
store so pre-existing runs there stay explainable) and whose
AskTheMap scans the same directory for run ids — the API's
create_app passes its own runs_dir so a run the API just created can
be explained. The module-level ``router`` keeps the llm package's
standalone default behaviour (runs resolved from the package default
store, data/runs/).

This module stays importable without FastAPI installed (router = None) so
the whole LLM layer is testable standalone.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import hmac
import json
import logging
import os
import threading
from pathlib import Path

try:
    from fastapi import APIRouter, Request
    from fastapi.concurrency import run_in_threadpool
    from fastapi.responses import HTMLResponse, JSONResponse
except Exception:  # pragma: no cover - FastAPI not installed
    APIRouter = None  # type: ignore

from .ask import AskTheMap
from .classifier import Classifier
from .explain import Explainer, canonical_stream_hash, default_chain_fetcher
try:  # The ONE materialise_edges (cycle-free leaf)
    from ..chain_edges import materialise_edges
except Exception:  # imported as a top-level package (server/ on sys.path)
    from chain_edges import materialise_edges  # type: ignore
from . import demo_gate
from .ledger import LedgerError, LedgerStore, observation_from_event
from .ledger_page import render_page
from .query import NLQuery
from .provider import (CallTooLarge, CapExceeded, LLMProvider, ProviderDisabled, ProviderError,
                       ProviderRateLimited, SpendStoreError)

log = logging.getLogger("supermap.llm.router")

# A provider call (HttpTransport -> urllib, up to
# 60 s) must never run on the event loop. Handlers hand their blocking
# body to this dedicated, bounded pool; an excess of concurrent LLM calls
# queues here instead of occupying the shared threadpool that serves
# every other route.
_LLM_POOL: concurrent.futures.ThreadPoolExecutor | None = None
_LLM_POOL_LOCK = threading.Lock()


def _llm_pool() -> concurrent.futures.ThreadPoolExecutor:
    global _LLM_POOL
    with _LLM_POOL_LOCK:
        if _LLM_POOL is None:
            try:
                n = max(1, int(os.environ.get("SUPERMAP_LLM_THREADS", "8")))
            except ValueError:
                n = 8
            _LLM_POOL = concurrent.futures.ThreadPoolExecutor(
                n, thread_name_prefix="llm")
        return _LLM_POOL


async def _off_loop(fn, *args):
    loop = asyncio.get_running_loop()
    ctx = contextvars.copy_context()
    return await loop.run_in_executor(_llm_pool(), ctx.run, fn, *args)


# Stable error code -> HTTP status (see module docstring for semantics).
ERROR_STATUS = {
    "bad_json": 400,
    "run_not_found": 404,
    "entry_not_found": 404,
    "invalid_request": 422,
    "ledger_invalid": 422,
    "grade_without_evidence": 422,
    "entry_already_exists": 409,
    "already_graded": 409,
    "lock_before_knowable": 409,
    "disruption_already_locked": 409,
    "unauthorized": 401,
    "spend_cap_reached": 429,
    "call_too_large": 422,
    "provider_error": 502,
    "provider_rate_limited": 503,
    "llm_disabled": 503,
    "spend_store_unreadable": 503,
    "internal_error": 500,
}


class LLMRequestError(Exception):
    """A typed client/domain error carrying a stable code.

    Raised by the validation helpers below; handlers translate it (and
    the llm package's domain errors) into the standard error shape.
    Public so the API app can register a safety-net exception handler
    for it as well.
    """

    def __init__(self, code: str, message: str, status: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status if status is not None \
            else ERROR_STATUS.get(code, 422)


def error_body(code: str, message: str) -> dict:
    """The standard error body."""
    return {"error": {"code": code, "message": message}}


def _error_response(code: str, message: str,
                    status: int | None = None) -> "JSONResponse":
    return JSONResponse(
        status_code=status if status is not None
        else ERROR_STATUS.get(code, 422),
        content=error_body(code, message))


def classify_ledger_error(exc: LedgerError) -> tuple[str, int]:
    """Map a LedgerError to (stable code, HTTP status) by its message.

    LedgerStore's messages are its public failure surface (see
    ledger.py); anything unrecognised is a generic ledger rule
    violation -> 422 ledger_invalid.
    """
    msg = str(exc)
    if msg.startswith("unknown entry_id:"):
        return "entry_not_found", 404
    if "is already graded" in msg:
        return "already_graded", 409
    if "is already locked by" in msg:
        return "disruption_already_locked", 409
    if msg.startswith("entry_id already exists:"):
        return "entry_already_exists", 409
    if msg.startswith("LOCK-BEFORE-KNOWABLE violated"):
        return "lock_before_knowable", 409
    if "grade without evidence does not publish" in msg:
        return "grade_without_evidence", 422
    return "ledger_invalid", 422


# ----- provider singleton -----------------------------------------

def _provider_env_fingerprint() -> tuple:
    """Fingerprint of the env vars that shape an LLMProvider: every
    SUPERMAP_LLM_* var, plus the prompts-dir and graph overrides the
    provider also reads. Any change -> a fresh provider."""
    keys = sorted(k for k in os.environ
                  if k.startswith("SUPERMAP_LLM_")
                  or k in ("SUPERMAP_PROMPTS_DIR", "SUPERMAP_GRAPH"))
    return tuple((k, os.environ[k]) for k in keys)


_provider_singleton: dict = {"fingerprint": None, "provider": None}
_provider_singleton_lock = threading.Lock()


# Demo-token gate (demo_gate.py): True only inside a request that may reach
# LIVE Meta calls. Default False so that, with the gate on, any code path that
# never looked at a request (a background thread) is served RECORDED.
_UNLOCKED: contextvars.ContextVar = contextvars.ContextVar(
    "supermap_llm_unlocked", default=False)

_recorded_singleton: dict = {"fingerprint": None, "provider": None}


def _recorded_provider() -> LLMProvider:
    """The shared fixture provider that answers visitors without the demo
    token (own spend file, no cap)."""
    fingerprint = _provider_env_fingerprint()
    with _provider_singleton_lock:
        if _recorded_singleton["provider"] is None \
                or _recorded_singleton["fingerprint"] != fingerprint:
            _recorded_singleton["provider"] = LLMProvider.recorded()
            _recorded_singleton["fingerprint"] = fingerprint
        return _recorded_singleton["provider"]


def _gated_provider() -> LLMProvider:
    """The provider this request may use: live only when the demo gate is off
    or the request holds the token."""
    if demo_gate.gate_enabled() and not _UNLOCKED.get():
        return _recorded_provider()
    return _shared_provider()


def provider_for_request(request) -> LLMProvider:
    """The provider a request may use, for routes outside /llm (the brief):
    live only when the demo gate is off or the request holds the token."""
    if demo_gate.gate_enabled() and not demo_gate.request_unlocked(request):
        return _recorded_provider()
    return _shared_provider()


def _shared_provider() -> LLMProvider:
    """One provider per process, keyed by the env
    fingerprint. Rebuilding LLMProvider.from_env() on every /llm/* request
    would re-read prompts and the 5.4 MB graph (twice per classify); a
    change of LLM env changes the fingerprint and yields a fresh provider."""
    fingerprint = _provider_env_fingerprint()
    with _provider_singleton_lock:
        if _provider_singleton["provider"] is None \
                or _provider_singleton["fingerprint"] != fingerprint:
            _provider_singleton["provider"] = LLMProvider.from_env()
            _provider_singleton["fingerprint"] = fingerprint
        return _provider_singleton["provider"]


# ----- admin auth -------------------------------------------------

ADMIN_TOKEN_ENV = "SUPERMAP_ADMIN_TOKEN"


def _require_admin(request: "Request") -> None:
    """Ledger write routes require the admin bearer token from
    the SUPERMAP_ADMIN_TOKEN env var, compared in constant time. Fail
    closed: an unset env var authorises nobody."""
    expected = os.environ.get(ADMIN_TOKEN_ENV) or ""
    header = request.headers.get("authorization", "")
    presented = header[len("Bearer "):] if header.startswith("Bearer ") \
        else ""
    if not expected or not presented \
            or not hmac.compare_digest(presented.encode("utf-8"),
                                       expected.encode("utf-8")):
        raise LLMRequestError(
            "unauthorized",
            "ledger writes require the admin bearer token "
            f"({ADMIN_TOKEN_ENV})", 401)


def _is_admin(request: "Request") -> bool:
    try:
        _require_admin(request)
    except LLMRequestError:
        return False
    return True


def _admin_gate(request: "Request"):
    """None when authorised, else the standard-shape 401 response."""
    try:
        _require_admin(request)
    except LLMRequestError as exc:
        return _error_response(exc.code, exc.message, exc.status)
    return None


def _default_events_path() -> Path:
    return Path(os.environ.get(
        "SUPERMAP_EVENTS_FILE",
        str(Path(__file__).resolve().parents[2] / "data" / "events.jsonl")))


def _load_events(events_path: Path) -> dict:
    """The server's event store as {id: record}. Read fresh on every
    write request (ingestion mints new events between requests; the
    file is small). A missing/unreadable store resolves nothing —
    every reference then fails closed with 422."""
    events: dict = {}
    try:
        text = events_path.read_text(encoding="utf-8")
    except Exception:
        return events
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if isinstance(rec, dict) and rec.get("id"):
            events[rec["id"]] = rec
    return events


# ----- request-body validation -------------------------------------------

async def _json_body(request: "Request") -> dict:
    try:
        body = await request.json()
    except Exception:
        raise LLMRequestError("bad_json", "request body must be JSON")
    if not isinstance(body, dict):
        raise LLMRequestError("invalid_request",
                              "request body must be a JSON object")
    return body


def _require_str(body: dict, field: str) -> str:
    value = body.get(field)
    if not isinstance(value, str) or not value:
        raise LLMRequestError(
            "invalid_request",
            f"{field} is required and must be a non-empty string")
    return value


def _require_dict(body: dict, field: str) -> dict:
    value = body.get(field)
    if not isinstance(value, dict):
        raise LLMRequestError(
            "invalid_request", f"{field} is required and must be an object")
    return value


def _require_list(body: dict, field: str) -> list:
    value = body.get(field)
    if not isinstance(value, list):
        raise LLMRequestError(
            "invalid_request", f"{field} is required and must be an array")
    return value


def _opt_str_list(body: dict, field: str) -> list:
    value = body.get(field, [])
    if not isinstance(value, list) \
            or any(not isinstance(v, str) for v in value):
        raise LLMRequestError(
            "invalid_request", f"{field} must be an array of strings")
    return value


# ----- runs-dir chain fetcher (seam fix) ----------------------------------

def make_chain_fetcher(runs_dir: str | Path):
    """A chain fetcher like explain.default_chain_fetcher, but reading
    run records from ``runs_dir`` FIRST and falling back to the package
    default store (data/runs/): a run the API just created in its own
    runs dir resolves, and pre-existing default-store runs stay
    explainable through an app mounted with a different runs dir.
    Raises LookupError for a run_id in neither store, exactly like the
    default fetcher."""
    runs = Path(runs_dir)

    def fetch(run_id: str) -> dict:
        if (not isinstance(run_id, str) or not run_id
                or len(run_id) > 128
                or "/" in run_id or "\\" in run_id or ".." in run_id
                or "\x00" in run_id):
            raise LookupError(
                f"unknown run_id: {run_id} (no stored run record)")
        path = runs / f"{run_id}.json"
        if not path.exists():
            return default_chain_fetcher(run_id)
        rec = json.loads(path.read_text(encoding="utf-8"))
        events = rec.get("events", [])
        edges = materialise_edges(
            events,
            rec.get("journal", {}).get("disruption", {}).get("node_id", ""))
        return {
            "run_id": rec.get("run_id", run_id),
            "status": rec.get("status", "complete"),
            "event_count": len(events),
            "stream_hash": rec.get("stream_hash")
            or canonical_stream_hash(events),
            "events": events,
            "edges": edges,
        }

    return fetch


# ----- the one translation mechanism --------------------------------------

def _translate(exc: Exception) -> "JSONResponse":
    if isinstance(exc, LLMRequestError):
        return _error_response(exc.code, exc.message, exc.status)
    if isinstance(exc, LedgerError):
        code, status = classify_ledger_error(exc)
        return _error_response(code, str(exc), status)
    if isinstance(exc, KeyError):
        return _error_response(
            "invalid_request", f"missing or invalid field: {exc}", 422)
    if isinstance(exc, LookupError):
        return _error_response("run_not_found", str(exc), 404)
    if isinstance(exc, CapExceeded):
        return _error_response("spend_cap_reached", str(exc), 429)
    if isinstance(exc, CallTooLarge):
        return _error_response("call_too_large", str(exc), 422)
    if isinstance(exc, ProviderRateLimited):
        return _error_response("provider_rate_limited", str(exc), 503)
    # A switched-off or fail-closed provider is a state of the service, not an upstream fault.
    if isinstance(exc, ProviderDisabled):
        return _error_response("llm_disabled", "Meta AI calls are switched off on this server", 503)
    if isinstance(exc, SpendStoreError):
        return _error_response("spend_store_unreadable", "the spend store cannot be read; calls are refused", 503)
    if isinstance(exc, ProviderError):
        return _error_response("provider_error", str(exc), 502)
    if isinstance(exc, (TypeError, ValueError)):
        return _error_response(
            "invalid_request", str(exc) or exc.__class__.__name__, 422)
    # Never echo the exception text (paths, internals): log it, answer
    # with a fixed message.
    log.error("llm router unexpected error", exc_info=exc)
    return _error_response("internal_error", "internal error", 500)


async def _respond(request: "Request", handler) -> object:
    """Run one POST handler: parse + validate the JSON body, call the
    handler, and translate ANY failure into the standard error shape."""
    ctx_token = _UNLOCKED.set(demo_gate.request_unlocked(request))
    try:
        body = await _json_body(request)
        return await _off_loop(handler, body)
    except Exception as exc:  # translated, never a bare 500
        return _translate(exc)
    finally:
        _UNLOCKED.reset(ctx_token)


def _respond_get(handler) -> object:  # GET routes are plain ``def``: the
    # framework already runs them on its threadpool.
    try:
        return handler()
    except Exception as exc:  # translated, never a bare 500
        return _translate(exc)


def build_router(runs_dir: str | Path | None = None,
                 ledger_path: str | Path | None = None,
                 events_path: str | Path | None = None) -> "APIRouter":
    """Build the /llm router.

    ``runs_dir`` (optional): resolve /llm/explain run records (and
    /llm/ask run ids) from this runs directory instead of the package
    default store — the API passes its own runs_dir here. ``ledger_path``
    (optional): use this ledger store instead of data/ledger.jsonl;
    the default keeps the package's standalone behaviour.
    ``events_path`` (optional): the server's event store the ledger
    write routes resolve disruption/observation references against
    (/); defaults to SUPERMAP_EVENTS_FILE or
    data/events.jsonl.
    """
    if APIRouter is None:  # pragma: no cover - FastAPI not installed
        return None
    runs = Path(runs_dir) if runs_dir is not None else None
    events_file = Path(events_path) if events_path is not None \
        else _default_events_path()
    r = APIRouter(prefix="/llm", tags=["llm"])

    def _provider() -> LLMProvider:
        return _gated_provider()

    def _explainer() -> Explainer:
        if runs is not None:
            return Explainer(_provider(), chain_fetcher=make_chain_fetcher(runs))
        return Explainer(_provider())

    def _ledger() -> LedgerStore:
        return LedgerStore(Path(ledger_path)) if ledger_path is not None \
            else LedgerStore()

    _tiers_cache: dict | None = None

    def _node_tiers() -> dict:
        """node_id -> tier from the served graph (SUPERMAP_GRAPH env or
        the repo default — the same resolution autolock uses), loaded
        lazily and cached. An unreadable graph yields {} (no tier data:
        LedgerStore.lock then applies no tier guard; the autolock path,
        the production minter, always passes its own map)."""
        nonlocal _tiers_cache
        if _tiers_cache is None:
            _tiers_cache = {}
            try:
                import os
                gp = Path(os.environ.get(
                    "SUPERMAP_GRAPH",
                    str(Path(__file__).resolve().parents[2]
                        / "data" / "graph" / "graph.json")))
                doc = json.loads(gp.read_text(encoding="utf-8"))
                _tiers_cache = {n["id"]: n.get("tier", "core")
                                for n in doc.get("nodes", [])}
            except Exception:
                _tiers_cache = {}
        return _tiers_cache

    @r.post("/classify")
    async def classify(request: Request):
        def handle(body: dict) -> dict:
            return Classifier(_provider()).classify(
                _require_str(body, "text"), _require_str(body, "source"),
                _require_str(body, "retrieved_at"))
        return await _respond(request, handle)

    @r.post("/classify/preview")
    async def classify_preview(request: Request):
        def handle(body: dict) -> dict:
            return Classifier(_provider()).cost_preview(
                _require_str(body, "text"), _require_str(body, "source"),
                _require_str(body, "retrieved_at"))
        return await _respond(request, handle)

    @r.post("/query")
    async def nl_query(request: Request):
        """NL text -> structured filters ONLY (closed schema, post-checked;
        see llm/query.py). Never an answer, never an entity."""
        def handle(body: dict) -> dict:
            return NLQuery(_provider()).resolve(_require_str(body, "text"))
        return await _respond(request, handle)

    @r.post("/explain")
    async def explain(request: Request):
        def handle(body: dict) -> dict:
            return _explainer().explain(_require_str(body, "run_id"))
        return await _respond(request, handle)

    def _asker() -> AskTheMap:
        fetcher = make_chain_fetcher(runs) if runs is not None \
            else default_chain_fetcher
        return AskTheMap(_provider(), runs_dir=runs, chain_fetcher=fetcher) \
            if runs is not None else AskTheMap(_provider(), chain_fetcher=fetcher)

    def _opt_run_id(body: dict) -> str | None:
        value = body.get("run_id")
        if value is None:
            return None
        if not isinstance(value, str) or not value:
            raise LLMRequestError(
                "invalid_request", "run_id must be a non-empty string")
        return value

    @r.post("/ask")
    async def ask(request: Request):
        """{question, scope_node_ids, run_id?}. With a run_id the question is
        grounded in that stored chain and the answer is post-checked against
        its step records (see llm/ask.py)."""
        def handle(body: dict) -> dict:
            question = _require_str(body, "question")
            scope = _opt_str_list(body, "scope_node_ids")
            return _asker().ask(question, scope, _opt_run_id(body))
        return await _respond(request, handle)

    @r.post("/ask/preview")
    async def ask_preview(request: Request):
        """Same body as /ask; makes NO model call. The pre-call cost estimate
        (``refused: false``) or, when the question would be refused, the
        refusal shape itself (free)."""
        def handle(body: dict) -> dict:
            question = _require_str(body, "question")
            scope = _opt_str_list(body, "scope_node_ids")
            return _asker().cost_preview(question, scope, _opt_run_id(body))
        return await _respond(request, handle)

    @r.get("/status")
    def status(request: Request) -> dict:
        def handle() -> dict:
            token = _UNLOCKED.set(demo_gate.request_unlocked(request))
            try:
                out = _provider().status()
            finally:
                _UNLOCKED.reset(token)
            out["demo_gate"] = demo_gate.status_block(request)
            # The spend, caps, model and transport label are public by design (the burn meter and the model
            # chip read them; a locked visitor sees the recorded provider's $0). The upstream host is not:
            # only the admin bearer, or a visitor holding the demo token while the gate is on, reads it
            if not (_is_admin(request) or (demo_gate.gate_enabled() and demo_gate.request_unlocked(request))):
                out["endpoint_host"] = None
            return out
        return _respond_get(handle)

    @r.post("/ledger/lock")
    async def ledger_lock(request: Request):
        gate = _admin_gate(request)
        if gate is not None:
            return gate

        def handle(body: dict) -> dict:
            # A caller-supplied chain (in any shape) is rejected outright — the server re-derives the
            # predicted chain from its own run store by run_id.
            for field in ("predicted_chain", "run", "disruption"):
                if field in body:
                    raise LedgerError(
                        f"lock no longer accepts a caller-supplied "
                        f"{field!r}: send disruption_id + "
                        "run_id and the server re-derives the "
                        "predicted chain from the run store")
            disruption_id = _require_str(body, "disruption_id")
            run_id = _require_str(body, "run_id")
            supersedes = body.get("supersedes")
            if supersedes is not None and not isinstance(supersedes, str):
                raise LLMRequestError(
                    "invalid_request", "supersedes must be a string")
            events = _load_events(events_file)
            event = events.get(disruption_id)
            if event is None:
                raise LedgerError(
                    f"disruption_id {disruption_id} does not resolve "
                    "in the server event store (referential "
                    "integrity)")
            fetcher = make_chain_fetcher(runs) if runs is not None \
                else default_chain_fetcher
            try:
                record = fetcher(run_id)
            except LookupError:
                raise LLMRequestError(
                    "invalid_request",
                    f"unknown run_id: {run_id} (no stored run record "
                    "to derive the predicted chain from)", 422)
            run = {"run_id": record.get("run_id", run_id),
                   "events": record.get("events") or []}
            return _ledger().lock(event, run, supersedes=supersedes,
                                  node_tiers=_node_tiers(),
                                  known_event_ids=set(events))
        return await _respond(request, handle)

    @r.post("/ledger/grade")
    async def ledger_grade(request: Request):
        gate = _admin_gate(request)
        if gate is not None:
            return gate

        def handle(body: dict) -> dict:
            # Caller-supplied observations are rejected — the
            # grade is computed from server-held records referenced
            # by id only.
            if "observations" in body:
                raise LedgerError(
                    "grade no longer accepts caller-supplied "
                    "observations: send observation_ids "
                    "referencing records in the server event store")
            entry_id = _require_str(body, "entry_id")
            obs_ids = _require_list(body, "observation_ids")
            if any(not isinstance(o, str) for o in obs_ids):
                raise LLMRequestError(
                    "invalid_request",
                    "observation_ids must be an array of strings")
            store = _ledger()
            entry = store.get(entry_id)
            if entry is None:
                raise LedgerError(f"unknown entry_id: {entry_id}")
            events = _load_events(events_file)
            observations = []
            for oid in obs_ids:
                record = events.get(oid)
                if record is None:
                    raise LedgerError(
                        f"unknown observation id: {oid} — grading "
                        "observations must resolve in the server event "
                        "store (grade-by-reference)")
                observations.append(observation_from_event(entry, record))
            return store.grade_entry(entry_id, observations)
        return await _respond(request, handle)

    @r.get("/ledger")
    def ledger_entries():
        def handle() -> dict:
            store = _ledger()
            body: dict = {"entries": store.entries()}
            warnings = []
            if store.torn_tail_detected:
                warnings.append(
                    "torn final ledger line dropped on read; "
                    "the sealed chain is unaffected — run ledger "
                    "verify() for a full integrity check")
            if store.duplicate_grade_detected:
                warnings.append(
                    "duplicate grade record present in the ledger "
                    "file; the first grade stands and "
                    "verify() fails on it")
            if warnings:
                body["warnings"] = warnings
            return body
        return _respond_get(handle)

    @r.get("/ledger/stats")
    def ledger_stats_route():
        """Read-only counts: locked / graded / hit / missed, the oldest
        unresolved entry, the auto-lock switch and today's lock count.
        LEDGER numbers only; additive, no ledger write."""
        def handle() -> dict:
            try:
                from .ledger_jobs import ledger_stats
            except ImportError:  # server/ on sys.path identity
                from llm.ledger_jobs import ledger_stats  # type: ignore
            return ledger_stats(Path(ledger_path) if ledger_path is not None else None)
        return _respond_get(handle)

    @r.get("/ledger/public", response_class=HTMLResponse)
    def ledger_public():
        return _respond_get(lambda: render_page(_ledger()))

    return r


router = build_router() if APIRouter is not None else None
