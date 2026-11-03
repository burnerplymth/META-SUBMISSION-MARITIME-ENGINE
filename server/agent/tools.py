"""The agent's typed tool registry.

Every tool is a small, closed function over the app's own API (see
``apicall.py``) or the loaded graph. Three kinds:

  read    runs immediately, free, no state change. Content-addressed or
          idempotent server-side (a repeated call returns the same result).
  action  changes state or can spend money. The loop NEVER runs it on the
          model's say-so: it returns a pending-confirmation object and the
          tool executes only after the user's explicit confirm call.
          ``confirm == "if_live"`` means: only when the model transport is
          LIVE (the call can then cost money); on the recorded transport it
          is free and runs like a read.
  client_action
          UI control (open or float a window, apply a layout, switch mode,
          focus a place, show a run). It executes in the user's BROWSER, not
          here: the loop shows it as a pending client step, the browser
          performs it through the window controller and posts the result
          back. Pure view changes auto-run (and are shown in the timeline);
          a client action that changes data would set ``confirm="always"`` and
          use the same pending-confirmation object as an action tool.

The model's arguments are untrusted. ``prepare()`` validates them with our
own schema checker (``schema.py``), then with the tool's semantic check
(place ids must exist in the loaded graph, runs must exist, bounds are our
own and stricter than the API's). The same ``prepare()`` runs again at
confirm time. Nothing from the model is interpolated into a path before it
matches a strict id pattern.

Results are small summaries built here (never raw dumps), labelled
MODELLED or SIMULATED as the underlying route labels them. No tool reads or
writes anything inside engine propagation: the engine only ever runs behind
the API routes, exactly as for a human user.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable

from .schema import validate

PLACE_ID = {"type": "string", "pattern": r"[A-Za-z0-9_\-]{1,64}", "maxLength": 64}
RUN_ID = {"type": "string", "pattern": r"[A-Za-z0-9_\-]{1,80}", "maxLength": 80}
KINDS = ["closure", "capacity_cut", "congestion", "strike", "weather",
         "grounding", "accident", "policy", "other"]
SHAPES = ["step", "ramp", "pulse"]
# Agent-side bounds are tighter than the API's (duration_d <= 3650).
MAX_DURATION_D = 365.0


class ToolError(Exception):
    """A refusal or failure that is reported back to the model as data."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ToolContext:
    graph: object                       # engine Graph (all places)
    api: object                         # AsgiApi-like: .get(path, params), .post(path, body)
    live: bool = False                  # model transport is LIVE
    session_id: str = ""
    research: object = None             # ResearchState: evidence list, notes (research tools)
    provider: object = None             # the session's LLMProvider (write_research_note only)


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict
    kind: str                           # "read" | "action" | "client_action"
    run: Callable[[ToolContext, dict], dict]
    check: Callable[[ToolContext, dict], None] | None = None
    confirm: str = "never"              # "never" | "always" | "if_live"
    describe: Callable[[dict], str] | None = None
    est_cost_usd: float = 0.0           # when it can spend (live explain)
    # per-call cost estimate and a context-aware confirmation sentence (research
    # scene scans: cost and calls left depend on the arguments and the SAM meter)
    estimate: Callable[[ToolContext, dict], float] | None = None
    explain: Callable[[ToolContext, dict], str] | None = None

    def needs_confirmation(self, ctx: ToolContext) -> bool:
        if self.confirm == "always":
            return True
        return self.confirm == "if_live" and ctx.live

    def spec(self) -> dict:
        """The Responses API function-tool definition shown to the model."""
        return {"type": "function", "name": self.name,
                "description": self.description,
                "parameters": {**self.parameters, "additionalProperties": False}}


# ----------------------------------------------------------------- helpers
def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or []}


def _name(ctx: ToolContext, pid: str) -> str:
    n = ctx.graph.nodes.get(pid)
    return n.get("name", pid) if n else pid


def humanize(text: str, ctx: ToolContext, args: dict) -> str:
    """A confirmation sentence names places, not ids: swap each place id the
    arguments carry for its name (display only; the arguments stay as sent)."""
    for k in ("place_id", "origin", "destination"):
        pid = args.get(k)
        if isinstance(pid, str) and pid in ctx.graph.nodes:
            text = text.replace(pid, _name(ctx, pid))
    dis = args.get("disruption")
    if isinstance(dis, dict) and isinstance(dis.get("place_id"), str) and dis["place_id"] in ctx.graph.nodes:
        text = text.replace(dis["place_id"], _name(ctx, dis["place_id"]))
    return text


def require_place(ctx: ToolContext, pid: str, field_name: str = "place_id",
                  need_core: bool = False, need_sea: bool = False) -> dict:
    node = ctx.graph.nodes.get(pid)
    if node is None:
        raise ToolError("unknown_place",
                        f"{field_name} {pid[:64]!r} is not a place in the map; "
                        "use search_places to find a valid place id")
    if need_core and node.get("tier", "core") != "core":
        raise ToolError("place_not_modelled",
                        f"{node.get('name')} is a place the model holds no state for; "
                        "pick a core place (search_places shows the tier)")
    if need_sea and node.get("mode") != "sea":
        raise ToolError("not_a_sea_place", f"{node.get('name')} is not a sea place")
    return node


def _call(res, ok_codes=(200, 201, 202)):
    if res.status in ok_codes:
        if res.body is None:
            raise ToolError("bad_response", "the service returned no JSON")
        return res.body
    code, msg = res.error()
    raise ToolError(code, msg or f"service answered HTTP {res.status}")


def _r(x, nd=3):
    return None if x is None else round(float(x), nd)


def _range(block) -> dict | None:
    if not isinstance(block, dict):
        return None
    out = {k: _r(block.get(k)) for k in ("low", "median", "high")}
    out["unit"] = block.get("unit", "d")
    out["label"] = block.get("label")
    return out


def _check_run_exists(ctx: ToolContext, run_id: str) -> None:
    res = ctx.api.get(f"/run/{run_id}/journal")
    if res.status == 404:
        raise ToolError("unknown_run", f"run {run_id!r} does not exist; use list_runs")
    _call(res)


# ------------------------------------------------------------------ reads
def _search_places(ctx, a):
    q = a["query"].strip().lower()
    mode, kind, limit = a.get("mode"), a.get("kind"), a.get("limit", 8)
    scored = []
    for pid, n in ctx.graph.nodes.items():
        if mode and n.get("mode") != mode:
            continue
        if kind and n.get("kind") != kind:
            continue
        name = str(n.get("name", "")).lower()
        aliases = [str(x).lower() for x in n.get("aliases") or []]
        country = str(n.get("country") or "").lower()
        s = 0
        if q == pid.lower() or q == name or q in aliases:
            s = 100
        elif name.startswith(q):
            s = 80
        elif q in name.split() or any(q in al.split() for al in aliases):
            s = 65
        elif q in name or any(q in al for al in aliases):
            s = 50
        elif q in pid.lower():
            s = 40
        elif country == q:
            s = 20
        if s:
            if n.get("tier", "core") == "core":
                s += 5
            scored.append((-s, pid))
    scored.sort()
    rows = []
    for _, pid in scored[:limit]:
        n = ctx.graph.nodes[pid]
        rows.append({"id": pid, "name": n.get("name"), "country": n.get("country"),
                     "mode": n.get("mode"), "kind": n.get("kind"),
                     "tier": n.get("tier", "core")})
    return {"places": rows, "matches": len(scored)}


def _get_place(ctx, a):
    pid = a["place_id"]
    n = ctx.graph.nodes[pid]
    out_legs = ctx.graph.outbound(pid)
    in_legs = ctx.graph.inbound(pid)
    nb = {}
    for e in out_legs:
        nb.setdefault(e["to"], e["mode"])
    nbrs = [{"id": t, "name": _name(ctx, t), "mode": m} for t, m in sorted(nb.items())[:8]]
    return {"id": pid, "name": n.get("name"), "country": n.get("country"),
            "mode": n.get("mode"), "kind": n.get("kind"),
            "tier": n.get("tier", "core"),
            "runnable": n.get("tier", "core") == "core",
            "lat": n.get("lat"), "lon": n.get("lon"),
            "capacity": n.get("capacity"), "capacity_unit": n.get("capacity_unit"),
            "legs_out": len(out_legs), "legs_in": len(in_legs),
            "neighbours": nbrs, "label": "MODELLED"}


def _list_scenarios(ctx, a):
    body = _call(ctx.api.get("/scenarios"))
    rows = []
    for s in body.get("scenarios", []):
        if not s.get("verified", True):
            continue
        rows.append({k: s.get(k) for k in (
            "id", "label", "place_id", "kind", "default_duration_d",
            "default_capacity_loss", "duration_options_d", "capacity_loss_options")
            if s.get(k) is not None})
    return {"scenarios": rows}


def _list_runs(ctx, a):
    body = _call(ctx.api.get("/runs"))
    runs = body.get("runs", [])
    kind = a.get("kind")
    if kind:
        runs = [r for r in runs if r.get("kind") == kind]
    rows = [{"id": r.get("id"), "kind": r.get("kind"), "title": r.get("title"),
             "status": r.get("status"),
             "events": (r.get("facts") or {}).get("event_count")}
            for r in runs[: a.get("limit", 10)]]
    return {"runs": rows, "total": len(runs)}


def _run_summary(ctx, a):
    rid = a["run_id"]
    chain = _call(ctx.api.get(f"/chain/{rid}"))
    journal = _call(ctx.api.get(f"/run/{rid}/journal"))
    events = chain.get("events") or []
    by_place: dict[str, dict] = {}
    channels: dict[str, int] = {}
    for ev in events:
        channels[ev.get("channel")] = channels.get(ev.get("channel"), 0) + 1
        pid = ev.get("node_id")
        row = by_place.setdefault(pid, {"place_id": pid, "name": _name(ctx, pid),
                                        "first_seq": ev.get("seq"),
                                        "max_delay_d": 0.0, "channels": set()})
        row["max_delay_d"] = max(row["max_delay_d"], float(ev.get("delay_d") or 0.0))
        row["channels"].add(ev.get("channel"))
    top = sorted(by_place.values(), key=lambda r: (-r["max_delay_d"], r["first_seq"]))[:10]
    for r in top:
        r["max_delay_d"] = _r(r["max_delay_d"])
        r["channels"] = sorted(r["channels"])
    shocks = [{"place_id": s.get("place_id"), "name": _name(ctx, s.get("place_id")),
               "kind": s.get("shock_kind"), "size": s.get("size"),
               "duration_d": s.get("duration_d"), "shape": s.get("shape")}
              for s in journal.get("shocks", [])]
    return {"run_id": rid, "status": chain.get("status"),
            "engine": journal.get("engine_label"), "event_count": chain.get("event_count"),
            "stream_hash": str(chain.get("stream_hash", ""))[:16],
            "shocks": shocks, "channel_counts": channels,
            "places_reached": len(by_place), "top_places": top,
            "label": "MODELLED"}


def _compare_runs(ctx, a):
    body = _call(ctx.api.get(f"/compare/{a['a']}/{a['b']}"))
    rows = sorted(body.get("places", []),
                  key=lambda r: -abs(r.get("delay_delta_d") or 0.0))[:10]
    out_rows = [{"place_id": r.get("place_id"), "name": _name(ctx, r.get("place_id")),
                 "only_in": r.get("only_in"), "delay_d_a": r.get("delay_d_a"),
                 "delay_d_b": r.get("delay_d_b"), "delay_delta_d": r.get("delay_delta_d")}
                for r in rows]
    return {"a": body.get("a"), "b": body.get("b"),
            "same_engine_rev": body.get("same_engine_rev"),
            "identical": body.get("identical"),
            "diverges_from_seq": body.get("diverges_from_seq"),
            "places_compared": len(body.get("places", [])),
            "largest_differences": out_rows, "label": "MODELLED"}


def _verify_run(ctx, a):
    b = _call(ctx.api.get(f"/run/{a['run_id']}/verify"))
    keep = ("run_id", "engine_label", "event_count", "stored_hash_matches",
            "replay_matches", "byte_identical", "first_difference_seq", "verified")
    out = {k: b.get(k) for k in keep if k in b}
    out["stream_hash"] = str(b.get("stored_stream_hash", ""))[:16]
    return out


def _check_shock(ctx, a):
    body = _call(ctx.api.post("/shocks/check", {"scenario": {"disruption": _disruption(a)}}))
    return {"accepted": body.get("accepted"), "refused": body.get("refused"),
            "shock": body.get("shock")}


def _lane_plan(ctx, a):
    req = {"origin": a["origin"], "destination": a["destination"]}
    dis = a.get("disruption")
    if dis:
        req["disruption"] = {"place_id": dis["place_id"], "kind": dis["kind"],
                             "duration_d": dis["duration_d"]}
        if "capacity_loss" in dis:
            req["disruption"]["capacity_loss"] = dis["capacity_loss"]
    plan = _call(ctx.api.post("/lanes/plan", req))
    routes = []
    for r in plan.get("routes", [])[:4]:
        vb = r.get("vs_baseline")
        routes.append({"id": r.get("id"), "label": r.get("label"),
                       "baseline": r.get("is_baseline"),
                       "km": (r.get("km") or {}).get("value"),
                       "sail_d": _range(r.get("sail")),
                       "queue_d": _range(r.get("queue")),
                       "queue_d_measure": "run peak, not the wait at arrival",
                       "queue_at_arrival_d": _range(r.get("queue_at_arrival")),
                       "total_d": _range(r.get("total")),
                       "disrupted_place_on_route": r.get("disrupted_place_on_route"),
                       "vs_baseline": vb if isinstance(vb, dict) else None})
    checks = [{"id": c.get("id"), "state": c.get("state")} for c in plan.get("checks", [])]
    return {"plan_id": plan.get("plan_id"), "engine": plan.get("engine_label"),
            "origin": _name(ctx, a["origin"]), "destination": _name(ctx, a["destination"]),
            "routes": routes, "reference_checks": checks,
            "note": "ranges are the low and high of named variants, not forecasts"}


def _shipment_exposure(ctx, a):
    """Which shipments a scenario reaches, as ranges (read-only, stateless; the sample portfolio is synthetic)."""
    if a.get("source", "sample") == "given":
        ships = a["shipments"]
    else:
        ships = _call(ctx.api.get("/exposure/sample")).get("shipments", [])
    scenario = {"scenario_id": a["scenario_id"]}
    if "duration_d" in a:
        scenario["duration_d"] = a["duration_d"]
    doc = _call(ctx.api.post("/exposure/evaluate", {"shipments": ships, "scenario": scenario}))
    s = doc.get("summary") or {}
    lanes = [b for b in doc.get("by_lane", []) if b.get("status") in ("delayed", "rerouted")]
    lanes.sort(key=lambda b: (-b["n_shipments"], b["origin_name"], b["destination_name"]))
    return {
        "label": doc.get("label"), "portfolio": "Sample portfolio (synthetic)" if a.get("source", "sample") != "given" else "Given list",
        "scenario": (doc.get("scenario") or {}).get("label"),
        "shipments": s.get("n"), "affected": s.get("n_affected"), "share_affected": s.get("share_affected"),
        "added_days_median_range_d": s.get("added_days_median_range"),
        "value_in_affected_shipments_usd": s.get("value_in_affected_usd"),
        "value_note": "Value in affected shipments is the listed value, not a loss estimate",
        "lanes_affected": [{"origin": b["origin_name"], "destination": b["destination_name"], "shipments": b["n_shipments"],
                            "status": b["status"], "route": b.get("route_label"), "added_days_d": b.get("added_days")}
                           for b in lanes[:6]],
        "most_exposed_places": [{"name": p["name"], "shipments": p["n"]} for p in (s.get("most_exposed_places") or [])[:3]],
        "not_served": s.get("n_not_served"), "unresolved": s.get("n_unresolved"),
    }


_PORT_KPI_KEYS = ("wait_h", "queue", "port_time_h")


def _port_scenario(ctx, a):
    pid, typ = a["place_id"], a["scenario_type"]
    item: dict = {"type": typ}
    if typ in ("surge", "strike"):
        item["factor"] = a["factor"]
    item["start_h"] = a.get("start_h", 0)
    item["duration_h"] = a.get("duration_h", 48)
    res = ctx.api.post(f"/ports/{pid}/sim", {"days": a.get("days", 7), "scenario": [item]})
    body = _call(res)
    sid = body.get("sim_id")
    if res.status == 202:
        return {"status": "building", "sim_id": sid, "place_id": pid,
                "note": "call port_scenario again with the same arguments"}
    kp = _call(ctx.api.get(f"/ports/{pid}/sim/{sid}/kpis"))
    summ = kp.get("summary") or {}
    out = {"sim_id": sid, "place": _name(ctx, pid), "label": "SIMULATED",
           "scenario": item, "days": a.get("days", 7)}
    for k in _PORT_KPI_KEYS:
        if k in summ:
            out[k] = {kk: vv for kk, vv in summ[k].items() if kk != "note"}
    vb = kp.get("vs_baseline")
    if isinstance(vb, dict):
        out["vs_baseline"] = {"diverges_at_h": vb.get("diverges_at_h"),
                              "delta": dict(list((vb.get("delta") or {}).items())[:8])}
    return out


def _list_reports(ctx, a):
    t = _call(ctx.api.get("/reports/templates"))
    d = _call(ctx.api.get("/exports/datasets"))
    return {"report_templates": [{"id": x.get("id"), "title": x.get("title")}
                                 for x in t.get("templates", [])],
            "formats": t.get("formats"),
            "datasets": [{"id": x.get("id"), "label": x.get("label"),
                          "needs": x.get("needs")} for x in d.get("datasets", [])]}


def _explain_chain(ctx, a):
    b = _call(ctx.api.post("/llm/explain", {"run_id": a["run_id"]}))
    return {"run_id": b.get("run_id"), "narrative": str(b.get("narrative", ""))[:1500],
            "post_check": b.get("post_check"), "cached": b.get("cached"),
            "transport": b.get("transport"), "model": b.get("model"),
            "cost_usd": b.get("cost_usd", 0.0)}


def _sat_body(a: dict) -> dict:
    body = {"source": a.get("source", "sentinel-2"), "place": a["place_id"], "mode": "recorded"}
    if body["source"] == "sentinel-2":
        body["role"] = a.get("role", "now")
    return body


AERIAL_SCENES = ["lalb_pier_t", "valencia_container", "rotterdam_maasvlakte"]
AERIAL_PHRASES = ["ship", "container ship", "vessel", "crane"]


def _check_satellite_sam(ctx, a):
    if a.get("aerial_scene"):
        return
    if not a.get("place_id"):
        raise ToolError("missing_target", "give place_id (a sea place) or aerial_scene")
    require_place(ctx, a["place_id"], need_sea=True)


SAM_VERIFICATION = "Detected, not verified"     # shown with every SAM-derived count


def _satellite_aerial_sam(ctx, a):
    """RECORDED aerial scene (sub-metre survey tiles): what SAM found, with lengths in metres."""
    body = {"scene": a["aerial_scene"], "mode": "recorded"}
    if a.get("phrases"):
        body["phrases"] = a["phrases"]
    b = _call(ctx.api.post("/satwatch/aerial/run", body))
    found = b.get("found") or {}
    objs = b.get("objects") or []
    longest = max((o["length_m"] for o in objs if o.get("phrase") in AERIAL_PHRASES[:3]), default=None)
    return {"aerial_scene": b.get("scene_id"), "name": b.get("name"), "source_badge": b.get("badge"),
            "sam_found": found, "summary": "; ".join(f"{(b.get('found_words') or {}).get(p)} ({p})" for p in found),
            "verification": b.get("verification") or SAM_VERIFICATION, "human_reviewed": False,
            "longest_ship_m": longest, "mode": b.get("mode"), "label": "MODELLED",
            "max_score": max((o.get("score") or 0 for o in objs), default=None),
            "note": ("recorded real SAM 3.1 replies on native aerial survey tiles; detections of one dated "
                     "scene, not a census: ships longer than a tile or cut by the scene border are partial")}


def _satellite_sam(ctx, a):
    """RECORDED replay only: the agent holds no operator token, so it can never
    spend a SAM call; the route refuses a live mode without one anyway."""
    if a.get("aerial_scene"):
        return _satellite_aerial_sam(ctx, a)
    body = _sat_body(a)
    if a.get("prompt"):
        body["prompt"] = a["prompt"]
    b = _call(ctx.api.post("/satwatch/sam/run", body))
    found = b.get("found")
    return {"place_id": b.get("place"), "source": b.get("source"), "role": b.get("role"),
            "scene_date": b.get("scene_date"), "prompt": b.get("prompt"),
            "sam_found": found, "summary": "SAM found none" if found == 0 else f"SAM found {found}",
            "verification": b.get("verification") or SAM_VERIFICATION, "human_reviewed": False,
            "mode": b.get("mode"), "label": "MODELLED",
            "max_score": max((o.get("score") or 0 for o in b.get("objects") or []), default=None),
            "note": (f"{SAM_VERIFICATION}. A recorded real SAM 3.1 reply for this exact crop; SAM recall at 10 m is "
                     "unvalidated, so this is what SAM found, not how many there are")}


def _satellite_vision(ctx, a):
    b = _call(ctx.api.post("/satwatch/vision/run", _sat_body(a)))
    r = b.get("read") or {}
    return {"place_id": b.get("place"), "source": b.get("source"), "role": b.get("role"),
            "scene_date": b.get("scene_date"), "mode": b.get("mode"), "label": "MODELLED",
            "scene": r.get("scene"), "image_quality": r.get("image_quality"),
            "congestion_level": r.get("congestion_level"), "confidence": r.get("confidence"),
            "visible_features": [f.get("label") for f in (r.get("visible_features") or [])][:8],
            "cannot_see": (r.get("cannot_see") or [])[:5], "limits": (r.get("limits") or [])[:4],
            "note": "a recorded Meta AI vision read of the native crop; a model reading, not a count"}


# ------------------------------------------------------- scenario builder
_SHOCK_ID = {"type": "string", "pattern": r"[A-Za-z0-9_\-]{1,24}", "maxLength": 24}
SCENARIO_SHOCK = _obj({
    "id": _SHOCK_ID,
    "place_id": PLACE_ID,
    "kind": {"type": "string", "enum": KINDS},
    "start_offset_d": {"type": "number", "minimum": 0, "maximum": 365},
    "duration_d": {"type": "number", "exclusiveMinimum": 0, "maximum": MAX_DURATION_D},
    "capacity_loss": {"type": "number", "exclusiveMinimum": 0, "maximum": 1},
    "shape": {"type": "string", "enum": SHAPES},
    "note": {"type": "string", "maxLength": 140},
}, ["place_id", "kind", "duration_d"])
SCENARIO_RULE = _obj({
    "kind": {"type": "string", "enum": ["stagger", "sequence", "cluster", "align_start",
                                        "spread_evenly", "shift"]},
    "gap_d": {"type": "number", "minimum": -365, "maximum": 365},
    "reference": {"type": "string", "enum": ["end", "start"]},
    "order": {"type": "array", "maxItems": 12, "items": _SHOCK_ID},
    "from_d": {"type": "number", "minimum": 0, "maximum": 365},
    "within_d": {"type": "number", "minimum": 0, "maximum": 365},
    "day": {"type": "number", "minimum": 0, "maximum": 365},
    "window_d": {"type": "number", "minimum": 0, "maximum": 365},
    "delta_d": {"type": "number", "minimum": -365, "maximum": 365},
    "ids": {"type": "array", "maxItems": 12, "items": _SHOCK_ID},
    "snap_d": {"type": "number", "minimum": 0.01, "maximum": 1},
}, ["kind"])
SCENARIO_START_AT = {"type": "string", "maxLength": 30}


def _scenario_body(a: dict) -> dict:
    body = {"shocks": a["shocks"]}
    if a.get("start_at"):
        body["start_at"] = a["start_at"]
    if a.get("name"):
        body["name"] = a["name"]
    return body


def _check_scenario_shocks(ctx, a):
    seen = set()
    for i, s in enumerate(a.get("shocks") or []):
        require_place(ctx, s["place_id"], f"shocks[{i}].place_id")
        if s.get("id") is not None:
            if s["id"] in seen:
                raise ToolError("duplicate_shock_id", f"shocks[{i}].id is used twice")
            seen.add(s["id"])


def _scenario_rules(ctx, a):
    j = _call(ctx.api.get("/scenarios/rules"))
    return {"max_shocks": j.get("max_shocks"), "kinds": j.get("kinds"),
            "start_offset_d": j.get("start_offset_d"), "duration_d": j.get("duration_d"),
            "capacity_loss": j.get("capacity_loss"), "sim": j.get("sim"),
            "organise_rules": (j.get("organise") or {}).get("kinds"),
            "organise_params": (j.get("organise") or {}).get("params"),
            "semantics": j.get("semantics"), "label": "MODELLED"}


def _scenario_validate(ctx, a):
    body = {"scenario": _scenario_body(a)}
    if a.get("engine_rev"):
        body["engine_rev"] = a["engine_rev"]
    j = _call(ctx.api.post("/scenarios/validate", body))
    rows = []
    for r in j.get("shocks", []):
        v = r.get("verdicts") or {}
        ch, sm = v.get("chain") or {}, v.get("sim") or {}
        rows.append({"id": r.get("id"), "place": r.get("place_name") or r.get("place_id"),
                     "chain": "accepted" if ch.get("ok") else ch.get("reason"),
                     "sim": "accepted" if sm.get("ok") else sm.get("reason"),
                     "window": r.get("window")})
    return {"accepted": j.get("accepted"), "runnable": j.get("runnable"), "shocks": rows,
            "overlaps": j.get("overlaps"), "span": j.get("span"), "start_at": j.get("start_at"),
            "label": "MODELLED"}


def _scenario_run(ctx, a):
    opts = {"target": a["target"]}
    if a.get("engine_rev") == 2:
        opts["engine_rev"] = 2
    if a.get("scenario_id"):
        r = _call(ctx.api.post(f"/scenarios/{a['scenario_id']}/run", opts))
    else:
        r = _call(ctx.api.post("/scenarios/run", {"scenario": _scenario_body(a), **opts}))
    out = {"target": r.get("target"), "label": r.get("label"), "shock_count": r.get("shock_count"),
           "overlaps": len(r.get("overlaps") or []), "span": r.get("span")}
    if r.get("target") == "chain":
        out["runs"] = [{"shock_id": x["shock_id"], "place": x.get("place_name") or x["place_id"],
                        "run_id": x["run_id"], "events": x.get("event_count")} for x in r.get("runs", [])]
        top = ((r.get("combined") or {}).get("places") or [])[:5]
        out["combined_top"] = [{"place": p.get("name") or p["place_id"], "max_delay_d": p["max_delay_d"],
                                "sum_delay_d": p["sum_delay_d"]} for p in top]
        out["combined_note"] = "superposition of independent runs (approximated)"
    else:
        out.update(sim_run_id=r.get("sim_run_id"), status=r.get("status"), ticks=r.get("ticks"))
    return out


def _check_scenario_run(ctx, a):
    if bool(a.get("scenario_id")) == bool(a.get("shocks")):
        raise ToolError("invalid_arguments", "give either scenario_id or shocks, not both and not neither")
    if a.get("shocks"):
        _check_scenario_shocks(ctx, a)


def _check_scenario_rule(ctx, a):
    try:
        from scenario.organise import validate_rule
    except ImportError:        # imported as server.agent.* from the repo root
        from server.scenario.organise import validate_rule  # type: ignore
    try:
        validate_rule(a["rule"])
    except Exception as exc:   # ScenarioError carries a code and a message
        raise ToolError(getattr(exc, "code", "invalid_rule"), getattr(exc, "message", str(exc))) from None


def _d_scenario_run(a):
    n = len(a.get("shocks") or [])
    what = f"saved scenario {a.get('scenario_id')}" if a.get("scenario_id") else f"a scenario of {n} shock(s)"
    return f"Run {what} on the {a.get('target')} engine"


# ---------------------------------------------------------------- actions
def _disruption(a: dict) -> dict:
    d = {"node_id": a["place_id"], "kind": a["kind"],
         "duration_d": a.get("duration_d", 14), "shape": a.get("shape", "step")}
    if "capacity_loss" in a:
        d["capacity_loss"] = a["capacity_loss"]
    elif a["kind"] == "closure":
        d["capacity_loss"] = 1.0
    return d


def _check_disruption(ctx, a):
    require_place(ctx, a["place_id"], need_core=True)
    if a["kind"] == "capacity_cut" and "capacity_loss" not in a:
        raise ToolError("missing_capacity_loss", "capacity_cut needs capacity_loss")


def _start_run(ctx, a):
    body = {"source": "scenario", "scenario": {"disruption": _disruption(a)}}
    if a.get("engine_rev") == 2:
        body["engine_rev"] = 2
    r = _call(ctx.api.post("/run", body))
    return {"run_id": r.get("run_id"), "status": r.get("status"),
            "event_count": r.get("event_count"),
            "stream_hash": str(r.get("stream_hash", ""))[:16],
            "place": _name(ctx, a["place_id"]), "label": "MODELLED"}


def _check_apply_shock(ctx, a):
    if not any(k in a for k in ("capacity_loss", "duration_d", "shape")):
        raise ToolError("empty_overrides", "give at least one of capacity_loss, duration_d, shape")
    _check_run_exists(ctx, a["run_id"])


def _apply_shock(ctx, a):
    overrides = {k: a[k] for k in ("capacity_loss", "duration_d", "shape") if k in a}
    r = _call(ctx.api.post("/fork", {"run_id": a["run_id"], "at_seq": a["at_seq"],
                                     "overrides": overrides}))
    return {"run_id": r.get("run_id"), "parent_run_id": r.get("parent_run_id"),
            "at_seq": r.get("at_seq"), "status": r.get("status"),
            "event_count": r.get("event_count"), "overrides": overrides,
            "label": "MODELLED"}


def _start_sim_run(ctx, a):
    body = {"disruptions": [_disruption(a)], "tick": "day", "ticks": a.get("ticks", 14),
            "seed": a.get("seed", 1), "weather": False,
            "lane_geometry": True}   # vessels sail lane polylines, not chords across land
    r = _call(ctx.api.post("/sim/run", body))
    return {"sim_run_id": r.get("sim_run_id") or r.get("run_id") or r.get("id"),
            "status": r.get("status"), "label": "SIMULATED",
            "ticks": body["ticks"]}


MC_JOB_ID = {"type": "string", "pattern": r"mc_[0-9a-f]{16}", "maxLength": 19}
MC_MAX_N = 500                          # agent-side bound, tighter than the API's 1000
MC_LABEL = ("Input-uncertainty simulation (seeded). Modelled, conditional on the stated "
            "input ranges; not a forecast.")


def _mc_get(ctx, job_id: str) -> dict:
    res = ctx.api.get(f"/montecarlo/{job_id}")
    if res.status == 404:
        raise ToolError("unknown_job", f"simulation job {job_id!r} does not exist")
    return _call(res)


def _mc_band(b: dict | None) -> dict | None:
    if not isinstance(b, dict):
        return None
    return {k: b.get(k) for k in ("p10", "p50", "p90", "mean") if k in b}


def _monte_carlo(ctx, a):
    jid = a.get("job_id")
    if jid is None:
        rows = _call(ctx.api.get("/montecarlo")).get("jobs", [])[:10]
        return {"jobs": [{"job_id": r.get("job_id"), "status": r.get("status"),
                          "progress": r.get("progress"),
                          "place": _name(ctx, (r.get("request") or {}).get("disruption", {}).get("node_id", "")),
                          "engine": (r.get("engine") or {}).get("engine_label"),
                          "n": (r.get("request") or {}).get("n"),
                          "seed": (r.get("request") or {}).get("seed")} for r in rows],
                "label": MC_LABEL}
    other = a.get("compare_with")
    if other is not None:
        qs = {"a": jid, "b": other}
        if "threshold" in a:
            qs["threshold"] = a["threshold"]
        cmp = _call(ctx.api.get("/montecarlo/compare", qs))
        m = (cmp.get("metrics") or {}).get("worst_added_transit_d") or {}
        return {"mode": "compare", "a": jid, "b": other, "paired": cmp.get("paired"),
                "common_inputs": cmp.get("common_inputs"), "n": cmp.get("n_a"),
                "worst_added_transit_d": {
                    "unit": "d", "a": _mc_band(m.get("a")), "b": _mc_band(m.get("b")),
                    "delta_p50": m.get("delta_p50"), "delta_p90": m.get("delta_p90"),
                    "overlap": m.get("overlap"),
                    "b_higher_in": m.get("share_b_worse"), "shift_ci": m.get("shift_ci")},
                "largest_place_differences": [
                    {k: p.get(k) for k in ("place", "name", "delta_p50", "delta_p90")}
                    for p in (cmp.get("per_place") or [])[:5]],
                "label": MC_LABEL}
    job = _mc_get(ctx, jid)
    out = {"job_id": jid, "status": job.get("status"), "progress": job.get("progress"),
           "engine": (job.get("engine") or {}).get("engine_label"),
           "inputs": [{k: i.get(k) for k in ("name", "dist", "low", "mode", "high")}
                      for i in job.get("inputs") or []],
           "label": MC_LABEL}
    res = job.get("results")
    if job.get("status") == "done" and isinstance(res, dict):
        m = (res.get("metrics") or {}).get("worst_added_transit_d") or {}
        out.update({
            "n": res.get("n"), "seed": res.get("seed"),
            "runs_digest": str(res.get("runs_digest", ""))[:16],
            "worst_added_transit_d": {k: m.get(k) for k in ("unit", "mean", "p10", "p50", "p90", "min", "max")},
            "exceedance": m.get("exceedance"),
            "top_places": [{"place": p.get("place"), "name": p.get("name"),
                            "added_delay_d": p.get("added_delay_d"),
                            "affected_share": p.get("affected_share")}
                           for p in (res.get("per_place") or [])[:5]],
            "sensitivity": res.get("sensitivity")})
    elif job.get("error"):
        out["error"] = job["error"]
    return out


def _check_monte_carlo(ctx, a):
    if a.get("job_id") is None:
        if "compare_with" in a or "threshold" in a:
            raise ToolError("missing_job_id", "compare needs job_id and compare_with")
        return
    _mc_get(ctx, a["job_id"])
    if "threshold" in a and "compare_with" not in a:
        raise ToolError("unexpected_threshold", "threshold applies to compare_with only")
    if a.get("compare_with") is not None:
        _mc_get(ctx, a["compare_with"])


def _check_run_monte_carlo(ctx, a):
    _check_disruption(ctx, a)
    if a.get("profile") is not None and a.get("engine_rev") != 2:
        raise ToolError("profile_needs_engine_rev_2", "profile needs engine_rev 2")
    if a.get("pair_with") is not None:
        _mc_get(ctx, a["pair_with"])


def _run_monte_carlo(ctx, a):
    body = {"disruption": {**_disruption(a), "node_id": a["place_id"]},
            "engine_rev": a.get("engine_rev", 1), "n": a.get("n", 100)}
    if a.get("profile") is not None:
        body["profile"] = a["profile"]
    if a.get("pair_with") is not None:
        body["pair_with"] = a["pair_with"]
    elif "seed" in a:
        body["seed"] = a["seed"]
    r = _call(ctx.api.post("/montecarlo", body))
    return {"job_id": r.get("job_id"), "status": r.get("status"), "quote": r.get("quote"),
            "place": _name(ctx, a["place_id"]), "label": MC_LABEL,
            "note": "read the result with monte_carlo once the job is done"}


def _d_monte_carlo(a):
    return (f"Run {a.get('n', 100)} seeded simulations (input ranges around {a.get('kind')} at "
            f"{a.get('place_id')}); uses server CPU and can take minutes")


def _check_run_arg(ctx, a):
    _check_run_exists(ctx, a["run_id"])


def _build_report(ctx, a):
    r = _call(ctx.api.post("/reports/build", {
        "run_id": a["run_id"], "template": a.get("template", "full"),
        "format": a.get("format", "html")}))
    meta = r.get("meta") or {}
    return {"filename": r.get("filename"), "media_type": r.get("media_type"),
            "bytes": meta.get("bytes"), "sha256": str(meta.get("sha256", ""))[:16],
            "sections": meta.get("sections"), "label": "MODELLED",
            "retrieve": {"method": "POST", "path": "/reports/build",
                         "body": {"run_id": a["run_id"], "template": a.get("template", "full"),
                                  "format": a.get("format", "html")}}}


def _write_brief(ctx, a):
    body = {"run_id": a["run_id"]}
    for k in ("mc_job_id", "lane_plan", "research_episode_id"):
        if a.get(k):
            body[k] = a[k]
    res = ctx.api.post("/brief", body)
    if not res.ok:
        code, msg = res.error()
        raise ToolError(code, msg or f"brief answered HTTP {res.status}")
    d = res.body or {}
    v = d.get("verifier") or {}
    cost = d.get("cost") if isinstance(d.get("cost"), dict) else {}
    usd = cost.get("usd")
    live = d.get("transport") == "LIVE"
    return {"brief_id": d.get("brief_id"), "transport": d.get("transport"),
            "cached": bool(d.get("cached")),
            # what the writer call cost; a stored brief and a recorded one cost nothing.
            "cost_usd": float(usd) if live and isinstance(usd, (int, float)) and not isinstance(usd, bool) else 0.0,
            "verified": f"{v.get('passed')} of {v.get('checked')} statements",
            "flagged": len(v.get("flagged") or []),
            "sections": [s.get("id") for s in d.get("sections") or []],
            "exports": d.get("exports"), "label": "MODELLED"}


def _export_pack(ctx, a):
    body = {"run_id": a["run_id"], "include_xlsx": False, "include_png": False}
    if a.get("datasets"):
        body["datasets"] = a["datasets"]
    res = ctx.api.post("/exports/pack", body)
    if not res.ok:
        code, msg = res.error()
        raise ToolError(code, msg or f"export answered HTTP {res.status}")
    return {"built": True, "bytes": res.raw_len, "content_type": res.content_type,
            "retrieve": {"method": "POST", "path": "/exports/pack", "body": body},
            "label": "MODELLED"}


# ----------------------------------------------------------- client actions
# Windows the model may name. Must stay a subset of web/app/src/windows/registry.ts;
# the live-twin windows are flag-gated there and are left out on purpose.
CLIENT_WINDOWS = [
    "map", "news", "market", "satwatch", "health", "mapcontent", "chain", "chaintrace",
    "whatif", "ensemble", "sim", "impact", "desks", "proof", "replay", "ledger", "money", "explain",
    "alerts", "reports", "brief", "dataviewer", "laneplanner", "shipments", "watchlists", "exports", "satdesk", "porttwin",
    "ports", "charts", "aiactivity", "agent", "tool-history", "tool-realism",
    "tool-scenarios", "tool-exports", "research", "scenariobuilder",
]
# Built-in layout preset ids (web/app/src/windows/presets.ts).
CLIENT_PRESETS = [
    "overview", "chain-room", "place-desk", "markets", "replay", "history", "proof", "analysis",
    "scenario", "reports", "data", "lanes", "exposure", "watchlists", "satellite", "port-twin", "ports", "agent", "demo", "research",
    "builder",
]
CLIENT_ARRANGE = ["float", "dock", "tab", "maximize", "restore", "close"]
CLIENT_TIMEOUT_DEFAULT_S = 25.0


def _client_only(ctx, a):  # pragma: no cover - never called by the loop
    raise ToolError("client_only", "this tool runs in the user's browser")


def _check_tab_with(ctx, a):
    if a.get("how") == "tab" and "tab_with" not in a:
        raise ToolError("missing_tab_with", "how=tab needs tab_with (the window to tab with)")
    if a.get("action") == "tab" and "tab_with" not in a:
        raise ToolError("missing_tab_with", "action=tab needs tab_with (the window to tab with)")


def _check_focus_place(ctx, a):
    require_place(ctx, a["place_id"])


def _client_tools() -> list:
    win = {"type": "string", "enum": CLIENT_WINDOWS}
    return [
        Tool("open_window",
             "UI CONTROL (runs in the user's browser, no confirmation). Open a window by id, docked, floating or tabbed with another window.",
             _obj({"window": win, "how": {"type": "string", "enum": ["dock", "float", "tab"]},
                   "tab_with": win}, ["window"]),
             "client_action", _client_only, check=_check_tab_with,
             describe=lambda a: f"Open {a.get('window')} ({a.get('how', 'dock')})"),
        Tool("arrange_window",
             "UI CONTROL (runs in the user's browser, no confirmation). Float, dock, tab, maximize, restore or close an open window.",
             _obj({"window": win, "action": {"type": "string", "enum": CLIENT_ARRANGE},
                   "tab_with": win}, ["window", "action"]),
             "client_action", _client_only, check=_check_tab_with,
             describe=lambda a: f"{a.get('action')} {a.get('window')}"),
        Tool("apply_layout",
             "UI CONTROL (runs in the user's browser, no confirmation). Switch the workspace to a built-in layout preset.",
             _obj({"preset": {"type": "string", "enum": CLIENT_PRESETS}}, ["preset"]),
             "client_action", _client_only,
             describe=lambda a: f"Layout {a.get('preset')}"),
        Tool("set_mode",
             "UI CONTROL (runs in the user's browser, no confirmation). Switch the shell between live and simulation mode.",
             _obj({"mode": {"type": "string", "enum": ["live", "sim"]}}, ["mode"]),
             "client_action", _client_only,
             describe=lambda a: f"Mode {a.get('mode')}"),
        Tool("focus_place",
             "UI CONTROL (runs in the user's browser, no confirmation). Select a place and fly the map to it. Use a place id from search_places.",
             _obj({"place_id": PLACE_ID}, ["place_id"]),
             "client_action", _client_only, check=_check_focus_place,
             describe=lambda a: f"Focus {a.get('place_id')} on the map"),
        Tool("open_brief",
             "UI CONTROL (runs in the user's browser, no confirmation). Open the Disruption brief window on a written brief. Use the brief id returned by write_brief.",
             _obj({"brief_id": {"type": "string", "pattern": r"brf_[0-9a-f]{16}", "maxLength": 20}}, ["brief_id"]),
             "client_action", _client_only,
             describe=lambda a: "Open the disruption brief"),
        Tool("scenario_open",
             "UI CONTROL (runs in the user's browser, no confirmation). Open the Scenario builder and fill it with shocks on a timeline for the user to review. Nothing is run.",
             _obj({"name": {"type": "string", "maxLength": 60}, "start_at": SCENARIO_START_AT,
                   "shocks": {"type": "array", "minItems": 1, "maxItems": 12, "items": SCENARIO_SHOCK}},
                  ["shocks"]),
             "client_action", _client_only, check=_check_scenario_shocks,
             describe=lambda a: f"Open the scenario builder with {len(a.get('shocks') or [])} shock(s)"),
        Tool("scenario_organise",
             "UI CONTROL (runs in the user's browser, no confirmation). Arrange the shocks already on the Scenario builder timeline with ONE structured rule; the browser shows the change as a diff the user accepts or undoes. Rules: stagger removes overlaps, sequence sets an order with a gap, cluster brings starts within N days, align_start, spread_evenly, shift.",
             _obj({"rule": SCENARIO_RULE}, ["rule"]),
             "client_action", _client_only, check=_check_scenario_rule,
             describe=lambda a: f"Arrange the scenario timeline: {(a.get('rule') or {}).get('kind')}"),
        Tool("show_run",
             "UI CONTROL (runs in the user's browser, no confirmation). Open a stored run in the causal chain window. Use a run id from list_runs or a started run.",
             _obj({"run_id": RUN_ID}, ["run_id"]),
             "client_action", _client_only, check=_check_run_arg,
             describe=lambda a: f"Show run {a.get('run_id')}"),
    ]


# --------------------------------------------------------------- registry
_DIS_PROPS = {
    "place_id": PLACE_ID,
    "kind": {"type": "string", "enum": KINDS},
    "capacity_loss": {"type": "number", "exclusiveMinimum": 0, "maximum": 1},
    "duration_d": {"type": "number", "exclusiveMinimum": 0, "maximum": MAX_DURATION_D},
    "shape": {"type": "string", "enum": SHAPES},
}


def _d_start(a):
    return (f"Start a run: {a.get('kind')} at {a.get('place_id')}"
            f" for {a.get('duration_d', 14)} d")


def _build() -> dict[str, Tool]:
    T: list[Tool] = [
        Tool("search_places",
             "Find places (ports, air hubs, ground hubs, chokepoints) by name, alias, id or country. Returns ids to use in other tools.",
             _obj({"query": {"type": "string", "minLength": 1, "maxLength": 80},
                   "mode": {"type": "string", "enum": ["sea", "air", "ground"]},
                   "kind": {"type": "string", "enum": ["port", "air_hub", "ground_node", "chokepoint"]},
                   "limit": {"type": "integer", "minimum": 1, "maximum": 20}}, ["query"]),
             "read", _search_places),
        Tool("get_place", "Read one place: kind, tier, coordinates, capacity, its legs and neighbours.",
             _obj({"place_id": PLACE_ID}, ["place_id"]), "read", _get_place,
             check=lambda c, a: require_place(c, a["place_id"])),
        Tool("list_scenarios", "List the curated disruption scenarios (place, kind, default duration).",
             _obj({}), "read", _list_scenarios),
        Tool("list_runs", "List stored runs, newest first.",
             _obj({"limit": {"type": "integer", "minimum": 1, "maximum": 20},
                   "kind": {"type": "string", "enum": ["chain", "sim", "ensemble"]}}),
             "read", _list_runs),
        Tool("run_summary", "Summarise a causal-chain run: the shock, channels, places reached, largest delays.",
             _obj({"run_id": RUN_ID}, ["run_id"]), "read", _run_summary, check=_check_run_arg),
        Tool("compare_runs", "Compare two runs: where their chains diverge and the largest per-place delay differences.",
             _obj({"a": RUN_ID, "b": RUN_ID}, ["a", "b"]), "read", _compare_runs,
             check=lambda c, a: (_check_run_exists(c, a["a"]), _check_run_exists(c, a["b"]))),
        Tool("verify_run", "Replay a run and check its stored stream hash matches byte for byte.",
             _obj({"run_id": RUN_ID}, ["run_id"]), "read", _verify_run, check=_check_run_arg),
        Tool("check_shock", "Dry-run: would this shock be accepted by the model? Creates nothing.",
             _obj(_DIS_PROPS, ["place_id", "kind"]), "read", _check_shock, check=_check_disruption),
        Tool("lane_plan",
             "Plan a sea lane between two sea places as ranges (low, median, high) from named variants, optionally under a disruption.",
             _obj({"origin": PLACE_ID, "destination": PLACE_ID,
                   "disruption": _obj({k: _DIS_PROPS[k] for k in
                                       ("place_id", "kind", "capacity_loss", "duration_d")},
                                      ["place_id", "kind", "duration_d"])},
                  ["origin", "destination"]),
             "read", _lane_plan,
             check=lambda c, a: (require_place(c, a["origin"], "origin", need_core=True, need_sea=True),
                                 require_place(c, a["destination"], "destination", need_core=True, need_sea=True),
                                 a.get("disruption") and require_place(c, a["disruption"]["place_id"], "disruption.place_id", need_core=True))),
        Tool("shipment_exposure",
             "Which shipments a library scenario reaches, as low, median and high added days, and the value in affected shipments. Uses the synthetic sample portfolio or a short list given here. Read-only; nothing is stored.",
             _obj({"scenario_id": {"type": "string", "pattern": r"[a-z0-9\-]{1,64}", "maxLength": 64},
                   "duration_d": {"type": "number", "exclusiveMinimum": 0, "maximum": MAX_DURATION_D},
                   "source": {"type": "string", "enum": ["sample", "given"]},
                   "shipments": {"type": "array", "maxItems": 25, "items": _obj({
                       "id": {"type": "string", "minLength": 1, "maxLength": 64},
                       "origin": {"type": "string", "minLength": 1, "maxLength": 120},
                       "destination": {"type": "string", "minLength": 1, "maxLength": 120},
                       "mode": {"type": "string", "enum": ["sea", "air", "ground"]},
                       "value_usd": {"type": "number", "minimum": 0, "maximum": 1e12}},
                       ["id", "origin", "destination"])}},
                  ["scenario_id"]),
             "read", _shipment_exposure, check=_check_shipment_exposure),
        Tool("port_scenario",
             "Simulate one port under a scenario (surge, strike, channel closure) and read waiting time and queue against its own baseline. Simulated, not observed.",
             _obj({"place_id": PLACE_ID,
                   "scenario_type": {"type": "string", "enum": ["surge", "strike", "channel_closure"]},
                   "factor": {"type": "number", "exclusiveMinimum": 0, "maximum": 5},
                   "start_h": {"type": "number", "minimum": 0, "maximum": 168},
                   "duration_h": {"type": "number", "minimum": 1, "maximum": 168},
                   "days": {"type": "integer", "minimum": 2, "maximum": 14}},
                  ["place_id", "scenario_type"]),
             "read", _port_scenario, check=_check_port_scenario),
        Tool("list_reports", "List report templates and export datasets that can be built.",
             _obj({}), "read", _list_reports),
        Tool("explain_chain",
             "Write a short narrative of a run's causal chain with Meta AI. Free from cache or on the recorded transport; costs money live, so it asks the user first.",
             _obj({"run_id": RUN_ID}, ["run_id"]), "read", _explain_chain,
             check=_check_run_arg, confirm="if_live", est_cost_usd=0.004,
             describe=lambda a: f"Explain run {a.get('run_id')} with Meta AI (live model call)"),
        Tool("satellite_sam",
             "Read the recorded SAM 3.1 result on a stored satellite crop (place_id) or on a sub-metre aerial port scene (aerial_scene, with ship lengths in metres): what SAM found. Recorded replies only (free); says SAM found N, never that N exist.",
             _obj({"place_id": PLACE_ID, "source": {"type": "string", "enum": ["sentinel-2", "naip"]},
                   "role": {"type": "string", "enum": ["now", "baseline"]},
                   "prompt": {"type": "string", "enum": ["container ship", "cargo vessel", "building"]},
                   "aerial_scene": {"type": "string", "enum": AERIAL_SCENES},
                   "phrases": {"type": "array", "items": {"type": "string", "enum": AERIAL_PHRASES}, "maxItems": 3}}),
             "read", _satellite_sam,
             check=_check_satellite_sam),
        Tool("satellite_vision",
             "Read the recorded Meta AI vision read of a stored satellite crop: visible features, congestion cues, and what it cannot see. Recorded replies only (free); a model reading, not a count.",
             _obj({"place_id": PLACE_ID, "source": {"type": "string", "enum": ["sentinel-2", "naip", "landsat"]},
                   "role": {"type": "string", "enum": ["now", "baseline"]}}, ["place_id"]),
             "read", _satellite_vision,
             check=lambda c, a: require_place(c, a["place_id"], need_sea=True)),
        Tool("scenario_rules",
             "Read the scenario builder rules: shock bounds, the structured arrangement rules, and what each engine does exactly or approximately with many shocks.",
             _obj({}), "read", _scenario_rules),
        Tool("scenario_validate",
             "Dry-run a scenario of one or many shocks on a timeline: a verdict per shock and engine, overlaps and total span. Creates nothing.",
             _obj({"name": {"type": "string", "maxLength": 60}, "start_at": SCENARIO_START_AT,
                   "shocks": {"type": "array", "minItems": 1, "maxItems": 12, "items": SCENARIO_SHOCK},
                   "engine_rev": {"type": "integer", "enum": [1, 2]}}, ["shocks"]),
             "read", _scenario_validate, check=_check_scenario_shocks),
        Tool("monte_carlo",
             "Read a seeded input-uncertainty simulation (Monte Carlo) job: percentile bands and 'k of N simulations'. Give job_id to read one, job_id plus compare_with to compare two, nothing to list recent jobs. Modelled, conditional on the stated input ranges; not a forecast.",
             _obj({"job_id": MC_JOB_ID, "compare_with": MC_JOB_ID,
                   "threshold": {"type": "number", "minimum": 0, "maximum": 3650}}),
             "read", _monte_carlo, check=_check_monte_carlo),
        # ---- actions
        Tool("scenario_run",
             "ACTION (asks the user first). Run a scenario of many shocks: target chain gives one exact causal-chain run per shock plus an approximated combined table; target sim gives one simulation with every shock as a window.",
             _obj({"scenario_id": {"type": "string", "pattern": r"scn_[0-9a-f]{16}", "maxLength": 20},
                   "name": {"type": "string", "maxLength": 60}, "start_at": SCENARIO_START_AT,
                   "shocks": {"type": "array", "minItems": 1, "maxItems": 12, "items": SCENARIO_SHOCK},
                   "target": {"type": "string", "enum": ["chain", "sim"]},
                   "engine_rev": {"type": "integer", "enum": [1, 2]}}, ["target"]),
             "action", _scenario_run, check=_check_scenario_run, confirm="always", describe=_d_scenario_run),
        Tool("run_monte_carlo",
             "ACTION (asks the user first; uses server CPU). Start a seeded input-uncertainty simulation: N engine runs with duration, capacity loss and queue sensitivity drawn from stated ranges. Same seed gives the same result. Pass pair_with (a job id) to reuse its seed for a paired comparison.",
             _obj({**_DIS_PROPS, "engine_rev": {"type": "integer", "enum": [1, 2]},
                   "profile": {"type": "string", "enum": ["c", "d", "e"]},
                   "n": {"type": "integer", "minimum": 20, "maximum": MC_MAX_N},
                   "seed": {"type": "integer", "minimum": 0, "maximum": 1000000},
                   "pair_with": MC_JOB_ID},
                  ["place_id", "kind", "duration_d"]),
             "action", _run_monte_carlo, check=_check_run_monte_carlo, confirm="always",
             describe=_d_monte_carlo),
        Tool("start_run",
             "ACTION (asks the user first). Start a deterministic causal-chain run from one disruption at one place.",
             _obj({**_DIS_PROPS, "engine_rev": {"type": "integer", "enum": [1, 2]}},
                  ["place_id", "kind", "duration_d"]),
             "action", _start_run, check=_check_disruption, confirm="always", describe=_d_start),
        Tool("apply_shock",
             "ACTION (asks the user first). Fork an existing run at a step with a changed capacity loss, duration or shape.",
             _obj({"run_id": RUN_ID, "at_seq": {"type": "integer", "minimum": 0, "maximum": 100000},
                   "capacity_loss": _DIS_PROPS["capacity_loss"],
                   "duration_d": _DIS_PROPS["duration_d"], "shape": _DIS_PROPS["shape"]},
                  ["run_id", "at_seq"]),
             "action", _apply_shock, check=_check_apply_shock, confirm="always",
             describe=lambda a: f"Fork run {a.get('run_id')} at step {a.get('at_seq')} with a changed shock"),
        Tool("start_sim_run",
             "ACTION (asks the user first). Start a vessel and aircraft simulation run with one disruption (no weather feed).",
             _obj({**_DIS_PROPS, "ticks": {"type": "integer", "minimum": 1, "maximum": 30},
                   "seed": {"type": "integer", "minimum": 0, "maximum": 1000000}},
                  ["place_id", "kind", "duration_d"]),
             "action", _start_sim_run, check=_check_disruption, confirm="always",
             describe=lambda a: f"Start a simulation run: {a.get('kind')} at {a.get('place_id')}"),
        Tool("build_report", "ACTION (asks the user first). Build a report for a run.",
             _obj({"run_id": RUN_ID, "template": {"type": "string", "pattern": r"[a-z0-9_\-]{1,40}", "maxLength": 40},
                   "format": {"type": "string", "enum": ["html", "md", "json"]}}, ["run_id"]),
             "action", _build_report, check=_check_run_arg, confirm="always",
             describe=lambda a: f"Build a report for run {a.get('run_id')}"),
        Tool("write_brief",
             "ACTION (asks the user first; spends one Meta AI call when live). Write a one-page disruption brief for a run. Every figure comes from the engine and the text is machine-checked.",
             _obj({"run_id": RUN_ID,
                   "mc_job_id": {"type": "string", "pattern": r"mc_[0-9a-f]{16}", "maxLength": 19},
                   "lane_plan": _obj({"origin": PLACE_ID, "destination": PLACE_ID}, ["origin", "destination"]),
                   "research_episode_id": RUN_ID}, ["run_id"]),
             "action", _write_brief, check=_check_run_arg, confirm="always", est_cost_usd=0.01,
             describe=lambda a: f"Write a disruption brief for run {a.get('run_id')}"),
        Tool("export_pack", "ACTION (asks the user first). Build an export pack (data files) for a run.",
             _obj({"run_id": RUN_ID,
                   "datasets": {"type": "array", "maxItems": 10,
                                "items": {"type": "string", "pattern": r"[a-z0-9_\-]{1,40}", "maxLength": 40}}},
                  ["run_id"]),
             "action", _export_pack, check=_check_run_arg, confirm="always",
             describe=lambda a: f"Export a data pack for run {a.get('run_id')}"),
    ]
    T += _client_tools()
    return {t.name: t for t in T}


def _check_shipment_exposure(ctx, a):
    if a.get("source") == "given" and not a.get("shipments"):
        raise ToolError("missing_shipments", "source=given needs a shipments list (at most 25)")
    if a.get("source", "sample") == "sample" and a.get("shipments"):
        raise ToolError("unexpected_shipments", "shipments are used only with source=given")


def _check_port_scenario(ctx, a):
    require_place(ctx, a["place_id"], need_sea=True)
    typ = a["scenario_type"]
    f = a.get("factor")
    if typ in ("surge", "strike") and f is None:
        raise ToolError("missing_factor", f"{typ} needs factor")
    if typ == "surge" and not (0.1 <= f <= 5.0):
        raise ToolError("factor_out_of_range", "surge factor must be 0.1 to 5")
    if typ == "strike" and not (0.0 < f < 1.0):
        raise ToolError("factor_out_of_range", "strike factor must be above 0 and below 1")
    if typ == "channel_closure" and f is not None:
        raise ToolError("unexpected_factor", "channel_closure takes no factor")
    if a.get("start_h", 0) + a.get("duration_h", 48) > a.get("days", 7) * 24:
        raise ToolError("window_too_long", "the scenario window must fit inside days")


REGISTRY: dict[str, Tool] = _build()


def register_tools(tools: list[Tool]) -> None:
    """Add tools from a sibling module (research tools); idempotent."""
    for t in tools:
        REGISTRY.setdefault(t.name, t)


def tool_specs(names: list[str] | None = None) -> list[dict]:
    return [REGISTRY[n].spec() for n in (names or REGISTRY)]


def parse_arguments(raw) -> dict:
    """Model arguments arrive as a JSON string; anything but an object is refused."""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or len(raw) > 8000:
        raise ToolError("invalid_arguments", "arguments must be a JSON object string (max 8000 chars)")
    try:
        val = json.loads(raw) if raw.strip() else {}
    except ValueError:
        raise ToolError("invalid_arguments", "arguments are not valid JSON") from None
    if not isinstance(val, dict):
        raise ToolError("invalid_arguments", "arguments must be a JSON object")
    return val


def prepare(ctx: ToolContext, name: str, raw_args) -> tuple[Tool, dict]:
    """Resolve + validate. Raises ToolError. Used at call time AND confirm time."""
    tool = REGISTRY.get(name if isinstance(name, str) else "")
    if tool is None:
        raise ToolError("unknown_tool", f"no tool named {str(name)[:40]!r}; available: {sorted(REGISTRY)}")
    args = parse_arguments(raw_args)
    errs = validate(args, tool.parameters)
    if errs:
        raise ToolError("invalid_arguments", "; ".join(errs[:5]))
    if tool.check is not None:
        tool.check(ctx, args)
    return tool, args


def summarize_result(name: str, result: dict) -> str:
    """One-line result summary for the activity log (no raw payloads)."""
    if "error" in result:
        e = result["error"]
        return f"error {e.get('code')}: {str(e.get('message', ''))[:120]}"
    if name.startswith("research_") or name == "write_research_note":
        from .research_tools import summarize as _research_summary
        line = _research_summary(name, result)
        if line:
            return line
    try:
        if name == "search_places":
            return f"{len(result['places'])} of {result['matches']} places: " + ", ".join(
                (f"{p['name']} ({p['id']})" if p.get("name") else p["id"]) for p in result["places"][:5])
        if name == "get_place":
            return f"{result['name']} ({result['kind']}, {result['tier']}), {result['legs_out']} legs out"
        if name == "list_scenarios":
            return f"{len(result['scenarios'])} scenarios"
        if name == "list_runs":
            return f"{len(result['runs'])} of {result['total']} runs"
        if name == "run_summary":
            top = result["top_places"][0] if result["top_places"] else None
            return (f"{result['run_id']}: {result['event_count']} events, "
                    f"{result['places_reached']} places"
                    + (f", largest delay {top['max_delay_d']} d at {top.get('name') or top['place_id']}" if top else ""))
        if name == "compare_runs":
            return (f"diverge at seq {result['diverges_from_seq']}, "
                    f"{result['places_compared']} places compared")
        if name == "verify_run":
            return f"hash match {result.get('stored_hash_matches')}, replay match {result.get('replay_matches')}"
        if name == "check_shock":
            return "accepted" if result.get("accepted") else f"refused: {result.get('refused')}"
        if name == "lane_plan":
            r = result["routes"][0] if result["routes"] else {}
            tot = (r.get("total_d") or {})
            return f"{len(result['routes'])} routes; first total {tot.get('low')}-{tot.get('high')} d (low-high)"
        if name == "shipment_exposure":
            return f"{result['affected']} of {result['shipments']} shipments affected ({result['scenario']})"
        if name == "port_scenario":
            return f"{result.get('place', '')}: " + (
                f"wait mean {result['wait_h'].get('mean')} h" if "wait_h" in result else result.get("status", "done"))
        if name == "list_reports":
            return f"{len(result['report_templates'])} templates, {len(result['datasets'])} datasets"
        if name == "satellite_sam":
            if result.get("aerial_scene"):
                return f"{result['summary']} on {result['name']} ({result['source_badge']}, {result['mode']}, MODELLED)"
            return f"{result['summary']} ({result['prompt']}, {result['mode']}, MODELLED)"
        if name == "satellite_vision":
            return f"vision read {result['mode']}: quality {result['image_quality']}, congestion {result['congestion_level']} (MODELLED)"
        if name == "explain_chain":
            return f"narrative for {result['run_id']} (post-check {result['post_check']})"
        if name in ("start_run", "apply_shock"):
            return f"{result['run_id']}: {result['event_count']} events"
        if name == "monte_carlo":
            if "jobs" in result:
                return f"{len(result['jobs'])} simulation jobs"
            if result.get("mode") == "compare":
                return f"compared {result['a']} with {result['b']} (paired {result.get('paired')})"
            w = result.get("worst_added_transit_d") or {}
            return (f"{result['job_id']} {result['status']}"
                    + (f": worst added transit p10 {w.get('p10')} / p50 {w.get('p50')} / p90 {w.get('p90')} d"
                       if w else ""))
        if name == "run_monte_carlo":
            return f"simulation job {result['job_id']} {result['status']}"
        if name == "start_sim_run":
            return f"sim run {result['sim_run_id']} {result['status']}"
        if name == "scenario_rules":
            return f"up to {result['max_shocks']} shocks; rules {', '.join(result['organise_rules'])}"
        if name == "scenario_validate":
            ok = [r["id"] for r in result["shocks"]]
            return (f"{len(ok)} shocks, runnable {result['runnable']}, "
                    f"{len(result['overlaps'] or [])} overlaps, span {(result['span'] or {}).get('total_span_d')} d")
        if name == "scenario_run":
            return (f"{result['shock_count']} shocks on {result['target']}: "
                    + (f"{len(result['runs'])} runs" if result["target"] == "chain"
                       else f"sim run {result['sim_run_id']} {result['status']}"))
        if name == "build_report":
            return f"{result['filename']} ({result['bytes']} bytes)"
        if name == "write_brief":
            return f"brief {result['brief_id']} ({result['transport']}), verified {result['verified']}"
        if name == "export_pack":
            return f"pack built ({result['bytes']} bytes)"
        if name in REGISTRY and REGISTRY[name].kind == "client_action":
            return str(result.get("summary") or result.get("status") or "done")[:160]
    except (KeyError, TypeError):
        pass
    return json.dumps(result, default=str)[:160]


# The research tools live in their own module and register themselves on import
# (kept at the end so the names research_tools.py imports from here exist).
from . import research_tools as _research_tools  # noqa: E402,F401
