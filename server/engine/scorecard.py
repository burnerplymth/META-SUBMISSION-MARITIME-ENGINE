"""Scorecard generator — decomposed backtest grading.

Turns the backtest harness into the publishable scorecard by
DECOMPOSING the raw window-rule rate. The
raw rate conflated three different things; this generator never reports a
rate without its denominator definition, in JSON or in Markdown.

Methodology (embedded verbatim in every output artifact):
  0. Evidence rule: an observed step with no
     evidence source is UNGRADEABLE — never a hit, never a miss, never
     silently dropped. Counted and listed as its own line; excluded
     from coverage, delay grading, and false positives.
  1. Seed exclusion: observed steps at the replay disruption's own node
     are the scenario input, not predictions — excluded from all grading,
     counted and listed separately as "input steps".
  2. Node coverage: of the distinct non-seed nodes with observed effects,
     the fraction the engine predicted at all. "Predicted" means at
     least one event at that node that is not a delay-0 cost-channel
     leaf (cost-leaf hygiene): a node whose only predicted
     events are delay-0 cost leaves counts as NOT predicted — no
     coverage credit, no false positive. Covered and uncovered nodes
     are both listed; cost-only nodes are listed separately.
  3. Delay accuracy: graded ONLY on non-seed steps where
     observed_delay_d > 0 (quantified) AND the node was predicted. HIT if
     the observed delay falls within [0.5x, 2x] of the predicted delay for
     that node, else MISS. The predicted delay for a node is the
     FIRST-HIT delay: the delay_d of the node's first event in
     stream order, excluding delay-0 cost leaves (the estimator the
     harness backtest.py uses; the legacy grading used
     the maximum delay over all events at the node, so its figures are
     not comparable — a legacy-grading comparison on the same runs is
     published alongside). Grading is deduplicated BY NODE: one graded
     step per node per replay, the earliest observed step for that node
     (first in dataset order); later quantified steps at an
     already-graded node are listed as duplicates and enter no
     denominator. Reported: hits, misses, and mean
     |observed - predicted| over the graded steps. Quantified non-seed
     steps at nodes the engine never predicted cannot be window-graded
     (there is no predicted delay); they are genuine misses, listed
     individually, and excluded from the mean. Steps with
     observed_delay_d = 0 are "effect observed, delay unquantified":
     counted in node coverage, excluded from delay grading, and listed.
  4. False positives: predicted nodes (cost-only nodes excluded, see 2)
     with no observed effect at all (any observed step, seed steps
     included) — listed by node.
  5. Miss classification: every step carries exactly one class —
     "seed-input", "unquantified", "duplicate", or "genuine". Genuine
     misses (window misses on graded steps + quantified steps at
     unpredicted nodes) are listed individually with observed vs
     predicted values. They are published, not hidden.
  6. Statistics: the aggregate prints exact
     Clopper-Pearson 95% confidence intervals on coverage and on the
     delay hit rate, a naive directed-k-hop baseline computed on the
     same events with the same per-replay node budget, and a plain
     small-sample statement. The in-sample caveats are printed on the
     scorecard itself (see Honesty caveats).

Effective specs: each replay is run on the EFFECTIVE disruption built by
engine.backtest.effective_disruption — the dataset's stated capacity_loss
passed explicitly, `expected_duration_d` mapped to the engine's
`duration_d`, and every mapping/default substitution recorded in the
replay's "disclosures" and printed in the Markdown. The duration and
capacity_loss basis used for each replay are stated alongside its numbers.

Determinism: this artifact embeds NO timestamp (deliberate choice).
scorecard.json is serialised canonically (sorted keys) and scorecard.md
is rendered deterministically from the same structure, so generating the
scorecard twice from the same datasets + graph yields byte-identical
files. Reproducibility anchors carried in the artifact: graph hash,
per-dataset SHA-256 of the raw dataset file bytes, and per-replay run_id
+ event stream hash.

CLI: python -m engine.scorecard [--graph G] [--out-json J] [--out-md M]
     [replay.json ...]   (defaults: the repo's real graph + every replay
     dataset under data/replay/, writing data/replay/scorecard.{json,md})
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .backtest import effective_disruption, has_evidence
from .canonical import stream_hash
from .engine import Engine

REPO_ROOT = Path(__file__).resolve().parents[2]

METHODOLOGY = {
    "seed_exclusion": (
        "Observed steps at the replay disruption's own node are the "
        "scenario input, not predictions. They are excluded from all "
        "grading and are counted and listed separately as input steps."),
    "node_coverage": (
        "Node coverage = (distinct non-seed nodes with at least one "
        "observed effect that the engine predicted) / (distinct "
        "non-seed nodes with at least one observed effect). 'Predicted' "
        "means at least one event at the node that is not a delay-0 "
        "cost-channel leaf (see cost_leaf_exclusion). Covered and "
        "uncovered nodes are listed."),
    "delay_accuracy": (
        "Delay accuracy is graded ONLY on non-seed steps where "
        "observed_delay_d > 0 (quantified) AND the node was predicted: "
        "HIT if the observed delay is within [0.5x, 2x] of the predicted "
        "delay for that node, else MISS. The predicted delay for a node "
        "is the FIRST-HIT delay: the delay_d of the node's first "
        "event in stream order, excluding delay-0 cost-channel leaves "
        "(the estimator the harness backtest.py uses; an "
        "legacy grading used the maximum delay_d over "
        "all events at the node, so its figures are not comparable — "
        "a legacy-grading comparison computed on the same runs is "
        "published alongside the headline). Grading is deduplicated "
        "BY NODE: one graded step per node per replay, the earliest "
        "observed step for the node (first in dataset order); later "
        "quantified steps at an already-graded node are duplicates — "
        "counted, listed, and in no denominator. mean_abs_error_d is "
        "the mean of |observed - predicted| over the graded steps only. "
        "Quantified non-seed steps at unpredicted nodes have no "
        "predicted delay to grade against: they are genuine misses, "
        "listed individually, and excluded from the mean. Steps with "
        "observed_delay_d = 0 are 'effect observed, delay unquantified': "
        "counted in node coverage, excluded from delay grading, and "
        "listed."),
    "false_positives": (
        "False positives = nodes the engine predicted (cost-only "
        "nodes excluded — see cost_leaf_exclusion) with no observed "
        "effect at all; an observed step at the seed node counts as an "
        "observed effect for this purpose. Listed by node; they are "
        "not graded as misses of observed steps."),
    "cost_leaf_exclusion": (
        "Cost-leaf exclusion: an event on the 'cost' "
        "channel with delay_d = 0 is a cost-pressure leaf, not a delay "
        "prediction. A node whose only predicted events are delay-0 "
        "cost leaves counts as NOT predicted: it earns no coverage "
        "credit and is not a false positive. Such nodes are listed "
        "per replay as cost-only nodes. Cost leaves never supply a "
        "node's first-hit delay."),
    "evidence_rule": (
        "Evidence rule, enforced in this grading path: an "
        "observed step with no evidence source (no evidence_source / "
        "evidence_url / evidence provenance) is UNGRADEABLE — never a "
        "hit, never a miss, and never silently dropped. Ungradeable "
        "steps are counted and listed as their own line per replay and "
        "in the aggregate, and are excluded from node coverage, delay "
        "grading, and the false-positive comparison (an evidence-less "
        "observation is not an observed effect), so every denominator "
        "covers evidence-backed steps only."),
    "miss_classification": (
        "Every observed step carries exactly one class: 'ungradeable' "
        "(no evidence source — see evidence_rule; checked first, before "
        "any other class), 'seed-input' (at the disruption node), "
        "'unquantified' (non-seed, observed_delay_d = 0), 'duplicate' "
        "(non-seed, quantified, at a node already graded in this "
        "replay — see the dedupe rule in delay_accuracy), or 'genuine' "
        "(non-seed, quantified, first step at its node). Genuine "
        "misses — graded steps outside the [0.5x, 2x] window plus "
        "quantified steps at unpredicted nodes — are listed "
        "individually with observed vs predicted values."),
    "statistics": (
        "Confidence intervals are exact Clopper-Pearson 95% intervals "
        "for a binomial proportion, computed on the pooled counts "
        "(coverage: covered / distinct non-seed observed nodes; delay "
        "hit rate: hits / graded steps). Baseline: a naive directed "
        "k-hop neighbourhood of the seed node over the graph's "
        "directed edges, expanded whole hop levels at a time until its "
        "non-seed node count first reaches the engine's predicted "
        "non-seed node count for the same replay (same events, same "
        "node budget); baseline coverage and precision (covered / "
        "predicted) are printed beside the engine's, per replay and "
        "pooled, and the scorecard states plainly when the engine "
        "does not beat the baseline on a metric."),
    "effective_spec": (
        "Each replay is run on an effective disruption built from the "
        "dataset's stated spec: the stated capacity_loss is passed "
        "explicitly and the dataset's expected_duration_d (the disruption spec field) "
        "is mapped to the engine's duration_d. Every mapping or default "
        "substitution is recorded in the replay's disclosures and printed "
        "alongside its numbers; the duration used and the capacity_loss "
        "basis are stated for every replay. No silent defaults."),
    "determinism": (
        "This artifact embeds no timestamp (deliberate). Generating the "
        "scorecard twice from the same datasets and graph yields "
        "byte-identical scorecard.json and scorecard.md. Anchors: graph "
        "hash, per-dataset SHA-256 of the raw file bytes, per-replay "
        "run_id and event stream hash."),
}

HONESTY_CAVEATS = [
    ("Edge shares are MODELED allocations pending re-derivation from "
     "PortWatch/Comtrade/CPPI/LSCI: the graph's edge capacity_share and "
     "transit values were allocated by the dataset builders, not extracted "
     "from those sources, so every predicted delay inherits that modeling "
     "choice."),
    ("Predicted delays scale with the effective disruption duration: the "
     "duration used for each replay is the dataset's stated "
     "expected_duration_d, mapped explicitly (see each replay's section). "
     "For Red Sea 2024 that stated duration (559 d) is an observed lower "
     "bound — days from onset to the last timeline observation, with the "
     "disruption still unresolved — not a forecast."),
    ("LA 2021 capacity_loss is a MODELED assumption, not a sourced "
     "figure (see the LA section, which prints the full basis alongside "
     "the LA numbers)."),
    ("This decomposed presentation refines the raw window-rule "
     "rate; it does not change the grading rule v1 used for "
     "live Ledger entries."),
    ("Engine model (revision 2): lost "
     "flow is aggregated in absolute terms over each node's inbound "
     "edges; delays are service-time waits from stored "
     "backlog — backlog = shortfall x inbound flow x duration, stored "
     "capped by the node's real queue_cap, cleared at the spare "
     "service rate capacity x (1 - utilisation); one "
     "continuous delay function serves queue and congestion; "
     "model constants live in the versioned registry. "
     "Utilisation is a global registry ASSUMPTION (0.85) — the graph "
     "carries no per-node utilisation field — so every wait inherits "
     "it. Per-node outcomes are in the tables above; no node-level "
     "narrative is reused from engine v1."),
    ("IN-SAMPLE: the engine rules were configured with sight of these "
     "replay results — the backlog model was adjusted against this same "
     "three-episode replay set. This scorecard is therefore an "
     "in-sample fit check on three known episodes, not a held-out "
     "evaluation, and its figures must not be quoted as out-of-sample "
     "accuracy."),
    ("LA 2021 is doubly in-sample: its capacity_loss input (0.78) is "
     "derived from the same MESocal 10 Nov 2021 snapshot that supplies "
     "the LA observed steps' labels, so the LA run is partly graded "
     "against its own input source."),
    ("DEFERRED: the full 25-event protocol v2 is a roadmap item and "
     "has NOT been run. This scorecard grades the 3 shipped replay "
     "episodes only; no 25-event held-out protocol stands behind any "
     "figure printed here."),
]

# The v1 column is the published grading of the original engine.
# Printed in the artifact's comparison section, labelled as
# not-comparable, so the republication never silently replaces a
# published number.
PREVIOUS_PUBLISHED = {
    "label": ("As published for the original engine "
              "(original grading: max-over-events estimator, "
              "no node dedupe, delay-0 cost leaves counted as "
              "predictions)"),
    "coverage_covered": 9,
    "coverage_distinct_non_seed_nodes": 10,
    "delay_hits": 3,
    "delay_graded_steps": 5,
    "mean_abs_error_d": 7.68,
    "false_positives": 52,
}


def _r6(x: float) -> float:
    return round(float(x), 6)


def _is_cost_leaf(ev: dict) -> bool:
    """A delay-0 cost-channel event: a cost-pressure leaf, not a
    delay prediction."""
    return ev.get("channel") == "cost" and float(ev["delay_d"]) == 0.0


def _binom_cdf(k: int, n: int, p: float) -> float:
    from math import comb
    return sum(comb(n, i) * p ** i * (1.0 - p) ** (n - i)
               for i in range(0, k + 1))


def clopper_pearson(x: int, n: int, alpha: float = 0.05) -> list | None:
    """Exact Clopper-Pearson two-sided (1-alpha) interval for x/n,
    by bisection on the exact binomial CDF. [lo, hi] rounded to 6dp;
    None when n = 0."""
    if n <= 0:
        return None
    if x <= 0:
        lo = 0.0
    else:
        a, b = 0.0, 1.0
        for _ in range(80):
            m = 0.5 * (a + b)
            # P(X >= x | m) = 1 - CDF(x-1); root where it equals alpha/2
            if 1.0 - _binom_cdf(x - 1, n, m) > alpha / 2.0:
                b = m
            else:
                a = m
        lo = 0.5 * (a + b)
    if x >= n:
        hi = 1.0
    else:
        a, b = 0.0, 1.0
        for _ in range(80):
            m = 0.5 * (a + b)
            if _binom_cdf(x, n, m) > alpha / 2.0:
                a = m
            else:
                b = m
        hi = 0.5 * (a + b)
    return [_r6(lo), _r6(hi)]


def _legacy_grade(events: list, dataset: dict, seed_node: str) -> dict:
    """The legacy grading rules applied to the SAME run, for the
    published comparison only: predicted delay = MAX delay_d over ALL
    events at the node (delay-0 cost leaves included), no node
    dedupe, predicted set = any event at the node. Never the headline."""
    pred_max: dict[str, float] = {}
    for ev in events:
        nid = ev["node_id"]
        if nid not in pred_max or float(ev["delay_d"]) > pred_max[nid]:
            pred_max[nid] = float(ev["delay_d"])
    observed_nodes = {o["node_id"] for o in dataset["observed_steps"]
                      if has_evidence(o)}
    non_seed = sorted(n for n in observed_nodes if n != seed_node)
    covered = sum(1 for n in non_seed if n in pred_max)
    graded = hits = 0
    abs_sum = 0.0
    for obs in dataset["observed_steps"]:
        node_id = obs["node_id"]
        observed = float(obs["observed_delay_d"])
        if not has_evidence(obs) or node_id == seed_node or observed <= 0.0:
            continue
        predicted = pred_max.get(node_id)
        if predicted is None:
            continue
        graded += 1
        abs_sum += abs(observed - predicted)
        if 0.5 * predicted <= observed <= 2.0 * predicted:
            hits += 1
    fp = sum(1 for n in pred_max if n not in observed_nodes)
    return {
        "coverage_covered": covered,
        "coverage_distinct_non_seed_nodes": len(non_seed),
        "coverage_fraction": _r6(covered / len(non_seed)) if non_seed else None,
        "delay_graded_steps": graded,
        "delay_hits": hits,
        "mean_abs_error_d": _r6(abs_sum / graded) if graded else None,
        "false_positives": fp,
    }


def _baseline_grade(engine: Engine, seed_node: str, budget: int,
                    observed_non_seed: set) -> dict:
    """Naive directed k-hop baseline: BFS from the seed over
    the graph's directed edges, whole hop levels, until the non-seed
    node count first reaches `budget` (the engine's predicted
    non-seed count for the same replay) or the frontier is exhausted."""
    adjacency: dict[str, set] = {}
    for e in engine.graph.edges:
        adjacency.setdefault(e["from"], set()).add(e["to"])
    seen = {seed_node}
    frontier = {seed_node}
    hops = 0
    while len(seen - {seed_node}) < budget and frontier:
        nxt: set = set()
        for n in frontier:
            nxt |= adjacency.get(n, set())
        nxt -= seen
        if not nxt:
            break
        seen |= nxt
        frontier = nxt
        hops += 1
    predicted = seen - {seed_node}
    covered = predicted & observed_non_seed
    return {
        "method": METHODOLOGY["statistics"],
        "hops": hops,
        "budget_nodes": budget,
        "predicted_count": len(predicted),
        "covered_count": len(covered),
        "coverage_fraction": (_r6(len(covered) / len(observed_non_seed))
                              if observed_non_seed else None),
        "precision": (_r6(len(covered) / len(predicted))
                      if predicted else None),
        "predicted_nodes": sorted(predicted),
    }


def _step_entry(index: int, obs: dict, cls: str, result: str,
                predicted_delay: float | None, reason: str,
                error: float | None = None,
                window: list | None = None) -> dict:
    entry = {
        "index": index,
        "node_id": obs["node_id"],
        "observed_delay_d": float(obs["observed_delay_d"]),
        "predicted_delay_d": predicted_delay,
        "class": cls,
        "result": result,
        "reason": reason,
    }
    if window is not None:
        entry["window"] = window
    if error is not None:
        entry["delay_error_d"] = _r6(error)
    for f in ("evidence_date", "effect", "delay_basis", "evidence_source"):
        if obs.get(f) is not None:
            entry[f] = obs[f]
    return entry


def grade_replay(engine: Engine, dataset: dict, replay_id: str,
                 dataset_file: str | None = None,
                 dataset_sha256: str | None = None,
                 params: dict | None = None) -> dict:
    """Decomposed grading of one replay dataset (see module docstring)."""
    if "disruption" not in dataset or "observed_steps" not in dataset:
        raise ValueError("replay dataset must have 'disruption' and 'observed_steps'")
    spec, disclosures = effective_disruption(dataset["disruption"])
    run_id = f"scorecard_{replay_id}"
    state = engine.run(run_id, spec, params)

    # Predicted delay per node = FIRST-HIT delay: the
    # delay_d of the node's first event in stream order, excluding
    # delay-0 cost leaves. A node with only cost leaves is NOT in the
    # predicted set at all (cost_leaf_exclusion).
    pred_first: dict[str, float] = {}
    nodes_with_events: set = set()
    for ev in state.events:
        nid = ev["node_id"]
        nodes_with_events.add(nid)
        if _is_cost_leaf(ev):
            continue
        if nid not in pred_first:
            pred_first[nid] = float(ev["delay_d"])
    cost_only_nodes = sorted(nodes_with_events - set(pred_first))

    seed_node = spec["node_id"]
    steps: list[dict] = []
    input_steps: list[dict] = []
    unquantified_steps: list[dict] = []
    ungradeable_steps: list[dict] = []
    graded_steps: list[dict] = []
    duplicate_steps: list[dict] = []
    quantified_unpredicted: list[dict] = []
    genuine_misses: list[dict] = []
    graded_nodes_seen: set = set()
    hits = 0
    abs_err_sum = 0.0

    for i, obs in enumerate(dataset["observed_steps"]):
        node_id = obs["node_id"]
        observed = float(obs["observed_delay_d"])
        predicted = pred_first.get(node_id)
        if not has_evidence(obs):
            # Evidence rule, checked FIRST: no
            # evidence source -> UNGRADEABLE, whatever the node/delay.
            # Never a hit, never dropped; excluded from every grading
            # denominator below and listed as its own line.
            e = _step_entry(i, obs, "ungradeable", "ungradeable", predicted,
                            "no evidence source on the observed step "
                            "(a hit must be backed by "
                            "queue/capacity evidence): ungradeable, "
                            "excluded from coverage and delay grading")
            steps.append(e)
            ungradeable_steps.append(e)
            continue
        if node_id == seed_node:
            e = _step_entry(i, obs, "seed-input", "input", predicted,
                            "step at the disruption node itself: scenario "
                            "input, excluded from grading")
            steps.append(e)
            input_steps.append(e)
            continue
        if observed <= 0.0:
            covered = node_id in pred_first
            e = _step_entry(i, obs, "unquantified", "unquantified", predicted,
                            "effect observed, delay unquantified "
                            "(observed_delay_d = 0): counted in node "
                            "coverage, excluded from delay grading; node "
                            + ("was predicted" if covered else "was NOT predicted"))
            steps.append(e)
            unquantified_steps.append(e)
            continue
        # Non-seed, quantified: dedupe BY NODE — one graded
        # step per node per replay; the earliest observed step grades,
        # later quantified steps at the same node are duplicates.
        if node_id in graded_nodes_seen:
            e = _step_entry(i, obs, "duplicate", "duplicate", predicted,
                            "duplicate: node already graded in this "
                            "replay (one graded step per node; the "
                            "earliest observed step grades) — counted "
                            "and listed, in no denominator")
            steps.append(e)
            duplicate_steps.append(e)
            continue
        graded_nodes_seen.add(node_id)
        # Class is genuine either way from here.
        if predicted is None:
            e = _step_entry(i, obs, "genuine", "miss", None,
                            "node not predicted: genuine miss (no predicted "
                            "delay to window-grade; excluded from "
                            "mean_abs_error_d)")
            steps.append(e)
            quantified_unpredicted.append(e)
            genuine_misses.append(e)
            continue
        lo, hi = 0.5 * predicted, 2.0 * predicted
        err = abs(observed - predicted)
        if lo <= observed <= hi:
            hits += 1
            abs_err_sum += err
            e = _step_entry(i, obs, "genuine", "hit", predicted,
                            f"observed {observed} within window [{lo}, {hi}]",
                            error=err, window=[_r6(lo), _r6(hi)])
        else:
            abs_err_sum += err
            e = _step_entry(i, obs, "genuine", "miss", predicted,
                            f"observed {observed} outside window [{lo}, {hi}]",
                            error=err, window=[_r6(lo), _r6(hi)])
            genuine_misses.append(e)
        steps.append(e)
        graded_steps.append(e)

    # Evidence-backed observations only: an evidence-less step is
    # ungradeable, not an observed effect (see evidence_rule).
    observed_nodes_all = {o["node_id"] for o in dataset["observed_steps"]
                          if has_evidence(o)}
    non_seed_nodes = sorted(n for n in observed_nodes_all if n != seed_node)
    covered_nodes = [n for n in non_seed_nodes if n in pred_first]
    uncovered_nodes = [n for n in non_seed_nodes if n not in pred_first]
    graded = len(graded_steps)
    misses = graded - hits
    predicted_unobserved = sorted(n for n in pred_first if n not in observed_nodes_all)
    predicted_non_seed_count = len([n for n in pred_first if n != seed_node])
    baseline = _baseline_grade(engine, seed_node, predicted_non_seed_count,
                                set(non_seed_nodes))
    engine_precision = (_r6(len(covered_nodes) / predicted_non_seed_count)
                        if predicted_non_seed_count else None)

    basis = dataset["disruption"].get("capacity_loss_basis")
    return {
        "id": replay_id,
        "title": dataset.get("title"),
        "dataset_file": dataset_file,
        "dataset_sha256": dataset_sha256,
        "run_id": run_id,
        "stream_hash": stream_hash(state.events),
        "event_count": len(state.events),
        "seed_node": seed_node,
        "disruption_as_stated": dataset["disruption"],
        "effective_disruption": spec,
        "duration_used_d": spec["duration_d"],
        "duration_source": ("dataset duration_d" if "duration_d" in dataset["disruption"]
                            else "dataset expected_duration_d mapped to engine duration_d"
                            if "expected_duration_d" in dataset["disruption"]
                            else "engine default (dataset states no duration)"),
        "capacity_loss_used": spec["capacity_loss"],
        "capacity_loss_basis": basis,
        "disruption_notes": dataset.get("disruption_notes"),
        "disclosures": disclosures,
        "counts": {
            "observed_steps": len(dataset["observed_steps"]),
            "input_steps": len(input_steps),
            "non_seed_steps": len(dataset["observed_steps"]) - len(input_steps),
            "unquantified_steps": len(unquantified_steps),
            "ungradeable_steps": len(ungradeable_steps),
            "duplicate_steps": len(duplicate_steps),
            "genuine_steps": graded + len(quantified_unpredicted),
            "genuine_misses": len(genuine_misses),
        },
        "input_steps": input_steps,
        "ungradeable_steps": ungradeable_steps,
        "duplicate_steps": duplicate_steps,
        "cost_only_predicted_nodes": cost_only_nodes,
        "coverage": {
            "definition": METHODOLOGY["node_coverage"],
            "distinct_non_seed_nodes": len(non_seed_nodes),
            "covered_count": len(covered_nodes),
            "uncovered_count": len(uncovered_nodes),
            "fraction": _r6(len(covered_nodes) / len(non_seed_nodes)) if non_seed_nodes else None,
            "covered_nodes": covered_nodes,
            "uncovered_nodes": uncovered_nodes,
        },
        "unquantified_steps": unquantified_steps,
        "delay_accuracy": {
            "definition": METHODOLOGY["delay_accuracy"],
            "predicted_delay_choice": (
                "first-hit delay_d: the node's first event in "
                "stream order, excluding delay-0 cost leaves"),
            "graded_steps": graded,
            "hits": hits,
            "misses": misses,
            "hit_fraction": _r6(hits / graded) if graded else None,
            "abs_error_sum_d": _r6(abs_err_sum),
            "mean_abs_error_d": _r6(abs_err_sum / graded) if graded else None,
            "quantified_unpredicted_steps": quantified_unpredicted,
            "steps": graded_steps,
        },
        "false_positives": {
            "definition": METHODOLOGY["false_positives"],
            "count": len(predicted_unobserved),
            "nodes": predicted_unobserved,
        },
        "predicted_unobserved": predicted_unobserved,
        "genuine_misses": genuine_misses,
        "steps": steps,
        "precision": engine_precision,
        "predicted_non_seed_count": predicted_non_seed_count,
        "baseline_k_hop": baseline,
        "legacy_grading": _legacy_grade(state.events, dataset, seed_node),
    }


def build_scorecard(engine: Engine, entries: list[dict],
                    params: dict | None = None) -> dict:
    """entries: [{dataset, replay_id, dataset_file?, dataset_sha256?}]"""
    replays = [grade_replay(engine, e["dataset"], e["replay_id"],
                            dataset_file=e.get("dataset_file"),
                            dataset_sha256=e.get("dataset_sha256"),
                            params=params)
               for e in entries]

    def _sum(key_path, items):
        total = 0
        for it in items:
            v = it
            for k in key_path:
                v = v[k]
            total += v or 0
        return total

    cov_cov = _sum(("coverage", "covered_count"), replays)
    cov_den = _sum(("coverage", "distinct_non_seed_nodes"), replays)
    graded = _sum(("delay_accuracy", "graded_steps"), replays)
    hits = _sum(("delay_accuracy", "hits"), replays)
    abs_sum = round(sum(r["delay_accuracy"]["abs_error_sum_d"] for r in replays), 6)
    genuine_all = []
    ungradeable_all = []
    for r in replays:
        for m in r["genuine_misses"]:
            genuine_all.append({"replay_id": r["id"], **m})
        for m in r["ungradeable_steps"]:
            ungradeable_all.append({"replay_id": r["id"], **m})
    # Pooled statistics: CIs, budget-matched k-hop baseline,
    # legacy grading on the same runs, small-sample statement.
    base_cov = _sum(("baseline_k_hop", "covered_count"), replays)
    base_pred = _sum(("baseline_k_hop", "predicted_count"), replays)
    eng_pred = _sum(("predicted_non_seed_count",), replays)
    legacy_cov_cov = _sum(("legacy_grading", "coverage_covered"), replays)
    legacy_cov_den = _sum(("legacy_grading", "coverage_distinct_non_seed_nodes"), replays)
    legacy_graded = _sum(("legacy_grading", "delay_graded_steps"), replays)
    legacy_hits = _sum(("legacy_grading", "delay_hits"), replays)
    legacy_abs = round(sum(
        (r["legacy_grading"]["mean_abs_error_d"] or 0.0)
        * r["legacy_grading"]["delay_graded_steps"] for r in replays), 6)
    statistics = {
        "method": METHODOLOGY["statistics"],
        "coverage_ci95_clopper_pearson": clopper_pearson(cov_cov, cov_den),
        "delay_hit_rate_ci95_clopper_pearson": clopper_pearson(hits, graded),
        "engine_precision": _r6(cov_cov / eng_pred) if eng_pred else None,
        "engine_predicted_non_seed_nodes": eng_pred,
        "baseline_k_hop_pooled": {
            "covered_count": base_cov,
            "predicted_count": base_pred,
            "coverage_fraction": _r6(base_cov / cov_den) if cov_den else None,
            "precision": _r6(base_cov / base_pred) if base_pred else None,
        },
        "small_sample": {
            "observed_steps": _sum(("counts", "observed_steps"), replays),
            "graded_steps": graded,
            "distinct_non_seed_nodes": cov_den,
            "one_graded_step_worth_hit_rate_points": (
                _r6(100.0 / graded) if graded else None),
            "one_node_worth_coverage_points": (
                _r6(100.0 / cov_den) if cov_den else None),
        },
    }
    legacy_aggregate = {
        "label": ("Legacy grading rules (max-over-events "
                  "estimator, no node dedupe, delay-0 cost leaves "
                  "counted as predictions) computed on the SAME "
                  "engine runs — for continuity only; NOT "
                  "comparable to the headline grading."),
        "coverage_covered": legacy_cov_cov,
        "coverage_distinct_non_seed_nodes": legacy_cov_den,
        "coverage_fraction": (_r6(legacy_cov_cov / legacy_cov_den)
                              if legacy_cov_den else None),
        "delay_graded_steps": legacy_graded,
        "delay_hits": legacy_hits,
        "mean_abs_error_d": (_r6(legacy_abs / legacy_graded)
                             if legacy_graded else None),
        "false_positives": _sum(("legacy_grading", "false_positives"), replays),
    }
    aggregate = {
        "replays": len(replays),
        "counts": {
            "observed_steps": _sum(("counts", "observed_steps"), replays),
            "input_steps": _sum(("counts", "input_steps"), replays),
            "non_seed_steps": _sum(("counts", "non_seed_steps"), replays),
            "unquantified_steps": _sum(("counts", "unquantified_steps"), replays),
            "ungradeable_steps": _sum(("counts", "ungradeable_steps"), replays),
            "duplicate_steps": _sum(("counts", "duplicate_steps"), replays),
            "genuine_misses": len(genuine_all),
        },
        "coverage": {
            "definition": ("Pooled node coverage: covered distinct "
                           "non-seed nodes summed over replays / distinct "
                           "non-seed observed nodes summed over replays. A "
                           "node appearing in two replays counts once per "
                           "replay (denominators are per-replay by "
                           "construction)."),
            "distinct_non_seed_nodes": cov_den,
            "covered_count": cov_cov,
            "uncovered_count": cov_den - cov_cov,
            "fraction": _r6(cov_cov / cov_den) if cov_den else None,
        },
        "delay_accuracy": {
            "definition": METHODOLOGY["delay_accuracy"],
            "graded_steps": graded,
            "hits": hits,
            "misses": graded - hits,
            "hit_fraction": _r6(hits / graded) if graded else None,
            "abs_error_sum_d": abs_sum,
            "mean_abs_error_d": _r6(abs_sum / graded) if graded else None,
        },
        "false_positives": {
            "definition": METHODOLOGY["false_positives"],
            "count": _sum(("false_positives", "count"), replays),
            "by_replay": {r["id"]: r["false_positives"]["nodes"] for r in replays},
        },
        "statistics": statistics,
        "legacy_grading": legacy_aggregate,
    }
    return {
        "artifact": "supermap-scorecard",
        "version": 2,
        "methodology": METHODOLOGY,
        "honesty_caveats": HONESTY_CAVEATS,
        "graph": {
            "hash": engine.graph.hash,
            "version": engine.graph.version,
            "nodes": len(engine.graph.nodes),
            "edges": len(engine.graph.edges),
        },
        "replays": replays,
        "aggregate": aggregate,
        "genuine_misses": genuine_all,
        "ungradeable_steps": ungradeable_all,
        "previous_published": PREVIOUS_PUBLISHED,
    }


# ---------------------------------------------------------------------------
# Markdown exhibit
# ---------------------------------------------------------------------------

def _f(x) -> str:
    if x is None:
        return "—"
    if isinstance(x, float):
        return f"{x:.6f}".rstrip("0").rstrip(".") if x != int(x) else f"{x:.1f}"
    return str(x)


def _pct(frac) -> str:
    return "—" if frac is None else f"{frac * 100:.1f}%"


def render_markdown(card: dict) -> str:
    L: list[str] = []
    L.append("# SupermAP Scorecard — decomposed backtest grading")
    L.append("")
    L.append("Every number below carries its denominator definition. The full "
             "methodology is in the Methodology section and is embedded in "
             "scorecard.json alongside these numbers.")
    L.append("")
    L.append("## Headline")
    L.append("")
    L.append("| Replay | Observed steps | Input steps (seed, excluded) | Ungradeable "
             "steps (no evidence) | Node coverage "
             "(covered / distinct non-seed nodes) | Delay grading (hits / graded) | "
             "Mean \\|observed − predicted\\| (graded steps) | False positives "
             "(predicted, never observed) | Genuine misses |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for r in card["replays"]:
        cov, da = r["coverage"], r["delay_accuracy"]
        L.append(
            f"| {r['id']} | {r['counts']['observed_steps']} | {r['counts']['input_steps']} "
            f"| {r['counts']['ungradeable_steps']} "
            f"| {cov['covered_count']} / {cov['distinct_non_seed_nodes']} ({_pct(cov['fraction'])}) "
            f"| {da['hits']} / {da['graded_steps']} | {_f(da['mean_abs_error_d'])} d "
            f"| {r['false_positives']['count']} | {r['counts']['genuine_misses']} |")
    agg = card["aggregate"]
    acov, ada = agg["coverage"], agg["delay_accuracy"]
    L.append(
        f"| **Aggregate (pooled)** | {agg['counts']['observed_steps']} | "
        f"{agg['counts']['input_steps']} | {agg['counts']['ungradeable_steps']} | "
        f"{acov['covered_count']} / "
        f"{acov['distinct_non_seed_nodes']} ({_pct(acov['fraction'])}) | {ada['hits']} / "
        f"{ada['graded_steps']} | {_f(ada['mean_abs_error_d'])} d | "
        f"{agg['false_positives']['count']} | {agg['counts']['genuine_misses']} |")
    L.append("")
    L.append("Denominators: node coverage is over distinct non-seed nodes with "
             "evidence-backed observed effects; delay grading is over non-seed steps "
             "with observed_delay_d > 0 at predicted nodes only, one graded step per "
             "node (earliest observed step; later quantified steps at the same node "
             "are duplicates, listed and in no denominator); the mean error is over "
             "those graded steps only. The predicted delay is the node's FIRST-HIT "
             "delay (first event in stream order, delay-0 cost leaves excluded); "
             "a node whose only events are delay-0 cost leaves counts as NOT "
             "predicted. Input steps, unquantified steps, ungradeable steps (no "
             "evidence source — never a hit, never a miss), duplicate steps, and "
             "quantified steps at unpredicted nodes are excluded from both rates "
             "and are listed per replay below.")
    L.append("")
    # --- Statistical honesty --------------------------------
    st = agg["statistics"]
    bp = st["baseline_k_hop_pooled"]
    L.append("## Statistical honesty")
    L.append("")
    L.append(f"- Coverage {acov['covered_count']}/{acov['distinct_non_seed_nodes']} "
             f"({_pct(acov['fraction'])}), exact Clopper-Pearson 95% CI: "
             f"[{_pct(st['coverage_ci95_clopper_pearson'][0])}, "
             f"{_pct(st['coverage_ci95_clopper_pearson'][1])}].")
    L.append(f"- Delay hit rate {ada['hits']}/{ada['graded_steps']} "
             f"({_pct(ada['hit_fraction'])}), exact Clopper-Pearson 95% CI: "
             f"[{_pct(st['delay_hit_rate_ci95_clopper_pearson'][0])}, "
             f"{_pct(st['delay_hit_rate_ci95_clopper_pearson'][1])}].")
    ss = st["small_sample"]
    L.append(f"- Small sample, stated plainly: {ss['observed_steps']} observed "
             f"steps across {agg['replays']} replays yield {ss['graded_steps']} "
             f"graded steps over {ss['distinct_non_seed_nodes']} distinct "
             f"non-seed nodes. One graded step is worth "
             f"{_f(ss['one_graded_step_worth_hit_rate_points'])} points of hit "
             f"rate; one node is worth {_f(ss['one_node_worth_coverage_points'])} "
             "points of coverage. Treat every rate here as a small-sample "
             "estimate, not a measurement.")
    L.append("")
    L.append("### Naive baseline — directed k-hop, same events, same node budget")
    L.append("")
    L.append("Baseline method: directed BFS from the seed over the graph's "
             "directed edges, whole hop levels, until the non-seed node count "
             "first reaches the engine's predicted non-seed count for the "
             "same replay. Precision = covered / predicted.")
    L.append("")
    L.append("| Replay | Engine coverage | Baseline coverage (k hops, budget) | "
             "Engine precision | Baseline precision |")
    L.append("|---|---|---|---|---|")
    for r in card["replays"]:
        b = r["baseline_k_hop"]
        L.append(f"| {r['id']} | {r['coverage']['covered_count']} / "
                 f"{r['coverage']['distinct_non_seed_nodes']} "
                 f"({_pct(r['coverage']['fraction'])}) | {b['covered_count']} / "
                 f"{r['coverage']['distinct_non_seed_nodes']} "
                 f"({_pct(b['coverage_fraction'])}) (k={b['hops']}, "
                 f"budget {b['budget_nodes']}, predicted {b['predicted_count']}) "
                 f"| {_pct(r['precision'])} | {_pct(b['precision'])} |")
    L.append(f"| **Pooled** | {acov['covered_count']} / "
             f"{acov['distinct_non_seed_nodes']} ({_pct(acov['fraction'])}) | "
             f"{bp['covered_count']} / {acov['distinct_non_seed_nodes']} "
             f"({_pct(bp['coverage_fraction'])}) | {_pct(st['engine_precision'])} "
             f"| {_pct(bp['precision'])} |")
    L.append("")
    for metric, eng_v, base_v in (
            ("coverage", acov["fraction"], bp["coverage_fraction"]),
            ("precision", st["engine_precision"], bp["precision"])):
        if eng_v is None or base_v is None:
            continue
        verdict = ("beats" if eng_v > base_v else
                   "does NOT beat" if eng_v < base_v else "ties")
        L.append(f"- On {metric}, the engine ({_pct(eng_v)}) {verdict} the "
                 f"naive baseline ({_pct(base_v)}).")
    L.append("")
    # --- Comparison with legacy grading ------------------------------
    lg = agg["legacy_grading"]
    pp = card["previous_published"]
    L.append("## Comparison with legacy grading (labelled, not comparable)")
    L.append("")
    L.append("| Grading | Engine | Coverage | Delay hits | Mean |error| (d) | "
             "False positives |")
    L.append("|---|---|---|---|---|---|")
    L.append(f"| Headline (first-hit, node dedupe, cost leaves "
             f"excluded) | current | {acov['covered_count']} / "
             f"{acov['distinct_non_seed_nodes']} | {ada['hits']} / "
             f"{ada['graded_steps']} | {_f(ada['mean_abs_error_d'])} | "
             f"{agg['false_positives']['count']} |")
    L.append(f"| Legacy rules on the SAME runs (max-over-events, no dedupe, "
             f"cost leaves counted) | current | "
             f"{lg['coverage_covered']} / "
             f"{lg['coverage_distinct_non_seed_nodes']} | {lg['delay_hits']} / "
             f"{lg['delay_graded_steps']} | {_f(lg['mean_abs_error_d'])} | "
             f"{lg['false_positives']} |")
    L.append(f"| {pp['label']} | original | {pp['coverage_covered']} / "
             f"{pp['coverage_distinct_non_seed_nodes']} | {pp['delay_hits']} / "
             f"{pp['delay_graded_steps']} | {_f(pp['mean_abs_error_d'])} | "
             f"{pp['false_positives']} |")
    L.append("")
    L.append("The three rows answer three different questions (different "
             "engine and/or different grading rules); no row may be quoted "
             "as a like-for-like improvement or regression against another.")
    L.append("")

    for r in card["replays"]:
        cov, da = r["coverage"], r["delay_accuracy"]
        L.append(f"## Replay: {r['id']}" + (f" — {r['title']}" if r.get("title") else ""))
        L.append("")
        L.append(f"- Seed (disruption) node: `{r['seed_node']}` — "
                 f"{r['counts']['input_steps']} input step(s) at this node excluded from grading.")
        L.append(f"- Duration used: {_f(r['duration_used_d'])} d ({r['duration_source']}).")
        basis = r.get("capacity_loss_basis") or {}
        if basis:
            L.append(f"- Capacity loss used: {_f(r['capacity_loss_used'])} "
                     f"(basis kind: {basis.get('kind')}). Basis: {basis.get('derivation')}")
            if basis.get("source"):
                L.append(f"- Capacity loss source: {basis.get('source')}")
        else:
            L.append(f"- Capacity loss used: {_f(r['capacity_loss_used'])} "
                     "(dataset states no capacity_loss_basis).")
        if r.get("disruption_notes"):
            L.append(f"- Dataset notes: {r['disruption_notes']}")
        for d in r["disclosures"]:
            L.append(f"- Disclosure — {d['field']}: {d['action']} "
                     f"(value {_f(d.get('value'))}"
                     + (f", dataset value {_f(d.get('dataset_value'))}" if "dataset_value" in d else "")
                     + f"). {d.get('note', '')}")
        L.append(f"- Run: run_id `{r['run_id']}`, stream hash `{r['stream_hash']}`, "
                 f"{r['event_count']} events; dataset file `{r.get('dataset_file')}`, "
                 f"dataset SHA-256 `{r.get('dataset_sha256')}`.")
        L.append("")
        L.append(f"### Node coverage — {cov['covered_count']} / {cov['distinct_non_seed_nodes']} "
                 f"distinct non-seed nodes ({_pct(cov['fraction'])})")
        L.append("")
        L.append(f"- Covered nodes: {', '.join('`' + n + '`' for n in cov['covered_nodes']) or 'none'}")
        L.append(f"- Uncovered nodes: {', '.join('`' + n + '`' for n in cov['uncovered_nodes']) or 'none'}")
        L.append("")
        L.append(f"### Delay accuracy — {da['hits']} hit / {da['misses']} miss "
                 f"across {da['graded_steps']} graded step(s); "
                 f"mean |observed − predicted| = {_f(da['mean_abs_error_d'])} d")
        L.append("")
        L.append("Predicted delay per node = first-hit delay_d (first event "
                 "in stream order, delay-0 cost leaves excluded). HIT = "
                 "observed within [0.5×, 2×] of predicted. One graded step "
                 "per node: the earliest observed step grades.")
        L.append("")
        if da["steps"]:
            L.append("| Node | Observed (d) | Predicted — first-hit (d) | Window | Result | \\|Error\\| (d) |")
            L.append("|---|---|---|---|---|---|")
            for s in da["steps"]:
                L.append(f"| `{s['node_id']}` | {_f(s['observed_delay_d'])} | "
                         f"{_f(s['predicted_delay_d'])} | [{_f(s['window'][0])}, {_f(s['window'][1])}] "
                         f"| {s['result']} | {_f(s.get('delay_error_d'))} |")
            L.append("")
        else:
            L.append("No graded steps in this replay.")
            L.append("")
        if da["quantified_unpredicted_steps"]:
            L.append("Quantified steps at unpredicted nodes (genuine misses, excluded "
                     "from the mean above):")
            L.append("")
            for s in da["quantified_unpredicted_steps"]:
                L.append(f"- `{s['node_id']}`: observed {_f(s['observed_delay_d'])} d, "
                         "no prediction. " + (s.get("effect") or ""))
            L.append("")
        L.append(f"### Input steps (seed node, excluded from grading) — {len(r['input_steps'])}")
        L.append("")
        for s in r["input_steps"]:
            L.append(f"- `{s['node_id']}`: observed {_f(s['observed_delay_d'])} d"
                     + (f" ({s['evidence_date']})" if s.get("evidence_date") else "")
                     + (f" — {s['effect']}" if s.get("effect") else ""))
        if not r["input_steps"]:
            L.append("- none")
        L.append("")
        L.append(f"### Unquantified steps (observed_delay_d = 0; in coverage, not delay-graded) "
                 f"— {len(r['unquantified_steps'])}")
        L.append("")
        for s in r["unquantified_steps"]:
            if s["predicted_delay_d"] is not None:
                line = (f"- `{s['node_id']}`: predicted delay (first-hit) "
                        f"{_f(s['predicted_delay_d'])} d")
            else:
                line = f"- `{s['node_id']}`: not predicted"
            if s.get("effect"):
                line += f" — {s['effect']}"
            L.append(line)
        if not r["unquantified_steps"]:
            L.append("- none")
        L.append("")
        L.append(f"### Ungradeable steps (no evidence source; never a hit, never a miss) "
                 f"— {len(r['ungradeable_steps'])}")
        L.append("")
        for s in r["ungradeable_steps"]:
            L.append(f"- `{s['node_id']}`: observed {_f(s['observed_delay_d'])} d — "
                     "no evidence source; excluded from coverage and delay grading")
        if not r["ungradeable_steps"]:
            L.append("- none")
        L.append("")
        L.append(f"### Duplicate steps (node already graded; in no denominator) "
                 f"— {len(r['duplicate_steps'])}")
        L.append("")
        for s in r["duplicate_steps"]:
            L.append(f"- `{s['node_id']}`: observed {_f(s['observed_delay_d'])} d — "
                     "duplicate of the earliest graded step at this node "
                     "(one graded step per node per replay)")
        if not r["duplicate_steps"]:
            L.append("- none")
        L.append("")
        L.append(f"### Cost-only predicted nodes (only delay-0 cost leaves; "
                 f"count as NOT predicted) — {len(r['cost_only_predicted_nodes'])}")
        L.append("")
        L.append(", ".join("`" + n + "`" for n in r["cost_only_predicted_nodes"]) or "none")
        L.append("")
        L.append(f"### False positives — {r['false_positives']['count']} predicted node(s) "
                 "with no observed effect")
        L.append("")
        L.append(", ".join("`" + n + "`" for n in r["false_positives"]["nodes"]) or "none")
        L.append("")

    L.append("## Genuine misses — all replays (published, not hidden)")
    L.append("")
    if card["genuine_misses"]:
        L.append("| Replay | Node | Observed (d) | Predicted — first-hit (d) | Why it is a miss |")
        L.append("|---|---|---|---|---|")
        for m in card["genuine_misses"]:
            why = ("node not predicted" if m["predicted_delay_d"] is None
                   else f"outside window [{_f(m['window'][0])}, {_f(m['window'][1])}]")
            L.append(f"| {m['replay_id']} | `{m['node_id']}` | {_f(m['observed_delay_d'])} "
                     f"| {_f(m['predicted_delay_d'])} | {why} |")
    else:
        L.append("None.")
    L.append("")

    L.append("## Ungradeable steps — all replays (no evidence source; listed, not dropped)")
    L.append("")
    if card.get("ungradeable_steps"):
        L.append("| Replay | Node | Observed (d) | Predicted — first-hit (d) |")
        L.append("|---|---|---|---|")
        for m in card["ungradeable_steps"]:
            L.append(f"| {m['replay_id']} | `{m['node_id']}` | "
                     f"{_f(m['observed_delay_d'])} | {_f(m['predicted_delay_d'])} |")
    else:
        L.append("None.")
    L.append("")

    L.append("## Methodology")
    L.append("")
    for i, key in enumerate(("evidence_rule", "seed_exclusion", "node_coverage",
                             "delay_accuracy", "false_positives",
                             "cost_leaf_exclusion", "miss_classification",
                             "statistics", "effective_spec",
                             "determinism"), start=1):
        L.append(f"{i}. **{key.replace('_', ' ').capitalize()}:** {card['methodology'][key]}")
        L.append("")
    L.append("## Honesty caveats")
    L.append("")
    for c in card["honesty_caveats"]:
        L.append(f"- {c}")
    L.append("")
    L.append("## Reproducibility")
    L.append("")
    g = card["graph"]
    L.append(f"- Graph: hash `{g['hash']}`, version `{g['version']}`, "
             f"{g['nodes']} nodes, {g['edges']} edges.")
    for r in card["replays"]:
        L.append(f"- {r['id']}: dataset `{r.get('dataset_file')}` SHA-256 "
                 f"`{r.get('dataset_sha256')}`; run_id `{r['run_id']}`; "
                 f"stream hash `{r['stream_hash']}`.")
    L.append("- No timestamp is embedded in scorecard.json or this file (deliberate): "
             "regenerating from the same datasets + graph yields byte-identical files.")
    L.append("")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# File generation
# ---------------------------------------------------------------------------

def load_entries(replay_paths: list[str | Path]) -> list[dict]:
    entries = []
    for p in replay_paths:
        p = Path(p)
        raw = p.read_bytes()
        entries.append({
            "dataset": json.loads(raw.decode("utf-8")),
            "replay_id": p.stem,
            "dataset_file": p.name,
            "dataset_sha256": hashlib.sha256(raw).hexdigest(),
        })
    return entries


def generate(graph_path: str | Path, replay_paths: list[str | Path],
             out_json: str | Path, out_md: str | Path,
             params: dict | None = None) -> dict:
    """Generate scorecard.json + scorecard.md from files; returns the dict."""
    from .graph import load_graph
    engine = Engine(load_graph(graph_path))
    card = build_scorecard(engine, load_entries(replay_paths), params=params)
    out_json = Path(out_json)
    out_md = Path(out_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(card, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
    out_md.write_text(render_markdown(card), encoding="utf-8")
    return card


def main(argv: list[str] | None = None) -> int:
    """CLI: python -m engine.scorecard [--graph G] [--out-json J]
    [--out-md M] [replay.json ...]"""
    import sys
    argv = list(sys.argv[1:] if argv is None else argv)
    graph_path: Path = REPO_ROOT / "data" / "graph" / "graph.json"
    out_json: Path = REPO_ROOT / "data" / "replay" / "scorecard.json"
    out_md: Path = REPO_ROOT / "data" / "replay" / "scorecard.md"
    positional: list[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--graph":
            graph_path = Path(argv[i + 1]); i += 2
        elif a == "--out-json":
            out_json = Path(argv[i + 1]); i += 2
        elif a == "--out-md":
            out_md = Path(argv[i + 1]); i += 2
        else:
            positional.append(a); i += 1
    if positional:
        replay_paths: list[Path] = [Path(p) for p in positional]
    else:
        replay_dir = REPO_ROOT / "data" / "replay"
        replay_paths = sorted(
            p for p in replay_dir.glob("*.json") if p.name != "scorecard.json")
    card = generate(graph_path, replay_paths, out_json, out_md)
    print(json.dumps({
        "wrote": [str(out_json), str(out_md)],
        "graph_hash": card["graph"]["hash"],
        "aggregate": card["aggregate"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
