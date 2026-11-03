"""Fork & compare (fork semantics of the run API).

A fork shares the parent's event prefix up to and including `at_seq`
byte-for-byte (modulo the child's own run_id, which the stream carries). From the
branch event forward, propagation re-runs deterministically with the
overrides applied to the branch impact:

  capacity_loss  branch shortfall is rescaled by new_loss / parent root loss
  duration_d     branch impact duration is replaced
  shape          re-applies the engine's shape adjustment to the branch impact
  params         engine params for the continuation (validated, never clamped)
  node_params    {node_id: {hit_threshold}} continuation overrides

Other prefix nodes do not re-propagate: their post-`at_seq` children in the
parent are exactly what a fork discards. Reforking the same parent at the
same at_seq with the same overrides is byte-identical because the child
run_id is a deterministic function of (parent run, at_seq, overrides) —
see server/api/store.py run-id derivation; the engine itself is a pure
function of its inputs.
"""
from __future__ import annotations

from .canonical import canonical_json
from .constants import const
from .engine import Engine, EngineError, Impact, RunState, _parse_ts, _shape_adjust, _strict_number, PARAM_BOUNDS, validate_params


def _strip_run_id(events: list[dict]) -> list[dict]:
    return [{k: v for k, v in ev.items() if k != "run_id"} for ev in events]


def divergence(events_a: list[dict], events_b: list[dict]) -> int | None:
    """First seq whose canonical event differs (run_id excluded — a fork's
    events necessarily carry the child's run_id). None if the shorter
    stream is a prefix of the longer and lengths are equal."""
    a, b = _strip_run_id(events_a), _strip_run_id(events_b)
    for i, (ea, eb) in enumerate(zip(a, b)):
        if canonical_json(ea) != canonical_json(eb):
            return i
    if len(a) != len(b):
        return min(len(a), len(b))
    return None


def fork_events(engine: Engine, parent: RunState, parent_spec: dict,
                parent_params: dict, at_seq: int, overrides: dict,
                child_run_id: str) -> RunState:
    """Build the child's RunState from the parent's completed state."""
    if not isinstance(overrides, dict) or not overrides:
        raise EngineError("overrides must be a non-empty object", code="empty_overrides")
    if not (0 <= at_seq < len(parent.events)):
        raise EngineError(f"at_seq {at_seq} out of range for parent with "
                          f"{len(parent.events)} events", code="at_seq_out_of_range")
    allowed = {"capacity_loss", "duration_d", "shape", "params", "node_params"}
    unknown = set(overrides) - allowed
    if unknown:
        raise EngineError(f"unknown override keys: {sorted(unknown)}", code="unknown_override")

    # Non-object params/node_params and non-numeric
    # capacity_loss/duration_d overrides would reach dict()/float() and
    # raise bare TypeErrors out of the API (plain-text 500s), and a
    # non-object node_params value would crash Engine.threshold()
    # mid-fork. Wrong types are rejected here with invalid_request,
    # never coerced.
    raw_params = overrides.get("params")
    if raw_params is not None and not isinstance(raw_params, dict):
        raise EngineError("params override must be an object")
    params = validate_params({**parent_params, **(raw_params or {})})
    raw_node_params = overrides.get("node_params")
    if raw_node_params is not None and not isinstance(raw_node_params, dict):
        raise EngineError("node_params override must be an object")
    node_overrides = dict(raw_node_params or {})
    for nid, value in node_overrides.items():
        if nid not in engine.graph.nodes:
            raise EngineError(f"node_params override for unknown node: {nid}",
                              code="unknown_node")
        if not isinstance(value, dict):
            raise EngineError(
                f"node_params override for {nid} must be an object")
        threshold = value.get("hit_threshold")
        if threshold is not None:
            threshold = _strict_number(
                threshold, f"node_params[{nid}].hit_threshold")
            lo, hi = PARAM_BOUNDS["hit_threshold"]
            if not (lo <= threshold <= hi):
                raise EngineError(
                    f"node_params[{nid}].hit_threshold out of bounds "
                    f"[{lo}, {hi}] (rejected, never clamped)",
                    code="param_out_of_bounds")

    # --- child state seeded with the shared prefix (re-stamped run_id) ---
    child = RunState()
    for ev in parent.events[: at_seq + 1]:
        nev = dict(ev)
        nev["run_id"] = child_run_id
        child.events.append(nev)
        rec = parent.impacts[ev["seq"]]
        child.impacts[nev["seq"]] = dict(rec)
        child.emitted.add((ev["node_id"], ev["channel"]))
    # WHYNOT evaluations carry across the fork: they were recorded
    # while the shared prefix propagated, and the child's not-hit list
    # (recomputed at the end of continue_run) must judge the whole run.
    child.evaluations = dict(parent.evaluations)
    # first-hit impacts for prefix nodes
    seen_nodes: set[str] = set()
    for ev in child.events:
        if ev["node_id"] not in seen_nodes:
            seen_nodes.add(ev["node_id"])
            rec = child.impacts[ev["seq"]]
            child.hit[ev["node_id"]] = Impact(
                node_id=ev["node_id"], s=rec["s"], duration_d=rec["duration_d"],
                total_delay_d=rec["total_delay_d"], seq=ev["seq"])

    # --- branch impact with overrides applied ---
    branch_ev = child.events[at_seq]
    rec = child.impacts[at_seq]
    s, duration = rec["s"], rec["duration_d"]
    if "capacity_loss" in overrides:
        new_loss = _strict_number(overrides["capacity_loss"],
                                  "override capacity_loss")
        if not (0.0 < new_loss <= 1.0):
            raise EngineError("override capacity_loss out of bounds (0, 1]",
                              code="param_out_of_bounds")
        root_loss = float(parent_spec["capacity_loss"]) or 1.0
        s = min(1.0, s * (new_loss / root_loss))
    if "duration_d" in overrides:
        duration = _strict_number(overrides["duration_d"],
                                  "override duration_d")
        if not (0.0 < duration <= const("duration_max_d")):
            raise EngineError(
                f"override duration_d out of bounds "
                f"(0, {'%g' % const('duration_max_d')}]",
                code="param_out_of_bounds")
    if "shape" in overrides:
        if overrides["shape"] not in ("step", "ramp", "pulse"):
            raise EngineError(f"unknown shape override: {overrides['shape']!r}",
                              code="invalid_shape")
        s, duration = _shape_adjust(s, duration, overrides["shape"])
    branch = Impact(node_id=branch_ev["node_id"], s=s, duration_d=duration,
                    total_delay_d=rec["total_delay_d"], seq=at_seq)
    child.hit[branch.node_id] = branch

    base_dt = _parse_ts(parent_spec.get("started_at"))
    engine.continue_run(child, child_run_id, [branch], params, node_overrides,
                        base_dt, start_tick=int(branch_ev.get("tick", 0)) + 1)
    return child
