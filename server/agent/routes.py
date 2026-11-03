"""HTTP surface of agent mode (additive; registered from create_app).

  GET  /agent/status                         enabled, transport label, limits
  GET  /agent/tools                          the tool registry (kind, confirm rule, schema)
  GET  /agent/demo-tasks                     the recorded demo prompts
  POST /agent/sessions            {limits?}  create (201)
  GET  /agent/sessions                       list
  GET  /agent/sessions/{id}                  state: status, messages, pending, usage, limits
  POST /agent/sessions/{id}/messages {text}  queue a user message (202); the loop runs async
  GET  /agent/sessions/{id}/steps?after_rev=&wait_s=
                                             the AI activity log; long-poll (wait_s <= 15)
                                             returns as soon as anything changes
  POST /agent/sessions/{id}/actions/{aid}/confirm | /reject
                                             resolve a pending action (202)
  POST /agent/sessions/{id}/actions/{aid}/result {ok, summary, code?}
                                             the browser reports a client action (UI control)
                                             it performed; resumes the loop (202)
  POST /agent/sessions/{id}/cancel           stop the session

Chat threads (multi-turn Meta AI chat; same loop, per-message caps, web search
toggle, citations, export; see threads.py):

  POST   /agent/threads {limits?, web_search?, title?}    create (201)
  GET    /agent/threads                                   the caller's own chats
  GET    /agent/threads/{id}                              messages (tool steps, citations), usage, web state
  PUT    /agent/threads/{id} {title?, web_search?}        rename, toggle web search
  DELETE /agent/threads/{id}
  POST   /agent/threads/{id}/messages {text, context?, web_search?}   (202)
  POST   /agent/threads/{id}/regenerate                   ask the last question again (202)
  GET    /agent/threads/{id}/steps?after_rev=&wait_s=     as for sessions
  POST   /agent/threads/{id}/actions/{aid}/confirm | /reject | /result
  POST   /agent/threads/{id}/cancel                       stop this message's turn
  POST   /agent/threads/{id}/export?format=html|md|json|pdf[&message=<id>]

Errors use the house shape {"error": {"code", "message"}}. Every response
that carries model or tool activity carries transport LIVE or RECORDED.
"""
from __future__ import annotations

import functools
import os
import threading

from fastapi import Request
from fastapi.responses import JSONResponse

try:
    from llm import demo_gate
except ImportError:  # imported as server.agent.* from the repo root
    from server.llm import demo_gate  # type: ignore

try:
    from api.security import request_client
except ImportError:  # imported as server.agent.* from the repo root
    from server.api.security import request_client  # type: ignore

from fastapi.responses import Response

from .apicall import AsgiApi
from .export import FORMATS, ExportError
from .fixture import DEMO_TASKS
from .session import AgentError, default_limits
from .threads import SUGGESTIONS, ChatManager, chat_limits, chat_settings
from .tools import REGISTRY

MAX_WAIT_S = 15.0


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, default)))
    except ValueError:
        return default


class LongPollGate:
    """Bound concurrent long-polls. One holds an API worker thread for up to
    MAX_WAIT_S and every async GET route shares that pool, so unbounded polls
    are a way to stall the whole API. A poll over the per-client or the
    server-wide cap is refused at once (429 + Retry-After); the web client
    backs off and retries. Limits are read per call (env)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._total = 0
        self._by_client: dict[str, int] = {}

    def acquire(self, client: str) -> bool:
        cap_all = _env_int("SUPERMAP_AGENT_MAX_LONGPOLLS", 8)
        cap_one = _env_int("SUPERMAP_AGENT_MAX_LONGPOLLS_PER_CLIENT", 3)
        with self._lock:
            if self._total >= cap_all or self._by_client.get(client, 0) >= cap_one:
                return False
            self._total += 1
            self._by_client[client] = self._by_client.get(client, 0) + 1
            return True

    def release(self, client: str) -> None:
        with self._lock:
            self._total = max(0, self._total - 1)
            n = self._by_client.get(client, 0) - 1
            if n > 0:
                self._by_client[client] = n
            else:
                self._by_client.pop(client, None)


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status,
                        content={"error": {"code": code, "message": str(message)[:300]}})


def register_agent_routes(app, ctx: dict) -> ChatManager:
    manager = ChatManager(graph=ctx["graph"], api=ctx.get("api") or AsgiApi(app),
                           data_root=ctx.get("data_root"),
                           provider_factory=ctx.get("provider_factory"))
    app.state.agent_manager = manager
    polls = LongPollGate()

    def guard(fn):
        @functools.wraps(fn)
        async def inner(*a, **kw):
            if not manager.enabled():
                return _error(503, "agent_disabled", "agent mode is switched off")
            try:
                return await fn(*a, **kw)
            except AgentError as e:
                return _error(e.status, e.code, e.message)
        return inner

    async def _json(req: Request) -> dict:
        raw = await req.body()
        if not raw.strip():
            return {}
        try:
            body = await req.json()
        except Exception:
            raise AgentError("bad_json", "request body must be JSON", 400)
        if not isinstance(body, dict):
            raise AgentError("invalid_request", "request body must be an object", 422)
        return body

    @app.get("/agent/status")
    async def agent_status(req: Request):
        from .session import _default_provider, _gate_enabled, _recorded_provider
        unlocked = demo_gate.request_unlocked(req)
        try:
            p = (ctx.get("provider_factory") or _default_provider)() \
                if (unlocked or not _gate_enabled()) else _recorded_provider()
            st = p.status()
            t = {"transport": st["transport"], "transport_label": st["transport_label"],
                 "model": st["model"], "cap_usd": st["cap_usd"], "cap_reached": st["cap_reached"],
                 "llm_disabled": st["disabled"]}
        except Exception as exc:  # fail closed and say so
            t = {"transport": None, "transport_label": None,
                 "unavailable": type(exc).__name__}
        cfg = chat_settings()
        web = None
        try:
            if t.get("transport") == "http":
                web = p.web_search_status()
        except Exception:
            web = None
        return {"enabled": manager.enabled(), "limits": default_limits(),
                "tools": len(REGISTRY), "demo_gate": demo_gate.status_block(req),
                "chat": {"limits": chat_limits(), "suggestions": SUGGESTIONS,
                         "export_formats": list(FORMATS), "web_search": web,
                         "client_daily_usd": cfg["client_daily_usd"],
                         "max_messages": cfg["max_messages"]}, **t}

    @app.get("/agent/tools")
    async def agent_tools():
        return {"tools": [{"name": t.name, "kind": t.kind, "confirm": t.confirm,
                           "description": t.description, "parameters": t.parameters}
                          for t in REGISTRY.values()]}

    @app.get("/agent/demo-tasks")
    async def agent_demo_tasks():
        from .fixture_research import RESEARCH_TASKS
        return {"tasks": DEMO_TASKS, "research_tasks": RESEARCH_TASKS, "label": "RECORDED"}

    @app.post("/agent/sessions", status_code=201)
    @guard
    async def create_session(req: Request):
        body = await _json(req)
        extra = set(body) - {"limits", "purpose"}
        if extra:
            raise AgentError("invalid_request", f"unknown keys: {sorted(extra)}", 422)
        lim = body.get("limits")
        if lim is not None and not isinstance(lim, dict):
            raise AgentError("invalid_request", "limits must be an object", 422)
        try:
            s = manager.create(lim, live_ok=demo_gate.request_unlocked(req),
                             owner=request_client(req), purpose=body.get("purpose"))
        except ValueError as e:
            raise AgentError("invalid_request", str(e), 422)
        return s.view()

    @app.get("/agent/sessions")
    @guard
    async def list_sessions(req: Request):
        return {"sessions": manager.list(owner=request_client(req))}

    @app.get("/agent/sessions/{sid}")
    @guard
    async def get_session(sid: str):
        s = manager.get(sid)
        if s is None:
            raise AgentError("unknown_session", "no such session", 404)
        return s.view()

    @app.post("/agent/sessions/{sid}/messages", status_code=202)
    @guard
    async def post_message(sid: str, req: Request):
        body = await _json(req)
        extra = set(body) - {"text", "timeline"}
        if extra:
            raise AgentError("invalid_request", f"unknown keys: {sorted(extra)}", 422)
        return manager.post_message(sid, body.get("text"), timeline=body.get("timeline")).view()

    @app.get("/agent/sessions/{sid}/steps")
    @guard
    async def get_steps(sid: str, req: Request):
        return _steps_doc(sid, req)

    def _steps_doc(sid: str, req: Request):
        s = manager.get(sid)
        if s is None:
            raise AgentError("unknown_session", "no such session", 404)
        try:
            after = int(req.query_params.get("after_rev", 0))
            wait = float(req.query_params.get("wait_s", 0))
        except ValueError:
            raise AgentError("invalid_request", "after_rev must be an integer and wait_s a number", 422)
        if after < 0 or not (0 <= wait <= MAX_WAIT_S):
            raise AgentError("invalid_request", f"after_rev >= 0 and 0 <= wait_s <= {MAX_WAIT_S:g}", 422)
        if wait:
            who = request_client(req)
            if not polls.acquire(who):
                return JSONResponse(
                    status_code=429, headers={"Retry-After": "2"},
                    content={"error": {"code": "too_many_polls",
                                       "message": "too many open long-polls; retry shortly"}})
            try:
                manager.wait_rev(sid, after, wait)
            finally:
                polls.release(who)
        with s.lock:
            steps = [dict(x) for x in s.steps if x["rev"] > after]
            return {"session_id": s.id, "status": s.status, "rev": s.rev,
                    "transport": s.transport, "steps": steps,
                    "pending": [dict(p) for p in s.pending.values() if p["state"] == "pending"],
                    "client_actions": [dict(p) for p in s.pending.values()
                                       if p["state"] == "dispatched"]}

    @app.post("/agent/sessions/{sid}/actions/{aid}/result", status_code=202)
    @guard
    async def client_action_result(sid: str, aid: str, req: Request):
        """The browser reports a client action (UI control) it performed."""
        body = await _json(req)
        extra = set(body) - {"ok", "summary", "code"}
        if extra:
            raise AgentError("invalid_request", f"unknown keys: {sorted(extra)}", 422)
        return manager.client_result(sid, aid, body.get("ok"), body.get("summary"),
                                     body.get("code")).view()

    @app.post("/agent/sessions/{sid}/actions/{aid}/confirm", status_code=202)
    @guard
    async def confirm_action(sid: str, aid: str):
        return manager.resolve(sid, aid, "confirm").view()

    @app.post("/agent/sessions/{sid}/actions/{aid}/reject", status_code=202)
    @guard
    async def reject_action(sid: str, aid: str):
        return manager.resolve(sid, aid, "reject").view()

    @app.post("/agent/sessions/{sid}/cancel")
    @guard
    async def cancel_session(sid: str):
        return manager.cancel(sid).view()

    # ------------------------------------------------------------------ chat threads
    def _live_gate(t, req: Request) -> None:
        """A LIVE thread keeps spending the demo credit: its continuation needs
        the demo token like its creation did (the thread id alone is not enough)."""
        if t.transport == "LIVE" and _gate_open() and not demo_gate.request_unlocked(req):
            raise AgentError("demo_locked", "live chats need the demo link; recorded chats are open", 403)

    def _gate_open() -> bool:
        return demo_gate.gate_enabled()

    @app.post("/agent/threads", status_code=201)
    @guard
    async def create_thread(req: Request):
        body = await _json(req)
        extra = set(body) - {"limits", "web_search", "title"}
        if extra:
            raise AgentError("invalid_request", f"unknown keys: {sorted(extra)}", 422)
        lim = body.get("limits")
        if lim is not None and not isinstance(lim, dict):
            raise AgentError("invalid_request", "limits must be an object", 422)
        web = body.get("web_search")
        if web is not None and not isinstance(web, bool):
            raise AgentError("invalid_request", "web_search must be true or false", 422)
        title = body.get("title")
        if title is not None and (not isinstance(title, str) or not title.strip()):
            raise AgentError("invalid_request", "title must be a non-empty string", 422)
        try:
            t = manager.create_thread(owner=request_client(req), live_ok=demo_gate.request_unlocked(req),
                                      limits=lim, web_search=web, title=title)
        except ValueError as e:
            raise AgentError("invalid_request", str(e), 422)
        return t.view()

    @app.get("/agent/threads")
    @guard
    async def list_threads(req: Request):
        return {"threads": manager.list_threads(request_client(req)),
                "suggestions": SUGGESTIONS}

    @app.get("/agent/threads/{tid}")
    @guard
    async def get_thread(tid: str):
        return manager.must_thread(tid).view()

    @app.put("/agent/threads/{tid}")
    @guard
    async def update_thread(tid: str, req: Request):
        body = await _json(req)
        extra = set(body) - {"title", "web_search"}
        if extra:
            raise AgentError("invalid_request", f"unknown keys: {sorted(extra)}", 422)
        return manager.update_thread(tid, title=body.get("title"), web_search=body.get("web_search")).view()

    @app.delete("/agent/threads/{tid}")
    @guard
    async def delete_thread(tid: str):
        manager.delete_thread(tid)
        return {"deleted": tid}

    @app.post("/agent/threads/{tid}/messages", status_code=202)
    @guard
    async def post_thread_message(tid: str, req: Request):
        body = await _json(req)
        extra = set(body) - {"text", "context", "web_search"}
        if extra:
            raise AgentError("invalid_request", f"unknown keys: {sorted(extra)}", 422)
        _live_gate(manager.must_thread(tid), req)
        return manager.post_thread_message(tid, body.get("text"), context=body.get("context"),
                                           web_search=body.get("web_search")).view()

    @app.post("/agent/threads/{tid}/regenerate", status_code=202)
    @guard
    async def regenerate_thread(tid: str, req: Request):
        _live_gate(manager.must_thread(tid), req)
        return manager.regenerate(tid).view()

    @app.get("/agent/threads/{tid}/steps")
    @guard
    async def get_thread_steps(tid: str, req: Request):
        manager.must_thread(tid)
        return _steps_doc(tid, req)

    @app.post("/agent/threads/{tid}/actions/{aid}/result", status_code=202)
    @guard
    async def thread_client_result(tid: str, aid: str, req: Request):
        manager.must_thread(tid)
        body = await _json(req)
        extra = set(body) - {"ok", "summary", "code"}
        if extra:
            raise AgentError("invalid_request", f"unknown keys: {sorted(extra)}", 422)
        return manager.client_result(tid, aid, body.get("ok"), body.get("summary"), body.get("code")).view()

    @app.post("/agent/threads/{tid}/actions/{aid}/confirm", status_code=202)
    @guard
    async def thread_confirm(tid: str, aid: str, req: Request):
        _live_gate(manager.must_thread(tid), req)
        return manager.resolve(tid, aid, "confirm").view()

    @app.post("/agent/threads/{tid}/actions/{aid}/reject", status_code=202)
    @guard
    async def thread_reject(tid: str, aid: str):
        manager.must_thread(tid)
        return manager.resolve(tid, aid, "reject").view()

    @app.post("/agent/threads/{tid}/cancel")
    @guard
    async def thread_cancel(tid: str):
        manager.must_thread(tid)
        return manager.cancel(tid).view()

    @app.post("/agent/threads/{tid}/export")
    @guard
    async def export_thread(tid: str, req: Request):
        fmt = req.query_params.get("format", "html")
        mid = req.query_params.get("message") or None
        manager.must_thread(tid)
        try:
            body, ctype, name = manager.export(tid, fmt, message_id=mid)
        except ExportError as e:
            raise AgentError("invalid_request", str(e), 422)
        return Response(content=body, media_type=ctype, headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})

    from .research_routes import register_research_routes
    register_research_routes(app, manager, guard)
    return manager
