"""/live/* routes (flag-gated by SUPERMAP_LIVE_TWIN).

    GET /live/stream     text/event-stream (SSE), ?topics=, Last-Event-ID
    GET /live/snapshot   pull equivalent of the stream (first paint)
    GET /live/entities   columnar spatial query (?bbox=&kind=&limit=)
    GET /live/vessel/{mmsi}     detail + bounded track
    GET /live/aircraft/{icao24} detail + bounded track
    GET /live/health     per-feed health + hub stats
    GET /live/stats      cheap counts / report ages / budget / collectors

Flag off (app.state.live_twin falsy): every route answers the standard
404 ``live_twin_off`` error, no hub is built and no thread is started.
Request handlers that do real work are plain ``def`` (FastAPI runs them
on the threadpool); the stream is a native async generator that never
touches a file or a lock held for long, so it cannot stall other routes.
"""
from __future__ import annotations

import asyncio
import gzip
import math
import os
import re
import threading
import time
from collections import OrderedDict
from contextlib import asynccontextmanager

from fastapi import Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from .collectors import CollectorSet, collectors_enabled
from .feeds import FeedPoller, env_float, env_int
from .hub import OWNER_CAP, TOPICS, LiveHub
from .payload import dumps, maybe_gzip
from .sse import SSE_HEADERS, stream

ENTITY_LIMIT_DEFAULT = 1000
ENTITY_LIMIT_MAX = 60000
_MMSI_RE = re.compile(r"^\d{1,9}$")
_ICAO_RE = re.compile(r"^[0-9a-fA-F]{6}$")
_KINDS = {"vessel": "vessel", "vessels": "vessel", "ship": "vessel",
          "aircraft": "aircraft", "plane": "aircraft", "air": "aircraft"}


def _e(status: int, code: str, message: str, headers=None) -> JSONResponse:
    return JSONResponse(status_code=status, headers=headers,
                        content={"error": {"code": code, "message": message}})


def _json(obj, request: Request, status: int = 200) -> Response:
    body = dumps(obj).encode("utf-8")
    body, hdrs = maybe_gzip(body, request.headers.get("accept-encoding"))
    hdrs["Cache-Control"] = "no-store"
    return Response(content=body, status_code=status,
                    media_type="application/json", headers=hdrs)


class ResponseCache:
    """Serialised (and lazily gzipped) entity responses, keyed by the
    columnar frame they were cut from: many clients polling the same
    view share one dumps + one gzip. Bounded by entries and bytes."""

    def __init__(self, max_entries: int = 24, max_bytes: int = 96 << 20):
        self._d: "OrderedDict[tuple, list]" = OrderedDict()
        self._bytes = 0
        self.max_entries, self.max_bytes = max_entries, max_bytes
        self._lock = threading.Lock()
        self.hits = self.misses = 0

    def get(self, key):
        with self._lock:
            ent = self._d.get(key)
            if ent is not None:
                self._d.move_to_end(key)
                self.hits += 1
            else:
                self.misses += 1
            return ent

    def put(self, key, raw: bytes):
        ent = [raw, None]
        with self._lock:
            old = self._d.pop(key, None)
            if old is not None:
                self._bytes -= len(old[0]) + len(old[1] or b"")
            self._d[key] = ent
            self._bytes += len(raw)
            while self._d and (len(self._d) > self.max_entries
                               or self._bytes > self.max_bytes):
                _, ev = self._d.popitem(last=False)
                self._bytes -= len(ev[0]) + len(ev[1] or b"")
        return ent

    def add_gz(self, ent, gz: bytes) -> None:
        with self._lock:
            if ent[1] is None:
                ent[1] = gz
                self._bytes += len(gz)


def _json_cached(cache: ResponseCache, key, build, request: Request):
    ent = cache.get(key)
    if ent is None:
        ent = cache.put(key, dumps(build()).encode("utf-8"))
    raw = ent[0]
    hdrs = {"Cache-Control": "no-store", "Vary": "Accept-Encoding"}
    ae = (request.headers.get("accept-encoding") or "").lower()
    if "gzip" in ae and len(raw) >= 1024:
        if ent[1] is None:
            cache.add_gz(ent, gzip.compress(raw, compresslevel=5, mtime=0))
        hdrs["Content-Encoding"] = "gzip"
        return Response(content=ent[1], media_type="application/json",
                        headers=hdrs)
    return Response(content=raw, media_type="application/json",
                    headers=hdrs)


def _parse_topics(raw: str | None):
    if raw is None or not raw.strip():
        return TOPICS, None
    wanted = [t.strip().lower() for t in raw.split(",") if t.strip()]
    bad = [t for t in wanted if t not in TOPICS]
    if bad or not wanted:
        return None, _e(422, "invalid_request",
                        f"unknown topic(s) {bad[:3]}; valid topics: "
                        + ", ".join(TOPICS))
    return tuple(t for t in TOPICS if t in wanted), None


def _parse_bbox(raw: str | None):
    if raw is None or not raw.strip():
        return None, None
    try:
        parts = [float(x) for x in raw.split(",")]
    except ValueError:
        parts = []
    if len(parts) != 4 or not all(math.isfinite(p) for p in parts):
        return None, _e(422, "invalid_request",
                        "bbox must be minLon,minLat,maxLon,maxLat "
                        "(four finite numbers)")
    min_lon, min_lat, max_lon, max_lat = parts
    if not (-180 <= min_lon <= 180 and -180 <= max_lon <= 180
            and -90 <= min_lat <= 90 and -90 <= max_lat <= 90
            and min_lat <= max_lat):
        return None, _e(422, "invalid_request",
                        "bbox out of range (lon -180..180, lat -90..90, "
                        "minLat <= maxLat; minLon > maxLon crosses the "
                        "antimeridian)")
    return tuple(parts), None


def _int_param(request: Request, name: str, default: int, lo: int, hi: int):
    raw = request.query_params.get(name)
    if raw is None:
        return default, None
    try:
        v = int(raw)
    except ValueError:
        return None, _e(422, "invalid_request", f"{name} must be an integer")
    if not lo <= v <= hi:
        return None, _e(422, "invalid_request",
                        f"{name} must be between {lo} and {hi}")
    return v, None


def _detect_server(hub: LiveHub) -> None:
    """Best effort: under ``uvicorn.run`` the SIGTERM/SIGINT handler is a
    bound method of the uvicorn Server, which exposes ``should_exit``.
    Read-only introspection (no handler is replaced); lets open SSE
    streams end on shutdown instead of holding the process until
    uvicorn's graceful-shutdown timeout. Silent no-op otherwise."""
    try:
        import signal
        for sig in (signal.SIGTERM, signal.SIGINT):
            srv = getattr(signal.getsignal(sig), "__self__", None)
            if srv is not None and hasattr(srv, "should_exit"):
                hub.stop_probe = lambda s=srv: bool(s.should_exit)
                return
    except Exception:
        pass


def _process_stats() -> dict:
    """RSS / CPU / threads of this process (cheap, /proc based)."""
    out = {"cpu_s": round(time.process_time(), 2),
           "threads": threading.active_count(), "rss_bytes": None}
    try:
        with open("/proc/self/statm") as fh:
            out["rss_bytes"] = int(fh.read().split()[1]) * os.sysconf(
                "SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError, AttributeError):
        pass
    return out


def register_live_routes(app, ctx: dict) -> None:
    """ctx: err (the app's _err), data_root, events_path, health (callable
    returning the adapter health report)."""
    state_lock = threading.Lock()
    cache = ResponseCache()
    flag_err = ctx["err"]

    def flag_off():
        if getattr(app.state, "live_twin", False):
            return None
        return flag_err("live_twin_off",
                        "live twin is off (set SUPERMAP_LIVE_TWIN=1 to "
                        "enable it)")

    def ensure():
        """Build hub (+poller) on first use. Worker-thread only."""
        with state_lock:
            hub = getattr(app.state, "live_hub", None)
            if hub is None:
                raw_collect = getattr(app.state, "live_collect", None)
                if raw_collect is None:
                    raw_collect = os.environ.get("SUPERMAP_LIVE_COLLECT")
                collect = collectors_enabled(raw_collect)
                hub = LiveHub(
                    ring_max=env_int("SUPERMAP_LIVE_RING", 2000),
                    max_clients=env_int("SUPERMAP_LIVE_MAX_CLIENTS", 64),
                    client_queue=env_int("SUPERMAP_LIVE_CLIENT_QUEUE", 256),
                    max_vessels=env_int("SUPERMAP_LIVE_MAX_VESSELS",
                                        40000 if "ais" in collect else 20000),
                    max_aircraft=env_int("SUPERMAP_LIVE_MAX_AIRCRAFT", 20000),
                    track_len=env_int("SUPERMAP_LIVE_TRACK_LEN", 40),
                    vessel_track_len=env_int("SUPERMAP_LIVE_VESSEL_TRACK_LEN",
                                             24 if "ais" in collect else 0)
                    or None,
                    vessel_thin_s=env_float("SUPERMAP_LIVE_VESSEL_THIN_S",
                                            30.0 if "ais" in collect else 0.0),
                    frame_ttl_s=env_float("SUPERMAP_LIVE_FRAME_TTL_S", 1.0),
                    vessel_ttl_s=env_float("SUPERMAP_LIVE_VESSEL_TTL_S",
                                           1800.0) if "ais" in collect
                    else None,
                    aircraft_ttl_s=env_float("SUPERMAP_LIVE_AIRCRAFT_TTL_S",
                                             600.0) if "opensky" in collect
                    else None)
                app.state.live_hub = hub
                if collect:
                    app.state.live_collectors = CollectorSet(
                        hub, collect, ctx["data_root"],
                        **(getattr(app.state, "live_collect_kwargs", None)
                           or {}))
            cs = getattr(app.state, "live_collectors", None)
            if cs is not None:
                cs.start()
            poller = getattr(app.state, "live_poller", None)
            if poller is None and getattr(app.state, "live_poll", True):
                poller = FeedPoller(
                    hub, data_root=ctx["data_root"],
                    events_path=ctx.get("events_path"),
                    health_fn=ctx.get("health"),
                    interval_s=env_float("SUPERMAP_LIVE_POLL_S", 2.0))
                app.state.live_poller = poller
        if poller is not None:
            poller.ensure_started()
        return hub

    # -- graceful shutdown + eager start, via the router lifespan -------
    try:
        orig = app.router.lifespan_context

        @asynccontextmanager
        async def lifespan(a):
            async with orig(a) as maybe_state:
                if getattr(a.state, "live_twin", False):
                    hub0 = await asyncio.to_thread(ensure)
                    if hub0.stop_probe is None:
                        _detect_server(hub0)
                try:
                    yield maybe_state
                finally:
                    hub = getattr(a.state, "live_hub", None)
                    poller = getattr(a.state, "live_poller", None)
                    cs = getattr(a.state, "live_collectors", None)
                    if hub is not None:
                        hub.close()
                    if cs is not None:
                        cs.stop()
                    if poller is not None:
                        poller.stop()

        app.router.lifespan_context = lifespan
    except Exception:           # never take app creation down
        pass

    # -- SSE ------------------------------------------------------------
    @app.get("/live/stream")
    async def live_stream(request: Request):
        off = flag_off()
        if off:
            return off
        topics, err = _parse_topics(request.query_params.get("topics"))
        if err:
            return err
        hub = await asyncio.to_thread(ensure)
        last_id = request.headers.get("last-event-id") \
            or request.query_params.get("lastEventId")
        # off the loop: subscribe takes the hub lock (ingest may hold it)
        try:
            from api.security import request_client
        except ImportError:  # imported as server.live.* from the repo root
            from server.api.security import request_client  # type: ignore
        owner = request_client(request)
        sub = await asyncio.to_thread(
            hub.subscribe, topics, last_id.strip() if last_id else None, owner)
        if sub == OWNER_CAP:
            return _e(429, "live_client_streams_full",
                      f"this client already holds {hub.max_per_client} live streams; "
                      "close one or retry shortly", headers={"Retry-After": "5"})
        if sub is None:
            return _e(503, "live_clients_full",
                      f"live stream is at its client cap "
                      f"({hub.max_clients}); retry shortly",
                      headers={"Retry-After": "5"})
        client, replay, reset, head_id = sub
        heartbeat_s = max(0.05, getattr(app.state, "live_heartbeat_s", None)
                          or env_float("SUPERMAP_LIVE_HEARTBEAT_S", 15.0))
        max_s = getattr(app.state, "live_stream_max_s", None)
        if max_s is None:
            max_s = env_float("SUPERMAP_LIVE_STREAM_MAX_S", 3600.0)
        return StreamingResponse(
            stream(hub, client, replay, reset, head_id, request,
                   heartbeat_s=heartbeat_s, max_stream_s=max_s,
                   resuming=bool(last_id)),
            media_type="text/event-stream", headers=dict(SSE_HEADERS))

    # -- pull routes (threadpool) ---------------------------------------
    @app.get("/live/snapshot")
    def live_snapshot(request: Request):
        off = flag_off()
        if off:
            return off
        topics, err = _parse_topics(request.query_params.get("topics"))
        if err:
            return err
        lims = {}
        for name, default, hi in (("entity_limit", ENTITY_LIMIT_DEFAULT,
                                   ENTITY_LIMIT_MAX),
                                  ("events_limit", 100, 500),
                                  ("ports_limit", 100, 2000),
                                  ("borders_limit", 200, 1000)):
            v, err = _int_param(request, name, default, 0, hi)
            if err:
                return err
            lims[name] = v
        hub = ensure()
        return _json(hub.snapshot(topics, **lims), request)

    @app.get("/live/stats")
    def live_stats(request: Request):
        off = flag_off()
        if off:
            return off
        hub = ensure()
        out = hub.live_stats()
        out["response_cache"] = {"hits": cache.hits, "misses": cache.misses,
                                 "bytes": cache._bytes,
                                 "entries": len(cache._d)}
        out["process"] = _process_stats()
        return _json(out, request)

    @app.get("/live/entities")
    def live_entities(request: Request):
        off = flag_off()
        if off:
            return off
        raw_kind = (request.query_params.get("kind") or "").strip().lower()
        kind = _KINDS.get(raw_kind)
        if kind is None:
            return _e(422, "invalid_request",
                      "kind is required: vessel or aircraft")
        bbox, err = _parse_bbox(request.query_params.get("bbox"))
        if err:
            return err
        limit, err = _int_param(request, "limit", ENTITY_LIMIT_DEFAULT, 1,
                                ENTITY_LIMIT_MAX)
        if err:
            return err
        max_age = None
        raw_age = request.query_params.get("max_age_s")
        if raw_age is not None:
            try:
                max_age = float(raw_age)
                if not math.isfinite(max_age) or max_age < 0:
                    raise ValueError
            except ValueError:
                return _e(422, "invalid_request",
                          "max_age_s must be a non-negative number")
        names = request.query_params.get("names") in ("1", "true", "yes")
        hub = ensure()
        fr = hub.frame(kind)
        key = ("entities", fr.id, kind, bbox, limit, max_age, names)
        return _json_cached(
            cache, key, lambda: hub.entities(
                kind, bbox=bbox, limit=limit, max_age_s=max_age,
                include_names=names, frame=fr), request)

    def _detail(kind: str, ent_id: str, request: Request):
        off = flag_off()
        if off:
            return off
        ok = _MMSI_RE.match(ent_id) if kind == "vessel" \
            else _ICAO_RE.match(ent_id)
        if not ok:
            return _e(422, "invalid_request",
                      "vessel id is an MMSI (1-9 digits)" if kind == "vessel"
                      else "aircraft id is an ICAO24 (6 hex digits)")
        if kind == "aircraft":
            ent_id = ent_id.lower()
        hub = ensure()
        tl = (hub.vessels if kind == "vessel" else hub.aircraft).track_len
        limit, err = _int_param(request, "limit", tl, 1, tl)
        if err:
            return err
        d = hub.entity_detail(kind, ent_id, limit)
        if d is None:
            topic = "vessels" if kind == "vessel" else "aircraft"
            st = hub.topic_status(topic)
            return _e(404, "entity_not_found",
                      f"no {kind} {ent_id} is held by the live hub"
                      + ("" if st["served"] else f" ({st['reason']})"))
        return _json(d, request)

    @app.get("/live/vessel/{mmsi}")
    def live_vessel(mmsi: str, request: Request):
        return _detail("vessel", mmsi, request)

    @app.get("/live/aircraft/{icao24}")
    def live_aircraft(icao24: str, request: Request):
        return _detail("aircraft", icao24, request)

    @app.get("/live/health")
    def live_health(request: Request):
        off = flag_off()
        if off:
            return off
        hub = ensure()
        poller = getattr(app.state, "live_poller", None)
        return _json({"feeds": hub.feeds_view(), "hub": hub.stats(),
                      "collectors": {n: c.status()
                                     for n, c in dict(hub.collectors).items()},
                      "poller": None if poller is None else {
                          "polls": poller.polls,
                          "interval_s": poller.interval_s,
                          "last_error": poller.last_error}}, request)

    app.state.live_routes = True
