"""Timeline markers for a stored sim run.

Markers are a PURE, deterministic function of stored facts — the sim
run record (journal + event segments) plus, for ``chain_event`` only,
records read from the existing chain run store. Nothing here simulates,
writes, or invents: no marker exists without a stored fact behind it,
and the same record (in any process) yields byte-identical markers.

Kinds:
  disruption_onset / disruption_expiry — the journal's disruption
    windows, verbatim: onset at the window's ``started_at`` (or the
    run's ``start_at`` when the spec carries none, mirroring
    ``simulate.disruption_windows``), expiry at start + duration_d.
    Ticks are floor((epoch - start_epoch) / tick_seconds); a window
    that starts before the run (a live event already under way) or
    ends past the final tick is still reported verbatim.
  queue_peak — per node, local maxima of the queued-count series
    reconstructed from the stored segments (arrivals minus berthings
    sampled at tick boundaries — the same accounting
    ``simulate._build_node_series`` performs, re-derived here from
    the segment record so markers depend only on journal + segments).
    A maximal plateau of equal counts is a peak when it is >=
    QUEUE_PEAK_MIN (3) and strictly higher than the counts on both
    sides (run edges count as lower); the marker sits at the
    plateau's first tick and its label carries node + peak count.
  congested_arrival — arrivals that join a NON-EMPTY queue at their
    node. Queue length is tracked per node over the segment events in
    (time, entity id, per-entity event order) — the simulator's own
    FIFO tie-break is entity id. Only arrivals inside the run's span
    [start_at, start_at + ticks x tick_seconds) count (pre-roll
    spin-up is derivation, not timeline). Capped at CONGESTED_CAP
    (50), keeping the largest by queue-joined length (the queue
    length including the arrival), ties broken by (time, node,
    entity); the caller surfaces ``markers_truncated`` when the cap
    bites.
  chain_event — ONLY for a live disruption the sim run and a chain run
    share. A chain-run record matches when (a) its journal's
    ``live_disruption_id`` (a fork's parent journal counts) is named
    explicitly by the sim journal (a disruption's ``disruption_id`` /
    ``live_disruption_id``, or a journal-level ``live_disruption_id``
    / ``live_disruption_ids``), or (b) it carries a
    ``live_disruption_id`` AND its journal disruption spec equals a
    sim disruption spec on (node_id, kind, capacity_loss, duration_d,
    started_at) — the two lenses' shared input — or (c) the sim
    journal names a live id explicitly AND the specs are equal. A
    scenario-vs-scenario spec coincidence never links. Each matched
    run's stored chain events become markers with ``ref`` = the chain
    run id. Absence is normal; chain data is never synthesised and
    the chain store is only ever read.

Final order is deterministic: (tick, kind, node_id, label, ref).
"""
from __future__ import annotations

import math

from .simulate import disruption_windows, fmt_ts, parse_ts
from .state import TICK_SECONDS

QUEUE_PEAK_MIN = 3
CONGESTED_CAP = 50


def _tick_of(epoch: float, start_epoch: float, tick_s: int) -> int:
    return int(math.floor((epoch - start_epoch) / tick_s))


def _place(node, names: dict | None) -> str:
    """Display name for a place id; the id itself when no name is known."""
    if names and isinstance(node, str):
        return names.get(node) or node
    return str(node)


def _disruption_markers(journal: dict, start_epoch: float,
                        tick_s: int, names: dict | None = None) -> list[dict]:
    out = []
    for w in disruption_windows(journal["disruptions"], start_epoch):
        node = w["node_id"]
        sim = w.get("sim_id")
        suffix = f" ({sim})" if sim else ""
        out.append({
            "tick": _tick_of(w["start"], start_epoch, tick_s),
            "at": fmt_ts(w["start"]), "kind": "disruption_onset",
            "label": f"Disruption onset at {_place(node, names)}{suffix}",
            "node_id": node, "ref": None})
        out.append({
            "tick": _tick_of(w["end"], start_epoch, tick_s),
            "at": fmt_ts(w["end"]), "kind": "disruption_expiry",
            "label": f"Disruption expiry at {_place(node, names)}{suffix}",
            "node_id": node, "ref": None})
    return out


def _node_events(record: dict) -> dict[str, list[tuple]]:
    """Per node, segment events as (t, entity_id, per-entity index,
    kind) for arrive/berth — the only kinds that move a queue."""
    per_node: dict[str, list[tuple]] = {}
    segments = record.get("segments") or {}
    for eid in sorted(segments):
        for idx, ev in enumerate(segments[eid]):
            if ev["kind"] in ("arrive", "berth"):
                per_node.setdefault(ev["node"], []).append(
                    (float(ev["t"]), eid, idx, ev["kind"]))
    for evs in per_node.values():
        evs.sort(key=lambda e: (e[0], e[1], e[2]))
    return per_node


def _queue_series(evs: list[tuple], start_epoch: float, tick_s: int,
                  ticks: int) -> list[int]:
    """Queued count at each tick boundary: arrivals minus berthings
    with event time <= the tick's time (pre-roll events included —
    they are why tick 0 can open with a queue)."""
    series = []
    queue = 0
    i = 0
    for k in range(ticks):
        tick_t = start_epoch + k * tick_s
        while i < len(evs) and evs[i][0] <= tick_t:
            queue += 1 if evs[i][3] == "arrive" else -1
            i += 1
        series.append(queue)
    return series


def _queue_peak_markers(record: dict, per_node: dict, start_epoch: float,
                        tick_s: int, ticks: int,
                        names: dict | None = None) -> list[dict]:
    out = []
    for node in sorted(per_node):
        q = _queue_series(per_node[node], start_epoch, tick_s, ticks)
        i = 0
        while i < len(q):
            j = i
            while j + 1 < len(q) and q[j + 1] == q[i]:
                j += 1
            v = q[i]
            left = q[i - 1] if i > 0 else None
            right = q[j + 1] if j + 1 < len(q) else None
            if v >= QUEUE_PEAK_MIN \
                    and (left is None or left < v) \
                    and (right is None or right < v):
                out.append({
                    "tick": i, "at": fmt_ts(start_epoch + i * tick_s),
                    "kind": "queue_peak",
                    "label": f"Queue peak {v} at {_place(node, names)}",
                    "node_id": node, "ref": None})
            i = j + 1
    return out


def _congested_arrivals(per_node: dict, start_epoch: float,
                        tick_s: int, ticks: int,
                        names: dict | None = None) -> tuple[list[dict], bool]:
    horizon = start_epoch + tick_s * ticks
    candidates: list[tuple] = []  # (-joined, t, node, eid, marker)
    for node in sorted(per_node):
        queue = 0
        for t, eid, _idx, kind in per_node[node]:
            if kind == "berth":
                queue -= 1
                continue
            joined = queue + 1
            if queue > 0 and start_epoch <= t < horizon:
                tick = _tick_of(t, start_epoch, tick_s)
                marker = {
                    "tick": tick, "at": fmt_ts(t),
                    "kind": "congested_arrival",
                    "label": f"Congested arrival {eid} at {_place(node, names)} "
                             f"(queue {joined})",
                    "node_id": node, "ref": None}
                candidates.append((-joined, t, node, eid, marker))
            queue += 1
    truncated = len(candidates) > CONGESTED_CAP
    if truncated:
        candidates.sort(key=lambda c: (c[0], c[1], c[2], c[3]))
        candidates = candidates[:CONGESTED_CAP]
    return [c[4] for c in candidates], truncated


def _spec_key(spec: dict) -> tuple:
    def num(v):
        return float(v) if isinstance(v, (int, float)) \
            and not isinstance(v, bool) else v
    return (spec.get("node_id"), spec.get("kind"),
            num(spec.get("capacity_loss")), num(spec.get("duration_d")),
            spec.get("started_at"))


def _chain_live_id(chain_journal: dict):
    lid = chain_journal.get("live_disruption_id")
    if lid:
        return lid
    parent = chain_journal.get("parent_journal") or {}
    return parent.get("live_disruption_id")


def _sim_live_ids(journal: dict) -> set:
    ids = set()
    for d in journal.get("disruptions") or []:
        for key in ("disruption_id", "live_disruption_id"):
            if isinstance(d.get(key), str):
                ids.add(d[key])
    if isinstance(journal.get("live_disruption_id"), str):
        ids.add(journal["live_disruption_id"])
    for v in journal.get("live_disruption_ids") or []:
        if isinstance(v, str):
            ids.add(v)
    return ids


def _chain_matches(journal: dict, chain_record: dict) -> bool:
    cj = chain_record.get("journal") or {}
    live_id = _chain_live_id(cj)
    sim_ids = _sim_live_ids(journal)
    if live_id and live_id in sim_ids:
        return True
    cspec = cj.get("disruption")
    if not isinstance(cspec, dict):
        parent = cj.get("parent_journal") or {}
        cspec = parent.get("disruption")
    if not isinstance(cspec, dict):
        return False
    ckey = _spec_key(cspec)
    if ckey[0] is None:
        return False
    spec_equal = any(_spec_key(d) == ckey
                     for d in journal.get("disruptions") or [])
    if not spec_equal:
        return False
    # Spec equality links the two lenses only when a live linkage is
    # recorded on at least one side (see module docstring).
    return bool(live_id) or bool(sim_ids)


def _chain_markers(journal: dict, chain_runs: list[dict],
                   start_epoch: float, tick_s: int,
                   names: dict | None = None) -> list[dict]:
    out = []
    matched = [r for r in chain_runs if _chain_matches(journal, r)]
    for rec in sorted(matched, key=lambda r: r.get("run_id") or ""):
        run_id = rec.get("run_id")
        for ev in rec.get("events") or []:
            at = ev.get("at")
            if not isinstance(at, str):
                continue
            try:
                epoch = parse_ts(at)
            except (ValueError, TypeError):
                continue
            node = ev.get("node_id")
            out.append({
                "tick": _tick_of(epoch, start_epoch, tick_s),
                "at": fmt_ts(epoch), "kind": "chain_event",
                "label": f"Chain event at {_place(node, names)} via {ev.get('channel')} "
                         f"(delay {ev.get('delay_d')}d)",
                "node_id": node, "ref": run_id})
    return out


def compute_markers(record: dict, chain_runs: list[dict] | tuple = (),
                    names: dict | None = None) -> dict:
    """Markers for one stored sim record. ``chain_runs`` are chain run
    records (read-only dicts as loaded from the run store); the result
    is {"markers": [...], "markers_truncated": bool} with markers in
    the deterministic (tick, kind, node_id, label, ref) order. ``names``
    maps place ids to display names for the label text (``node_id`` stays
    the id); without it labels carry the id."""
    journal = record["journal"]
    tick_s = TICK_SECONDS[journal["tick"]]
    ticks = int(journal["ticks"])
    start_epoch = parse_ts(journal["start_at"])
    per_node = _node_events(record)
    markers = _disruption_markers(journal, start_epoch, tick_s, names)
    markers += _queue_peak_markers(record, per_node, start_epoch,
                                   tick_s, ticks, names)
    congested, truncated = _congested_arrivals(per_node, start_epoch,
                                               tick_s, ticks, names)
    markers += congested
    markers += _chain_markers(journal, list(chain_runs), start_epoch,
                              tick_s, names)
    markers.sort(key=lambda m: (m["tick"], m["kind"],
                                m["node_id"] or "", m["label"],
                                m["ref"] or ""))
    return {"markers": markers, "markers_truncated": truncated}
