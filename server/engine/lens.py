"""Commodity lenses (engine side).

A lens is a FILTER / VIEW over existing runs and ensemble outputs —
never a change to propagation. This module is pure and deterministic:
no RNG, no clocks, no mutation of its inputs, and nothing here is ever
fed back into the engine.

Definitions live in ``lenses.json`` next to this module. Each lens is::

    {"lens_id": str, "label": str, "node_ids": [str, ...], "basis": str}

``node_ids`` are real node ids from ``data/graph/graph.json``.
``basis`` states, in plain public terms, why those nodes belong to the
commodity — every basis is explicitly labelled "curated mapping,
MODELED — not a measured flow share". No throughput percentages are
stated in the definitions, and none are computed here.

Public functions (the exact surface the UI chip and any API mount call):

- ``list_lenses()`` — all lens definitions, in file order, deep-copied.
- ``get_lens(lens_id)`` — one definition by id, deep-copied.
- ``apply_lens(run_events, lens)`` — the in-lens sub-stream of an
  event list (see its docstring for the seed rule and the annotations).
- ``lens_ensemble_stats(ensemble_result, lens)`` — per-lens aggregates
  over an ensemble result (see its docstring for the accepted shapes
  and for which fields each shape can honestly supply).

``lens`` arguments accept either a lens definition dict (as returned by
``list_lenses()`` / loaded from ``lenses.json``) or a ``lens_id`` string.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

LENSES_PATH = Path(__file__).resolve().with_name("lenses.json")

MODELED_LABEL = "MODELED — not a measured flow share"


class LensError(ValueError):
    """Malformed lens definition or unknown lens_id."""


# -- definitions -----------------------------------------------------------

def _load_lens_file() -> list[dict]:
    with open(LENSES_PATH, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    lenses = data.get("lenses") if isinstance(data, dict) else None
    if not isinstance(lenses, list) or not lenses:
        raise LensError("lenses.json: 'lenses' must be a non-empty list")
    for lens in lenses:
        _validate_lens(lens)
    return lenses


def _validate_lens(lens) -> dict:
    if not isinstance(lens, dict):
        raise LensError(f"lens must be an object, got {type(lens).__name__}")
    for field_name in ("lens_id", "label", "node_ids", "basis"):
        if field_name not in lens:
            raise LensError(f"lens missing required field {field_name!r}: "
                            f"{lens.get('lens_id')!r}")
    if not isinstance(lens["lens_id"], str) or not lens["lens_id"]:
        raise LensError("lens_id must be a non-empty string")
    if not isinstance(lens["label"], str) or not lens["label"]:
        raise LensError(f"lens {lens['lens_id']!r}: label must be a non-empty string")
    node_ids = lens["node_ids"]
    if (not isinstance(node_ids, list) or not node_ids
            or not all(isinstance(n, str) and n for n in node_ids)):
        raise LensError(f"lens {lens['lens_id']!r}: node_ids must be a "
                        "non-empty list of node id strings")
    if len(set(node_ids)) != len(node_ids):
        raise LensError(f"lens {lens['lens_id']!r}: duplicate node_ids")
    if not isinstance(lens["basis"], str) or MODELED_LABEL not in lens["basis"]:
        raise LensError(f"lens {lens['lens_id']!r}: basis must state the "
                        f"{MODELED_LABEL!r} label")
    return lens


def list_lenses() -> list[dict]:
    """All lens definitions, in ``lenses.json`` order.

    Returns deep copies: mutating the result never affects later calls.
    """
    return copy.deepcopy(_load_lens_file())


def get_lens(lens_id: str) -> dict:
    """One lens definition by ``lens_id`` (deep copy)."""
    for lens in _load_lens_file():
        if lens["lens_id"] == lens_id:
            return copy.deepcopy(lens)
    raise LensError(f"unknown lens_id: {lens_id!r}")


def _resolve_lens(lens) -> dict:
    if isinstance(lens, str):
        return get_lens(lens)
    return _validate_lens(lens)


# -- filtering a run ------------------------------------------------------

def apply_lens(run_events, lens) -> list[dict]:
    """Filter an event stream to one lens. Pure; inputs are not mutated.

    An event is retained when its ``node_id`` is in the lens, OR when it
    is a seed event — a disruption-seeded root, i.e. ``parent_id is
    None`` (the only events allowed a null
    parent). Seed events are always retained, even when the disrupted
    node itself is outside the lens, so a filtered chain never loses
    its origin.

    Retained events keep their original order and every original field,
    and are returned as copies annotated with two added fields:

    - ``in_lens``: ``True`` iff ``node_id`` is in the lens;
    - ``is_seed``: ``True`` iff the event is a seed (``parent_id is None``).
    """
    resolved = _resolve_lens(lens)
    node_set = set(resolved["node_ids"])
    out: list[dict] = []
    for ev in run_events:
        is_seed = ev.get("parent_id") is None
        in_lens = ev.get("node_id") in node_set
        if in_lens or is_seed:
            annotated = dict(ev)
            annotated["in_lens"] = in_lens
            annotated["is_seed"] = is_seed
            out.append(annotated)
    return out


# -- ensemble aggregates ------------------------------------------------------

def _stats_base(resolved: dict, source_shape: str) -> dict:
    return {
        "lens_id": resolved["lens_id"],
        "label": resolved["label"],
        "source_shape": source_shape,
        "variants": None,
        "nodes_hit": [],
        "nodes_hit_count": 0,
        "events_in_lens": None,
        "variant_hits_in_lens": None,
        "events_total": None,
        "seed_events": None,
        "worst_delay_d": None,
        "worst_delay_event": None,
        "per_node": [],
    }


def _ranked_stats(ensemble_result: dict, resolved: dict) -> dict:
    """Aggregates over the existing ``run_ensemble`` output shape:
    ``{"variants": int, "ranked_bottlenecks": [{node_id, hits, variants,
    first_seq_min, first_seq_mean}, ...]}``.

    That shape carries hit COUNTS over the enumerated variant grid — it
    carries no events and no delays. So ``events_in_lens``,
    ``seed_events`` and ``worst_delay_d`` stay ``None`` here rather than
    being fabricated from hit counts (hits are not events: one node can
    emit several events in one run). Pass run events (see
    ``lens_ensemble_stats``) when event counts / worst delay are needed.
    """
    node_set = set(resolved["node_ids"])
    out = _stats_base(resolved, "ranked_bottlenecks")
    out["variants"] = ensemble_result.get("variants")
    per_node = []
    for entry in ensemble_result.get("ranked_bottlenecks") or []:
        if entry.get("node_id") in node_set:
            per_node.append({
                "node_id": entry["node_id"],
                "hits": entry.get("hits"),
                "variants": entry.get("variants", out["variants"]),
                "first_seq_min": entry.get("first_seq_min"),
                "first_seq_mean": entry.get("first_seq_mean"),
            })
    out["per_node"] = per_node  # ranked order of the input is preserved
    out["nodes_hit"] = sorted(p["node_id"] for p in per_node)
    out["nodes_hit_count"] = len(per_node)
    out["variant_hits_in_lens"] = sum(p["hits"] for p in per_node
                                      if isinstance(p["hits"], int))
    return out


def _event_runs(ensemble_result) -> tuple[list[list[dict]], str] | None:
    """Normalise an event-carrying ensemble result to a list of runs.

    Accepted: ``{"runs": [{"events": [event, ...]}, ...]}``,
    ``{"events": [event, ...]}`` (a single run), a list of runs
    (each a list of events or a dict with ``events``), or a bare
    list of events (a single run). Returns ``None`` when the input
    is none of these.
    """
    if isinstance(ensemble_result, dict):
        if isinstance(ensemble_result.get("runs"), list):
            runs = []
            for run in ensemble_result["runs"]:
                if isinstance(run, dict) and isinstance(run.get("events"), list):
                    runs.append(run["events"])
                elif isinstance(run, list):
                    runs.append(run)
                else:
                    return None
            return runs, "runs"
        if isinstance(ensemble_result.get("events"), list):
            return [ensemble_result["events"]], "events"
        return None
    if isinstance(ensemble_result, list):
        if all(isinstance(item, list) for item in ensemble_result):
            return list(ensemble_result), "runs"
        if all(isinstance(item, dict) and "node_id" in item
               for item in ensemble_result):
            return [ensemble_result], "events"
    return None


def _events_stats(ensemble_result, resolved: dict) -> dict | None:
    normalised = _event_runs(ensemble_result)
    if normalised is None:
        return None
    runs, shape = normalised
    node_set = set(resolved["node_ids"])
    out = _stats_base(resolved, shape)
    if shape == "runs":
        declared = (ensemble_result.get("variants")
                    if isinstance(ensemble_result, dict) else None)
        out["variants"] = declared if declared is not None else len(runs)

    per_node: dict[str, dict] = {}
    events_in_lens = 0
    events_total = 0
    seed_events = 0
    worst_delay = None
    worst_event = None
    for run in runs:
        nodes_in_run: set[str] = set()
        for ev in run:
            events_total += 1
            if ev.get("parent_id") is None:
                seed_events += 1
            node_id = ev.get("node_id")
            if node_id not in node_set:
                continue
            events_in_lens += 1
            nodes_in_run.add(node_id)
            delay = ev.get("delay_d")
            delay = float(delay) if isinstance(delay, (int, float)) else None
            st = per_node.setdefault(node_id, {"node_id": node_id, "events": 0,
                                               "runs_hit": 0,
                                               "worst_delay_d": None})
            st["events"] += 1
            if delay is not None and (st["worst_delay_d"] is None
                                      or delay > st["worst_delay_d"]):
                st["worst_delay_d"] = delay
            # Worst delay overall: first event wins ties (deterministic —
            # runs and events are scanned in the order given).
            if delay is not None and (worst_delay is None or delay > worst_delay):
                worst_delay = delay
                worst_event = {"node_id": node_id, "seq": ev.get("seq"),
                               "run_id": ev.get("run_id"), "delay_d": delay}
        for node_id in nodes_in_run:
            per_node[node_id]["runs_hit"] += 1

    out["per_node"] = [per_node[nid] for nid in sorted(per_node)]
    out["nodes_hit"] = sorted(per_node)
    out["nodes_hit_count"] = len(per_node)
    out["events_in_lens"] = events_in_lens
    out["events_total"] = events_total
    out["seed_events"] = seed_events
    out["worst_delay_d"] = worst_delay
    out["worst_delay_event"] = worst_event
    return out


def lens_ensemble_stats(ensemble_result, lens) -> dict:
    """Per-lens aggregates over an ensemble result. Pure; no mutation.

    Accepted ``ensemble_result`` shapes:

    1. The existing engine ensemble output (``engine.ensemble.run_ensemble``):
       ``{"variants": int, "ranked_bottlenecks": [...]}`` — supplies
       ``nodes_hit`` / ``nodes_hit_count``, ``variant_hits_in_lens``
       (sum of the per-node hit counts over the variant grid — counts,
       never percentages or probabilities) and the filtered ranking in
       ``per_node``. ``events_in_lens`` / ``worst_delay_d`` are ``None``
       for this shape: the ranked output contains no events or delays,
       and this function does not invent them.
    2. Event-carrying results — ``{"runs": [{"events": [event, ...]}, ...]}``,
       ``{"events": [event, ...]}``, a list of runs, or a bare event
       list — supply ``events_in_lens`` (count of events whose
       ``node_id`` is in the lens), ``events_total``, ``seed_events``
       (events with ``parent_id is None``), ``nodes_hit``,
       ``worst_delay_d`` (max ``delay_d`` among in-lens events) with
       ``worst_delay_event``, and a per-node breakdown sorted by node id.

    Common return fields: ``lens_id``, ``label``, ``source_shape``,
    ``variants``, ``nodes_hit`` (sorted), ``nodes_hit_count``,
    ``events_in_lens``, ``variant_hits_in_lens``, ``events_total``,
    ``seed_events``, ``worst_delay_d``, ``worst_delay_event``,
    ``per_node``. Fields a shape cannot supply are ``None``, never
    estimated.
    """
    resolved = _resolve_lens(lens)
    if isinstance(ensemble_result, dict) \
            and isinstance(ensemble_result.get("ranked_bottlenecks"), list):
        return _ranked_stats(ensemble_result, resolved)
    stats = _events_stats(ensemble_result, resolved)
    if stats is None:
        raise LensError("unrecognised ensemble_result shape: expected "
                        "{'ranked_bottlenecks': [...]}, {'runs': [...]}, "
                        "{'events': [...]} or an event list")
    return stats
