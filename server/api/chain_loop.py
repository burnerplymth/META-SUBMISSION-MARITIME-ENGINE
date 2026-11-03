"""Chain scenario loop (API only): journal, compare, verify, shock book,
scenario library.

Mounted from app.py by one hook (register_chain_loop). Read-only over the
run store; nothing here creates a run, mutates a stored record, or feeds
the engine. Works for both engines: a stored run carries its engine rev
in its journal (absent = rev 1, ENGINE REV 2 = labelled), and replay /
fork dry-runs dispatch on it.

Routes
  GET  /run/{run_id}/journal   settings + shocks in order + lineage
  GET  /compare/{a}/{b}        diverges_from_seq, shared prefix, per-place
                               delta table, channel counts
  GET  /run/{run_id}/verify    stream sha256 + replay, byte for byte
  POST /shocks/check           shock book: accepted, or refused with a reason
  GET  /shocks/rules           the bounds the shock book enforces
  GET  /scenarios              curated, parameterised scenario library

Vocabulary: places, legs, causal chain. Payload text is labels and codes,
no explanatory prose.
"""
from __future__ import annotations

from fastapi import Request

from engine.canonical import canonical_json, sha256_hex, stream_hash
from engine.constants import const
from engine.engine import (DISRUPTION_KINDS, SHAPES, EngineError, validate_disruption,
                           validate_params)
from engine.fork import divergence, fork_events

CHANNELS = ("queue", "congestion", "reroute", "mode-shift", "inventory", "cost")

# Curated scenario library. Every place id is verified against the served
# map at request time; a scenario whose place is
# absent is omitted, never faked. Durations are the options the UI offers,
# not claims about how long a real event lasts.
_SCENARIOS = [
    {"id": "suez-closure", "label": "Suez Canal closure", "place_id": "chk_suez",
     "kind": "closure", "duration_options_d": [3, 7, 14, 30, 60], "default_duration_d": 14},
    {"id": "bab-el-mandeb-closure", "label": "Red Sea / Bab el-Mandeb closure",
     "place_id": "chk_babelmandeb", "kind": "closure",
     # 30 d, not 14: a 14 d closure of this strait returns ONE event on engine rev 1 (only the
     # shocked place; the engine's queue thresholds are not crossed downstream within 14 d), which
     # looks broken as the second library entry. Measured: 1 / 14 / 30 events at 14 / 30 / 60 d.
     "duration_options_d": [3, 7, 14, 30, 60, 180], "default_duration_d": 30},
    {"id": "panama-slot-cut", "label": "Panama Canal slot cut", "place_id": "chk_panama",
     "kind": "capacity_cut", "capacity_loss_options": [0.25, 0.5, 0.75],
     "default_capacity_loss": 0.5, "duration_options_d": [14, 30, 90, 215],
     "default_duration_d": 30},
    {"id": "malacca-closure", "label": "Strait of Malacca closure", "place_id": "chk_malacca",
     "kind": "closure", "duration_options_d": [3, 7, 14, 30], "default_duration_d": 14},
    {"id": "shanghai-port-closure", "label": "Shanghai port closure", "place_id": "sea_shanghai",
     "kind": "closure", "duration_options_d": [3, 7, 14, 30], "default_duration_d": 7},
    {"id": "rotterdam-strike", "label": "Rotterdam port strike", "place_id": "sea_rotterdam",
     "kind": "strike", "capacity_loss_options": [0.25, 0.5, 0.75],
     "default_capacity_loss": 0.5, "duration_options_d": [3, 7, 14, 30],
     "default_duration_d": 7},
    {"id": "la-long-beach-congestion", "label": "Los Angeles / Long Beach congestion",
     "place_id": "sea_losangeles", "related_place_ids": ["sea_longbeach"],
     "kind": "congestion", "capacity_loss_options": [0.25, 0.5, 0.78],
     "default_capacity_loss": 0.5, "duration_options_d": [14, 30, 58, 90],
     "default_duration_d": 30},
    {"id": "singapore-congestion", "label": "Singapore port congestion",
     "place_id": "sea_singapore", "kind": "congestion",
     "capacity_loss_options": [0.25, 0.5, 0.75], "default_capacity_loss": 0.5,
     "duration_options_d": [3, 7, 14, 30], "default_duration_d": 7},
]


def _rev_of(journal: dict) -> int:
    return 2 if journal.get("engine_rev") == 2 else 1


def _label(rev: int) -> str:
    return f"ENGINE REV {rev}"


def _label_j(journal: dict) -> str:
    """The label a journal was run under: a rev-2 journal carries its own (a profile run says
    ENGINE REV 2 \u00b7 PROFILE d); anything else is the plain revision label."""
    rev = _rev_of(journal)
    return (journal.get("engine_label") or _label(rev)) if rev == 2 else _label(rev)


def register_chain_loop(app, ctx: dict) -> None:
    err = ctx["err"]
    engine = ctx["engine"]
    engine2 = ctx.get("engine2")
    store = ctx["store"]
    replay_journal = ctx["replay_journal"]
    state_from_record = ctx["state_from_record"]

    def _flag(req: Request, name: str, default: int):
        """Query integer, standard-shape 422 on junk (never FastAPI's own)."""
        raw = req.query_params.get(name)
        if raw is None:
            return default, None
        try:
            return int(raw), None
        except ValueError:
            return None, err("invalid_request", f"{name} must be an integer")

    def _load(run_id):
        try:
            record = store.load(run_id)
        except ValueError:
            record = None
        return record

    def _shock_row(n, journal):
        d = journal["disruption"]
        return {"n": n, "kind": "SHOCK", "place_id": d["node_id"],
                "shock_kind": d["kind"], "size": d["capacity_loss"],
                "duration_d": d["duration_d"], "shape": d["shape"],
                "starts_at": d.get("started_at"), "at_seq": None}

    def _chain_of(record):
        """Journals from the root run down to this run (root first)."""
        chain = []
        cur_id, cur_j = record["run_id"], record["journal"]
        while True:
            chain.append((cur_id, cur_j))
            if cur_j.get("kind") != "fork":
                break
            cur_id, cur_j = cur_j["parent_run_id"], cur_j["parent_journal"]
        chain.reverse()
        return chain

    @app.get("/run/{run_id}/journal")
    async def get_run_journal(run_id: str, req: Request):
        raw, bad = _flag(req, "raw", 0)
        if bad is not None:
            return bad
        record = _load(run_id)
        if record is None:
            return err("unknown_run", f"unknown run_id: {run_id!r}")
        journal = record["journal"]
        rev = _rev_of(journal)
        chain = _chain_of(record)
        root_id, root_j = chain[0]
        shocks = [_shock_row(1, root_j)]
        lineage = [{"run_id": root_id, "parent_run_id": None, "at_seq": None,
                    "engine_rev": _rev_of(root_j)}]
        n = 1
        # branch place of each fork = the place of the parent's event at at_seq
        for (rid, j) in chain[1:]:
            n += 1
            at_seq = j["at_seq"]
            parent_id = j["parent_run_id"]
            parent_rec = _load(parent_id)
            place = None
            if parent_rec is not None and 0 <= at_seq < len(parent_rec["events"]):
                place = parent_rec["events"][at_seq]["node_id"]
            shocks.append({"n": n, "kind": "FORK SHOCK", "place_id": place,
                           "at_seq": at_seq, "overrides": j["overrides"]})
            lineage.append({"run_id": rid, "parent_run_id": parent_id, "at_seq": at_seq,
                            "engine_rev": _rev_of(j)})
        settings = {"params": root_j["params"],
                    "engine_rev": rev, "engine_label": _label_j(journal),
                    "stream_hash": record["stream_hash"],
                    "event_count": record["event_count"],
                    "map_version": journal.get("graph_version")}
        if rev == 2:
            settings["plugin_registry_hash"] = journal.get("engine2_registry_hash")
            if journal.get("profile"):
                settings["profile"] = journal["profile"]
            if root_j.get("routes"):
                settings["routes"] = root_j["routes"]
            if root_j.get("extra"):
                settings["extra"] = root_j["extra"]
        if root_j.get("live_disruption_id"):
            settings["live_disruption_id"] = root_j["live_disruption_id"]
        out = {"run_id": run_id, "kind": journal["kind"], "engine_rev": rev,
               "engine_label": _label_j(journal), "settings": settings, "shocks": shocks,
               "lineage": lineage}
        if raw:
            out["journal"] = journal
        return out

    def _per_place(events):
        """First-hit (non-cost) summary per place, plus cost presence."""
        rows: dict = {}
        for ev in events:
            nid = ev["node_id"]
            row = rows.setdefault(nid, {"first": None, "cost": None})
            if ev["channel"] == "cost":
                if row["cost"] is None:
                    row["cost"] = ev
            elif row["first"] is None:
                row["first"] = ev
        return rows

    def _channel_counts(events):
        c = {ch: 0 for ch in CHANNELS}
        for ev in events:
            c[ev["channel"]] = c.get(ev["channel"], 0) + 1
        return c

    @app.get("/compare/{a}/{b}")
    async def get_compare(a: str, b: str):
        ra, rb = _load(a), _load(b)
        for rid, rec in ((a, ra), (b, rb)):
            if rec is None:
                return err("unknown_run", f"unknown run_id: {rid!r}")
        ea, eb = ra["events"], rb["events"]
        div = divergence(ea, eb)
        shared = div if div is not None else len(ea)
        pa, pb = _per_place(ea), _per_place(eb)
        rows = []
        for pid in sorted(set(pa) | set(pb)):
            xa = (pa.get(pid) or {}).get("first")
            xb = (pb.get(pid) or {}).get("first")
            ca = (pa.get(pid) or {}).get("cost")
            cb = (pb.get(pid) or {}).get("cost")
            da = xa["delay_d"] if xa else None
            db = xb["delay_d"] if xb else None
            delta = round((db or 0.0) - (da or 0.0), 6)
            only = None
            if (xa or ca) and not (xb or cb):
                only = "a"
            elif (xb or cb) and not (xa or ca):
                only = "b"
            rows.append({
                "place_id": pid, "only_in": only,
                "channel_a": xa["channel"] if xa else None,
                "channel_b": xb["channel"] if xb else None,
                "first_seq_a": xa["seq"] if xa else None,
                "first_seq_b": xb["seq"] if xb else None,
                "delay_d_a": da, "delay_d_b": db, "delay_delta_d": delta,
                "magnitude_a": xa["magnitude"] if xa else None,
                "magnitude_b": xb["magnitude"] if xb else None,
                "magnitude_delta": round(((xb or {}).get("magnitude") or 0.0)
                                         - ((xa or {}).get("magnitude") or 0.0), 6),
                "cost_a": ca["magnitude"] if ca else None,
                "cost_b": cb["magnitude"] if cb else None,
                "cost_delta": round(((cb or {}).get("magnitude") or 0.0)
                                    - ((ca or {}).get("magnitude") or 0.0), 6),
            })
        rows.sort(key=lambda r: (-abs(r["delay_delta_d"]), -abs(r["cost_delta"]),
                                 r["place_id"]))
        cha, chb = _channel_counts(ea), _channel_counts(eb)
        delta_ch = {ch: chb.get(ch, 0) - cha.get(ch, 0) for ch in cha}
        ja, jb = ra["journal"], rb["journal"]
        return {
            "a": {"run_id": a, "engine_rev": _rev_of(ja), "event_count": len(ea),
                  "stream_hash": ra["stream_hash"]},
            "b": {"run_id": b, "engine_rev": _rev_of(jb), "event_count": len(eb),
                  "stream_hash": rb["stream_hash"]},
            "same_engine_rev": _rev_of(ja) == _rev_of(jb),
            "identical": div is None,
            "diverges_from_seq": div,
            "shared_prefix_len": shared,
            "places": rows,
            "channels": {"a": cha, "b": chb, "delta": delta_ch},
        }

    @app.get("/run/{run_id}/verify")
    async def get_run_verify(run_id: str):
        record = _load(run_id)
        if record is None:
            return err("unknown_run", f"unknown run_id: {run_id!r}")
        journal = record["journal"]
        stored_events = record["events"]
        recomputed = stream_hash(stored_events)
        out = {"run_id": run_id, "engine_rev": _rev_of(journal),
               "engine_label": _label_j(journal),
               "event_count": len(stored_events),
               "stored_stream_hash": record["stream_hash"],
               "recomputed_stream_hash": recomputed,
               "stored_hash_matches": recomputed == record["stream_hash"]}
        try:
            state = replay_journal(engine, journal, run_id)
        except Exception as exc:     # a journal that cannot replay is reported
            out.update(replay_stream_hash=None, replay_event_count=None,
                       replay_matches=False, byte_identical=False,
                       first_difference_seq=None, verified=False,
                       replay_error=type(exc).__name__)
            return out
        replay_hash = stream_hash(state.events)
        a = canonical_json(stored_events)
        b = canonical_json(state.events)
        first_diff = None
        if a != b:
            first_diff = divergence_seq(stored_events, state.events)
        out.update(replay_stream_hash=replay_hash,
                   replay_event_count=len(state.events),
                   replay_matches=replay_hash == record["stream_hash"],
                   byte_identical=a == b,
                   canonical_bytes=len(a.encode("utf-8")),
                   canonical_sha256=sha256_hex(a),
                   first_difference_seq=first_diff,
                   verified=bool(out["stored_hash_matches"] and a == b))
        return out

    def divergence_seq(ea, eb):
        for i, (x, y) in enumerate(zip(ea, eb)):
            if canonical_json(x) != canonical_json(y):
                return i
        return min(len(ea), len(eb)) if len(ea) != len(eb) else None

    # ------------------------------------------------------- shock book
    @app.get("/shocks/rules")
    async def get_shock_rules():
        return {"kinds": sorted(DISRUPTION_KINDS), "shapes": sorted(SHAPES),
                "capacity_loss": {"min_exclusive": 0.0, "max": 1.0,
                                  "closure_forces": const("closure_capacity_loss")},
                "duration_d": {"min_exclusive": 0.0, "max": const("duration_max_d")},
                "params": {"queue_sensitivity": list(const_bounds("queue_sensitivity_default")),
                           "hit_threshold": list(const_bounds("hit_threshold_default"))},
                "fork_overrides": ["capacity_loss", "duration_d", "shape", "params",
                                   "node_params"],
                "engine_revs": [1, 2] if engine2 is not None else [1]}

    def const_bounds(name):
        from engine.constants import const_entry
        return const_entry(name)["bounds"]

    @app.post("/shocks/check")
    async def post_shock_check(req: Request):
        """Dry-run validation of a /run or /fork input: accepted, or
        refused with the code and reason /run or /fork would answer with.
        Creates nothing."""
        try:
            body = await req.json()
        except Exception:
            return err("bad_json", "request body must be JSON")
        if not isinstance(body, dict):
            return err("invalid_request", "request body must be an object")
        rev = body.get("engine_rev")
        if rev is not None and (isinstance(rev, bool) or rev not in (1, 2)):
            return err("invalid_request", "engine_rev must be 1 or 2")
        if rev == 2 and engine2 is None:
            return err("invalid_request", "engine_rev 2 is unavailable")
        params_in = body.get("params") or {}
        if not isinstance(params_in, dict):
            return err("invalid_request", "params must be an object")
        try:
            validate_params(params_in)
            if "run_id" in body or "at_seq" in body or "overrides" in body:
                return _check_fork(body)
            spec = body.get("disruption")
            if spec is None:
                scen = body.get("scenario")
                spec = scen.get("disruption") if isinstance(scen, dict) else None
            if spec is None:
                raise EngineError("disruption (or scenario.disruption) is required")
            norm = validate_disruption(engine.graph, spec)
        except EngineError as exc:
            return {"accepted": False, "refused": {"code": exc.code, "reason": str(exc)},
                    "shock": None}
        return {"accepted": True, "refused": None, "shock": norm,
                "kind": "SHOCK", "engine_rev": rev or 1}

    def _check_fork(body):
        parent_id = body.get("run_id")
        record = _load(parent_id) if isinstance(parent_id, str) else None
        if record is None:
            raise EngineError(f"unknown run_id: {parent_id!r}", code="unknown_run")
        at_seq = body.get("at_seq")
        if not isinstance(at_seq, int) or isinstance(at_seq, bool):
            raise EngineError("at_seq must be an integer")
        overrides = body.get("overrides")
        if not isinstance(overrides, dict) or not overrides:
            raise EngineError("overrides must be a non-empty object", code="empty_overrides")
        pj = record["journal"]
        rev = _rev_of(pj)
        if rev == 2:
            from engine2.fork import fork_events2
            from engine2.journal import engine_for
            parent = replay_journal(engine, pj, parent_id)
            child = fork_events2(engine_for(engine2, pj), parent, pj["disruption"], pj["params"],
                                 at_seq, overrides, "check")
        else:
            parent = state_from_record(record)
            child = fork_events(engine, parent, pj["disruption"], pj["params"],
                                at_seq, overrides, "check")
        return {"accepted": True, "refused": None, "kind": "FORK SHOCK",
                "engine_rev": rev, "at_seq": at_seq, "overrides": overrides,
                "diverges_from_seq": divergence(parent.events, child.events)}

    # ----------------------------------------------------------- scenarios
    @app.get("/scenarios")
    async def get_scenarios(req: Request):
        engine_rev, bad = _flag(req, "engine_rev", 1)
        if bad is not None:
            return bad
        if engine_rev not in (1, 2) or (engine_rev == 2 and engine2 is None):
            return err("invalid_request", "engine_rev must be 1 or 2")
        out = []
        for s in _SCENARIOS:
            node = engine.graph.nodes.get(s["place_id"])
            if node is None:        # never serve a scenario whose place is absent
                continue
            kind = s["kind"]
            loss = (const("closure_capacity_loss") if kind == "closure"
                    else s["default_capacity_loss"])
            disruption = {"node_id": s["place_id"], "kind": kind, "capacity_loss": loss,
                          "duration_d": s["default_duration_d"], "shape": "step"}
            request = {"source": "scenario", "scenario": {"disruption": disruption}}
            if engine_rev == 2:
                request["engine_rev"] = 2
            row = {k: v for k, v in s.items()}
            row.update({"place_name": node["name"], "place_kind": node["kind"],
                        "verified": True, "request": request,
                        "shapes": ["step", "ramp", "pulse"]})
            out.append(row)
        return {"scenarios": out, "count": len(out), "engine_revs": [1, 2] if engine2 else [1]}
