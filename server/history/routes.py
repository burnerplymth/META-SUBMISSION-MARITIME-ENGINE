"""History / fork / realism routes (additive; mounted from create_app).

  GET  /history/sources        capability board (HISTORY-CAPABLE / CURRENT-ONLY / BLOCKED)
  GET  /history/metrics        every observation metric each source serves
  GET  /history/examples       the seeded offline backplay cases
  GET  /history/cache          the on-disk history cache listing
  GET  /history/events         ?from=&to=&places=a,b&sources=&infer=
  GET  /history/observations   ?place=&metric=&from=&to=
  GET  /history/timeline       ?date=&places=a,b
  POST /history/fork           {event_ids | window, options} -> /run + /sim/run bodies
  GET  /realism                ?run=&from=&to=&places=   (BACKTEST card)
  POST /realism                {run, from?, to?, places?, store?}
  GET  /realism/rule           the frozen published grading rule
  GET  /realism/results        stored-results index
  GET  /realism/results/{id}   one stored card

Error shape everywhere: {"error": {"code", "message"}} - same as the rest
of the API. Nothing here runs a simulation; POST /history/fork may create
a CHAIN run (cheap, deterministic) only when options.chain.create is true.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from fastapi import Request
from fastapi.responses import JSONResponse

from .fork import ForkLog, build_fork
from .realism import CAVEAT, LABEL, RULE, RealismStore
from .realism_service import RealismService, RunNotFound
from .service import HistoryService
from .transport import default_transport
from .util import HistoryError, canonical, parse_day, sha256_hex

MAX_PLACES = 24


def _json_err(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status,
                        content={"error": {"code": code, "message": str(message)[:600]}})


def _places_param(raw) -> list[str] | None:
    if raw is None or raw == "":
        return None
    out = [p.strip() for p in str(raw).split(",") if p.strip()]
    if len(out) > MAX_PLACES:
        raise HistoryError(f"at most {MAX_PLACES} places", "too_many_places")
    return out


def build_services(ctx: dict) -> tuple[HistoryService, RealismService]:
    data_root = Path(ctx["data_root"])
    repo_data = Path(ctx.get("repo_data_dir") or data_root)
    hist = HistoryService(
        data_dir=data_root, graph_path=ctx.get("graph_path"),
        transport=ctx.get("transport") or default_transport,
        fetch_enabled=ctx.get("fetch_enabled"),
        live_events=ctx.get("live_events"), repo_data_dir=repo_data)
    store = RealismStore(data_root / "history_realism")
    forks = ForkLog(data_root / "history_forks")

    def chain_loader(run_id):
        return ctx["run_store"].load(run_id)

    def sim_loader(sim_run_id):
        s = ctx["get_sim_store"]()
        return s.load(sim_run_id) if s is not None else None

    return hist, RealismService(hist, store, chain_loader, sim_loader, forks)


async def _post_self(app, path: str, body: dict):
    """POST to this same app through an in-process ASGI transport, so a
    fork-created chain run goes through the REAL POST /run validator and
    store (no duplicated run logic)."""
    import httpx
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://history.internal") as client:
        resp = await client.post(path, json=body)
        try:
            return resp.status_code, resp.json()
        except ValueError:
            return resp.status_code, {}


def register_history_routes(app, ctx: dict) -> None:
    """ctx keys: data_root, graph_path, run_store (RunStore),
    get_sim_store (callable -> SimStore|None), live_events (callable ->
    folded events), engine_graph (core Graph), graph (full Graph),
    validate_disruption, derive_run_id, graph_hash, post_json (async
    callable(path, body) -> (status, json) calling this app) and
    optional transport / fetch_enabled / repo_data_dir seams."""
    hist, realism = build_services(ctx)
    post_json = ctx.get("post_json") or (lambda path, body: _post_self(app, path, body))
    app.state.history_service = hist
    app.state.realism_service = realism
    forks: ForkLog = realism.fork_log

    def guard(fn):
        try:
            return fn()
        except HistoryError as exc:
            status = 404 if exc.code in ("unknown_place", "unknown_event") else 422
            return _json_err(status, exc.code, str(exc))

    # ------------------------------------------------------------ board
    @app.get("/history/sources")
    def history_sources():
        return hist.sources()

    @app.get("/history/metrics")
    def history_metrics():
        return {"metrics": hist.metrics()}

    @app.get("/history/cache")
    def history_cache():
        entries = hist.cache.entries()
        return {"count": len(entries), "entries": entries,
                "note": "offline: a request covered by one of these is "
                        "answered from disk, byte-identically, with the "
                        "ORIGINAL retrieved_at"}

    @app.get("/history/examples")
    def history_examples():
        stored = {c["run"]["run_id"]: c for c in realism.store.cards()}
        out = []
        for c in hist.replay.catalogue():
            ev = hist.replay.to_event(c)
            cards = [x for x in realism.store.cards()
                     if (x.get("event") or {}).get("event_id") == ev["id"]]
            out.append({
                "event_id": ev["id"], "replay_id": c["replay_id"],
                "title": c["title"], "node_id": c["node_id"],
                "started_at": c["started_at"], "mode": ev["mode"],
                "watch_places": ev.get("watch_places"),
                "realism_cards": [x["card_id"] for x in cards],
                "try": {
                    "timeline": f"/history/timeline?date={c['started_at'][:10]}",
                    "events": "/history/events?from={0}&to={0}".format(c["started_at"][:10]),
                    "fork": {"method": "POST", "path": "/history/fork",
                             "body": {"event_ids": [ev["id"]]}}}})
        return {"examples": out}

    # ----------------------------------------------------------- events
    @app.get("/history/events")
    def history_events(request: Request):
        def run():
            q = request.query_params
            if "from" not in q or "to" not in q:
                raise HistoryError("from and to (YYYY-MM-DD) are required")
            start, end = parse_day(q["from"], "from"), parse_day(q["to"], "to")
            infer = None
            if "infer" in q:
                infer = q["infer"].lower() not in ("0", "false", "no", "off")
            return hist.events(start, end, _places_param(q.get("places")),
                               _places_param(q.get("sources")), infer)
        return guard(run)

    # ----------------------------------------------------- observations
    @app.get("/history/observations")
    def history_observations(request: Request):
        def run():
            q = request.query_params
            for k in ("place", "from", "to"):
                if k not in q:
                    raise HistoryError(f"{k} is required")
            metric = q.get("metric") or "activity"
            res = hist.observations(q["place"], metric, parse_day(q["from"], "from"),
                                    parse_day(q["to"], "to"))
            return res
        return guard(run)

    # --------------------------------------------------------- timeline
    @app.get("/history/timeline")
    def history_timeline(request: Request):
        def run():
            q = request.query_params
            if "date" not in q:
                raise HistoryError("date (YYYY-MM-DD) is required")
            lb = 7
            if "lookback_days" in q:
                try:
                    lb = int(q["lookback_days"])
                except ValueError:
                    raise HistoryError("lookback_days must be an integer") from None
                if not 1 <= lb <= 60:
                    raise HistoryError("lookback_days must be 1..60")
            return hist.timeline(parse_day(q["date"], "date"),
                                 _places_param(q.get("places")), lb)
        return guard(run)

    # ------------------------------------------------------------- fork
    def _validators():
        from engine.engine import EngineError

        def v_chain(spec):
            try:
                ctx["validate_disruption"](ctx["engine_graph"], spec)
            except EngineError as exc:
                return {"code": exc.code, "message": str(exc)}
            return None

        def v_sim(spec):
            try:
                out = ctx["validate_disruption"](ctx["graph"], spec)
            except EngineError as exc:
                return {"code": exc.code, "message": str(exc)}
            if out["shape"] != "step":
                return {"code": "invalid_shape",
                        "message": "sim v1 supports step-shape disruptions only"}
            return None
        return v_chain, v_sim

    @app.post("/history/fork")
    async def history_fork(request: Request):
        try:
            body = await request.json()
        except Exception:
            return _json_err(400, "bad_json", "request body must be JSON")
        if not isinstance(body, dict):
            return _json_err(422, "invalid_request", "request body must be an object")
        extra = sorted(set(body) - {"event_ids", "window", "options"})
        if extra:
            return _json_err(422, "invalid_request", f"unknown keys: {extra}")
        try:
            window = body.get("window")
            ids = body.get("event_ids")
            events: list[dict] = []
            wf = wt = None
            places = None
            if window is not None:
                if not isinstance(window, dict) or set(window) - {
                        "from", "to", "places", "sources"}:
                    raise HistoryError("window must be {from, to, places?, sources?}")
                wf, wt = parse_day(window.get("from"), "window.from"), \
                    parse_day(window.get("to"), "window.to")
                pl = window.get("places")
                if pl is not None and (not isinstance(pl, list)
                                       or not all(isinstance(x, str) for x in pl)):
                    raise HistoryError("window.places must be a list of strings")
                places = pl
            if ids is not None:
                if not isinstance(ids, list) or not ids or \
                        not all(isinstance(x, str) for x in ids):
                    raise HistoryError("event_ids must be a non-empty list of strings")
                for eid in ids:
                    ev = hist.event_by_id(eid, wf, wt, places)
                    if ev is None:
                        raise HistoryError(
                            f"unknown event id {eid!r} (curated events resolve "
                            "offline; GDACS/PortWatch ids need the window that "
                            "contains them)", "unknown_event")
                    events.append(ev)
            elif window is not None:
                res = hist.events(wf, wt, places, window.get("sources"))
                events = res["events"]
            else:
                raise HistoryError("pass event_ids or window")
            v_chain, v_sim = _validators()
            options = body.get("options") or {}
            if not isinstance(options, dict):
                raise HistoryError("options must be an object")
            fork = build_fork(
                events, options,
                window=({"from": wf.isoformat(), "to": wt.isoformat()}
                        if wf else None),
                validate_chain=v_chain, validate_sim=v_sim,
                chain_run_id=lambda spec, params: ctx["derive_run_id"](
                    {"kind": "run", "graph_hash": ctx["graph_hash"],
                     "disruption": ctx["validate_disruption"](
                         ctx["engine_graph"], spec),
                     "params": ctx["validate_params"](params)}))
        except HistoryError as exc:
            status = 404 if exc.code == "unknown_event" else 422
            return _json_err(status, exc.code, str(exc))
        created = []
        if (options.get("chain") or {}).get("create") is True:
            for item in fork["chain_requests"]:
                if not item["validation"]["ok"]:
                    created.append({"event_id": item["event_id"], "created": False,
                                    "reason": item["validation"]["message"]})
                    continue
                status, doc = await post_json("/run", item["request"])
                if status == 201:
                    item["run_id"] = doc["run_id"]
                    created.append({"event_id": item["event_id"], "created": True,
                                    "run_id": doc["run_id"],
                                    "stream_hash": doc.get("stream_hash")})
                else:
                    created.append({"event_id": item["event_id"], "created": False,
                                    "reason": (doc.get("error") or {}).get("message")})
            run_ids = [c["run_id"] for c in created if c.get("created")]
            if run_ids:
                forks.save(fork["forked_from"], run_ids)
        fork["created_chain_runs"] = created
        fork["sim_not_run"] = ("the sim is NOT run here (expensive): POST "
                               "sim_request.request to /sim/run yourself")
        return fork

    # ---------------------------------------------------------- realism
    @app.get("/realism/rule")
    def realism_rule():
        return {"label": LABEL, "rule": RULE, "caveat": CAVEAT}

    @app.get("/realism/results")
    def realism_results():
        return {"label": LABEL, "count": len(realism.store.index()),
                "results": realism.store.index()}

    @app.get("/realism/results/{card_id}")
    def realism_result(card_id: str):
        card = realism.store.load(card_id)
        if card is None:
            return _json_err(404, "result_not_found", f"no stored result {card_id!r}")
        card["source"] = "STORED"
        return card

    def _evaluate(run, frm, to, places, store, title=None):
        try:
            return realism.evaluate(run, frm, to, places, title=title, store=store)
        except RunNotFound:
            return _json_err(404, "run_not_found",
                             f"run {run!r} is not in the run store and no stored "
                             "realism result exists for it")
        except HistoryError as exc:
            return _json_err(422, exc.code, str(exc))

    @app.get("/realism")
    def realism_get(request: Request):
        q = request.query_params
        if not q.get("run"):
            return _json_err(422, "invalid_request",
                             "run (a run_... chain id or sim_... sim id) is required")
        try:
            places = _places_param(q.get("places"))
        except HistoryError as exc:
            return _json_err(422, exc.code, str(exc))
        return _evaluate(q["run"], q.get("from"), q.get("to"), places, False)

    @app.post("/realism")
    async def realism_post(request: Request):
        try:
            body = await request.json()
        except Exception:
            return _json_err(400, "bad_json", "request body must be JSON")
        if not isinstance(body, dict) or not isinstance(body.get("run"), str):
            return _json_err(422, "invalid_request", "body must be {run: <id>, ...}")
        extra = sorted(set(body) - {"run", "from", "to", "places", "store", "title"})
        if extra:
            return _json_err(422, "invalid_request", f"unknown keys: {extra}")
        places = body.get("places")
        if places is not None and (not isinstance(places, list)
                                   or not all(isinstance(x, str) for x in places)):
            return _json_err(422, "invalid_request", "places must be a list of strings")
        title = body.get("title")
        if title is not None and (not isinstance(title, str) or len(title) > 120):
            return _json_err(422, "invalid_request",
                             "title must be a string of at most 120 characters")
        return _evaluate(body["run"], body.get("from"), body.get("to"), places,
                         body.get("store") is True, title)
