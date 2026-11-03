"""Export centre: one-dataset CSV and the full data pack (zip).

Datasets are per-place / per-event row sets. LAW: no dataset carries a
total row or total column across places, unit classes or goods classes;
every row carries its provenance label; units are named in the CSV
header as `column [unit]`. Output is deterministic for fixed inputs:
rows have a fixed order, CSV is "\\n" terminated UTF-8, the zip uses
sorted entries with 1980-01-01 timestamps. The ONLY varying field in a
pack is manifest.generated_at; board/health datasets depend on the
`as_of` instant (feed freshness), which the manifest records and a
caller may pin to reproduce a pack byte for byte.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone

import source_credits as _credits
from ports.board import Board
from ports.feeds import Feeds, iso, parse_ts
from ports.sources import Ctx, load_graph

from . import centre, xlsx
from .charts import catalogue as chartcat
from .charts import png as chartpng
from .charts import render as chartrender
from .csv_guard import neutralise  # noqa: F401  (the one shared guard)
from .data import ReportError, RunBundle
from .pack import canonical_bytes  # noqa: F401  (re-exported for callers)

DEFAULT_ROWS_CAP = 10_000
MAX_ROWS_CAP = 200_000
BOARD_CELLS = ("activity", "activity_ratio", "border_wait", "hub_aircraft",
               "ais_samples", "satwatch", "events")


# ------------------------------------------------------------------ env
class Env:
    """Per-request lazy loader shared by the datasets."""

    def __init__(self, ctx: Ctx, run_id=None, sim_run_id=None, tick=None):
        self.ctx = ctx
        self.run_id, self.sim_run_id, self.tick = run_id, sim_run_id, tick
        self._bundle = None
        self._board = None
        self._graph = None

    @property
    def graph(self):
        if self._graph is None:
            self._graph = load_graph(self.ctx.graph_path)
        return self._graph

    @property
    def bundle(self) -> RunBundle | None:
        if self.run_id is None:
            return None
        if self._bundle is None:
            self._bundle = RunBundle(self.ctx, self.run_id)
        return self._bundle

    @property
    def board(self) -> Board:
        if self._board is None:
            from ports.board import BoardError
            try:
                self._board = Board(self.ctx, run_id=self.run_id,
                                    sim_run_id=self.sim_run_id,
                                    tick=self.tick)
            except BoardError as exc:
                raise ReportError(exc.code, exc.message, exc.status)
        return self._board

    def sim_record(self):
        store = self.ctx.sim_store
        rec = store.load(self.sim_run_id) if (
            store is not None and self.sim_run_id) else None
        if rec is None:
            raise ReportError("unknown_sim_run",
                              f"unknown sim_run_id: {self.sim_run_id!r}", 404)
        return rec

    def name(self, pid):
        n = (self.graph.nodes.get(pid) if self.graph else None) or {}
        return n.get("name") or pid

    def tier(self, pid):
        return ((self.graph.nodes.get(pid) if self.graph else None) or {}
                ).get("tier")


@dataclass
class Params:
    from_: datetime | None = None
    to: datetime | None = None
    channel: tuple | None = None
    place: tuple | None = None
    board: dict | None = None     # board filters: mode/kind/tier/region/status


def _in_window(ts_text, p: Params) -> bool:
    if p.from_ is None and p.to is None:
        return True
    ts = parse_ts(ts_text)
    if ts is None:
        return False
    if p.from_ is not None and ts < p.from_:
        return False
    if p.to is not None and ts > p.to:
        return False
    return True


# ------------------------------------------------------------- datasets
def _col(name, unit=None):
    return (name, unit)


def ds_chain_events(env: Env, p: Params):
    b = env.bundle
    for e in b.events:
        if p.channel and e["channel"] not in p.channel:
            continue
        if p.place and e["node_id"] not in p.place:
            continue
        if not _in_window(e["at"], p):
            continue
        yield [e["seq"], "" if e["parent_id"] is None else e["parent_id"],
               e["tick"], e["at"], e["node_id"], env.name(e["node_id"]),
               env.tier(e["node_id"]), e["channel"], e["mechanism"],
               e["delay_d"], e["magnitude"], "MODELLED"]


def ds_legs(env: Env, p: Params):
    b = env.bundle
    by = {e["seq"]: e for e in b.events}
    for leg in b.edges:
        ev = by[leg["to_seq"]]
        if p.channel and leg["channel"] not in p.channel:
            continue
        if p.place and not ({leg["from_node"], leg["to_node"]} & set(p.place)):
            continue
        if not _in_window(ev["at"], p):
            continue
        yield ["" if leg["from_seq"] is None else leg["from_seq"],
               leg["to_seq"], leg["from_node"], env.name(leg["from_node"]),
               leg["to_node"], env.name(leg["to_node"]), leg["channel"],
               ev["at"], ev["delay_d"], "MODELLED"]


def ds_impact(env: Env, p: Params):
    for r in env.bundle.place_rows:
        if p.place and r["node_id"] not in p.place:
            continue
        cap_chip = ("NOT SERVED" if r["capacity"] is None
                    else r["capacity_class"])
        risk_chip = ("NOT SERVED" if r["capacity_at_risk"] is None
                     else "MODELLED")
        yield [r["node_id"], r["name"], r["kind"], env.tier(r["node_id"]),
               r["unit_class"],
               "" if r["capacity"] is None else r["capacity"], cap_chip,
               r["capacity_reason"] or "",
               "" if r["capacity_at_risk"] is None else r["capacity_at_risk"],
               risk_chip, r["capacity_at_risk_reason"] or "",
               r["worst_magnitude"], r["max_delay_d"], r["hits"]]


def _board_cols():
    cols = [_col("place_id"), _col("place"), _col("kind"), _col("mode"),
            _col("tier"), _col("region"), _col("country"),
            _col("status"), _col("status_rule"),
            _col("capacity", "see capacity_unit"),
            _col("capacity_unit"), _col("capacity_chip"),
            _col("capacity_basis")]
    for c in BOARD_CELLS:
        cols += [_col(c, "see " + c + "_unit"), _col(c + "_unit"),
                 _col(c + "_chip"), _col(c + "_as_of", "UTC")]
    cols += [_col("chain_worst_magnitude", "share of capacity"),
             _col("chain_max_delay_d", "days"),
             _col("chain_capacity_at_risk", "see chain_unit_class"),
             _col("chain_unit_class"),
             _col("sim_queue", "simulated entities"),
             _col("sim_berthed", "simulated entities")]
    return cols


def ds_board(env: Env, p: Params):
    f = p.board or {}
    res = env.board.list_rows(
        mode=f.get("mode"), kind=f.get("kind"), tier=f.get("tier"),
        region=f.get("region"), status=f.get("status"),
        sort="id", order="asc", limit=10 ** 9, offset=0)
    for r in res["places"]:
        if p.place and r["place_id"] not in p.place:
            continue
        cap = r["capacity"]
        row = [r["place_id"], r["name"], r["kind"], r["mode"], r["tier"],
               r["region"], r["country"] or "", r["status"]["label"],
               f"{r['status']['rule_id']}@{r['status']['rule_version']}",
               "" if cap["value"] is None else cap["value"],
               cap.get("unit") or "", cap["chip"], cap.get("basis") or ""]
        for cname in BOARD_CELLS:
            c = r["cells"].get(cname)
            if c is None:
                row += ["", "", "NOT APPLICABLE", ""]
            else:
                row += ["" if c["value"] is None else c["value"],
                        c.get("unit") or "", c["chip"], c.get("as_of") or ""]
        ch = r["cells"].get("chain_exposure")
        if ch is not None and ch["value"].get("on_chain"):
            v = ch["value"]
            row += [v["worst_magnitude"], v["max_delay_d"],
                    "" if v["capacity_at_risk"] is None
                    else v["capacity_at_risk"], v["unit_class"]]
        else:
            row += ["", "", "", ""]
        sm = r["cells"].get("simulated")
        if sm is not None and sm["value"] is not None:
            row += [sm["value"]["queue"], sm["value"]["berthed"]]
        else:
            row += ["", ""]
        yield row


def ds_sim(env: Env, p: Params):
    from datetime import timedelta
    from ports.sources import TICK_SECONDS
    rec = env.sim_record()
    j = rec["journal"]
    start = parse_ts(j["start_at"])
    step = TICK_SECONDS[j["tick"]]
    series = rec.get("node_series") or {}
    for nid in sorted(series):
        if p.place and nid not in p.place:
            continue
        s = series[nid]
        for t in range(len(s["queue"])):
            at = iso(start + timedelta(seconds=step * t))
            if not _in_window(at, p):
                continue
            yield [env.sim_run_id, t, at, nid, env.name(nid), s["queue"][t],
                   s["berthed"][t], s["arrivals"][t], s["departures"][t],
                   "SIMULATED"]


def _sim_count(env: Env) -> int:
    rec = env.sim_record()
    return sum(len(s["queue"]) for s in (rec.get("node_series") or {}).values())


BACKTEST_CAVEAT = ("Descriptive comparison with documented past replays; "
                   "not live performance and not a forecast")


def ds_scorecards(env: Env, p: Params):
    sc = _read_scorecard(env.ctx)
    if sc is None:
        return
    for r in sc.get("replays") or []:
        cov, dly = r.get("coverage") or {}, r.get("delay_accuracy") or {}
        fps, cnt = r.get("false_positives") or {}, r.get("counts") or {}
        n_obs, n_hit = cov.get("distinct_non_seed_nodes"), \
            cov.get("covered_count")
        n_miss = (n_obs - n_hit) if isinstance(n_obs, int) and \
            isinstance(n_hit, int) else None
        yield [r.get("id"), r.get("title"), r.get("seed_node"),
               r.get("event_count"), cnt.get("observed_steps"),
               n_obs, n_hit, n_miss,
               dly.get("graded_steps"), dly.get("hits"),
               dly.get("mean_abs_error_d"), fps.get("count"),
               cnt.get("genuine_misses"), "BACKTEST",
               BACKTEST_CAVEAT]


def _read_scorecard(ctx: Ctx):
    from ports.sources import load_json
    sc = load_json(ctx.scorecard_path)
    return sc if isinstance(sc, dict) else None


def _n(x):
    return len(x) if isinstance(x, (list, tuple)) else ""


def ds_ledger(env: Env, p: Params):
    from ports.sources import read_ledger
    entries, _ = read_ledger(env.ctx.ledger_path)
    for e in sorted(entries, key=lambda x: (x.get("locked_at") or "",
                                            x.get("entry_id") or "")):
        if not _in_window(e.get("locked_at"), p):
            continue
        g = e.get("grade") or {}
        yield [e.get("entry_id"), e.get("disruption_id"), e.get("run_id"),
               e.get("locked_at"), len(e.get("predicted_chain") or []),
               "graded" if e.get("grade") else "ungraded",
               _n(g.get("hit_steps")), _n(g.get("missed_steps")),
               g.get("delay_error_d", ""), "LEDGER"]


def ds_health(env: Env, p: Params):
    ctx = env.ctx
    feeds = Feeds(ctx.snapshots_dir, ctx.now)
    for st in feeds.all_states():
        yield [st["feed"], st["label"], st["state"],
               st.get("mode_recorded") or "", st.get("retrieved_at") or "",
               "" if st.get("age_s") is None else st["age_s"],
               st["max_age_s"],
               "" if st.get("stale") is None else str(st["stale"]).lower(),
               st["records"], st.get("reason") or "",
               "SNAPSHOT" if st["state"] != "NOT SERVED" else "NOT SERVED"]


def ds_money(env: Env, p: Params):
    from ports.sources import read_exposures
    exp = read_exposures(env.ctx.exposures_path)
    if exp is None:
        return
    on_chain = set(env.bundle.chain_place_ids()) if env.bundle else None
    for m in sorted(exp["mappings"],
                    key=lambda m: (m.get("node_id") or "",
                                   m.get("ticker") or "")):
        if on_chain is not None and m.get("node_id") not in on_chain:
            continue
        if p.place and m.get("node_id") not in p.place:
            continue
        f = m.get("filing") or {}
        yield [m.get("node_id"), env.name(m.get("node_id")), m.get("ticker"),
               m.get("company"), m.get("exposure_kind"),
               m.get("matched_phrase"), f.get("form"), f.get("filed_date"),
               f.get("url"), f.get("excerpt"),
               "SNAPSHOT" if f.get("excerpt") else "NOT SERVED",
               "UNPRICED", "SNAPSHOT"]


DATASETS = {
    "chain_events": dict(
        label="Chain events", needs="run", provenance="MODELLED",
        description="Every event of the run's causal chain, in stream "
                    "order.",
        columns=[_col("seq"), _col("parent_seq"), _col("step"),
                 _col("at", "UTC"), _col("place_id"), _col("place"),
                 _col("tier"), _col("channel"), _col("mechanism"),
                 _col("delay_d", "days"),
                 _col("magnitude", "share of capacity"),
                 _col("provenance")],
        fn=ds_chain_events, filters=["from", "to", "channel", "place"]),
    "legs": dict(
        label="Legs of the chain", needs="run", provenance="MODELLED",
        description="The legs the chain travelled: parent place to reached "
                    "place, with the channel.",
        columns=[_col("from_seq"), _col("to_seq"), _col("from_place_id"),
                 _col("from_place"), _col("to_place_id"), _col("to_place"),
                 _col("channel"), _col("arrives_at", "UTC"),
                 _col("delay_d", "days"), _col("provenance")],
        fn=ds_legs, filters=["from", "to", "channel", "place"]),
    "impact_per_place": dict(
        label="Impact per place per unit class", needs="run",
        sources="IMF PortWatch; UN Comtrade (derived shares)",
        provenance="MODELLED",
        description="One row per affected place in its own unit class. "
                    "No total row or column exists.",
        columns=[_col("place_id"), _col("place"), _col("kind"),
                 _col("tier"), _col("unit_class"),
                 _col("capacity", "see unit_class"),
                 _col("capacity_chip"), _col("capacity_reason"),
                 _col("capacity_at_risk", "see unit_class impaired/day"),
                 _col("capacity_at_risk_chip"),
                 _col("capacity_at_risk_reason"),
                 _col("worst_magnitude", "share of capacity"),
                 _col("max_delay_d", "days"), _col("events")],
        fn=ds_impact, filters=["place"]),
    "ports_board": dict(
        label="Ports board snapshot", needs=None, provenance="mixed (per cell)",
        sources="IMF PortWatch; NOAA / NWS; GDACS; OpenSky; CBP",
        description="The ports tracker board at the as_of instant: one row "
                    "per place, every cell with its own chip and as-of. "
                    "With a run, chain columns are filled; with a sim run, "
                    "simulated state.",
        columns=_board_cols(),
        fn=ds_board, filters=["place", "mode", "kind", "tier", "region",
                              "status"]),
    "sim_series": dict(
        label="Simulation run state series", needs="sim",
        provenance="SIMULATED",
        description="Per place per tick: simulated queue, berthed, "
                    "arrivals, departures. SIMULATED, not observed.",
        columns=[_col("sim_run_id"), _col("tick"), _col("sim_time", "UTC"),
                 _col("place_id"), _col("place"),
                 _col("queue", "simulated entities"),
                 _col("berthed", "simulated entities"),
                 _col("arrivals", "simulated entities/tick"),
                 _col("departures", "simulated entities/tick"),
                 _col("provenance")],
        fn=ds_sim, filters=["from", "to", "place"]),
    "scorecards": dict(
        label="Backtest scorecards", needs=None, provenance="BACKTEST",
        description="Per-replay comparison of the engine's output with "
                    "documented past disruptions. Descriptive of past "
                    "replays only: BACKTEST, not live performance and not a "
                    "forecast.",
        columns=[_col("replay_id"), _col("title"), _col("seed_place_id"),
                 _col("events"), _col("observed_steps"),
                 _col("observed_places", "count"),
                 _col("matched_places", "count"),
                 _col("not_matched_places", "count"),
                 _col("graded_steps", "count"),
                 _col("matched_delay_steps", "count"),
                 _col("delay_difference_mean_d", "days"),
                 _col("modelled_not_observed", "count"),
                 _col("observed_not_modelled", "count"),
                 _col("provenance"), _col("caveat")],
        fn=ds_scorecards, filters=[]),
    "ledger": dict(
        label="Chain ledger", needs=None, provenance="LEDGER",
        description="Locked chains and their grades (append-only).",
        columns=[_col("entry_id"), _col("disruption_id"), _col("run_id"),
                 _col("locked_at", "UTC"),
                 _col("locked_steps", "count"), _col("grade_state"),
                 _col("matched_steps", "count"),
                 _col("not_matched_steps", "count"),
                 _col("delay_difference_d", "days"), _col("provenance")],
        fn=ds_ledger, filters=["from", "to"]),
    "health_board": dict(
        label="Feed health board", needs=None, provenance="SNAPSHOT",
        sources="NOAA / NWS; GDACS; GDELT; CBP; IMF PortWatch; OpenSky; AISStream; EIA; UN Comtrade",
        description="State of each contributing feed at the as_of "
                    "instant, derived from the stored packs.",
        columns=[_col("feed"), _col("label"), _col("state"),
                 _col("mode_recorded"), _col("retrieved_at", "UTC"),
                 _col("age_s", "seconds"), _col("max_age_s", "seconds"),
                 _col("stale"), _col("records", "count"), _col("reason"),
                 _col("provenance")],
        fn=ds_health, filters=[]),
    "money_exposures": dict(
        label="Money exposure citations", needs=None, provenance="SNAPSHOT",
        sources="SEC EDGAR",
        description="Companies whose own filing ties them to a place, "
                    "excerpt verbatim, UNPRICED. With a run: places on its "
                    "chain only.",
        columns=[_col("place_id"), _col("place"), _col("ticker"),
                 _col("company"), _col("exposure_kind"),
                 _col("matched_phrase"), _col("filing_form"),
                 _col("filed_date"), _col("filing_url"),
                 _col("filing_excerpt_verbatim"),
                 _col("filing_excerpt_chip"), _col("price"),
                 _col("provenance")],
        fn=ds_money, filters=["place"]),
}
DATASET_ORDER = list(DATASETS)


# -------------------------------------------------------------- csv + meta
def header_of(columns) -> list[str]:
    return [f"{n} [{u}]" if u else n for n, u in columns]


def to_csv_bytes(columns, rows) -> bytes:
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header_of(columns))
    for r in rows:
        w.writerow([neutralise(v) for v in r])
    return buf.getvalue().encode("utf-8")


def needs_state(spec, env: Env) -> str | None:
    if spec["needs"] == "run" and env.run_id is None:
        return "NEEDS RUN"
    if spec["needs"] == "sim" and env.sim_run_id is None:
        return "NEEDS SIM RUN"
    return None


def collect(env: Env, dataset_id: str, p: Params, cap: int | None = None):
    """(rows written, rows available). Rows past `cap` are cut."""
    spec = DATASETS[dataset_id]
    gate = needs_state(spec, env)
    if gate:
        raise ReportError("invalid_request",
                          f"dataset {dataset_id!r} needs "
                          f"{'run_id' if gate == 'NEEDS RUN' else 'sim_run_id'}")
    out, n = [], 0
    for row in spec["fn"](env, p):
        n += 1
        if cap is None or len(out) < cap:
            out.append(row)
    return out, n


def catalogue(ctx: Ctx, run_id=None, sim_run_id=None, tick=None) -> dict:
    env = Env(ctx, run_id, sim_run_id, tick)
    items = []
    for did in DATASET_ORDER:
        spec = DATASETS[did]
        gate = needs_state(spec, env)
        if gate:
            state, rows = gate, None
        else:
            if did == "sim_series":
                rows = _sim_count(env)
            else:
                rows = sum(1 for _ in spec["fn"](env, Params()))
            state = "HAS DATA" if rows else "NO DATA"
        items.append({
            "id": did, "label": spec["label"],
            "description": spec["description"], "state": state,
            "rows": rows, "provenance": spec["provenance"],
            "needs": spec["needs"],
            "columns": [{"name": n, "unit": u, "header": h}
                        for (n, u), h in zip(spec["columns"],
                                             header_of(spec["columns"]))],
            "filters": spec["filters"],
            "csv": f"/exports/dataset/{did}.csv"})
    return {"schema": "exports/1", "run_id": run_id,
            "sim_run_id": sim_run_id,
            "default_rows_cap": DEFAULT_ROWS_CAP,
            "max_rows_cap": MAX_ROWS_CAP, "datasets": items,
            "counter": f"{sum(1 for i in items if i['state'] == 'HAS DATA')}"
                       f" of {len(items)} datasets have data"}


# ------------------------------------------------------------------- pack
LAW_LINES = [
    "Every dataset row carries its own provenance label (MODELLED, "
    "SIMULATED, BACKTEST, LEDGER, SNAPSHOT, or per-cell chips in the "
    "board).",
    "No file contains a total across places, unit classes or goods "
    "classes.",
    "A value that is not served is an empty cell with chip NOT SERVED "
    "(or reason column), never a zero.",
    "No forward price claim; money citations are UNPRICED.",
]


def _run_info(env: Env) -> dict:
    """Run identity for the cover sheet and the manifest (empty without a
    run)."""
    b = env.bundle
    if b is None:
        return {}
    j, d = b.journal, b.journal.get("disruption") or {}
    seed = d.get("node_id")
    return {
        "run_kind": j.get("kind"),
        "seed_place": f"{b.name(seed)} ({seed})" if seed else None,
        "disruption": (f"{d.get('kind')}; capacity loss "
                       f"{d.get('capacity_loss')}; duration "
                       f"{d.get('duration_d')} days; shape {d.get('shape')}; "
                       f"starts {d.get('started_at')}"),
        "engine_rev": j.get("engine_rev", 1),
        "engine_label": j.get("engine_label"),
        "map_version_in_journal": j.get("graph_version"),
        "event_count": b.record.get("event_count"),
    }


COVER_WIDTHS = [36, 58, 22, 16, 16, 12, 60]
CHIP_MEANING = [
    ("SOURCED", "read from a published source answering now"),
    ("SNAPSHOT", "read from a stored, dated copy of a source"),
    ("MODELLED", "derived by this product from stated rules"),
    ("SIMULATED", "a value from a stored simulation, not an observation"),
    ("NOT SERVED", "no data: an empty cell, never a zero"),
    ("BACKTEST", "compared with documented past disruptions; descriptive of the past, not a forecast"),
    ("LEDGER", "locked chains and grades, append-only"),
]


def cover_rows(meta: dict, info: dict) -> list:
    """Rows of the workbook's cover sheet: run identity, provenance, map
    hash, engine revision, row counts and what was cut. A pure function of
    the manifest fields and the run info (no clock)."""
    R = []

    def sec(name):
        R.append([])
        R.append([(name, "section")] + [(" ", "section")] * 6)

    def kv(k, v):
        R.append([(k, "bold"), (v if v is not None else "not available",
                                "wrap")])

    R.append([("SupermAP data export", "title")])
    R.append([("Built from stored data; nothing was fetched while building "
               "this workbook. The same inputs and as-of instant give the "
               "same workbook.", "muted")])
    sec("Identity")
    kv("Run id", meta.get("run_id") or "none (datasets that need no run)")
    kv("Simulation run id", meta.get("sim_run_id") or "none")
    kv("As-of (UTC)", meta["as_of"])
    if info:
        kv("Run kind", info.get("run_kind"))
        kv("Seed place", info.get("seed_place"))
        kv("Disruption", info.get("disruption"))
        kv("Engine revision", f"{info.get('engine_rev')}"
           + (f" ({info['engine_label']})" if info.get("engine_label")
              else ""))
        kv("Causal chain events", info.get("event_count"))
        kv("Stream hash (SHA-256 of the event stream)",
           meta.get("stream_hash"))
    g = meta.get("graph") or {}
    kv("Place-and-leg map version recorded by the run",
       info.get("map_version_in_journal") if info else None)
    kv("Map hash recorded by the run", g.get("journal_graph_hash"))
    kv("Map hash of the current map file", g.get("current_map_hash"))
    kv("Map file SHA-256", g.get("map_file_sha256"))
    sec("Provenance")
    for line in LAW_LINES:
        R.append([(line, "wrap")])
    for chip, meaning in CHIP_MEANING:
        kv(chip, meaning)
    kv("Units", "Column headers carry the unit as `name [unit]`; an empty "
       "cell means not served.")
    sec("Sources")
    for line in _credits.source_lines(_credits.doc_text(
            [meta.get("datasets"), meta.get("charts"),
             [DATASETS[e["id"]].get("sources") for e in meta["datasets"]]])):
        R.append([(line, "wrap")])
    sec("Sheets")
    R.append([(h, "header") for h in (
        "Sheet", "Dataset", "Provenance", "Rows in sheet", "Rows available",
        "Rows cut", "What it holds")])
    for e in meta["datasets"]:
        R.append([(e["id"], "bold"), (e["label"], "wrap"),
                  (e["provenance"], None), (e["rows_written"], "int"),
                  (e["rows_available"], "int"), (e["cut"], "int"),
                  (DATASETS[e["id"]]["description"], "wrap")])
    sec("What was cut")
    cuts = [e for e in meta["datasets"] if e["cut"] > 0]
    if cuts:
        for e in cuts:
            kv(e["id"], f"{e['cut']} of {e['rows_available']} rows cut by "
               f"the cap of {meta['rows_cap']} rows per dataset (the first "
               f"{e['rows_written']} are kept, in fixed order)")
    else:
        kv("Rows", f"nothing was cut (cap {meta['rows_cap']} rows per "
           f"dataset)")
    if meta.get("filters"):
        kv("Filters applied", ", ".join(
            f"{k}={v}" for k, v in sorted(meta["filters"].items())))
    for sk in meta.get("skipped") or []:
        kv(f"Not included: {sk['id']}", sk["reason"])
    if meta.get("charts"):
        sec("Figures (also in the data pack as SVG and CSV)")
        R.append([(h, "header") for h in ("Figure", "Title", "Provenance",
                                          "Type", "Points")])
        for c in meta["charts"]:
            R.append([(c["id"], "bold"), (c["title"], "wrap"),
                      (c["chip"], None), (c["type"], None),
                      (c["points"], "int")])
    return R


def build_workbook(meta: dict, info: dict, tables: dict, created: str,
                   title: str) -> tuple[bytes, list[str]]:
    """Cover + one typed sheet per dataset."""
    sheets = [xlsx.text_sheet("Cover", cover_rows(meta, info), COVER_WIDTHS)]
    for e in meta["datasets"]:
        cols, rows = tables[e["id"]]
        sheets.append(xlsx.table_sheet(e["id"], header_of(cols), rows))
    data = xlsx.write_workbook(sheets, created, title)
    used: set = set()
    return data, [xlsx.safe_sheet_name(s.name, used) for s in sheets]


def dataset_workbook(env: Env, did: str, p: Params, cap: int,
                     as_of_dt: datetime) -> tuple[bytes, int, int]:
    """One-dataset workbook (Cover + the dataset sheet). -> (bytes, rows
    written, rows available)."""
    spec = DATASETS[did]
    rows, avail = collect(env, did, p, cap)
    gv = load_graph(env.ctx.graph_path)
    b = env.bundle
    meta = {
        "run_id": env.run_id, "sim_run_id": env.sim_run_id,
        "as_of": iso(as_of_dt), "rows_cap": cap, "filters": _filters_view(p),
        "graph": {"journal_graph_hash": b.journal.get("graph_hash")
                  if b else None,
                  "current_map_hash": gv.engine_hash if gv else None,
                  "map_file_sha256": gv.file_sha256 if gv else None},
        "stream_hash": b.record["stream_hash"] if b else None,
        "datasets": [{"id": did, "label": spec["label"],
                      "provenance": spec["provenance"],
                      "rows_available": avail, "rows_written": len(rows),
                      "cut": avail - len(rows)}],
        "skipped": [], "charts": []}
    wb, _ = build_workbook(meta, _run_info(env), {did: (spec["columns"],
                                                        rows)},
                           iso(as_of_dt), f"SupermAP {spec['label']}")
    return wb, len(rows), avail


def build_pack(ctx: Ctx, *, run_id=None, sim_run_id=None, tick=None,
               datasets=None, rows_cap=DEFAULT_ROWS_CAP, as_of=None,
               include_report=True, template="full", sections=None,
               params: Params | None = None,
               generated_at: str | None = None, include_charts=True,
               charts=None, include_xlsx=True, include_png=False
               ) -> tuple[bytes, dict]:
    if not isinstance(rows_cap, int) or isinstance(rows_cap, bool) \
            or not 1 <= rows_cap <= MAX_ROWS_CAP:
        raise ReportError("invalid_request",
                          f"rows_cap must be an integer in 1..{MAX_ROWS_CAP}")
    if charts is not None and (not isinstance(charts, list) or not all(
            isinstance(c, str) for c in charts)):
        raise ReportError("invalid_request",
                          "charts must be a list of chart ids")
    as_of_dt = as_of or ctx.now.replace(second=0, microsecond=0)
    pctx = Ctx(**{**ctx.__dict__, "now_fn": (lambda: as_of_dt)})
    env = Env(pctx, run_id, sim_run_id, tick)
    p = params or Params()

    explicit = datasets is not None
    if explicit:
        if not isinstance(datasets, list) or not all(
                isinstance(d, str) for d in datasets):
            raise ReportError("invalid_request",
                              "datasets must be a list of dataset ids")
        bad = [d for d in datasets if d not in DATASETS]
        if bad:
            raise ReportError("invalid_request",
                              f"unknown dataset {bad[0]!r}; known: "
                              f"{DATASET_ORDER}")
        wanted = [d for d in DATASET_ORDER if d in datasets]
    else:
        wanted = list(DATASET_ORDER)

    files: dict[str, bytes] = {}
    entries, skipped, tables = [], [], {}
    for did in wanted:
        spec = DATASETS[did]
        gate = needs_state(spec, env)
        if gate:
            if explicit:
                raise ReportError(
                    "invalid_request",
                    f"dataset {did!r} needs "
                    f"{'run_id' if gate == 'NEEDS RUN' else 'sim_run_id'}")
            skipped.append({"id": did, "reason": gate})
            continue
        rows, avail = collect(env, did, p, rows_cap)
        if avail == 0 and not explicit:
            skipped.append({"id": did, "reason": "NO DATA"})
            continue
        data = to_csv_bytes(spec["columns"], rows)
        fname = f"datasets/{did}.csv"
        files[fname] = data
        tables[did] = (spec["columns"], rows)
        entries.append({
            "id": did, "file": fname, "label": spec["label"],
            "provenance": spec["provenance"],
            "rows_available": avail, "rows_written": len(rows),
            "cut": avail - len(rows), "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "columns": header_of(spec["columns"])})

    report_meta = None
    bundle = env.bundle
    if include_report and bundle is not None:
        rep = centre.build_report(bundle, template, sections, "html")
        files["report.html"] = rep.content
        md = centre.build_report(bundle, template, sections, "md")
        files["report.md"] = md.content
        report_meta = {"file": "report.html", "md_file": "report.md",
                       "template": template,
                       "sections": rep.meta["sections"],
                       "bytes": rep.meta["bytes"],
                       "sha256": rep.meta["sha256"],
                       "md_sha256": md.meta["sha256"]}

    chart_entries, chart_svgs = [], {}
    if include_charts:
        have = {e["id"] for e in entries}
        for row in chartcat.list_charts(env, datasets=have):
            if row["state"] != "HAS DATA":
                continue
            if charts is not None and row["id"] not in charts:
                continue
            spec = row["spec"]
            svg = chartrender.render_svg(spec, theme="light").encode("utf-8")
            csvb = chartrender.chart_csv(spec)
            jsn = (json.dumps(spec, indent=1, sort_keys=True,
                              ensure_ascii=False) + "\n").encode("utf-8")
            base = f"charts/{row['id']}"
            files[base + ".svg"], files[base + ".csv"] = svg, csvb
            files[base + ".json"] = jsn
            chart_svgs[row["id"]] = svg.decode("utf-8")
            chart_entries.append({
                "id": row["id"], "title": spec["title"], "type": spec["type"],
                "chip": spec["chip"], "dataset": row["dataset"],
                "points": sum(len(s["points"]) for s in spec["series"]),
                "files": {"svg": base + ".svg", "csv": base + ".csv",
                          "json": base + ".json"}})
    png_meta = {"requested": bool(include_png and include_charts)}
    if png_meta["requested"] and chart_svgs:
        try:
            pngs = chartpng.render_pngs(chart_svgs)
            for cid, data in pngs.items():
                files[f"charts/{cid}.png"] = data
                for ce in chart_entries:
                    if ce["id"] == cid:
                        ce["files"]["png"] = f"charts/{cid}.png"
            png_meta.update({"available": True, "reason": None})
        except chartpng.PngError as exc:
            png_meta.update({"available": False, "reason": str(exc)})
    elif png_meta["requested"]:
        png_meta.update({"available": None, "reason": "no charts to draw"})

    graph_hash = None
    stream = None
    if bundle is not None:
        graph_hash = bundle.journal.get("graph_hash")
        stream = bundle.record["stream_hash"]
    gv = load_graph(ctx.graph_path)
    cut = [{"id": e["id"], "rows_cut": e["cut"], "rows_cap": rows_cap}
           for e in entries if e["cut"] > 0]
    manifest = {
        "pack": "supermap-export-pack", "schema": "exports-pack/2",
        "run_id": run_id, "sim_run_id": sim_run_id,
        "as_of": iso(as_of_dt), "generated_at": generated_at or iso(
            datetime.now(timezone.utc)),
        "determinism": "every file is byte-identical across regenerations "
                       "for the same inputs and as_of; the only varying "
                       "field is this manifest's generated_at (PNG figures, "
                       "when requested, are the browser's raster output and "
                       "are not promised byte-identical across machines)",
        "rows_cap": rows_cap, "filters": _filters_view(p),
        "graph": {"journal_graph_hash": graph_hash,
                  "current_map_hash": gv.engine_hash if gv else None,
                  "map_file_sha256": gv.file_sha256 if gv else None,
                  "map_version": gv.version if gv else None},
        "stream_hash": stream,
        "engine": {k: v for k, v in _run_info(env).items()
                   if k in ("engine_rev", "engine_label")},
        "datasets": entries,
        "cut": cut, "skipped": skipped, "report": report_meta,
        "charts": chart_entries, "charts_png": png_meta,
        "workbook": None,
        "credits": _credits.source_lines(_credits.doc_text(
            [entries, chart_entries,
             [DATASETS[e["id"]].get("sources") for e in entries]])),
        "provenance": {
            "law": LAW_LINES,
            "chips": ["SOURCED", "SNAPSHOT", "MODELLED", "SIMULATED",
                      "NOT SERVED", "BACKTEST", "LEDGER"],
            "sources": "stored packs under the data root; nothing is "
                       "fetched at build time"},
    }
    if include_xlsx and entries:
        wb, names = build_workbook(
            manifest, _run_info(env), tables, iso(as_of_dt),
            f"SupermAP export {run_id or sim_run_id or ''}".strip())
        files["workbook.xlsx"] = wb
        manifest["workbook"] = {"file": "workbook.xlsx", "bytes": len(wb),
                                "sha256": hashlib.sha256(wb).hexdigest(),
                                "sheets": names}
    files["SOURCES.txt"] = ("Sources\n" + "\n".join(
        f"- {t}" for t in manifest["credits"]) + "\n").encode("utf-8")
    manifest["files"] = {n: {"bytes": len(d),
                             "sha256": hashlib.sha256(d).hexdigest()}
                         for n, d in sorted(files.items())}
    files["manifest.json"] = (json.dumps(manifest, indent=1, sort_keys=True,
                                         ensure_ascii=False) + "\n"
                              ).encode("utf-8")
    return zip_bytes(files), manifest


def _filters_view(p: Params) -> dict:
    out = {}
    if p.from_:
        out["from"] = iso(p.from_)
    if p.to:
        out["to"] = iso(p.to)
    if p.channel:
        out["channel"] = list(p.channel)
    if p.place:
        out["place"] = list(p.place)
    if p.board:
        out["board"] = {k: list(v) for k, v in p.board.items() if v}
    return out


def zip_bytes(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED,
                         compresslevel=6) as zf:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, files[name], compresslevel=6)
    return buf.getvalue()
