"""Scenario builder routes (additive; defensive mount from create_app).

  GET    /scenarios/rules        bounds, kinds, organise rules, exact vs approximated
  POST   /scenarios/validate     per-shock verdicts per target, overlaps, span
  POST   /scenarios              save (hash-addressed, bounded)
  GET    /scenarios/saved        saved scenarios, newest first
  GET    /scenarios/{id}         one saved scenario
  DELETE /scenarios/{id}         remove a saved scenario
  POST   /scenarios/organise     deterministic timeline arrangement from a structured rule
  POST   /scenarios/{id}/run     run a saved scenario: target chain | sim
  POST   /scenarios/run          the same for an inline scenario (nothing is stored)

``GET /scenarios`` (the curated library) is served by chain_loop.py and is
unchanged.

The two run routes reach the app's own /run and /sim/run through the ASGI
stack (the same validation, rate limits, error shape and stores as a direct
call). They are marked as self-managed so the offload wrapper does not park a
worker thread on them: an offloaded handler that waits on /run would need a
second worker of the same pool, which can deadlock under load. They hold no
thread while waiting, and they do their file work in the threadpool.
"""
from __future__ import annotations

import json
import math
import secrets
from pathlib import Path

from fastapi import Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

try:
    from api.security import request_client
except ImportError:  # imported as server.scenario.* from the repo root
    from server.api.security import request_client  # type: ignore

from .model import (SCENARIO_ID_RE, SIM_DEFAULT_SEED, SIM_HORIZON_D, TARGETS, ScenarioError,
                    chain_order, fmt_iso, parse_iso, r4, rules, semantics, validate_scenario)
from .organise import organise, rules_spec
from .store import ScenarioStore

_DROP_HEADERS = {b"host", b"content-length", b"content-type", b"accept", b"transfer-encoding",
                 b"connection", b"expect", b"upgrade"}
MAX_RESPONSE_BYTES = 4_000_000
COMBINED_ROWS = 40
TOKEN_HEADER = "X-Scenario-Token"


STATUS = {"bad_json": 400, "forbidden": 403, "unknown_scenario": 404, "storage_full": 507}


def fail(code: str, message: str, **extra) -> JSONResponse:
    """The standard error shape, with optional structured detail beside it."""
    body = {"error": {"code": code, "message": str(message)[:300]}}
    body.update(extra)
    return JSONResponse(status_code=STATUS.get(code, 422), content=body)


def _self_managed(fn):
    """Skip the offload wrapper (see the module docstring)."""
    fn.__offloaded__ = True
    return fn


async def _asgi(app, req: Request, method: str, path: str, body: dict | None):
    """One in-process call of the app's own route, made AS the caller: the peer and the request
    headers are the caller's, so the inner call is rate limited, gated and attributed to the
    person who asked, not to a shared internal peer. Returns (status, json|None)."""
    payload = json.dumps(body).encode("utf-8") if body is not None else b""
    host = req.headers.get("host", "").encode("latin-1", "ignore") or b"scenario.internal"
    headers = [(b"host", host), (b"accept", b"application/json")]
    headers += [(k, v) for k, v in req.headers.raw if k.lower() not in _DROP_HEADERS]
    if body is not None:
        headers += [(b"content-type", b"application/json"),
                    (b"content-length", str(len(payload)).encode())]
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
             "method": method, "scheme": "http", "path": path, "raw_path": path.encode(),
             "root_path": "", "query_string": b"", "headers": headers,
             "client": req.scope.get("client"), "server": req.scope.get("server") or ("scenario.internal", 80),
             "state": {}}
    sent = False

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": payload, "more_body": False}
        return {"type": "http.disconnect"}

    status, chunks, size, ctype = 500, [], 0, ""

    async def send(msg):
        nonlocal status, size, ctype
        if msg["type"] == "http.response.start":
            status = msg["status"]
            for k, v in msg.get("headers", []):
                if k.decode().lower() == "content-type":
                    ctype = v.decode("latin-1")
        elif msg["type"] == "http.response.body":
            b = msg.get("body", b"")
            size += len(b)
            if size <= MAX_RESPONSE_BYTES:
                chunks.append(b)

    await app(scope, receive, send)
    parsed = None
    if "json" in ctype and size <= MAX_RESPONSE_BYTES:
        try:
            parsed = json.loads(b"".join(chunks).decode("utf-8"))
        except ValueError:
            parsed = None
    return status, parsed


def _busiest_instant(entries: list) -> tuple[float, list]:
    """(largest sum of delays of shocks active at one instant, those shocks' ids).

    ``entries``: [(shock id, start_d, end_d, delay_d)], windows half open [start, end), so windows that
    only touch do not overlap (the same rule as scenario.model.overlaps). A window of None means the
    shock's window is unknown: it is taken as active throughout (the old sum)."""
    if any(a is None for _i, a, _b, _d in entries):
        return sum(d for *_x, d in entries), sorted(i for i, *_x in entries)
    best, who = -1.0, []
    for _i, start, _end, _d in entries:                 # the maximum is reached at some window start
        live = [(i, d) for i, a, b, d in entries if a <= start < b]
        tot = sum(d for _i2, d in live)
        if tot > best + 1e-12 or (abs(tot - best) <= 1e-12 and len(live) > len(who)):
            best, who = tot, sorted(i for i, _d in live)
    return best, who


def combined_table(records: list[tuple[dict, dict]], names: dict) -> dict:
    """Superposition of independent chain runs: per place, each run's first non-cost delay, then the
    maximum and a sum. The sum adds only shocks whose windows overlap in time (``sum_delay_d`` is the
    largest total of delays of shocks active at one instant, ``overlapping_hit_by`` names them); two
    shocks apart in time hit by the same place do not add. The window is the shock's own
    [start_offset_d, start_offset_d + duration_d), not the arrival time at that place. APPROXIMATED by
    construction (the engine has no interaction between shocks)."""
    per_place: dict[str, dict] = {}
    for shock, record in records:
        seen = set()
        for ev in record["events"]:
            nid = ev["node_id"]
            if ev["channel"] == "cost" or nid in seen:
                continue
            seen.add(nid)
            row = per_place.setdefault(nid, {"place_id": nid, "name": names.get(nid),
                                             "by_shock": {}, "win": {}})
            row["by_shock"][shock["id"]] = r4(ev.get("delay_d") or 0.0)
            a = shock.get("start_offset_d")
            d = shock.get("duration_d")
            row["win"][shock["id"]] = (None, None) if a is None or d is None else (a, a + d)
    rows = []
    for nid, row in per_place.items():
        vals = list(row["by_shock"].values())
        total, who = _busiest_instant([(sid, row["win"][sid][0], row["win"][sid][1], v)
                                       for sid, v in row["by_shock"].items()])
        rows.append({"place_id": nid, "name": row["name"],
                     "max_delay_d": r4(max(vals)), "sum_delay_d": r4(total),
                     "overlapping_hit_by": who,
                     "hit_by": sorted(row["by_shock"]), "by_shock": row["by_shock"]})
    rows.sort(key=lambda r: (-r["max_delay_d"], r["place_id"]))
    return {"method": "superposition", "exact": False, "label": "APPROXIMATED",
            "sum_rule": "sum over shocks whose windows overlap at the place; shocks apart in time do not add",
            "places_reached": len(rows), "places": rows[:COMBINED_ROWS]}


def _refused(code, message, extra):
    return fail(code, message, **extra)


def register_scenario_routes(app, ctx: dict) -> None:
    err = fail
    engine = ctx["engine"]
    engine2 = ctx.get("engine2")
    graph = ctx["graph"]
    runs = ctx["store"]
    # beside the run store (data/runs -> data/scenarios), so an app with its own runs dir is isolated
    scn_store = ScenarioStore(Path(runs.dir).parent / "scenarios")
    app.state.scenario_store = scn_store

    def chain_graph(rev):
        if rev == 2 and engine2 is not None:
            return getattr(engine2, "graph", None) or engine.graph
        return engine.graph

    def refuse(exc: ScenarioError):
        return err(exc.code, exc.message)

    async def read_body(req: Request):
        try:
            body = await req.json()
        except Exception:
            return None, err("bad_json", "request body must be JSON")
        if not isinstance(body, dict):
            return None, err("invalid_request", "request body must be an object")
        return body, None

    def parse_rev(body):
        rev = body.get("engine_rev")
        if rev is None:
            return 1, None
        if isinstance(rev, bool) or rev not in (1, 2):
            return None, err("invalid_request", "engine_rev must be 1 or 2")
        if rev == 2 and engine2 is None:
            return None, err("invalid_request", "engine_rev 2 is unavailable")
        return rev, None

    def validated(raw, rev=1):
        return validate_scenario(raw, chain_graph(rev), graph)

    def summary_row(sid, scn):
        w = [s["start_offset_d"] + s["duration_d"] for s in scn["shocks"]]
        starts = [s["start_offset_d"] for s in scn["shocks"]]
        return {"id": sid, "name": scn["name"], "start_at": scn["start_at"],
                "shock_count": len(scn["shocks"]),
                "places": sorted({s["place_id"] for s in scn["shocks"]}),
                "total_span_d": r4(max(w) - min(starts))}

    # ------------------------------------------------------------ reads
    @app.get("/scenarios/rules")
    async def get_rules():
        return {**rules(), "organise": rules_spec(), "engine_revs": [1, 2] if engine2 else [1]}

    @app.get("/scenarios/saved")
    async def get_saved():
        rows = [{**summary_row(r["id"], r["scenario"])} for r in scn_store.list()]
        return {"scenarios": rows, "count": len(rows), "max_saved": scn_store.max_saved}

    @app.get("/scenarios/{scenario_id_}")
    async def get_scenario(scenario_id_: str):
        scn = scn_store.load(scenario_id_)
        if scn is None:
            return err("unknown_scenario", f"unknown scenario_id: {scenario_id_[:40]!r}")
        return {"id": scenario_id_, "scenario": scn, **{k: v for k, v in summary_row(scenario_id_, scn).items()
                                                     if k in ("total_span_d", "shock_count")}}

    @app.delete("/scenarios/{scenario_id_}")
    async def delete_scenario(scenario_id_: str, req: Request):
        if not SCENARIO_ID_RE.match(scenario_id_):
            return err("unknown_scenario", f"unknown scenario_id: {scenario_id_[:40]!r}")
        # The header is the way to send the token (a query string reaches proxy and access logs; logscrub redacts
        # it there). The ?owner_token= form is also accepted: the web client uses it.
        token = req.headers.get(TOKEN_HEADER) or req.query_params.get("owner_token") or ""
        if not scn_store.exists(scenario_id_):
            return {"id": scenario_id_, "deleted": False}      # idempotent, as before
        if not await run_in_threadpool(scn_store.delete, scenario_id_, token[:200]):
            return err("forbidden", f"deleting a saved scenario needs the owner token returned when it "
                                    f"was saved ({TOKEN_HEADER} header or owner_token parameter)")
        return {"id": scenario_id_, "deleted": True}

    # ------------------------------------------------------------ validate
    @app.post("/scenarios/validate")
    async def post_validate(req: Request):
        body, bad = await read_body(req)
        if bad is not None:
            return bad
        rev, bad = parse_rev(body)
        if bad is not None:
            return bad
        extra = sorted(set(body) - {"scenario", "engine_rev"})
        if extra:
            return err("invalid_request", f"unknown keys: {extra}")
        try:
            v = validated(body.get("scenario"), rev)
        except ScenarioError as exc:
            return refuse(exc)
        v["engine_rev"] = rev
        return v

    @app.post("/scenarios", status_code=201)
    async def post_save(req: Request):
        body, bad = await read_body(req)
        if bad is not None:
            return bad
        extra = sorted(set(body) - {"scenario"})
        if extra:
            return err("invalid_request", f"unknown keys: {extra}")
        try:
            v = validated(body.get("scenario"))
        except ScenarioError as exc:
            return refuse(exc)
        if not v["runnable"]:
            bad_rows = [{"id": r["id"], "verdicts": r["verdicts"]} for r in v["shocks"] if not r["accepted"]]
            first = bad_rows[0]["verdicts"]["chain"] if bad_rows else {}
            return _refused("scenario_refused",
                            f"{len(bad_rows) or 'a'} shock(s) refused: {first.get('reason') or 'no target accepts the scenario'}",
                            {"refused": bad_rows})
        token = secrets.token_urlsafe(18)
        try:
            sid, created = await run_in_threadpool(scn_store.save, v["normalised"], token,
                                                   request_client(req))
        except ValueError:
            return err("invalid_request", "scenario too large")
        except OSError:
            return err("storage_full", "scenario storage is not writable")
        out = {"id": sid, "created": created, "scenario": v["normalised"],
               **summary_row(sid, v["normalised"])}
        if created:                       # shown once; only its digest is stored
            out["owner_token"] = token
            out["owner_token_header"] = TOKEN_HEADER
        return out

    # ------------------------------------------------------------ organise
    @app.post("/scenarios/organise")
    async def post_organise(req: Request):
        body, bad = await read_body(req)
        if bad is not None:
            return bad
        extra = sorted(set(body) - {"shocks", "instruction", "rule"})
        if extra:
            return err("invalid_request", f"unknown keys: {extra}")
        rule = body.get("instruction", body.get("rule"))
        try:
            return organise(body.get("shocks"), rule)
        except ScenarioError as exc:
            return refuse(exc)

    # ------------------------------------------------------------ run
    def run_options(body):
        target = body.get("target")
        if target not in TARGETS:
            return None, err("invalid_request", f"target must be one of {list(TARGETS)}")
        rev, bad = parse_rev(body)
        if bad is not None:
            return None, bad
        profile = body.get("profile")
        if profile is not None and (target != "chain" or rev != 2 or profile not in ("d", "e")):
            return None, err("invalid_request", "profile 'd' or 'e' needs target chain and engine_rev 2")
        seed = body.get("seed", SIM_DEFAULT_SEED)
        if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 1_000_000:
            return None, err("invalid_request", "seed must be an integer from 0 to 1000000")
        weather = body.get("weather", False)
        if not isinstance(weather, bool):
            return None, err("invalid_request", "weather must be a boolean")
        return {"target": target, "engine_rev": rev, "profile": profile, "seed": seed,
                "weather": weather}, None

    def shock_start_iso(scn, s):
        return fmt_iso(parse_iso(scn["start_at"]) + s["start_offset_d"] * 86400.0)

    def disruption_of(scn, s):
        return {"node_id": s["place_id"], "kind": s["kind"], "capacity_loss": s["capacity_loss"],
                "duration_d": s["duration_d"], "shape": s["shape"],
                "started_at": shock_start_iso(scn, s)}

    async def execute(req, scn, sid, opts):
        v = await run_in_threadpool(validated, scn, opts["engine_rev"])
        target = opts["target"]
        if not v["runnable"] or not v["accepted"][target]:
            bad_rows = [{"id": r["id"], "place_id": r["place_id"], "verdict": r["verdicts"][target]}
                        for r in v["shocks"] if not r["verdicts"][target]["ok"]]
            first = bad_rows[0]["verdict"] if bad_rows else {}
            return _refused("scenario_refused",
                            f"target {target}: {len(bad_rows)} shock(s) refused: {first.get('reason')}",
                            {"refused": bad_rows, "target": target})
        scn = v["normalised"]
        order = chain_order(scn)
        label = "MODELLED" if target == "chain" else "SIMULATED"
        base = {"scenario_id": sid, "target": target, "engine_rev": opts["engine_rev"],
                "label": label, "start_at": scn["start_at"], "shock_count": len(order),
                "overlaps": v["overlaps"], "span": v["span"], "exactness": semantics()[target]}
        if target == "chain":
            rows, recs = [], []
            for n, s in enumerate(order, 1):
                req_body = {"source": "scenario", "scenario": {"disruption": disruption_of(scn, s)}}
                if opts["engine_rev"] == 2:
                    req_body["engine_rev"] = 2
                if opts["profile"]:
                    req_body["profile"] = opts["profile"]
                status, out = await _asgi(app, req, "POST", "/run", req_body)
                if status not in (200, 201) or not isinstance(out, dict) or "run_id" not in out:
                    e = (out or {}).get("error", {}) if isinstance(out, dict) else {}
                    return _refused("scenario_refused",
                                    f"shock {s['id']} refused by the chain engine: {e.get('message', status)}",
                                    {"refused": [{"id": s["id"], "place_id": s["place_id"],
                                                  "verdict": {"ok": False, "code": e.get("code"),
                                                              "reason": e.get("message")}}],
                                     "target": target, "runs": rows})
                rows.append({"order": n, "shock_id": s["id"], "place_id": s["place_id"],
                             "place_name": (graph.nodes.get(s["place_id"]) or {}).get("name"),
                             "kind": s["kind"], "run_id": out["run_id"],
                             "event_count": out.get("event_count"),
                             "stream_hash": str(out.get("stream_hash", ""))[:16],
                             "starts_at": shock_start_iso(scn, s),
                             "start_offset_d": s["start_offset_d"], "duration_d": s["duration_d"]})
                recs.append((s, await run_in_threadpool(runs.load, out["run_id"])))
            hit_ids = {ev["node_id"] for _, rec in recs if rec for ev in rec["events"]}
            names = {nid: (nd or {}).get("name") for nid, nd in graph.nodes.items() if nid in hit_ids}
            combined = combined_table([(s, rec) for s, rec in recs if rec], names)
            return {**base, "runs": rows, "combined": combined}
        # sim: every shock a native capacity window in ONE run
        last_end = max(s["start_offset_d"] + s["duration_d"] for s in order)
        ticks = min(SIM_HORIZON_D, max(14, int(math.ceil(last_end)) + 7))
        req_body = {"disruptions": [disruption_of(scn, s) for s in order], "tick": "day",
                    "ticks": ticks, "seed": opts["seed"], "start_at": scn["start_at"],
                    "weather": opts["weather"]}
        status, out = await _asgi(app, req, "POST", "/sim/run", req_body)
        if status not in (200, 201, 202) or not isinstance(out, dict):
            e = (out or {}).get("error", {}) if isinstance(out, dict) else {}
            return err(e.get("code", "sim_build_failed"), e.get("message", "simulation could not be started"))
        sim_id = out.get("sim_run_id") or out.get("run_id") or out.get("id")
        return {**base, "sim_run_id": sim_id, "status": out.get("status", "building"),
                "ticks": ticks, "seed": opts["seed"], "weather": opts["weather"],
                "windows": [{"shock_id": s["id"], "sim_id": f"d{i + 1}", "place_id": s["place_id"],
                             "starts_at": shock_start_iso(scn, s), "duration_d": s["duration_d"],
                             "capacity_loss": s["capacity_loss"]} for i, s in enumerate(order)]}

    @app.post("/scenarios/run")
    @_self_managed
    async def post_run_inline(req: Request):
        body, bad = await read_body(req)
        if bad is not None:
            return bad
        extra = sorted(set(body) - {"scenario", "target", "engine_rev", "profile", "seed", "weather"})
        if extra:
            return err("invalid_request", f"unknown keys: {extra}")
        opts, bad = run_options(body)
        if bad is not None:
            return bad
        try:
            return await execute(req, body.get("scenario"), None, opts)
        except ScenarioError as exc:
            return refuse(exc)

    @app.post("/scenarios/{scenario_id_}/run")
    @_self_managed
    async def post_run_saved(scenario_id_: str, req: Request):
        body, bad = await read_body(req)
        if bad is not None:
            return bad
        extra = sorted(set(body) - {"target", "engine_rev", "profile", "seed", "weather"})
        if extra:
            return err("invalid_request", f"unknown keys: {extra}")
        opts, bad = run_options(body)
        if bad is not None:
            return bad
        scn = await run_in_threadpool(scn_store.load, scenario_id_)
        if scn is None:
            return err("unknown_scenario", f"unknown scenario_id: {scenario_id_[:40]!r}")
        try:
            return await execute(req, scn, scenario_id_, opts)
        except ScenarioError as exc:
            return refuse(exc)
