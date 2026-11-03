"""PORT TWIN routes. Registered from ports.routes.register_ports_routes.

  POST /ports/{place_id}/sim                       run (sync when small, else a job)
  GET  /ports/{place_id}/sim/{sim_id}              status, journal, summary, provenance
  GET  /ports/{place_id}/sim/{sim_id}/frames       windowed compact animation frames
  GET  /ports/{place_id}/sim/{sim_id}/kpis         KPI summary + hourly timeline
  GET  /ports/{place_id}/sim/{sim_id}/compare      against a baseline (default: no scenario)
  GET  /ports/{place_id}/sim/{sim_id}/verify       replay from the journal
  GET  /ports/{place_id}/sim-profile               the port's simulated structure
  GET  /portsim/registry                           plug-in registry

Errors use the house shape {"error": {"code", "message"}}.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from fastapi import Request
from fastapi.responses import JSONResponse

from . import chain as chain_mod
from . import output
from .classes import PortSimError
from .profile import Sources
from .registry import (MODEL_LABEL, MODEL_VERSION, REGISTRY_HASH,
                       registry_table, plug)
from .service import PortSimService, tier_block

SCHEMA = "portsim/1"
DOCTRINE = [
    "SIMULATED and MODELLED: a deterministic operational picture, not an "
    "observation and not a forecast.",
    "No random numbers; ranges are the named variants low / base / high.",
    "SHELL places are simulated for visualisation only; nothing here enters "
    "the scorecard.",
    "TEU and tonnes are never added; no market data is an input.",
]
_SVC_LOCK = threading.Lock()


def _err(status, code, message):
    return JSONResponse(status_code=status,
                        content={"error": {"code": code,
                                           "message": str(message)[:400]}})


def _service(app) -> PortSimService:
    st = app.state
    root = Path(getattr(st, "data_root"))
    key = (str(st.graph_path), str(st.impact_snapshots_dir), str(root))
    with _SVC_LOCK:
        svc = getattr(st, "portsim_service", None)
        if svc is None or getattr(st, "portsim_service_key", None) != key:
            svc = PortSimService(
                Sources(Path(st.graph_path), Path(st.impact_snapshots_dir),
                        root / "ports_geo" / "profiles"),
                store_dir=root / "portsim_runs")
            st.portsim_service, st.portsim_service_key = svc, key
        return svc


_E2_CACHE: dict = {}


def _resolve_chain_items(app, place_id, body):
    """Replace {"type": "chain_replay", "run_id": ...} items by the explicit
    chain_path item the engine run implies at this place (so the journal
    carries the numbers, not a reference to a store)."""
    sc = body.get("scenario") if isinstance(body, dict) else None
    if not isinstance(sc, list):
        return body
    out, changed = [], False
    for it in sc:
        if not (isinstance(it, dict) and it.get("type") == "chain_replay"):
            out.append(it)
            continue
        extra = set(it) - {"type", "run_id", "step_h"}
        if extra:
            raise PortSimError("invalid_scenario",
                               f"chain_replay: unknown key(s) {sorted(extra)}")
        rid = it.get("run_id")
        if not isinstance(rid, str):
            raise PortSimError("invalid_scenario", "chain_replay needs run_id")
        step_h = it.get("step_h", 24.0)
        if isinstance(step_h, bool) or not isinstance(step_h, (int, float)) or step_h <= 0:
            raise PortSimError("invalid_scenario", "chain_replay step_h must be > 0")
        st = app.state
        store = getattr(st, "store", None)
        rec = store.load(rid) if store is not None else None
        if rec is None:
            raise PortSimError("run_not_found", f"unknown run_id: {rid!r}", 404)
        j = rec.get("journal", {})
        # The replay below re-runs the DEFAULT ENGINE REV 2 on the journaled disruption. A run made
        # with any layer that changes the engine (services routes table, profile d, graph extra)
        # would be replayed as a different model and then labelled with its run id.
        if (j.get("engine_rev") != 2 or j.get("routes") or j.get("profile") or j.get("extra")
                or "disruption" not in j):
            raise PortSimError("chain_replay_unsupported",
                               "chain_replay needs a plain ENGINE REV 2 run "
                               "(no routes table, profile or extra layer)")
        eng_v1 = getattr(st, "engine", None)
        if eng_v1 is None:
            raise PortSimError("chain_replay_unsupported", "engine unavailable", 503)
        try:
            from engine2 import Engine2
        except Exception:  # pragma: no cover
            from server.engine2 import Engine2  # type: ignore
        key = id(eng_v1.graph)
        eng = _E2_CACHE.get(key)
        if eng is None:
            _E2_CACHE.clear()
            eng = _E2_CACHE[key] = Engine2(eng_v1.graph)
        eng.run("portsim-replay", j["disruption"], j.get("params"))
        path = chain_mod.chain_path(eng, place_id, step_h=float(step_h))
        item = chain_mod.to_scenario_item(path, days=body.get("days", 7)
                                          if isinstance(body.get("days", 7), int) else 7)
        item["source"]["run_id"] = rid
        out.append(item)
        changed = True
    return {**body, "scenario": out} if changed else body


def _hours(req, name, default):
    raw = req.query_params.get(name)
    if raw is None:
        return default
    try:
        v = float(raw)
    except ValueError:
        raise PortSimError("invalid_request", f"{name} must be a number (hours)")
    if v != v or abs(v) > 1e6:
        raise PortSimError("invalid_request", f"{name} out of range")
    return v


def _links(place_id, sid):
    b = f"/ports/{place_id}/sim/{sid}"
    return {"self": b, "frames": b + "/frames", "kpis": b + "/kpis",
            "compare": b + "/compare", "verify": b + "/verify"}


def _head(run) -> dict:
    spec = run.spec
    return {"schema": SCHEMA, "sim_id": run.id, "place_id": spec.place_id,
            "name": spec.name, "status": "complete",
            "doctrine": DOCTRINE, "journal": run.journal,
            "links": _links(spec.place_id, run.id)}


def register_portsim_routes(app) -> None:
    @app.get("/portsim/registry")
    def portsim_registry():
        return {"schema": SCHEMA, "model": MODEL_LABEL, "model_version": MODEL_VERSION,
                "registry_hash": REGISTRY_HASH, "label": "MODELLED",
                "plugins": registry_table()}

    @app.get("/ports/{place_id}/sim-profile")
    def portsim_profile(place_id: str):
        try:
            spec = _service(app).canonicalise(place_id, {})[0]
        except PortSimError as exc:
            return _err(exc.status, exc.code, exc.message)
        return {"schema": SCHEMA, "label": "MODELLED", "place_id": place_id,
                "profile": spec.to_dict(), "profile_hash": spec.profile_hash,
                **tier_block(spec)}

    @app.post("/ports/{place_id}/sim")
    async def portsim_post(place_id: str, request: Request):
        try:
            raw = await request.body()
            if not raw.strip():
                body = {}
            else:
                try:
                    body = json.loads(raw)
                except ValueError:
                    raise PortSimError("invalid_request", "body is not valid JSON")
            body = _resolve_chain_items(app, place_id, body)
            status, sid, run = _service(app).create(place_id, body)
        except PortSimError as exc:
            return _err(exc.status, exc.code, exc.message)
        if run is None:
            return JSONResponse(status_code=202, content={
                "schema": SCHEMA, "sim_id": sid, "place_id": place_id,
                "status": "building", "links": _links(place_id, sid)})
        out = _head(run)
        out.update({"summary": run.summary, "provenance": _service(app).provenance(run),
                    "run_seconds": round(run.built_s, 3)})
        return JSONResponse(status_code=201, content=out)

    @app.get("/ports/{place_id}/sim/{sim_id}")
    def portsim_get(place_id: str, sim_id: str):
        svc = _service(app)
        try:
            run = svc.get(place_id, sim_id)
        except PortSimError as exc:
            return _err(exc.status, exc.code, exc.message)
        out = _head(run)
        out.update({"summary": run.summary, "provenance": svc.provenance(run),
                    "profile": run.spec.to_dict(), "run_seconds": round(run.built_s, 3)})
        return out

    @app.get("/ports/{place_id}/sim/{sim_id}/frames")
    def portsim_frames(place_id: str, sim_id: str, request: Request):
        svc = _service(app)
        try:
            run = svc.get(place_id, sim_id)
            T1h = run.result.t1 / 3600.0
            f = _hours(request, "from", 0.0)
            t = _hours(request, "to", min(T1h, f + 24.0))
            step_min = _hours(request, "step", 60.0)
            if step_min != int(step_min):
                raise PortSimError("invalid_request", "step must be whole minutes")
            body = output.frames(run.result, int(round(f * 3600)), int(round(t * 3600)),
                                 int(step_min) * 60, plug("run_limits")["frames_max"])
        except PortSimError as exc:
            return _err(exc.status, exc.code, exc.message)
        return {"schema": SCHEMA, "sim_id": sim_id, "place_id": place_id,
                "label": "SIMULATED", "t_unit": "seconds from run start (t=0 is the "
                "end of the warm-up; negative t is warm-up)",
                "start_at": run.journal["start_at"], **body}

    @app.get("/ports/{place_id}/sim/{sim_id}/kpis")
    def portsim_kpis(place_id: str, sim_id: str, request: Request):
        svc = _service(app)
        try:
            run = svc.get(place_id, sim_id)
            f = _hours(request, "from", 0.0)
            t = _hours(request, "to", run.result.t1 / 3600.0)
        except PortSimError as exc:
            return _err(exc.status, exc.code, exc.message)
        smp = [s for s in run.timeline["samples"] if f * 3600 <= s["t"] <= t * 3600]
        out = {"schema": SCHEMA, "sim_id": sim_id, "place_id": place_id,
               "label": "SIMULATED", "basis": "MODELLED",
               "start_at": run.journal["start_at"],
               "summary": run.summary,
               "timeline": {"step_s": run.timeline["step_s"], "samples": smp},
               **tier_block(run.spec)}
        if run.journal["scenario"]:
            # additive: the same run read against its own undisturbed
            # baseline (cached; the compare route uses the same run)
            try:
                base = svc.baseline_of(run)
                cmp_ = output.compare(base.result, run.result, base.summary,
                                      run.summary)
                out["vs_baseline"] = {
                    "baseline_id": base.id,
                    "diverges_at_h": cmp_["diverges_at_h"],
                    "baseline_state": cmp_["baseline_state"],
                    "delta": {r["measure"]: {k: r[k] for k in
                                             ("baseline", "scenario", "delta",
                                              "delta_pct", "direction") if k in r}
                              for r in cmp_["table"]}}
            except PortSimError as exc:
                out["vs_baseline"] = {"unavailable": exc.message}
        return out

    @app.get("/ports/{place_id}/sim/{sim_id}/compare")
    def portsim_compare(place_id: str, sim_id: str, request: Request):
        svc = _service(app)
        try:
            run = svc.get(place_id, sim_id)
            bid = request.query_params.get("baseline")
            base = svc.get(place_id, bid) if bid else svc.baseline_of(run)
            if base.id == run.id:
                raise PortSimError("invalid_request",
                                   "baseline is the run itself; give a baseline "
                                   "run id or run a scenario")
            for k in ("days", "warmup_days", "variant", "arrival_shape", "anchor",
                      "profile_hash"):
                if base.journal[k] != run.journal[k]:
                    raise PortSimError(
                        "not_comparable",
                        f"baseline differs in {k} ({base.journal[k]!r} vs "
                        f"{run.journal[k]!r}); compare runs that differ only in "
                        "their scenario", 422)
            body = output.compare(base.result, run.result, base.summary, run.summary)
        except PortSimError as exc:
            return _err(exc.status, exc.code, exc.message)
        return {"schema": SCHEMA, "place_id": place_id, "label": "SIMULATED",
                "basis": "MODELLED", "sim_id": run.id, "baseline_id": base.id,
                "scenario": run.journal["scenario"],
                "baseline_scenario": base.journal["scenario"], **body,
                **tier_block(run.spec)}

    @app.get("/ports/{place_id}/sim/{sim_id}/verify")
    def portsim_verify(place_id: str, sim_id: str):
        svc = _service(app)
        try:
            run = svc.get(place_id, sim_id)
            out = svc.verify(run)
        except PortSimError as exc:
            return _err(exc.status, exc.code, exc.message)
        return {"schema": SCHEMA, "place_id": place_id, "label": "SIMULATED", **out}
