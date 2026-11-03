"""Fork & compare for ENGINE REV 2 (fork semantics).

A fork shares the parent's event prefix up to and including `at_seq`
byte-for-byte (modulo the child's own run_id). From the branch moment
forward the fluid network is re-simulated with the overrides applied as a
timed INTERVENTION on the branch place:

  capacity_loss  absolute loss of the branch place from the branch step on
                 (replaces, it is not a ratio as in engine v1)
  duration_d     length of that intervention in days (for a branch at the
                 seed event: the new disruption duration, from the start)
  shape          step | ramp | pulse profile of the intervention
  params         hit_threshold applies from the branch step on;
                 queue_sensitivity scales the continuation's reported waits
  node_params    {place: {hit_threshold}} continuation overrides

Because the simulation is causal (a state at step k depends only on steps
< k) the child's events up to the branch step equal the parent's; the
prefix events are carried verbatim from the parent record (their delay_d
and magnitude stay the parent's recorded aggregates, as in v1), and the
engine verifies the (place, channel, parent, tick, at) structure of the
prefix matches. Interventions accumulate along a fork chain (they are
journaled), so a fork of a fork replays exactly.

Defaults when an override is absent: capacity_loss = the seed disruption's
current loss, duration_d = the seed disruption's duration, shape = the
seed disruption's shape.
"""
from __future__ import annotations

from engine.constants import const
from engine.engine import (PARAM_BOUNDS, EngineError, _strict_number,
                           validate_params)

from .engine import Engine2, RunState2

ALLOWED = {"capacity_loss", "duration_d", "shape", "params", "node_params"}


def fork_events2(engine: Engine2, parent: RunState2, parent_spec: dict,
                 parent_params: dict, at_seq: int, overrides: dict,
                 child_run_id: str, parent_node_overrides: dict | None = None) -> RunState2:
    if not isinstance(overrides, dict) or not overrides:
        raise EngineError("overrides must be a non-empty object", code="empty_overrides")
    if not (0 <= at_seq < len(parent.events)):
        raise EngineError(f"at_seq {at_seq} out of range for parent with "
                          f"{len(parent.events)} events", code="at_seq_out_of_range")
    unknown = set(overrides) - ALLOWED
    if unknown:
        raise EngineError(f"unknown override keys: {sorted(unknown)}", code="unknown_override")
    # a fork of a fork continues from the parent's EFFECTIVE params / place
    # thresholds (the state carries them), not the root journal's
    parent_params = parent.params or parent_params
    parent_node_overrides = parent.node_overrides or parent_node_overrides
    raw_params = overrides.get("params")
    if raw_params is not None and not isinstance(raw_params, dict):
        raise EngineError("params override must be an object")
    params = validate_params({**parent_params, **(raw_params or {})})
    raw_np = overrides.get("node_params")
    if raw_np is not None and not isinstance(raw_np, dict):
        raise EngineError("node_params override must be an object")
    node_overrides = dict(parent_node_overrides or {})
    for nid, value in (raw_np or {}).items():
        if nid not in engine.graph.nodes:
            raise EngineError(f"node_params override for unknown node: {nid}", code="unknown_node")
        if not isinstance(value, dict):
            raise EngineError(f"node_params override for {nid} must be an object")
        thr = value.get("hit_threshold")
        if thr is not None:
            thr = _strict_number(thr, f"node_params[{nid}].hit_threshold")
            lo, hi = PARAM_BOUNDS["hit_threshold"]
            if not (lo <= thr <= hi):
                raise EngineError(
                    f"node_params[{nid}].hit_threshold out of bounds [{lo}, {hi}] "
                    f"(rejected, never clamped)", code="param_out_of_bounds")
        node_overrides[nid] = value

    eff = parent.eff_root or {"capacity_loss": parent_spec["capacity_loss"],
                              "duration_d": parent_spec["duration_d"],
                              "shape": parent_spec["shape"]}
    loss = eff["capacity_loss"]
    if "capacity_loss" in overrides:
        loss = _strict_number(overrides["capacity_loss"], "override capacity_loss")
        if not (0.0 < loss <= 1.0):
            raise EngineError("override capacity_loss out of bounds (0, 1]",
                              code="param_out_of_bounds")
    duration = eff["duration_d"]
    if "duration_d" in overrides:
        duration = _strict_number(overrides["duration_d"], "override duration_d")
        if not (0.0 < duration <= const("duration_max_d")):
            raise EngineError(
                f"override duration_d out of bounds (0, {'%g' % const('duration_max_d')}]",
                code="param_out_of_bounds")
    shape = eff["shape"]
    if "shape" in overrides:
        if overrides["shape"] not in ("step", "ramp", "pulse"):
            raise EngineError(f"unknown shape override: {overrides['shape']!r}",
                              code="invalid_shape")
        shape = overrides["shape"]

    branch_ev = parent.events[at_seq]
    step_f = int(parent.impacts[at_seq]["step"])
    node = branch_ev["node_id"]
    if at_seq == 0:
        start_step = 0
    else:
        start_step = step_f + 1
    iv = {"node_id": node, "start_step": start_step, "capacity_loss": float(loss),
          "duration_d": float(duration), "shape": shape, "at_seq": at_seq}
    interventions = [dict(x) for x in parent.interventions] + [iv]

    history = [dict(s) for s in (parent.thr_history or [
        {"from_step": 0, "params": dict(parent_params),
         "node_overrides": dict(parent_node_overrides or {})}])]
    history.append({"from_step": step_f + 1, "params": dict(params),
                    "node_overrides": {k: dict(v) for k, v in node_overrides.items()}})
    prefix = {"events": parent.events[: at_seq + 1],
              "impacts": {s: parent.impacts[s] for s in range(at_seq + 1)}}
    state = engine.run(child_run_id, parent_spec, params, node_overrides,
                       interventions=interventions, dt=parent.dt,
                       thr_history=history, prefix=prefix)
    return state
