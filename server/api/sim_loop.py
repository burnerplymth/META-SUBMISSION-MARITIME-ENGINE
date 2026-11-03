"""SIM loop routes: interventions journal, fork, compare, verify.

The SIM layer is a precomputed batch simulation (server/sim); this
module is the thin API surface that lets a client treat it as a
step / shock / fork loop WITHOUT touching the chain propagation engine or
the run, graph and ledger interfaces:

  POST /sim/run (extended in app.py by `fold_interventions`)
      an optional `interventions` list of tick-stamped shocks;
  POST /sim/run/{id}/fork            -> new run id (async create-then-poll)
  GET  /sim/run/{id}/journal         -> the recipe (settings + shocks)
  GET  /sim/runs/lineage?root=       -> the fork tree under a run
  GET  /sim/compare?a=&b=            -> divergence tick + measures table
  GET  /sim/run/{id}/verify          -> stream SHA-256, rule version
  POST /sim/run/{id}/verify          -> replay-by-rebuild (async)

Design:

* An intervention is NOT a new journal key. It is an entry of the
  existing `disruptions` / `airspace` / `storm_cells` lists carrying
  two extra fields: `known_from` (ISO, = the shock's start instant) and
  `intervention: {seq, at_tick}`. A body without interventions folds
  nothing, so its journal core - hence its sim_run_id and every byte of
  its record - is exactly that of a run without interventions.
* `known_from` gates the simulator's planning look-ahead
  (server/sim/simulate.py), so a shock dated tick N cannot change any
  event before N: a fork shares its parent's segment record up to N.
* The parent edge of a fork is provenance, not input: it lives in the
  append-only sidecar `<sim_runs>/lineage.jsonl`, never in the hashed
  journal. A fork's recipe is recoverable from its own journal alone.
* Everything here is SIMULATED / MODELLED and says so in a
  `provenance` field. Nothing is fabricated: a measure that needs an
  input that is absent is listed under `omitted`, never zero-filled.
"""
from __future__ import annotations

import bisect
import hashlib
import json
import math
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from .memtrim import release_free_memory
from .security import scrub_paths

try:
    from engine.canonical import canonical_json, sha256_hex
except Exception:  # imported as server.api.* from the repo root instead
    from server.engine.canonical import canonical_json, sha256_hex  # type: ignore

try:
    from sim.store import NullSink, write_record
except Exception:  # imported as server.api.* from the repo root instead
    from server.sim.store import NullSink, write_record  # type: ignore

try:  # the model fingerprint lives next to the caches it keys
    from sim import runner as _runner
except Exception:  # imported as server.api.* from the repo root instead
    try:
        from server.sim import runner as _runner  # type: ignore
    except Exception:  # pragma: no cover - sim layer unavailable
        _runner = None  # type: ignore[assignment]

PROVENANCE = "SIMULATED"
MODELLED = "MODELLED"
MAX_INTERVENTIONS = 32
SHOCK_KINDS = ("disruption", "airspace", "storm_cell")
_JOURNAL_LISTS = {"disruption": "disruptions", "airspace": "airspace",
                  "storm_cell": "storm_cells"}
_FORK_FIXED = ("seed", "ticks", "tick", "start_at", "weather", "graph_hash")


def rule_version() -> str:
    """Content fingerprint of the model code (derive, geometry,
    simulate, weather, land data). Any mechanism change moves it."""
    return "sim-rules-" + _runner._code_fp(_runner._SIM_CODE)[:12]


class SimLoopRefused(Exception):
    """A refusal with a reason: surfaced as the standard error shape
    (422 unless the code is mapped). Never a clamp, never a coercion."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# ---------------------------------------------------------------------
# Interventions: strict validation, canonical order, journal folding.
# ---------------------------------------------------------------------

def _number(value, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SimLoopRefused("invalid_request", f"{label} must be a number")
    value = float(value)
    if math.isnan(value) or math.isinf(value):
        raise SimLoopRefused("invalid_request", f"{label} must be finite")
    return value


def _at_tick(value, label: str, lo: int, ticks: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SimLoopRefused("invalid_request", f"{label} must be an integer")
    if not lo <= value <= ticks - 1:
        raise SimLoopRefused(
            "invalid_request",
            f"{label} out of range {lo}..{ticks - 1} (refused, never clamped)")
    return value


def parse_interventions(raw, *, graph, ticks: int, start_epoch: float,
                        tick_s: int, fmt_ts, validate_disruption,
                        engine_error, min_tick: int = 0) -> list[dict]:
    """Validate an `interventions` list into normalised items
    {kind, at_tick, spec}. Strict: exact key sets, real numbers (no
    bools, no coercion), places must exist in the graph, bounds are
    refused with the reason. `spec` is the existing shock shape without
    `started_at` (derived from `at_tick`)."""
    if not isinstance(raw, list):
        raise SimLoopRefused("invalid_request", "interventions must be a list")
    if len(raw) > MAX_INTERVENTIONS:
        raise SimLoopRefused(
            "invalid_request",
            f"at most {MAX_INTERVENTIONS} interventions per run")
    items = []
    for i, item in enumerate(raw):
        tag = f"interventions[{i}]"
        if not isinstance(item, dict):
            raise SimLoopRefused("invalid_request", f"{tag} must be an object")
        if set(item) != {"kind", "at_tick", "spec"}:
            raise SimLoopRefused(
                "invalid_request",
                f"{tag}: keys must be exactly kind, at_tick, spec")
        kind = item["kind"]
        if not isinstance(kind, str) or kind not in SHOCK_KINDS:
            raise SimLoopRefused(
                "invalid_request",
                f"{tag}.kind must be one of {list(SHOCK_KINDS)}")
        at = _at_tick(item["at_tick"], f"{tag}.at_tick", 0, ticks)
        if at < min_tick:
            raise SimLoopRefused(
                "intervention_refused",
                f"{tag}.at_tick {at} is before the fork point (tick "
                f"{min_tick}); a fork accepts shocks dated at or after "
                "the fork point only")
        spec = item["spec"]
        if not isinstance(spec, dict):
            raise SimLoopRefused("invalid_request", f"{tag}.spec must be an object")
        if "started_at" in spec:
            raise SimLoopRefused(
                "invalid_request",
                f"{tag}.spec.started_at is derived from at_tick; remove it")
        iso = fmt_ts(start_epoch + at * tick_s)
        if kind == "disruption":
            allowed = {"node_id", "kind", "capacity_loss", "duration_d", "shape"}
            required = {"node_id", "kind", "duration_d"}
            extra = sorted(set(spec) - allowed)
            missing = sorted(required - set(spec))
            if extra or missing:
                raise SimLoopRefused(
                    "invalid_request",
                    f"{tag}.spec: unknown keys {extra}, missing keys {missing}")
            try:
                eff = validate_disruption(graph, {**spec, "started_at": iso})
            except engine_error as exc:
                raise SimLoopRefused(exc.code, f"{tag}.spec: {exc}") from None
            if eff["shape"] != "step":
                raise SimLoopRefused(
                    "invalid_shape",
                    f"{tag}.spec: sim supports step-shape shocks only; got "
                    f"shape={eff['shape']!r} (never silently reinterpreted)")
            norm = {k: eff[k] for k in ("node_id", "kind", "capacity_loss",
                                        "duration_d", "shape")}
        elif kind == "airspace":
            if set(spec) != {"anchor", "radius_km", "duration_d"}:
                raise SimLoopRefused(
                    "invalid_request",
                    f"{tag}.spec: keys must be exactly anchor, radius_km, "
                    "duration_d")
            anchor = spec["anchor"]
            if not isinstance(anchor, str) or anchor not in graph.nodes:
                raise SimLoopRefused(
                    "invalid_request",
                    f"{tag}.spec.anchor: unknown place: {anchor!r}")
            radius = _number(spec["radius_km"], f"{tag}.spec.radius_km")
            if not 0.0 < radius <= 20000.0:
                raise SimLoopRefused(
                    "invalid_request",
                    f"{tag}.spec.radius_km out of bounds (0, 20000]")
            dur = _number(spec["duration_d"], f"{tag}.spec.duration_d")
            if not 0.0 < dur <= 3650.0:
                raise SimLoopRefused(
                    "invalid_request",
                    f"{tag}.spec.duration_d out of bounds (0, 3650]")
            norm = {"anchor": anchor, "radius_km": radius, "duration_d": dur}
        else:  # storm_cell
            if set(spec) != {"from_node", "to_node", "radius_km", "duration_d"}:
                raise SimLoopRefused(
                    "invalid_request",
                    f"{tag}.spec: keys must be exactly from_node, to_node, "
                    "radius_km, duration_d")
            for field in ("from_node", "to_node"):
                if not isinstance(spec[field], str) \
                        or spec[field] not in graph.nodes:
                    raise SimLoopRefused(
                        "invalid_request",
                        f"{tag}.spec.{field}: unknown place: {spec[field]!r}")
            radius = _number(spec["radius_km"], f"{tag}.spec.radius_km")
            if not 0.0 < radius <= 20000.0:
                raise SimLoopRefused(
                    "invalid_request",
                    f"{tag}.spec.radius_km out of bounds (0, 20000]")
            dur = _number(spec["duration_d"], f"{tag}.spec.duration_d")
            if not 0.0 < dur <= 3650.0:
                raise SimLoopRefused(
                    "invalid_request",
                    f"{tag}.spec.duration_d out of bounds (0, 3650]")
            norm = {"from_node": spec["from_node"], "to_node": spec["to_node"],
                    "radius_km": radius, "duration_d": dur}
        items.append({"kind": kind, "at_tick": at, "spec": norm})
    return items


def fold_interventions(raw, graph, ticks: int, start_epoch: float,
                       tick_s: int, fmt_ts, validate_disruption,
                       engine_error, effective: list, airspace_eff: list,
                       storm_eff: list):
    """The POST /sim/run hook: validate `raw` and append its entries to
    the three effective lists (returned as new lists). Interventions
    are put in canonical order - by at_tick, then submission order -
    and numbered seq 1..n, so the same set of shocks always yields the
    same journal. An empty list folds nothing."""
    items = parse_interventions(
        raw, graph=graph, ticks=ticks, start_epoch=start_epoch,
        tick_s=tick_s, fmt_ts=fmt_ts, validate_disruption=validate_disruption,
        engine_error=engine_error)
    return fold(items, start_epoch, tick_s, fmt_ts, effective, airspace_eff,
                storm_eff)


def fold(items: list[dict], start_epoch: float, tick_s: int, fmt_ts,
         effective: list, airspace_eff: list, storm_eff: list):
    dis, air, storm = list(effective), list(airspace_eff), list(storm_eff)
    ordered = sorted(enumerate(items), key=lambda p: (p[1]["at_tick"], p[0]))
    for seq, (_, it) in enumerate(ordered, start=1):
        at = it["at_tick"]
        iso = fmt_ts(start_epoch + at * tick_s)
        meta = {"seq": seq, "at_tick": at}
        if it["kind"] == "disruption":
            dis.append({**it["spec"], "started_at": iso,
                        "sim_id": f"d{len(dis) + 1}", "known_from": iso,
                        "intervention": meta})
        elif it["kind"] == "airspace":
            air.append({**it["spec"], "started_at": iso,
                        "sim_id": f"a{len(air) + 1}", "known_from": iso,
                        "intervention": meta})
        else:
            storm.append({**it["spec"], "started_at": iso,
                          "sim_id": f"s{len(storm) + 1}", "known_from": iso,
                          "intervention": meta})
    return dis, air, storm


_ENTRY_STRIP = ("sim_id", "known_from", "intervention", "started_at")


def journal_interventions(journal: dict) -> list[dict]:
    """The interventions of a stored journal, in seq order:
    [{seq, kind, at_tick, spec, sim_id}]."""
    out = []
    for kind, key in _JOURNAL_LISTS.items():
        for ent in journal.get(key) or []:
            meta = ent.get("intervention")
            if not isinstance(meta, dict):
                continue
            out.append({"seq": meta["seq"], "kind": kind,
                        "at_tick": meta["at_tick"],
                        "spec": {k: v for k, v in ent.items()
                                 if k not in _ENTRY_STRIP},
                        "sim_id": ent.get("sim_id")})
    out.sort(key=lambda e: e["seq"])
    return out


def merge_fork_journal(parent: dict, fork_tick: int, new_items: list[dict],
                       remove_seqs: list[int], start_epoch: float,
                       tick_s: int, fmt_ts) -> dict:
    """Journal core of a fork: the parent's settings and base shocks,
    its interventions minus `remove_seqs` (only ones at or after the
    fork tick may go), plus `new_items`. Re-folded canonically, so a
    fork whose edits reproduce a root body's journal has the root's id."""
    inherited = journal_interventions(parent)
    by_seq = {e["seq"]: e for e in inherited}
    seen = set()
    for seq in remove_seqs:
        if isinstance(seq, bool) or not isinstance(seq, int):
            raise SimLoopRefused("invalid_request",
                                 "remove must be a list of integer seq numbers")
        if seq in seen:
            raise SimLoopRefused("intervention_refused",
                                 f"remove: seq {seq} listed twice")
        seen.add(seq)
        if seq not in by_seq:
            raise SimLoopRefused("intervention_refused",
                                 f"remove: no intervention with seq {seq}")
        if by_seq[seq]["at_tick"] < fork_tick:
            raise SimLoopRefused(
                "intervention_refused",
                f"remove: seq {seq} is dated tick {by_seq[seq]['at_tick']}, "
                f"before the fork point (tick {fork_tick}); removing it "
                "would change the shared prefix")
    kept = [{"kind": e["kind"], "at_tick": e["at_tick"], "spec": e["spec"]}
            for e in inherited if e["seq"] not in seen]
    items = kept + list(new_items)
    if len(items) > MAX_INTERVENTIONS:
        raise SimLoopRefused(
            "invalid_request",
            f"at most {MAX_INTERVENTIONS} interventions per run")
    base = {}
    for key in ("disruptions", "airspace", "storm_cells"):
        base[key] = [e for e in (parent.get(key) or [])
                     if not isinstance(e.get("intervention"), dict)]
    dis, air, storm = fold(items, start_epoch, tick_s, fmt_ts,
                           base["disruptions"], base["airspace"],
                           base["storm_cells"])
    core = {k: v for k, v in parent.items()
            if k not in ("disruptions", "airspace", "storm_cells")}
    core["disruptions"] = dis
    if air:
        core["airspace"] = air
    if storm:
        core["storm_cells"] = storm
    return core


# ---------------------------------------------------------------------
# Lineage sidecar (append-only provenance, never part of any hash).
# ---------------------------------------------------------------------

class LineageStore:
    def __init__(self, sim_dir: Path):
        self.path = Path(sim_dir) / "lineage.jsonl"
        self._lock = threading.Lock()

    def edges(self) -> list[dict]:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out = []
        for line in lines:
            try:
                ent = json.loads(line)
            except ValueError:
                continue
            if isinstance(ent, dict) and ent.get("child_id") \
                    and ent.get("parent_id"):
                out.append(ent)
        return out

    def append(self, child_id: str, parent_id: str, at_tick: int,
               n_interventions: int) -> None:
        with self._lock:
            for e in self.edges():
                if e["child_id"] == child_id and e["parent_id"] == parent_id \
                        and e.get("at_tick") == at_tick:
                    return
            ent = {"child_id": child_id, "parent_id": parent_id,
                   "at_tick": at_tick, "n_interventions": n_interventions,
                   "created_utc": datetime.now(timezone.utc).strftime(
                       "%Y-%m-%dT%H:%M:%SZ")}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(ent, sort_keys=True) + "\n")


# ---------------------------------------------------------------------
# Compare: pure functions over two stored records.
# ---------------------------------------------------------------------

_COMPARE_KEYS = ("graph_hash", "tick", "ticks", "start_at", "seed",
                 "weather_snapshot_hash")


def comparable_reason(ja: dict, jb: dict) -> list[str]:
    return [k for k in _COMPARE_KEYS if ja.get(k) != jb.get(k)]


def _first_event_difference(sa: dict, sb: dict):
    """(t, entity_id, index, a_event, b_event, n_entities_differing) of
    the earliest differing segment event between two segment records,
    or None when identical."""
    best = None
    differing = 0
    for eid in sorted(set(sa) | set(sb)):
        ea, eb = sa.get(eid) or [], sb.get(eid) or []
        if ea == eb:
            continue
        differing += 1
        i = 0
        n = min(len(ea), len(eb))
        while i < n and ea[i] == eb[i]:
            i += 1
        ta = ea[i]["t"] if i < len(ea) else math.inf
        tb = eb[i]["t"] if i < len(eb) else math.inf
        t = min(ta, tb)
        if best is None or t < best[0]:
            best = (t, eid, i, ea[i] if i < len(ea) else None,
                    eb[i] if i < len(eb) else None)
    if best is None:
        return None
    return (*best, differing)


def _series_first_diff(a: list, b: list):
    for k in range(min(len(a), len(b))):
        if a[k] != b[k]:
            return k
    return None if len(a) == len(b) else min(len(a), len(b))


def _node_series_diff(na: dict, nb: dict) -> list[tuple]:
    """[(place_id, first_differs_tick)] for places whose per-tick
    series differ, ordered by (tick, id)."""
    out = []
    for nid in sorted(set(na) | set(nb)):
        sa, sb = na.get(nid), nb.get(nid)
        if sa is None or sb is None:
            out.append((nid, 0))
            continue
        first = None
        for key in sa:
            k = _series_first_diff(sa[key], sb.get(key) or [])
            if k is not None and (first is None or k < first):
                first = k
        if first is not None:
            out.append((nid, first))
    out.sort(key=lambda p: (p[1], p[0]))
    return out


def _unit_label(unit: str) -> str:
    return unit.split("/")[0]


def measure_facts(record: dict) -> dict:
    """The expensive, graph-independent half of ``run_measures``: the
    per-tick series that need a pass over every segment event and every
    place's node series. Plain JSON (ints, floats, strings), so it can
    be stored beside the record and reused without loading it."""
    journal = record["journal"]
    ticks = int(journal["ticks"])
    tick_s = {"hour": 3600, "day": 86400, "week": 604800,
              "month": 2592000}[journal["tick"]]
    start_epoch = _runner_parse_ts(journal["start_at"])
    tick_times = [start_epoch + k * tick_s for k in range(ticks)]
    mode_of = {e["id"]: e["mode"] for e in record["derivation"]["entities"]}
    sea_risk, air_div, divert_ts, hold_ts = {}, {}, [], []
    for eid, evs in (record.get("segments") or {}).items():
        mode = mode_of.get(eid)
        for ev in evs:
            if ev["kind"] == "divert":
                divert_ts.append(ev["t"])
                book = air_div if mode == "air" else sea_risk
                if eid not in book or ev["t"] < book[eid]:
                    book[eid] = ev["t"]
            elif ev["kind"] == "arrive" and ev.get("hold"):
                hold_ts.append(ev["t"])
                if mode != "air" and (eid not in sea_risk
                                      or ev["t"] < sea_risk[eid]):
                    sea_risk[eid] = ev["t"]

    def cum_series(times):
        times = sorted(times)
        return [bisect.bisect_right(times, tt) for tt in tick_times]

    ns = record.get("node_series") or {}
    place_ids = sorted(ns)
    queue_tot_peak, qh_tot = [], []
    for k in range(ticks):
        peak, qh = 0, 0.0
        for nid in place_ids:
            q = ns[nid]["queue"][k]
            if q > peak:
                peak = q
            qh += ns[nid]["queue_hours_cum"][k]
        queue_tot_peak.append(peak)
        qh_tot.append(round(qh, 6))
    # overall peak position: first (tick, place) reaching the max
    overall = max(queue_tot_peak) if queue_tot_peak else 0
    at = None
    if overall > 0:
        k0 = queue_tot_peak.index(overall)
        nid0 = next(n for n in place_ids if ns[n]["queue"][k0] == overall)
        at = {"place_id": nid0, "tick": k0}
    return {"v": 1,
            "vessels_at_risk": cum_series(list(sea_risk.values())),
            "aircraft_diverted": cum_series(list(air_div.values())),
            "legs_diverted": cum_series(divert_ts),
            "legs_held": cum_series(hold_ts),
            "queue_peak": queue_tot_peak, "queue_peak_value": overall,
            "queue_peak_at": at, "queue_hours": qh_tot}


def measures_from_facts(facts: dict, sd: dict,
                        nodes_meta: dict | None) -> dict:
    """Per-run measure rows from the stored facts (``measure_facts``)
    and the record's ``sim_delay_by_node`` - see ``run_measures``."""
    out: dict = {}

    def put(mid, label, unit, series, value=None, at_=None, basis=PROVENANCE):
        out[mid] = {"label": label, "unit": unit, "basis": basis,
                    "series": series, "at": at_,
                    "value": series[-1] if (value is None and series) else value}

    put("vessels_at_risk", "VESSELS AT RISK", "vessels",
        facts["vessels_at_risk"])
    put("aircraft_diverted", "AIRCRAFT DIVERTED", "aircraft",
        facts["aircraft_diverted"])
    put("legs_diverted", "LEGS DIVERTED", "legs", facts["legs_diverted"])
    put("legs_held", "LEGS HELD", "legs", facts["legs_held"])
    put("queue_peak", "QUEUE PEAK", "vessels or aircraft",
        facts["queue_peak"], value=facts["queue_peak_value"],
        at_=facts["queue_peak_at"])
    put("queue_hours", "QUEUE HOURS", "hours", facts["queue_hours"])
    if nodes_meta is not None:
        # Throughput displaced against the run's own undisrupted twin:
        # per CORE place, daily throughput x mean added leg-arrival
        # delay (days) from the stored sim_delay_by_node - the
        # volume-at-risk formula, units kept apart. MODELLED.
        totals: dict = {}
        for nid in sorted(sd):
            n = nodes_meta.get(nid)
            if not n or n.get("tier", "core") != "core":
                continue
            cap, unit = n.get("capacity"), n.get("capacity_unit")
            mean = sd[nid].get("mean_added_delay_d")
            if not isinstance(cap, (int, float)) or not unit \
                    or mean is None:
                continue
            u = _unit_label(unit)
            totals[u] = totals.get(u, 0.0) + float(cap) * float(mean)
        seen = sorted({_unit_label(n["capacity_unit"])
                       for n in nodes_meta.values()
                       if n.get("tier", "core") == "core"
                       and n.get("capacity_unit")})
        for u in seen:
            put(f"delayed_{u.lower()}", f"{u.upper()} DELAYED", u, None,
                value=round(totals.get(u, 0.0), 6), basis=MODELLED)
    affected = sum(1 for s in sd.values()
                   if (s.get("mean_added_delay_d") or 0) > 0
                   or (s.get("hold_arrivals") or 0) > 0)
    put("places_affected", "PLACES AFFECTED", "places", None, value=affected)
    return out


def run_measures(record: dict, nodes_meta: dict | None,
                 facts: dict | None = None) -> dict:
    """Per-run measure series from stored facts only (segments,
    node_series, sim_delay_by_node). Returns {id: {label, unit, basis,
    series|None, value, at}} in the table's fixed order.

    ``nodes_meta`` ({place_id: graph node}) is needed only for the
    throughput-delayed rows; None omits them. ``facts`` (optional): the
    already-computed ``measure_facts(record)``."""
    if facts is None:
        facts = measure_facts(record)
    return measures_from_facts(facts, record.get("sim_delay_by_node") or {},
                               nodes_meta)


def _measures_of(record: dict, nodes_meta, memo: dict | None,
                 facts_of=None) -> dict:
    """Measure rows of a loaded record. ``facts_of(record)`` (optional)
    supplies ``measure_facts`` from a store that may already hold them."""
    if memo is None:
        return run_measures(record, nodes_meta,
                            facts_of(record) if facts_of else None)
    key = (record["sim_run_id"], nodes_meta is not None)
    got = memo.get(key)
    if got is None:
        got = run_measures(record, nodes_meta,
                           facts_of(record) if facts_of else None)
        memo[key] = got
        while len(memo) > 8:
            memo.pop(next(iter(memo)))
    return got


def _runner_parse_ts(text: str) -> float:
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _fmt_ts(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _diff(a, b):
    d = b - a
    return round(d, 6) if isinstance(d, float) else d


def compare_records(ra: dict, rb: dict, nodes_meta: dict | None = None,
                    measure_memo: dict | None = None,
                    facts_of=None) -> dict:
    """Divergence tick, first difference and measures table for two
    comparable records. Pure function of the stored records
    (``measure_memo``, when given, only caches per-run measures;
    ``facts_of`` only supplies already-computed measure facts)."""
    diff = _first_event_difference(ra.get("segments") or {},
                                   rb.get("segments") or {})

    def entity_mode(eid):
        return next((e["mode"] for e in ra["derivation"]["entities"]
                     if e["id"] == eid), None)

    return assemble_compare(
        ra["journal"], diff, entity_mode,
        ra.get("node_series") or {}, rb.get("node_series") or {},
        _measures_of(ra, nodes_meta, measure_memo, facts_of),
        _measures_of(rb, nodes_meta, measure_memo, facts_of), nodes_meta)


def assemble_compare(ja: dict, diff, entity_mode, na: dict, nb: dict,
                     ma: dict, mb: dict, nodes_meta) -> dict:
    """The comparison result from its parts: ``diff`` is
    ``_first_event_difference``'s answer (or None), ``entity_mode(eid)``
    the mode of an entity of run A, ``na``/``nb`` the runs' node series,
    ``ma``/``mb`` their measure rows. Shared by the full-record path
    (``compare_records``) and the route's lazy path, which feeds it
    parts read from the store without loading either record."""
    tick_s = {"hour": 3600, "day": 86400, "week": 604800,
              "month": 2592000}[ja["tick"]]
    start_epoch = _runner_parse_ts(ja["start_at"])
    first_difference = None
    diverges = None
    differing_entities = 0
    if diff is not None:
        t, eid, idx, ea, eb, differing_entities = diff
        node = (ea or eb or {}).get("node")
        diverges = max(0, int(math.floor((t - start_epoch) / tick_s)))
        mode = entity_mode(eid)
        first_difference = {"tick": diverges, "t": _fmt_ts(t),
                            "entity_id": eid, "mode": mode,
                            "place_id": node,
                            "a_event": ea, "b_event": eb}
    changed = _node_series_diff(na, nb)
    rows = []
    for mid, a in ma.items():
        b = mb[mid]
        first = None
        if a["series"] is not None and b["series"] is not None:
            first = _series_first_diff(a["series"], b["series"])
        rows.append({
            "id": mid, "label": a["label"], "unit": a["unit"],
            "basis": a["basis"], "a": a["value"], "b": b["value"],
            "difference": _diff(a["value"], b["value"]),
            "first_differs_tick": first,
            "per_tick": a["series"] is not None,
            "a_at": a["at"], "b_at": b["at"]})
    omitted = []
    if nodes_meta is None:
        omitted.append({"measure": "delayed_throughput",
                        "reason": "graph_changed"})
    return {
        "provenance": PROVENANCE,
        "diverges_from_tick": diverges,
        "first_difference": first_difference,
        "differing_entities": differing_entities,
        "places_changed": {
            "count": len(changed),
            "first": [{"place_id": n, "first_differs_tick": k}
                      for n, k in changed[:25]]},
        "measures": rows,
        "omitted": omitted,
    }


# ---------------------------------------------------------------------
# Verify helpers.
# ---------------------------------------------------------------------

def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def stream_sha256(record: dict) -> str:
    """SHA-256 over the canonical simulated event stream of a record:
    segments + node_series + auxiliary paths + reassignment accounting.

    Equal to ``sha256_hex(canonical_json(payload))`` of the payload
    dict {segments, node_series, aux_paths, reassignment_accounting};
    the canonical text is hashed piece by piece (top-level keys in
    sorted order, the segments one entity at a time) so the 170 MB
    string, and its UTF-8 copy, never exist."""
    segments = record.get("segments") or {}
    h = hashlib.sha256()

    def feed(text: str) -> None:
        h.update(text.encode("utf-8"))

    feed("{")
    feed('"aux_paths":' + _canonical(record.get("aux_paths") or {}))
    feed(',"node_series":' + _canonical(record.get("node_series") or {}))
    feed(',"reassignment_accounting":' + _canonical(
        record.get("reassignment_accounting") or {}))
    feed(',"segments":')
    if all(type(k) is str for k in segments):
        feed("{")
        for i, eid in enumerate(sorted(segments)):
            feed(("" if i == 0 else ",") + _canonical(eid) + ":"
                 + _canonical(segments[eid]))
        feed("}")
    else:  # not a stored record: one-shot, same text
        feed(_canonical(segments))
    feed("}")
    return h.hexdigest()


def record_bytes_sha256(record: dict) -> str:
    """SHA-256 of the exact bytes SimStore.save writes for a record
    (written through the same chunked writer, to a hash)."""
    sink = NullSink()
    sha, _size, _sections, _segments = write_record(sink, record)
    return sha


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------
# Route mounting.
# ---------------------------------------------------------------------

def mount_sim_loop(app, ctx) -> None:
    """Register the loop routes on `app`. `ctx` carries what lives in
    app.py's closure: graph, _err, _sim_summary, SIM_TICK_SECONDS,
    parse/fmt, validate_disruption, EngineError, derive_sim_run_id,
    build_record (called late, so it can be patched), SimTierContractError
    and the registry bound. Must be called BEFORE the
    /sim/run/{sim_run_id:path} catch-all route is registered."""
    if app.state.sim_store is None:
        return
    store = app.state.sim_store
    graph = ctx.graph
    err = ctx.err
    sim_dir = Path(app.state.sim_runs_dir)
    cache_dir = sim_dir / "_cache"
    app.state.sim_cache_dir = cache_dir
    lineage = LineageStore(sim_dir)
    app.state.sim_lineage = lineage
    verify_jobs: dict = {}
    verify_lock = threading.Lock()
    app.state.sim_verify_jobs = verify_jobs
    stream_memo: dict = {}
    compare_memo: dict = {}
    measure_memo: dict = {}

    # -- per-run sidecar metadata, written by SimStore.save ----------
    meta_dir = sim_dir / "_meta"
    verify_dir = sim_dir / "_verify"

    def _atomic_json(path: Path, value) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        tmp.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
        tmp.replace(path)

    def _write_meta(record: dict) -> None:
        try:
            p = meta_dir / f"{record['sim_run_id']}.json"
            if not p.exists():
                _atomic_json(p, {
                    "sim_run_id": record["sim_run_id"],
                    "rule_version": rule_version(),
                    "created_utc": datetime.now(timezone.utc).strftime(
                        "%Y-%m-%dT%H:%M:%SZ")})
        except OSError:
            pass
        # The comparison's per-run facts, from the record we
        # already hold, so a later compare never has to load it.
        try:
            _write_facts(record["sim_run_id"], measure_facts(record))
        except Exception:
            pass
        # The verify route's stream hash, off the build's critical
        # path: a background thread hashes the record we still hold, so
        # the first GET /verify of this run does not parse it again.
        threading.Thread(target=_write_stream_sidecar, args=(record,),
                         daemon=True, name="sim-stream-hash").start()

    def _write_stream_sidecar(record: dict) -> None:
        try:
            sim_run_id = record["sim_run_id"]
            idx = store.index(sim_run_id)
            if idx is None:
                return
            _atomic_json(_stream_path(sim_run_id), {
                "record_sha256": idx["sha256"],
                "stream_sha256": stream_sha256(record)})
        except Exception:
            pass

    store.on_saved = _write_meta

    def _read_json(path: Path):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    # Sidecars keyed by the record's SHA-256 (from the store's byte
    # index): they hold values that are pure functions of the record's
    # bytes, so they can be reused without loading a 75-177 MB record
    # and are ignored the moment the file on disk is not the one they
    # were computed from.
    def _facts_path(sim_run_id: str) -> Path:
        return meta_dir / f"{sim_run_id}.measures.json"

    def _read_facts(sim_run_id: str):
        idx = store.index(sim_run_id)
        got = _read_json(_facts_path(sim_run_id)) if idx else None
        if isinstance(got, dict) and idx is not None \
                and got.get("record_sha256") == idx["sha256"] \
                and isinstance(got.get("facts"), dict) \
                and got["facts"].get("v") == 1:
            return got["facts"]
        return None

    def _write_facts(sim_run_id: str, facts: dict) -> None:
        idx = store.index(sim_run_id)
        if idx is None:
            return
        try:
            _atomic_json(_facts_path(sim_run_id),
                         {"record_sha256": idx["sha256"], "facts": facts})
        except OSError:
            pass

    def _facts_of(record: dict) -> dict:
        sim_run_id = record["sim_run_id"]
        facts = _read_facts(sim_run_id)
        if facts is None:
            facts = measure_facts(record)
            # A run saved without a byte index: build it now
            # (only if re-encoding reproduces the file), so this run is
            # lazy from here on.
            store.ensure_index(record)
            _write_facts(sim_run_id, facts)
        return facts

    def _meta(sim_run_id: str) -> dict:
        return _read_json(meta_dir / f"{sim_run_id}.json") or {}

    # -- job registry helpers (same registry as POST /sim/run) -------
    def _now_iso() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _evict_finished_locked() -> None:
        finished = [k for k, v in app.state.sim_jobs.items()
                    if v.get("status") != "building"]
        excess = len(finished) - ctx.registry_finished_max
        for key in finished[:max(0, excess)]:
            del app.state.sim_jobs[key]

    def _finish(sim_run_id: str, status: str, error) -> None:
        with app.state.sim_jobs_lock:
            entry = app.state.sim_jobs.pop(sim_run_id, None) \
                or {"started_at": None}
            entry["status"] = status
            entry["error"] = error
            app.state.sim_jobs[sim_run_id] = entry
            _evict_finished_locked()

    def _is_building(sim_run_id: str) -> bool:
        with app.state.sim_jobs_lock:
            entry = app.state.sim_jobs.get(sim_run_id)
            return entry is not None and entry.get("status") == "building"

    def _load(sim_run_id: str):
        record = store.load(sim_run_id)
        if record is not None:
            return record, None
        if _is_building(sim_run_id):
            return None, err("sim_run_not_ready",
                             f"sim run {sim_run_id} is still building")
        return None, err("unknown_sim_run",
                         f"unknown sim_run_id: {sim_run_id!r}")

    def _probe(sim_run_id: str):
        """None when the run is stored, else the standard refusal
        (still building / unknown) - without loading the record."""
        if store.exists(sim_run_id):
            return None
        if _is_building(sim_run_id):
            return err("sim_run_not_ready",
                       f"sim run {sim_run_id} is still building")
        return err("unknown_sim_run", f"unknown sim_run_id: {sim_run_id!r}")

    def _parts(sim_run_id: str, keys):
        """({key: value}, None) for the named top-level record keys,
        read from the store's byte index when there is one (no record
        load), else cut from the loaded record; (None, response) when
        the run is not available."""
        got = store.read_sections(sim_run_id, keys)
        if got is not None:
            return got, None
        record, resp = _load(sim_run_id)
        if resp is not None:
            return None, resp
        store.ensure_index(record)
        return {k: record.get(k) for k in keys}, None

    def _build_worker(sim_run_id, journal_core, snapshot, weather_reason):
        """The build, then its memory goes back to the OS once the build function has returned (api/memtrim.py)."""
        try:
            _build_job(sim_run_id, journal_core, snapshot, weather_reason)
        finally:
            release_free_memory()

    def _build_job(sim_run_id, journal_core, snapshot, weather_reason):
        try:
            # A cold build is the memory peak: free the parsed-record cache.
            store.clear_cache()
            record = ctx.build_record(graph.raw, graph.hash, graph.version,
                                      journal_core, snapshot, weather_reason,
                                      cache_dir)
            record["sim_run_id"] = sim_run_id
            store.save(record)
        except ctx.tier_error as exc:
            _finish(sim_run_id, "failed",
                    {"code": "derivation_error", "message": scrub_paths(exc)})
            return
        except MemoryError:
            _finish(sim_run_id, "failed",
                    {"code": "sim_build_failed",
                     "message": ctx.out_of_memory_message})
            return
        except Exception as exc:
            _finish(sim_run_id, "failed",
                    {"code": "sim_build_failed",
                     "message": scrub_paths(exc)})
            return
        _finish(sim_run_id, "complete", None)

    def _start_build(sim_run_id, journal_core, snapshot, weather_reason):
        """Single-flight build on the shared bounded pool. Returns a
        JSONResponse on refusal, or the string 'building'."""
        with app.state.sim_jobs_lock:
            entry = app.state.sim_jobs.get(sim_run_id)
            if entry is not None and entry.get("status") == "building":
                return "building"
            full = store.storage_refusal()
            if full:
                return err("storage_full", full)
            pool = app.state.sim_job_pool
            if not pool.reserve():
                return err("rate_limited",
                           "sim build queue is full; retry once a running "
                           "build completes")
            app.state.sim_jobs.pop(sim_run_id, None)
            app.state.sim_jobs[sim_run_id] = {
                "status": "building", "started_at": _now_iso(),
                "error": None}
        try:
            pool.submit_reserved(lambda: _build_worker(
                sim_run_id, journal_core, snapshot, weather_reason))
        except Exception:
            _finish(sim_run_id, "failed",
                    {"code": "sim_build_failed",
                     "message": "sim build could not be queued"})
            return err("sim_build_failed", "sim build could not be queued")
        return "building"

    def _err_status(code: str, status: int, message: str):
        return JSONResponse(status_code=status, content={
            "error": {"code": code, "message": message}})

    def _tick_s(journal: dict) -> int:
        return ctx.tick_seconds[journal["tick"]]

    # -- POST /sim/run/{id}/fork ------------------------------------
    @app.post("/sim/run/{sim_run_id}/fork")
    async def post_sim_fork(sim_run_id: str, req: Request):
        try:
            body = await req.json()
        except Exception:
            return err("bad_json", "request body must be JSON")
        if not isinstance(body, dict):
            return err("invalid_request", "request body must be an object")
        # Loading a stored record is seconds of JSON work: off the loop.
        return await run_in_threadpool(_fork, sim_run_id, body)

    def _fork(sim_run_id: str, body: dict):
        fixed = sorted(set(body) & set(_FORK_FIXED))
        if fixed:
            return err("invalid_request",
                       f"a fork keeps its parent's settings; {fixed} cannot "
                       "be changed")
        extra = sorted(set(body) - {"at_tick", "interventions", "remove"})
        if extra:
            return err("invalid_request", f"unknown fork keys: {extra}")
        if "at_tick" not in body:
            return err("invalid_request", "at_tick is required")
        # Only the recipe is needed to start a fork's build: read those
        # byte ranges, not the 75-177 MB record (the summary branches
        # below load it, they are rare).
        parent, resp = _parts(sim_run_id,
                              ["journal", "weather", "weather_reason"])
        if resp is not None:
            return resp
        pj = parent["journal"]
        if pj["graph_hash"] != graph.hash:
            return _err_status(
                "graph_mismatch", 409,
                "run was built on a different graph than the one this "
                "server serves")
        ticks = int(pj["ticks"])
        tick_s = _tick_s(pj)
        start_epoch = ctx.parse_ts(pj["start_at"])
        try:
            at = _at_tick(body["at_tick"], "at_tick", 0, ticks)
            new_items = parse_interventions(
                body.get("interventions", []), graph=graph, ticks=ticks,
                start_epoch=start_epoch, tick_s=tick_s, fmt_ts=ctx.fmt_ts,
                validate_disruption=ctx.validate_disruption,
                engine_error=ctx.engine_error, min_tick=at)
            remove = body.get("remove", [])
            if not isinstance(remove, list):
                raise SimLoopRefused("invalid_request",
                                     "remove must be a list of seq numbers")
            core = merge_fork_journal(pj, at, new_items, remove,
                                      start_epoch, tick_s, ctx.fmt_ts)
        except SimLoopRefused as exc:
            return err(exc.code, exc.message)
        child_id = ctx.derive_sim_run_id(core)
        n_iv = len(journal_interventions(core))
        extra_out = {"parent_id": sim_run_id, "forked_at_tick": at,
                     "provenance": PROVENANCE}
        if child_id == sim_run_id:
            whole, resp = _load(sim_run_id)
            if resp is not None:
                return resp
            return JSONResponse(status_code=201, content={
                "sim_run_id": sim_run_id, "status": "ready",
                "summary": ctx.sim_summary(whole), **extra_out,
                "unchanged": True})
        if store.exists(child_id):
            lineage.append(child_id, sim_run_id, at, n_iv)
            rec = store.load(child_id)
            return JSONResponse(status_code=201, content={
                "sim_run_id": child_id, "status": "ready",
                "summary": ctx.sim_summary(rec), **extra_out})
        started = _start_build(child_id, core, parent.get("weather"),
                               parent.get("weather_reason"))
        if not isinstance(started, str):
            return started
        lineage.append(child_id, sim_run_id, at, n_iv)
        return JSONResponse(status_code=202, content={
            "sim_run_id": child_id, "status": "building", **extra_out})

    # -- GET /sim/run/{id}/journal ----------------------------------
    def _place_ids(kind: str, ent: dict) -> list:
        if kind == "disruption":
            return [ent["node_id"]]
        if kind == "airspace":
            return [ent["anchor"]]
        return [ent["from_node"], ent["to_node"]]

    @app.get("/sim/run/{sim_run_id}/journal")
    def get_sim_journal(sim_run_id: str):
        record, resp = _load(sim_run_id)
        if resp is not None:
            return resp
        j = record["journal"]
        tick_s = _tick_s(j)
        start_epoch = ctx.parse_ts(j["start_at"])
        settings = {k: j[k] for k in ("graph_hash", "graph_version", "tick",
                                      "ticks", "seed", "start_at",
                                      "weather_snapshot_hash")}
        if j.get("lane_geometry"):
            # opt-in, journaled: echoed so a fork or a replay can see which geometry it rides on
            settings["lane_geometry"] = j["lane_geometry"]
        rows = []
        n = 0
        for key, val in (("SEED", j["seed"]), ("TICK", j["tick"]),
                         ("TICKS", j["ticks"]), ("START", j["start_at"]),
                         ("WEATHER", "PINNED" if j["weather_snapshot_hash"]
                          else "OFF")):
            n += 1
            rows.append({"n": n, "tick": 0, "kind": "SETTING",
                         "setting": key, "value": val})
        shocks = []
        for kind, lst in _JOURNAL_LISTS.items():
            for ent in j.get(lst) or []:
                meta = ent.get("intervention")
                started = ctx.parse_ts(ent["started_at"]) \
                    if ent.get("started_at") else start_epoch
                tick = int(math.floor((started - start_epoch) / tick_s))
                dur = ent.get("duration_d")
                row = {"kind": "SHOCK", "shock": kind,
                       "place_ids": _place_ids(kind, ent),
                       "sim_id": ent.get("sim_id"),
                       "source": "INJECTED" if meta else "RUN",
                       "seq": meta["seq"] if meta else None,
                       "tick": meta["at_tick"] if meta else tick,
                       "duration_d": dur, "spec": {
                           k: v for k, v in ent.items()
                           if k not in ("intervention", "known_from")}}
                if kind == "disruption":
                    row["capacity_loss"] = ent.get("capacity_loss")
                    row["disruption_kind"] = ent.get("kind")
                else:
                    row["radius_km"] = ent.get("radius_km")
                shocks.append(row)
        shocks.sort(key=lambda r: (r["tick"], r["source"] != "RUN",
                                   r["seq"] or 0, r["sim_id"] or ""))
        for r in shocks:
            n += 1
            r["n"] = n
            rows.append(r)
        edge = next((e for e in lineage.edges()
                     if e["child_id"] == sim_run_id), None)
        replay_body = {"disruptions": [
            {k: v for k, v in d.items()
             if k in ("node_id", "kind", "capacity_loss", "duration_d",
                      "started_at", "shape")}
            for d in j.get("disruptions") or []
            if not isinstance(d.get("intervention"), dict)],
            "tick": j["tick"], "ticks": j["ticks"], "seed": j["seed"],
            "start_at": j["start_at"],
            "weather": j["weather_snapshot_hash"] is not None}
        if j.get("lane_geometry"):
            replay_body["lane_geometry"] = True
        for key in ("airspace", "storm_cells"):
            base = [{k: v for k, v in e.items()
                     if k not in ("sim_id", "known_from", "intervention")}
                    for e in j.get(key) or []
                    if not isinstance(e.get("intervention"), dict)]
            if base:
                replay_body[key] = base
        ivs = journal_interventions(j)
        if ivs:
            replay_body["interventions"] = [
                {"kind": e["kind"], "at_tick": e["at_tick"],
                 "spec": e["spec"]} for e in ivs]
        return {
            "sim_run_id": sim_run_id, "provenance": PROVENANCE,
            "lineage": {"parent_id": edge["parent_id"] if edge else None,
                        "forked_at_tick": edge["at_tick"] if edge else None},
            "settings": settings, "rows": rows,
            "interventions": [{"seq": e["seq"], "kind": e["kind"],
                               "at_tick": e["at_tick"], "spec": e["spec"],
                               "sim_id": e["sim_id"]} for e in ivs],
            "replay_body": replay_body,
        }

    # -- GET /sim/runs/lineage --------------------------------------
    @app.get("/sim/runs/lineage")
    def get_sim_lineage(req: Request):
        root = req.query_params.get("root")
        if not root:
            return err("invalid_request", "root query parameter is required")
        if not store.exists(root) and not _is_building(root):
            return err("unknown_sim_run", f"unknown sim_run_id: {root!r}")
        edges = lineage.edges()
        children: dict = {}
        parents: dict = {}
        for e in edges:
            children.setdefault(e["parent_id"], []).append(e)
            parents.setdefault(e["child_id"], e)

        def status_of(rid: str) -> str:
            if store.exists(rid):
                return "ready"
            with app.state.sim_jobs_lock:
                entry = app.state.sim_jobs.get(rid)
            if entry is not None:
                return entry.get("status", "unknown")
            return "missing"

        ancestors, seen_up = [], {root}
        cur = root
        while cur in parents:
            cur = parents[cur]["parent_id"]
            if cur in seen_up:
                break
            seen_up.add(cur)
            ancestors.append(cur)
        runs = [{"sim_run_id": root,
                 "parent_id": parents[root]["parent_id"]
                 if root in parents else None,
                 "forked_at_tick": parents[root]["at_tick"]
                 if root in parents else None,
                 "status": status_of(root),
                 "created_utc": parents[root]["created_utc"]
                 if root in parents else None,
                 "n_interventions": parents[root].get("n_interventions")
                 if root in parents else None}]
        out_edges = []
        queue, visited = [root], {root}
        while queue:
            cur = queue.pop(0)
            for e in sorted(children.get(cur, []),
                            key=lambda x: (x["at_tick"], x["child_id"])):
                out_edges.append({"parent_id": e["parent_id"],
                                  "child_id": e["child_id"],
                                  "forked_at_tick": e["at_tick"]})
                if e["child_id"] in visited:
                    continue
                visited.add(e["child_id"])
                runs.append({"sim_run_id": e["child_id"],
                             "parent_id": e["parent_id"],
                             "forked_at_tick": e["at_tick"],
                             "status": status_of(e["child_id"]),
                             "created_utc": e.get("created_utc"),
                             "n_interventions": e.get("n_interventions")})
                queue.append(e["child_id"])
        return {"root": root, "provenance": PROVENANCE,
                "ancestors": ancestors, "runs": runs, "edges": out_edges}

    # -- GET /sim/compare -------------------------------------------
    def _compare_lazy(a_id: str, b_id: str, ja: dict, nodes_meta):
        """compare_records without loading either record: entities whose
        stored text is identical in both files are equal and are never
        parsed; only the differing entities, the node series and the
        stored measure facts are read. None when either run lacks the
        byte index or the facts (the caller then loads whole records,
        and stores the facts for next time)."""
        fa, fb = _read_facts(a_id), _read_facts(b_id)
        if fa is None or fb is None:
            return None
        va = store.segment_view(a_id)
        vb = store.segment_view(b_id)
        try:
            if va is None or vb is None:
                return None
            sa = store.read_sections(a_id, ["node_series",
                                            "sim_delay_by_node"])
            sb = store.read_sections(b_id, ["node_series",
                                            "sim_delay_by_node"])
            if sa is None or sb is None:
                return None
            diff_a, diff_b = {}, {}
            for eid in sorted(set(va.eids()) | set(vb.eids())):
                blob_a, blob_b = va.blob(eid), vb.blob(eid)
                if blob_a == blob_b:
                    continue
                if blob_a is not None:
                    diff_a[eid] = json.loads(blob_a)
                if blob_b is not None:
                    diff_b[eid] = json.loads(blob_b)
            diff = _first_event_difference(diff_a, diff_b)

            def entity_mode(eid):
                got = store.read_sections(a_id, ["derivation"])
                if got is None:
                    raise ValueError("derivation unreadable")
                return next((e["mode"] for e in got["derivation"]["entities"]
                             if e["id"] == eid), None)

            return assemble_compare(
                ja, diff, entity_mode,
                sa["node_series"] or {}, sb["node_series"] or {},
                measures_from_facts(fa, sa["sim_delay_by_node"] or {},
                                    nodes_meta),
                measures_from_facts(fb, sb["sim_delay_by_node"] or {},
                                    nodes_meta), nodes_meta)
        except (ValueError, OSError):
            return None
        finally:
            for view in (va, vb):
                if view is not None:
                    view.close()

    @app.get("/sim/compare")
    def get_sim_compare(req: Request):
        a_id = req.query_params.get("a")
        b_id = req.query_params.get("b")
        if not a_id or not b_id:
            return err("invalid_request",
                       "query parameters a and b (sim_run_id) are required")
        for rid in (a_id, b_id):
            resp = _probe(rid)
            if resp is not None:
                return resp
        parts_a, resp = _parts(a_id, ["journal"])
        if resp is not None:
            return resp
        parts_b, resp = _parts(b_id, ["journal"])
        if resp is not None:
            return resp
        ja, jb = parts_a["journal"], parts_b["journal"]
        bad = comparable_reason(ja, jb)
        if bad:
            return err("runs_not_comparable",
                       f"runs differ in {bad}; compare needs the same graph, "
                       "tick, ticks, start_at, seed and weather snapshot")
        nodes_meta = graph.nodes if ja["graph_hash"] == graph.hash \
            else None
        # Records are immutable per id, so the comparison is a pure
        # function of the two ids (and of whether the graph is the
        # run's): memoise it, and each run's measures separately (a
        # root is compared against many forks).
        key = (a_id, b_id, nodes_meta is not None)
        result = compare_memo.get(key)
        if result is None:
            result = _compare_lazy(a_id, b_id, ja, nodes_meta)
            if result is None:  # no byte index / facts: whole records
                ra, resp = _load(a_id)
                if resp is not None:
                    return resp
                rb, resp = _load(b_id)
                if resp is not None:
                    return resp
                result = compare_records(ra, rb, nodes_meta, measure_memo,
                                         _facts_of)
            compare_memo[key] = result
            while len(compare_memo) > 16:
                compare_memo.pop(next(iter(compare_memo)))
        rel = None
        for e in lineage.edges():
            if e["parent_id"] == a_id and e["child_id"] == b_id:
                rel = {"relation": "B_IS_FORK_OF_A",
                       "forked_at_tick": e["at_tick"]}
            elif e["parent_id"] == b_id and e["child_id"] == a_id:
                rel = {"relation": "A_IS_FORK_OF_B",
                       "forked_at_tick": e["at_tick"]}
        return {"a": a_id, "b": b_id, "lineage": rel, **result}

    # -- verify ------------------------------------------------------
    def _stream_path(sim_run_id: str) -> Path:
        return verify_dir / f"{sim_run_id}.stream.json"

    def _hashes(sim_run_id: str) -> dict:
        h = stream_memo.get(sim_run_id)
        if h is None:
            record_sha = file_sha256(store._path(sim_run_id))
            # The stream hash is a pure function of the record's bytes:
            # kept beside the verify result, valid only for this file.
            got = _read_json(_stream_path(sim_run_id))
            if isinstance(got, dict) and got.get("record_sha256") == record_sha \
                    and isinstance(got.get("stream_sha256"), str):
                stream = got["stream_sha256"]
            else:
                record, resp = _load(sim_run_id)
                if resp is not None:
                    raise ValueError("run is not available")
                stream = stream_sha256(record)
                try:
                    _atomic_json(_stream_path(sim_run_id), {
                        "record_sha256": record_sha,
                        "stream_sha256": stream})
                except OSError:
                    pass
            h = {"stream_sha256": stream, "record_sha256": record_sha}
            stream_memo[sim_run_id] = h
            while len(stream_memo) > 64:
                stream_memo.pop(next(iter(stream_memo)))
        return h

    def _verify_payload(sim_run_id: str) -> dict:
        h = _hashes(sim_run_id)
        stored = _read_json(verify_dir / f"{sim_run_id}.json")
        check, checked_utc, detail = "NOT_CHECKED", None, None
        if stored and stored.get("record_sha256") == h["record_sha256"]:
            check = stored["replay_check"]
            checked_utc = stored.get("checked_utc")
            detail = stored.get("detail")
        with verify_lock:
            job = dict(verify_jobs.get(sim_run_id) or {})
        return {
            "sim_run_id": sim_run_id, "provenance": PROVENANCE,
            "stream_sha256": h["stream_sha256"],
            "record_sha256": h["record_sha256"],
            "rule_version": rule_version(),
            "stored_rule_version": _meta(sim_run_id).get("rule_version"),
            "replay_check": check, "checked_utc": checked_utc,
            "replay_detail": detail,
            "replay_job": ({"status": job["status"],
                            "started_at": job.get("started_at"),
                            "error": job.get("error")} if job else None),
        }

    @app.get("/sim/run/{sim_run_id}/verify")
    def get_sim_verify(sim_run_id: str):
        resp = _probe(sim_run_id)
        if resp is not None:
            return resp
        return _verify_payload(sim_run_id)

    def _replay_worker(sim_run_id: str, parts: dict) -> None:
        try:
            rebuilt = ctx.build_record(
                graph.raw, graph.hash, graph.version, parts["journal"],
                parts.get("weather"), parts.get("weather_reason"))
            rebuilt["sim_run_id"] = sim_run_id
            h = _hashes(sim_run_id)
            re_stream = stream_sha256(rebuilt)
            re_record = record_bytes_sha256(rebuilt)
            stream_ok = re_stream == h["stream_sha256"]
            record_ok = re_record == h["record_sha256"]
            result = {
                "replay_check": "MATCH" if record_ok else "MISMATCH",
                "record_sha256": h["record_sha256"],
                "checked_utc": _now_iso(),
                "rule_version": rule_version(),
                "detail": {"stream_match": stream_ok,
                           "record_match": record_ok,
                           "replay_stream_sha256": re_stream,
                           "replay_record_sha256": re_record}}
            verify_dir.mkdir(parents=True, exist_ok=True)
            p = verify_dir / f"{sim_run_id}.json"
            tmp = p.with_name(f"{p.name}.{uuid.uuid4().hex}.tmp")
            tmp.write_text(json.dumps(result, sort_keys=True),
                           encoding="utf-8")
            tmp.replace(p)
            status, error = "complete", None
        except Exception as exc:
            status, error = "failed", {"code": "replay_failed",
                                       "message": scrub_paths(exc)}
        with verify_lock:
            entry = verify_jobs.get(sim_run_id) or {}
            entry.update(status=status, error=error)
            verify_jobs[sim_run_id] = entry

    @app.post("/sim/run/{sim_run_id}/verify", status_code=202)
    def post_sim_verify(sim_run_id: str):
        parts, resp = _parts(sim_run_id,
                             ["journal", "weather", "weather_reason"])
        if resp is not None:
            return resp
        if parts["journal"]["graph_hash"] != graph.hash:
            return _err_status(
                "graph_mismatch", 409,
                "run was built on a different graph than the one this "
                "server serves; replay is not possible")
        with verify_lock:
            entry = verify_jobs.get(sim_run_id)
            if entry is not None and entry.get("status") == "building":
                return JSONResponse(status_code=202, content={
                    "sim_run_id": sim_run_id, "status": "building"})
            pool = app.state.sim_job_pool
            if not pool.reserve():
                return err("rate_limited",
                           "sim build queue is full; retry once a running "
                           "build completes")
            verify_jobs[sim_run_id] = {"status": "building",
                                       "started_at": _now_iso(),
                                       "error": None}
        try:
            pool.submit_reserved(lambda: _replay_worker(sim_run_id, parts))
        except Exception:
            with verify_lock:
                verify_jobs[sim_run_id] = {
                    "status": "failed", "started_at": _now_iso(),
                    "error": {"code": "replay_failed",
                              "message": "replay could not be queued"}}
            return err("sim_build_failed", "replay could not be queued")
        return JSONResponse(status_code=202, content={
            "sim_run_id": sim_run_id, "status": "building"})
