"""RECORDED agent transport: scripted tool-call sequences, no model, no key.

``fixture_agent_turn(history, tools, nodes)`` is a PURE function of the item
history so far: it returns the next model turn, either parallel tool calls or
a final text. It is reached only through ``LLMProvider.respond`` on the
fixture transport (the single provider seam), so the spend ledger, caps and
kill switch apply exactly as they do live, and every record is labelled
RECORDED. The script never reads instructions out of tool results: it only
reads structured fields (ids, counts, ranges) from them, which also keeps
it immune to injected instructions.

Demo tasks (matched by keywords in the user message):
  1. Suez closure for 14 d with the reroute (one run to confirm, summary, lane
     plans with and without the closure, the run and Suez shown in the UI);
     this is the demo storyline and the same sentence as the recorded voice
     phrase (server/voice/service.py FIXTURE_PHRASES[0])
  2. Lane plan Singapore to Rotterdam, with and without a Suez closure
  3. Rotterdam crane strike, port simulation vs its own baseline
  4. Fork the latest run with a longer shock, verify, compare
  6. SAM 3.1 and the Meta AI vision read of the latest Rotterdam crop (recorded replies)
  7. Research (fixture_research.py): real read tools over the bundled research caches, then a
     note whose numbers the code checks. Los Angeles and Long Beach before and after the 2021
     congestion peak with weather and scene listings; an event study on the Ever Given
     blockage; change points in Panama Canal transits with the events listed near them
  8. Scenario builder: compose or arrange a shock timeline ('a canal closure then a port
     strike 5 days later', 'stagger these', 'cluster all within a week'); browser steps only
  anything else: a place search and a plain answer listing what it can do

Final texts quote numbers exactly as the tools returned them, say modelled or
simulated, never predict, and carry no accuracy claim.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

LABEL = "RECORDED SCRIPT (fixture transport, not a live model)"
# UI control tools run in the browser; a missing or slow browser never ends a
# scripted task (the answer then just does not mention the view change).
UI_TOOLS = frozenset({"open_brief", "open_window", "arrange_window", "apply_layout", "set_mode",
                      "focus_place", "show_run", "open_research", "scenario_open", "scenario_organise"})


def _ui_done(res: list, name: str) -> bool:
    return any(r["name"] == name and r["data"].get("status") == "done" for r in res)


def _call(turn: int, i: int, name: str, args: dict) -> dict:
    return {"call_id": f"call_{turn}_{i}", "name": name,
            "arguments": json.dumps(args, sort_keys=True)}


def _segment(history: list) -> tuple[str, list]:
    """(last user text, items after it)."""
    idx = max((i for i, it in enumerate(history)
               if it.get("type") == "message" and it.get("role") == "user"), default=-1)
    if idx < 0:
        return "", []
    return str(history[idx].get("content") or ""), history[idx + 1:]


def _results(seg: list) -> list[dict]:
    """[{turn, name, args, data|error}] in call order, outputs matched by call_id."""
    calls = {it["call_id"]: it for it in seg if it.get("type") == "function_call"}
    out = []
    for it in seg:
        if it.get("type") != "function_call_output":
            continue
        c = calls.get(it.get("call_id"))
        if c is None:
            continue
        try:
            wrapper = json.loads(it.get("output") or "{}")
        except ValueError:
            wrapper = {}
        data = wrapper.get("data") if isinstance(wrapper, dict) else None
        try:
            args = json.loads(c.get("arguments") or "{}")
        except ValueError:
            args = {}
        out.append({"turn": c.get("turn", 0), "name": c.get("name"), "args": args,
                    "data": data if isinstance(data, dict) else {}})
    return out


def _turns_done(seg: list) -> int:
    return len({it.get("turn") for it in seg if it.get("type") == "function_call"})


def _pick(res: list, name: str, nth: int = 0) -> dict | None:
    rows = [r for r in res if r["name"] == name]
    return rows[nth] if len(rows) > nth else None


def _first_place(res_row: dict | None, kind: str | None, fallback: str) -> str:
    if res_row:
        for p in res_row["data"].get("places", []):
            if kind is None or p.get("kind") == kind:
                return p.get("id") or fallback
    return fallback


def _declined(res: list) -> dict | None:
    for r in res:
        if r["data"].get("status") == "rejected_by_user":
            return r
    return None


def _fmt_range(b) -> str:
    if not isinstance(b, dict):
        return "no range"
    return f"{b.get('low')} to {b.get('high')} {b.get('unit', 'd')} (median {b.get('median')})"


def _final(lines: list[str]) -> dict:
    return {"text": "\n".join([LABEL] + lines[:6]), "tool_calls": []}


def _stop_text(res: list) -> dict | None:
    d = _declined(res)
    if d:
        return _final([f"Stopped: you declined {d['name']}; nothing further was run.",
                       "Modelled output only."])
    errs = [r for r in res if r["data"].get("error") and r["name"] not in UI_TOOLS]
    if errs:
        e = errs[0]["data"]["error"]
        return _final([f"{errs[0]['name']} could not complete: {e.get('code')}.",
                       "Nothing further was run."])
    return None


# ------------------------------------------------------------------ scripts
def _t_suez_story(text, res, n):
    """The demo storyline: a canal closure.
    Reads in parallel, ONE action that waits for the person (the 14 d Suez
    closure), then a summary, two lane plans (with and without the closure) and
    two view changes (the run in the causal chain, Suez on the map)."""
    if n == 0:
        return {"tool_calls": [
            _call(1, 0, "search_places", {"query": "suez", "kind": "chokepoint", "limit": 3}),
            _call(1, 1, "search_places", {"query": "shanghai", "mode": "sea", "limit": 3}),
            _call(1, 2, "search_places", {"query": "rotterdam", "mode": "sea", "limit": 3})],
            "text": ""}
    if n == 1:
        su = _first_place(_pick(res, "search_places", 0), "chokepoint", "chk_suez")
        return {"tool_calls": [
            _call(2, 0, "start_run", {"place_id": su, "kind": "closure", "duration_d": 14})],
            "text": ""}
    stop = _stop_text(res)
    if stop and n <= 2:
        return stop
    if n == 2:
        run = _pick(res, "start_run")["data"].get("run_id")
        su = _first_place(_pick(res, "search_places", 0), "chokepoint", "chk_suez")
        o = _first_place(_pick(res, "search_places", 1), None, "sea_shanghai")
        d = _first_place(_pick(res, "search_places", 2), None, "sea_rotterdam")
        return {"tool_calls": [
            _call(3, 0, "run_summary", {"run_id": run}),
            _call(3, 1, "lane_plan", {"origin": o, "destination": d}),
            _call(3, 2, "lane_plan", {"origin": o, "destination": d,
                                      "disruption": {"place_id": su, "kind": "closure",
                                                     "duration_d": 14}}),
            _call(3, 3, "show_run", {"run_id": run}),
            _call(3, 4, "focus_place", {"place_id": su})], "text": ""}
    if stop:
        return stop
    s = _pick(res, "run_summary")["data"]
    p0, p1 = _pick(res, "lane_plan", 0)["data"], _pick(res, "lane_plan", 1)["data"]
    sh = (s.get("shocks") or [{}])[0]
    top = (s.get("top_places") or [{}])[0]

    def routes(plan):
        return "; ".join(f"{r.get('label')} {_fmt_range(r.get('total_d'))}"
                         for r in (plan.get("routes") or [])[:2]) or "no routes"
    lines = [f"{sh.get('name')} {sh.get('kind')} {sh.get('duration_d')} d (run {s.get('run_id')}): "
             f"{s.get('event_count')} events, {s.get('places_reached')} places reached, largest delay "
             f"{top.get('max_delay_d')} d at {top.get('name')} (modelled).",
             f"Shanghai to Rotterdam, no disruption (modelled range): {routes(p0)}.",
             f"With the 14 d Suez closure (modelled range): {routes(p1)}."]
    shown = [w for w, ok in (("the Suez run in the causal chain", _ui_done(res, "show_run")),
                             ("Suez on the map", _ui_done(res, "focus_place"))) if ok]
    if shown:
        lines.append("Showing " + " and ".join(shown) + " (view only).")
    lines.append("One deterministic run and named-variant ranges, not forecasts.")
    return _final(lines)


def _t_show(text, res, n):
    """UI control only: nothing is run, nothing needs a confirmation."""
    if n == 0:
        return {"tool_calls": [
            _call(1, 0, "search_places", {"query": "rotterdam", "mode": "sea", "limit": 3})],
            "text": ""}
    if n == 1:
        pid = _first_place(_pick(res, "search_places", 0), None, "sea_rotterdam")
        return {"tool_calls": [
            _call(2, 0, "focus_place", {"place_id": pid}),
            _call(2, 1, "open_window", {"window": "laneplanner", "how": "float"})], "text": ""}
    shown = []
    if _ui_done(res, "focus_place"):
        shown.append("Rotterdam is selected on the map")
    if _ui_done(res, "open_window"):
        shown.append("the lane planner is open")
    return _final([("; ".join(shown).capitalize() + ".") if shown
                   else "The view could not be changed from here.",
                   "View changes only; no run was started."])


def _t_lane(text, res, n):
    if n == 0:
        return {"tool_calls": [
            _call(1, 0, "search_places", {"query": "singapore", "mode": "sea", "limit": 3}),
            _call(1, 1, "search_places", {"query": "rotterdam", "mode": "sea", "limit": 3})],
            "text": ""}
    if n == 1:
        o = _first_place(_pick(res, "search_places", 0), None, "sea_singapore")
        d = _first_place(_pick(res, "search_places", 1), None, "sea_rotterdam")
        return {"tool_calls": [
            _call(2, 0, "lane_plan", {"origin": o, "destination": d}),
            _call(2, 1, "lane_plan", {"origin": o, "destination": d,
                                      "disruption": {"place_id": "chk_suez", "kind": "closure",
                                                     "duration_d": 14}})], "text": ""}
    stop = _stop_text(res)
    if stop:
        return stop
    p0, p1 = _pick(res, "lane_plan", 0)["data"], _pick(res, "lane_plan", 1)["data"]

    def best(plan):
        return [f"{r.get('label')}: {_fmt_range(r.get('total_d'))}" for r in plan.get("routes", [])[:2]]
    lines = [f"{p0.get('origin')} to {p0.get('destination')}, modelled ranges (low to high over "
             f"named variants, not forecasts). No disruption (plan {p0.get('plan_id')}):"]
    lines += best(p0)
    lines += [f"With a 14 d Suez closure (plan {p1.get('plan_id')}):"] + best(p1)
    return _final(lines)


def _t_port(text, res, n):
    if n == 0:
        return {"tool_calls": [
            _call(1, 0, "search_places", {"query": "rotterdam", "mode": "sea", "limit": 3})],
            "text": ""}
    if n == 1:
        pid = _first_place(_pick(res, "search_places", 0), None, "sea_rotterdam")
        return {"tool_calls": [
            _call(2, 0, "get_place", {"place_id": pid}),
            _call(2, 1, "port_scenario", {"place_id": pid, "scenario_type": "strike",
                                          "factor": 0.5, "start_h": 0, "duration_h": 48, "days": 7})],
            "text": ""}
    stop = _stop_text(res)
    if stop:
        return stop
    pl = _pick(res, "get_place")["data"]
    ps = _pick(res, "port_scenario")["data"]
    w = ps.get("wait_h") or {}
    q = ps.get("queue") or {}
    lines = [f"{pl.get('name')} with a crane-capacity strike (factor 0.5, 48 h), simulated over "
             f"{ps.get('days')} days (sim {ps.get('sim_id')}):",
             f"waiting time mean {w.get('mean')} h, 95th percentile {w.get('p95')} h, max {w.get('max')} h; "
             f"queue peak {q.get('peak')}, mean {q.get('mean')}."]
    vb = ps.get("vs_baseline") or {}
    delta = vb.get("delta") or {}
    if delta:
        k = sorted(delta)[0]
        lines.append(f"Against the port's own baseline, {k}: {delta[k]}.")
    lines.append("Simulated, not observed; not a forecast.")
    return _final(lines)


def _t_fork(text, res, n):
    if n == 0:
        return {"tool_calls": [_call(1, 0, "list_runs", {"limit": 5, "kind": "chain"})], "text": ""}
    runs = (_pick(res, "list_runs")["data"].get("runs") or [])
    stop = _stop_text(res)
    if stop:
        return stop
    started = _pick(res, "start_run")
    parent = (started["data"].get("run_id") if started
              else (runs[0].get("id") if runs else None))
    forked = _pick(res, "apply_shock")
    if forked is None:
        if parent is None:
            return {"tool_calls": [_call(n + 1, 0, "start_run", {
                "place_id": "chk_suez", "kind": "closure", "duration_d": 14})], "text": ""}
        return {"tool_calls": [_call(n + 1, 0, "apply_shock", {
            "run_id": parent, "at_seq": 0, "duration_d": 90})], "text": ""}
    child = forked["data"].get("run_id")
    if _pick(res, "verify_run") is None:
        return {"tool_calls": [
            _call(n + 1, 0, "verify_run", {"run_id": child}),
            _call(n + 1, 1, "compare_runs", {"a": forked["data"].get("parent_run_id"), "b": child})],
            "text": ""}
    v = _pick(res, "verify_run")["data"]
    c = _pick(res, "compare_runs")["data"]
    diff = (c.get("largest_differences") or [{}])[0]
    return _final([
        f"Forked run {forked['data'].get('parent_run_id')} at step {forked['data'].get('at_seq')} "
        f"with a 90 d shock: run {child}, {forked['data'].get('event_count')} events.",
        f"Replay check: stored hash match {v.get('stored_hash_matches')}, replay match "
        f"{v.get('replay_matches')}.",
        f"Chains diverge from step {c.get('diverges_from_seq')}; largest per-place difference "
        f"{diff.get('delay_delta_d')} d at {diff.get('name')} (modelled).",
        "Deterministic model output, not a forecast."])


def _t_brief(text, res, n):
    """One-page disruption brief for the latest run: one confirmed action, then
    the browser opens the brief window."""
    if n == 0:
        return {"tool_calls": [_call(1, 0, "list_runs", {"limit": 5, "kind": "chain"})], "text": ""}
    stop = _stop_text(res)
    if stop:
        return stop
    runs = (_pick(res, "list_runs")["data"].get("runs") or [])
    if not runs:
        return _final(["No stored run to write a brief from.", "Start a run first."])
    wrote = _pick(res, "write_brief")
    if wrote is None:
        return {"tool_calls": [_call(n + 1, 0, "write_brief", {"run_id": runs[0].get("id")})], "text": ""}
    d = wrote["data"]
    if _pick(res, "open_brief") is None and d.get("brief_id"):
        return {"tool_calls": [_call(n + 1, 0, "open_brief", {"brief_id": d["brief_id"]})], "text": ""}
    return _final([
        f"Brief {d.get('brief_id')} written for run {runs[0].get('id')} ({d.get('transport')}).",
        f"Statements verified against the figures: {d.get('verified')}; blocks replaced: {d.get('flagged')}.",
        "Every figure is read from the engine output; modelled, not a forecast."])


def _t_sat(text, res, n):
    """Two read tools over recorded crops: SAM 3.1 and the Muse Spark vision read."""
    if n == 0:
        return {"tool_calls": [
            _call(1, 0, "satellite_sam", {"place_id": "sea_rotterdam", "source": "sentinel-2",
                                          "role": "now", "prompt": "cargo vessel"}),
            _call(1, 1, "satellite_vision", {"place_id": "sea_rotterdam", "source": "sentinel-2",
                                             "role": "now"})], "text": ""}
    stop = _stop_text(res)
    if stop:
        return stop
    sam = _pick(res, "satellite_sam")["data"]
    vis = _pick(res, "satellite_vision")["data"]
    lines = [f"Rotterdam, Sentinel-2 crop of {sam.get('scene_date')}: {sam.get('summary')} for "
             f"'{sam.get('prompt')}' (recorded SAM 3.1 reply, modelled; {sam.get('verification') or 'Detected, not verified'}; what SAM found, not how many there are).",
             f"Meta AI vision read (recorded, modelled): congestion {vis.get('congestion_level')}, "
             f"image quality {vis.get('image_quality')}, confidence {vis.get('confidence')}."]
    if vis.get("cannot_see"):
        lines.append("It cannot see: " + "; ".join(str(x) for x in vis["cannot_see"][:3]) + ".")
    lines.append("A model reading of one native crop, not a count and not a forecast.")
    return _final(lines)


_NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
              "eight": 8, "nine": 9, "ten": 10, "fourteen": 14, "a week": 7, "one week": 7,
              "two weeks": 14, "a fortnight": 14}


def _days_in(t: str, default: float | None = None) -> float | None:
    """The first duration in a sentence, in days: '5 days', 'five days', 'a week', '2 weeks'."""
    import re
    m = re.search(r"(\d+(?:\.\d+)?)\s*(weeks?|days?|d)\b", t)
    if m:
        v = float(m.group(1))
        return v * 7 if m.group(2).startswith("week") else v
    for word, v in sorted(_NUM_WORDS.items(), key=lambda kv: -len(kv[0])):
        m = re.search(rf"\b{word}\s*(weeks?|days?)?\b", t)
        if m and (word in ("a week", "one week", "two weeks", "a fortnight") or m.group(1)):
            return float(v) * (7 if (m.group(1) or "").startswith("week") else 1)
    return default


def _scenario_rule(t: str) -> dict | None:
    """Sentence -> ONE structured rule (the recorded stand-in for the model's own translation)."""
    if "stagger" in t or "not overlap" in t or "no overlap" in t or "without overlap" in t:
        return {"kind": "stagger", "gap_d": _days_in(t, 0.0) if "gap" in t or "apart" in t else 0.0}
    if "cluster" in t or "within" in t:
        return {"kind": "cluster", "within_d": _days_in(t, 7.0)}
    if "evenly" in t or "spread" in t:
        return {"kind": "spread_evenly", "window_d": _days_in(t, 30.0)}
    if "same day" in t or "align" in t or "simultaneous" in t:
        return {"kind": "align_start", "day": 0}
    if "later" in t or "after" in t:
        return {"kind": "shift", "delta_d": _days_in(t, 1.0)}
    return None


def _t_scenario(text, res, n):
    """The Scenario builder through the tool path: read the rules, then either compose a timeline
    ('a canal closure then a port strike 5 days later') or arrange the shocks already on it
    ('stagger these', 'cluster all within a week'). Both are browser steps that fill the builder
    and show the change as a diff to accept or undo; nothing is run."""
    t = text.lower()
    compose = "then" in t and ("closure" in t or "strike" in t or "schedule" in t)
    if n == 0:
        calls = [_call(1, 0, "scenario_rules", {})]
        if compose:
            calls += [_call(1, 1, "search_places", {"query": "suez", "kind": "chokepoint", "limit": 3}),
                      _call(1, 2, "search_places", {"query": "rotterdam", "mode": "sea", "limit": 3})]
        return {"tool_calls": calls, "text": ""}
    if n == 1:
        if compose:
            su = _first_place(_pick(res, "search_places", 0), "chokepoint", "chk_suez")
            ro = _first_place(_pick(res, "search_places", 1), None, "sea_rotterdam")
            gap = _days_in(t, 5.0)
            return {"tool_calls": [
                _call(2, 0, "scenario_open", {"name": "Closure then strike", "shocks": [
                    {"id": "s1", "place_id": su, "kind": "closure", "start_offset_d": 0, "duration_d": 14},
                    {"id": "s2", "place_id": ro, "kind": "strike", "start_offset_d": 0, "duration_d": 7,
                     "capacity_loss": 0.5}]}),
                _call(2, 1, "scenario_organise", {"rule": {
                    "kind": "sequence", "order": ["s1", "s2"], "gap_d": gap, "reference": "start"}})],
                "text": ""}
        rule = _scenario_rule(t)
        if rule is None:
            return _final(["The arrangement could not be read as a rule.",
                           "Rules: stagger, sequence, cluster, align_start, spread_evenly, shift."])
        return {"tool_calls": [_call(2, 0, "scenario_organise", {"rule": rule})], "text": ""}
    stop = _stop_text(res)
    if stop:
        return stop
    org = _pick(res, "scenario_organise")
    if org is None or not _ui_done(res, "scenario_organise"):
        return _final(["The builder timeline could not be changed from here.",
                       "Open the Scenario builder window and try again. Nothing was run."])
    kind = org["args"].get("rule", {}).get("kind")
    lines = [f"Timeline arranged with one rule ({kind}); the change is shown as a diff in the Scenario builder.",
             "Accept keeps it, Undo restores the previous starts. Nothing was run."]
    if _pick(res, "scenario_open") and _ui_done(res, "scenario_open"):
        lines.insert(0, "Scenario builder filled with a canal closure and a port strike.")
    return _final(lines)


ARRANGE_MARK = "Shocks on the timeline (id | place | start | duration):"


def _t_arrange(text, res, n):
    """The Arrange box (purpose session, one tool): the sentence becomes ONE rule over the shock ids
    listed in the message; the same tool path then runs. No sentence match: one plain line."""
    body, _, instruction = text.partition("Instruction from the user:\n")
    ids = [ln.split(" | ")[0].strip() for ln in body.splitlines()[1:] if " | " in ln]
    t = instruction.lower()
    if n == 0:
        rule = None
        if len(ids) >= 2 and ("then" in t or "first" in t) and ("later" in t or "after" in t):
            rule = {"kind": "sequence", "order": ids[:12], "gap_d": _days_in(t, 5.0), "reference": "start"}
        rule = rule or _scenario_rule(t)
        if rule is None:
            return _final(["The instruction could not be read as one rule.",
                           "Rules: stagger, sequence, cluster, align_start, spread_evenly, shift."])
        return {"tool_calls": [_call(1, 0, "scenario_organise", {"rule": rule})], "text": ""}
    org = _pick(res, "scenario_organise")
    if org is None or not _ui_done(res, "scenario_organise"):
        return _final(["The timeline could not be changed from here.", "Nothing was run."])
    kind = org["args"].get("rule", {}).get("kind")
    return _final([f"Arranged with one rule ({kind}); the change is shown as a diff.",
                   "Accept keeps it, Undo restores the previous starts. Nothing was run."])


def _t_generic(text, res, n):
    words = [w.strip(".,?!").lower() for w in text.split() if len(w.strip(".,?!")) >= 4]
    if n == 0:
        q = (sorted(words, key=len, reverse=True) or ["port"])[0][:40]
        return {"tool_calls": [_call(1, 0, "search_places", {"query": q, "limit": 5}),
                               _call(1, 1, "list_scenarios", {})], "text": ""}
    sp = _pick(res, "search_places")
    ls = _pick(res, "list_scenarios")
    names = ", ".join(p.get("name") or p.get("id") for p in (sp["data"].get("places") if sp else [])[:5])
    scen = ", ".join(s.get("label") for s in (ls["data"].get("scenarios") if ls else [])[:4])
    return _final([
        f"Places matching your words: {names or 'none found'}.",
        f"Scenarios I can run for you (each asks first): {scen or 'none listed'}.",
        "Ask for a comparison, a lane plan, a port scenario or a fork-and-verify."])


# ------------------------------------------------------------ chat scripts
WEB_FIXTURE_PATH = Path(__file__).with_name("web_fixture.json")
_WEB_INTENT_RE = re.compile(r"\bnews\b|headlines?|search (the )?(web|internet)|on the web|\bonline\b", re.I)


def _context_chips(text: str) -> list[dict]:
    """The attached-context data block of the last user message, if any."""
    m = re.search(r"<data>\n(.*?)\n</data>", text, re.S)
    if not m:
        return []
    try:
        doc = json.loads(m.group(1).replace("<\\/data>", "</data>"))
    except ValueError:
        return []
    chips = doc.get("attached_context") if isinstance(doc, dict) else None
    return [c for c in chips if isinstance(c, dict)] if isinstance(chips, list) else []


def load_web_fixture(path: Path | None = None) -> dict | None:
    """The recorded web reply (a real reply saved once). None when absent."""
    try:
        doc = json.loads((path or WEB_FIXTURE_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) and doc.get("paragraphs") and doc.get("sources") else None


def _web_turn(doc: dict) -> dict:
    """A recorded cited answer: text plus url citations placed at the end of
    each paragraph, and the recorded search hits. Nothing is searched."""
    head = (f"{LABEL}. Recorded web reply of {doc.get('recorded_at')}: these sources were "
            "retrieved on that date, not now.")
    text, cites = head, []
    for p in doc["paragraphs"]:
        text += "\n\n"
        start = len(text)
        text += str(p["text"])
        for i in p.get("cite") or []:
            src = doc["sources"][int(i)]
            cites.append({"url": src["url"], "title": src.get("title"), "start": start, "end": len(text)})
    return {"text": text, "tool_calls": [], "web": {
        "searches": [{"id": "ws_recorded", "status": "completed", "query": doc.get("query"),
                      "results": doc["sources"]}], "citations": cites}}


def _t_web(web_on: bool):
    def script(text, res, n):
        if not web_on:
            return _final(["Web search is off for this thread.",
                           "Turn it on in the composer to search the internet; no sources were retrieved."])
        low = text.lower()
        doc = load_web_fixture()
        if doc is None or "rotterdam" not in low:
            return _final(["Recorded mode holds one cited web reply: port congestion at Rotterdam.",
                           "No recorded web reply exists for this question; a live chat searches the internet."])
        return _web_turn(doc)
    return script


def _t_summarise(text, res, n):
    chips = _context_chips(text)
    rid = next((c.get("id") for c in chips if c.get("kind") in ("run", "chain") and c.get("id")), None)
    if rid is None and n == 0:
        return {"tool_calls": [_call(1, 0, "list_runs", {"limit": 1})], "text": ""}
    if n == 0:
        return {"tool_calls": [_call(1, 0, "run_summary", {"run_id": rid})], "text": ""}
    if rid is None and _pick(res, "run_summary") is None:
        runs = (_pick(res, "list_runs") or {"data": {}})["data"].get("runs") or []
        if not runs:
            return _final(["No run is attached and none is stored.", "Start a run, then ask again."])
        return {"tool_calls": [_call(2, 0, "run_summary", {"run_id": runs[0].get("id")})], "text": ""}
    stop = _stop_text(res)
    if stop:
        return stop
    s = _pick(res, "run_summary")["data"]
    sh = (s.get("shocks") or [{}])[0]
    top = (s.get("top_places") or [{}])[0]
    ch = ", ".join(f"{k} {v}" for k, v in sorted((s.get("channel_counts") or {}).items())[:4])
    return _final([f"Run {s.get('run_id')}: {sh.get('name')} {sh.get('kind')} {sh.get('duration_d')} d (modelled).",
                   f"{s.get('event_count')} events, {s.get('places_reached')} places reached; largest delay "
                   f"{top.get('max_delay_d')} d at {top.get('name')}.",
                   f"Events by channel: {ch or 'none'}.",
                   "Deterministic model output, not a forecast."])


def _t_shipments(text, res, n):
    if n == 0:
        return {"tool_calls": [
            _call(1, 0, "search_places", {"query": "singapore", "mode": "sea", "limit": 3}),
            _call(1, 1, "search_places", {"query": "rotterdam", "mode": "sea", "limit": 3})], "text": ""}
    if n == 1:
        o = _first_place(_pick(res, "search_places", 0), None, "sea_singapore")
        d = _first_place(_pick(res, "search_places", 1), None, "sea_rotterdam")
        return {"tool_calls": [
            _call(2, 0, "lane_plan", {"origin": o, "destination": d}),
            _call(2, 1, "lane_plan", {"origin": o, "destination": d,
                                      "disruption": {"place_id": "chk_suez", "kind": "closure",
                                                     "duration_d": 14}})], "text": ""}
    stop = _stop_text(res)
    if stop:
        return stop
    p0, p1 = _pick(res, "lane_plan", 0)["data"], _pick(res, "lane_plan", 1)["data"]

    def best(plan):
        r = (plan.get("routes") or [{}])[0]
        return f"{r.get('label')}: {_fmt_range(r.get('total_d'))}"
    has_list = any(c.get("kind") == "shipments" for c in _context_chips(text))
    return _final([("Shipment list attached: lane effect shown for its main lane." if has_list
                    else "No shipment list is attached; the lane effect is shown instead."),
                   f"Singapore to Rotterdam, no disruption (modelled range): {best(p0)}.",
                   f"With a 14 d Suez closure (modelled range): {best(p1)}.",
                   "Named-variant ranges, not forecasts."])


def _task(text: str, web_on: bool = False):
    t = text.lower()
    if ARRANGE_MARK.lower() in t:
        return _t_arrange
    if any(k in t for k in ("timeline", "stagger", "cluster", "do not overlap", "not overlap",
                            "days later", "spread evenly", "same day")) and "run" not in t.split():
        return _t_scenario
    from . import fixture_research as fr
    if "los angeles" in t and "long beach" in t:
        return fr.t_lalb
    if "ever given" in t:
        return fr.t_evergiven
    if "change point" in t and "panama" in t:
        return fr.t_changepoints
    if _WEB_INTENT_RE.search(t):
        return _t_web(web_on)
    if ("summaris" in t or "summariz" in t) and "run" in t:
        return _t_summarise
    if "shipment" in t:
        return _t_shipments
    if "suez" in t and ("close" in t or "closure" in t or "block" in t) \
            and ("reroute" in t or "cape" in t):
        return _t_suez_story
    if "brief" in t.split() or "one-page" in t:
        return _t_brief
    if "on the map" in t:
        return _t_show
    if ("sam" in t.split() or "satellite" in t) and "rotterdam" in t:
        return _t_sat
    if ("singapore" in t and "rotterdam" in t) or "lane" in t:
        return _t_lane
    if any(k in t for k in ("strike", "crane", "queue", "port scenario", "congest")) and "rotterdam" in t:
        return _t_port
    if any(k in t for k in ("fork", "verify", "longer", "extend")):
        return _t_fork
    return _t_generic


DEMO_TASKS = [
    "Close the Suez Canal for fourteen days and show me the reroute around the Cape of Good Hope",
    "Plan a lane from Singapore to Rotterdam and show the range if the Suez canal is closed.",
    "How congested could Rotterdam get if a crane strike cuts capacity?",
    "Fork the latest run with a longer shock and verify it.",
    "Show Rotterdam on the map and open the lane planner next to it.",
    "What did SAM find in the latest satellite crop of Rotterdam?",
    "Write a one-page disruption brief for the latest run.",
]


def fixture_agent_turn(history: list, tools: list, nodes: list | None = None) -> dict:
    text, seg = _segment(history)
    res = _results(seg)
    n = _turns_done(seg)
    web_on = any(isinstance(t, dict) and t.get("type") == "web_search" for t in tools or [])
    plain = text.split("\n\n[Attached context.")[0]
    turn = _task(plain, web_on)(text, res, n)
    # stamp the turn number the loop will record on these calls
    for i, c in enumerate(turn.get("tool_calls", [])):
        c["call_id"] = f"call_{n + 1}_{i}"
    out = {"text": turn.get("text", ""), "tool_calls": turn.get("tool_calls", [])}
    if turn.get("web"):
        out["web"] = turn["web"]
    return out
