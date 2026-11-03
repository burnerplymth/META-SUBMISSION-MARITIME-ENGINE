"""Report section registry.

Each section builder returns a plain structure:
  {id, title, chip, locked, blocks: [...]}
with blocks of type
  p        {"t": "p", "text"}
  list     {"t": "list", "items": [str]}
  kv       {"t": "kv", "rows": [[label, value]]}
  table    {"t": "table", "title"?, "columns": [{key,label,unit?}],
            "rows": [[value...]], "shown", "of", "note"?}
  code     {"t": "code", "text"}
A table/kv value is a primitive or {"v": value, "chip": CHIP}; a None v
renders as NOT SERVED. Builders are pure functions of the RunBundle and
the template caps: no wall clock, no randomness (determinism law).

Doctrine kept here: impact is per unit class with no cross-class
total; money exposure is filing-verbatim and UNPRICED; the backtest is
labelled BACKTEST, the ledger LEDGER; Limits and caveats is locked.
"""
from __future__ import annotations

import json
from collections import Counter

from engine.canonical import stream_hash
from reports.charts import catalogue as chartcat
from reports.vocab import plain

from ports.cells import CHIPS

BACKTEST, LEDGER = "BACKTEST", "LEDGER"
SECTION_CHIPS = CHIPS + (BACKTEST, LEDGER)

UNPRICED = "UNPRICED"


def V(value, chip):
    return {"v": value, "chip": chip}


def _table(columns, rows, shown=None, of=None, note=None, title=None):
    t = {"t": "table", "columns": columns, "rows": rows,
         "shown": len(rows) if shown is None else shown,
         "of": len(rows) if of is None else of}
    if note:
        t["note"] = note
    if title:
        t["title"] = title
    return t


def _col(key, label, unit=None):
    c = {"key": key, "label": label}
    if unit:
        c["unit"] = unit
    return c


def _cap(rows, n):
    return rows if n is None else rows[:n]


def _figures(b, caps, keys):
    """Figure blocks for chart ids (the same figures the export centre
    serves). A chart that cannot be drawn becomes a stated absence."""
    src = chartcat.BundleSource(b)
    blocks = []
    for key in keys:
        try:
            spec = chartcat.build_spec(src, key, caps)
        except chartcat.ChartError as exc:
            blocks.append({"t": "p", "text": f"Figure not drawn (NOT "
                           f"SERVED): {exc.message}."})
            continue
        if not any(s["points"] for s in spec["series"]):
            blocks.append({"t": "p", "text": f"Figure {spec['title']!r} not "
                           f"drawn (NOT SERVED): there are no rows to draw."})
            continue
        blocks.append({"t": "figure", "chart_id": key, "run_id": b.run_id,
                       "spec": spec})
    return blocks


# --------------------------------------------------------------- sections
def s_identity(b, caps):
    j = b.journal
    d = j["disruption"]
    seed = d["node_id"]
    kv = [
        ["Run id", V(b.run_id, "MODELLED")],
        ["Run kind", V(j.get("kind"), "MODELLED")],
    ]
    if j.get("kind") == "fork":
        kv.append(["Forked from", V(
            f"{j.get('parent_run_id')} at step {j.get('at_seq')}",
            "MODELLED")])
    kv += [
        ["Seed place", V(f"{b.name(seed)} ({seed}); tier "
                         f"{b.tier(seed)}", "MODELLED")],
        ["Disruption", V(
            f"{d.get('kind')}; capacity loss {d.get('capacity_loss')}; "
            f"duration {d.get('duration_d')} days; shape {d.get('shape')}; "
            f"starts {d.get('started_at')}", "MODELLED")],
        ["Parameters", V(json.dumps(j.get("params") or {}, sort_keys=True),
                         "MODELLED")],
        ["Place-and-leg map", V(
            f"version {j.get('graph_version')}; hash {j.get('graph_hash')}",
            "MODELLED")],
        ["Chain", V(f"{b.record['event_count']} events; stream hash "
                    f"{b.record['stream_hash']}", "MODELLED")],
    ]
    if caps.get("compact"):
        # brief: parameters and map hash live in the full report / proof
        kv = [row for row in kv
              if row[0] not in ("Parameters", "Place-and-leg map")]
    daily = b.impact_store.daily()
    comtrade = b.impact_store.comtrade()
    exp = b.exposures or {}
    sc = b.scorecard or {}
    basis_rows = [
        ["IMF PortWatch daily layers (goods mix, capacity context)",
         V(daily.get("retrieved_at"), "SNAPSHOT"),
         "stored snapshot pack"],
        ["UN Comtrade, derived shares (country exposure)",
         V(comtrade.get("retrieved_at"), "SNAPSHOT"),
         "stored derived aggregates"],
        ["SEC EDGAR filing citations (money exposure)",
         V((exp.get("harvest") or {}).get("retrieved_at"), "SNAPSHOT"),
         "stored harvest, quoted verbatim"],
        ["Backtest scorecard",
         V(sc.get("version"), BACKTEST), "stored artifact"],
        ["Chain ledger",
         V("present" if b.ledger_present else None, LEDGER),
         "append-only file"],
    ]
    blocks = [{"t": "kv", "rows": kv}]
    if not caps.get("compact"):
        blocks.append(_table(
            [_col("source", "Input"), _col("asof", "Recorded as-of"),
             _col("how", "Kind")], basis_rows,
            title="Data basis (recorded, not re-fetched)"))
    return {"id": "identity", "title": "Identity and configuration",
            "chip": "MODELLED", "locked": False, "blocks": blocks}


def _depth(events):
    by = {e["seq"]: e for e in events}
    depth = {}

    def d(seq):
        if seq in depth:
            return depth[seq]
        p = by[seq]["parent_id"]
        depth[seq] = 0 if p is None else d(p) + 1
        return depth[seq]
    for e in events:
        d(e["seq"])
    return depth


def s_exec(b, caps):
    ev = b.events
    places = b.chain_place_ids()
    seed = b.journal["disruption"]["node_id"]
    chans = Counter(e["channel"] for e in ev)
    depth = _depth(ev)
    root = next((e["seq"] for e in ev if e["parent_id"] is None), None)
    first_order = sorted({e["node_id"] for e in ev
                          if e["parent_id"] == root and e["node_id"] != seed})
    core = sum(1 for p in places if b.tier(p) == "core")
    shell = sum(1 for p in places if b.tier(p) == "shell")
    horizon = b.impact["volume_at_risk"]["clearance_horizon"]
    paras = [
        f"The modelled disruption at {b.name(seed)} propagates through a "
        f"causal chain of {len(ev)} events across {len(places)} places "
        f"over {len(b.edges)} legs, up to {max(depth.values(), default=0)} "
        f"steps deep. Channels used: "
        + ", ".join(f"{k} x{v}" for k, v in sorted(chans.items())) + ".",
        f"{len(first_order)} places are hit directly from the seed "
        f"(first-order). {core} CORE and {shell} SHELL places are on the "
        f"chain; only CORE places are calibration-grade, SHELL places carry "
        f"a labelled ESTIMATE or NOT PUBLISHED capacity.",
    ]
    if horizon.get("value") is not None:
        paras.append(
            f"The last modelled arrival clears about {horizon['value']} "
            f"days after the run start (MODELLED; a model horizon, not a "
            f"forecast).")
    paras.append(
        "Impact is reported per place and per unit class in the sections "
        "below. This summary states no single trade-at-risk figure and no "
        "total across unit classes, and makes no forward price claim.")
    # top places by worst magnitude (non-seed), per place, no totals
    worst = {}
    for e in ev:
        n = e["node_id"]
        w = worst.setdefault(n, [0.0, 0.0])
        w[0] = max(w[0], float(e["magnitude"]))
        w[1] = max(w[1], float(e["delay_d"]))
    ranked = sorted((n for n in worst if n != seed),
                    key=lambda n: (-worst[n][0], n))
    rows = [[b.name(n), V(b.tier(n), "MODELLED"),
             V(round(worst[n][0], 6), "MODELLED"),
             V(round(worst[n][1], 6), "MODELLED")]
            for n in ranked[:caps.get("exec_top", 5)]]
    return {"id": "exec_summary",
            "title": "Executive summary of the causal chain",
            "chip": "MODELLED", "locked": False, "blocks": [
                {"t": "p", "text": t} for t in paras] + [
                _table([_col("place", "Place"), _col("tier", "Tier"),
                        _col("mag", "Worst magnitude", "share of capacity"),
                        _col("delay", "Longest delay", "days")], rows,
                       shown=len(rows), of=len(ranked),
                       title="Most strongly hit places (excluding the seed)")]}


def s_timeline(b, caps):
    ev = b.events
    n = caps.get("timeline")
    rows = [[e["seq"], e["tick"], e["at"], b.name(e["node_id"]),
             e["channel"], e["mechanism"], V(e["delay_d"], "MODELLED"),
             V(e["magnitude"], "MODELLED"),
             "-" if e["parent_id"] is None else e["parent_id"]]
            for e in _cap(ev, n)]
    return {"id": "timeline", "title": "Timeline of steps",
            "chip": "MODELLED", "locked": False, "blocks": [
                {"t": "p", "text": "Chain events in stream order. Each event "
                 "is a place being hit through a leg from its parent event."}]
            + _figures(b, {"timeline": caps.get("cascade_rows", 40)},
                       ["cascade_timeline", "places_reached"]) + [
                _table([_col("seq", "Seq"), _col("tick", "Step"),
                        _col("at", "At (UTC)"), _col("place", "Place"),
                        _col("channel", "Channel"),
                        _col("mech", "Mechanism"),
                        _col("delay", "Delay", "days"),
                        _col("mag", "Magnitude", "share of capacity"),
                        _col("parent", "Parent seq")], rows,
                       shown=len(rows), of=len(ev))]}


def s_places(b, caps):
    n = caps.get("places")
    by_unit: dict = {}
    for r in b.place_rows:
        by_unit.setdefault(r["unit_class"], []).append(r)
    blocks = [{"t": "p", "text":
               "One table per unit class. Capacity figures are the place's "
               "own, in its own unit; SHELL places carry a labelled ESTIMATE "
               "or NOT PUBLISHED capacity. No row or table is a total "
               "across places of different unit classes."}]
    blocks += _figures(b, caps, caps.get("places_figs",
                                         ["delay_by_place", "leg_delay"]))
    for unit in sorted(by_unit, key=str):
        rows_all = sorted(
            by_unit[unit],
            key=lambda r: (-(r["capacity_at_risk"] or -1), r["node_id"]))
        shown = _cap(rows_all, n)
        rows = []
        for r in shown:
            tier = b.tier(r["node_id"])
            cap_chip = ("NOT SERVED" if r["capacity"] is None
                        else r["capacity_class"])
            rows.append([
                r["name"], r["kind"], V(tier, "MODELLED"),
                r["hits"], V(r["worst_magnitude"], "MODELLED"),
                V(r["max_delay_d"], "MODELLED"),
                V(r["capacity"] if r["capacity"] is not None
                  else None, cap_chip),
                V(r["capacity_at_risk"], "MODELLED"
                  if r["capacity_at_risk"] is not None else "NOT SERVED")])
        blocks.append(_table(
            [_col("place", "Place"), _col("kind", "Kind"),
             _col("tier", "Tier"), _col("hits", "Events"),
             _col("mag", "Worst magnitude", "share of capacity"),
             _col("delay", "Longest delay", "days"),
             _col("cap", "Capacity", unit), _col("risk", "Capacity at risk",
                                                  f"{unit} impaired")],
            rows, shown=len(rows), of=len(rows_all),
            title=f"Unit class: {unit}"))
    return {"id": "places", "title": "Places affected", "chip": "MODELLED",
            "locked": False, "blocks": blocks}


def s_impact(b, caps):
    vol = b.impact["volume_at_risk"]
    blocks = [{"t": "p", "text":
               "Impact per unit class. Each class is summed only within "
               "itself; the classes are never converted or merged into one "
               "figure."}]
    if caps.get("impact_figs", True):
        blocks += _figures(b, caps, [k for k in chartcat.impact_charts(
            chartcat.BundleSource(b))])
    for u in vol["by_unit"]:
        d, c = u["daily_volume_at_risk"], u["capacity_days_impaired"]
        blocks.append({"t": "kv", "title": f"Unit class: {u['unit']}",
                       "rows": [
            ["Places counted", V(len(u["places"]), "MODELLED")],
            ["Volume at risk", V(f"{d['value']} {u['unit']} impaired",
                                 "MODELLED")],
            ["Capacity-days impaired", V(f"{c['value']} {u['unit']} x days",
                                         "MODELLED")]]})
    hz = vol["clearance_horizon"]
    blocks.append({"t": "kv", "rows": [
        ["Clearance horizon", V(
            f"{hz['value']} {hz['unit']}" if hz.get("value") is not None
            else None, "MODELLED" if hz.get("value") is not None
            else "NOT SERVED")]]})
    gp = {} if caps.get("compact") else b.impact["goods_profile"]
    for kind, label in (("port", "ports"), ("chokepoint", "straits/canals")):
        if not gp:
            break
        prof = gp.get(kind) or {}
        mix = prof.get("mix") or {}
        if prof.get("state") == "ABSENT" or "value" in mix:
            blocks.append({"t": "p", "text":
                           f"Goods profile through affected {label}: NOT "
                           f"SERVED (no affected {label} place has both a "
                           f"published capacity and a PortWatch goods "
                           f"record)."})
            continue
        rows = [[k.replace("_", " "), V(v["value"], "MODELLED")]
                for k, v in mix.items()]
        blocks.append(_table(
            [_col("cls", "Vessel class"),
             _col("share", "Share of goods through affected " + label,
                  "share")], rows,
            note="Impaired-volume-weighted mean of the affected places' own "
                 "mixes, inside one unit class. Shares of one profile; not "
                 "a total across classes.",
            title=f"Goods profile through affected {label}"))
    return {"id": "impact", "title": "Impact per class (no cross-class totals)",
            "chip": "MODELLED", "locked": False, "blocks": blocks}


def s_money(b, caps):
    n = caps.get("money")
    on_chain = set(b.chain_place_ids())
    maps = [m for m in (b.exposures or {}).get("mappings", [])
            if m.get("node_id") in on_chain]
    maps.sort(key=lambda m: (m["node_id"], m.get("ticker") or ""))
    if b.exposures is None:
        return {"id": "money", "title": "Money exposure citations",
                "chip": "NOT SERVED", "locked": False, "blocks": [
                    {"t": "p", "text": "NOT SERVED: the exposures artifact "
                     "is absent, so no company citation is shown."}]}
    rows = []
    for m in _cap(maps, n):
        f = m.get("filing") or {}
        rows.append([b.name(m["node_id"]), m.get("ticker"),
                     m.get("company"), m.get("exposure_kind"),
                     f"{f.get('form')} filed {f.get('filed_date')}",
                     f.get("url"), f.get("excerpt"),
                     V(None, "NOT SERVED")])
    return {"id": "money", "title": "Money exposure citations",
            "chip": "SNAPSHOT", "locked": False, "blocks": [
                {"t": "p", "text":
                 "Companies whose own annual filing ties them to a place on "
                 "this chain, quoted verbatim from the filing. Every row is "
                 + UNPRICED + ": this product serves no market price, no "
                 "price target and no forward price claim."},
                _table([_col("place", "Place"), _col("ticker", "Ticker"),
                        _col("company", "Company"),
                        _col("kind", "Exposure kind"),
                        _col("filing", "Filing"), _col("url", "URL"),
                        _col("excerpt", "Filing excerpt (verbatim)"),
                        _col("price", "Price")], rows,
                       shown=len(rows), of=len(maps),
                       note="No row is an assertion that the chain moves "
                            "the company's value.")]}


def replay_check(b):
    """('MATCH'|'MISMATCH'|'NOT RUN', detail). Replays the stored journal
    through the app's own engine and compares event streams."""
    eng = b.ctx.engine
    if eng is None:
        return "NOT RUN", "no engine is attached to this process"
    try:
        from api.store import replay_journal
        state = replay_journal(eng, b.journal, b.run_id)
    except Exception as exc:  # replay refusal is a result, not a crash
        return "NOT RUN", f"replay could not run: {type(exc).__name__}"
    same = state.events == b.events
    return ("MATCH" if same else "MISMATCH",
            "the replayed event stream equals the stored stream"
            if same else "the replayed event stream differs")


def s_proof(b, caps):
    recomputed = stream_hash(b.events)
    stored = b.record["stream_hash"]
    status, detail = replay_check(b)
    cur_hash = b.graph.engine_hash if b.graph else None
    rec_hash = b.journal.get("graph_hash")
    kv = [
        ["Stream hash (stored)", V(stored, "MODELLED")],
        ["Stream hash (recomputed from stored events)",
         V(recomputed, "MODELLED")],
        ["Stream hash check",
         V("MATCH" if recomputed == stored else "MISMATCH", "MODELLED")],
        ["Replay check (journal replayed through the engine)",
         V(f"{status}: {detail}", "MODELLED")],
        ["Map hash in journal", V(rec_hash, "MODELLED")],
        ["Map hash of the current map file",
         V(cur_hash, "MODELLED" if cur_hash else "NOT SERVED")],
    ]
    if cur_hash:
        kv.append(["Map hash check", V(
            "MATCH" if cur_hash == rec_hash else "DIFFERS: the map has "
            "changed since this run", "MODELLED")])
    if caps.get("compact"):
        kv = [row for row in kv if row[0] in (
            "Stream hash check", "Replay check (journal replayed through "
            "the engine)", "Map hash check")]
        kv[0] = ["Stream hash check (stored vs recomputed)", V(
            f"{'MATCH' if recomputed == stored else 'MISMATCH'}: {stored}",
            "MODELLED")]
    return {"id": "proof", "title": "Proof and integrity",
            "chip": "MODELLED", "locked": False, "blocks": [
                {"t": "kv", "rows": kv}] + ([] if caps.get("compact") else [
                {"t": "p", "text": "Run journal (the recipe a replay uses):"},
                {"t": "code", "text": json.dumps(
                    {k: v for k, v in b.journal.items()
                     if k != "parent_journal"}, sort_keys=True, indent=1)}])}


def s_backtest(b, caps):
    sc = b.scorecard
    if sc is None:
        return {"id": "backtest", "title": "Backtest scorecard excerpt",
                "chip": "NOT SERVED", "locked": False, "blocks": [
                    {"t": "p", "text": "NOT SERVED: no scorecard artifact "
                     "is present."}]}
    agg = sc.get("aggregate") or {}
    cov = agg.get("coverage") or {}
    dly = agg.get("delay_accuracy") or {}
    fps = agg.get("false_positives") or {}
    cnt = agg.get("counts") or {}
    kv = [
        ["Scorecard", V(f"version {sc.get('version')}", BACKTEST)],
        ["Replays graded", V(agg.get("replays"), BACKTEST)],
        ["Places matched", V(
            f"{cov.get('covered_count')} of "
            f"{cov.get('distinct_non_seed_nodes')} observed", BACKTEST)],
        ["Delay difference (past replays)", V(
            f"{dly.get('hits')} of {dly.get('graded_steps')} graded steps "
            f"matched; mean absolute difference "
            f"{dly.get('mean_abs_error_d')} days", BACKTEST)],
        ["Modelled, not observed", V(fps.get("count"), BACKTEST)],
        ["Observed, not modelled", V(cnt.get("genuine_misses"), BACKTEST)],
    ]
    rows = [[r.get("title") or r.get("id"), r.get("event_count"),
             V(f"{(r.get('coverage') or {}).get('covered_count')} of "
               f"{(r.get('coverage') or {}).get('distinct_non_seed_nodes')}",
               BACKTEST),
             V((r.get("delay_accuracy") or {}).get("hits"), BACKTEST)]
            for r in (sc.get("replays") or [])]
    cav = [plain(str(c)) for c in sc.get("honesty_caveats") or []]
    return {"id": "backtest", "title": "Backtest scorecard excerpt",
            "chip": BACKTEST, "locked": False, "blocks": [
                {"t": "p", "text": "BACKTEST: the engine's output compared with "
                 "documented past disruptions. Descriptive of past replays "
                 "only: not live-ledger performance, and it says nothing "
                 "about future events."},
                {"t": "kv", "rows": kv}]
            + _figures(b, caps, ["backtest_coverage", "backtest_precision",
                                 "backtest_delay_error"]) + [
                _table([_col("replay", "Replay"), _col("ev", "Events"),
                        _col("cov", "Places matched"),
                        _col("hit", "Matched delay steps")],
                       rows)] + (
                [{"t": "p", "text": "Scorecard caveats:"},
                 {"t": "list", "items": cav}] if cav else [])}


def s_ledger(b, caps):
    if not b.ledger_present:
        return {"id": "ledger", "title": "Ledger excerpt", "chip": LEDGER,
                "locked": False, "blocks": [
                    {"t": "p", "text": "LEDGER: NOT SERVED. No ledger file "
                     "is present; zero entries is a fact about the store, "
                     "not a grade."}]}
    mine = b.ledger_entries()
    graded_all = sum(1 for e in b.ledger_all if e.get("grade"))
    rows = [[e.get("entry_id"), e.get("disruption_id"), e.get("locked_at"),
             V("graded" if e.get("grade") else "ungraded", LEDGER),
             V(len((e.get("grade") or {}).get("hit_steps") or [])
               if e.get("grade") else None, LEDGER
               if e.get("grade") else "NOT SERVED")] for e in mine]
    return {"id": "ledger", "title": "Ledger excerpt", "chip": LEDGER,
            "locked": False, "blocks": [
                {"t": "p", "text":
                 f"LEDGER: locked chains are append-only. The ledger "
                 f"holds {len(b.ledger_all)} entries in total, "
                 f"{graded_all} graded; {len(mine)} lock this run."},
                _table([_col("entry", "Entry"), _col("dis", "Disruption"),
                        _col("locked", "Locked at (UTC)"),
                        _col("graded", "Grade state"),
                        _col("hit", "Steps matched")], rows)]}


CAVEATS = [
    "This is a model of how a disruption propagates, not a forecast. Every "
    "figure labelled MODELLED is derived from a stored recipe (the journal) "
    "and the map; it is deterministic, not predictive.",
    "No total is computed across unit classes (TEU, vessels, trucks, "
    "tonnes) or across goods classes, and no single 'trade at risk' number "
    "is offered. Sums appear only inside one unit class.",
    "No forward price claim is made. Company citations are quoted verbatim "
    "from filings and are UNPRICED; freight and commodity index values are "
    "cited with dates, never projected. SCFI is not displayed: its licence "
    "is unverified.",
    "Only the 200 CORE places are calibration-grade. SHELL places carry a "
    "labelled ESTIMATE or NOT PUBLISHED capacity and are not calibrated.",
    "Leg shares and transit times in the map are MODELLED allocations; "
    "PortWatch tonnage is an AIS draught-model estimate, not a "
    "measurement.",
    "Data marked SNAPSHOT is a stored, dated copy. A section whose feed is "
    "down is NOT SERVED and is never filled in.",
    "The BACKTEST scorecard compares past replays; it says nothing about "
    "live runs. The LEDGER holds locked chains and their grades; absence of "
    "entries is not a grade.",
]


def s_caveats(b, caps):
    return {"id": "caveats", "title": "Limits and caveats",
            "chip": "MODELLED", "locked": True, "blocks": [
                {"t": "list", "items": CAVEATS}]}


GLOSSARY = [
    ("Place", "A port, strait or canal, air hub or ground crossing on the "
              "map."),
    ("Leg", "A connection between two places along which a disruption can "
            "travel."),
    ("Causal chain", "The ordered set of events a disruption causes, each "
                     "linked to the event that triggered it."),
    ("Step (tick)", "One position in the chain's stream order."),
    ("Channel", "How an event reaches a place: congestion, queue, "
                "inventory, reroute and similar."),
    ("Magnitude", "Share of a place's capacity impaired by an event (0 to "
                  "1)."),
    ("Delay", "Days of delay the event adds at the place."),
    ("Capacity at risk", "Capacity x worst magnitude, in the place's own "
                         "unit per day."),
    ("Unit class", "The unit a place's capacity is published in (TEU/day, "
                   "vessels/day, trucks/day, tonnes/day). Classes are never "
                   "merged."),
    ("SOURCED", "Read from a published source answering now."),
    ("SNAPSHOT", "Read from a stored, dated copy of a source."),
    ("MODELLED", "Derived by this product from stated rules."),
    ("SIMULATED", "A value from a stored simulation, not an observation."),
    ("NOT SERVED", "No data; shown as an absence, never as zero."),
    ("BACKTEST", "Graded against documented past disruptions."),
    ("LEDGER", "Locked predictions and grades, append-only."),
    ("CORE / SHELL", "CORE places are calibration-grade; SHELL places carry "
                     "an estimate or no published capacity."),
    ("Stream hash", "SHA-256 over the canonical event stream; identical "
                    "inputs give the identical hash."),
    ("Replay check", "Re-running the stored journal and comparing the "
                     "event streams."),
]


def s_glossary(b, caps):
    return {"id": "glossary", "title": "Glossary", "chip": "MODELLED",
            "locked": False, "blocks": [
                _table([_col("term", "Term"), _col("meaning", "Meaning")],
                       [[t, m] for t, m in GLOSSARY])]}


# ----------------------------------------------------------------- registry
# size model: bytes ~ base + per_item * items, items named in `scales`
REGISTRY = {
    "identity": dict(build=s_identity, title="Identity and configuration",
                     chip="MODELLED", base=2600, per=0, scales=None),
    "exec_summary": dict(build=s_exec,
                         title="Executive summary of the causal chain",
                         chip="MODELLED", base=1800, per=170,
                         scales="top"),
    "timeline": dict(build=s_timeline, title="Timeline of steps",
                     chip="MODELLED", base=700, per=210, scales="events",
                     fig={"full": 9800, "per": 330}),
    "places": dict(build=s_places, title="Places affected",
                   chip="MODELLED", base=900, per=380, scales="places",
                   fig={"full": 6700, "executive_brief": 2700}),
    "impact": dict(build=s_impact,
                   title="Impact per class (no cross-class totals)",
                   chip="MODELLED", base=2200, per=0, scales=None,
                   fig={"full": 8000}),
    "money": dict(build=s_money, title="Money exposure citations",
                  chip="SNAPSHOT", base=700, per=900, scales="money"),
    "proof": dict(build=s_proof, title="Proof and integrity",
                  chip="MODELLED", base=2400, per=0, scales=None),
    "backtest": dict(build=s_backtest, title="Backtest scorecard excerpt",
                     chip=BACKTEST, base=2600, per=0, scales=None,
                     fig={"full": 7600}),
    "ledger": dict(build=s_ledger, title="Ledger excerpt", chip=LEDGER,
                   base=800, per=260, scales="ledger"),
    "caveats": dict(build=s_caveats, title="Limits and caveats",
                    chip="MODELLED", base=1700, per=0, scales=None,
                    locked=True),
    "glossary": dict(build=s_glossary, title="Glossary", chip="MODELLED",
                     base=3400, per=0, scales=None),
}
ORDER = list(REGISTRY)
LOCKED = [k for k, v in REGISTRY.items() if v.get("locked")]

TEMPLATES = {
    "full": {
        "title": "Full run report",
        "description": "Every section, with capped tables (120 timeline "
                       "rows, 120 places per unit class, 60 citations).",
        "sections": ORDER,
        "caps": {"timeline": 120, "places": 120, "money": 60,
                 "exec_top": 5, "cascade_rows": 40, "delay_rows": 25,
                 "leg_rows": 20, "impact_rows": 15},
    },
    "executive_brief": {
        "title": "Executive brief (1-2 pages)",
        "description": "Identity, executive summary, places (top 5 per unit "
                       "class), impact per class, proof, and the locked "
                       "caveats.",
        "sections": ["identity", "exec_summary", "places", "impact",
                     "proof", "caveats"],
        "caps": {"timeline": 12, "places": 5, "money": 6, "exec_top": 3,
                 "compact": True, "cascade_rows": 12, "delay_rows": 5,
                 "leg_rows": 5, "impact_rows": 5,
                 "places_figs": ["delay_by_place"], "impact_figs": False},
    },
}
