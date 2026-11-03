"""Keep blocking route handlers off the event loop.

Problem: ~67 of the API's route handlers are ``async def`` but do
synchronous work (file IO, engine runs, LLM calls through ``urllib``).
Run on the event loop they stall EVERY request: with a 5 s LLM call in
flight ``GET /health`` would wait for it.

Applied once at the end of ``create_app`` by
``offload_blocking_routes(app)``: every ``async def`` endpoint is
wrapped so its body runs on a worker thread instead of the loop. The
handlers themselves are untouched (same code, same responses), so
behaviour is identical and routes registered directly on ``app`` by
other modules are covered automatically as long as the hook is the last
thing ``create_app`` does before ``return app``. Routers added with
``include_router`` are NOT visible to it under the pinned FastAPI (they
appear as ``_IncludedRouter`` objects, not ``APIRoute``): today that is
``/health`` (plain ``def``) and ``/llm`` (offloads its own work);
an included router must not carry an
``async def`` endpoint that is not self-offloading.

How a coroutine endpoint runs on a thread:
  * the request body is read on the loop first (``await request.body()``;
    Starlette caches it), so the handler's own ``await req.json()`` is
    satisfied from the cache and never touches the loop's socket;
  * the coroutine then runs to completion on a per-thread private event
    loop inside a bounded worker pool.

Two bounded pools keep slow work from starving cheap work:
  * ``light`` (default 16 threads, ``SUPERMAP_API_LIGHT_THREADS``):
    GET/HEAD/OPTIONS handlers;
  * ``heavy`` (default 8 threads, ``SUPERMAP_API_HEAVY_THREADS``):
    POST/PUT/PATCH/DELETE handlers.
Plain ``def`` handlers keep using FastAPI's own (anyio) threadpool,
which these pools never occupy.

Serialisation: several mutating handlers rely on running one at a time
(check-then-claim of a job slot, read-modify-write of a JSONL
store, LRU caches). So mutating handlers take one app-wide lock on their
worker thread: they run one at a time among themselves
but do not block reads, ``/health`` or the LLM routes. The
``/llm`` handlers are not wrapped here: ``llm/router.py`` already offloads
its own work (to a dedicated pool) and its state is file-locked.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import functools
import inspect
import os
import threading

from fastapi import Request
# FastAPI's own request_response (a modified copy of Starlette's that
# also wires the per-request dependency exit stack); older FastAPI
# releases simply re-export Starlette's under the same name.
from fastapi.routing import APIRoute, request_response

_MUTATING = {"POST", "PUT", "PATCH", "DELETE"}

# Mutating routes that need no app-wide serialisation because their
# shared state already has its own lock / is content-addressed and
# idempotent (sim_jobs_lock + bounded pool; RunStore atomic writes;
# stateless ensemble). Exempting them keeps a slow sim POST (weather
# fetch, cold record load) from queueing unrelated writes behind it.
# /voice/transcribe waits on a network call; it writes only its own
# file-locked usage ledger, so it must not hold the app-wide lock.
#
# The heavy export / report / planning routes below are
# stateless (they read the run store and build a response; the exposure memo
# and the port-sim service carry their own locks; history forks and port-sim
# journals are written atomically under content-derived names). One visitor's
# Chromium bundle or 60-lane evaluation must not stall every other write. They
# are rate-limited in tight groups instead (api/security.py). /desks/crit/run
# has its own claim lock (desks_routes.py), so it is exempt too.
LOCK_EXEMPT_PATHS = frozenset({"/run", "/fork", "/ensemble", "/sim/run",
                               "/voice/transcribe", "/montecarlo",
                               "/montecarlo/batch",
                               "/exports/bundle", "/exports/pack",
                               "/exports/chart", "/reports/pdf",
                               "/reports/build", "/replay/export/xlsx",
                               "/exposure/resolve", "/exposure/evaluate",
                               "/exposure/export", "/lanes/plan",
                               "/ports/{place_id}/sim", "/history/fork",
                               "/desks/crit/run"})


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, default)))
    except ValueError:
        return default


class _Pools:
    """Process-wide worker pools (shared by every app instance)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.light: concurrent.futures.ThreadPoolExecutor | None = None
        self.heavy: concurrent.futures.ThreadPoolExecutor | None = None

    def get(self, heavy: bool) -> concurrent.futures.ThreadPoolExecutor:
        with self._lock:
            if self.light is None:
                self.light = concurrent.futures.ThreadPoolExecutor(
                    _env_int("SUPERMAP_API_LIGHT_THREADS", 16),
                    thread_name_prefix="api-light")
                self.heavy = concurrent.futures.ThreadPoolExecutor(
                    _env_int("SUPERMAP_API_HEAVY_THREADS", 8),
                    thread_name_prefix="api-heavy")
            return self.heavy if heavy else self.light  # type: ignore


_POOLS = _Pools()
_tl = threading.local()


def _run_here(coro_fn, args, kwargs):
    """Run ``coro_fn(*args, **kwargs)`` to completion on this thread's
    private event loop (created once per worker thread)."""
    loop = getattr(_tl, "loop", None)
    if loop is None or loop.is_closed():
        loop = _tl.loop = asyncio.new_event_loop()
    return loop.run_until_complete(coro_fn(*args, **kwargs))


def _is_llm_router_endpoint(endpoint) -> bool:
    mod = getattr(endpoint, "__module__", "") or ""
    return mod.endswith("llm.router")


def _wrap(endpoint, mutating: bool, write_lock):
    @functools.wraps(endpoint)
    async def offloaded(*args, **kwargs):
        # Read (and cache) the body on the loop; the handler's own
        # ``await req.json()`` then never waits on the socket.
        for value in (*args, *kwargs.values()):
            if isinstance(value, Request):
                await value.body()
                break

        def work():
            if mutating and write_lock is not None:
                with write_lock:
                    return _run_here(endpoint, args, kwargs)
            return _run_here(endpoint, args, kwargs)

        loop = asyncio.get_running_loop()
        ctx = contextvars.copy_context()
        return await loop.run_in_executor(
            _POOLS.get(mutating), ctx.run, work)

    offloaded.__offloaded__ = True  # type: ignore[attr-defined]
    return offloaded


def offload_blocking_routes(app) -> int:
    """Wrap every ``async def`` APIRoute endpoint of ``app`` so it runs
    off the event loop (idempotent). Returns how many routes were
    wrapped by this call."""
    write_lock = getattr(app.state, "mutation_lock", None)
    if write_lock is None:
        write_lock = app.state.mutation_lock = threading.Lock()
    wrapped = 0
    for route in app.router.routes:
        if not isinstance(route, APIRoute):
            continue
        endpoint = route.dependant.call
        if getattr(endpoint, "__offloaded__", False):
            continue
        if not inspect.iscoroutinefunction(endpoint):
            continue  # plain def: FastAPI already threadpools it
        if _is_llm_router_endpoint(endpoint):
            continue  # llm/router.py offloads its own work
        mutating = bool(route.methods and (route.methods & _MUTATING))
        route.dependant.call = _wrap(
            endpoint, mutating,
            None if route.path in LOCK_EXEMPT_PATHS else write_lock)
        # Rebuild the ASGI app: FastAPI decides coroutine-vs-thread
        # when the request handler is built.
        route.app = request_response(route.get_route_handler())
        wrapped += 1
    app.state.offloaded_routes = getattr(app.state, "offloaded_routes", 0) \
        + wrapped
    return wrapped
