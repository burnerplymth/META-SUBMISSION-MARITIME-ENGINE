"""Ensemble statistics layer.

A Monte-Carlo-style layer: run up to 10,000 CAUSAL variants of one
disruption through the deterministic chain engine and combine the results —
averages, distribution curves, percentiles, and per-branch fire counts
with conditional delay statistics. The difference from Monte Carlo is
the whole point of the product, and this module holds that line:

  * VARIANTS, NOT DICE. A variant is a named combination of declared
    variation axes (below). Each variant is run through Engine.run
    exactly as a single run would be — propagation inside a run never
    samples randomness (there is no RNG anywhere on this path), so an
    identical variant replays byte-identically and any variant's full
    causal chain can be re-derived on demand from its stored seed +
    axis combination (see replay_variant).
  * AGGREGATES INDEX HISTORIES; THEY NEVER REPLACE THEM. Per variant
    only a compact record is kept (seed, axis combination, stream
    hash, outcome vector, fired-branch flags). Full chain detail is
    re-derived by replay, never persisted per variant.
  * INCREMENTAL AGGREGATION. Running sums give exact means; delay
    distributions live in fixed histograms (percentiles are read off
    the bins with linear interpolation — the bin resolution travels
    with the payload). Memory stays flat in the variant count.
  * HONEST LABELLING. Every figure is an ensemble statistic over the
    variants actually run. Shares are counts of variants ("reached in
    6,214 of 10,000 variants"), never real-world chances; the axes,
    their levels and the uniform weighting are printed beside the
    numbers in every payload (the LABELLING / definitions blocks).
    Nothing here feeds the BACKTEST scorecard or the Ledger: this
    module imports neither, and ensemble jobs write only to their
    own store.

Axes (all pre-existing machinery — the causal-ensemble grid's engine
params, the disruption spec's own fields, and the fork/what-if
overrides; nothing new is invented):

  duration_mult       multiplier on the disruption's duration_d
                      (ensemble grid axis; fork override field)
  capacity_loss_mult  multiplier on the validated capacity_loss
                      (fork override field; inapplicable to a closure,
                      whose loss is total by definition)
  queue_sensitivity   engine param (ensemble grid axis)
  hit_threshold       engine param (ensemble grid axis)
  shape               disruption shape: step | ramp | pulse
                      (disruption spec field / fork override field)

Variant selection: variant 0 is always the BASE variant (every axis at
its centre level, or the nearest declared level). The remaining
variants are an evenly spaced selection across the full declared
grid: flat grid indices floor(j * P / N), j = 0..N-1, deduplicated,
centre excluded (it already ran as variant 0). When the requested
count reaches the grid size the full grid runs. A request for more
variants than the grid defines runs the whole grid and says so —
combinations are never silently repeated.
"""
from __future__ import annotations

from .canonical import stream_hash
from .engine import (Engine, EngineError, PARAM_BOUNDS, SHAPES,
                     validate_params)

# ---------------------------------------------------------------- axes

MAX_VARIANTS = 10_000
# The per-level cap equals the per-job variant cap — interactive
# deepening sweeps a single axis over up to MAX_VARIANTS values, and
# MAX_VARIANTS still bounds the runs any one job actually performs.
MAX_LEVELS_PER_AXIS = 10_000

_NUMERIC_AXES = ("duration_mult", "capacity_loss_mult",
                 "queue_sensitivity", "hit_threshold")

# Bounds for the two multiplier axes (the engine params validate
# themselves against PARAM_BOUNDS at resolve time; duration results
# are additionally checked against the disruption bound below).
_MULT_BOUNDS = {"duration_mult": (0.0, 16.0), "capacity_loss_mult": (0.0, 1.0)}

DEFAULT_LEVELS: dict[str, list] = {
    "duration_mult": [0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0],
    "capacity_loss_mult": [0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
    "queue_sensitivity": [0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0],
    "hit_threshold": [0.05, 0.075, 0.1, 0.125, 0.15, 0.2, 0.25, 0.35, 0.5],
    "shape": ["step", "ramp", "pulse"],
}

# Canonical axis order — grid enumeration and payloads both use it.
AXIS_ORDER = ["duration_mult", "capacity_loss_mult", "queue_sensitivity",
              "hit_threshold", "shape"]

# The unmodified scenario, per axis. The base variant takes the
# declared level nearest the centre (ties to the lower level; for
# shape, the first of this preference order that is declared).
CENTRE = {"duration_mult": 1.0, "capacity_loss_mult": 1.0,
          "queue_sensitivity": 1.0, "hit_threshold": 0.15}
_SHAPE_PREFERENCE = ["step", "ramp", "pulse"]

# Human-readable axis descriptions, printed beside the numbers. These
# strings are display text: they follow the product vocabulary rule
# (places and legs, never the developer words) like every served label.
AXIS_DESCRIPTIONS = {
    "duration_mult": "how long the disruption lasts, as a multiple of its stated duration",
    "capacity_loss_mult": "how much capacity the disruption removes, as a multiple of its stated loss",
    "queue_sensitivity": "how strongly queues build at a place under a shortfall",
    "hit_threshold": "how large a shortfall a place must take before the effect counts as landing there",
    "shape": "how the loss unfolds over the disruption window",
}

LABELLING = {
    "title": "Ensemble statistics — causal variants, not sampled chances",
    "statement": (
        "Every figure is computed over the variants actually run. Each "
        "variant is one named combination of the variation axes printed "
        "here, run through the same deterministic causal engine as a "
        "single run — nothing is sampled and no dice are rolled at any "
        "point, so every variant replays exactly and its full causal "
        "chain can be opened. A share is a count of variants (for "
        "example, 'reached in 6,214 of 10,000 variants'), never a "
        "real-world chance. Weighting is uniform over the variants run."),
    "weighting": "uniform over the variants run",
}

DEFINITIONS = {
    "worst_path_delay_d": (
        "the largest accumulated delay at any place in the variant's "
        "causal chain, in days — the deepest knock-on delay the variant "
        "produces anywhere"),
    "queue_peak_delay_d": (
        "the largest single queue delay at any place in the variant, "
        "in days (zero when no queue forms)"),
    "places_reached": "how many places the causal chain reaches in the variant",
    "chain_events": "how many causal chain events the variant produces",
    "place_delay_contribution_d": (
        "the delay the chain adds at that place itself when it lands "
        "there, in days — conditional on the place being reached"),
    "place_total_delay_d": (
        "the accumulated delay at that place when the chain lands "
        "there, in days — its own delay plus every delay upstream of "
        "it on the chain"),
    "leg_delay_contribution_d": (
        "the delay added on that leg of the causal chain when the leg "
        "carries an effect, in days — conditional on the leg firing"),
    "fire_share": (
        "the share of variants run in which the branch fired — a "
        "count of variants, never a real-world chance"),
    "percentiles": (
        "percentiles are read from the distribution of variant "
        "outcomes collected in fixed bins (0.1-day bins up to 200 "
        "days, 1-day bins above that) with interpolation inside the "
        "bin; means, minima and maxima are exact"),
}


class EnsembleStatsError(EngineError):
    """Ensemble-statistics request error; `code` maps to run API shapes."""


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _validate_levels(axis: str, levels) -> list:
    if not isinstance(levels, list) or not levels:
        raise EnsembleStatsError(
            f"axis {axis}: levels must be a non-empty array",
            code="invalid_request")
    if len(levels) > MAX_LEVELS_PER_AXIS:
        raise EnsembleStatsError(
            f"axis {axis}: at most {MAX_LEVELS_PER_AXIS} levels",
            code="invalid_request")
    if axis == "shape":
        out = []
        for lv in levels:
            if not isinstance(lv, str) or lv not in SHAPES:
                raise EnsembleStatsError(
                    f"axis shape: unknown shape {lv!r} (step | ramp | pulse)",
                    code="invalid_shape")
            out.append(lv)
    else:
        out = []
        for lv in levels:
            if not _is_number(lv):
                raise EnsembleStatsError(
                    f"axis {axis}: level {lv!r} is not a number",
                    code="invalid_request")
            out.append(float(lv))
        if axis in _MULT_BOUNDS:
            lo, hi = _MULT_BOUNDS[axis]
            in_bounds = lambda lv: lo < lv <= hi  # noqa: E731
            bracket = f"({lo}, {hi}]"
        else:
            lo, hi = PARAM_BOUNDS[axis]
            in_bounds = lambda lv: lo <= lv <= hi  # noqa: E731
            bracket = f"[{lo}, {hi}]"
        for lv in out:
            if not in_bounds(lv):
                raise EnsembleStatsError(
                    f"axis {axis}: level {lv} out of bounds {bracket} "
                    f"(rejected, never clamped)",
                    code="param_out_of_bounds")
    if len(set(out)) != len(out):
        raise EnsembleStatsError(
            f"axis {axis}: duplicate levels are not distinct variants",
            code="invalid_request")
    return out


def resolve_axes(base_spec: dict, requested: dict | None) -> tuple[dict, list[str]]:
    """Resolve the requested axis levels against the validated spec.

    ``requested`` maps axis name -> levels array; unmentioned axes take
    DEFAULT_LEVELS. Returns (axes, notes) with axes in AXIS_ORDER and
    only axes that actually vary or fix a value present — every axis
    is always present in the result (a single-level axis is a fixed
    value, stated as such). Raises EnsembleStatsError (wrong types are
    rejected, never coerced or clamped).
    """
    if requested is None:
        requested = {}
    if not isinstance(requested, dict):
        raise EnsembleStatsError("axes must be an object mapping axis "
                                 "names to level arrays", code="invalid_request")
    unknown = set(requested) - set(AXIS_ORDER)
    if unknown:
        raise EnsembleStatsError(
            f"unknown axis: {sorted(unknown)[0]} (axes are "
            f"{', '.join(AXIS_ORDER)})", code="unknown_axis")
    notes: list[str] = []
    axes: dict[str, list] = {}
    is_closure = base_spec["kind"] == "closure"
    for axis in AXIS_ORDER:
        explicit = axis in requested
        levels = _validate_levels(axis, requested[axis]) if explicit \
            else list(DEFAULT_LEVELS[axis])
        if axis == "capacity_loss_mult" and is_closure:
            if explicit and levels != [1.0]:
                raise EnsembleStatsError(
                    "the capacity-loss axis cannot vary a closure: a "
                    "closure removes the full capacity of the place by "
                    "definition, so there is no partial loss to vary",
                    code="axis_not_applicable")
            notes.append(
                "capacity_loss_mult fixed at 1.0 — a closure removes "
                "the full capacity by definition, so this axis cannot vary")
            levels = [1.0]
        if axis == "duration_mult":
            worst = base_spec["duration_d"] * max(levels)
            if worst > 3650.0:
                raise EnsembleStatsError(
                    f"duration levels reach {worst:g} days for this "
                    f"disruption (bound 3650, rejected, never clamped)",
                    code="param_out_of_bounds")
        axes[axis] = levels
    # Engine-param axes must satisfy the engine's own bounds even when
    # custom levels were supplied (validate_params is the authority;
    # _validate_levels already applied the same bounds — this is the
    # belt-and-braces pass so the two can never drift apart).
    for qs in axes["queue_sensitivity"]:
        validate_params({"queue_sensitivity": qs})
    for thr in axes["hit_threshold"]:
        validate_params({"hit_threshold": thr})
    return axes, notes


def grid_size(axes: dict) -> int:
    n = 1
    for axis in AXIS_ORDER:
        n *= len(axes[axis])
    return n


def combination_at(axes: dict, flat_index: int) -> dict:
    """The axis combination at a flat grid index (last axis fastest,
    itertools.product order over AXIS_ORDER)."""
    combo: dict = {}
    rem = flat_index
    for axis in reversed(AXIS_ORDER):
        levels = axes[axis]
        combo[axis] = levels[rem % len(levels)]
        rem //= len(levels)
    return combo


def flat_index_of(axes: dict, combo: dict) -> int:
    idx = 0
    for axis in AXIS_ORDER:
        idx = idx * len(axes[axis]) + axes[axis].index(combo[axis])
    return idx


def base_combination(axes: dict, base_shape: str = "step") -> dict:
    """The unmodified scenario as a combination: every multiplier at
    1.0, engine params at the defaults the ensemble grid centres on, and
    the shape axis at the disruption's own shape (the nearest declared
    level when the centre itself was not declared)."""
    combo: dict = {}
    for axis in AXIS_ORDER:
        levels = axes[axis]
        if axis == "shape":
            combo[axis] = base_shape if base_shape in levels else next(
                s for s in _SHAPE_PREFERENCE if s in levels)
        else:
            centre = CENTRE[axis]
            combo[axis] = min(levels, key=lambda lv: (abs(lv - centre), lv))
    return combo


def select_flat_indices(axes: dict, count: int,
                        base_shape: str = "step") -> list[int]:
    """Flat grid indices to run, in variant order: the base
    combination first (variant 0), then an evenly spaced selection
    across the full grid. Never more than the grid defines."""
    total = grid_size(axes)
    n = min(count, total)
    base_idx = flat_index_of(axes, base_combination(axes, base_shape))
    if n >= total:
        rest = [i for i in range(total) if i != base_idx]
        return [base_idx] + rest
    picked = {(j * total) // n for j in range(n)}
    picked.discard(base_idx)
    rest = sorted(picked)[: n - 1]
    # If discarding the base index left a shortfall (it was picked and
    # n-1 slots remain), top up with the smallest unpicked indices so
    # the count is exact and the selection stays deterministic.
    if len(rest) < n - 1:
        candidates = (i for i in range(total)
                      if i not in picked and i != base_idx)
        while len(rest) < n - 1:
            rest.append(next(candidates))
        rest.sort()
    return [base_idx] + rest


def variant_spec(base_spec: dict, combo: dict) -> dict:
    spec = dict(base_spec)
    spec["duration_d"] = base_spec["duration_d"] * combo["duration_mult"]
    if combo.get("capacity_loss_mult", 1.0) != 1.0:
        spec["capacity_loss"] = base_spec["capacity_loss"] * combo["capacity_loss_mult"]
    spec["shape"] = combo["shape"]
    return spec


def variant_params(combo: dict) -> dict:
    return {"queue_sensitivity": combo["queue_sensitivity"],
            "hit_threshold": combo["hit_threshold"]}


# ------------------------------------------------------------- outcomes

def extract_outcomes(state) -> dict:
    """Compact outcome vector + fired branches from one RunState.

    Branch identity: a place branch fires when the chain first lands
    at that place; a leg branch fires when an event's parent link
    realises it (parent place -> place, by channel). Per (place,
    channel) the engine emits at most one event per run, so a leg key
    fires at most once per variant; should that invariant ever bend,
    the larger delay is kept and the flags stay truthful.
    """
    events = state.events
    worst_path = 0.0
    for imp in state.impacts.values():
        if imp["total_delay_d"] > worst_path:
            worst_path = imp["total_delay_d"]
    queue_peak = 0.0
    for ev in events:
        if ev["channel"] == "queue" and ev["delay_d"] > queue_peak:
            queue_peak = ev["delay_d"]
    places: dict[str, dict] = {}
    for node_id, imp in state.hit.items():
        ev = events[imp.seq]
        places[node_id] = {
            "contribution_d": ev["delay_d"],
            "total_delay_d": imp.total_delay_d,
            "channel": ev["channel"],
            "first_seq": imp.seq,
        }
    legs: dict[str, dict] = {}
    for ev in events:
        if ev["parent_id"] is None:
            continue
        frm = events[ev["parent_id"]]["node_id"]
        key = f"{frm}>{ev['node_id']}#{ev['channel']}"
        prev = legs.get(key)
        if prev is None or ev["delay_d"] > prev["contribution_d"]:
            legs[key] = {"from": frm, "to": ev["node_id"],
                         "channel": ev["channel"],
                         "contribution_d": ev["delay_d"]}
    return {
        "outcomes": {
            "worst_path_delay_d": round(worst_path, 6),
            "queue_peak_delay_d": round(queue_peak, 6),
            "places_reached": len(places),
            "chain_events": len(events),
        },
        "places": places,
        "legs": legs,
    }


def variant_record(index: int, combo: dict, run_id: str, state) -> dict:
    """The persisted per-variant record: seed + combination + hashes
    + outcome vector + fired-branch flags. Nothing more is stored —
    full chain detail is re-derived by replay (replay_variant)."""
    ext = extract_outcomes(state)
    return {
        "index": index,
        "seed": index,
        "combination": {axis: combo[axis] for axis in AXIS_ORDER},
        "run_id": run_id,
        "stream_hash": stream_hash(state.events),
        "outcomes": ext["outcomes"],
        "places_fired": sorted(ext["places"],
                               key=lambda nid: ext["places"][nid]["first_seq"]),
        "legs_fired": sorted(ext["legs"]),
        "_places": ext["places"],   # aggregation input; stripped before persist
        "_legs": ext["legs"],
    }


def replay_variant(engine: Engine, base_spec: dict, axes: dict,
                   flat_indices: list[int], index: int, run_id: str):
    """Re-derive one variant's full RunState from its seed (its index
    in the job's selection). Determinism makes the replay exact: the
    stream hash equals the stored record's."""
    combo = combination_at(axes, flat_indices[index])
    state = engine.run(run_id, variant_spec(base_spec, combo),
                       variant_params(combo))
    return combo, state


# ------------------------------------------------------------ statistics

def _bin_edges() -> list[float]:
    edges = [round(0.1 * i, 6) for i in range(2001)]          # 0..200 by 0.1
    edges += [float(v) for v in range(201, 1201)]             # 200..1200 by 1
    return edges


BIN_EDGES = _bin_edges()
_N_BINS = len(BIN_EDGES)  # bin k covers [EDGES[k], EDGES[k+1]); last = overflow


def _bin_index(x: float) -> int:
    if x < 200.0:
        k = int(x / 0.1)
        return min(k, 1999)
    if x < 1200.0:
        return 2000 + int(x - 200.0)
    return _N_BINS - 1  # overflow bin [1200, inf)


class RunningStat:
    """Exact running sum/min/max + a fixed-bin histogram for one
    outcome. Percentiles interpolate inside the containing bin."""

    __slots__ = ("count", "total", "minimum", "maximum", "bins")

    def __init__(self):
        self.count = 0
        self.total = 0.0
        self.minimum: float | None = None
        self.maximum: float | None = None
        self.bins: dict[int, int] = {}

    def add(self, x: float) -> None:
        x = float(x)
        self.count += 1
        self.total += x
        self.minimum = x if self.minimum is None else min(self.minimum, x)
        self.maximum = x if self.maximum is None else max(self.maximum, x)
        k = _bin_index(x)
        self.bins[k] = self.bins.get(k, 0) + 1

    def percentile(self, p: float) -> tuple[float, float, bool]:
        """(value, bin_resolution_d, capped) at percentile p (0..100)."""
        if self.count == 0:
            return 0.0, 0.0, False
        rank = (p / 100.0) * self.count
        cum = 0
        for k in sorted(self.bins):
            c = self.bins[k]
            if cum + c >= rank:
                lo = BIN_EDGES[k]
                hi = BIN_EDGES[k + 1] if k + 1 < len(BIN_EDGES) else None
                if hi is None:
                    return lo, 0.0, True
                frac = 1.0 if c == 0 else (rank - cum) / c
                frac = min(max(frac, 0.0), 1.0)
                return lo + frac * (hi - lo), hi - lo, False
            cum += c
        lo = BIN_EDGES[-1]
        return lo, 0.0, True

    def summary(self) -> dict:
        if self.count == 0:
            return {"count": 0, "mean": None, "min": None, "max": None,
                    "p10": None, "p50": None, "p90": None,
                    "percentile_resolution_d": None, "percentile_capped": False}
        p10, r10, c10 = self.percentile(10)
        p50, r50, c50 = self.percentile(50)
        p90, r90, c90 = self.percentile(90)
        return {
            "count": self.count,
            "mean": round(self.total / self.count, 6),
            "min": round(self.minimum, 6),
            "max": round(self.maximum, 6),
            "p10": round(p10, 6),
            "p50": round(p50, 6),
            "p90": round(p90, 6),
            "percentile_resolution_d": round(max(r10, r50, r90), 6),
            "percentile_capped": bool(c10 or c50 or c90),
        }

    def curve(self) -> list[dict]:
        """Non-empty bins, in order — the distribution curve data.
        Every bin that holds outcomes is served; the final overflow
        bin (hi = null) is explicit, so a dense result set can never
        clip silently and a sparse one stays visibly sparse."""
        out = []
        for k in sorted(self.bins):
            out.append({
                "lo": BIN_EDGES[k],
                "hi": BIN_EDGES[k + 1] if k + 1 < len(BIN_EDGES) else None,
                "count": self.bins[k],
            })
        return out


class _PlaceAcc:
    __slots__ = ("fired", "contribution", "total", "queue_peak_d",
                 "channels", "first_seq_total", "first_seq_min")

    def __init__(self):
        self.fired = 0
        self.contribution = RunningStat()
        self.total = RunningStat()
        self.queue_peak_d = 0.0
        self.channels: dict[str, int] = {}
        self.first_seq_total = 0
        self.first_seq_min: int | None = None


class _LegAcc:
    __slots__ = ("fired", "contribution")

    def __init__(self):
        self.fired = 0
        self.contribution = RunningStat()


class EnsembleAccumulator:
    """Incremental aggregates over variant outcome extractions.
    Memory is flat in the variant count: one RunningStat per outcome,
    per reached place and per fired leg."""

    def __init__(self):
        self.variants = 0
        self.outcomes = {
            "worst_path_delay_d": RunningStat(),
            "queue_peak_delay_d": RunningStat(),
            "places_reached": RunningStat(),
            "chain_events": RunningStat(),
        }
        self.places: dict[str, _PlaceAcc] = {}
        self.legs: dict[str, _LegAcc] = {}

    def add(self, ext: dict) -> None:
        self.variants += 1
        for name, stat in self.outcomes.items():
            stat.add(ext["outcomes"][name])
        for node_id, p in ext["places"].items():
            acc = self.places.setdefault(node_id, _PlaceAcc())
            acc.fired += 1
            acc.contribution.add(p["contribution_d"])
            acc.total.add(p["total_delay_d"])
            if p["channel"] == "queue" and p["contribution_d"] > acc.queue_peak_d:
                acc.queue_peak_d = p["contribution_d"]
            acc.channels[p["channel"]] = acc.channels.get(p["channel"], 0) + 1
            acc.first_seq_total += p["first_seq"]
            acc.first_seq_min = p["first_seq"] if acc.first_seq_min is None \
                else min(acc.first_seq_min, p["first_seq"])
        for key, leg in ext["legs"].items():
            acc = self.legs.setdefault(key, _LegAcc())
            acc.fired += 1
            acc.contribution.add(leg["contribution_d"])

    # -- serialisers --------------------------------------------------

    def _share(self, fired: int) -> float:
        return round(fired / self.variants, 6) if self.variants else 0.0

    def outcome_block(self) -> dict:
        return {
            name: {**stat.summary(), "curve": stat.curve()}
            for name, stat in self.outcomes.items()
        }

    def place_branches(self, graph_nodes: dict) -> list[dict]:
        out = []
        for node_id, acc in self.places.items():
            node = graph_nodes.get(node_id, {})
            out.append({
                "branch": f"place:{node_id}",
                "node_id": node_id,
                "name": node.get("name"),
                "kind": node.get("kind"),
                "mode": node.get("mode"),
                "fired": acc.fired,
                "variants": self.variants,
                "fire_share": self._share(acc.fired),
                "delay_contribution_d": acc.contribution.summary(),
                "total_delay_d": acc.total.summary(),
                "queue_peak_delay_d": round(acc.queue_peak_d, 6),
                "first_hit_channels": dict(sorted(acc.channels.items())),
                "first_seq_min": acc.first_seq_min,
                "first_seq_mean": round(acc.first_seq_total / acc.fired, 3)
                if acc.fired else None,
            })
        out.sort(key=lambda r: (-r["fired"], r["first_seq_mean"], r["node_id"]))
        return out

    def leg_branches(self, graph_nodes: dict) -> list[dict]:
        out = []
        for key, acc in self.legs.items():
            frm, rest = key.split(">", 1)
            to, channel = rest.rsplit("#", 1)
            out.append({
                "branch": f"leg:{key}",
                "from": frm,
                "to": to,
                "from_name": graph_nodes.get(frm, {}).get("name"),
                "to_name": graph_nodes.get(to, {}).get("name"),
                "channel": channel,
                "fired": acc.fired,
                "variants": self.variants,
                "fire_share": self._share(acc.fired),
                "delay_contribution_d": acc.contribution.summary(),
            })
        out.sort(key=lambda r: (-r["fired"], r["branch"]))
        return out


def axes_payload(axes: dict) -> list[dict]:
    """The declared axes + levels + weighting, printed beside every
    set of ensemble numbers."""
    return [{
        "axis": axis,
        "levels": list(axes[axis]),
        "description": AXIS_DESCRIPTIONS[axis],
        "varies": len(axes[axis]) > 1,
    } for axis in AXIS_ORDER]
