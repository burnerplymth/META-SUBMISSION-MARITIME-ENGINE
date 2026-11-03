"""Deterministic propagation engine.

Propagation rule: a disruption at a node pushes lost flow
to its neighbours as an ABSOLUTE quantity, and every affected node
is evaluated ONCE per cascade layer on the AGGREGATE of what arrived:

  Flow basis (Engine._build_flow_basis carries the full
  definition and unit caveat): throughput F_n = utilisation_n *
  capacity_n; edge flow f_e = capacity_share_e * F_source; inbound
  flow F_c^in = sum of primary inbound edge flows (fallback F_c).
  Lost flow on an edge L_e = s_parent * f_e; child shortfall
  sigma_c = min(1, sum_e L_e / F_c^in) — the child's inbound
  dependence, never the parent's outbound normalisation alone.
  Upstream arrivals carry the registry upstream_attenuation factor.

  Aggregation: first-hit-wins is gone. Propagation is a
  two-phase fixed point (Engine._propagate): the hit set and
  shortfall labels iterate to their least fixed point — arriving
  shortfalls SUM (capped at 1) over ALL parents in the final hit
  set — and only then are first-hit events emitted, in (round,
  node_id) causal order. Each label carries two quantities:
  s_hit, aggregating ALL arrivals (downstream + upstream +
  reroute), which drives the hit test, the event magnitude and the
  delay; and s_prop, aggregating downstream + reroute arrivals
  only, which is what propagates onward — so upstream backlog (a
  supplier's record of its customer's failure) can never feed back
  downstream and double-count the original loss, and neither the
  propagated shortfall nor the delay depends on which channel
  labelled the node. The node's inventory cover absorbs
  shortfall-days once, on each aggregate: effective shortfall
  sigma_eff = max(0, sigma_total * D - inventory_days) / D, with
  D = the largest arriving duration. A node emits at most one
  first-hit event; it may additionally emit one cost leaf.
  Runaway bounds (registry max_layers / max_events) make
  quiescence structural. NO RNG anywhere on this path.

  Delay: ONE continuous service-time function serves
  the queue and congestion channels (Engine._service_wait): backlog
  B = sigma_eff * F^in * D accumulates over the disruption; the
  queue stores at most queue_cap of it (backlog beyond the cap is
  diverted/unmet and does not wait; a no-storage node holds only
  the registry congestion share in transit); the stored backlog
  clears at the spare service rate capacity * (1 - utilisation);
  delay_d is the MEAN wait, qs * mean_wait_factor * stored / spare.
  There are no stepped coefficients and no clock/inventory
  quantisation: delay is continuous and monotone non-decreasing in
  shortfall and in duration. The seed's delay is the same function
  on the lost service B = loss * capacity * duration (not a
  loss*duration volume-as-time; the Panama seed wait is 13.9 d
  against 17.5 d observed).
  Mode-shift delay stays transit-proportional
  (mode_shift_transit_fraction * longest modal leg * sigma_eff)
  and reroute delay is the largest added transit over the primary
  legs among the aggregated bypass arrivals; both are continuous
  in shortfall. The emitted delay_d is the LARGEST aggregated
  channel delay among the channels present at the node (primary
  wait vs reroute added transit), and the channel label is the
  argmax — so delay_d never depends on labelling order. Arrival times: a child's
  elapsed time is the winning parent's elapsed + the leg's
  base_transit_d + the child's wait, so `at` = start + the sum of
  (transit + wait) along the causal path — transit is added, and
  `at` is non-decreasing along every path by at least the transit.

  Hit rule (unchanged thresholds, applied to totals): a node is HIT
  when sigma_eff >= its threshold (precedence: node_overrides >
  node.hit_threshold > params) OR the raw delay >= delay_hit_floor_d
  with sigma_eff >= min_material_share. Cost leaves require the
  winning parent's elapsed >= cost_gate_d and a material sigma_eff;
  magnitude = min(1, parent elapsed / cost_delay_scale_d) — expedite
  pressure, never currency.

  Constants: every model constant lives in the versioned
  registry server/engine/constants.json (source citation or explicit
  ASSUMPTION label, plus bounds), loaded via engine.constants; the
  registry's canonical-JSON SHA-256 is stamped on every RunState.

WHYNOT recording (additive; definitions follow the
mechanics above): every run also records, for each place ADJACENT
to the chain — connected by a leg (any graph edge, either direction,
bypass legs included) to the disrupted place or to any first-hit
place (state.hit) — that was NOT itself first-hit, exactly one entry
in RunState.not_hit: {"place_id", "reason", "figures"}, sorted by
place_id. Reasons, defined strictly from the mechanics above:

  ABSORBED         the aggregated arrival's shortfall share had
                   ALREADY crossed the node's hit threshold, and the
                   node's inventory cover fully absorbed it:
                   sigma_total >= threshold with
                   sigma_total * D <= inventory_days, so sigma_eff = 0
                   and no event fires. Figures: received (total) share
                   vs threshold, shortfall-days vs inventory_days.
  BELOW_THRESHOLD  flow DID arrive (downstream share, upstream
                   backlog share, or a bypass's diverted share, each
                   as an inbound-dependence fraction) but the hit test
                   on the AGGREGATE failed: sigma_eff < threshold AND
                   NOT (raw delay >= 1.0 d floor with sigma_eff >=
                   material). Figures are the aggregated evaluation:
                   total received share vs threshold, raw delay vs
                   the floor.
  NO_ROUTE         the place is adjacent in the graph sense but no
                   leg delivered flow to it under this disruption:
                   either every connecting delivery evaluated to a
                   zero share (capacity_share 0 legs deliver L = 0),
                   or the engine never dispatched a delivery over the
                   connecting leg at all (e.g. a bypass leg whose
                   bypass_of primary belongs to another leg —
                   bypasses fire only from their primary's source).
                   Figures: the connecting edge key(s) and
                   delivered_share = 0.
  BEYOND_HORIZON   NEVER EMITTED by this engine: there is no run
                   horizon anywhere in its mechanics — no horizon
                   parameter exists, propagation runs to quiescence
                   (bounded only by the runaway guard, which aborts
                   the run rather than skipping late places), and an
                   arrival's `at` may lie arbitrarily far in the
                   future and still hit. Inventing a horizon to emit
                   this code would falsify the record, so the reason
                   set the engine honestly produces is the three
                   above.

Recording is OBSERVATION ONLY: aggregated evaluations are recorded
into RunState.evaluations as they are computed anyway; nothing in
the propagation path reads them, and event emission, ordering and
figures are untouched by them. Derivation (Engine.derive_not_hit)
runs after propagation quiesces and is a pure function of (graph,
hit set, evaluations) — no wall clock, no dict-order dependence (all
outputs explicitly sorted). The event stream hash
(canonical.stream_hash) is over the events array ONLY, so not_hit is
not part of the hash input; fork children inherit the parent's
evaluations for the shared prefix (fork.fork_events) so prefix
neighbours keep their recorded reasons.
"""
from __future__ import annotations


from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .constants import CONSTANTS_HASH, const, const_entry
from .graph import Graph, edge_key

CHANNELS = {"queue", "congestion", "reroute", "mode-shift", "inventory", "cost"}
DISRUPTION_KINDS = {"closure", "capacity_cut", "congestion", "strike", "weather",
                    "grounding", "accident", "policy", "other"}
SHAPES = {"step", "ramp", "pulse"}

# Every model constant below is loaded from the versioned
# constants registry (server/engine/constants.json) — no model-constant
# numeric literals live in this module (the only numeric literals
# remaining are structural: probability
# bounds 0/1, the _r6 rounding precision, the default epoch date, and
# sequence sentinels). The registry hash is stamped on every RunState.
DEFAULT_PARAMS = {"queue_sensitivity": const("queue_sensitivity_default"),
                  "hit_threshold": const("hit_threshold_default")}
PARAM_BOUNDS = {"queue_sensitivity": tuple(const_entry("queue_sensitivity_default")["bounds"]),
                "hit_threshold": tuple(const_entry("hit_threshold_default")["bounds"])}

MAX_EVENTS = const("max_events")
MAX_LAYERS = const("max_layers")
DELAY_HIT_FLOOR_D = const("delay_hit_floor_d")
COST_DELAY_SCALE_D = const("cost_delay_scale_d")
# A hit earned through the delay floor (rather than the shortfall
# threshold) must still carry a material share of flow — otherwise clock
# quantisation would mint 1-day "hits" out of 0.001% shortfalls on dense
# graphs. Hit tests always use RAW (pre-quantisation) delays.
MIN_MATERIAL_SHARE = const("min_material_share")

MECHANISMS = {
    "root": "capacity loss at source",
    "queue": "queue spillback",
    "queue_upstream": "upstream queue backlog",
    "congestion": "congestion delay",
    "reroute": "reroute via bypass",
    "mode-shift": "mode-shift to air",
    "inventory": "inventory draw",
    "cost": "expedite cost pressure",
}


class EngineError(ValueError):
    """Validation error; `code` maps to the run API error shape."""

    def __init__(self, message: str, code: str = "invalid_request"):
        super().__init__(message)
        self.code = code


def validate_params(params: dict | None) -> dict:
    out = dict(DEFAULT_PARAMS)
    if params:
        for k, v in params.items():
            if k not in PARAM_BOUNDS:
                raise EngineError(f"unknown param: {k}", code="unknown_param")
            lo, hi = PARAM_BOUNDS[k]
            if not isinstance(v, (int, float)) or isinstance(v, bool) or not (lo <= float(v) <= hi):
                raise EngineError(
                    f"param {k}={v!r} out of bounds [{lo}, {hi}] (rejected, never clamped)",
                    code="param_out_of_bounds")
            out[k] = float(v)
    return out


def _strict_number(value, field: str) -> float:
    """A JSON number for ``field`` as a float, or an EngineError.

    List/dict values would otherwise reach float() and raise a bare
    TypeError out of the API (a plain-text 500), while bools and numeric
    strings would be silently coerced — inconsistent with
    validate_params, which rejects non-numbers and bools outright. The
    same discipline applies here: wrong types are REJECTED with
    invalid_request, never converted.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EngineError(
            f"{field} must be a number, got {type(value).__name__}: "
            f"{value!r:.80}")
    return float(value)


def validate_disruption(graph: Graph, spec) -> dict:
    if not isinstance(spec, dict):
        raise EngineError("disruption spec must be an object")
    node_id = spec.get("node_id")
    # Non-string ids can never name a graph node; the isinstance check
    # also keeps unhashable values (list/dict) out of the dict lookup,
    # where they raised a bare TypeError.
    if not isinstance(node_id, str) or node_id not in graph.nodes:
        raise EngineError(f"unknown node: {node_id!r}", code="unknown_node")
    kind = spec.get("kind")
    if not isinstance(kind, str) or kind not in DISRUPTION_KINDS:
        raise EngineError(f"unknown disruption kind: {kind!r}", code="invalid_kind")
    loss = spec.get("capacity_loss")
    if kind in ("closure", "capacity_cut") and loss is None:
        raise EngineError(f"capacity_loss required for kind={kind}", code="missing_capacity_loss")
    if kind == "closure":
        loss = const("closure_capacity_loss")
    if loss is None:
        loss = const("default_capacity_loss")
    loss = _strict_number(loss, "capacity_loss")
    if not (0.0 < loss <= 1.0):
        raise EngineError(f"capacity_loss out of bounds (0, 1]: {loss!r}",
                          code="param_out_of_bounds")
    duration = spec.get("duration_d", const("default_duration_d"))
    # The bound is 3650 d: the replay
    # datasets carry real stated durations above one year (Red Sea
    # expected_duration_d = 559). Rejecting them would force the backtest
    # path to clamp a dataset value, falsifying the run; the bound still
    # rejects absurd durations.
    duration = _strict_number(duration, "duration_d")
    if not (0.0 < duration <= const("duration_max_d")):
        raise EngineError(f"duration_d out of bounds (0, {const('duration_max_d')}]: {duration!r}",
                          code="param_out_of_bounds")
    shape = spec.get("shape", "step")
    if not isinstance(shape, str) or shape not in SHAPES:
        raise EngineError(f"unknown shape: {shape!r}", code="invalid_shape")
    started_at = spec.get("started_at") or spec.get("detected_at")
    if started_at is not None:
        # A non-string or unparseable timestamp would otherwise escape as a
        # bare AttributeError/ValueError deep inside Engine.run, after
        # validation had "passed". Reject it here.
        if not isinstance(started_at, str):
            raise EngineError(
                f"started_at must be an ISO-8601 string, got "
                f"{type(started_at).__name__}: {started_at!r:.80}")
        try:
            _parse_ts(started_at)
        except (ValueError, TypeError):
            raise EngineError(
                f"started_at is not a valid ISO-8601 timestamp: "
                f"{started_at!r:.80}") from None
    return {
        "node_id": node_id, "kind": kind, "capacity_loss": loss,
        "duration_d": duration, "shape": shape,
        "started_at": started_at,
    }


def _shape_adjust(loss: float, duration: float, shape: str) -> tuple[float, float]:
    """Deterministic shape semantics: ramp averages half the peak loss over
    the duration; pulse delivers the full loss for half the duration."""
    if shape == "ramp":
        return loss * const("ramp_loss_factor"), duration
    if shape == "pulse":
        return loss, duration * const("pulse_duration_factor")
    return loss, duration


def _r6(x: float) -> float:
    return round(float(x), 6)


def _parse_ts(text: str | None) -> datetime:
    if not text:
        return datetime(2026, 1, 1, tzinfo=timezone.utc)
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _fmt_ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Impact:
    node_id: str
    s: float            # shortfall fraction arriving at this node
    duration_d: float   # remaining disruption duration driving this impact
    total_delay_d: float  # accumulated additive delay along the path
    seq: int = -1       # seq of this node's first-hit event


@dataclass
class RunState:
    events: list[dict] = field(default_factory=list)
    impacts: dict[int, dict] = field(default_factory=dict)  # seq -> impact record
    hit: dict[str, Impact] = field(default_factory=dict)    # node -> first-hit impact
    emitted: set = field(default_factory=set)               # (node_id, channel)
    # The constants-registry hash this run was produced under.
    # Stamped on every run record the engine emits (this state and
    # run_record()); the event stream itself does not
    # carry it.
    constants_hash: str = CONSTANTS_HASH
    # WHYNOT recording (additive; never read by propagation, and never
    # part of the event stream hash, which is computed over `events` only):
    # the strongest candidate evaluation per node that did not produce
    # a first hit, plus the derived not-hit list (Engine.derive_not_hit,
    # attached at the end of run()/continue_run()).
    evaluations: dict[str, dict] = field(default_factory=dict)
    not_hit: list[dict] = field(default_factory=list)

    def run_record(self, run_id: str | None = None) -> dict:
        """The run record for this state: identity + the
        constants-registry hash the run was produced under."""
        from .canonical import stream_hash
        return {"run_id": run_id if run_id is not None else (
                    self.events[0]["run_id"] if self.events else None),
                "constants_hash": self.constants_hash,
                "stream_hash": stream_hash(self.events),
                "event_count": len(self.events)}


# WHYNOT reasons. BEYOND_HORIZON is reserved: this engine has no
# horizon cutoff (propagation runs to quiescence), so no evaluation is
# ever dropped for arriving late and the reason is never emitted.
WHYNOT_REASONS = {"ABSORBED", "BELOW_THRESHOLD", "BEYOND_HORIZON", "NO_ROUTE"}
_EVAL_TO_REASON = {"absorbed": "ABSORBED", "below": "BELOW_THRESHOLD",
                   "no_flow": "NO_ROUTE"}


def _record_eval(state: RunState, node_id: str, kind: str, via: str,
                 figures: dict) -> None:
    """Record a non-hit candidate evaluation, keeping the strongest
    (largest inbound share; first recorded wins ties — evaluation order
    is deterministic, so the kept record is too)."""
    prev = state.evaluations.get(node_id)
    if prev is not None and prev["figures"].get("inbound_share", 0.0) \
            >= figures.get("inbound_share", 0.0):
        return
    state.evaluations[node_id] = {"kind": kind, "via": via, "figures": figures}


def core_subgraph(graph: Graph) -> Graph:
    """The CORE-tier view of a tiered graph:
    CORE nodes + CORE<->CORE edges only.

    The chain engine — and therefore the backtest, the
    scorecard, ensembles and forks, which all run through Engine —
    grades the CORE tier only. SHELL nodes are routing-grade: they
    never feed scorecard/backtest claims, so propagation runs on this
    subgraph and a disruption addressed at a shell node is refused by
    validate_disruption as unknown to the chain engine. On an untiered
    graph (every fixture graph; nodes without a `tier` field count as
    CORE) the graph is returned unchanged, so pre-tier behaviour is
    bit-identical."""
    raw = graph.raw
    nodes = raw.get("nodes", [])
    if not any(n.get("tier") == "shell" for n in nodes):
        return graph
    core_ids = {n["id"] for n in nodes if n.get("tier", "core") == "core"}
    return Graph({
        "version": raw.get("version"),
        "nodes": [n for n in nodes if n["id"] in core_ids],
        "edges": [e for e in raw.get("edges", [])
                  if e["from"] in core_ids and e["to"] in core_ids],
    })


class Engine:
    def __init__(self, graph: Graph):
        # full_graph keeps the served graph (all tiers) for callers
        # that resolve names/metadata; self.graph is the CORE subgraph
        # the propagation, validation and grading run on.
        self.full_graph = graph
        self.graph = core_subgraph(graph)
        self._build_flow_basis()

    # -- flow basis ----------------------------------------------
    @staticmethod
    def _utilisation(node: dict) -> float:
        u = node.get("utilisation", node.get("utilization"))
        if isinstance(u, bool) or not isinstance(u, (int, float)):
            u = const("baseline_utilisation")
        return min(max(float(u), 0.0), const("max_utilisation"))

    def _build_flow_basis(self) -> None:
        """Absolute modelled flows.

        F definition (unit basis — read this before trusting any
        number): a node's nominal THROUGHPUT is
        F_n = utilisation_n * capacity_n, in the node's own
        capacity_unit per day. An edge's modelled flow is
        f_e = capacity_share_e * F_source — capacity_share is the
        fraction of the SOURCE's modelled flow the edge carries (the
        graph's outbound share totals run 0 to ~1.02, i.e. shares are
        fractions of source flow, NOT weights to renormalise within
        the source; a sole outbound edge at share 0.18 carries 18% of
        its source's flow, which is exactly the Milan ->
        Frankfurt dependence 0.18 / 3.32 = 0.054). A node's INBOUND
        FLOW is
        F_c^in = sum of f_e over its primary (non-bypass) inbound
        edges, falling back to F_c itself when the node has no inbound
        modelled flow (a pure source).

        UNIT CAVEAT (unit normalisation is out of scope): capacities are
        TEU/day, tonnes/day, trucks/day or vessels/day depending on
        mode, so an inbound sum over cross-mode edges adds numbers
        whose physical units differ. The engine treats every capacity
        as a modelled-flow magnitude in the RECEIVING sum and the
        resulting shortfall is a dimensionless fraction of modelled
        inbound flow — it does NOT claim physical-unit conservation
        across modes. Within a mode the units are consistent.
        """
        g = self.graph
        self._throughput: dict[str, float] = {}
        for nid, node in g.nodes.items():
            self._throughput[nid] = self._utilisation(node) * float(node["capacity"])
        self._edge_flow: dict[str, float] = {}
        self._edge_by_key: dict[str, dict] = {}
        for e in g.edges:
            self._edge_by_key[edge_key(e)] = e
            self._edge_flow[edge_key(e)] = float(e["capacity_share"]) \
                * self._throughput[e["from"]]
        self._flow_basis: dict[str, float] = {}
        for nid in g.nodes:
            inflow = sum(self._edge_flow[edge_key(e)]
                         for e in g.inbound(nid) if not e.get("bypass_of"))
            self._flow_basis[nid] = inflow if inflow > 0 else self._throughput[nid]

    # -- thresholds ------------------------------------------------------
    def threshold(self, node_id: str, params: dict, node_overrides: dict | None) -> float:
        if node_overrides and node_id in node_overrides:
            v = node_overrides[node_id].get("hit_threshold")
            if v is not None:
                return float(v)
        node = self.graph.nodes[node_id]
        if node.get("hit_threshold") is not None:
            return float(node["hit_threshold"])
        return params["hit_threshold"]

    # -- main entry --------------------------------------------------------
    def run(self, run_id: str, disruption: dict, params: dict | None = None,
            node_overrides: dict | None = None) -> RunState:
        params = validate_params(params)
        spec = validate_disruption(self.graph, disruption)
        base_dt = _parse_ts(spec["started_at"])
        eff_loss, eff_dur = _shape_adjust(spec["capacity_loss"], spec["duration_d"], spec["shape"])
        # The seed delay is a SERVICE TIME, not loss*duration
        # (a volume). The backlog accumulated over the disruption is
        # the lost service (eff_loss * capacity per day, sustained for
        # eff_dur days); the seed's wait is that backlog — capped by
        # its queue_cap — cleared at its spare service rate
        # (Engine._service_wait). Panama Canal (loss 0.5, 215 d,
        # capacity 36 vessels/day, queue_cap 150): stored backlog
        # min(0.5*36*215, 150) = 150 vessels, spare 36*(1-0.85) = 5.4
        # vessels/day, mean wait 0.5*150/5.4 = 13.9 d (observed 17.5 d).
        seed_node = self.graph.nodes[spec["node_id"]]
        root_delay = _r6(self._service_wait(
            eff_loss * float(seed_node["capacity"]) * eff_dur,
            seed_node, params["queue_sensitivity"]))
        state = RunState()
        root = Impact(node_id=spec["node_id"], s=eff_loss, duration_d=eff_dur,
                      total_delay_d=root_delay, seq=0)
        self._emit(state, run_id, root, parent_id=None, channel="congestion",
                   mechanism=MECHANISMS["root"], delay_d=root_delay,
                   magnitude=_r6(eff_loss), tick=0, base_dt=base_dt)
        state.hit[root.node_id] = root
        self._propagate(state, run_id, [root], params, node_overrides or {}, base_dt, start_tick=1)
        state.not_hit = self.derive_not_hit(state)
        return state

    def continue_run(self, state: RunState, run_id: str, frontier: list[Impact],
                     params: dict, node_overrides: dict, base_dt: datetime,
                     start_tick: int) -> RunState:
        """Continue propagation from an existing (fork-prefix) state."""
        self._propagate(state, run_id, frontier, params, node_overrides, base_dt, start_tick)
        state.not_hit = self.derive_not_hit(state)
        return state

    # -- WHYNOT derivation ---------------------------------------------------
    def derive_not_hit(self, state: RunState) -> list[dict]:
        """Places adjacent to the chain that were NOT hit, each with the
        reason its strongest evaluation failed and the deciding figures.

        Adjacent = evaluated as a candidate during propagation, or joined
        by a leg (either direction, bypass legs included) to a hit place
        in the propagation graph. Places that were hit, or that carry any
        event (leaf emissions included), are not listed. Pure function of
        the finished state + graph: identical state -> identical list.
        """
        hit_ids = set(state.hit)
        event_nodes = {ev["node_id"] for ev in state.events}
        out: dict[str, dict] = {}
        for node_id, rec in state.evaluations.items():
            if node_id in hit_ids or node_id in event_nodes:
                continue
            figures = {"via_place": rec["via"], **rec["figures"]}
            out[node_id] = {"place_id": node_id,
                            "reason": _EVAL_TO_REASON[rec["kind"]],
                            "figures": figures}
        # Neighbour sweep: a leg joins the place to a hit place but no
        # evaluation ever reached it (e.g. a bypass leg whose primary
        # belongs to another pair — the propagation never traverses it).
        for hit_id in sorted(hit_ids):
            for e in self.graph.outbound(hit_id) + self.graph.inbound(hit_id):
                other = e["to"] if e["from"] == hit_id else e["from"]
                if other in hit_ids or other in event_nodes or other in out:
                    continue
                out[other] = {"place_id": other, "reason": "NO_ROUTE",
                              "figures": {"via_place": hit_id,
                                          "inbound_share": 0.0}}
        return [out[k] for k in sorted(out)]

    # -- event emission ----------------------------------------------------
    def _emit(self, state: RunState, run_id: str, impact: Impact, parent_id,
              channel: str, mechanism: str, delay_d: float, magnitude: float,
              tick: int, base_dt: datetime) -> dict:
        seq = len(state.events)
        ev = {
            "run_id": run_id,
            "seq": seq,
            "node_id": impact.node_id,
            "parent_id": parent_id,
            "channel": channel,
            "mechanism": mechanism,
            "delay_d": _r6(delay_d),
            "magnitude": _r6(min(1.0, max(0.0, magnitude))),
            "at": _fmt_ts(base_dt + timedelta(days=impact.total_delay_d)),
            "tick": tick,
        }
        state.events.append(ev)
        state.impacts[seq] = {
            "node_id": impact.node_id, "s": impact.s,
            "duration_d": impact.duration_d, "total_delay_d": impact.total_delay_d,
        }
        state.emitted.add((impact.node_id, channel))
        return ev

    # -- propagation ---------------------------------------------------------
    def _arrivals_to(self, node_id: str, labels: dict) -> tuple[list[dict], list[dict]]:
        """All arrivals at ``node_id`` from labelled parents, as
        absolute lost flow: L_e = s_parent * f_e, where f_e
        is the edge's modelled flow (capacity_share_e * F_parent; see
        _build_flow_basis). A contribution is L_e / F_node^in — the
        child's inbound dependence, never the parent's outbound
        normalisation alone. Bypass arrivals are delivered by their
        primary leg's source (the reacting parent), as in the graph's
        bypass semantics."""
        g = self.graph
        primary: list[dict] = []
        reroutes: list[dict] = []
        basis = self._flow_basis[node_id]

        def sigma(lost: float) -> float:
            return lost / basis if basis > 0 else 0.0

        for e in g.inbound(node_id):
            key = edge_key(e)
            if e.get("bypass_of"):
                primary_edge = self._edge_by_key[e["bypass_of"]]
                parent_id = primary_edge["from"]
                if parent_id not in labels:
                    continue
                lab = labels[parent_id]
                reroutes.append({
                    "kind": "reroute", "parent_id": parent_id, "parent": lab,
                    "edge": e, "edge_key": key,
                    "transit": float(e["base_transit_d"]),
                    "delta": float(e["base_transit_d"])
                    - float(primary_edge["base_transit_d"]),
                    "sigma": sigma(lab["s"] * self._edge_flow[key]),
                    "duration_d": lab["duration_d"]})
                continue
            parent_id = e["from"]
            if parent_id not in labels:
                continue
            lab = labels[parent_id]
            primary.append({
                "kind": "downstream", "parent_id": parent_id, "parent": lab,
                "edge": e, "edge_key": key,
                "transit": float(e["base_transit_d"]),
                "sigma": sigma(lab["s"] * self._edge_flow[key]),
                "duration_d": lab["duration_d"]})
        for e in g.outbound(node_id):
            if e.get("bypass_of"):
                continue
            parent_id = e["to"]
            if parent_id not in labels:
                continue
            lab = labels[parent_id]
            key = edge_key(e)
            primary.append({
                "kind": "upstream", "parent_id": parent_id, "parent": lab,
                "edge": e, "edge_key": key,
                "transit": float(e["base_transit_d"]),
                "sigma": sigma(lab.get("s_hit", lab["s"])
                               * const("upstream_attenuation")
                               * self._edge_flow[key]),
                "duration_d": lab["duration_d"]})
        return primary, reroutes

    def _aggregate_primary(self, node_id: str, arrivals: list[dict],
                           params: dict, node_overrides: dict) -> dict:
        """The aggregated evaluation of a node's primary arrivals:
        totals, effective shortfall (inventory buffer applied once,
        to the total), channel and wait — all functions of the node
        and its TOTALS, never of visit order."""
        g = self.graph
        node = g.nodes[node_id]
        qs = params["queue_sensitivity"]
        sigma_total = min(1.0, sum(a["sigma"] for a in arrivals))
        d_agg = max(a["duration_d"] for a in arrivals)
        inv = float(node["inventory_days"])
        sigma_eff = max(0.0, sigma_total * d_agg - inv) / d_agg if d_agg > 0 else 0.0
        backlog = sigma_eff * self._flow_basis[node_id] * d_agg
        if inv >= const("inventory_gate_days") and sigma_total >= \
                self.threshold(node_id, params, node_overrides) and sigma_eff > 0:
            channel = "inventory"
            wait = self._service_wait(backlog, node, qs)
        elif node["mode"] == "air" or any(
                a["edge"]["mode"] == "air"
                and g.nodes[a["parent_id"]]["mode"] != "air" for a in arrivals):
            # Mode-shift: any aggregated arrival is a modal change to
            # air. Its delay scales with the LONGEST modal leg among
            # the arrivals (a set-monotone quantity).
            channel = "mode-shift"
            wait = const("mode_shift_transit_fraction") \
                * max(a["transit"] for a in arrivals) * sigma_eff
        elif node["queue_cap"] > 0:
            channel = "queue"
            wait = self._service_wait(backlog, node, qs)
        else:
            channel = "congestion"
            wait = self._service_wait(backlog, node, qs)
        return {"sigma_total": sigma_total, "sigma_eff": sigma_eff,
                "d_agg": d_agg, "channel": channel, "wait": _r6(wait),
                "inventory_days": inv}

    def _aggregate_reroute(self, arrivals: list[dict]) -> dict:
        """Aggregated bypass evaluation: shortfalls sum; the delay is
        the LARGEST added transit among the arrivals (set-monotone);
        elapsed uses the winning (max elapsed + transit) arrival."""
        return {"sigma_r": min(1.0, sum(a["sigma"] for a in arrivals)),
                "delay": _r6(max(0.0, max(a["delta"] for a in arrivals))),
                "d_agg": max(a["duration_d"] for a in arrivals)}

    @staticmethod
    def _winning(arrivals: list[dict], elapsed: dict) -> dict:
        """The deciding arrival: largest elapsed arrival time
        (parent elapsed + this leg's transit); ties by parent_id,
        then edge key (deterministic, order-independent)."""
        return sorted(arrivals, key=lambda a: (
            -(elapsed[a["parent_id"]] + a["transit"]),
            a["parent_id"], a["edge_key"]))[0]

    def _service_wait(self, backlog: float, node: dict, qs: float) -> float:
        """Service-time wait (ONE function for queue and
        congestion).

        ``backlog`` is an accumulated quantity of flow (flow units of
        the node's modelled basis): lost flow sustained over a
        duration, B = sigma * F^in * D for a child, or lost service
        B = loss * capacity * D for the seed. The node's queue can
        store at most queue_cap of it: stored = min(B, queue_cap);
        backlog beyond the cap does NOT wait at the node — it is
        diverted or unmet (recorded downstream as shortfall, not as
        dwell). A no-storage node (queue_cap = 0) holds only the
        registry congestion_storage_share of its backlog in transit.

        The stored backlog clears at the node's SPARE service rate,
        capacity net of arriving load: spare = capacity *
        (1 - utilisation). The delay is the MEAN wait of the affected
        flow under a triangular backlog profile (rise then clear):
        wait = qs * mean_wait_factor * stored / spare. Doubling
        capacity at fixed lost flow doubles the spare rate and halves
        the wait (until the cap binds differently); as utilisation
        -> 1 the spare rate -> 0 and the wait grows steeply.
        """
        capacity = float(node["capacity"])
        spare = capacity * (1.0 - self._utilisation(node))
        queue_cap = float(node["queue_cap"])
        if queue_cap > 0:
            stored = min(backlog, queue_cap)
        else:
            stored = const("congestion_storage_share") * backlog
        return qs * const("mean_wait_factor") * stored / spare

    def _propagate(self, state: RunState, run_id: str, frontier: list[Impact],
                   params: dict, node_overrides: dict, base_dt: datetime,
                   start_tick: int) -> None:
        """Two-phase fixed-point propagation.

        Phase A solves the hit set and shortfall labels to their
        least fixed point. Labels start from the pre-existing hits
        (the root on a fresh run; the shared prefix on a fork — those
        are FIXED and never recomputed). Repeatedly: (i) stabilise —
        recompute every non-fixed label's shortfall from the CURRENT
        label set (aggregates only grow as labels grow, so in-place
        updates in canonical order are monotone and the _r6 grid
        makes stabilisation finite); (ii) expand — label every
        unlabelled node whose aggregated evaluation (over the
        current label set) passes the hit test, at the next round.
        Neither first-hit-wins nor per-layer aggregation has this
        fixed point: a node hit early under a harsher shock would
        otherwise freeze a SMALLER shortfall than under a milder
        shock that lets more parents arrive first — the sweep's
        residual inversions were exactly that, and they are gone
        here because a label's final shortfall aggregates over ALL
        parents in the final hit set.

        Phase B emits the first-hit events in (round, node_id)
        order: a node's causal parent and elapsed time come from
        arrivals whose parents precede it in that order (every
        labelled node was expanded on the strength of earlier-round
        parents, so such a parent always exists), while its
        magnitude, channel and delay come from the FINAL aggregates
        over the whole hit set (see label_quantities: s_hit vs
        s_prop, and delay = the largest aggregated channel delay
        present). Shortfall is a stock (it aggregates over all
        suppliers that ultimately fail); elapsed time is a path
        quantity (it follows the causal order). Phase C then emits
        cost leaves and records WHYNOT evaluations from the same
        final aggregates.
        """
        labels: dict[str, dict] = {}
        elapsed: dict[str, float] = {}
        for nid, imp in state.hit.items():
            ev = state.events[imp.seq]
            labels[nid] = {"s": imp.s, "s_hit": imp.s,
                           "duration_d": imp.duration_d,
                           "round": int(ev.get("tick", 0)), "fixed": True}
            elapsed[nid] = imp.total_delay_d
        for imp in frontier:
            if imp.node_id not in labels:
                labels[imp.node_id] = {
                    "s": imp.s, "s_hit": imp.s,
                    "duration_d": imp.duration_d,
                    "round": start_tick - 1, "fixed": True}
                elapsed[imp.node_id] = imp.total_delay_d
        fixed = set(labels)
        round_no = max((lab["round"] for lab in labels.values()), default=0)

        def label_quantities(nid, labels):
            """(s_hit, s_prop, primary_all_agg, reroute_agg) for a node
            over the current label set. s_hit aggregates ALL arrivals
            (downstream + upstream + reroute) and drives the hit test,
            magnitude and delay. s_prop aggregates only downstream +
            reroute arrivals — the shortfall that propagates onward —
            so upstream backlog (a supplier's record of ITS customer's
            failure) can never feed back downstream and double-count
            the original loss, and a label's propagated shortfall
            never depends on which channel happened to label it."""
            primary, reroutes = self._arrivals_to(nid, labels)
            down = [a for a in primary if a["kind"] == "downstream"]
            up = [a for a in primary if a["kind"] == "upstream"]
            agg_all = self._aggregate_primary(nid, primary, params,
                                              node_overrides) if primary else None
            agg_down = self._aggregate_primary(nid, down, params,
                                               node_overrides) if down else None
            agg_up = self._aggregate_primary(nid, up, params,
                                             node_overrides) if up else None
            agg_r = self._aggregate_reroute(reroutes) if reroutes else None
            s_hit = max(agg_all["sigma_eff"] if agg_all else 0.0,
                        agg_r["sigma_r"] if agg_r else 0.0)
            # Onward propagation: downstream-sourced strain at full
            # strength; reroute-sourced at the reroute fraction;
            # upstream-sourced strain attenuated AGAIN by the
            # upstream factor (second-hand strain: a supplier backed
            # up by one customer does delay its other deliveries,
            # but the downstream->upstream->downstream loop gain is
            # then <= attenuation^2 = 0.25 and cannot ratchet the
            # original loss upward — the doubling/diamond sums in
            # the flow-basis sums stay exact).
            s_prop = max(agg_down["sigma_eff"] if agg_down else 0.0,
                         agg_r["sigma_r"] * const("reroute_propagate_fraction")
                         if agg_r else 0.0,
                         agg_up["sigma_eff"] * const("upstream_attenuation")
                         if agg_up else 0.0)
            d_agg = max([a["d_agg"] for a in (agg_all, agg_r) if a],
                        default=0.0)
            return s_hit, s_prop, d_agg, agg_all, agg_r, primary, reroutes

        def is_hit(nid, agg_all, agg_r):
            thr = self.threshold(nid, params, node_overrides)
            if agg_all and (agg_all["sigma_eff"] >= thr or (
                    agg_all["wait"] >= DELAY_HIT_FLOOR_D
                    and agg_all["sigma_eff"] >= MIN_MATERIAL_SHARE)):
                return True
            if agg_r and (agg_r["sigma_r"] >= thr or (
                    agg_r["delay"] >= DELAY_HIT_FLOOR_D
                    and agg_r["sigma_r"] >= MIN_MATERIAL_SHARE)):
                return True
            return False

        # ---- Phase A: least fixed point of hit set + shortfalls ----
        while True:
            if round_no > MAX_LAYERS or len(labels) > MAX_EVENTS:
                raise EngineError("runaway cascade: quiescence bound exceeded",
                                  code="runaway")
            # (i) stabilise non-fixed labels over the current label set
            for _ in range(MAX_EVENTS + MAX_EVENTS):
                changed = False
                for nid in sorted(labels):
                    if nid in fixed:
                        continue
                    s_hit, s_prop, d_agg, _, _, _, _ = \
                        label_quantities(nid, labels)
                    lab = labels[nid]
                    if _r6(s_hit) != _r6(lab["s_hit"]) or \
                            _r6(s_prop) != _r6(lab["s"]) or \
                            d_agg != lab["duration_d"]:
                        lab["s_hit"] = _r6(s_hit)
                        lab["s"] = _r6(s_prop)
                        lab["duration_d"] = d_agg
                        changed = True
                if not changed:
                    break
            else:
                raise EngineError("runaway cascade: stabilisation bound",
                                  code="runaway")
            # (ii) expand the hit set by one round. Evaluations use
            # the label set frozen at the round's start (snapshot);
            # additions are merged only after every candidate has
            # been judged — otherwise a node labelled early in the
            # round would silently distort its later round-mates.
            round_no += 1
            snapshot = dict(labels)
            additions: dict[str, dict] = {}
            for nid in sorted(self.graph.nodes):
                if nid in snapshot:
                    continue
                s_hit, s_prop, d_agg, agg_all, agg_r, primary, reroutes = \
                    label_quantities(nid, snapshot)
                if not primary and not reroutes:
                    continue
                if is_hit(nid, agg_all, agg_r):
                    additions[nid] = {"s": _r6(s_prop), "s_hit": _r6(s_hit),
                                      "duration_d": d_agg,
                                      "round": round_no, "fixed": False}
            labels.update(additions)
            if not additions:
                break

        # ---- Phase B: emit first-hit events in causal order --------
        order = sorted((nid for nid in labels if nid not in fixed),
                       key=lambda n: (labels[n]["round"], n))
        for nid in order:
            lab = labels[nid]
            primary, reroutes = self._arrivals_to(nid, labels)
            agg_all = self._aggregate_primary(nid, primary, params,
                                              node_overrides) if primary else None
            agg_r = self._aggregate_reroute(reroutes) if reroutes else None
            # The node's delay is the LARGEST aggregated channel delay
            # among the channels present (primary wait vs reroute
            # added transit) — a set-monotone quantity that never
            # depends on which channel labelled the node. The channel
            # label is the argmax (primary wins ties).
            cands = []
            if agg_all:
                cands.append((agg_all["wait"], 1, agg_all["channel"]))
            if agg_r:
                cands.append((agg_r["delay"], 0, "reroute"))
            delay, _, channel = max(cands)
            magnitude = max(agg_all["sigma_eff"] if agg_all else 0.0,
                            agg_r["sigma_r"] if agg_r else 0.0)
            all_arrivals = primary + reroutes
            prior = [a for a in all_arrivals if a["parent_id"] in elapsed]
            if not prior:  # pragma: no cover - expansion guarantees one
                raise EngineError("internal: labelled node without causal parent",
                                  code="runaway")
            winning = self._winning(prior, elapsed)
            mechanism = MECHANISMS["queue_upstream"] if (
                channel == "queue" and winning["kind"] == "upstream") \
                else MECHANISMS[channel]
            total = _r6(elapsed[winning["parent_id"]] + winning["transit"]
                        + (0.0 if channel == "reroute" else delay))
            # For a reroute channel the elapsed uses the bypass leg's
            # full transit (in `winning["transit"]`); delay_d is the
            # added transit over the primary leg, already inside it.
            child = Impact(node_id=nid, s=lab["s"],
                           duration_d=lab["duration_d"], total_delay_d=total)
            parent_seq = labels[winning["parent_id"]].get(
                "seq", state.hit[winning["parent_id"]].seq)
            self._accept(state, run_id, child, parent_seq, channel, mechanism,
                         delay, magnitude, lab["round"], base_dt, [], set())
            lab["seq"] = child.seq
            elapsed[nid] = total

        # ---- Phase C: leaves + WHYNOT evaluations ------------------
        for nid in sorted(self.graph.nodes):
            primary, reroutes = self._arrivals_to(nid, labels)
            if nid not in labels:
                if primary:
                    agg = self._aggregate_primary(nid, primary, params,
                                                  node_overrides)
                    winning = self._winning(primary, elapsed)
                    if agg["sigma_total"] <= 0:
                        _record_eval(state, nid, "no_flow", winning["parent_id"],
                                     {"inbound_share": 0.0,
                                      "via_edge": winning["edge_key"],
                                      **({"direction": "upstream"}
                                         if winning["kind"] == "upstream"
                                         else {})})
                    elif agg["sigma_total"] >= self.threshold(
                            nid, params, node_overrides) \
                            and agg["sigma_eff"] <= 0:
                        _record_eval(state, nid, "absorbed",
                                     winning["parent_id"],
                                     {"inbound_share": _r6(agg["sigma_total"]),
                                      "shortfall_days": _r6(
                                          agg["sigma_total"] * agg["d_agg"]),
                                      "inventory_days": _r6(
                                          agg["inventory_days"])})
                    else:
                        figures = {"inbound_share": _r6(agg["sigma_total"]),
                                   "hit_threshold": _r6(self.threshold(
                                       nid, params, node_overrides)),
                                   "raw_delay_d": agg["wait"],
                                   "delay_floor_d": DELAY_HIT_FLOOR_D,
                                   "channel": agg["channel"]}
                        if all(a["kind"] == "upstream" for a in primary):
                            figures["direction"] = "upstream"
                        _record_eval(state, nid, "below",
                                     winning["parent_id"], figures)
                elif reroutes:
                    agg_r = self._aggregate_reroute(reroutes)
                    winning = self._winning(reroutes, elapsed)
                    _record_eval(state, nid, "below", winning["parent_id"],
                                 {"inbound_share": _r6(agg_r["sigma_r"]),
                                  "hit_threshold": _r6(self.threshold(
                                      nid, params, node_overrides)),
                                  "raw_delay_d": agg_r["delay"],
                                  "delay_floor_d": DELAY_HIT_FLOOR_D,
                                  "channel": "reroute"})
            # cost leaf: any node (hit or not) whose aggregated primary
            # evaluation carries a material effective shortfall from a
            # parent whose elapsed time passes the cost gate.
            if primary and (nid, "cost") not in state.emitted:
                agg = self._aggregate_primary(nid, primary, params,
                                              node_overrides)
                winning = self._winning(primary, elapsed)
                if elapsed[winning["parent_id"]] >= const("cost_gate_d") \
                        and agg["sigma_eff"] >= MIN_MATERIAL_SHARE:
                    mag = min(1.0, elapsed[winning["parent_id"]]
                              / COST_DELAY_SCALE_D)
                    leaf = Impact(node_id=nid, s=agg["sigma_total"],
                                  duration_d=agg["d_agg"],
                                  total_delay_d=_r6(
                                      elapsed[winning["parent_id"]]
                                      + winning["transit"]))
                    self._emit(state, run_id, leaf,
                               parent_id=state.hit[winning["parent_id"]].seq,
                               channel="cost", mechanism=MECHANISMS["cost"],
                               delay_d=0.0, magnitude=mag,
                               tick=labels[winning["parent_id"]]["round"] + 1,
                               base_dt=base_dt)

    def _accept(self, state, run_id, child: Impact, parent_seq: int, channel: str,
                mechanism: str, delay: float, magnitude: float, tick: int,
                base_dt, accepted, accepted_nodes) -> None:
        ev = self._emit(state, run_id, child, parent_id=parent_seq, channel=channel,
                        mechanism=mechanism, delay_d=delay, magnitude=magnitude,
                        tick=tick, base_dt=base_dt)
        child.seq = ev["seq"]
        state.hit[child.node_id] = child
        accepted.append(child)
        accepted_nodes.add(child.node_id)


def run_scenario(graph: Graph, run_id: str, disruption: dict,
                 params: dict | None = None, node_overrides: dict | None = None) -> RunState:
    return Engine(graph).run(run_id, disruption, params, node_overrides)
