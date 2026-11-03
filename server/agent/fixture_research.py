"""RECORDED research tasks (scripted, no model, no key).

Three scripts for the agent's fixture transport (see ``fixture.py``). Each
executes REAL read tools over the research routes and their bundled caches
(catalogue, episodes, assemble, analyse) and ends with ``write_research_note``
(the note is built from the evidence list and checked by code) and
``open_research`` (a view change). The script only reads structured fields
from tool results, never text; the final answer quotes numbers exactly as the
tools returned them, says descriptive statistics, associations and not a
forecast, and names evidence ids.
"""
from __future__ import annotations

from datetime import date, timedelta

from .fixture import _call, _final, _first_place, _pick, _stop_text

RESEARCH_TASKS = [
    "Compare port calls at Los Angeles and Long Beach before and after the 2021 congestion peak and relate them to weather and satellite scene counts.",
    "Run an event study on the Ever Given blockage.",
    "Look for change points in Panama Canal transits over the cached period and list coinciding events.",
]


def _g(d, *path, default=None):
    for k in path:
        if isinstance(d, dict):
            d = d.get(k)
        elif isinstance(d, list) and isinstance(k, int) and 0 <= k < len(d):
            d = d[k]
        else:
            return default
    return default if d is None else d


def _series_key(asm: dict, dataset_prefix: str, place: str | None = None) -> str | None:
    for row in asm.get("series") or []:
        if str(row.get("dataset", "")).startswith(dataset_prefix) and \
                (place is None or row.get("place") == place):
            return row.get("key")
    return None


def _note_turn(n: int, places: list, assembly: dict) -> dict:
    """The last tool turn of every research task: the note, then the view change."""
    w = assembly.get("window") or {}
    args = {"places": places[:5]}
    if w.get("from") and w.get("to"):
        args.update({"from": w["from"], "to": w["to"]})
    if assembly.get("assembly_id"):
        args["assembly_id"] = assembly["assembly_id"]
    return {"tool_calls": [_call(n + 1, 0, "write_research_note", {}),
                           _call(n + 1, 1, "open_research", args)], "text": ""}


def _close(res: list, lines: list[str]) -> dict:
    note = _pick(res, "write_research_note")
    if note is None:
        return _final(lines + ["The note could not be written."])
    d = note["data"]
    lines = lines + [f"Research note {d.get('note_id')}: verified {d.get('verified')} of "
                     f"{d.get('proposed')} findings ({d.get('label')}); the check ran in code.",
                     "Descriptive statistics on historical data; associations, not causes; "
                     "not a forecast."]
    return _final(lines)


def _asm_line(asm: dict, label: str) -> str:
    w, sc = asm.get("window") or {}, asm.get("scenes") or {}
    return (f"{label}, {w.get('from')} to {w.get('to')}: assembly {asm.get('assembly_id')}, "
            f"{asm.get('series_count')} series, {asm.get('event_count')} dated events, "
            f"{sc.get('listed')} scenes listed ({sc.get('usable')} usable) ({asm.get('evidence_id')}).")


def _analyses(res: list, kind: str) -> list[dict]:
    return [r["data"] for r in res
            if r["name"] == "research_analyze" and r["data"].get("kind") == kind]


# --------------------------------------------------------------------- tasks
def t_lalb(text, res, n):
    """Port calls at Los Angeles and Long Beach before and after the 2021
    congestion peak, with weather and satellite scene listings."""
    if n == 0:
        return {"tool_calls": [
            _call(1, 0, "search_places", {"query": "los angeles", "mode": "sea", "limit": 3}),
            _call(1, 1, "search_places", {"query": "long beach", "mode": "sea", "limit": 3}),
            _call(1, 2, "research_episodes", {"category": "congestion"})], "text": ""}
    la = _first_place(_pick(res, "search_places", 0), None, "sea_losangeles")
    lb = _first_place(_pick(res, "search_places", 1), None, "sea_longbeach")
    eps = (_pick(res, "research_episodes") or {"data": {}})["data"].get("episodes") or []
    ep = next((e for e in eps if "la_lb" in str(e.get("id"))), eps[0] if eps else {})
    stop = _stop_text(res)
    if stop and n <= 2:
        return stop
    if n == 1:
        return {"tool_calls": [_call(2, 0, "research_assemble", {
            "places": [la, lb], "from": ep.get("window_from") or "2021-08-01",
            "to": ep.get("window_to") or "2022-03-31",
            "series": ["portwatch_port_calls", "weather_wind_max_kmh", "weather_precip_mm"],
            "event_categories": ["congestion"], "event_source": "episodes",
            "scenes": True, "scene_sources": ["sentinel-2"], "max_scenes": 20})], "text": ""}
    asm = _pick(res, "research_assemble")["data"]
    if n == 2:
        evs = asm.get("events") or []
        ev = (evs[0] if evs else (ep.get("events") or [{}])[0]).get("id")
        calls_la = _series_key(asm, "portwatch_port_calls", la)
        calls_lb = _series_key(asm, "portwatch_port_calls", lb)
        wind_la = _series_key(asm, "weather_wind_max_kmh", la)
        aid = asm["assembly_id"]
        tc = []
        for key in (calls_la, calls_lb):       # a place the cache holds no series for is skipped
            if key:
                tc.append(_call(3, len(tc), "research_analyze", {
                    "assembly_id": aid, "kind": "before_after", "series": key,
                    "split": "event", "event_id": ev, "window_days": [-28, 28]}))
        if wind_la and calls_la:
            tc.append(_call(3, len(tc), "research_analyze", {
                "assembly_id": aid, "kind": "lagged_correlation",
                "x": wind_la, "y": calls_la, "max_lag_days": 14}))
        if not tc:
            return _final(["No port-call series is held for these places in the cached data."])
        return {"tool_calls": tc, "text": ""}
    if stop and n == 3:
        return stop
    if n == 3:
        return _note_turn(3, [la, lb], asm)
    if stop:
        return stop
    ba, lc = _analyses(res, "before_after"), _analyses(res, "lagged_correlation")
    lines = [_asm_line(asm, "Los Angeles and Long Beach")]
    held = [r.get("place") for r in asm.get("series") or [] if str(r.get("dataset")).startswith("portwatch_port_calls")]
    names = [nm for pid, nm in ((la, "Los Angeles"), (lb, "Long Beach")) if pid in held]
    for pid, nm in ((la, "Los Angeles"), (lb, "Long Beach")):
        if pid not in held:
            lines.append(f"{nm}: no port-call series in the cached data; not analysed.")
    for d, nm in zip(ba, names):
        lines.append(f"{nm} port calls around the listed event: mean {_g(d, 'estimate', 'before', 'mean')} "
                     f"before, {_g(d, 'estimate', 'after', 'mean')} after "
                     f"(n = {_g(d, 'estimate', 'before', 'n')} and {_g(d, 'estimate', 'after', 'n')} d) "
                     f"({d.get('evidence_id')}).")
    if lc:
        d = lc[0]
        lines.append(f"Wind and Los Angeles port calls: rank correlation {_g(d, 'estimate', 'peak', 'rho')} "
                     f"at lag {_g(d, 'estimate', 'peak', 'lag')} d (n = {_g(d, 'estimate', 'peak', 'n')}); "
                     f"several lags examined ({d.get('evidence_id')}).")
    return _close(res, lines)


def t_evergiven(text, res, n):
    """An event study on the Ever Given blockage in the Suez Canal."""
    if n == 0:
        return {"tool_calls": [
            _call(1, 0, "search_places", {"query": "suez", "kind": "chokepoint", "limit": 3}),
            _call(1, 1, "research_episodes", {"category": "accident_blockage"})], "text": ""}
    suez = _first_place(_pick(res, "search_places", 0), "chokepoint", "chk_suez")
    eps = (_pick(res, "research_episodes") or {"data": {}})["data"].get("episodes") or []
    ep = next((e for e in eps if "ever_given" in str(e.get("id"))), eps[0] if eps else {})
    stop = _stop_text(res)
    if stop and n <= 2:
        return stop
    if n == 1:
        sers = [s for s in ep.get("series") or [] if str(s).startswith("portwatch_transits")][:1]
        return {"tool_calls": [_call(2, 0, "research_assemble", {
            "places": [suez], "from": ep.get("window_from") or "2021-02-01",
            "to": ep.get("window_to") or "2021-05-15",
            "series": sers or ["portwatch_transits"],
            "event_categories": ["accident_blockage"], "event_source": "episodes"})], "text": ""}
    asm = _pick(res, "research_assemble")["data"]
    if n == 2:
        evs = asm.get("events") or []
        ev = next((e.get("id") for e in evs if "grounding" in str(e.get("id"))),
                  (evs[0] if evs else {}).get("id"))
        key, aid = _series_key(asm, "portwatch_transits", suez), asm["assembly_id"]
        return {"tool_calls": [
            _call(3, 0, "research_analyze", {"assembly_id": aid, "kind": "event_study", "series": key,
                                             "event_ids": [ev], "window_days": [-14, 28]}),
            _call(3, 1, "research_analyze", {"assembly_id": aid, "kind": "before_after", "series": key,
                                             "split": "event", "event_id": ev,
                                             "window_days": [-14, 14]})], "text": ""}
    if stop and n == 3:
        return stop
    if n == 3:
        return _note_turn(3, [suez], asm)
    if stop:
        return stop
    es, ba = _analyses(res, "event_study"), _analyses(res, "before_after")
    lines = [_asm_line(asm, "Suez Canal")]
    if es:
        d = es[0]
        lines.append(f"Event study (index 1.0 is the pre-event mean): lowest index "
                     f"{_g(d, 'estimate', 'trough', 'index')} at day {_g(d, 'estimate', 'trough', 'offset_day')}; "
                     f"recovery marker day {_g(d, 'estimate', 'recovery_offset_day')}; "
                     f"n = {d.get('n')} {d.get('n_unit')} ({d.get('evidence_id')}).")
    if ba:
        d = ba[0]
        lines.append(f"Transits before and after the event: mean {_g(d, 'estimate', 'before', 'mean')} "
                     f"then {_g(d, 'estimate', 'after', 'mean')} ({d.get('evidence_id')}).")
    return _close(res, lines)


def _near(a: str, b: str, days: int = 14) -> bool:
    try:
        return abs((date.fromisoformat(str(a)[:10]) - date.fromisoformat(str(b)[:10])).days) <= days
    except ValueError:
        return False


def _five_years_back(to: str) -> str:
    """First day of the longest window the assemble route accepts (1,826 days)."""
    try:
        return (date.fromisoformat(to) - timedelta(days=1825)).isoformat()
    except ValueError:
        return to


def t_changepoints(text, res, n):
    """Change points in Panama Canal transits over the period the cache
    covers, with the dated events the assembly lists near them."""
    if n == 0:
        return {"tool_calls": [
            _call(1, 0, "search_places", {"query": "panama canal", "kind": "chokepoint", "limit": 3}),
            _call(1, 1, "research_catalog", {})], "text": ""}
    rot = _first_place(_pick(res, "search_places", 0), "chokepoint", "chk_panama")
    stop = _stop_text(res)
    if stop and n <= 2:
        return stop
    if n == 1:
        cat = (_pick(res, "research_catalog") or {"data": {}})["data"].get("datasets") or []
        ds = next((d for d in cat if d.get("id") == "portwatch_transits"), {})
        to = ds.get("coverage_to") or "2024-06-30"
        frm = max(ds.get("coverage_from") or "2023-06-01", _five_years_back(to))
        return {"tool_calls": [_call(2, 0, "research_assemble", {
            "places": [rot], "from": frm, "to": to, "series": ["portwatch_transits"],
            "event_categories": ["canal_restriction", "weather", "accident_blockage"],
            "event_source": "episodes"})], "text": ""}
    asm = _pick(res, "research_assemble")["data"]
    if n == 2:
        return {"tool_calls": [_call(3, 0, "research_analyze", {
            "assembly_id": asm["assembly_id"], "kind": "change_points",
            "series": _series_key(asm, "portwatch_transits", rot), "method": "cusum",
            "min_size": 7})], "text": ""}
    if stop and n == 3:
        return stop
    if n == 3:
        return _note_turn(3, [rot], asm)
    if stop:
        return stop
    cps = _analyses(res, "change_points")
    lines = [_asm_line(asm, "Panama Canal transits")]
    if cps:
        cp = cps[0]
        first = _g(cp, "estimate", "points", 0, default={})
        lines.append(f"Change points ({_g(cp, 'estimate', 'method')}, minimum segment "
                     f"{_g(cp, 'estimate', 'min_size')} d): {_g(cp, 'estimate', 'point_count')} "
                     f"({cp.get('evidence_id')}).")
        if first:
            lines.append(f"First, on {first.get('date')}: mean {first.get('mean_before')} before and "
                         f"{first.get('mean_after')} after.")
            near = [e for e in asm.get("events") or [] if _near(e.get("t"), first.get("date"))]
            lines.append("Dated events listed near that date: " + (
                "; ".join(f"{e.get('title')} ({str(e.get('t'))[:10]})" for e in near[:3])
                if near else "none in this assembly") + ".")
    return _close(res, lines)
