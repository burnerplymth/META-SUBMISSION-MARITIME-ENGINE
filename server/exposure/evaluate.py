"""Shipment exposure: which shipments a disruption reaches, by how many days (a range), and the value in them.

A pure function of (graph, request): it groups the shipments by unique (origin place, destination place,
mode), asks the lane planner once per unique sea lane, and reads each shipment's range from the planner's
named variants. It writes nothing and keeps nothing: no file, no store, no log line with shipment text.

How a lane is read:

* ``baseline_days``  the baseline route's sail days (lane geometry when served, else the chord basis).
* ``added_days``     the wait at arrival at the places of the route: low, median and high over the planner's
                     named speed and queue variants (not the run peak). Zero on a route that avoids the
                     disrupted place.
* ``status``         ``unaffected`` (every bound is zero), ``delayed`` (the shipment stays on its route),
                     ``rerouted`` (the planner offers a route whose median days are lower than staying; the
                     added days are that route's days over the baseline), ``not_served`` (air and ground
                     legs, and sea lanes the planner refuses, with a reason chip).
* a shipment that names a ``via`` place the baseline route misses is read on a route that passes that place
  when the planner offers one; it is never rerouted away from it.

Value in affected shipments is the sum of ``value_usd`` of the shipments whose status is delayed or
rerouted. It is not a loss estimate.
"""
from __future__ import annotations

import re
import statistics
import threading

try:
    from laneplan.planner import LaneRefusal, plan
except Exception:  # pragma: no cover - imported as server.* from the repo root
    from server.laneplan.planner import LaneRefusal, plan  # type: ignore

from . import model
from .resolve import PlaceIndex

LABEL = "Modelled from named variants. Not a forecast."
SCHEMA = "exposure.evaluate/1"
REROUTE_CORRIDORS = ("suez", "panama", "cape")   # named sea corridors only: a leg that skips a place on paper is not a reroute
EPS = 0.05                      # days: below this a bound is treated as zero
VALUE_LABEL = "Value in affected shipments"

ASSUMPTIONS = [
    "The disruption starts when each shipment departs.",
    "Added days are the wait at arrival over named speed and queue variants.",
    "Median is the base variant of the plan, not a statistic.",
    "A delayed shipment stays on its route; a reroute is shown when the alternative is shorter.",
    "Departure and arrival dates are not used.",
    "Air and ground legs are not served.",
    "Value in affected shipments is the listed value, not a loss estimate.",
]

# planner refusal code -> reason chip
REFUSAL_CHIPS = {
    "same_place": "Same place",
    "not_a_sea_place": "Not a sea place",
    "no_sea_path": "No sea path",
    "unknown_place": "Unknown place",
    "shell_endpoint": "Place not modelled",
    "shell_no_core_leg": "Place not modelled",
    "shell_anchor_is_endpoint": "Same access place",
    "shell_same_anchor": "Same access place",
}
SCENARIO_FATAL = {"unknown_scenario", "invalid_disruption", "disruption_place_not_modelled", "invalid_request"}

_PLAN_MEMO: dict = {}
_PLAN_MEMO_MAX = 256
_MEMO_LOCK = threading.Lock()
_INDEX: dict = {}


def place_index(graph) -> PlaceIndex:
    key = id(graph)
    idx = _INDEX.get(key)
    if idx is None or idx[0] is not graph:
        idx = (graph, PlaceIndex(graph))
        _INDEX.clear()
        _INDEX[key] = idx
    return idx[1]


def _scenarios() -> list:
    try:
        from api.chain_loop import _SCENARIOS
    except Exception:  # pragma: no cover
        try:
            from server.api.chain_loop import _SCENARIOS  # type: ignore
        except Exception:
            return []
    return list(_SCENARIOS)


def _r(x, nd=2):
    return None if x is None else round(float(x) + 0.0, nd)


def _plain(label: str) -> str:
    """A route label without the planner's disambiguating counter, e.g. 'Via Suez Canal (2)'."""
    return re.sub(r" \(\d+\)$", "", str(label))


def _rng(low, median, high) -> dict:
    a = sorted((float(low), float(median), float(high)))
    return {"low": _r(a[0]), "median": _r(a[1]), "high": _r(a[2])}


def _block(b) -> dict | None:
    if not isinstance(b, dict) or b.get("median") is None:
        return None
    return _rng(b["low"], b["median"], b["high"])


# --------------------------------------------------------------- the planner read
def _reduce_plan(doc: dict) -> dict:
    """The few numbers the exposure reads from a lane plan (kept in the memo instead of the whole plan)."""
    routes = []
    for r in doc.get("routes", []):
        lane = r.get("lane") if isinstance(r.get("lane"), dict) else None
        sail = _block((lane or {}).get("sail")) or _block(r.get("sail"))
        wait = None
        if lane is not None:
            wait = _block(lane.get("queue_at_arrival"))
        if wait is None:
            wait = _block(r.get("queue_at_arrival"))
        legs = [[lg.get("from"), lg.get("to")] for lg in (r.get("legs") or []) if lg.get("from") and lg.get("to")]
        routes.append({
            "id": r.get("id"), "label": r.get("label"), "corridor": r.get("corridor"),
            "is_baseline": bool(r.get("is_baseline")), "sail": sail, "wait": wait,
            "legs": legs,
            "places": [{"id": p["id"], "name": p["name"], "role": p.get("role"), "kind": p.get("kind")}
                       for p in r.get("places", [])],
            "waits": [{"id": q["place_id"], "name": q["name"], "wait_d": q["wait_d"]}
                      for q in (r.get("queue_by_place") or [])],
            "lane_geometry": lane is not None,
            "disrupted_place_on_route": bool(r.get("disrupted_place_on_route")),
        })
    return {"plan_id": doc.get("plan_id"), "routes": routes,
            "shell": bool(doc.get("shell_access")),
            "states": doc.get("states") or {}}


def lane_plan(graph, origin: str, destination: str, dis: dict | None, scenarios: list) -> dict:
    """Reduced plan for one lane (memoised: the key holds place ids only, never shipment data)."""
    key = (id(graph), origin, destination, None if dis is None else tuple(sorted(dis.items())))
    with _MEMO_LOCK:
        hit = _PLAN_MEMO.get(key)
    if hit is not None:
        return hit
    body = {"origin": origin, "destination": destination, "shell_access": True}
    if dis is not None:
        body["disruption"] = dis
    red = _reduce_plan(plan(graph, body, scenarios))
    with _MEMO_LOCK:
        if len(_PLAN_MEMO) >= _PLAN_MEMO_MAX:
            _PLAN_MEMO.clear()
        _PLAN_MEMO[key] = red
    return red


# --------------------------------------------------------------- reading a lane
def _own_days(route: dict, with_wait: bool) -> tuple[dict | None, dict, dict | None]:
    """(baseline sail range, added range, disrupted range) for staying on ``route``."""
    sail = route["sail"]
    if sail is None:
        return None, _rng(0, 0, 0), None
    wait = route["wait"] if with_wait and route["wait"] is not None else _rng(0, 0, 0)
    dis = _rng(sail["low"] + wait["low"], sail["median"] + wait["median"], sail["high"] + wait["high"])
    return sail, wait, dis


def read_lane(red: dict, has_disruption: bool, via_place: str | None = None) -> dict:
    """Status, baseline, disrupted and added ranges, the route used, for one planned lane (and one via)."""
    routes = [r for r in red["routes"] if r["sail"] is not None]
    if not routes:
        return {"status": "not_served", "reason": "No route"}
    base = next((r for r in routes if r["is_baseline"]), routes[0])
    chosen, rerouted, via_miss = base, False, False
    if via_place:
        on = [r for r in routes if any(p["id"] == via_place for p in r["places"])]
        if on:
            chosen = base if base in on else min(on, key=lambda r: (r["sail"]["median"], r["id"]))
        else:
            via_miss = True
    b_sail, b_wait, b_dis = _own_days(base, has_disruption)
    if via_place is None and has_disruption and b_wait["high"] > EPS:
        alts = [r for r in routes if r is not base and r["wait"] is not None and r["corridor"] in REROUTE_CORRIDORS
                and not r["disrupted_place_on_route"]]
        best = None
        for r in alts:
            _s, _w, d = _own_days(r, True)
            if d["median"] < b_dis["median"] - EPS and (best is None or d["median"] < best[1]["median"]):
                best = (r, d)
        if best is not None:
            chosen, rerouted = best[0], True
    c_sail, c_wait, c_dis = _own_days(chosen, has_disruption)
    if rerouted:
        added = _rng(max(0.0, c_dis["low"] - b_sail["low"]), max(0.0, c_dis["median"] - b_sail["median"]),
                     max(0.0, c_dis["high"] - b_sail["high"]))
        disrupted = c_dis
    else:
        added = c_wait
        disrupted = c_dis
    ref = c_sail if via_place else b_sail
    if rerouted:
        status = "rerouted"
    elif added["high"] > EPS:
        status = "delayed"
    else:
        status = "unaffected"
    if status == "unaffected":
        added = _rng(0, 0, 0)
        disrupted = ref
    return {"status": status, "baseline": ref, "disrupted": disrupted, "added": added, "route": chosen,
            "base_route": base, "via_miss": via_miss}


# --------------------------------------------------------------- the request
class ScenarioError(Exception):
    pass


def resolve_scenario(graph, idx: PlaceIndex, sc: dict, scenarios: list) -> tuple[dict | None, dict]:
    """(planner disruption or None, echo for the response). Raises model.Invalid."""
    if sc["mode"] == "none":
        return None, {"mode": "none", "label": "No disruption"}
    if sc["mode"] == "library":
        lib = {s["id"]: s for s in scenarios}
        s = lib.get(sc["scenario_id"])
        if s is None or s["place_id"] not in graph.nodes:
            raise model.Invalid("unknown_scenario", "the scenario is not in the library")
        dis = {"scenario_id": s["id"]}
        for k in ("duration_d", "capacity_loss"):
            if k in sc:
                dis[k] = sc[k]
        dur = sc.get("duration_d", float(s["default_duration_d"]))
        loss = 1.0 if s["kind"] == "closure" else sc.get("capacity_loss", float(s.get("default_capacity_loss", 0.5)))
        echo = {"mode": "library", "scenario_id": s["id"], "label": s["label"], "kind": s["kind"],
                "place": {"id": s["place_id"], "name": graph.nodes[s["place_id"]].get("name")},
                "duration_d": _r(dur), "capacity_loss": _r(loss, 3)}
        return dis, echo
    got = idx.resolve(sc["place"], "via")
    if got["status"] != "resolved":
        raise model.Invalid("unknown_place", "the disruption place is not a known place",
                            suggestions=[{"id": s["id"], "name": s["name"]} for s in got.get("suggestions", [])])
    pid = got["id"]
    dis = {"place_id": pid, "kind": sc["kind"], "duration_d": sc["duration_d"]}
    if "capacity_loss" in sc and sc["kind"] != "closure":
        dis["capacity_loss"] = sc["capacity_loss"]
    name = graph.nodes[pid].get("name")
    loss = 1.0 if sc["kind"] == "closure" else sc.get("capacity_loss", 0.5)
    echo = {"mode": "explicit", "scenario_id": None, "label": f"{sc['kind'].replace('_', ' ').capitalize()} at {name}",
            "kind": sc["kind"], "place": {"id": pid, "name": name}, "duration_d": _r(sc["duration_d"]),
            "capacity_loss": _r(loss, 3)}
    return dis, echo


def _lane_key(o: str, d: str, mode: str) -> str:
    return f"{o}>{d}:{mode}"


def evaluate(graph, req: dict, mc: dict | None = None, scenarios: list | None = None) -> dict:
    """``req`` is the validated request (model.parse_body). Returns the response document."""
    scenarios = _scenarios() if scenarios is None else scenarios
    idx = place_index(graph)
    dis, scen_echo = resolve_scenario(graph, idx, req["scenario"], scenarios)
    has_dis = dis is not None
    ships = req["shipments"]

    resolved, unresolved = [], []
    placed: list[dict] = []          # shipments with both ends resolved
    for s in ships:
        ends, bad = {}, False
        for fld in ("origin", "destination"):
            got = idx.resolve(s[fld], s["mode"])
            if got["status"] == "resolved":
                ends[fld] = got
            else:
                bad = True
                unresolved.append({"id": s["id"], "field": fld, "text": s[fld],
                                   "reason": got.get("reason", "no_match"), "suggestions": got.get("suggestions", [])})
        via_id = None
        if s.get("via"):
            got = idx.resolve(s["via"], "via")
            if got["status"] == "resolved":
                via_id = got["id"]
            else:
                bad = True
                unresolved.append({"id": s["id"], "field": "via", "text": s["via"],
                                   "reason": got.get("reason", "no_match"), "suggestions": got.get("suggestions", [])})
        if bad:
            continue
        o, d = ends["origin"], ends["destination"]
        resolved.append({"id": s["id"], "origin_place": idx.row(o["id"]), "destination_place": idx.row(d["id"]),
                         "match_quality": {"origin": o["quality"], "destination": d["quality"]}})
        placed.append({**s, "o": o["id"], "d": d["id"], "via_id": via_id})

    lanes: dict[str, dict] = {}
    for s in placed:
        k = _lane_key(s["o"], s["d"], s["mode"])
        lanes.setdefault(k, {"key": k, "o": s["o"], "d": s["d"], "mode": s["mode"], "ships": []})["ships"].append(s)
    if len(lanes) > model.MAX_LANES:
        raise model.Invalid("too_many_lanes", f"at most {model.MAX_LANES} unique lanes per request", status=413)

    lane_rows, ship_by_id = [], {}
    for k, ln in lanes.items():
        o, d, mode = ln["o"], ln["d"], ln["mode"]
        row = {"lane_key": k, "origin": o, "destination": d, "mode": mode,
               "origin_name": graph.nodes[o].get("name"), "destination_name": graph.nodes[d].get("name"),
               "n_shipments": len(ln["ships"]), "baseline_days": None, "disrupted_days": None, "added_days": None,
               "status": "not_served", "via": [], "notes_chips": [], "route_legs": [], "route_label": None, "_route_id": None}
        red = None
        if mode != "sea":
            row["notes_chips"].append("Air legs not served" if mode == "air" else "Ground legs not served")
        elif o == d:
            row["notes_chips"].append(REFUSAL_CHIPS["same_place"])
        else:
            try:
                red = lane_plan(graph, o, d, dis, scenarios)
            except LaneRefusal as exc:
                if exc.code in SCENARIO_FATAL:
                    raise model.Invalid(exc.code if exc.code != "invalid_request" else "invalid_scenario",
                                        "the scenario is not accepted by the lane planner") from None
                row["notes_chips"].append(REFUSAL_CHIPS.get(exc.code, "Lane not served"))
            except Exception:
                row["notes_chips"].append("Lane not served")
        if red is not None:
            base = read_lane(red, has_dis)
            if base["status"] == "not_served":
                row["notes_chips"].append(base.get("reason", "Lane not served"))
            else:
                rt = base["route"]
                row.update({"status": base["status"], "baseline_days": base["baseline"],
                            "disrupted_days": base["disrupted"], "added_days": base["added"],
                            "via": [{"id": p["id"], "name": p["name"]} for p in rt["places"] if p.get("role") == "gate"],
                            "route_legs": rt["legs"], "route_label": _plain(rt["label"]), "_route_id": rt["id"]})
                row["notes_chips"].append(_plain(rt["label"]))
                if not rt["lane_geometry"]:
                    row["notes_chips"].append("Approx. lane length")
                if red.get("shell"):
                    row["notes_chips"].append("Shell access")
                if has_dis and base["status"] == "delayed" and base["added"]["median"] <= EPS:
                    row["notes_chips"].append("Upper bound only")
                if has_dis and not rt["waits"] and base["status"] == "unaffected":
                    row["notes_chips"].append("Disruption not on route")
        row["_red"] = red
        lane_rows.append(row)
        for s in ln["ships"]:
            view = {"status": row["status"], "added_days": row["added_days"], "baseline_days": row["baseline_days"],
                    "disrupted_days": row["disrupted_days"], "route_label": row["route_label"],
                    "route_id": row["_route_id"], "via": row["via"], "chips": []}
            if red is not None and row["status"] != "not_served" and s["via_id"] is not None:
                v = read_lane(red, has_dis, s["via_id"])
                if v["status"] != "not_served":
                    view.update({"status": v["status"], "baseline_days": v["baseline"], "disrupted_days": v["disrupted"],
                                 "added_days": v["added"], "route_label": _plain(v["route"]["label"]),
                                 "route_id": v["route"]["id"],
                                 "via": [{"id": p["id"], "name": p["name"]} for p in v["route"]["places"] if p.get("role") == "gate"]})
                    if v["via_miss"]:
                        view["chips"].append("Via not on a modelled route")
            val = s["value_usd"]
            affected = view["status"] in ("delayed", "rerouted")
            ship_by_id[s["id"]] = {
                "id": s["id"], "lane_key": k, "status": view["status"], "added_days": view["added_days"],
                "baseline_days": view["baseline_days"], "disrupted_days": view["disrupted_days"],
                "value_usd": val, "value_in_affected_usd": (val if (affected and val is not None) else 0.0),
                "route_label": view["route_label"], "via": view["via"], "notes_chips": view["chips"],
                "_red": red, "_route_id": view["route_id"],
            }

    # ---- shipment rows, in request order
    out_ships = []
    for s in ships:
        got = ship_by_id.get(s["id"])
        if got is None:
            out_ships.append({"id": s["id"], "lane_key": None, "status": "unresolved", "added_days": None,
                              "baseline_days": None, "disrupted_days": None, "value_usd": s["value_usd"],
                              "value_in_affected_usd": 0.0, "route_label": None, "via": None, "notes_chips": []})
            continue
        out_ships.append({k: v for k, v in got.items() if not k.startswith("_")})

    # ---- most exposed places: places where affected shipments met a wait, the disrupted place, and the gates
    exposed: dict[str, dict] = {}
    for s in ships:
        got = ship_by_id.get(s["id"])
        if got is None or got["status"] not in ("delayed", "rerouted"):
            continue
        red = got["_red"]
        if red is None:
            continue
        rt = next((r for r in red["routes"] if r["id"] == got["_route_id"]), None)
        if rt is None:
            continue
        names = {p["id"]: p["name"] for p in rt["places"]}
        hit = {q["id"] for q in rt["waits"]}
        dp = (scen_echo.get("place") or {}).get("id")
        if dp and any(p["id"] == dp for p in rt["places"]):
            hit.add(dp)
        for pid in hit:
            e = exposed.setdefault(pid, {"place": pid, "name": names.get(pid) or graph.nodes[pid].get("name"), "n": 0})
            e["n"] += 1
    most = sorted(exposed.values(), key=lambda e: (-e["n"], str(e["name"]), e["place"]))[:8]
    for e in most:
        n = graph.nodes.get(e["place"], {})
        e["lat"], e["lon"] = n.get("lat"), n.get("lon")

    by_lane = [{k: v for k, v in r.items() if not k.startswith("_")} for r in lane_rows]
    n_total = len(ships)
    served = [x for x in out_ships if x["status"] in ("unaffected", "delayed", "rerouted")]
    affected = [x for x in served if x["status"] in ("delayed", "rerouted")]
    value_aff = sum(x["value_in_affected_usd"] for x in affected)
    value_all = sum((x["value_usd"] or 0.0) for x in out_ships)
    meds = [x["added_days"]["median"] for x in affected]
    summary = {
        "n": n_total, "n_served": len(served), "n_affected": len(affected),
        "n_not_served": sum(1 for x in out_ships if x["status"] == "not_served"),
        "n_unresolved": sum(1 for x in out_ships if x["status"] == "unresolved"),
        "share_affected": _r(len(affected) / len(served), 4) if served else None,
        "added_days_median_range": ({"low": min(meds), "high": max(meds)} if meds else None),
        "added_days_envelope": ({"low": min(x["added_days"]["low"] for x in affected),
                                 "median": _r(statistics.median(meds)),
                                 "high": max(x["added_days"]["high"] for x in affected)} if affected else None),
        "value_in_affected_usd": _r(value_aff, 2), "value_label": VALUE_LABEL,
        "value_total_usd": _r(value_all, 2),
        "most_exposed_places": most,
        "assumptions": list(ASSUMPTIONS),
    }
    doc = {
        "schema": SCHEMA, "label": LABEL, "scenario": scen_echo, "resolved": resolved, "unresolved": unresolved,
        "by_lane": by_lane, "shipments": out_ships, "summary": summary,
        "counts": {"shipments": n_total, "unique_lanes": len(lanes)},
    }
    doc["mc"] = mc_overlay(mc, lane_rows, scen_echo) if mc is not None else None
    return doc


# --------------------------------------------------------------- Monte Carlo overlay (optional)
LANE_PLAN_ENGINE_REV = 2          # the lane planner runs engine rev 2 (laneplan.planner)
MISSING_IDS_CAP = 40
_EPS = 1e-6


def mc_state(job_id: str, job: dict | None) -> dict:
    """The MC block of the response from a finished job's document, or NOT SERVED."""
    if not job_id:
        return {"state": "NOT SERVED"}
    if not isinstance(job, dict) or job.get("status") != "done" or not isinstance(job.get("results"), dict):
        return {"state": "NOT SERVED", "job_id": job_id}
    res = job["results"]
    per = {}
    for row in res.get("per_place") or []:
        pid, d = row.get("place"), row.get("added_delay_d") or {}
        if isinstance(pid, str) and all(isinstance(d.get(k), (int, float)) for k in ("p10", "p50", "p90")):
            per[pid] = {"p10": float(d["p10"]), "p50": float(d["p50"]), "p90": float(d["p90"]),
                        "affected_share": row.get("affected_share")}
    return {"state": "SERVED", "job_id": job_id, "n": res.get("n"), "label": job.get("label"),
            "request": job.get("request"), "inputs": job.get("inputs"), "per_place": per}


def _num_or_none(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _within(value, mc: dict, name: str, base_key: str):
    """True/False/None: is the scenario's ``value`` inside the job's range for input ``name``
    (or equal to the job's fixed value when the job carries no range). None = cannot tell."""
    if value is None:
        return None
    for spec in mc.get("inputs") or []:
        if isinstance(spec, dict) and spec.get("name") == name:
            lo, hi = _num_or_none(spec.get("low")), _num_or_none(spec.get("high"))
            if lo is not None and hi is not None:
                return lo - _EPS <= value <= hi + _EPS
    dis = (mc.get("request") or {}).get("disruption") or {}
    base = _num_or_none(dis.get(base_key))
    return None if base is None else abs(base - value) <= _EPS


def mc_match(mc: dict, scen: dict | None) -> dict:
    """Does the finished job describe the scenario the exposure is evaluated under? Four checks
    (place, kind, duration, capacity loss), each True, False or None (cannot tell). ``scenario_match``
    is False when any check failed, True only when every check could be made and passed, else None.
    The engine is reported beside it, not folded in: the lane planner always runs engine rev 2."""
    req = mc.get("request") if isinstance(mc.get("request"), dict) else {}
    dis = req.get("disruption") if isinstance(req.get("disruption"), dict) else {}
    scen = scen or {}
    explicit = scen.get("mode") in ("library", "explicit")
    sp = (scen.get("place") or {}).get("id") if explicit else None
    jp = dis.get("node_id")
    checks = {
        "place": None if not (sp and jp) else sp == jp,
        "kind": None if not (explicit and scen.get("kind") and dis.get("kind")) else scen["kind"] == dis["kind"],
        "duration": _within(_num_or_none(scen.get("duration_d")) if explicit else None, mc, "duration_d", "duration_d"),
        "capacity_loss": _within(_num_or_none(scen.get("capacity_loss")) if explicit else None, mc,
                                 "capacity_loss", "capacity_loss"),
    }
    vals = list(checks.values())
    match = False if any(v is False for v in vals) else (True if all(v is True for v in vals) else None)
    labels = {"place": "disruption place", "kind": "disruption kind", "duration": "duration",
              "capacity_loss": "capacity loss"}
    rev = req.get("engine_rev")
    return {"scenario_match": match, "match_checks": checks,
            "mismatches": [labels[k] for k, v in checks.items() if v is False],
            "engine": {"engine_rev": rev, "profile": req.get("profile"),
                       "lane_plan_engine_rev": LANE_PLAN_ENGINE_REV,
                       "same": None if rev is None else rev == LANE_PLAN_ENGINE_REV}}


def mc_overlay(mc: dict | None, lane_rows: list, scen: dict | None = None) -> dict:
    """The MC block. ``by_lane`` keeps its shape (older clients); everything else is additive:
    ``scenario_match`` / ``match_checks`` / ``mismatches`` / ``engine`` say whether the job is about this
    scenario, ``missing_places`` counts lane places the job's list does not hold (the job lists its top
    places only, so a place can be absent because it is small or because it fell outside the list),
    and ``statistic`` names what the lane range is."""
    if mc is None or mc.get("state") != "SERVED":
        return mc or {"state": "NOT SERVED"}
    per = mc["per_place"]
    lanes, missing = [], set()
    for row in lane_rows:
        red = row.get("_red")
        if red is None or row["status"] == "not_served":
            continue
        rt = next((r for r in red["routes"] if r["id"] == row["_route_id"]), None)
        route_places = [p["id"] for p in (rt["places"] if rt else [])]
        hits = [per[p] for p in route_places if p in per]
        gone = [p for p in route_places if p not in per]
        if not hits:
            continue
        missing.update(gone)
        lanes.append({"lane_key": row["lane_key"],
                      "added_days": _rng(max(h["p10"] for h in hits), max(h["p50"] for h in hits), max(h["p90"] for h in hits)),
                      "places": len(hits), "places_missing": len(gone), "places_total": len(route_places)})
    out = {"state": "SERVED", "job_id": mc["job_id"], "n": mc["n"], "label": mc["label"], "by_lane": lanes,
           "statistic": "max_of_place_percentiles",
           "rule": ("largest place delay (p10, p50, p90 each taken separately) among the lane's places listed by "
                    "the job; this is not a percentile of the lane's worst place"),
           "job_places_listed": len(per),
           "missing_places": len(missing), "missing_place_ids": sorted(missing)[:MISSING_IDS_CAP]}
    out.update(mc_match(mc, scen))
    return out
