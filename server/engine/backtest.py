"""Backtest harness.

Input replay dataset (files under data/replay/):
  {"disruption": {<scenario disruption spec>},
   "observed_steps": [{"node_id": ..., "observed_delay_d": ...,
                        "evidence": {...}?}, ...]}

grade_run() below is the RAW window rule: a step is a HIT when the
engine predicted the node and the observed delay falls within
[0.5 * predicted delay_d, 2 * predicted delay_d] (predicted delay_d = the
node's FIRST-hit event delay); delay_error_d = mean |observed - predicted|
over hit steps. The decomposed scorecard (engine/scorecard.py) does NOT
use this mashed rate: it decomposes steps into seed-input / unquantified /
genuine classes, reports node coverage separately from delay accuracy, and
uses the MAX-delay event per node as the predicted delay. Numbers from the
two paths are not comparable.

Effective-spec mapping (no silent defaults in the backtest/scorecard
path): replay disruption specs carry `expected_duration_d`, while the engine reads `duration_d` and would
otherwise silently default to 7 d. effective_disruption() maps
`expected_duration_d` -> `duration_d` explicitly, passes the dataset's
stated `capacity_loss` explicitly, and records EVERY mapping or default
substitution it (or the engine) applies in a disclosures list that callers
must surface (scorecard.json carries it per replay; scorecard.md prints
it). If a dataset ever omits capacity_loss for a non-closure kind, the
engine's substitution (0.5, or 1.0 for closure) is mirrored here, passed
explicitly, and disclosed — never left silent.

Evidence rule: an observed
step is gradeable ONLY when it carries an evidence source (see
has_evidence). A step with no evidence source is UNGRADEABLE — never a
hit, never a miss, never silently dropped: grade_run counts and lists
such steps separately and excludes them from hits/misses/delay_error_d,
and the decomposed scorecard (engine/scorecard.py) does the same so every
denominator stays honest. Current shipped replay datasets all carry
evidence (`evidence_source`); this is enforcement of the code path.

Deterministic; no LLM anywhere in the grading path.
"""
from __future__ import annotations

import json
from pathlib import Path

from .canonical import stream_hash
from .engine import Engine

# Engine validate_disruption defaults (engine.py) — mirrored here ONLY so
# the harness can disclose them, never to change their values.
ENGINE_DEFAULT_DURATION_D = 7.0
ENGINE_DEFAULT_CAPACITY_LOSS = 0.5
ENGINE_CLOSURE_CAPACITY_LOSS = 1.0
ENGINE_DEFAULT_SHAPE = "step"


def has_evidence(obs: dict) -> bool:
    """True when an observed step carries an evidence source.

    Accepted forms (any one suffices — a non-empty source identifier):
      * ``evidence_source``: non-empty string (the shipped replay
        datasets' field, a source name/URL per step);
      * ``evidence_url``: non-empty string;
      * ``evidence``: a non-empty string, a provenance dict with a
        non-empty ``source``, or a non-empty list (``evidence[]``
        style) containing at least one such dict / string.
    Empty strings, empty dicts/lists, and dicts without a source do NOT
    count — a grade without evidence does not publish.
    """
    if not isinstance(obs, dict):
        return False
    for field in ("evidence_source", "evidence_url"):
        v = obs.get(field)
        if isinstance(v, str) and v.strip():
            return True
    ev = obs.get("evidence")
    if isinstance(ev, str):
        return bool(ev.strip())
    if isinstance(ev, dict):
        src = ev.get("source")
        return isinstance(src, str) and bool(src.strip())
    if isinstance(ev, list):
        for item in ev:
            if isinstance(item, str) and item.strip():
                return True
            if isinstance(item, dict):
                src = item.get("source")
                if isinstance(src, str) and src.strip():
                    return True
    return False


def effective_disruption(dataset_disruption: dict) -> tuple[dict, list[dict]]:
    """Build the engine spec for a replay dataset disruption, explicitly.

    Returns (spec, disclosures). `spec` contains exactly the fields the
    engine consumes, with dataset-stated values passed through. Each
    disclosure is {"field", "action", ...} with action one of:
    "mapped" (renamed from the dataset's disruption spec field), "default_substituted"
    (the dataset stated nothing usable; the engine default is passed
    explicitly instead of being applied silently), "forced" (engine rule
    that overrides the stated value, e.g. closure -> capacity_loss 1.0).
    """
    if not isinstance(dataset_disruption, dict):
        raise ValueError("disruption spec must be an object")
    src = dataset_disruption
    disclosures: list[dict] = []

    # --- duration: dataset duration_d wins; else map expected_duration_d.
    if src.get("duration_d") is not None:
        duration_d = float(src["duration_d"])
        duration_source = "dataset duration_d"
    elif src.get("expected_duration_d") is not None:
        duration_d = float(src["expected_duration_d"])
        duration_source = "dataset expected_duration_d (mapped to engine duration_d)"
        disclosures.append({
            "field": "duration_d", "action": "mapped",
            "from_field": "expected_duration_d", "value": duration_d,
            "note": "replay specs carry the field expected_duration_d; "
                    "the engine reads duration_d — mapped explicitly so the "
                    "engine's silent 7.0 d default cannot fire",
        })
    else:
        duration_d = ENGINE_DEFAULT_DURATION_D
        duration_source = "engine default (dataset states no duration)"
        disclosures.append({
            "field": "duration_d", "action": "default_substituted",
            "value": duration_d,
            "note": "dataset states neither duration_d nor "
                    "expected_duration_d; engine default passed explicitly",
        })

    # --- capacity_loss: pass the stated value; disclose engine forcing /
    # substitution instead of letting it happen silently inside the engine.
    kind = src.get("kind")
    stated_loss = src.get("capacity_loss")
    if kind == "closure":
        capacity_loss = ENGINE_CLOSURE_CAPACITY_LOSS
        if stated_loss is None or float(stated_loss) != ENGINE_CLOSURE_CAPACITY_LOSS:
            disclosures.append({
                "field": "capacity_loss", "action": "forced",
                "dataset_value": stated_loss, "value": capacity_loss,
                "note": "engine rule: kind=closure forces capacity_loss = 1.0",
            })
    elif stated_loss is not None:
        capacity_loss = float(stated_loss)
    elif kind in ("closure", "capacity_cut"):
        # The engine raises missing_capacity_loss for these kinds; surface
        # that here as a dataset error with the same meaning.
        raise ValueError(
            f"capacity_loss required for kind={kind} (dataset states none)")
    else:
        capacity_loss = ENGINE_DEFAULT_CAPACITY_LOSS
        disclosures.append({
            "field": "capacity_loss", "action": "default_substituted",
            "dataset_value": None, "value": capacity_loss,
            "note": "dataset states capacity_loss = null; engine default "
                    "for non-closure kinds passed explicitly (this is the "
                    "substitution that silently produced a 3.5 d LA root "
                    "delay when a null capacity_loss was defaulted)",
        })

    # --- shape: engine default, disclosed when the dataset omits it.
    shape = src.get("shape")
    if shape is None:
        shape = ENGINE_DEFAULT_SHAPE
        disclosures.append({
            "field": "shape", "action": "default_substituted",
            "value": shape,
            "note": "dataset states no shape; engine default passed explicitly",
        })

    spec = {
        "node_id": src.get("node_id"),
        "kind": kind,
        "capacity_loss": capacity_loss,
        "duration_d": duration_d,
        "shape": shape,
        "started_at": src.get("started_at") or src.get("detected_at"),
    }
    return spec, disclosures


def grade_run(engine: Engine, disruption: dict, observed_steps: list[dict],
              params: dict | None = None, run_id: str = "backtest") -> dict:
    spec, disclosures = effective_disruption(disruption)
    state = engine.run(run_id, spec, params)
    predicted: dict[str, dict] = {}
    for ev in state.events:
        predicted.setdefault(ev["node_id"], ev)

    steps = []
    errors: list[float] = []
    hits = 0
    ungradeable = 0
    for obs in observed_steps:
        node_id = obs["node_id"]
        observed = float(obs["observed_delay_d"])
        ev = predicted.get(node_id)
        pred = float(ev["delay_d"]) if ev is not None else None
        if not has_evidence(obs):
            # Evidence rule: no evidence source ->
            # UNGRADEABLE. Never a hit, never a miss, never dropped:
            # counted and listed here, excluded from delay_error_d.
            ungradeable += 1
            steps.append({"node_id": node_id, "observed_delay_d": observed,
                          "predicted_delay_d": pred, "result": "ungradeable",
                          "reason": "no evidence source on the observed "
                                    "step (a hit must be backed "
                                    "by queue/capacity evidence)"})
            continue
        if ev is None:
            steps.append({"node_id": node_id, "observed_delay_d": observed,
                          "predicted_delay_d": None, "result": "miss",
                          "reason": "node not predicted"})
            continue
        lo, hi = 0.5 * pred, 2.0 * pred
        if lo <= observed <= hi:
            hits += 1
            err = abs(observed - pred)
            errors.append(err)
            steps.append({"node_id": node_id, "observed_delay_d": observed,
                          "predicted_delay_d": pred, "result": "hit",
                          "delay_error_d": round(err, 6)})
        else:
            steps.append({"node_id": node_id, "observed_delay_d": observed,
                          "predicted_delay_d": pred, "result": "miss",
                          "reason": f"observed {observed} outside window [{lo}, {hi}]"})
    observed_nodes = {o["node_id"] for o in observed_steps if has_evidence(o)}
    return {
        "run_id": run_id,
        "stream_hash": stream_hash(state.events),
        "event_count": len(state.events),
        "observed_steps": len(observed_steps),
        "hits": hits,
        "misses": len(observed_steps) - hits - ungradeable,
        "ungradeable": ungradeable,
        "delay_error_d": round(sum(errors) / len(errors), 6) if errors else None,
        "steps": steps,
        "predicted_unobserved": sorted(n for n in predicted if n not in observed_nodes),
        "effective_disruption": spec,
        "disclosures": disclosures,
    }


def main(argv: list[str] | None = None) -> int:
    """CLI: python -m engine.backtest <replay.json> <graph.json> [params.json]

    Prints the raw-rule scorecard as JSON. Exit 0 even with misses — a
    scorecard with misses is a result, not a failure. For the decomposed
    scorecard use python -m engine.scorecard.
    """
    import sys
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2:
        print("usage: python -m engine.backtest <replay.json> <graph.json> [params.json]",
              file=sys.stderr)
        return 2
    from .graph import load_graph
    params = None
    if len(argv) > 2:
        params = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
    card = run_backtest(Engine(load_graph(argv[1])), argv[0], params=params)
    print(json.dumps(card, indent=2, sort_keys=True))
    return 0


def run_backtest(engine: Engine, replay_path: str | Path,
                 params: dict | None = None) -> dict:
    with open(replay_path, "r", encoding="utf-8") as fh:
        dataset = json.load(fh)
    if "disruption" not in dataset or "observed_steps" not in dataset:
        raise ValueError("replay dataset must have 'disruption' and 'observed_steps'")
    return grade_run(engine, dataset["disruption"], dataset["observed_steps"],
                     params=params, run_id=f"backtest_{Path(replay_path).stem}")


if __name__ == "__main__":
    raise SystemExit(main())
