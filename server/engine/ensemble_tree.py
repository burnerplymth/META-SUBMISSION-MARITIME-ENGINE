"""Ensemble TREE mode: breadth-first refinement of the
flat ensemble layer in ensemble_stats.

The flat layer runs named combinations of the declared variation
axes. Tree mode organises those combinations as a refinement tree
(a "tree 100, 100 and so on" request): the root is the base variant,
level 1 varies the first axis under the root, level 2 varies the
second axis under EACH level-1 variant, and so on — breadth-first,
one axis per level in ensemble_stats.AXIS_ORDER.

Branching is PER-LEVEL: a tree is described by a list of branching
factors, one per level (branching_list[k] is the factor level k+1
refines with), defaulting to [10, 10, 10, 10, 10] — ten children per
variant at each of the five axis levels. A bare integer is shorthand
for that factor repeated to the requested depth. quote() sizes a
tree before anything runs and prints its arithmetic so an oversized
tree is refused on its numbers, never discovered mid-run.

Counting convention: a level's "runs" are the variants that level
INTRODUCES (the base variant is not one of them: level 1 introduces
fanout_1 variants, level 2 introduces fanout_1 * fanout_2, and so
on), and quoted_runs is the sum over levels. The base variant is the
+1 in total_runs_including_base (= quoted_runs + 1), which is the
total the build accounting closes against.

Budget model: a quote is launchable only when quoted_runs fits BOTH
the effective budget and the hard maximum. The default budget is
1,050,000 and the hard maximum is 3,300,000; a tree over the default
budget can launch only when the budget is explicitly raised (to at
least quoted_runs), and a tree over the hard maximum cannot launch
at any budget.

Two disciplines carry over from the flat layer and are the point of
this module:

  * PRUNING IS A CAUSAL FACT, NOT A SAMPLE. A variant is expanded
    only when its own causal chain fired something beyond the
    disrupted place itself (fired_beyond_seed). The disrupted place
    always registers in its own run, so a variant whose chain reaches
    nothing beyond it has no sub-possibilities to refine: every
    downstream effect was absorbed or fell below the hit threshold,
    the materiality floor. Its would-be descendants are counted in
    the accounting as unexpanded runs, never silently dropped.
  * ACCOUNTING IDENTITIES HOLD BY CONSTRUCTION. With quoted the
    full unpruned total INCLUDING the base variant (quote()'s
    total_runs_including_base), run the variants actually run,
    and unexpanded_runs the descendants pruned variants never had:
    quoted == run + unexpanded_runs, and every run variant is
    exactly one of expanded, pruned or a depth leaf, so
    expanded + pruned + depth_leaves == run.

Aggregates are the flat layer's EnsembleAccumulator: every run
variant's extraction is added to its own accumulator and to every
ancestor's, so a variant's aggregates describe its whole sub-tree
(the variants actually run under it — pruning is stated in the
accounting, never hidden inside an aggregate). Per-variant records
stay compact exactly as in the flat layer; full chain detail is
re-derived by replay, never persisted per variant.

Values at a level are built only from the resolved axis levels:
numeric children interpolate over the span [min(levels), max(levels)]
of levels that resolve_axes already validated against the axis's
bounds, and an interpolated value is a convex combination of the
two endpoints, so every child value lies within the axis's validated
bounds by construction — nothing here can produce a value the flat
layer would have rejected, and nothing is ever clamped.
"""
from __future__ import annotations

from collections import deque

from . import ensemble_stats as es

# ---------------------------------------------------------------- limits

DEFAULT_LEVEL_FACTOR = 10
DEFAULT_BRANCHING = [10, 10, 10, 10, 10]
DEFAULT_DEPTH = 5
MAX_BRANCHING = 1_000_000
DEFAULT_BUDGET = 1_050_000
HARD_MAX = 3_300_000

# Measured rates for the flat ensemble layer, used only to
# size a quote before anything runs.
EST_SECONDS_PER_VARIANT = 0.0024
EST_BYTES_PER_VARIANT = 692
ESTIMATOR = {
    "seconds_per_variant": EST_SECONDS_PER_VARIANT,
    "bytes_per_variant": EST_BYTES_PER_VARIANT,
    "basis": ("estimate from the flat ensemble layer's measured rates: "
              "10,000 variants in 23.8 s, 6.92 MB of job record"),
}

_PRUNE_MESSAGE = (
    "not expanded — this variant's causal chain reached no place "
    "beyond the disrupted place itself, so it has no "
    "sub-possibilities to refine")


def _invalid(message: str):
    raise es.EnsembleStatsError(message, code="invalid_request")


# ------------------------------------------------------------- validation

def validate_tree_params(branching=None, depth=None) -> tuple[list[int], int]:
    """Resolve (branching, depth) to (branching_list, depth).

    branching None -> [DEFAULT_LEVEL_FACTOR] * depth (depth itself
    defaulting to DEFAULT_DEPTH); a bare int is shorthand for that
    factor repeated to the depth; a list/tuple of ints is the
    per-level list itself — depth None then takes len(list), and a
    given depth must equal len(list). Depth must lie in
    [0, len(es.AXIS_ORDER)] and depth 0 resolves to an empty list.
    Every factor must be an int in [1, 1,000,000]. Any non-integer
    (bools included), out-of-range value or list/depth mismatch is
    rejected — never coerced, never clamped — with
    EnsembleStatsError(code="invalid_request").
    """
    if depth is not None:
        if isinstance(depth, bool) or not isinstance(depth, int):
            _invalid(f"depth must be an integer, got {depth!r}")
        if not 0 <= depth <= len(es.AXIS_ORDER):
            _invalid(
                f"depth {depth} outside [0, {len(es.AXIS_ORDER)}] — one "
                f"level per declared axis (rejected, never clamped)")

    if branching is None:
        resolved_depth = depth if depth is not None else DEFAULT_DEPTH
        return [DEFAULT_LEVEL_FACTOR] * resolved_depth, resolved_depth

    if isinstance(branching, bool):
        _invalid(f"branching must be an integer or a list of integers, "
                 f"got {branching!r}")
    if isinstance(branching, int):
        if not 1 <= branching <= MAX_BRANCHING:
            _invalid(
                f"branching {branching} outside [1, {MAX_BRANCHING}] "
                f"(rejected, never clamped)")
        resolved_depth = depth if depth is not None else DEFAULT_DEPTH
        return [branching] * resolved_depth, resolved_depth

    if isinstance(branching, (list, tuple)):
        factors = list(branching)
        for factor in factors:
            if isinstance(factor, bool) or not isinstance(factor, int):
                _invalid(
                    f"branching factors must be integers, got {factor!r}")
            if not 1 <= factor <= MAX_BRANCHING:
                _invalid(
                    f"branching factor {factor} outside "
                    f"[1, {MAX_BRANCHING}] (rejected, never clamped)")
        if depth is None:
            if len(factors) > len(es.AXIS_ORDER):
                _invalid(
                    f"branching list of {len(factors)} levels exceeds "
                    f"the {len(es.AXIS_ORDER)} declared axes "
                    f"(rejected, never clamped)")
            return factors, len(factors)
        if depth != len(factors):
            _invalid(
                f"depth {depth} does not match the {len(factors)} "
                f"per-level branching factors given")
        return factors, depth

    _invalid(f"branching must be an integer, a list of integers, or "
             f"None, got {branching!r}")


def validate_budget(budget=None) -> int:
    """Resolve the effective budget: None -> DEFAULT_BUDGET; any
    other value must be an int (bools rejected) >= 1, else
    EnsembleStatsError(code="invalid_request") — never clamped."""
    if budget is None:
        return DEFAULT_BUDGET
    if isinstance(budget, bool) or not isinstance(budget, int):
        _invalid(f"budget must be an integer, got {budget!r}")
    if budget < 1:
        _invalid(f"budget {budget} is below 1 (rejected, never clamped)")
    return budget


# ------------------------------------------------------------- fanout

def fanout_for(axes: dict, axis: str, level_branching: int) -> int:
    """How many children one variant gets at a level refining `axis`
    with this level's branching factor.

    Shape: the declared shapes in order, at most `level_branching`
    of them (the first fanout values). A numeric axis with a single
    resolved level is fixed: fanout 1. Any other numeric axis:
    `level_branching`.
    """
    if axis == "shape":
        return min(len(axes["shape"]), level_branching)
    if len(axes[axis]) == 1:
        return 1
    return level_branching


def value_at(axes: dict, axis: str, level_branching: int, i: int):
    """The i-th child value (0-based) for a level refining `axis`
    with this level's branching factor.

    Shape takes the declared shapes in order. A numeric axis with a
    single resolved level takes that level. With level_branching
    == 1 a numeric axis takes its base value (the declared level
    nearest the axis centre, ensemble_stats.base_combination's
    rule). With level_branching >= 2 the values are evenly spaced
    over the span of the resolved levels, endpoints inclusive.
    Every value lies within the axis's validated bounds by
    construction: the endpoints are resolved levels (validated by
    resolve_axes) and every interior value is a convex combination
    of them (see the module docstring).
    """
    levels = axes[axis]
    if axis == "shape":
        return levels[i]
    if len(levels) == 1:
        return levels[0]
    if level_branching == 1:
        centre = es.CENTRE[axis]
        return min(levels, key=lambda lv: (abs(lv - centre), lv))
    lo, hi = min(levels), max(levels)
    if i == 0:
        return lo
    if i == level_branching - 1:
        return hi
    return lo + (hi - lo) * i / (level_branching - 1)


def child_values(axes: dict, axis: str, level_branching: int) -> list:
    """All child values for a level refining `axis`, in child order.
    Only called for modest fanouts."""
    return [value_at(axes, axis, level_branching, i)
            for i in range(fanout_for(axes, axis, level_branching))]


def _rule_for(axes: dict, axis: str, fanout: int) -> str:
    if axis == "shape":
        return "levels"
    if len(axes[axis]) == 1:
        return "fixed"
    return "interpolated"


def _sequence_branching(branching) -> list[int]:
    """The per-level factors for an axis sequence: a list/tuple is
    the per-level list itself; a bare int or None is expanded over
    every declared axis (the sequence describes the full plan)."""
    if isinstance(branching, (list, tuple)):
        return validate_tree_params(branching, len(branching))[0]
    return validate_tree_params(branching, None)[0]


def axis_sequence(axes: dict, branching=None) -> list[dict]:
    """The level plan for the given per-level branching: one entry
    per level, in AXIS_ORDER — level (1-based), axis, "branching"
    (this level's factor), fanout, how the children are chosen
    ("interpolated" over the levels' span, "levels" for shape,
    "fixed" for a single-level axis), the child values themselves
    when the fanout is modest (<= 128, else None), the span for
    interpolated numeric axes (else None), and the axis description
    printed beside the numbers."""
    branching_list = _sequence_branching(branching)
    out = []
    for idx, factor in enumerate(branching_list):
        axis = es.AXIS_ORDER[idx]
        fanout = fanout_for(axes, axis, factor)
        rule = _rule_for(axes, axis, fanout)
        levels = axes[axis]
        out.append({
            "level": idx + 1,
            "axis": axis,
            "branching": factor,
            "fanout": fanout,
            "rule": rule,
            "values": child_values(axes, axis, factor)
            if fanout <= 128 else None,
            "span": [min(levels), max(levels)]
            if rule == "interpolated" else None,
            "description": es.AXIS_DESCRIPTIONS[axis],
        })
    return out


# ---------------------------------------------------------------- quote

def quote(axes: dict, branching=None, depth=None, budget=None) -> dict:
    """Size a tree job before anything runs: for levels 1..depth,
    the axis, the level's branching factor, its fanout, the runs
    that level INTRODUCES (the base variant is not counted — level
    1 introduces fanout_1 runs) and the cumulative run count. The
    quoted total (quoted_runs) is the sum of the level runs;
    total_runs_including_base adds the base variant itself. The
    budget fields state the effective budget, the default budget,
    the hard maximum, and whether the tree fits each (launchable =
    it fits both; requires_raised_budget = it exceeds the default
    budget). Wall-time / record-size estimates are computed from
    quoted_runs at the flat layer's measured rates (ESTIMATOR).
    The arithmetic string prints the sum it claims and levels_table
    prints each level's runs beside its cumulative count,
    thousands-separated."""
    branching_list, depth = validate_tree_params(branching, depth)
    effective_budget = validate_budget(budget)
    levels = []
    parents = 1
    cumulative = 0
    for idx in range(depth):
        axis = es.AXIS_ORDER[idx]
        factor = branching_list[idx]
        fanout = fanout_for(axes, axis, factor)
        runs = parents * fanout
        cumulative += runs
        levels.append({"level": idx + 1, "axis": axis,
                       "branching": factor, "fanout": fanout,
                       "runs": runs, "cumulative": cumulative})
        parents = runs
    quoted_runs = cumulative
    est_bytes = int(quoted_runs * EST_BYTES_PER_VARIANT)
    est_wall_seconds = round(quoted_runs * EST_SECONDS_PER_VARIANT, 1)
    if levels:
        arithmetic = " + ".join(f"{lvl['runs']:,}" for lvl in levels) \
            + f" = {quoted_runs:,}"
    else:
        arithmetic = "0 = 0"
    return {
        "branching": branching_list,
        "depth": depth,
        "levels": levels,
        "quoted_runs": quoted_runs,
        "total_runs_including_base": quoted_runs + 1,
        "budget": effective_budget,
        "default_budget": DEFAULT_BUDGET,
        "hard_max": HARD_MAX,
        "within_budget": quoted_runs <= effective_budget,
        "within_hard_max": quoted_runs <= HARD_MAX,
        "launchable": quoted_runs <= effective_budget
        and quoted_runs <= HARD_MAX,
        "requires_raised_budget": quoted_runs > DEFAULT_BUDGET,
        "est_wall_seconds": est_wall_seconds,
        "est_wall_minutes": round(est_wall_seconds / 60, 1),
        "est_bytes": est_bytes,
        "est_mb": round(est_bytes / 1e6, 1),
        "estimator": ESTIMATOR,
        "arithmetic": arithmetic,
        "levels_table": " · ".join(
            f"L{lvl['level']} {lvl['runs']:,} (cum {lvl['cumulative']:,})"
            for lvl in levels),
    }


def refusal_message(q: dict) -> str:
    """Display text refusing a quote that cannot launch: the level
    table verbatim, the quoted total, why it cannot launch, the
    wall-time / size estimates, and that nothing ran.

    Over the hard maximum, no budget can launch the tree. Over the
    effective budget (but within the hard maximum), raising the
    budget to at least the quoted total would allow the launch."""
    quoted = q["quoted_runs"]
    estimates = (
        f"Estimated wall time {q['est_wall_seconds']:,} seconds "
        f"({q['est_wall_minutes']:,} minutes); estimated size "
        f"{q['est_bytes']:,} bytes ({q['est_mb']:,} MB).")
    if not q["within_hard_max"]:
        return (
            f"Refused: this combination tree would introduce "
            f"{quoted:,} variants ({q['arithmetic']}; levels: "
            f"{q['levels_table']}), which exceeds the hard maximum "
            f"of {q['hard_max']:,} variants, and no budget can "
            f"launch it. {estimates} Nothing ran — reduce the "
            f"branching, the depth, or both, and quote again.")
    if not q["within_budget"]:
        return (
            f"Refused: this combination tree would introduce "
            f"{quoted:,} variants ({q['arithmetic']}; levels: "
            f"{q['levels_table']}), which exceeds the budget of "
            f"{q['budget']:,} variants; raising the budget to at "
            f"least {quoted:,} (hard maximum {q['hard_max']:,}) "
            f"would allow the launch. {estimates} Nothing ran "
            f"under the current budget.")
    return (
        f"This combination tree would introduce {quoted:,} variants "
        f"({q['arithmetic']}; levels: {q['levels_table']}), which "
        f"fits the budget of {q['budget']:,} and the hard maximum "
        f"of {q['hard_max']:,}, so no refusal applies. {estimates} "
        f"Nothing ran.")


# -------------------------------------------------------------- pruning

def fired_beyond_seed(places_fired, legs_fired, seed_node_id) -> bool:
    """True iff a variant's causal chain fired anything beyond the
    disrupted place itself: any leg fired, or any fired place other
    than the seed place. The disrupted place itself always registers
    in its own run, so False means every downstream effect was
    absorbed or fell below the hit threshold."""
    if legs_fired:
        return True
    return any(place != seed_node_id for place in places_fired)


def subtree_full_runs(fanouts: list[int], level: int, depth: int) -> int:
    """Runs in the full unpruned sub-tree of a variant at `level`
    (0-based), including the variant itself. `fanouts` is the
    per-level fanout list for levels 1..depth."""
    total = 1
    level_runs = 1
    for idx in range(level, depth):
        level_runs *= fanouts[idx]
        total += level_runs
    return total


# ---------------------------------------------------------------- build

def build_tree(axes: dict, branching, depth, base_shape: str,
               seed_node_id, run_one, on_progress=None) -> dict:
    """Run a tree breadth-first and return its variants, per-variant
    accumulators, accounting, level plan and quote.

    branching / depth accept the amended forms (an int, a per-level
    list, or None; depth may be None when branching is a list or
    None) and are resolved via validate_tree_params: the children
    of a variant at level k are created with level k+1's factor,
    branching_list[k]. run_one(index, combination) ->
    (core_record, extraction), where core_record carries
    combination / run_id / stream_hash / outcomes / places_fired /
    legs_fired and extraction is an EnsembleAccumulator.add input
    ({"outcomes", "places", "legs"}). The root runs the base
    combination as index 0; each variant popped in breadth-first
    order is a depth leaf at level == depth, is pruned when its own
    chain fired nothing beyond the disrupted place, and otherwise
    is expanded: its children (its combination with the next
    level's axis replaced by each child value, in order) run and
    are appended. Every extraction is added to its own variant's
    accumulator and to every ancestor's. on_progress, when given,
    is called with the running run count after each run. The
    accounting's "quoted" is the full unpruned total INCLUDING the
    base variant and "quoted_variants" the variants the levels
    introduce, so quoted == run + unexpanded_runs by construction.
    """
    branching_list, depth = validate_tree_params(branching, depth)
    q = quote(axes, branching_list, depth)
    seq = axis_sequence(axes, branching_list)[:depth]
    fanouts = [lvl["fanout"] for lvl in q["levels"]]

    nodes: list[dict] = []
    accumulators: dict[int, es.EnsembleAccumulator] = {}

    def _run(parent, level, ordinal, path, axis, value, combination):
        index = len(nodes)
        core, extraction = run_one(index, dict(combination))
        node = {
            "index": index,
            "parent": parent,
            "children": [],
            "level": level,
            "ordinal": ordinal,
            "path": list(path),
            "axis": axis,
            "value": value,
            "seed": index,
            "combination": {a: combination[a] for a in es.AXIS_ORDER},
            "run_id": core["run_id"],
            "stream_hash": core["stream_hash"],
            "outcomes": core["outcomes"],
            "places_fired": list(core["places_fired"]),
            "legs_fired": list(core["legs_fired"]),
            "expanded": False,
            "pruned": False,
            "prune_reason": None,
            "subtree_run_count": 1,
            "aggregates": None,
        }
        nodes.append(node)
        accumulators[index] = es.EnsembleAccumulator()
        cur = index
        while cur is not None:
            accumulators[cur].add(extraction)
            cur = nodes[cur]["parent"]
        if on_progress is not None:
            on_progress(len(nodes))
        return index

    root = _run(None, 0, 0, [], None, None,
                es.base_combination(axes, base_shape))
    queue = deque([root])
    expanded = pruned = depth_leaves = 0
    while queue:
        idx = queue.popleft()
        node = nodes[idx]
        if node["level"] == depth:
            depth_leaves += 1
            continue
        if not fired_beyond_seed(node["places_fired"],
                                 node["legs_fired"], seed_node_id):
            node["pruned"] = True
            node["prune_reason"] = {"code": "no_branch_fired",
                                     "message": _PRUNE_MESSAGE}
            pruned += 1
            continue
        node["expanded"] = True
        expanded += 1
        child_axis = es.AXIS_ORDER[node["level"]]
        fanout = fanouts[node["level"]]
        for i in range(fanout):
            value = value_at(axes, child_axis,
                             branching_list[node["level"]], i)
            combo = dict(node["combination"])
            combo[child_axis] = value
            child = _run(idx, node["level"] + 1, i,
                         node["path"] + [i], child_axis, value, combo)
            node["children"].append(child)
            queue.append(child)

    for node in reversed(nodes):
        node["subtree_run_count"] = 1 + sum(
            nodes[c]["subtree_run_count"] for c in node["children"])

    unexpanded_runs = sum(
        subtree_full_runs(fanouts, node["level"], depth) - 1
        for node in nodes if node["pruned"])
    accounting = {
        "quoted": q["total_runs_including_base"],
        "quoted_variants": q["quoted_runs"],
        "run": len(nodes),
        "expanded": expanded,
        "pruned": pruned,
        "depth_leaves": depth_leaves,
        "unexpanded_runs": unexpanded_runs,
    }
    return {
        "nodes": nodes,
        "accumulators": accumulators,
        "accounting": accounting,
        "axis_sequence": seq,
        "quote": q,
    }


def node_aggregates(acc: es.EnsembleAccumulator, graph_nodes: dict) -> dict:
    """Serialise one variant's sub-tree accumulator (the flat
    layer's blocks): variant count, outcome statistics, and the
    per-place / per-leg branch figures over the variants actually
    run in that sub-tree."""
    return {
        "variants": acc.variants,
        "outcomes": acc.outcome_block(),
        "places": acc.place_branches(graph_nodes),
        "legs": acc.leg_branches(graph_nodes),
    }
