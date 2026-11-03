"""Fact extraction: every number, place and source a brief may state.

``build_facts`` reads the stored run (and, when their ids are supplied, the
lane planner, a Monte Carlo job, a shipment exposure summary, a research
episode) and registers each value once in a ``FactTable`` as F1..Fn:

    {id, key, label, kind, value, unit, display, section, chip, source, names}

``kind`` is number | range | text | date | citation | row | status | code.
``display`` is the exact string the server inserts for a {Fn} placeholder.
``source`` says where the value came from (run id, chain event seq, lane plan
variant, Monte Carlo percentile, exposure row, feed state and retrieved_at,
citation URL, publisher and date), with a short human ``label``.

Nothing here is an estimate of its own: a value that cannot be read from an
input is not registered, and the section that needed it reads NOT SERVED.
"""
from __future__ import annotations

import math
from typing import Any

from . import fmt
from .model import (MAX_CONTEXT_EVENTS, MAX_EVENT_ROWS, MAX_EXPOSED_PLACES,
                    MAX_LEG_ROWS, MAX_MC_PLACES, MAX_PLACE_ROWS,
                    MAX_ROUTE_ROWS)

KIND_WORDS = {"closure": "closure", "capacity_cut": "capacity cut",
              "congestion": "congestion", "strike": "strike",
              "weather": "weather event", "grounding": "grounding",
              "accident": "accident", "policy": "policy change",
              "other": "disruption"}

REFUSAL_WORDS = {
    "unknown_place": "Place not found",
    "same_place": "Same place",
    "not_a_sea_place": "Not a sea place",
    "shell_endpoint": "Place not modelled",
    "no_sea_path": "No sea path",
    "unknown_scenario": "Scenario not found",
    "disruption_place_not_modelled": "Disrupted place not modelled",
    "invalid_disruption": "Disruption not accepted",
    "invalid_request": "Request not accepted",
}

INPUT_WORDS = {"duration_d": "Duration", "capacity_loss": "Capacity loss",
               "queue_sensitivity": "Queue sensitivity"}
INPUT_UNITS = {"duration_d": "d", "capacity_loss": "", "queue_sensitivity": ""}


def is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


class FactTable:
    def __init__(self) -> None:
        self.items: list[dict] = []
        self._by_key: dict[str, dict] = {}

    def add(self, key: str, label: str, kind: str, value: Any, display: str, *,
            section: str, source: dict, unit: str | None = None,
            chip: str = "MODELLED", names: list[str] | None = None) -> dict:
        if key in self._by_key:
            raise ValueError(f"duplicate fact key {key}")
        fact = {"id": f"F{len(self.items) + 1}", "key": key, "label": label,
                "kind": kind, "value": value, "unit": unit, "display": display,
                "section": section, "chip": chip, "source": source,
                "names": list(names or [])}
        self.items.append(fact)
        self._by_key[key] = fact
        return fact

    def get(self, key: str) -> dict | None:
        return self._by_key.get(key)

    def by_id(self) -> dict[str, dict]:
        return {f["id"]: f for f in self.items}

    def in_section(self, section: str) -> list[dict]:
        return [f for f in self.items if f["section"] == section]

    def keys_with_prefix(self, prefix: str) -> list[dict]:
        return [f for f in self.items if f["key"].startswith(prefix)]


# ---------------------------------------------------------------- numbers

def fact_numbers(fact: dict) -> list[float]:
    """Numerals a literal in prose may legitimately match for this fact."""
    out: list[float] = []

    def walk(v):
        if is_num(v):
            out.append(float(v))
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)
        elif isinstance(v, str) and fact["kind"] == "date":
            out.extend(fmt.numbers_in(v))
    walk(fact["value"])
    if fact["kind"] != "code":
        out.extend(fmt.numbers_in(fact["display"]))
    return out


# ------------------------------------------------------------------ source

def src(type_: str, label: str, **kw) -> dict:
    return {"type": type_, "label": label, **kw}


def _r1(x):
    return None if not is_num(x) else round(float(x), 3)


def _rng3(block) -> dict | None:
    if not isinstance(block, dict):
        return None
    lo, md, hi = block.get("low"), block.get("median"), block.get("high")
    if not (is_num(lo) and is_num(md) and is_num(hi)):
        return None
    a = sorted((float(lo), float(md), float(hi)))
    return {"low": round(a[0], 3), "median": round(a[1], 3), "high": round(a[2], 3)}


# ---------------------------------------------------------------- fetchers

class AsgiFetch:
    """Reads sibling features through the app's own routes, in process. A
    route that is absent (404/405), a refusal or any error reads as 'not
    served': the section then says so and nothing stands in for it."""

    def __init__(self, app) -> None:
        self.app = app

    def _call(self, method: str, path: str, body=None):
        try:
            try:
                from agent.apicall import AsgiApi
            except ImportError:  # imported as server.* from the repo root
                from server.agent.apicall import AsgiApi  # type: ignore
            api = AsgiApi(self.app)
            res = api.get(path) if method == "GET" else api.post(path, body)
            return res.status, res.body
        except Exception:
            return 0, None

    def plan(self, body: dict):
        return self._call("POST", "/lanes/plan", body)

    def mc(self, job_id: str):
        return self._call("GET", f"/montecarlo/{job_id}")

    def exposure(self, evaluation_id: str):
        return self._call("GET", f"/exposure/evaluations/{evaluation_id}")

    def episodes(self):
        return self._call("GET", "/research/episodes")


# ------------------------------------------------------------------- build

class Built:
    """Result of fact extraction."""

    def __init__(self) -> None:
        self.facts = FactTable()
        self.states: dict[str, dict] = {}
        self.raw: dict[str, Any] = {}
        self.digest_parts: dict[str, Any] = {}


def _state(built: Built, section: str, ok: bool, reason: str | None = None) -> None:
    built.states[section] = {"state": "SERVED" if ok else "NOT SERVED",
                             "reason": None if ok else reason}


def _day_offset(at, start_dt) -> float | None:
    dt = fmt.parse_iso(at)
    if dt is None or start_dt is None:
        return None
    return (dt - start_dt).total_seconds() / 86400.0


def build_facts(ctx, bundle, req: dict, sections: list[str], fetch) -> Built:
    b = Built()
    t = b.facts
    rid = bundle.run_id
    j = bundle.journal
    d = j["disruption"]
    seed_id = d["node_id"]
    seed_name = bundle.name(seed_id)
    run_src = lambda ref, label: src("run", f"Run {rid}, {label}", run_id=rid, ref=ref)
    b.digest_parts["run"] = {"run_id": rid, "stream_hash": bundle.record.get("stream_hash")}

    # ---------------------------------------------------------- situation
    kind = str(d.get("kind") or "other")
    kind_word = KIND_WORDS.get(kind, kind.replace("_", " "))
    loss = d.get("capacity_loss")
    loss = 1.0 if kind == "closure" and not is_num(loss) else loss
    dur = d.get("duration_d")
    start = d.get("started_at")
    t.add("sit.place", "Disrupted place", "text", seed_name, seed_name, section="situation",
          source=run_src("journal.disruption.node_id", "disruption"), chip="INPUT", names=[seed_name])
    t.add("sit.kind", "Disruption kind", "text", kind, kind_word, section="situation",
          source=run_src("journal.disruption.kind", "disruption"), chip="INPUT")
    if is_num(loss):
        t.add("sit.loss", "Capacity loss", "number", round(float(loss) * 100, 3),
              fmt.pct(float(loss) * 100), unit="%", section="situation",
              source=run_src("journal.disruption.capacity_loss", "disruption"), chip="INPUT")
    if is_num(dur):
        t.add("sit.duration", "Disruption duration", "number", float(dur), fmt.days(dur),
              unit="d", section="situation",
              source=run_src("journal.disruption.duration_d", "disruption"), chip="INPUT")
    if start:
        t.add("sit.start", "Start", "date", start, fmt.date(start), section="situation",
              source=run_src("journal.disruption.started_at", "disruption"), chip="INPUT")
    rev = j.get("engine_label") or "ENGINE REV 1"
    prof = j.get("profile")
    rev_txt = "Engine rev " + str(rev).split()[-1] if str(rev).split() else "Engine rev 1"
    t.add("sit.engine", "Engine", "text", rev, rev_txt + (f", profile {prof}" if prof else ""),
          section="situation", source=run_src("journal.engine_label", "journal"), chip="MODELLED")
    t.add("sit.run", "Run", "code", rid, rid, section="situation",
          source=run_src("run_id", "identifier"), chip="MODELLED")
    t.add("sit.stream", "Chain hash", "code", str(bundle.record.get("stream_hash") or "")[:12],
          str(bundle.record.get("stream_hash") or "")[:12], section="situation",
          source=run_src("stream_hash", "event stream"), chip="MODELLED")

    basis, cite = _situation_basis(ctx, bundle, d, j)
    t.add("sit.basis", "Basis of the scenario", "text", basis["text"], basis["text"],
          section="situation", source=basis["source"], chip=basis["chip"])
    if cite is not None:
        t.add("sit.cite", "Source citation", "citation", cite,
              f"{cite['publisher']}, {cite['date_text']}", section="situation",
              source=src("citation", f"{cite['publisher']}, {cite['date_text']}",
                         publisher=cite["publisher"], title=cite.get("title"),
                         url=cite.get("url"), date=cite.get("date")),
              chip="SOURCED")
    _state(b, "situation", True)

    # -------------------------------------------------------------- chain
    events = list(bundle.events)
    start_dt = fmt.parse_iso(start)
    if start_dt is None and events:
        start_dt = min((fmt.parse_iso(e["at"]) for e in events if fmt.parse_iso(e["at"])), default=None)
    by_seq = {e["seq"]: e for e in events}
    places_in = {e["node_id"] for e in events}
    channels = sorted({str(e["channel"]) for e in events})
    ev_src = lambda e: src("chain_event", f"Run {rid}, chain event {e['seq']}", run_id=rid, seq=e["seq"])
    t.add("chain.events", "Chain events", "number", len(events), fmt.num(len(events), 0),
          unit="events", section="chain", source=run_src("event_count", "event stream"))
    t.add("chain.places", "Places on the chain", "number", len(places_in), fmt.num(len(places_in), 0),
          unit="places", section="chain", source=run_src("events[].node_id", "event stream"))
    t.add("chain.channels", "Channels", "text", channels, ", ".join(channels) or "none",
          section="chain", source=run_src("events[].channel", "event stream"))
    spans = [x for x in (_day_offset(e["at"], start_dt) for e in events) if x is not None]
    if spans:
        t.add("chain.span", "Days until the chain reaches its last place", "number",
              round(max(spans), 3), fmt.days(max(spans)), unit="d", section="chain",
              source=run_src("events[].at", "event stream"))
    if events:
        worst = max(events, key=lambda e: (e["delay_d"], -e["seq"]))
        t.add("chain.worst", "Largest single added delay", "number", float(worst["delay_d"]),
              fmt.days(worst["delay_d"]), unit="d", section="chain", source=ev_src(worst))
        t.add("chain.worst_place", "Place of the largest added delay", "text",
              bundle.name(worst["node_id"]), bundle.name(worst["node_id"]), section="chain",
              source=ev_src(worst), names=[bundle.name(worst["node_id"])])
    rows = []
    for e in events[:MAX_EVENT_ROWS]:
        name = bundle.name(e["node_id"])
        day = _day_offset(e["at"], start_dt)
        arrival = None if day is None else round(max(0.0, day - float(e["delay_d"])), 3)
        row = {"seq": e["seq"], "place": name, "channel": str(e["channel"]),
               "mechanism": str(e.get("mechanism") or ""), "added_d": round(float(e["delay_d"]), 3),
               "arrival_d": arrival}
        rows.append(row)
        t.add(f"chain.ev.{e['seq']}", f"Chain event {e['seq']}", "row", row,
              f"{name} ({e['channel']}, {fmt.days(e['delay_d'])})", section="chain",
              source=ev_src(e), names=[name])
    b.raw["events"] = events
    b.raw["event_rows"] = rows
    b.raw["start_dt"] = start_dt
    _state(b, "chain", True)

    # ------------------------------------------------------------- places
    mc_doc = None
    mc_places: dict[str, dict] = {}
    if "uncertainty" in sections or "places" in sections:
        mc_doc = _fetch_mc(req, fetch)
        b.raw["mc_doc"] = mc_doc
        if mc_doc is not None:
            for row in (mc_doc.get("results") or {}).get("per_place") or []:
                if isinstance(row, dict) and isinstance(row.get("place"), str):
                    mc_places[row["place"]] = row
    worst_by: dict[str, dict] = {}
    for e in events:
        if e["node_id"] == seed_id:
            continue
        w = worst_by.get(e["node_id"])
        if w is None or e["delay_d"] > w["delay_d"]:
            worst_by[e["node_id"]] = e
    ranked = sorted(worst_by.values(), key=lambda e: (-e["delay_d"], e["node_id"]))
    impact_by = {r["node_id"]: r for r in (bundle.place_rows or [])}
    place_rows = []
    for e in ranked[:MAX_PLACE_ROWS]:
        pid = e["node_id"]
        name = bundle.name(pid)
        row = {"place": name, "place_id": pid, "channel": str(e["channel"]),
               "added_d": round(float(e["delay_d"]), 3), "seq": e["seq"],
               "range": None, "capacity_at_risk": None, "unit_class": None}
        extra = ""
        mp = mc_places.get(pid)
        if mp is not None:
            ad = mp.get("added_delay_d") or {}
            if is_num(ad.get("p10")) and is_num(ad.get("p90")):
                row["range"] = {"low": round(float(ad["p10"]), 3), "high": round(float(ad["p90"]), 3),
                                "basis": "p10 to p90 of simulations"}
                extra = f", simulated range {fmt.rng(ad['p10'], ad['p90'])}"
        ir = impact_by.get(pid)
        if ir and is_num(ir.get("capacity_at_risk")):
            row["capacity_at_risk"] = round(float(ir["capacity_at_risk"]), 3)
            row["unit_class"] = ir.get("unit_class")
        t.add(f"place.{pid}", f"Place {name}", "row", row,
              f"{name} ({fmt.days(e['delay_d'])}{extra})", section="places",
              source=src("chain_event", f"Run {rid}, chain event {e['seq']}", run_id=rid, seq=e["seq"]),
              names=[name])
        place_rows.append(row)
    b.raw["place_rows"] = place_rows
    t.add("places.count", "Places hit apart from the disrupted place", "number", len(worst_by),
          fmt.num(len(worst_by), 0), unit="places", section="places",
          source=run_src("events[].node_id", "event stream"))
    legs = []
    for leg in bundle.edges:
        if leg["from_seq"] is None:
            continue
        ev = by_seq.get(leg["to_seq"])
        if ev is None:
            continue
        legs.append((ev["delay_d"], leg, ev))
    legs.sort(key=lambda x: (-x[0], x[1]["to_seq"]))
    leg_rows = []
    for dly, leg, ev in legs[:MAX_LEG_ROWS]:
        a, z = bundle.name(leg["from_node"]), bundle.name(leg["to_node"])
        row = {"from": a, "to": z, "channel": str(leg["channel"]), "added_d": round(float(dly), 3),
               "seq": ev["seq"]}
        leg_rows.append(row)
        t.add(f"leg.{ev['seq']}", f"Leg {a} to {z}", "row", row,
              f"{a} to {z} ({leg['channel']}, {fmt.days(dly)})", section="places",
              source=ev_src(ev), names=[a, z])
    b.raw["leg_rows"] = leg_rows
    _state(b, "places", True)

    # -------------------------------------------------------- delay ranges
    if "delay" in sections:
        _lane_facts(b, bundle, req, fetch)

    # ------------------------------------------------------ uncertainty
    if "uncertainty" in sections:
        _mc_facts(b, req, mc_doc)

    # --------------------------------------------------------- exposure
    if "exposure" in sections:
        _exposure_facts(b, req, fetch)

    # ---------------------------------------------------------- context
    if "context" in sections:
        _context_facts(b, req, fetch)

    # ------------------------------------------------------ data status
    if "data" in sections:
        _feed_facts(b, ctx)
    return b


# ------------------------------------------------------------ situation

def _situation_basis(ctx, bundle, d: dict, j: dict):
    """(basis, citation or None). A live-event run cites the event's
    human-readable page; a scenario run says it is a scenario."""
    rid = bundle.run_id
    if getattr(bundle, "recorded_replay", False):
        return ({"text": "Recorded replay", "chip": "RECORDED",
                 "source": src("scenario", f"Run {rid}, recorded replay chain", run_id=rid)}, None)
    did = j.get("live_disruption_id")
    if isinstance(did, str) and did:
        ev = _find_event(ctx, did)
        if ev is not None:
            mode = "LIVE" if str(ev.get("mode")) == "LIVE" else "SNAPSHOT"
            word = "Live" if mode == "LIVE" else "Recorded"
            publisher = str(ev.get("source") or "feed")
            text = f"{word} {publisher} event"
            cite = None
            try:
                try:
                    from store.citations import derive_citation
                except ImportError:
                    from server.store.citations import derive_citation  # type: ignore
                c = derive_citation({k: v for k, v in ev.items() if k != "citation"})
            except Exception:
                c = None
            if isinstance(c, dict) and fmt.safe_url(c.get("url")):
                dt = c.get("published_at")
                cite = {"publisher": fmt.clean_text(c.get("publisher"), 80),
                        "title": fmt.clean_text(c.get("title"), 160), "url": c["url"],
                        "date": dt, "date_text": fmt.date(dt) if dt else "undated"}
            return ({"text": text, "chip": mode,
                     "source": src("event", f"{publisher}, event {did}", event_id=did,
                                   mode=mode, retrieved_at=ev.get("retrieved_at"))}, cite)
    match = None
    try:
        try:
            from api.chain_loop import _SCENARIOS
        except ImportError:
            from server.api.chain_loop import _SCENARIOS  # type: ignore
        for s in _SCENARIOS:
            if s.get("place_id") == d.get("node_id") and s.get("kind") == d.get("kind"):
                match = s
                break
    except Exception:
        match = None
    if match is not None:
        text = f"Scenario library: {fmt.clean_text(match.get('label'), 80)}"
    else:
        text = "Scenario entered by the user"
    return ({"text": text, "chip": "INPUT",
             "source": src("scenario", f"Run {rid}, scenario inputs", run_id=rid)}, None)


def _find_event(ctx, did: str):
    try:
        try:
            from ports.sources import read_events
        except ImportError:
            from server.ports.sources import read_events  # type: ignore
        evs = read_events(ctx.events_path, ctx.now) or []
    except Exception:
        return None
    for e in evs:
        if isinstance(e, dict) and e.get("id") == did:
            return e
    return None


# ----------------------------------------------------------- lane plan

def _lane_facts(b: Built, bundle, req: dict, fetch) -> None:
    t = b.facts
    lane = req.get("lane_plan")
    if not lane:
        _state(b, "delay", False, "No lane supplied")
        return
    d = bundle.journal["disruption"]
    dis = {"place_id": d["node_id"], "kind": d.get("kind"), "duration_d": d.get("duration_d")}
    if d.get("kind") != "closure" and is_num(d.get("capacity_loss")):
        dis["capacity_loss"] = d["capacity_loss"]
    body = {"origin": lane["origin"], "destination": lane["destination"], "disruption": dis}
    status, doc = fetch.plan(body) if fetch is not None else (0, None)
    if status != 200 or not isinstance(doc, dict):
        reason = "Lane planner not served"
        if isinstance(doc, dict):
            code = ((doc.get("refusal") or {}).get("reason")
                    or (doc.get("error") or {}).get("code"))
            reason = REFUSAL_WORDS.get(code, reason)
        _state(b, "delay", False, reason)
        return
    plan_id = doc.get("plan_id")
    places = doc.get("places") or {}
    on = (places.get("origin") or {}).get("name") or lane["origin"]
    dn = (places.get("destination") or {}).get("name") or lane["destination"]
    psrc = lambda route, variant: src("lane_plan", f"Lane plan {plan_id}, {route}, {variant}",
                                      plan_id=plan_id, route=route, variant=variant)
    b.digest_parts["lane"] = {"plan_id": plan_id}
    t.add("lane.pair", "Lane", "text", f"{on} to {dn}", f"{on} to {dn}", section="delay",
          source=psrc("lane", "inputs"), chip="MODELLED", names=[on, dn])
    t.add("lane.engine", "Planner engine", "text", doc.get("engine_label"),
          str(doc.get("engine_label") or "Engine rev 2").capitalize(),
          section="delay", source=psrc("lane", "engine"), chip="SIMULATED")
    var = doc.get("variants") or {}
    n_speed = len(var.get("sail") or [])
    n_queue = len(var.get("queue") or [])
    if n_speed:
        t.add("lane.n_speed", "Speed variants", "number", n_speed, fmt.num(n_speed, 0),
              unit="variants", section="delay", source=psrc("variants", "sail"), chip="MODELLED")
    if n_queue:
        t.add("lane.n_queue", "Queue variants", "number", n_queue, fmt.num(n_queue, 0),
              unit="variants", section="delay", source=psrc("variants", "queue"), chip="SIMULATED")
    end = _rng3((doc.get("disruption") or {}).get("end_d"))
    if end:
        t.add("lane.end", "Day the disruption ends across duration variants", "range", end,
              fmt.rng(end["low"], end["high"]), unit="d", section="delay",
              source=psrc("disruption", "end_d"), chip="MODELLED")
    routes = [r for r in (doc.get("routes") or []) if isinstance(r, dict)]
    shown = []
    for r in routes[:MAX_ROUTE_ROWS]:
        sail = _rng3(r.get("sail"))
        wait = _rng3(r.get("queue_at_arrival"))
        total = _rng3(r.get("total_at_arrival")) or _rng3(r.get("total"))
        if total is None:
            continue
        label = fmt.clean_text(r.get("label"), 80)
        gates = [fmt.clean_text(g.get("name"), 60) for g in (r.get("gates") or []) if isinstance(g, dict)]
        row = {"route_id": r.get("id"), "label": label, "corridor": r.get("corridor"),
               "baseline": bool(r.get("is_baseline")), "sail": sail, "wait": wait, "total": total,
               "gates": gates, "disrupted_place_on_route": bool(r.get("disrupted_place_on_route"))}
        shown.append(row)
        t.add(f"lane.route.{r.get('id')}", f"Route {label}", "row", row,
              f"{label} ({fmt.rng(total['low'], total['high'])}, median {fmt.days(total['median'])})",
              section="delay", source=psrc(str(r.get("id")), "total_at_arrival, low to high"),
              chip="SIMULATED", names=gates)
        if r.get("is_baseline"):
            t.add("lane.base.total", "Baseline route total at arrival", "range", total,
                  fmt.rng(total["low"], total["high"]), unit="d", section="delay",
                  source=psrc(str(r.get("id")), "total_at_arrival, low to high"), chip="SIMULATED")
            t.add("lane.base.median", "Baseline route median", "number", total["median"],
                  fmt.days(total["median"]), unit="d", section="delay",
                  source=psrc(str(r.get("id")), "total_at_arrival, median"), chip="SIMULATED")
            if sail:
                t.add("lane.base.sail", "Baseline route sail time", "range", sail,
                      fmt.rng(sail["low"], sail["high"]), unit="d", section="delay",
                      source=psrc(str(r.get("id")), "sail, low to high"), chip="MODELLED")
            if wait:
                t.add("lane.base.wait", "Baseline route wait at arrival", "range", wait,
                      fmt.rng(wait["low"], wait["high"]), unit="d", section="delay",
                      source=psrc(str(r.get("id")), "queue_at_arrival, low to high"),
                      chip="SIMULATED")
    if not shown:
        _state(b, "delay", False, "No route served")
        return
    for s in doc.get("sources") or []:
        if isinstance(s, dict) and s.get("state") == "SOURCED" and fmt.safe_url(s.get("url")):
            title = fmt.clean_text(s.get("title") or s.get("label"), 160)
            pub = title.split(" ")[0] if title else "Source"
            cite = {"publisher": pub, "title": title, "url": s["url"], "date": None,
                    "date_text": _cite_date(title)}
            t.add("lane.cite", "Cited reference", "citation", cite, f"{pub}, {cite['date_text']}",
                  section="delay", source=src("citation", f"{pub}, {cite['date_text']}", publisher=pub,
                                              title=title, url=s["url"], date=cite["date_text"]),
                  chip="SOURCED")
            break
    b.raw["lane_routes"] = shown
    b.raw["lane_pair"] = f"{on} to {dn}"
    _state(b, "delay", True)


def _cite_date(title: str) -> str:
    import re
    m = re.search(r"\(([A-Z][a-z]{2,8} \d{4})\)", title or "")
    return m.group(1) if m else "undated"


# ---------------------------------------------------------- Monte Carlo

def _fetch_mc(req: dict, fetch):
    job = req.get("mc_job_id")
    if not job or fetch is None:
        return None
    status, doc = fetch.mc(job)
    if status != 200 or not isinstance(doc, dict):
        return {"_not_served": "Monte Carlo job not served"}
    if doc.get("status") != "done" or not isinstance(doc.get("results"), dict):
        return {"_not_served": "Monte Carlo job not finished"}
    return doc


def _mc_facts(b: Built, req: dict, doc) -> None:
    t = b.facts
    job = req.get("mc_job_id")
    if not job:
        _state(b, "uncertainty", False, "No Monte Carlo job supplied")
        return
    if not isinstance(doc, dict) or doc.get("_not_served") or not isinstance(doc.get("results"), dict):
        _state(b, "uncertainty", False, (doc or {}).get("_not_served") or "Monte Carlo job not served")
        return
    res = doc["results"]
    n = res.get("n")
    msrc = lambda ref, label: src("montecarlo", f"Monte Carlo {job}, {label}", job_id=job, ref=ref)
    b.digest_parts["mc"] = {"job_id": job, "runs_digest": res.get("runs_digest"), "n": n}
    t.add("mc.label", "Method label", "text", doc.get("label"), fmt.clean_text(doc.get("label"), 200),
          section="uncertainty", source=msrc("label", "label"), chip="SIMULATED")
    if is_num(n):
        t.add("mc.n", "Simulations", "number", int(n), fmt.num(n, 0), unit="simulations",
              section="uncertainty", source=msrc("results.n", "count"), chip="SIMULATED")
    eng = doc.get("engine") or {}
    if eng.get("engine_rev"):
        txt = f"Engine rev {eng['engine_rev']}" + (f", profile {eng['profile']}" if eng.get("profile") else "")
        t.add("mc.engine", "Simulation engine", "text", eng, txt, section="uncertainty",
              source=msrc("engine", "engine"), chip="SIMULATED")
    for i, inp in enumerate([x for x in (doc.get("inputs") or []) if isinstance(x, dict)][:3]):
        nm = str(inp.get("name"))
        word = INPUT_WORDS.get(nm, nm.replace("_", " ").capitalize())
        unit = INPUT_UNITS.get(nm, "")
        lo, md, hi = inp.get("low"), inp.get("mode"), inp.get("high")
        if not (is_num(lo) and is_num(hi)):
            continue
        scale = 100.0 if nm == "capacity_loss" else 1.0
        u = "%" if nm == "capacity_loss" else unit
        text = (f"{word}: {fmt.num(lo * scale)} to {fmt.num(hi * scale)}{(' ' + u) if u and u != '%' else u}"
                f" ({inp.get('dist')})")
        t.add(f"mc.in.{nm}", f"Input range: {word}", "row",
              {"input": word, "dist": inp.get("dist"), "low": lo * scale, "mode": (md * scale) if is_num(md) else None,
               "high": hi * scale, "unit": u}, text, section="uncertainty",
              source=msrc(f"inputs.{nm}", f"input {nm}"), chip="INPUT")
    metrics = res.get("metrics") or {}
    for key, label, unit, prefix in (("worst_added_transit_d", "Largest added delay", "d", "worst"),
                                     ("total_added_delay_d", "Sum of the largest delays by place", "d", "total"),
                                     ("places_affected_count", "Places affected", "places", "places")):
        m = metrics.get(key)
        if not isinstance(m, dict) or not all(is_num(m.get(p)) for p in ("p10", "p50", "p90")):
            continue
        dec = 0 if unit == "places" else 1
        t.add(f"mc.{prefix}.band", f"{label}, p10 to p90", "range",
              {"low": m["p10"], "median": m["p50"], "high": m["p90"]},
              fmt.rng(m["p10"], m["p90"], unit, dec), unit=unit, section="uncertainty",
              source=msrc(f"metrics.{key}", "p10 to p90"), chip="SIMULATED")
        t.add(f"mc.{prefix}.p50", f"{label}, median", "number", m["p50"],
              f"{fmt.num(m['p50'], dec)} {unit}", unit=unit, section="uncertainty",
              source=msrc(f"metrics.{key}.p50", "p50"), chip="SIMULATED")
        if prefix == "worst" and is_num(m.get("p05")) and is_num(m.get("p95")):
            t.add("mc.worst.wide", f"{label}, p05 to p95", "range",
                  {"low": m["p05"], "high": m["p95"]}, fmt.rng(m["p05"], m["p95"], unit, dec),
                  unit=unit, section="uncertainty", source=msrc(f"metrics.{key}", "p05 to p95"),
                  chip="SIMULATED")
    worst = metrics.get("worst_added_transit_d") or {}
    for i, ex in enumerate([x for x in (worst.get("exceedance") or []) if isinstance(x, dict)][:3]):
        if not (is_num(ex.get("threshold")) and is_num(ex.get("k")) and is_num(ex.get("n"))):
            continue
        t.add(f"mc.exceed.{i + 1}", "Share of simulations above a delay", "row",
              {"threshold_d": ex["threshold"], "k": int(ex["k"]), "n": int(ex["n"])},
              f"{fmt.num(ex['k'], 0)} of {fmt.num(ex['n'], 0)} simulations above {fmt.days(ex['threshold'])}",
              section="uncertainty", source=msrc("metrics.worst_added_transit_d.exceedance",
                                                 f"threshold {ex['threshold']} d"), chip="SIMULATED")
    mc_rows = []
    for row in [x for x in (res.get("per_place") or []) if isinstance(x, dict)][:MAX_MC_PLACES]:
        ad = row.get("added_delay_d") or {}
        if not all(is_num(ad.get(p)) for p in ("p10", "p50", "p90")):
            continue
        name = fmt.clean_text(row.get("name") or row.get("place"), 80)
        k = row.get("affected_k")
        r = {"place": name, "place_id": row.get("place"), "p10": ad["p10"], "p50": ad["p50"],
             "p90": ad["p90"], "affected_k": int(k) if is_num(k) else None, "n": int(n) if is_num(n) else None}
        mc_rows.append(r)
        t.add(f"mc.place.{row.get('place')}", f"Simulated range at {name}", "row", r,
              f"{name} ({fmt.rng(ad['p10'], ad['p90'])}, median {fmt.days(ad['p50'])})",
              section="uncertainty", source=msrc(f"per_place.{row.get('place')}", f"place {name}"),
              chip="SIMULATED", names=[name])
    sens = [x for x in (res.get("sensitivity") or []) if isinstance(x, dict) and is_num(x.get("swing_p10_p90"))]
    if sens:
        s0 = max(sens, key=lambda x: abs(x["swing_p10_p90"]))
        word = INPUT_WORDS.get(str(s0.get("input")), str(s0.get("input")))
        t.add("mc.sens", "Largest input swing", "row",
              {"input": word, "swing_d": s0["swing_p10_p90"], "rank_corr": s0.get("rank_corr")},
              f"{word}: {fmt.num(abs(s0['swing_p10_p90']))} d between the lowest and highest tenth of inputs",
              section="uncertainty", source=msrc("sensitivity", f"input {s0.get('input')}"),
              chip="SIMULATED")
    b.raw["mc_rows"] = mc_rows
    b.raw["mc_series"] = res.get("series")
    _state(b, "uncertainty", True)


# ------------------------------------------------------------- exposure

def _exposure_summary(req: dict, fetch):
    exp = req.get("exposure")
    if not exp:
        return None, "No exposure supplied"
    if "evaluation_id" in exp:
        status, doc = fetch.exposure(exp["evaluation_id"]) if fetch is not None else (0, None)
        if status != 200 or not isinstance(doc, dict):
            return None, "Exposure evaluation not served"
    else:
        doc = exp.get("inline")
    if not isinstance(doc, dict):
        return None, "Exposure summary not served"
    s = doc.get("summary") if isinstance(doc.get("summary"), dict) else doc
    return {"doc": doc, "s": s}, None


def _exposure_facts(b: Built, req: dict, fetch) -> None:
    t = b.facts
    got, why = _exposure_summary(req, fetch)
    if got is None:
        _state(b, "exposure", False, why)
        return
    doc, s = got["doc"], got["s"]
    esrc = lambda ref: src("exposure", f"Shipment exposure, {ref}", ref=ref)

    def pick(*names):
        for n in names:
            if is_num(s.get(n)):
                return float(s[n])
        return None
    n = pick("n", "shipments", "n_shipments")
    served = pick("n_served")
    aff = pick("n_affected", "affected", "shipments_affected")
    nots = pick("n_not_served", "not_served")
    unres = pick("n_unresolved")
    env = s.get("added_days_envelope") or s.get("added_days")
    env = _rng3(env) if isinstance(env, dict) else None
    val = pick("value_in_affected_usd", "value_in_affected")
    if n is None and aff is None and env is None and val is None:
        _state(b, "exposure", False, "Exposure summary not served")
        return
    b.digest_parts["exposure"] = {"n": n, "aff": aff, "env": env, "val": val}
    label = doc.get("label") if isinstance(doc.get("label"), str) else None
    if label:
        t.add("exp.label", "Method label", "text", label, fmt.clean_text(label, 160),
              section="exposure", source=esrc("label"), chip="MODELLED")
    sc = doc.get("scenario") if isinstance(doc.get("scenario"), dict) else None
    if sc and isinstance(sc.get("label"), str):
        t.add("exp.scenario", "Scenario of the evaluation", "text", sc["label"],
              fmt.clean_text(sc["label"], 120), section="exposure", source=esrc("scenario"),
              chip="MODELLED")
    if n is not None:
        t.add("exp.n", "Shipments evaluated", "number", int(n), fmt.num(n, 0), unit="shipments",
              section="exposure", source=esrc("summary.n"))
    if aff is not None:
        t.add("exp.affected", "Shipments hit", "number", int(aff), fmt.num(aff, 0),
              unit="shipments", section="exposure", source=esrc("summary.n_affected"))
    if served is not None:
        t.add("exp.served", "Shipments on served lanes", "number", int(served), fmt.num(served, 0),
              unit="shipments", section="exposure", source=esrc("summary.n_served"))
    if nots is not None:
        t.add("exp.not_served", "Shipments not served", "number", int(nots), fmt.num(nots, 0),
              unit="shipments", section="exposure", source=esrc("summary.n_not_served"),
              chip="NOT SERVED")
    if unres is not None and unres > 0:
        t.add("exp.unresolved", "Shipments with an unresolved place", "number", int(unres),
              fmt.num(unres, 0), unit="shipments", section="exposure",
              source=esrc("summary.n_unresolved"), chip="NOT SERVED")
    if env:
        t.add("exp.added", "Added days across affected shipments", "range", env,
              fmt.rng(env["low"], env["high"]), unit="d", section="exposure",
              source=esrc("summary.added_days_envelope, low to high"), chip="MODELLED")
        t.add("exp.added_median", "Median added days of the affected shipment set", "number",
              env["median"], fmt.days(env["median"]), unit="d", section="exposure",
              source=esrc("summary.added_days_envelope, median"), chip="MODELLED")
    if val is not None:
        t.add("exp.value", "Value in affected shipments", "number", val, fmt.usd(val), unit="USD",
              section="exposure", source=esrc("summary.value_in_affected_usd"), chip="INPUT")
    rows = []
    for p in [x for x in (s.get("most_exposed_places") or []) if isinstance(x, dict)][:MAX_EXPOSED_PLACES]:
        name = fmt.clean_text(p.get("name") or p.get("place"), 80)
        if not name or not is_num(p.get("n")):
            continue
        row = {"place": name, "shipments": int(p["n"])}
        rows.append(row)
        t.add(f"exp.place.{len(rows)}", f"Exposed place {name}", "row", row,
              f"{name} ({fmt.num(p['n'], 0)} shipments)", section="exposure",
              source=esrc(f"summary.most_exposed_places, {name}"), chip="MODELLED", names=[name])
    b.raw["exposure_rows"] = rows
    _state(b, "exposure", True)


# -------------------------------------------------------------- context

def _context_facts(b: Built, req: dict, fetch) -> None:
    t = b.facts
    eid = req.get("research_episode_id")
    if not eid:
        _state(b, "context", False, "No episode supplied")
        return
    status, doc = fetch.episodes() if fetch is not None else (0, None)
    ep = None
    if status == 200 and isinstance(doc, dict):
        for e in doc.get("episodes") or []:
            if isinstance(e, dict) and e.get("id") == eid:
                ep = e
                break
    if ep is None:
        _state(b, "context", False, "Episode not served")
        return
    rsrc = lambda ref: src("research", f"Research episode {eid}, {ref}", episode_id=eid, ref=ref)
    b.digest_parts["context"] = {"episode_id": eid}
    title = fmt.clean_text(ep.get("title"), 140)
    t.add("ctx.title", "Episode", "text", title, title, section="context", source=rsrc("title"),
          chip="SNAPSHOT")
    w = ep.get("window") or {}
    if w.get("from") and w.get("to"):
        t.add("ctx.window", "Episode window", "text", w, f"{fmt.date(w['from'])} to {fmt.date(w['to'])}",
              section="context", source=rsrc("window"), chip="SNAPSHOT")
    rows = []
    for ev in [x for x in (ep.get("events") or []) if isinstance(x, dict)][:MAX_CONTEXT_EVENTS]:
        s = ev.get("source") or {}
        url = fmt.safe_url(s.get("url"))
        pub = fmt.clean_text(s.get("publisher"), 80)
        if not (url and pub):
            continue
        row = {"title": fmt.clean_text(ev.get("title"), 120), "date": ev.get("t"), "publisher": pub}
        rows.append(row)
        t.add(f"ctx.ev.{len(rows)}", f"Dated event {len(rows)}", "citation",
              {**row, "url": url, "date_text": fmt.date(s.get("date") or ev.get("t"))},
              f"{pub}, {fmt.date(s.get('date') or ev.get('t'))}", section="context",
              source=src("citation", f"{pub}, {fmt.date(s.get('date') or ev.get('t'))}", publisher=pub,
                         title=row["title"], url=url, date=s.get("date") or ev.get("t")),
              chip="SOURCED")
    b.raw["context_rows"] = rows
    _state(b, "context", True)


# ----------------------------------------------------------- feed status

def _feed_facts(b: Built, ctx) -> None:
    t = b.facts
    try:
        try:
            from ports.feeds import Feeds
        except ImportError:
            from server.ports.feeds import Feeds  # type: ignore
        states = Feeds(ctx.snapshots_dir, ctx.now).all_states()
    except Exception:
        states = None
    if not states:
        _state(b, "data", False, "Feed board not served")
        return
    counts = {"LIVE": 0, "SNAPSHOT": 0, "NOT SERVED": 0}
    rows = []
    for s in states:
        st = s.get("state") if s.get("state") in counts else "NOT SERVED"
        counts[st] += 1
        ret = s.get("retrieved_at")
        row = {"feed": s["feed"], "label": s["label"], "state": st, "retrieved_at": ret}
        rows.append(row)
        text = f"{s['label']}, {st}" + (f", retrieved {fmt.stamp(ret)}" if ret and st != "NOT SERVED" else "")
        t.add(f"feed.{s['feed']}", f"Feed {s['label']}", "row", row, text, section="data",
              source=src("feed", f"Feed board, {s['label']}", feed=s["feed"], state=st, retrieved_at=ret),
              chip=st)
    for st, key in (("LIVE", "live"), ("SNAPSHOT", "snapshot"), ("NOT SERVED", "not_served")):
        t.add(f"data.{key}", f"Feeds {st.lower()}", "number", counts[st], fmt.num(counts[st], 0),
              unit="feeds", section="data", source=src("feed", "Feed board, state count"),
              chip=st)
    b.raw["feed_rows"] = rows
    _state(b, "data", True)
