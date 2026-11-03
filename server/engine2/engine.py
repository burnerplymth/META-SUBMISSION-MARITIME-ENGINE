"""ENGINE REV 2: a causal stock-and-flow world model on the graph.

This package sits beside server/engine (engine v1), which it leaves unchanged.
It emits the same event stream (run_id, seq, node_id, parent_id,
channel, mechanism, delay_d, magnitude, at, tick), keeps the same
invariants (DAG, gapless seq, quiescence, no RNG, byte-identical replay)
and is opt-in: POST /run {"engine_rev": 2}.

WHAT CHANGED VERSUS ENGINE v1 (and why):

  v1 summed vessels/day (chokepoints) and TEU/day (ports) as if they
  were comparable, so a chokepoint's lost flow was ~0.1 % of a port's
  inbound flow and the flagship scenarios were nearly empty.
  v1 also had no time axis (duration only scaled a backlog), discarded
  backlog above queue_cap, and picked "reroute delay" by subtracting the
  transit of two unrelated legs. Rev 2 replaces the formulas by state:

  STATE (per place): stock Z (cargo held at the place, own unit), the
  part of it that is diverted cargo Zx, baseline cover S0 =
  inventory_days x lambda0, service rate mu(t) = mu_max x (1 - loss(t)).
  STATE (per leg): a FIFO pipeline: cargo that left the sender at step
  k arrives at k + round(transit / dt). Gaps and bunching emerge from
  the delay line; nothing is added by hand.

  MECHANISMS (identities unless marked PLUG; the PLUG list with
  sources/replacements is server/engine2/plugins.py):
    queue     dZ/dt = arrivals - departures, departures d = min(mu,
              Z/dt) while Z exceeds the cover S0, else min(mu, lambda0,
              Z/dt); FIFO wait of an arriving unit = backlog ahead /
              service rate (fluid Little's law), evaluated exactly on
              the cumulative curves after the run. Nothing is dropped.
    spillback above queue_cap a place accepts only mu; refused flow
              stays at the sender, whose own queue then grows.
    reroute   blocked flow on a leg is reassigned to its authored
              bypass lane when added days + forecast wait on the bypass
              beat the forecast wait on the primary; diverted flow is
              capped by the bypass place's headroom mu_max - lambda0
              (saturation). Cargo that reaches a place by diversion is
              forwarded on that place's bypass lanes to restore the
              destination's supply, arriving after the lane's transit.
    inventory while Z > 0 a place keeps shipping lambda0 although
              arrivals fall (cover days / shortfall rate = stockout
              time); afterwards onward flow equals arrivals.
    mode-shift a port's delayed cargo moves to its air leg up to the
              air hub's headroom (PLUG fractions).
    cost      derived indicator: queue + cover-deficit stock-days over
              baseline stock-days (never currency).

  UNITS: one flow unit per layer. Sea = vessel-calls/day (ports: TEU/day
  / teu_per_call; chokepoints are already vessels/day). Cross-layer legs
  use a plug-in equivalence. Receiver-side supply per leg is the
  attributed volume V_e = omega_e x lambda0_receiver (see plugins).

Event emission: first-hit events are emitted in (step, place id) order, a
parent is always a place already hit at an EARLIER step, so parent.seq <
seq and parent.at < at hold by construction. delay_d and magnitude are
final aggregates over the whole run (peak FIFO wait, peak share), as in
v1. NO RNG anywhere.
"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field
from datetime import timedelta

from engine.canonical import canonical_json, stream_hash
from engine.constants import const
from engine.engine import (EngineError, _fmt_ts, _parse_ts, _r6,
                           core_subgraph, validate_disruption,
                           validate_params)
from engine.graph import Graph, edge_key

from .plugins import ENGINE_LABEL, ENGINE_REV, PLUGINS_HASH, plug
from .plugins_cal import CAL_HASH, PROFILE_LABEL
from .plugins_d import D_HASH, PROFILE_LABEL as D_LABEL

EPS = 1e-9
KMAX_DAYS = 40000.0
CHANNELS = {"queue", "congestion", "reroute", "mode-shift", "inventory", "cost"}

DELAY_FLOOR_D = const("delay_hit_floor_d")
MIN_MATERIAL = const("min_material_share")
PULSE_FACTOR = const("pulse_duration_factor")

WHYNOT_REASONS = {"ABSORBED", "BELOW_THRESHOLD", "NO_ROUTE"}

MECH = {
    "root": "capacity loss at source: service rate = mu_max x (1 - loss)",
    "queue_deficit": "queue: arrivals exceed service rate, wait = backlog / service rate",
    "queue_surge": "queue: bunched arrivals (released backlog and late diverted cargo) exceed service rate, wait = backlog / service rate",
    "queue_block": "queue: spillback, receiving place above queue_cap accepts only its service rate so refused flow is held here, wait = backlog / service rate",
    "congestion": "congestion: arrivals exceed service rate with no storage, refused flow held upstream",
    "inventory": "inventory: arrivals below baseline draw the cover down, at stockout onward flow equals arrivals (cover days / shortfall rate)",
    "reroute": "reroute: bypass lane {lane} carries the flow of {primary}, forecast wait at the primary exceeds added days plus forecast wait here, capped by headroom",
    "mode-shift": "mode-shift: delayed cargo moves to the air leg, capped by air hub headroom (mu_max - lambda0)",
    "cost": "cost: excess queue and cover stock-days over baseline stock-days (normalised, not currency)",
}


def _dt_for(duration_d: float) -> float:
    ts = plug("time_step_d")
    if duration_d <= ts["short_max_horizon_d"]:
        return ts["short"]
    if duration_d <= ts["medium_max_horizon_d"]:
        return ts["medium"]
    return ts["long"]


@dataclass
class RunState2:
    events: list = field(default_factory=list)
    impacts: dict = field(default_factory=dict)
    hit: dict = field(default_factory=dict)       # place -> seq of first-hit event
    emitted: set = field(default_factory=set)
    constants_hash: str = PLUGINS_HASH
    evaluations: dict = field(default_factory=dict)
    not_hit: list = field(default_factory=list)
    engine_rev: int = ENGINE_REV
    engine_label: str = ENGINE_LABEL
    interventions: list = field(default_factory=list)
    dt: float = 0.0
    eff_root: dict = field(default_factory=dict)
    audit: dict = field(default_factory=dict)
    params: dict = field(default_factory=dict)            # effective run params
    node_overrides: dict = field(default_factory=dict)    # effective per-place thresholds
    thr_history: list = field(default_factory=list)       # threshold segments over steps

    def run_record(self, run_id: str | None = None) -> dict:
        return {"run_id": run_id if run_id is not None else (
                    self.events[0]["run_id"] if self.events else None),
                "engine_rev": self.engine_rev, "engine_label": self.engine_label,
                "constants_hash": self.constants_hash,
                "stream_hash": stream_hash(self.events),
                "event_count": len(self.events)}


class _Sim:
    """Raw simulation output handed to the event emission pass."""


class Engine2:
    engine_rev = ENGINE_REV
    engine_label = ENGINE_LABEL

    def __init__(self, graph: Graph, plugins: dict | None = None, routes=None,
                 calibration=None, profile=None):
        """``routes`` (opt-in, None by default - then nothing below differs
        from the engine without it): a routes.RoutesTable built from the
        graph services layer. The solver then runs on the table's
        routes-augmented copy of the CORE graph and gains its Cape A/B
        lanes; ``full_graph`` stays the served graph.

        ``calibration`` (opt-in, None by default - then nothing differs from the
        engine without it): a calibration.Calibration (profile rev2c). Per-place
        mu_max ratios and cover days from cited data replace the two global
        TEMPORARY plug-ins, and an inventory event reports its deficit in days
        instead of the length of the shortfall. Stamped ENGINE REV 2 CALIBRATED
        with its own registry hash.

        ``profile`` (opt-in, None by default - then nothing differs from the engine
        without it): a profile_d.ProfileD (ENGINE REV 2 profile d). It brings the
        calibration (rev2c) and adds: sea-lane transit days on every sea leg, the
        chokepoint queue disciplines (Panama slots and booking tiers, Suez convoys,
        spillback of exogenous through-traffic, a graded demand response) and rotation
        recovery. Stamped ENGINE REV 2 \u00b7 PROFILE d with its own registry hash."""
        self.full_graph = graph
        self.prof = profile
        if profile is not None:
            calibration = profile.cal
        self.graph = core_subgraph(graph)
        if profile is not None:
            # profile d runs on the WHOLE graph (SHELL places route and propagate, tier wall in
            # _emit); the CORE view of the same retimed graph is kept for the naive baseline
            self.graph = profile.retime(graph)
        ov = plugins or {}
        self.psi = float(ov.get("teu_per_call", plug("teu_per_call")))
        self.routes = routes
        self.cal = calibration
        self.base_core = core_subgraph(self.graph) if profile is not None else self.graph
        if routes is not None:
            self.graph = routes.augment(self.graph, self.psi)
        self.mu_ratio = float(ov.get("mu_max_ratio", plug("mu_max_ratio")))
        ms = plug("mode_shift")
        self.shift_frac = float(ov.get("time_sensitive_fraction", ms["time_sensitive_fraction"]))
        self.shift_trigger = float(ov.get("trigger_wait_d", ms["trigger_wait_d"]))
        self.tpt = float(ov.get("tonnes_per_teu", ms["tonnes_per_teu"]))
        self.cost_ref_d = float(ov.get("cost_reference_d", plug("cost_reference_d")))
        self._memo: dict = {}
        self._build()
        if profile is not None:
            profile.attach(self)

    # ------------------------------------------------------------------ build
    def _build(self) -> None:
        G = self.graph
        self.ids = sorted(G.nodes)
        self.ix = {nid: i for i, nid in enumerate(self.ids)}
        n = len(self.ids)
        self.lam0 = [0.0] * n
        self.mumax = [0.0] * n
        self.S0 = [0.0] * n
        self.qcap = [0.0] * n
        self.layer = [""] * n
        self.unit = [1.0] * n
        self.core_flag = [G.nodes[nid].get("tier", "core") == "core" for nid in self.ids]
        for nid in self.ids:
            i = self.ix[nid]
            node = G.nodes[nid]
            unit_s = str(node.get("capacity_unit", ""))
            u = 1.0 / self.psi if unit_s.upper().startswith("TEU") else 1.0
            self.unit[i] = u
            self.lam0[i] = float(node["capacity"]) * u
            ratio, cover = self.mu_ratio, float(node["inventory_days"])
            if self.cal is not None:
                ratio = self.cal.mu_ratio_for(nid, node, self.mu_ratio)
                cover = self.cal.cover_days_for(nid, node)
            self.mumax[i] = ratio * self.lam0[i]
            self.S0[i] = cover * self.lam0[i]
            self.qcap[i] = float(node["queue_cap"]) * u
            self.layer[i] = node["mode"]
        totals: dict[str, float] = {}
        for i in range(n):
            totals[self.layer[i]] = totals.get(self.layer[i], 0.0) + self.lam0[i]
        self.layer_total = totals
        # cost indicator denominator: baseline stock-days = (cover stock + the
        # in-process stock of cost_in_process_days of throughput) x reference days
        inproc = float(plug("cost_in_process_days"))
        self.cost_base = [(self.S0[i] + self.lam0[i] * inproc) * self.cost_ref_d
                          for i in range(n)]

        # primary legs
        self.leg_key, self.leg_p, self.leg_q = [], [], []
        self.leg_share, self.leg_tau, self.leg_mode = [], [], []
        leg_of_key: dict[str, int] = {}
        prim = [e for e in sorted(G.edges, key=edge_key) if not e.get("bypass_of")]
        for e in prim:
            leg_of_key[edge_key(e)] = len(self.leg_key)
            self.leg_key.append(edge_key(e))
            self.leg_p.append(self.ix[e["from"]])
            self.leg_q.append(self.ix[e["to"]])
            self.leg_share.append(float(e["capacity_share"]))
            self.leg_tau.append(float(e["base_transit_d"]))
            self.leg_mode.append(e["mode"])
        nl = len(self.leg_key)
        self.in_prim = [[] for _ in range(n)]
        self.out_prim = [[] for _ in range(n)]
        for lg in range(nl):
            self.in_prim[self.leg_q[lg]].append(lg)
            self.out_prim[self.leg_p[lg]].append(lg)
        # attribution omega and receiver-scale supply V
        w = [self.leg_share[lg] * self.lam0[self.leg_p[lg]]
             * self.g(self.leg_p[lg], self.leg_q[lg]) for lg in range(nl)]
        self.omega = [0.0] * nl
        for q in range(n):
            tot = sum(w[lg] for lg in self.in_prim[q])
            if tot > 0:
                for lg in self.in_prim[q]:
                    self.omega[lg] = w[lg] / tot
        # Receiver-scale supply of a leg: the receiver's modelled dependence on
        # it (omega x lambda0_receiver) bounded by what the sender physically
        # ships on it (share x lambda0_sender x g). The bound keeps a small
        # sender from creating mass at a large receiver (conservation) and
        # makes feedback around a cycle decay with the shares.
        self.V = [min(self.omega[lg] * self.lam0[self.leg_q[lg]], w[lg])
                  for lg in range(nl)]

        # bypass lanes (routes table = authored bypass legs)
        self.lane_key, self.lane_kind, self.lane_x, self.lane_y = [], [], [], []
        self.lane_leg, self.lane_tau, self.lane_dtau = [], [], []
        self.lane_exo = []        # may the place's exogenous through-traffic use this A lane
        self.unrouted_bypass = []
        for e in sorted(G.edges, key=edge_key):
            pk = e.get("bypass_of")
            if not pk:
                continue
            lg = leg_of_key.get(pk)
            if lg is None:
                self.unrouted_bypass.append(edge_key(e))
                continue
            x, y = self.ix[e["from"]], self.ix[e["to"]]
            if x == self.leg_p[lg]:
                kind = "A"
            elif y == self.leg_q[lg]:
                kind = "B"
            else:
                self.unrouted_bypass.append(edge_key(e))
                continue
            self.lane_key.append(edge_key(e))
            self.lane_kind.append(kind)
            self.lane_x.append(x)
            self.lane_y.append(y)
            self.lane_leg.append(lg)
            self.lane_tau.append(float(e["base_transit_d"]))
            self.lane_exo.append(True)
            self.lane_dtau.append(max(0.0, float(e["base_transit_d"]) - self.leg_tau[lg]))
        # ROUTES (opt-in): the table's Cape A/B lanes join the authored ones.
        if self.routes is not None:
            for sp in self.routes.lane_specs:
                lg = leg_of_key.get(sp["leg"])
                if lg is None or sp["from"] not in self.ix or sp["to"] not in self.ix:
                    self.unrouted_bypass.append(sp["key"])
                    continue
                x, y = self.ix[sp["from"]], self.ix[sp["to"]]
                if sp["kind"] == "A" and x == self.leg_p[lg]:
                    kind = "A"
                elif sp["kind"] == "B" and y == self.leg_q[lg]:
                    kind = "B"
                else:
                    self.unrouted_bypass.append(sp["key"])
                    continue
                self.lane_key.append(sp["key"])
                self.lane_kind.append(kind)
                self.lane_x.append(x)
                self.lane_y.append(y)
                self.lane_leg.append(lg)
                self.lane_exo.append(bool(sp.get("exo", True)))
                self.lane_tau.append(float(sp["tau"]))
                self.lane_dtau.append(float(sp["dtau"]) if kind == "A" else
                                      max(0.0, float(sp["tau"]) - self.leg_tau[lg]))
        nln = len(self.lane_key)
        self.lanesA_of_leg = [[] for _ in range(nl)]
        self.lanesB_of_node = [[] for _ in range(n)]
        self.lanes_into = [[] for _ in range(n)]     # lanes delivering extra arrivals into a node
        for ln in range(nln):
            if self.lane_kind[ln] == "A":
                self.lanesA_of_leg[self.lane_leg[ln]].append(ln)
                self.lanes_into[self.lane_y[ln]].append(ln)
            else:
                self.lanesB_of_node[self.lane_x[ln]].append(ln)
        for lg in range(nl):
            self.lanesA_of_leg[lg].sort(key=lambda ln: (self.lane_dtau[ln], self.lane_key[ln]))
        # mode-shift lanes: a sea port's air leg to an air hub
        self.shift_leg_of_node = [[] for _ in range(n)]
        self.shift_into = [[] for _ in range(n)]
        for lg in range(nl):
            p, q = self.leg_p[lg], self.leg_q[lg]
            if (self.leg_mode[lg] == "air" and G.nodes[self.ids[p]]["kind"] == "port"
                    and G.nodes[self.ids[q]]["kind"] == "air_hub"
                    and self.unit[p] != 1.0):
                self.shift_leg_of_node[p].append(lg)
        self.nA = nln
        # senders that hold an A lane into a place (they must be re-evaluated
        # whenever that place is active)
        self.senders_with_A = [set() for _ in range(n)]
        # exogenous through-traffic of a place (arrivals no modelled leg
        # accounts for) takes the same authored alternative as its modelled
        # legs: A lanes whose primary leg ends at the place
        self.exo_lanes = [[] for _ in range(n)]
        for ln in range(nln):
            if self.lane_kind[ln] == "A":
                q = self.leg_q[self.lane_leg[ln]]
                self.senders_with_A[q].add(self.lane_x[ln])
                if self.lane_exo[ln]:
                    self.exo_lanes[q].append(ln)
        for q in range(n):
            self.exo_lanes[q].sort(key=lambda ln: (self.lane_dtau[ln], self.lane_key[ln]))
        self.exo_base = [max(0.0, self.lam0[q] - sum(self.V[lg] for lg in self.in_prim[q]))
                         for q in range(n)]
        # profile d hooks; these defaults make every one of them a no-op (profile.attach sets them)
        self.p_slot = [False] * n          # slot-managed: service rate in a cut = lambda0 x (1 - loss)
        self.p_tier = [0.0] * n            # booked share of a two-tier place (0 = not tiered)
        self.p_fifo = [False] * n          # profile e: a tiered place reads its wait on the whole slot count (no strict priority)
        self.p_conv = [None] * n           # convoy mix [(weight, convoys/day)] or None
        self.p_spill = [False] * n         # hold exogenous traffic above the anchorage
        self.p_div = 0.0                   # logistic scale (d) of the demand response; 0 = all-or-nothing
        self.p_zeta = [0.0] * nl           # share of a leg's flow carried by continuing vessels
        self.p_cc = [0.0] * n              # baseline coupled throughput of a place
        self.p_rho = 0.0                   # absorbed fraction of a late arrival per call

    def _quant(self, j: int, w: float) -> float:
        """Convoy windows (profile d): a wait at a convoy place is rounded up to the next convoy
        departure of each direction, averaged over the directions' weights. Identity elsewhere."""
        cv = self.p_conv[j]
        if cv is None or w <= EPS or math.isinf(w):
            return w
        return sum(wt * math.ceil(w * nn - EPS) / nn for wt, nn in cv)

    def g(self, a: int, b: int) -> float:
        la, lb = self.layer[a], self.layer[b]
        if la == lb:
            return 1.0
        ta, tb = self.layer_total.get(la, 0.0), self.layer_total.get(lb, 0.0)
        return tb / ta if ta > 0 and tb > 0 else 1.0

    # ----------------------------------------------------------------- loss
    @staticmethod
    def _profile(loss: float, dur: float, shape: str, t: float) -> float:
        if t < 0:
            return 0.0
        if shape == "step":
            return loss if t < dur else 0.0
        if shape == "ramp":
            return loss * (t / dur) if t < dur else 0.0
        return loss if t < dur * PULSE_FACTOR else 0.0     # pulse

    @staticmethod
    def _profile_end(dur: float, shape: str) -> float:
        return dur * PULSE_FACTOR if shape == "pulse" else dur

    # -------------------------------------------------------------- threshold
    def threshold(self, node_id: str, params: dict, node_overrides: dict | None) -> float:
        if node_overrides and node_id in node_overrides:
            v = node_overrides[node_id].get("hit_threshold")
            if v is not None:
                return float(v)
        node = self.graph.nodes[node_id]
        if node.get("hit_threshold") is not None:
            return float(node["hit_threshold"])
        return params["hit_threshold"]

    # ------------------------------------------------------------------- run
    def run(self, run_id: str, disruption: dict, params: dict | None = None,
            node_overrides: dict | None = None, *, interventions: list | None = None,
            dt: float | None = None, thr_history: list | None = None,
            prefix: dict | None = None) -> RunState2:
        """thr_history (forks): earlier threshold segments
        [{from_step, params, node_overrides}, ...]; the run's own params /
        node_overrides apply from the end of that history. Without it the
        run's params apply from step 0."""
        params = validate_params(params)
        spec = validate_disruption(self.graph, disruption)
        if self.prof is not None and not self.core_flag[self.ix[spec["node_id"]]]:
            raise EngineError(f"unknown node_id: {spec['node_id']!r} (SHELL places are routing-grade; "
                              "a disruption must be at a CORE place)", code="unknown_node")
        base_dt = _parse_ts(spec["started_at"])
        dt = float(dt) if dt is not None else _dt_for(spec["duration_d"])
        ivs = [dict(iv) for iv in (interventions or [])]
        # The simulation is a pure function of (spec, params, overrides,
        # interventions, dt, threshold history) and read-only afterwards, so
        # repeated identical requests (ensembles run each variant twice) are
        # memoised; the run_id only enters at event emission below.
        mkey = canonical_json([spec, params, node_overrides or {}, ivs, dt, thr_history])
        sim = self._memo.get(mkey)
        if sim is None:
            sim = self._simulate(spec, params, node_overrides or {}, ivs, dt, thr_history)
            if len(self._memo) >= 96:
                self._memo.clear()
            self._memo[mkey] = sim
        self._last_sim = sim        # debugging / introspection only
        state = self._emit(run_id, spec, params, base_dt, dt, sim, ivs, prefix)
        state.params = dict(params)
        state.node_overrides = {k: dict(v) for k, v in (node_overrides or {}).items()}
        state.thr_history = ([dict(s) for s in thr_history] if thr_history else
                             [{"from_step": 0, "params": dict(params),
                               "node_overrides": state.node_overrides}])
        return state

    # --------------------------------------------------------------- simulate
    def _simulate(self, spec, params, node_overrides, ivs, dt, thr_history):
        ids, ix, n = self.ids, self.ix, len(self.ids)
        lam0, mumax, S0, qcap = self.lam0, self.mumax, self.S0, self.qcap
        nl, nln = len(self.leg_key), len(self.lane_key)
        root = ix[spec["node_id"]]

        # ---- loss profiles per place -------------------------------
        profiles: dict[int, list] = {root: [(0.0, spec["capacity_loss"], spec["duration_d"],
                                              spec["shape"])]}
        for iv in ivs:
            i = ix[iv["node_id"]]
            profiles.setdefault(i, []).append(
                (iv["start_step"] * dt, float(iv["capacity_loss"]),
                 float(iv["duration_d"]), iv["shape"]))
        for i in profiles:
            profiles[i].sort(key=lambda p: p[0])
        prof_end = {i: max(p[0] + self._profile_end(p[2], p[3]) for p in pl)
                    for i, pl in profiles.items()}
        prof_start = {i: min(p[0] for p in pl) for i, pl in profiles.items()}
        t_last = max(prof_end.values())
        starts: dict[int, list] = {}
        for i, pl in profiles.items():
            for p in pl:
                starts.setdefault(int(round(p[0] / dt)), []).append(i)

        def loss_at(i, t):
            pl = profiles.get(i)
            if not pl:
                return 0.0, 0.0
            cur = None
            for p in pl:
                if t >= p[0]:
                    cur = p
            if cur is None:
                return 0.0, 0.0
            return (self._profile(cur[1], cur[2], cur[3], t - cur[0]),
                    cur[0] + self._profile_end(cur[2], cur[3]))

        lag_leg = [max(1, int(round(self.leg_tau[lg] / dt))) for lg in range(nl)]
        lag_lane = [max(1, int(round(self.lane_tau[ln] / dt))) for ln in range(nln)]
        shift_lag = {lg: max(1, int(round(self.leg_tau[lg] / dt)))
                     for i in range(n) for lg in self.shift_leg_of_node[i]}
        # profile d hooks (every one is a no-op for the default engine, see _build)
        slot_j, tier_j, fifo_j = self.p_slot, self.p_tier, self.p_fifo
        spill_j, rho_a, div_s = self.p_spill, self.p_rho, self.p_div
        spill_on = any(spill_j)
        quant = self._quant
        dust = EPS if self.prof is not None else 0.0

        def mu_at(j, l):
            """service rate of place j under capacity loss l: mu_max x (1 - l); a slot-managed
            place cuts its slot count (lambda0 x (1 - l)) instead, never above mu_max"""
            if slot_j[j] and l > 0.0:
                return min(mumax[j], lam0[j] * (1.0 - l))
            return mumax[j] * (1.0 - l)

        def div_frac(adv):
            """share of a flow that takes the alternative for a time advantage ``adv`` > 0 days:
            2 x sigmoid(adv / s) - 1 (0 at no advantage, 1 for a large one); all of it when
            the demand response is off (rev 2 behaviour)"""
            if div_s <= 0.0:
                return 1.0
            return 2 / (1 + math.exp(-adv / div_s)) - 1

        # hit thresholds: a history of segments (a fork switches thresholds for
        # the steps after its branch step; earlier steps keep the parent's)
        thr_default = [self.threshold(ids[i], params, node_overrides) for i in range(n)]
        segs = []
        for sgm in (thr_history or []):
            segs.append((int(sgm["from_step"]),
                         [self.threshold(ids[i], sgm["params"], sgm["node_overrides"])
                          for i in range(n)]))
        segs.sort(key=lambda s: s[0])

        def thr_of(i, k):
            cur = thr_default[i]
            for fs, arr in segs:
                if k >= fs:
                    cur = arr[i]
            return cur

        # ---- state ------------------------------------------------
        Z = list(S0)
        Zx = [0.0] * n
        accept = [1.0] * n
        exo_rate = [0.0] * n
        phi_last = [1.0] * nl
        lane_rate = [0.0] * nln
        shift_rate = {lg: 0.0 for lg in shift_lag}
        pipe_phi = [dict() for _ in range(nl)]
        pipe_x = [dict() for _ in range(nln)]
        pipe_shift = {lg: dict() for lg in shift_lag}
        sched: dict[int, set] = {}
        carry: set = set()
        hit_step = {root: 0}
        hit_info = {root: {"step": 0, "channel": "congestion", "parent": None, "cause": "root"}}
        pending_leaf: dict = {}
        leaf_done: set = set()
        leaves: list = []
        cause = [dict() for _ in range(n)]
        E = [0.0] * n
        Xh = [0.0] * n              # exogenous traffic held outside a full place (profile d)
        bk_rate = [0.0] * n         # booked rate served at once at a tiered place, last step
        ub_share = [1.0] * n        # unbooked share of a tiered place's arrivals, last step
        peak = [dict(short=0.0, arr_def=0.0, extra=0.0, shift=0.0, held=0.0, surge=0.0,
                     bl=0.0, wait_now=0.0, qfill=0.0, dtau=0.0, rr_lane=None,
                     first_step=None, gap=0, gap_run=0, adef=0, dcum=0.0, dmax=0.0)
                for _ in range(n)]
        series: list = [None] * n
        nodeseen = [False] * n
        audit = {"arrivals": 0.0, "departed": 0.0, "shifted_out": 0.0, "held_back": 0.0,
                 "delta_stock": 0.0, "steps": 0}
        if spill_on:
            audit.update(exo_held=0.0, exo_released=0.0)
        kmax = int(KMAX_DAYS / dt)
        n_events = 1
        tref = self.cost_ref_d

        for sid in starts.get(0, []):
            carry.add(sid)
        k = 0
        while True:
            if k > kmax:
                raise EngineError("runaway cascade: step bound exceeded", code="runaway")
            t = k * dt
            todo = set(sched.pop(k, ())) | carry
            for sid in starts.get(k, ()):
                todo.add(sid)
            for i, te in prof_end.items():
                if prof_start[i] <= t < te:
                    todo.add(i)
            # senders that must see an impaired / blocked receiver
            extra_todo = set()
            for q in list(todo):
                extra_todo |= self.senders_with_A[q]
                if accept[q] < 1.0 or loss_at(q, t)[0] > 0:
                    for lg in self.in_prim[q]:
                        extra_todo.add(self.leg_p[lg])
            todo |= extra_todo
            if not todo:
                if sched:
                    k = min(sched)
                    continue
                if t >= t_last:
                    break
                k += 1
                continue
            audit["steps"] += 1
            order = sorted(todo)
            wfc: dict = {}

            def wf(j):
                c = wfc.get(j)
                if c is None:
                    lj, tend = loss_at(j, t)
                    mu_j = mu_at(j, lj)
                    blj = max(0.0, Z[j] - S0[j])
                    if mu_j > 1e-12:
                        # a tiered place: the class that waits (unbooked) is served at the
                        # residual rate; held exogenous traffic is part of the backlog
                        c = (blj + Xh[j]) / (mu_j - min(bk_rate[j], tier_j[j] * mu_j)
                                             if tier_j[j] > 0.0 and 0.0 < lj < 1.0 and not fifo_j[j] else mu_j)
                    else:
                        c = max(0.0, tend - t) + (blj / mumax[j] if mumax[j] > 0 else 0.0)
                    c = quant(j, c)
                    wfc[j] = c
                return c

            # ---------------- phase 1: read frozen state, decide -------
            P1: dict = {}
            reqs: dict = {}           # y -> list of (key, lane/leg, x_req)
            for i in order:
                lam = lam0[i]
                dev = 0.0
                devs = []
                for lg in self.in_prim[i]:
                    v = pipe_phi[lg].pop(k, 0.0)
                    if v:
                        dev += self.V[lg] * v
                        devs.append((lg, v))
                extra = 0.0
                extra_l = []
                for ln in self.lanes_into[i]:
                    v = pipe_x[ln].pop(k, 0.0)
                    if v:
                        extra += v
                        extra_l.append((ln, v))
                shx = 0.0
                shx_l = []
                for lg2, pdict in pipe_shift.items():
                    if self.leg_q[lg2] == i:
                        v = pdict.pop(k, 0.0)
                        if v:
                            shx += v
                            shx_l.append((lg2, v))
                dev -= exo_rate[i]          # exogenous ships that already turned back
                alpha_base = lam + dev
                if alpha_base < 0.0:
                    alpha_base = 0.0
                exo_held = 0.0
                exo_rel = 0.0
                if spill_j[i]:
                    # exogenous through-traffic is refused like modelled legs when the place is
                    # full (acceptance < 1); what is refused waits outside (Xh) and is released
                    # into the spare service rate once the place accepts again
                    exo_eff = max(0.0, self.exo_base[i] - exo_rate[i])
                    exo_held = min((1.0 - accept[i]) * exo_eff, alpha_base)
                    if accept[i] >= 1.0 and Xh[i] > EPS:
                        exo_rel = min(Xh[i] / dt, max(0.0, mumax[i] - alpha_base))
                    alpha_base = alpha_base - exo_held + exo_rel
                alpha = alpha_base + extra + shx
                Zn = Z[i] + alpha * dt
                Zxn = Zx[i] + extra * dt + shx * dt
                lossi, _ = loss_at(i, t)
                mu = mu_at(i, lossi)
                tol = EPS * max(1.0, lam)
                bl = Zn - S0[i]
                if bl > tol:
                    d = min(mu, Zn / dt)
                else:
                    lam_d = lam
                    if self.p_cc[i] > 0.0:
                        # rotation recovery: the part of the inbound deviation carried by continuing
                        # vessels reaches the next calls (a deficit less what schedule slack and
                        # speed-up recover, a surplus in full); cover does not hide it
                        devc = 0.0
                        for (lgc, vc) in devs:
                            devc += self.p_zeta[lgc] * self.V[lgc] * vc
                        lam_d = max(0.0, lam + (devc if devc >= 0.0 else (1.0 - rho_a) * devc))
                    d = min(mu, lam_d, Zn / dt)
                d = max(0.0, d)
                frac_x = (Zxn / Zn) if Zn > 0 else 0.0
                dx = min(d * frac_x, Zxn / dt)
                d_base = max(0.0, d - dx)
                r = d_base / lam if lam > 0 else 0.0
                rec = {"exo_held": exo_held, "exo_rel": exo_rel,
                       "alpha": alpha, "alpha_base": alpha_base, "extra": extra, "shx": shx,
                       "dev": dev, "devs": devs, "extra_l": extra_l, "shx_l": shx_l,
                       "Zn": Zn, "Zxn": Zxn, "mu": mu, "d": d, "dx": dx,
                       "d_base": d_base, "r": r, "bl": bl, "loss": lossi, "tol": tol}
                # decisions on outbound primary legs
                legdec = []
                for lg in self.out_prim[i]:
                    q = self.leg_q[lg]
                    vq = self.V[lg] * r
                    x_req = 0.0
                    ln_used = None
                    if vq > 0.0:
                        for ln in self.lanesA_of_leg[lg]:
                            y = self.lane_y[ln]
                            if wf(q) - (self.lane_dtau[ln] + wf(y)) > dust:
                                ln_used = ln
                                x_req = (vq * self.g(q, y)
                                         * div_frac(wf(q) - (self.lane_dtau[ln] + wf(y))) * ub_share[q])
                                reqs.setdefault(y, []).append((ln, lg, x_req))
                                break
                    legdec.append((lg, q, vq, ln_used, x_req))
                rec["legdec"] = legdec
                # exogenous through-traffic diverts like its modelled legs
                exo_req = None
                if self.exo_lanes[i] and self.exo_base[i] > 0.0:
                    for ln in self.exo_lanes[i]:
                        y = self.lane_y[ln]
                        if wf(i) - (self.lane_dtau[ln] + wf(y)) > dust:
                            x = (self.exo_base[i] * self.g(i, y)
                                 * div_frac(wf(i) - (self.lane_dtau[ln] + wf(y))) * ub_share[i])
                            reqs.setdefault(y, []).append((("E", i), ln, x))
                            exo_req = (ln, x)
                            break
                rec["exo_req"] = exo_req
                # mode-shift request
                sh = []
                if self.shift_leg_of_node[i] and bl > tol and mu > 0 and (bl / mu) >= self.shift_trigger:
                    for lg in self.shift_leg_of_node[i]:
                        qa = self.leg_q[lg]
                        want_t = self.shift_frac * alpha * self.psi * self.tpt
                        reqs.setdefault(qa, []).append((("S", lg), lg, want_t))
                        sh.append((lg, qa, want_t))
                rec["shift"] = sh
                P1[i] = rec

            # ---------------- phase 2: resolve headroom ----------------
            granted: dict = {}
            for y, lst in reqs.items():
                head = max(0.0, mumax[y] - lam0[y])
                tot = sum(x for _, _, x in lst)
                scale = 1.0 if tot <= head or tot <= 0 else head / tot
                for key, lg, x in lst:
                    granted[key] = x * scale

            carry_next: set = set()
            for i in order:
                rec = P1[i]
                lam = lam0[i]
                r = rec["r"]
                d = rec["d"]
                held_total = 0.0
                legout = []
                for (lg, q, vq, ln_used, x_req) in rec["legdec"]:
                    theta = 0.0
                    xg = 0.0
                    if ln_used is not None:
                        xg = granted.get(ln_used, 0.0)
                        gqy = self.g(q, self.lane_y[ln_used])
                        theta = min(1.0, xg / (vq * gqy)) if vq * gqy > 0 else 0.0
                    a_q = accept[q]
                    phi_sent = r * (1.0 - theta) * a_q
                    held = self.leg_share[lg] * rec["d_base"] * (1.0 - theta) * (1.0 - a_q)
                    held_total += held
                    legout.append((lg, q, phi_sent, held, ln_used, xg, theta))
                # forwarding of diverted cargo on bypass B lanes
                fwd = []
                dx = rec["dx"]
                if dx > 0 and self.lanesB_of_node[i]:
                    needs = []
                    for ln in self.lanesB_of_node[i]:
                        lg = self.lane_leg[ln]
                        q = self.leg_q[lg]
                        need = self.V[lg] * max(0.0, 1.0 - phi_last[lg]) * self.g(q, i)
                        needs.append((ln, lg, q, need))
                    tneed = sum(nd for *_, nd in needs)
                    if tneed > 0:
                        sc = 1.0 if tneed <= dx else dx / tneed
                        for ln, lg, q, need in needs:
                            f_amt = need * sc
                            if f_amt > 0:
                                fwd.append((ln, lg, q, f_amt))
                shifted_i = 0.0
                shift_out = []
                for (lg, qa, want_t) in rec["shift"]:
                    xg = granted.get(("S", lg), 0.0)
                    if xg > 0:
                        shifted_i += xg / (self.psi * self.tpt)
                        shift_out.append((lg, qa, xg))
                served_stock = (rec["d"] - held_total + shifted_i) * dt
                Zn_new = rec["Zn"] - served_stock
                if Zn_new < 0:
                    Zn_new = 0.0
                Zxn_new = max(0.0, rec["Zxn"] - dx * dt)
                if Zxn_new > Zn_new:
                    Zxn_new = Zn_new
                bl_new = Zn_new - S0[i]
                tol = rec["tol"]
                rec.update(held_total=held_total, legout=legout, fwd=fwd, shifted_i=shifted_i,
                           shift_out=shift_out, Zn_new=Zn_new, Zxn_new=Zxn_new, bl_new=bl_new)

                # ---- commit state ----------------------------------
                audit["arrivals"] += rec["alpha"] * dt
                audit["departed"] += (rec["d"] - held_total) * dt
                audit["shifted_out"] += shifted_i * dt
                audit["delta_stock"] += Zn_new - Z[i]
                Z[i] = Zn_new
                Zx[i] = Zxn_new
                mu = rec["mu"]
                if bl_new <= qcap[i]:
                    accept[i] = 1.0
                else:
                    accept[i] = min(1.0, mu / max(rec["alpha"] + rec["exo_held"], 1e-12))
                if spill_j[i]:
                    Xh[i] = max(0.0, Xh[i] + (rec["exo_held"] - rec["exo_rel"]) * dt)
                    audit["exo_held"] += rec["exo_held"] * dt
                    audit["exo_released"] += rec["exo_rel"] * dt
                bkr = 0.0
                bk_rate[i] = 0.0
                ub_share[i] = 1.0
                if tier_j[i] > 0.0 and 0.0 < rec["loss"] < 1.0:
                    # slot regime (a partial capacity cut: slots are ordered down, bookings cannot exceed
                    # them): booked arrivals are served at once, the unbooked wait for the residual.
                    # A full closure or an open place has no slot regime and is plain FIFO.
                    bkr = tier_j[i] * min(rec["alpha"], mu)
                    bk_rate[i] = bkr
                    ub_share[i] = (rec["alpha"] - bkr) / rec["alpha"] if rec["alpha"] > 0.0 else 1.0
                # pipelines out
                for (lg, q, phi_sent, held, ln_used, xg, theta) in legout:
                    phi_last[lg] = phi_sent
                    val = phi_sent - 1.0
                    ka = k + lag_leg[lg]
                    if abs(val) > 1e-12:
                        pipe_phi[lg][ka] = pipe_phi[lg].get(ka, 0.0) + val
                        sched.setdefault(ka, set()).add(q)
                    if ln_used is not None:
                        y = self.lane_y[ln_used]
                        lane_rate[ln_used] = xg
                        if xg > 0:
                            ka2 = k + lag_lane[ln_used]
                            pipe_x[ln_used][ka2] = pipe_x[ln_used].get(ka2, 0.0) + xg
                            sched.setdefault(ka2, set()).add(y)
                if rec["exo_req"] is not None:
                    ln_e, _x = rec["exo_req"]
                    xg_e = granted.get(("E", i), 0.0)
                    y_e = self.lane_y[ln_e]
                    ge = self.g(i, y_e)
                    exo_rate[i] = xg_e / ge if ge > 0 else 0.0
                    if xg_e > 0:
                        ka5 = k + lag_lane[ln_e]
                        pipe_x[ln_e][ka5] = pipe_x[ln_e].get(ka5, 0.0) + xg_e
                        sched.setdefault(ka5, set()).add(y_e)
                else:
                    exo_rate[i] = 0.0
                # lanes A of legs we did not request this step: zero their rates
                for lg in self.out_prim[i]:
                    for ln in self.lanesA_of_leg[lg]:
                        if all(ln != e[4] for e in legout if e[4] is not None):
                            lane_rate[ln] = 0.0
                for (ln, lg, q, f_amt) in fwd:
                    frac = f_amt * self.g(i, q) / self.V[lg] if self.V[lg] > 0 else 0.0
                    ka3 = k + lag_lane[ln]
                    pipe_phi[lg][ka3] = pipe_phi[lg].get(ka3, 0.0) + frac
                    sched.setdefault(ka3, set()).add(q)
                for (lg, qa, xg) in shift_out:
                    ka4 = k + shift_lag[lg]
                    pipe_shift[lg][ka4] = xg
                    sched.setdefault(ka4, set()).add(qa)
                    shift_rate[lg] = xg
                for lg in self.shift_leg_of_node[i]:
                    if all(lg != s[0] for s in shift_out):
                        shift_rate[lg] = 0.0

                # ---- observation --------------------------------------
                bl_pos = max(0.0, bl_new)
                mu_eff = mu if fifo_j[i] else mu - bkr
                bl_w = bl_pos + Xh[i]
                wait_now = (bl_w / mu_eff) if mu_eff > 1e-12 else (float("inf") if bl_w > tol else 0.0)
                wait_now = quant(i, wait_now)
                short = max(0.0, 1.0 - r)
                dev = rec["dev"]
                arr_def = max(0.0, -dev / lam) if lam > 0 else 0.0
                surge = max(0.0, dev / lam) if lam > 0 else 0.0
                extra_share = (rec["extra"] / lam) if lam > 0 else 0.0
                shift_share = (rec["shx"] / lam) if lam > 0 else 0.0
                held_share = (held_total / lam) if lam > 0 else 0.0
                pk = peak[i]
                nodeseen[i] = True
                if pk["first_step"] is None:
                    pk["first_step"] = k
                pk["short"] = max(pk["short"], short)
                if short >= thr_of(i, k):
                    pk["gap_run"] += 1
                    if pk["gap_run"] > pk["gap"]:
                        pk["gap"] = pk["gap_run"]
                else:
                    pk["gap_run"] = 0
                pk["dcum"] += (lam - rec["alpha"]) * dt        # cumulative arrival deficit
                pk["dmax"] = max(pk["dmax"], pk["dcum"])
                pk["arr_def"] = max(pk["arr_def"], arr_def)
                if arr_def >= thr_of(i, k):
                    pk["adef"] += 1
                pk["surge"] = max(pk["surge"], surge)
                pk["extra"] = max(pk["extra"], extra_share)
                pk["shift"] = max(pk["shift"], shift_share)
                pk["held"] = max(pk["held"], held_share)
                pk["bl"] = max(pk["bl"], bl_pos)
                if wait_now != float("inf"):
                    pk["wait_now"] = max(pk["wait_now"], wait_now)
                if qcap[i] > 0:
                    pk["qfill"] = max(pk["qfill"], min(1.0, bl_pos / qcap[i]))
                for (ln, v) in rec["extra_l"]:
                    if self.lane_dtau[ln] > pk["dtau"]:
                        pk["dtau"] = self.lane_dtau[ln]
                        pk["rr_lane"] = ln
                if series[i] is None:
                    series[i] = []
                dep_s = rec["d"] - held_total - shifted_i
                if bkr > 0.0 and not fifo_j[i]:
                    dep_s = max(0.0, dep_s - bkr)         # the unbooked class's own departures
                series[i].append((k, bl_pos + Xh[i], dep_s, rec["alpha"]))
                # stock-days above baseline: the queue, plus the cover drawn
                # down WHILE arrivals are below baseline (once supply resumes
                # the pressure ends; a drawn-down buffer is inert, so counting
                # it would make the indicator depend on which places the
                # solver happens to touch)
                E[i] += (bl_pos + (max(0.0, S0[i] - Zn_new) if dev < -1e-12 else 0.0)) * dt

                # cause accumulation (weights are shares of lambda0)
                cz = cause[i]
                for (lg, v) in rec["devs"]:
                    p = self.leg_p[lg]
                    kind = "deficit" if v < 0 else "surge"
                    slot = cz.setdefault(p, {})
                    slot[kind] = slot.get(kind, 0.0) + (self.V[lg] * abs(v) / lam if lam > 0 else 0.0) * dt
                for (ln, v) in rec["extra_l"]:
                    lg = self.lane_leg[ln]
                    q = self.leg_q[lg]
                    slot = cz.setdefault(q, {})
                    slot["reroute"] = slot.get("reroute", 0.0) + (v / lam if lam > 0 else 0.0) * dt
                for (lg2, v) in rec["shx_l"]:
                    p = self.leg_p[lg2]
                    slot = cz.setdefault(p, {})
                    slot["shift"] = slot.get("shift", 0.0) + (v / lam if lam > 0 else 0.0) * dt
                for (lg, q, phi_sent, held, ln_used, xg, theta) in legout:
                    if held > 0:
                        slot = cz.setdefault(q, {})
                        slot["block"] = slot.get("block", 0.0) + (held / lam if lam > 0 else 0.0) * dt

                # carry
                # ``dust`` is 0 for the default engine (exact comparisons). Profile d adds
                # substitution lanes that can pass floating-point dust around a loop of places
                # forever, so there a flow below EPS is no work (registry: NUMERICAL, time_step_d class).
                need = (bl_pos > tol or Zx[i] > tol or Xh[i] > tol or abs(r - 1.0) > 1e-9 or held_total > dust
                        or shifted_i > dust or accept[i] < 1.0 or abs(dev) > 1e-12
                        or abs(rec["extra"]) > dust or abs(rec["shx"]) > dust or t < prof_end.get(i, 0.0)
                        or exo_rate[i] > dust
                        or any(lane_rate[ln] > dust for lg in self.out_prim[i]
                               for ln in self.lanesA_of_leg[lg])
                        or any(shift_rate.get(lg, 0.0) > dust for lg in self.shift_leg_of_node[i]))
                if need:
                    carry_next.add(i)
                rec.update(short=short, arr_def=arr_def, surge=surge, extra_share=extra_share,
                           shift_share=shift_share, held_share=held_share, wait_now=wait_now,
                           bl_pos=bl_pos)

            carry = carry_next
            # nodes that regained acceptance < 1 force their senders next step (handled above)

            # ---------------- phase 3: hit detection --------------------
            hit_before = dict(hit_step)         # hits at steps < k (or root)
            new_hits = []
            for i in order:
                if i == root or i in hit_step:
                    continue
                rec = P1[i]
                thr = thr_of(i, k)
                lam = lam0[i]
                tol = rec["tol"]
                bl_pos = rec["bl_pos"]
                wait_now = rec["wait_now"]
                chan = None
                R3 = (rec["extra_share"] >= thr) or (
                    rec["extra_share"] >= MIN_MATERIAL
                    and any(self.lane_dtau[ln] >= DELAY_FLOOR_D for ln, _ in rec["extra_l"]))
                R4 = rec["shift_share"] >= MIN_MATERIAL
                R2 = (bl_pos > tol and wait_now >= DELAY_FLOOR_D
                      and bl_pos >= MIN_MATERIAL * lam)
                R1 = rec["short"] >= thr
                if R3 and rec["extra_l"]:
                    chan = "reroute"
                elif R4:
                    chan = "mode-shift"
                elif R2:
                    chan = "queue" if qcap[i] > 0 else "congestion"
                elif R1:
                    chan = "inventory"
                if chan is None:
                    continue
                best = None
                for pnode, kinds in sorted(cause[i].items()):
                    if pnode in hit_before and hit_before[pnode] < k:
                        tot = sum(kinds.values())
                        if best is None or tot > best[0] + 1e-15:
                            best = (tot, pnode, kinds)
                if best is None:
                    continue
                _, pnode, kinds = best
                kind = sorted(kinds.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
                new_hits.append((i, chan, pnode, kind))
            for (i, chan, pnode, kind) in new_hits:
                hit_step[i] = k
                hit_info[i] = {"step": k, "channel": chan, "parent": pnode, "cause": kind}
                n_events += 1
            if n_events > const("max_events"):
                raise EngineError("runaway cascade: quiescence bound exceeded", code="runaway")

            # ---------------- cost leaves --------------------------------
            for i in order:
                if i == root or i in leaf_done:
                    continue
                c = E[i] / self.cost_base[i] if lam0[i] > 0 else 0.0
                if c < MIN_MATERIAL:
                    continue
                if i in hit_step:
                    ps = hit_step[i]
                    leaves.append({"node": i, "step": max(k, ps + 1), "parent": i,
                                   "cause": "self"})
                    leaf_done.add(i)
                    n_events += 1
                    continue
                best = None
                for pnode, kinds in sorted(cause[i].items()):
                    if pnode in hit_before and hit_before[pnode] < k:
                        tot = sum(kinds.values())
                        if best is None or tot > best[0] + 1e-15:
                            best = (tot, pnode)
                if best is not None:
                    leaves.append({"node": i, "step": k, "parent": best[1], "cause": "parent"})
                    leaf_done.add(i)
                    n_events += 1
            if n_events > const("max_events"):
                raise EngineError("runaway cascade: quiescence bound exceeded", code="runaway")

            # quiescence: after every impairment window and no work left
            if t >= t_last and not carry and not sched:
                break
            k += 1

        sim = _Sim()
        sim.root = root
        sim.hit_step = hit_step
        sim.hit_info = hit_info
        sim.leaves = leaves
        sim.peak = peak
        sim.series = series
        sim.E = E
        sim.cause = cause
        sim.dt = dt
        sim.profiles = profiles
        sim.audit = audit
        sim.nodeseen = nodeseen
        sim.k_end = k
        sim.thr_default = thr_default
        sim.params = params
        sim.Zend = Z
        sim.Xend = Xh
        return sim

    # ------------------------------------------------------------ FIFO wait
    @staticmethod
    def _fifo_peak_wait(series: list, dt: float, k_end: int) -> float:
        waits = Engine2._fifo_waits(series, dt)
        return max((w for _, w in waits), default=0.0)

    @staticmethod
    def _fifo_waits(series: list, dt: float) -> list:
        """FIFO wait of the units arriving in each recorded step, on the
        cumulative curves (Newell): a unit arriving at step j with backlog
        bl_j ahead leaves when the cumulative EFFECTIVE departures after j
        reach bl_j. Steps not recorded have no backlog. Returns
        [(step, wait_d)] for steps with a backlog."""
        out = []
        if not series:
            return out
        steps = [s[0] for s in series]
        bl = [s[1] for s in series]
        dd = [s[2] for s in series]
        cum = []
        c = 0.0
        for j in range(len(steps)):
            c += dd[j] * dt
            cum.append(c)
        for j in range(len(steps)):
            if bl[j] <= 1e-12:
                continue
            target = cum[j] + bl[j]
            idx = bisect.bisect_left(cum, target, lo=j + 1)
            if idx < len(cum):
                prev = cum[idx - 1] if idx - 1 >= 0 else 0.0
                span = cum[idx] - prev
                frac = (target - prev) / span if span > 0 else 1.0
                w = (steps[idx] - steps[j] - 1 + frac) * dt
            else:
                # censored at the end of the run: remaining backlog served at
                # the last recorded departure rate
                rem = target - cum[-1]
                last_d = dd[-1] if dd[-1] > 0 else 1e-12
                w = ((steps[-1] - steps[j]) * dt) + rem / last_d
            out.append((steps[j], w))
        return out

    # ------------------------------------------------------------- emission
    def _emit(self, run_id, spec, params, base_dt, dt, sim, ivs, prefix):
        ids = self.ids
        qs = params["queue_sensitivity"]
        root = sim.root
        wait_peak = {}

        def wpeak(i):
            if i not in wait_peak:
                wait_peak[i] = self._quant(i, self._fifo_peak_wait(sim.series[i] or [], dt, sim.k_end))
            return wait_peak[i]

        core = self.core_flag

        def core_anc(p):
            """tier wall: the nearest CORE place on a hit chain (the place itself if CORE)"""
            while not core[p]:
                p = sim.hit_info[p]["parent"]
            return p

        raw = []
        for i, info in sim.hit_info.items():
            if core[i]:
                raw.append({"step": info["step"], "order": 0, "node": i, "kind": "hit", "info": info})
        for lf in sim.leaves:
            if core[lf["node"]]:
                raw.append({"step": lf["step"], "order": 1, "node": lf["node"], "kind": "leaf", "info": lf})
        raw.sort(key=lambda r: (r["step"], r["order"], ids[r["node"]]))
        seq_of_hit: dict[int, int] = {}
        tick_of_seq: dict[int, int] = {}
        events = []
        impacts = {}
        for r in raw:
            i = r["node"]
            info = r["info"]
            seq = len(events)
            step = r["step"]
            pk = sim.peak[i]
            if r["kind"] == "hit":
                if i == root:
                    eff_loss = max((p[1] for p in sim.profiles.get(root, [(0, spec["capacity_loss"], 0, "")])),
                                   default=spec["capacity_loss"])
                    chan = "congestion"
                    parent = None
                    mech = MECH["root"]
                    delay = wpeak(i) * qs
                    mag = eff_loss
                    tick = 0
                else:
                    chan = info["channel"]
                    parent_node = core_anc(info["parent"])
                    parent = seq_of_hit[parent_node]
                    tick = tick_of_seq[parent] + 1
                    delay_core = wpeak(i)
                    if chan == "queue" or chan == "congestion":
                        cause = info["cause"]
                        if chan == "congestion":
                            mech = MECH["congestion"]
                        elif cause == "block":
                            mech = MECH["queue_block"]
                        elif cause == "surge":
                            mech = MECH["queue_surge"]
                        else:
                            mech = MECH["queue_deficit"]
                        delay = delay_core * qs
                        mag = pk["qfill"] if (chan == "queue" and qcap_pos(self, i)) else max(pk["short"], pk["held"])
                    elif chan == "reroute":
                        ln = pk["rr_lane"]
                        mech = MECH["reroute"].format(
                            lane=self.lane_key[ln],
                            primary=self.leg_key[self.lane_leg[ln]])
                        delay = pk["dtau"] + delay_core * qs
                        mag = pk["extra"]
                    elif chan == "mode-shift":
                        mech = MECH["mode-shift"]
                        delay = delay_core * qs
                        mag = pk["shift"]
                    else:   # inventory
                        mech = MECH["inventory"]
                        # supply gap: how long onward flow stayed cut by >= the
                        # hit threshold (that cargo arrives late by about that long)
                        if self.cal is not None:
                            # rev2c: lateness = peak cumulative arrival deficit in days
                            late = pk["dmax"] / self.lam0[i] if self.lam0[i] > 0 else 0.0
                            delay = late + delay_core * qs
                        else:
                            delay = pk["gap"] * dt + delay_core * qs
                        mag = pk["short"]
                seq_of_hit[i] = seq
                tick_of_seq[seq] = tick
            else:   # cost leaf
                chan = "cost"
                if info["cause"] == "self":
                    parent = seq_of_hit[i]
                else:
                    parent = seq_of_hit[core_anc(info["parent"])]
                tick = tick_of_seq[parent] + 1
                tref = self.cost_ref_d
                c = sim.E[i] / self.cost_base[i] if self.lam0[i] > 0 else 0.0
                mag = min(1.0, c)
                mech = MECH["cost"]
                delay = 0.0
                tick_of_seq[seq] = tick
            ev = {"run_id": run_id, "seq": seq, "node_id": ids[i], "parent_id": parent,
                  "channel": chan, "mechanism": mech, "delay_d": _r6(delay),
                  "magnitude": _r6(min(1.0, max(0.0, mag))),
                  "at": _fmt_ts(base_dt + timedelta(days=step * dt)), "tick": tick}
            events.append(ev)
            impacts[seq] = {"node_id": ids[i], "s": ev["magnitude"],
                            "duration_d": spec["duration_d"],
                            "total_delay_d": ev["delay_d"], "step": step}
        state = RunState2(events=events, impacts=impacts)
        if self.cal is not None:
            state.engine_label = PROFILE_LABEL
            state.constants_hash = CAL_HASH
        if self.prof is not None:
            state.engine_label = getattr(self.prof, "label", D_LABEL)
            state.constants_hash = getattr(self.prof, "hash", D_HASH)
        state.hit = dict((ids[i], s) for i, s in seq_of_hit.items())
        state.emitted = {(e["node_id"], e["channel"]) for e in events}
        state.interventions = ivs
        state.dt = dt
        eff = sim.profiles.get(root, [(0.0, spec["capacity_loss"], spec["duration_d"], spec["shape"])])[-1]
        state.eff_root = {"capacity_loss": eff[1], "duration_d": eff[2], "shape": eff[3]}
        state.audit = {kk: _r6(v) if isinstance(v, float) else v for kk, v in sim.audit.items()}
        state.not_hit = self._derive_not_hit(sim, params, seq_of_hit)
        if prefix is not None:
            self._apply_prefix(state, prefix, run_id)
        return state

    @staticmethod
    def _apply_prefix(state: RunState2, prefix: dict, run_id: str) -> None:
        """Fork: the shared prefix is the parent's recorded events verbatim
        (re-stamped run_id); the continuation is this run's."""
        pev = prefix["events"]
        pimp = prefix["impacts"]
        n = len(pev)
        if len(state.events) < n:
            raise EngineError("internal: fork continuation shorter than prefix", code="runaway")
        for j in range(n):
            a, b = state.events[j], pev[j]
            if (a["node_id"], a["channel"], a["parent_id"], a["tick"], a["at"]) != \
                    (b["node_id"], b["channel"], b["parent_id"], b["tick"], b["at"]):
                raise EngineError(
                    f"internal: fork prefix structure diverged at seq {j}", code="runaway")
            nev = dict(b)
            nev["run_id"] = run_id
            state.events[j] = nev
            state.impacts[j] = dict(pimp[j])
        state.emitted = {(e["node_id"], e["channel"]) for e in state.events}

    # ------------------------------------------------------------- WHYNOT
    def _derive_not_hit(self, sim, params, seq_of_hit) -> list:
        ids = self.ids
        G = self.graph
        hit_ids = set(seq_of_hit)
        event_nodes = set(hit_ids) | {lf["node"] for lf in sim.leaves}
        core = self.core_flag
        out = {}
        for i in range(len(ids)):
            if i in event_nodes or not sim.nodeseen[i] or not core[i]:
                continue
            pk = sim.peak[i]
            cz = sim.cause[i]
            via = None
            bestv = -1.0
            for pnode, kinds in sorted(cz.items()):
                if pnode in hit_ids:
                    tot = sum(kinds.values())
                    if tot > bestv:
                        bestv, via = tot, pnode
            if via is None:
                continue
            thr = sim.thr_default[i]
            material = max(pk["arr_def"], pk["short"], pk["extra"], pk["surge"], pk["held"])
            if material <= 0 and pk["wait_now"] <= 0:
                reason = "NO_ROUTE"
                fig = {"via_place": ids[via], "inbound_share": 0.0}
            elif pk["arr_def"] >= thr and pk["short"] < thr:
                reason = "ABSORBED"
                # cover absorbed it: shortfall-days (peak share x days at or
                # above the threshold) fit inside the cover days
                fig = {"via_place": ids[via], "inbound_share": _r6(pk["arr_def"]),
                       "shortfall_days": _r6(pk["arr_def"] * pk["adef"] * sim.dt),
                       "inventory_days": _r6(self.S0[i] / self.lam0[i] if self.lam0[i] else 0.0)}
            else:
                reason = "BELOW_THRESHOLD"
                chan = ("reroute" if pk["extra"] > 0 else
                        "queue" if pk["wait_now"] > 0 else "inventory")
                fig = {"via_place": ids[via], "inbound_share": _r6(max(pk["arr_def"], pk["extra"], pk["surge"])),
                       "hit_threshold": _r6(thr), "raw_delay_d": _r6(pk["wait_now"]),
                       "delay_floor_d": DELAY_FLOOR_D, "channel": chan}
            out[ids[i]] = {"place_id": ids[i], "reason": reason, "figures": fig}
        for h in sorted(hit_ids):
            for e in self.graph.outbound(ids[h]) + self.graph.inbound(ids[h]):
                other = e["to"] if e["from"] == ids[h] else e["from"]
                oi = self.ix[other]
                if oi in event_nodes or other in out or not core[oi]:
                    continue
                out[other] = {"place_id": other, "reason": "NO_ROUTE",
                              "figures": {"via_place": ids[h], "inbound_share": 0.0}}
        return [out[k] for k in sorted(out)]


def qcap_pos(eng: Engine2, i: int) -> bool:
    return eng.qcap[i] > 0


def run_scenario2(graph: Graph, run_id: str, disruption: dict,
                  params: dict | None = None, node_overrides: dict | None = None) -> RunState2:
    return Engine2(graph).run(run_id, disruption, params, node_overrides)
