"""The figures the export centre knows how to draw, each defined as a
ChartSpec built from the same stored data the datasets and the report read.

    list_charts(env)            catalogue: id, state, chip, dataset, spec
    build_spec(env, chart_id)   one normalised ChartSpec (or ChartError)

`env` is duck-typed: `exports.Env` (run / sim run / board) or the report's
`BundleSource` (a run only). Builders never fetch; they read the stored
bundle. Doctrine: per-place values stay in their own unit class (one
figure per class, never a merged one); backtest figures are BACKTEST and
carry the scorecard's own baseline; nothing here sums across classes."""
from __future__ import annotations

import re
from dataclasses import dataclass

from ports import rules as PORTS_RULES
from ports.sources import load_json

from .fmt import parse_iso, to_days
from .spec import normalise
from .theme import CHANNEL_SLOT

CHAIN_SRC = "stored run record (causal chain)"
IMPACT_SRC = "stored run record and impact layer (capacity x worst magnitude)"


class ChartError(Exception):
    def __init__(self, code, message, status=422):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


class BundleSource:
    """Adapter so reports can call the builders with only a RunBundle."""

    def __init__(self, bundle):
        self.bundle = bundle
        self.ctx = bundle.ctx
        self.run_id = bundle.run_id
        self.sim_run_id = None


def slug(unit: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(unit).lower()).strip("_")


def _depths(events):
    by = {e["seq"]: e for e in events}
    memo = {}

    def d(seq):
        if seq not in memo:
            p = by[seq]["parent_id"]
            memo[seq] = 0 if p is None or p not in by else d(p) + 1
        return memo[seq]
    return {e["seq"]: d(e["seq"]) for e in events}


def _run_start_days(b):
    j = b.journal
    s = to_days((j.get("disruption") or {}).get("started_at"))
    if s is None and b.events:
        s = min(to_days(e["at"]) for e in b.events)
    return s or 0.0


def _arrival_day(e, t0):
    """Day (after the run's start) the chain reaches the place. An event's
    `at` is start + the cumulative delay up to and including its own, so
    the arrival is `at` minus the event's own delay."""
    # `at` is stored to the second: round away that resolution (so the seed
    # reads exactly day 0) and never return -0.0
    return round(to_days(e["at"]) - e["delay_d"] - t0, 4) + 0.0


def _seed(b):
    return b.journal["disruption"]["node_id"]


# -------------------------------------------------------------- builders
def cascade_timeline(env, caps=None):
    b = env.bundle
    ev = b.events
    t0 = _run_start_days(b)
    index = {e["seq"]: i for i, e in enumerate(ev)}
    depth = _depths(ev)
    pts = []
    for e in ev:
        p = {"label": b.name(e["node_id"]),
             "start": _arrival_day(e, t0),
             "duration": e["delay_d"], "channel": e["channel"],
             "parent": index.get(e["parent_id"]) if e["parent_id"] is not None
             and index.get(e["parent_id"], 10 ** 9) < index[e["seq"]]
             else None,
             "depth": depth[e["seq"]], "magnitude": e["magnitude"]}
        pts.append(p)
    places = len({e["node_id"] for e in ev})
    cap = (caps or {}).get("timeline") or 120
    return {
        "type": "cascade", "chip": "MODELLED",
        "title": "Causal chain: when each place is hit and the delay it adds",
        "subtitle": f"{len(ev)} events across {places} places, in stream "
                    f"order; one row per event, coloured by channel",
        "series": [{"name": "Chain events", "source": CHAIN_SRC,
                    "points": pts}],
        "axes": {"x": {"label": "Days after the disruption starts",
                       "unit": "days"}},
        "options": {"max_rows": min(cap, 400, max(1, len(pts)))},
        "note": "MODELLED: a model of propagation, not a forecast."}


def _worst_by_place(b):
    seed = _seed(b)
    worst = {}
    for e in b.events:
        n = e["node_id"]
        if n == seed:
            continue
        w = worst.setdefault(n, [0.0, 0.0])
        w[0] = max(w[0], float(e["magnitude"]))
        w[1] = max(w[1], float(e["delay_d"]))
    return worst


def delay_by_place(env, caps=None):
    b = env.bundle
    worst = _worst_by_place(b)
    ranked = sorted(worst, key=lambda n: (-worst[n][1], n))
    pts = [{"x": b.name(n), "y": round(worst[n][1], 6)} for n in ranked]
    n_show = (caps or {}).get("delay_rows") or 25
    return {
        "type": "bar", "chip": "MODELLED",
        "title": "Longest modelled delay by place",
        "subtitle": "Places on the causal chain, longest delay first "
                    "(the seed place is the disruption itself and is not "
                    "ranked)",
        "series": [{"name": "Longest delay", "source": CHAIN_SRC,
                    "unit": "days", "points": pts}],
        "axes": {"y": {"label": "Longest delay at the place",
                       "unit": "days"}},
        "options": {"orientation": "h", "sort": "value_desc",
                    "max_rows": max(1, min(n_show, 400))}}


def leg_delay(env, caps=None):
    b = env.bundle
    by = {e["seq"]: e for e in b.events}
    pts = []
    for leg in b.edges:
        if leg["from_seq"] is None:
            continue                      # the seed injection, not a leg
        ev = by[leg["to_seq"]]
        pts.append({"from": b.name(leg["from_node"]),
                    "to": b.name(leg["to_node"]),
                    "value": ev["delay_d"], "channel": leg["channel"]})
    n_show = (caps or {}).get("leg_rows") or 20
    return {
        "type": "route_delay", "chip": "MODELLED",
        "title": "Delay added on each leg of the causal chain",
        "subtitle": "One bar per leg (from place to place), coloured by "
                    "channel, longest first",
        "series": [{"name": "Legs", "source": CHAIN_SRC, "unit": "days",
                    "points": pts}],
        "axes": {"y": {"label": "Delay added at the receiving place",
                       "unit": "days"}},
        "options": {"max_rows": max(1, min(n_show, 400))}}


def places_reached(env, caps=None):
    """Cumulative count of distinct places the chain has reached, by
    arrival day, with the disruption's start and end marked. A count of
    places (not of trade), one series, never a sum across classes."""
    b = env.bundle
    t0 = _run_start_days(b)
    d = b.journal["disruption"]
    seen, pts = set(), []
    for e in sorted(b.events, key=lambda e: (_arrival_day(e, t0), e["seq"])):
        if e["node_id"] in seen:
            continue
        seen.add(e["node_id"])
        pts.append({"x": _arrival_day(e, t0), "y": len(seen),
                    "label": b.name(e["node_id"])})
    ann = [{"kind": "marker", "x": 0, "label": "Disruption starts"}]
    if d.get("duration_d"):
        ann.append({"kind": "marker", "x": float(d["duration_d"]),
                    "label": "Disruption ends"})
    return {
        "type": "line", "chip": "MODELLED",
        "title": "Places reached by the causal chain over time",
        "subtitle": "Cumulative count of distinct places hit, by the day "
                    "the chain first reaches them",
        "series": [{"name": "Places reached", "source": CHAIN_SRC,
                    "unit": "places", "points": pts}],
        "axes": {"x": {"label": "Days after the disruption starts",
                       "unit": "days", "kind": "number"},
                 "y": {"label": "Places reached", "unit": "places",
                       "min": 0}},
        "annotations": ann}


def impact_charts(env, caps=None):
    """{chart_id: builder} - one figure per unit class, never merged."""
    b = env.bundle
    units = sorted({r["unit_class"] for r in b.place_rows}, key=str)
    return {f"impact_at_risk_{slug(u)}": (lambda e, c=None, u=u:
                                          impact_for_unit(e, u, c))
            for u in units}


def impact_for_unit(env, unit, caps=None):
    b = env.bundle
    rows = [r for r in b.place_rows if r["unit_class"] == unit]
    rows.sort(key=lambda r: (-(r["capacity_at_risk"]
                               if r["capacity_at_risk"] is not None else -1),
                             r["node_id"]))
    pts = [{"x": r["name"] or r["node_id"], "y": r["capacity_at_risk"]}
           for r in rows]
    n_show = (caps or {}).get("impact_rows") or 20
    return {
        "type": "bar", "chip": "MODELLED",
        "title": f"Capacity at risk by place, unit class {unit}",
        "subtitle": "Capacity x worst modelled magnitude, per place, in the "
                    "place's own unit; places without a published capacity "
                    "read NOT SERVED",
        "series": [{"name": "Capacity at risk", "source": IMPACT_SRC,
                    "unit": f"{unit} impaired", "points": pts}],
        "axes": {"y": {"label": "Capacity at risk",
                       "unit": f"{unit} impaired"}},
        "options": {"orientation": "h", "sort": "value_desc",
                    "max_rows": max(1, min(n_show, 400))},
        "note": "One figure per unit class; classes are never merged or "
                "summed."}


def _scorecard(env):
    sc = load_json(env.ctx.scorecard_path)
    return sc if isinstance(sc, dict) else None


def _short(t, n=34):
    t = str(t or "")
    return t if len(t) <= n else t[:n - 1].rstrip() + "..."


def _num(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _backtest(env, metric):
    """Counts only: places matched or not matched, never a rate or a rival."""
    sc = _scorecard(env)
    if sc is None:
        raise ChartError("not_served", "no scorecard artifact is present",
                         404)
    reps = sc.get("replays") or []
    src = f"backtest scorecard v{sc.get('version')} (stored artifact)"
    names = {"coverage": ("Matched", "Not matched"),
             "precision": ("Observed", "Not observed")}.get(metric)
    a_pts, b_pts = [], []
    tot_a = tot_b = 0
    for r in reps:
        lab = _short(r.get("title") or r.get("id"))
        if metric == "coverage":
            cov = r.get("coverage") or {}
            n_all = _num(cov.get("distinct_non_seed_nodes"))
            n_hit = _num(cov.get("covered_count"))
            y_a = n_hit
            y_b = (n_all - n_hit) if n_all is not None and n_hit is not None \
                else None
        elif metric == "precision":
            n_all = _num(r.get("predicted_non_seed_count"))
            n_off = _num((r.get("false_positives") or {}).get("count"))
            y_b = n_off
            y_a = (n_all - n_off) if n_all is not None and n_off is not None \
                else None
        else:
            a_pts.append({"x": lab, "y": (r.get("delay_accuracy") or {}).get(
                "mean_abs_error_d")})
            continue
        a_pts.append({"x": lab, "y": y_a})
        b_pts.append({"x": lab, "y": y_b})
        tot_a += y_a or 0
        tot_b += y_b or 0
    if metric != "delay_error" and reps:
        a_pts.append({"x": "All replays", "y": tot_a})
        b_pts.append({"x": "All replays", "y": tot_b})
    meta = {
        "coverage": ("Backtest, places observed",
                     "Places in the observed chain, matched and not matched "
                     "by the modelled chain", "Places", "places"),
        "precision": ("Backtest, modelled places",
                      "Places in the modelled chain, with and without an "
                      "observed effect", "Places", "places"),
        "delay_error": ("Backtest, delay difference per replay",
                        "Mean absolute difference between modelled and "
                        "observed delay on graded steps (days)",
                        "Mean absolute delay difference", "days"),
    }[metric]
    if metric == "delay_error":
        series = [{"name": "Delay difference", "source": src,
                   "unit": meta[3], "points": a_pts}]
    else:
        series = [{"name": names[0], "source": src, "unit": meta[3],
                   "points": a_pts},
                  {"name": names[1], "source": src, "unit": meta[3],
                   "points": b_pts}]
    note = ("BACKTEST: compared with documented past disruptions; descriptive "
            "of past replays, not live performance and no statement about "
            "future events.")
    ya = {"label": meta[2], "unit": meta[3]}
    return {"type": "bar", "chip": "BACKTEST", "title": meta[0],
            "subtitle": meta[1], "series": series, "axes": {"y": ya},
            "options": {"orientation": "v" if len(a_pts) <= 5 else "h"},
            "note": note}


def backtest_coverage(env, caps=None):
    return _backtest(env, "coverage")


def backtest_precision(env, caps=None):
    return _backtest(env, "precision")


def backtest_delay_error(env, caps=None):
    return _backtest(env, "delay_error")


def sim_queue(env, caps=None):
    from datetime import timedelta
    from ports.feeds import iso
    from ports.sources import TICK_SECONDS
    rec = env.sim_record()
    j = rec["journal"]
    start = parse_iso(j["start_at"])
    step = TICK_SECONDS[j["tick"]]
    series = rec.get("node_series") or {}
    peak = sorted(series, key=lambda n: (-max(series[n]["queue"] or [0]), n))
    out = []
    names = (lambda p: env.name(p)) if hasattr(env, "name") else (lambda p: p)
    for nid in peak[:12]:
        q = series[nid]["queue"]
        out.append({"name": names(nid), "unit": "simulated entities",
                    "source": f"stored simulation run {env.sim_run_id}",
                    "points": [{"x": iso(start + timedelta(seconds=step * t)),
                                "y": v} for t, v in enumerate(q)]})
    return {
        "type": "small_multiples", "chip": "SIMULATED",
        "title": "Simulated queue by place",
        "subtitle": f"The {len(out)} places with the largest simulated "
                    f"queue, one panel each",
        "series": out,
        "axes": {"x": {"label": "Simulated time (UTC)", "kind": "time"},
                 "y": {"unit": "simulated entities"}},
        "options": {"columns": 3, "panel_type": "line"},
        "note": "SIMULATED: values from a stored simulation, not "
                "observations."}


def ports_status(env, caps=None):
    res = env.board.list_rows(sort="id", order="asc", limit=10 ** 9,
                              offset=0)
    counts = {}
    for r in res["places"]:
        counts[r["status"]["label"]] = counts.get(r["status"]["label"], 0) + 1
    order = tuple(reversed([l["label"] for l in PORTS_RULES.RULE["levels"]])) + (PORTS_RULES.RULE["no_data_label"],)
    pts = [{"x": k, "y": counts.get(k, 0)} for k in order]
    return {
        "type": "bar", "chip": "MODELLED",
        "title": "Places on the ports board by status",
        "subtitle": "A count of places at the snapshot instant, by the "
                    "published PORTS-STATUS rule",
        "series": [{"name": "Places", "source": "ports board snapshot "
                    "(rule PORTS-STATUS 1.1.0)", "unit": "places",
                    "points": pts}],
        "axes": {"y": {"label": "Places", "unit": "places"}},
        "options": {"orientation": "v"},
        "note": "A low status flags a place for review; it is not a closure "
                "claim and not a measure of trade."}


# --------------------------------------------------------------- registry
@dataclass(frozen=True)
class ChartDef:
    id: str
    title: str
    type: str
    dataset: str | None
    needs: str | None          # "run" | "sim" | None
    chip: str
    build: object
    what: str


STATIC = [
    ChartDef("cascade_timeline", "Causal chain cascade timeline", "cascade",
             "chain_events", "run", "MODELLED", cascade_timeline,
             "Each event of the chain on a day axis, coloured by channel."),
    ChartDef("places_reached", "Places reached by the causal chain over time",
             "line", "chain_events", "run", "MODELLED", places_reached,
             "Cumulative places reached by arrival day, with the "
             "disruption start and end marked."),
    ChartDef("delay_by_place", "Longest modelled delay by place", "bar",
             "chain_events", "run", "MODELLED", delay_by_place,
             "Per-place longest delay, ranked."),
    ChartDef("leg_delay", "Delay added on each leg", "route_delay", "legs",
             "run", "MODELLED", leg_delay,
             "Delay added at the receiving place of each leg."),
    ChartDef("impact_at_risk", "Capacity at risk by place (per unit class)",
             "bar", "impact_per_place", "run", "MODELLED", None,
             "One bar chart per unit class; never merged."),
    ChartDef("backtest_coverage", "Backtest, places observed",
             "bar", "scorecards", None, "BACKTEST", backtest_coverage,
             "Observed places matched and not matched by the modelled chain."),
    ChartDef("backtest_precision", "Backtest, modelled places",
             "bar", "scorecards", None, "BACKTEST", backtest_precision,
             "Modelled places with and without an observed effect."),
    ChartDef("backtest_delay_error", "Backtest, delay difference", "bar",
             "scorecards", None, "BACKTEST", backtest_delay_error,
             "Mean absolute delay difference per replay."),
    ChartDef("sim_queue", "Simulated queue by place", "small_multiples",
             "sim_series", "sim", "SIMULATED", sim_queue,
             "Small multiples of the simulated queue."),
    ChartDef("ports_status", "Places by status", "bar", "ports_board", None,
             "MODELLED", ports_status,
             "Counts of places per published status level."),
]
BY_ID = {d.id: d for d in STATIC}


def resolve(env, chart_id):
    """-> (ChartDef-like, builder) or raise ChartError 404."""
    if chart_id in BY_ID and chart_id != "impact_at_risk":
        d = BY_ID[chart_id]
        return d, d.build
    if chart_id.startswith("impact_at_risk_"):
        if getattr(env, "run_id", None) is None:
            raise ChartError("needs_run", f"chart {chart_id!r} needs run_id")
        b = impact_charts(env)
        if chart_id in b:
            d = BY_ID["impact_at_risk"]
            return d, b[chart_id]
        raise ChartError("chart_not_found",
                         f"this run has no unit class for {chart_id!r}", 404)
    raise ChartError("chart_not_found", f"unknown chart {chart_id!r}", 404)


def gate(d, env):
    if d.needs == "run" and getattr(env, "run_id", None) is None:
        return "NEEDS RUN"
    if d.needs == "sim" and getattr(env, "sim_run_id", None) is None:
        return "NEEDS SIM RUN"
    return None


def build_spec(env, chart_id, caps=None) -> dict:
    d, fn = resolve(env, chart_id)
    g = gate(d, env)
    if g:
        raise ChartError("invalid_request",
                         f"chart {chart_id!r} needs "
                         f"{'run_id' if g == 'NEEDS RUN' else 'sim_run_id'}")
    raw = fn(env, caps)
    return normalise(raw)


def list_charts(env, with_spec=True, dataset=None, caps=None,
                datasets=None):
    """Catalogue rows. A chart that cannot be drawn says why (state) and
    carries no spec; absence is stated, never faked."""
    rows = []
    ids = []
    for d in STATIC:
        if d.id == "impact_at_risk":
            if getattr(env, "run_id", None) is None:
                ids.append((d, "impact_at_risk", "NEEDS RUN"))
            else:
                for cid in impact_charts(env):
                    ids.append((d, cid, None))
            continue
        ids.append((d, d.id, gate(d, env)))
    for d, cid, g in ids:
        if dataset and d.dataset != dataset:
            continue
        if datasets is not None and d.dataset not in datasets:
            continue
        row = {"id": cid, "title": d.title, "type": d.type,
               "dataset": d.dataset, "chip": d.chip, "what": d.what,
               "needs": d.needs}
        spec, state, reason = None, g or "HAS DATA", None
        if not g:
            try:
                spec = build_spec(env, cid, caps)
                row["title"] = spec["title"]
                pts = sum(len(s["points"]) for s in spec["series"])
                if pts == 0:
                    state, reason = "NO DATA", "no rows to draw"
                    spec = None
            except ChartError as exc:
                state, reason = "NO DATA", exc.message
                spec = None
        row.update({"state": state, "reason": reason,
                    "spec": spec if with_spec else None,
                    "svg": f"/exports/chart/{cid}.svg",
                    "json": f"/exports/chart/{cid}.json",
                    "csv": f"/exports/chart/{cid}.csv",
                    "png": f"/exports/chart/{cid}.png"})
        rows.append(row)
    return rows
