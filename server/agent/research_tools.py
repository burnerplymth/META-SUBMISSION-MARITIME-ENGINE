"""Agent tools for research over historical data.

Read tools (free, idempotent): research_catalog, research_episodes,
research_assemble (cached, no spend), research_analyze (one analysis kind per
call), research_export_link. Action tools (never run on the model's say-so):
research_scene_scan (can spend SAM calls; asks first, shows the estimated cost
and the calls left) and write_research_note (asks first on a live model, the
one place this feature spends model tokens). Client action: open_research
(opens the Research window prefilled; view only).

How a result reaches the model:
  1. the tool calls the research route in process (``ctx.api``);
  2. the response is cut down to a digest: the numbers and labels a person
     would quote, never series dumps or chart points;
  3. the digest is recorded as an evidence entry (E1, E2 ...; see
     ``research_evidence.py``) and the entry id is added to what the model
     sees as ``evidence_id``.
The model cites evidence ids; ``research_verify.py`` checks every number it
writes against the entries it cites. Statistics are computed by the research
routes (pure functions, seeded); the model computes nothing.

A tool that takes an ``assembly_id`` accepts only ids this session produced,
and series keys and event ids only as that assembly listed them, so a made-up
id never reaches the API.
"""
from __future__ import annotations

import re
from datetime import date

from .research_evidence import EvidenceError, ResearchState
from .tools import PLACE_ID, Tool, ToolContext, ToolError, _call, _obj, register_tools, require_place

DATE = {"type": "string", "pattern": r"\d{4}-\d{2}-\d{2}", "maxLength": 10}
ASSEMBLY_ID = {"type": "string", "pattern": r"asm_[0-9a-f]{16}", "maxLength": 20}
DATASET_ID = {"type": "string", "pattern": r"[a-z0-9_:.\-]{1,64}", "maxLength": 64}
SERIES_KEY = {"type": "string", "pattern": r"[a-z0-9_:.\-]{1,100}", "maxLength": 100}
EVENT_ID = {"type": "string", "pattern": r"[a-z0-9_:.\-]{1,64}", "maxLength": 64}
CATEGORIES = ["weather", "congestion", "labour_action", "accident_blockage", "canal_restriction",
              "infrastructure", "economic_release"]
KINDS = ["event_study", "before_after", "lagged_correlation", "change_points", "distribution",
         "seasonality", "model_overlay"]
MAX_SPAN_DAYS = 1826
SAM_PRICE_PER_CALL = 0.0025
MAX_SCANS_PER_SESSION = 2
MAX_LIVE_SCENES = 3                  # the same limit the route enforces (research/scenes.py)
RESEARCH_TOOL_NAMES = ("research_catalog", "research_episodes", "research_assemble",
                       "research_analyze", "research_export_link", "research_scene_scan",
                       "write_research_note", "open_research")

DROP_KEYS = frozenset({"path", "mean_path", "observed", "simulated", "histogram", "by_lag",
                       "acf", "chart_spec", "edges", "counts"})
LIST_CAP = 12


# ------------------------------------------------------------------ helpers
def _state(ctx: ToolContext) -> ResearchState:
    st = getattr(ctx, "research", None)
    if st is None:                       # a tool run outside a session
        st = ResearchState()
        ctx.research = st
    return st


def _rnd(x):
    return round(x, 6) if isinstance(x, float) else x


def _prune(o, depth: int = 0):
    if depth > 7:
        return None
    if isinstance(o, dict):
        return {k: _prune(v, depth + 1) for k, v in o.items() if k not in DROP_KEYS}
    if isinstance(o, list):
        return [_prune(v, depth + 1) for v in o[:LIST_CAP]]
    return _rnd(o)


def _api_get(ctx, path, params=None):
    res = ctx.api.get(path, params)
    if res.status == 404 and res.body is None:
        raise ToolError("research_unavailable", "the research service is not mounted")
    return _call(res)


def _api_post(ctx, path, body):
    res = ctx.api.post(path, body)
    if res.status == 404 and res.body is None:
        raise ToolError("research_unavailable", "the research service is not mounted")
    return _call(res)


def _record(ctx, **kw) -> dict:
    try:
        return _state(ctx).evidence.add(**kw)
    except EvidenceError as e:
        raise ToolError(e.code, e.message) from None


def _parse_date(s: str, field: str) -> date:
    try:
        return date.fromisoformat(s)
    except ValueError:
        raise ToolError("invalid_date", f"{field} {s!r} is not a calendar date (YYYY-MM-DD)") from None


def _is_obs(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


# ------------------------------------------------------------------ catalog
def _research_catalog(ctx, a):
    body = _api_get(ctx, "/research/catalog")
    rows, meta = [], {}
    for d in body.get("datasets", []):
        cov = d.get("coverage") or {}
        places = d.get("places")
        rows.append({
            "id": d.get("id"), "title": d.get("title"), "kind": d.get("kind"),
            "unit": d.get("unit"), "resolution": d.get("resolution"),
            "coverage_from": cov.get("from"), "coverage_to": cov.get("to"),
            "source": d.get("source"), "state": d.get("state"),
            "places": places if places == "any" else (places or [])[:6],
            "place_count": None if places == "any" else len(places or [])})
        meta[d.get("id")] = {"title": d.get("title"), "source": d.get("source"),
                             "licence": d.get("licence"), "state": d.get("state"),
                             "kind": d.get("kind"), "unit": d.get("unit"),
                             "coverage": [cov.get("from"), cov.get("to")]}
    cached = sum(1 for r in rows if r["state"] == "cached")
    digest = {"label": body.get("label"), "dataset_count": len(rows), "cached_count": cached,
              "datasets": rows[:24]}
    ev = _record(ctx, tool="research_catalog", kind="catalog", args=a, digest=digest,
                 n=len(rows), label=body.get("label"),
                 meta={"datasets": meta},
                 summary=f"{len(rows)} datasets, {cached} cached")
    return {"evidence_id": ev["id"], **digest}


def _research_episodes(ctx, a):
    body = _api_get(ctx, "/research/episodes")
    cat = a.get("category")
    rows = []
    for e in body.get("episodes", []):
        if cat and cat not in (e.get("categories") or []):
            continue
        win = e.get("window") or {}
        rows.append({
            "id": e.get("id"), "title": e.get("title"), "places": (e.get("places") or [])[:5],
            "window_from": win.get("from"), "window_to": win.get("to"),
            "categories": e.get("categories"), "series": (e.get("series") or [])[:10],
            "scenes": e.get("scenes"), "has_tracks": bool(e.get("has_tracks")),
            "sim_runs": (e.get("sim_runs") or [])[:4], "event_count": len(e.get("events") or []),
            "events": [{"id": v.get("id"), "t": v.get("t"), "title": v.get("title"),
                        "category": v.get("category"), "place": v.get("place")}
                       for v in (e.get("events") or [])[:6]]})
    digest = {"label": body.get("label"), "episode_count": len(rows), "episodes": rows[:12]}
    ev = _record(ctx, tool="research_episodes", kind="episodes", args=a, digest=digest,
                 n=len(rows), label=body.get("label"),
                 meta={"episode_ids": [r["id"] for r in rows]},
                 summary=f"{len(rows)} episodes")
    return {"evidence_id": ev["id"], **digest}


# ----------------------------------------------------------------- assemble
def _check_assemble(ctx, a):
    for pid in a["places"]:
        require_place(ctx, pid)
    lo, hi = _parse_date(a["from"], "from"), _parse_date(a["to"], "to")
    if hi < lo:
        raise ToolError("invalid_window", "to must be on or after from")
    if (hi - lo).days + 1 > MAX_SPAN_DAYS:
        raise ToolError("window_too_long", f"the window may span at most {MAX_SPAN_DAYS} days")
    if len(set(a["places"])) != len(a["places"]):
        raise ToolError("duplicate_places", "list each place once")


def _research_assemble(ctx, a):
    req = {"places": a["places"], "from": a["from"], "to": a["to"],
           "series": a.get("series", [])}
    if a.get("event_categories"):
        req["events"] = {"categories": a["event_categories"],
                         "source": a.get("event_source", "episodes")}
    if a.get("scenes"):
        req["scenes"] = {"enabled": True,
                         "sources": a.get("scene_sources", ["sentinel-2", "sentinel-1"]),
                         "max_scenes": a.get("max_scenes", 20)}
    body = _api_post(ctx, "/research/assemble", req)
    tl = body.get("timeline") or {}
    t = tl.get("t") or []
    series_rows, series_meta = [], {}
    for key, s in (tl.get("series") or {}).items():
        vals = s.get("values") or []
        obs = [(t[i] if i < len(t) else None, v) for i, v in enumerate(vals) if _is_obs(v)]
        row = {"key": key, "dataset": s.get("dataset"), "place": s.get("place"),
               "unit": s.get("unit"), "state": s.get("state"), "source": s.get("source"),
               "n_days": len(t), "n_obs": len(obs)}
        if obs:
            nums = [v for _, v in obs]
            row.update(first_obs=obs[0][0], last_obs=obs[-1][0],
                       mean=_rnd(sum(nums) / len(nums)), min=_rnd(min(nums)), max=_rnd(max(nums)))
        series_rows.append(row)
        series_meta[key] = {"dataset": s.get("dataset"), "place": s.get("place"),
                            "unit": s.get("unit"), "state": s.get("state"),
                            "source": s.get("source"),
                            "mode": (s.get("provenance") or {}).get("mode"),
                            "first_obs": row.get("first_obs"), "last_obs": row.get("last_obs")}
    events = body.get("events") or []
    scenes = body.get("scenes") or []
    by_source: dict[str, int] = {}
    for sc in scenes:
        if sc.get("usable"):
            by_source[sc.get("source")] = by_source.get(sc.get("source"), 0) + 1
    win = body.get("window") or {}
    digest = {
        "assembly_id": body.get("assembly_id"), "label": body.get("label"),
        "window": {"from": win.get("from"), "to": win.get("to"), "days": win.get("days")},
        "places": [{"id": p.get("id"), "name": p.get("name")} for p in body.get("places", [])],
        "series_count": len(series_rows), "series": series_rows[:16],
        "event_count": len(events),
        "events": [{"id": e.get("id"), "t": e.get("t"), "title": e.get("title"),
                    "category": e.get("category"), "place": e.get("place")} for e in events[:12]],
        "scenes": {"listed": len(scenes), "usable": sum(1 for s in scenes if s.get("usable")),
                   "not_usable": sum(1 for s in scenes if not s.get("usable")),
                   "usable_by_source": by_source,
                   "with_recorded_sam": sum(1 for s in scenes if s.get("sam"))},
        "coverage_notes": [{"chip": c.get("chip"), "dataset": c.get("dataset"),
                            "place": c.get("place")} for c in (body.get("coverage_notes") or [])[:8]],
    }
    ev = _record(ctx, tool="research_assemble", kind="assemble", args=a, digest=digest,
                 analysis_id=body.get("assembly_id"), n=win.get("days"), label=body.get("label"),
                 meta={"series": series_meta, "series_keys": sorted(series_meta),
                       "event_ids": [e.get("id") for e in events],
                       "event_times": {e.get("id"): e.get("t") for e in events},
                       "places": [p.get("id") for p in body.get("places", [])],
                       "scene_ids": [s.get("scene_id") for s in scenes][:80]},
                 summary=f"{body.get('assembly_id')}: {len(series_rows)} series, "
                         f"{win.get('days')} days, {len(events)} events, {len(scenes)} scenes")
    return {"evidence_id": ev["id"], **digest}


# ------------------------------------------------------------------ analyze
def _need(a, *keys):
    for k in keys:
        if k not in a:
            raise ToolError("missing_argument", f"{a['kind']} needs {k}")


def _check_analyze(ctx, a):
    asm = _state(ctx).evidence.assembly(a["assembly_id"])
    if asm is None:
        raise ToolError("unknown_assembly",
                        "assembly_id was not produced in this session; call research_assemble first")
    keys, kind = asm["meta"].get("series_keys", []), a["kind"]
    if kind == "lagged_correlation":
        _need(a, "x", "y")
        used = [a["x"], a["y"]]
    elif kind == "model_overlay":
        _need(a, "run_or_scenario", "series")
        used = [a["series"]]
    else:
        _need(a, "series")
        used = [a["series"]]
    for k in used:
        if k not in keys:
            raise ToolError("unknown_series", f"{k!r} is not a series of this assembly; "
                                              f"available: {keys[:12]}")
    ev_ids = asm["meta"].get("event_ids", [])
    if kind == "event_study":
        if not (a.get("event_ids") or a.get("category")):
            raise ToolError("missing_argument", "event_study needs event_ids or category")
        if a.get("event_ids") and a.get("category"):
            raise ToolError("invalid_arguments", "give event_ids or category, not both")
    if kind == "before_after":
        _need(a, "split")
        if a["split"] == "event":
            _need(a, "event_id")
        elif not re.fullmatch(r"\d{4}-\d{2}-\d{2}", a["split"]):
            raise ToolError("invalid_arguments", 'split must be "event" or a date YYYY-MM-DD')
        else:
            _parse_date(a["split"], "split")
    for e in (a.get("event_ids") or []) + ([a["event_id"]] if a.get("event_id") else []):
        if e not in ev_ids:
            raise ToolError("unknown_event", f"{e!r} is not an event of this assembly; "
                                             f"available: {ev_ids[:12]}")
    w = a.get("window_days")
    if w is not None and not (-120 <= w[0] < w[1] <= 365):
        raise ToolError("invalid_arguments", "window_days must be [from, to] within -120 and 365, from < to")


def _spec(a: dict) -> dict:
    spec: dict = {"kind": a["kind"]}
    for k in ("series", "x", "y", "event_ids", "category", "event_id", "split", "window_days",
              "max_lag_days", "method", "min_size", "by", "run_or_scenario"):
        if k in a:
            spec[k] = a[k]
    if a["kind"] == "lagged_correlation":
        spec.setdefault("method", "spearman")
    return spec


def _digest_estimate(kind: str, est: dict) -> dict:
    est = est or {}
    out = _prune({k: v for k, v in est.items() if k != "per_event"})
    if kind == "event_study":
        out["per_event"] = [{"event_id": p.get("event_id"), "t0": p.get("t0"),
                             "n_obs": p.get("n_obs"), "baseline_mean": _rnd(p.get("baseline_mean"))}
                            for p in (est.get("per_event") or [])[:6]]
    elif kind == "lagged_correlation":
        zero = next((r for r in est.get("by_lag") or [] if r.get("lag") == 0), None)
        if zero:
            out["lag_zero"] = _prune(zero)
    elif kind == "seasonality":
        wk = [w for w in est.get("weekday") or [] if _is_obs(w.get("index"))]
        if wk:
            lo, hi = min(wk, key=lambda w: w["index"]), max(wk, key=lambda w: w["index"])
            out["weekday_highlights"] = {"index_low": {"dow": lo.get("dow"), "index": _rnd(lo["index"])},
                                         "index_high": {"dow": hi.get("dow"), "index": _rnd(hi["index"])}}
    elif kind == "distribution":
        out["groups"] = [_prune({k: v for k, v in g.items() if k != "histogram"})
                         for g in (est.get("groups") or [])[:8]]
    elif kind == "change_points":
        out["point_count"] = len(est.get("points") or [])
    return out


def _research_analyze(ctx, a):
    asm = _state(ctx).evidence.assembly(a["assembly_id"])
    body = _api_post(ctx, "/research/analyze",
                     {"assembly_id": a["assembly_id"], "analyses": [_spec(a)]})
    results = body.get("results") or []
    if not results:
        raise ToolError("bad_response", "the research service returned no result")
    r = results[0]
    if isinstance(r.get("error"), dict):
        raise ToolError(str(r["error"].get("code", "analysis_failed")),
                        str(r["error"].get("message", ""))[:300])
    kind = r.get("kind") or a["kind"]
    digest = {"kind": kind, "analysis_id": r.get("id"), "label": r.get("label"),
              "inputs": _prune(r.get("inputs") or {}), "n": r.get("n"), "n_unit": r.get("n_unit"),
              "estimate": _digest_estimate(kind, r.get("estimate") or {}),
              "ci": _prune(r.get("ci")), "caveat_chips": r.get("caveat_chips") or [],
              "figure_available": bool(r.get("chart_spec"))}
    ev = _record(ctx, tool="research_analyze", kind=kind, args=a, digest=digest,
                 analysis_id=r.get("id"), n=r.get("n"), label=r.get("label"),
                 caveat_chips=r.get("caveat_chips"), figure=r.get("chart_spec"),
                 meta={"n_unit": r.get("n_unit"), "assembly_id": a["assembly_id"],
                       "series_keys": (asm or {}).get("meta", {}).get("series_keys", [])},
                 summary=f"{kind} on {a.get('series') or a.get('x')}: n={r.get('n')} {r.get('n_unit') or ''}".strip())
    return {"evidence_id": ev["id"], **digest}


# ------------------------------------------------------------------- export
def _check_export(ctx, a):
    if _state(ctx).evidence.assembly(a["assembly_id"]) is None:
        raise ToolError("unknown_assembly", "assembly_id was not produced in this session")


def _research_export_link(ctx, a):
    fmt = a.get("format", "csv")
    aid = a["assembly_id"]
    return {"assembly_id": aid, "format": fmt,
            "path": f"/research/export?assembly_id={aid}&format={fmt}",
            "filename": f"research_{aid}.{fmt}",
            "note": "data tables with a manifest and the label; open the link to download"}


# --------------------------------------------------------------- scene scan
def _scan_body(a: dict, ctx_live: bool = False) -> dict:
    body: dict = {"place": a["place"], "detector": a.get("detector", "sam"),
                  "mode": a.get("mode", "recorded")}
    if a.get("scene_ids"):
        body["scene_ids"] = a["scene_ids"]
    else:
        body["window"] = {"from": a["from"], "to": a["to"]}
    return body


def _scan_calls(a: dict) -> int | None:
    return len(a["scene_ids"]) if a.get("scene_ids") else None


def _check_scan(ctx, a):
    require_place(ctx, a["place"])
    if not a.get("scene_ids"):
        if "from" not in a or "to" not in a:
            raise ToolError("missing_argument", "give scene_ids or both from and to")
        lo, hi = _parse_date(a["from"], "from"), _parse_date(a["to"], "to")
        if hi < lo or (hi - lo).days > 120:
            raise ToolError("invalid_window", "the scene window must be at most 120 days")
    elif "from" in a or "to" in a:
        raise ToolError("invalid_arguments", "give scene_ids or a window, not both")
    st = _state(ctx)
    if a.get("mode") == "live":
        if not ctx.live:
            raise ToolError("live_unavailable", "a live scan needs a live agent session")
        if not a.get("scene_ids"):
            raise ToolError("scene_ids_required", "a live scan names its scenes (scene_ids)")
        if len(a["scene_ids"]) > MAX_LIVE_SCENES:
            raise ToolError("too_many_scenes", f"a live scan covers at most {MAX_LIVE_SCENES} scenes")
    if sum(1 for e in st.evidence.items if e["kind"] == "scene_scan") >= MAX_SCANS_PER_SESSION:
        raise ToolError("scan_cap", f"at most {MAX_SCANS_PER_SESSION} scene scans per session")


def _sam_usage(ctx) -> dict:
    try:
        res = ctx.api.get("/satwatch/sam/usage")
        if res.ok and isinstance(res.body, dict):
            return res.body
    except Exception:
        pass
    return {}


def _scan_estimate(ctx, a) -> float:
    if a.get("mode") != "live":
        return 0.0
    return round((_scan_calls(a) or 0) * SAM_PRICE_PER_CALL, 6)


def _scan_explain(ctx, a) -> str:
    use = _sam_usage(ctx)
    left = use.get("remaining_calls_today")
    cap = use.get("cap_calls_per_day")
    where = (f"{len(a['scene_ids'])} scenes" if a.get("scene_ids")
             else f"scenes from {a.get('from')} to {a.get('to')}")
    detector = "SAM 3.1" if a.get("detector", "sam") == "sam" else "S1 CFAR"
    if a.get("mode") == "live":
        left_txt = f"; {left} of {cap} calls left today" if left is not None else ""
        return (f"Scan {where} at {a['place']} with {detector}, live: "
                f"estimated cost USD {_scan_estimate(ctx, a):.4f} for {_scan_calls(a)} calls{left_txt}")
    left_txt = f"; {left} of {cap} live calls left today" if left is not None else ""
    return f"Scan {where} at {a['place']} with {detector}, recorded replies, no cost{left_txt}"


def _research_scene_scan(ctx, a):
    body = _scan_body(a)
    if a.get("mode") == "live":
        body["confirm_cost_usd"] = _scan_estimate(ctx, a)
    r = _api_post(ctx, "/research/scenes/scan", body)
    results = r.get("results") or []
    digest = {
        "place": r.get("place"), "detector": r.get("detector"), "mode": r.get("mode"),
        "label": r.get("label"), "verification": r.get("verification") or "Detected, not verified",
        "human_reviewed": False, "scene_count": len(results),
        "found_total": sum(x.get("found") or 0 for x in results if x.get("usable", True)),
        "results": [{"scene_id": x.get("scene_id"), "t": x.get("t"), "source": x.get("source"),
                     "usable": x.get("usable"), "found": x.get("found"), "prompt": x.get("prompt"),
                     "text": x.get("text")} for x in results[:12]],
        "not_counted": [{"scene_id": x.get("scene_id"), "reason": x.get("reason")}
                        for x in (r.get("not_counted") or [])[:8]],
        "not_counted_count": len(r.get("not_counted") or []),
        "cost": _prune(r.get("cost") or {})}
    chips = sorted({c for x in results for c in (x.get("chips") or [])})
    ev = _record(ctx, tool="research_scene_scan", kind="scene_scan", args=a, digest=digest,
                 n=len(results), label=str(r.get("label") or "MODELLED"), caveat_chips=chips,
                 meta={}, summary=f"{len(results)} scenes scanned ({r.get('mode')}), "
                                  f"{digest['not_counted_count']} not counted")
    cost = (r.get("cost") or {}).get("est_usd")
    return {"evidence_id": ev["id"], **digest,
            "sam_cost_usd": cost if isinstance(cost, (int, float)) else 0.0}


# -------------------------------------------------------------- the note
def _check_note(ctx, a):
    st = _state(ctx)
    if not any(e["kind"] not in ("catalog", "episodes") for e in st.evidence.items):
        raise ToolError("no_evidence", "run research_assemble and research_analyze first")
    if getattr(ctx, "provider", None) is None:
        raise ToolError("no_provider", "no model provider is bound to this session")


def _write_research_note(ctx, a):
    from . import research_note
    return research_note.write_for_tool(_state(ctx), ctx.provider, ctx)


# ---------------------------------------------------------- client action
def _check_open_research(ctx, a):
    for pid in a.get("places", []):
        require_place(ctx, pid)
    if "from" in a or "to" in a:
        if "from" not in a or "to" not in a:
            raise ToolError("missing_argument", "give both from and to")
        if _parse_date(a["to"], "to") < _parse_date(a["from"], "from"):
            raise ToolError("invalid_window", "to must be on or after from")


def _client_only(ctx, a):  # pragma: no cover - never called by the loop
    raise ToolError("client_only", "this tool runs in the user's browser")


# --------------------------------------------------------------- registry
def _d_assemble(a):
    return f"Assemble {', '.join(a.get('series') or ['events'])} for {len(a.get('places', []))} place(s)"


def tool_list() -> list[Tool]:
    return [
        Tool("research_catalog",
             "List historical datasets (id, source, coverage, state cached or not_served). Read first to see what exists.",
             _obj({}), "read", _research_catalog),
        Tool("research_episodes",
             "List curated historical episodes (blockages, congestion, weather, strikes) with dated events and the series held for each.",
             _obj({"category": {"type": "string", "enum": CATEGORIES}}), "read", _research_episodes),
        Tool("research_assemble",
             "Assemble historical series, events and satellite scene listings for 1 to 5 places and a date window (cached, no spend). Returns an assembly_id for research_analyze.",
             _obj({"places": {"type": "array", "items": PLACE_ID, "minItems": 1, "maxItems": 5},
                   "from": DATE, "to": DATE,
                   "series": {"type": "array", "items": DATASET_ID, "maxItems": 8},
                   "event_categories": {"type": "array", "items": {"type": "string", "enum": CATEGORIES},
                                        "maxItems": 4},
                   "event_source": {"type": "string", "enum": ["episodes", "gdelt", "both"]},
                   "scenes": {"type": "boolean"},
                   "scene_sources": {"type": "array", "items": {"type": "string",
                                                                "enum": ["sentinel-2", "sentinel-1"]},
                                     "maxItems": 2},
                   "max_scenes": {"type": "integer", "minimum": 1, "maximum": 40}},
                  ["places", "from", "to"]),
             "read", _research_assemble, check=_check_assemble, describe=_d_assemble),
        Tool("research_analyze",
             "Run ONE descriptive analysis on an assembly made in this session: event_study, before_after, lagged_correlation, change_points, distribution, seasonality, model_overlay. Series are keys like portwatch_transits:chk_suez. Associations, not causes.",
             _obj({"assembly_id": ASSEMBLY_ID, "kind": {"type": "string", "enum": KINDS},
                   "series": SERIES_KEY, "x": SERIES_KEY, "y": SERIES_KEY,
                   "event_ids": {"type": "array", "items": EVENT_ID, "minItems": 1, "maxItems": 10},
                   "category": {"type": "string", "enum": CATEGORIES},
                   "event_id": EVENT_ID,
                   "split": {"type": "string", "pattern": r"event|\d{4}-\d{2}-\d{2}", "maxLength": 10},
                   "window_days": {"type": "array", "items": {"type": "integer", "minimum": -120,
                                                              "maximum": 365},
                                   "minItems": 2, "maxItems": 2},
                   "max_lag_days": {"type": "integer", "minimum": 1, "maximum": 30},
                   "method": {"type": "string", "enum": ["cusum", "pelt-lite"]},
                   "min_size": {"type": "integer", "minimum": 3, "maximum": 60},
                   "by": {"type": "string", "enum": ["month", "event", "none"]},
                   "run_or_scenario": {"type": "string", "pattern": r"[A-Za-z0-9_:.\-]{1,64}",
                                       "maxLength": 64}},
                  ["assembly_id", "kind"]),
             "read", _research_analyze, check=_check_analyze,
             describe=lambda a: f"Run {a.get('kind')} on {a.get('series') or a.get('x')}"),
        Tool("research_export_link",
             "Give the download link for an assembly's data tables (csv, xlsx or json) with manifest and label. Returns a link only.",
             _obj({"assembly_id": ASSEMBLY_ID,
                   "format": {"type": "string", "enum": ["csv", "xlsx", "json"]}}, ["assembly_id"]),
             "read", _research_export_link, check=_check_export),
        Tool("research_scene_scan",
             "ACTION (asks the user first). Run the satellite detector (SAM 3.1 or S1 CFAR) on listed scenes of one place: recorded replies are free, a live scan spends SAM calls. Counts are modelled detector output, not vessel counts.",
             _obj({"place": PLACE_ID,
                   "scene_ids": {"type": "array", "items": {"type": "string", "pattern": r"[A-Za-z0-9_.\-]{1,80}",
                                                            "maxLength": 80}, "minItems": 1, "maxItems": 40},
                   "from": DATE, "to": DATE,
                   "detector": {"type": "string", "enum": ["sam", "s1_cfar"]},
                   "mode": {"type": "string", "enum": ["recorded", "live"]}}, ["place"]),
             "action", _research_scene_scan, check=_check_scan, confirm="always",
             describe=lambda a: f"Scan scenes at {a.get('place')} ({a.get('mode', 'recorded')})",
             estimate=_scan_estimate, explain=_scan_explain),
        Tool("write_research_note",
             "Write the research note from the evidence list: findings with evidence ids, limitations and methods. Numbers are checked by code against the evidence. Free on the recorded transport; on a live model it costs money, so it asks the user first. Call once, last.",
             _obj({}), "read", _write_research_note, check=_check_note, confirm="if_live",
             est_cost_usd=0.02,
             describe=lambda a: "Write the research note with Meta AI (live model call)"),
        Tool("open_research",
             "UI CONTROL (runs in the user's browser, no confirmation). Open the Research window, prefilled with places, a date window or an assembly.",
             _obj({"places": {"type": "array", "items": PLACE_ID, "maxItems": 5},
                   "from": DATE, "to": DATE,
                   "series": {"type": "array", "items": DATASET_ID, "maxItems": 8},
                   "episode_id": EVENT_ID, "assembly_id": ASSEMBLY_ID}),
             "client_action", _client_only, check=_check_open_research,
             describe=lambda a: "Open Research"),
    ]


# ------------------------------------------------------ activity-log lines
def summarize(name: str, result: dict) -> str | None:
    try:
        if name == "research_catalog":
            return f"{result['dataset_count']} datasets, {result['cached_count']} cached ({result['evidence_id']})"
        if name == "research_episodes":
            return f"{result['episode_count']} episodes ({result['evidence_id']})"
        if name == "research_assemble":
            w = result["window"]
            return (f"{result['assembly_id']}: {result['series_count']} series, {w['days']} days, "
                    f"{result['event_count']} events, {result['scenes']['listed']} scenes ({result['evidence_id']})")
        if name == "research_analyze":
            return f"{result['kind']}: n = {result['n']} {result.get('n_unit') or ''} ({result['evidence_id']})".replace("  ", " ")
        if name == "research_export_link":
            return f"export link, {result['format']}"
        if name == "research_scene_scan":
            return (f"{result['scene_count']} scenes ({result['mode']}), "
                    f"{result['not_counted_count']} not counted ({result['evidence_id']})")
        if name == "write_research_note":
            return (f"note {result['note_id']}: verified {result['verified']} of {result['proposed']} findings"
                    + (" (cached)" if result.get("cached") else ""))
    except (KeyError, TypeError):
        pass
    return None


register_tools(tool_list())
