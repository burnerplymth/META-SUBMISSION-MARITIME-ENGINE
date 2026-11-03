"""The deterministic discrete-event core.

Clock: integer seconds (t = 0 is the end of the warm-up; the warm-up runs
from -warmup_days). Events are ordered by (time, phase, serial); all
events of one instant are applied, then berth / anchorage decisions are
taken once from the state at that instant, so heap insertion order cannot
change a result. Rates change only at control events (scenario window
edges and a fixed hourly yard tick), so work progress is exact between
them. No random numbers.

Vessel life cycle:
  approach        outer limit -> anchorage            (movement_times_s.approach)
  waiting         at anchorage (a slot) or 'waiting outside' when the
                  anchorage is full (spillback)
  moving_in       anchorage -> alongside (pilotage through the channel, or
                  mooring); the berth is reserved from the start of this
  working         cargo work: remaining = work - integral rate dt; rate =
                  base x service multipliers x yard throttle
  ready           work done, alongside, waiting for an open channel
  moving_out      alongside -> clear (berth is already free)
Berth choice: queue order by (time at anchorage, serial); the first
waiting call (per size class) that FITS (kind routing, free, open) takes
the first such berth in group/berth order. A vessel longer than one berth
of a group holds as many berths of that group as its length spans
(classes.berths_needed; the free berths with the lowest numbers, no
adjacency or shifting model); all of them are busy until it is clear.
"""
from __future__ import annotations

import heapq
import math
from collections import deque
from dataclasses import dataclass, field

from .arrivals import build_schedule
from .classes import (PortSimError, berths_needed, pilot_in_s, pilot_out_s,
                      size_classes)
from .profile import PortSpec, anchor_scales, route_groups
from .registry import KIND_ORDER, plug
from .scenario import Controls

CTRL, WORK_END, IN_DONE, GONE, REQ = 0, 1, 2, 3, 4
MAX_EVENTS = 4_000_000

STATE_NAMES = ("approach", "waiting_outside", "anchored", "moving_in",
               "working", "ready_to_depart", "moving_out")


class V:
    __slots__ = ("i", "kind", "size", "cls", "loa", "work", "rate_h",
                 "t_arr", "t_req", "t_anch", "slot", "t_move", "t_berth",
                 "t_work_end", "t_dep", "t_gone", "berth", "group",
                 "done", "rate", "t_upd", "ver", "out_live", "tag", "nberths", "held")

    def __init__(self, i, kind, size, cls, loa, work, rate_h, t_arr):
        self.i, self.kind, self.size, self.cls = i, kind, size, cls
        self.loa, self.work, self.rate_h, self.t_arr = loa, work, rate_h, t_arr
        self.t_req = self.t_anch = self.slot = self.t_move = None
        self.t_berth = self.t_work_end = self.t_dep = self.t_gone = None
        self.berth = self.group = None
        self.nberths = 1
        self.held = ()
        self.done = 0.0
        self.rate = 0.0
        self.t_upd = 0
        self.ver = 0
        self.out_live = True

    def row(self):
        return (self.i, self.kind, self.size, self.t_arr, self.t_req,
                self.t_anch, self.t_move, self.t_berth, self.t_work_end,
                self.t_dep, self.t_gone, self.berth)


@dataclass
class RunParams:
    days: int
    warmup_days: int
    variant: str
    arrival_shape: str
    anchor: str
    daily_calls: dict | None = None


@dataclass
class Result:
    spec: PortSpec
    params: RunParams
    t0: int
    t1: int
    vessels: list
    berth_table: list          # [(group_index, index_in_group, length_m)]
    ticks: list                # [(t, yard_stock_teu, fill, throttle)]
    scales: dict
    anchor_report: dict
    oversize: int
    events: int
    class_table: dict = field(default_factory=dict)
    pin_s: int = 0
    pout_s: int = 0


class _Berth:
    __slots__ = ("g", "idx", "gidx", "length", "busy")

    def __init__(self, g, idx, gidx, length):
        self.g, self.idx, self.gidx, self.length, self.busy = g, idx, gidx, length, None


def simulate(spec: PortSpec, params: RunParams, ctl: Controls) -> Result:
    mu_y = plug("yard")
    var = float(plug("service_variants")[params.variant])
    T0 = -params.warmup_days * 86400
    T1 = params.days * 86400
    scales, anchor_rep = anchor_scales(spec, params.anchor)
    pin = pilot_in_s(spec.channel_present)
    pout = pilot_out_s(spec.channel_present)
    approach = int(plug("movement_times_s")["approach"])
    tick = int(mu_y["tick_s"])

    # ---- berths --------------------------------------------------------
    berths_by_g = []
    berth_table = []
    for gi, g in enumerate(spec.groups):
        row = []
        for k in range(g.berths):
            b = _Berth(gi, k, len(berth_table), g.berth_length_m)
            row.append(b)
            berth_table.append((gi, k, round(g.berth_length_m, 3)))
        berths_by_g.append(row)
    routes = {k: route_groups(spec.groups, k) for k in KIND_ORDER
              if spec.calls_per_day.get(k, 0.0) > 0}

    # ---- vessels ---------------------------------------------------------
    classes = {k: size_classes(k, spec.work_per_call[k],
                               spec.loa_scale.get(k, 1.0))
               for k in routes}
    # longest stretch of quay any group the kind may use offers: a call
    # longer than that cannot be fitted even along a whole group (it is
    # accommodated along the whole longest group and counted)
    maxquay = {k: max(spec.groups[gi].berths * spec.groups[gi].berth_length_m
                      for gi in rs) for k, rs in routes.items()}
    sched = build_schedule(spec.calls_per_day, T0, T1, ctl.arr,
                           params.arrival_shape, ctl.breaks(),
                           params.daily_calls)
    vessels = []
    oversize = 0
    for i, (t, kind, s) in enumerate(sched):
        c = classes[kind][s]
        loa = c["loa_m"]
        if loa > maxquay[kind] + 1e-9:
            oversize += 1
        rate_h = c["rate_per_h"] / (scales[kind] * var)
        vessels.append(V(i, kind, s, c["name"], loa, c["work"], rate_h, t))

    # berths a call of each (kind, size class) holds in each group it may use
    need = {(k, si): {gi: berths_needed(c["loa_m"], spec.groups[gi].berth_length_m,
                                        spec.groups[gi].berths)
                      for gi in routes[k]}
            for k, cl in classes.items() for si, c in enumerate(cl)}

    # ---- event machinery -------------------------------------------------
    heap: list = []
    serial = 0

    def push(t, prio, v=None, ver=0):
        nonlocal serial
        serial += 1
        heapq.heappush(heap, (t, prio, serial, v, ver))

    for v in vessels:
        push(v.t_arr + approach, REQ, v)
    ctrl_times = set(range(T0, T1 + 1, tick))
    ctrl_times |= {b for b in ctl.breaks() if T0 <= b <= T1}
    ctrl_times.add(T0)
    for t in sorted(ctrl_times):
        push(t, CTRL)

    # ---- state ------------------------------------------------------------
    queues: dict = {}
    qkeys: list = []
    for k in routes:
        for s in range(len(classes[k])):
            queues[(KIND_ORDER.index(k), s)] = deque()
    qkeys = sorted(queues)
    qblock = {qk: -1 for qk in qkeys}
    epoch = 0
    outside: deque = deque()
    anchored = 0
    free_slots: list = []
    next_slot = 0
    slots = spec.anchorage_slots
    nopen = [g.berths for g in spec.groups]
    working: list = []
    ready: list = []
    ticks: list = []
    n_events = 0

    # yard
    K = spec.yard_capacity_teu
    has_yard = K is not None and K > 0
    dwell_s = mu_y["dwell_days"] * 86400.0
    Y = (mu_y["import_share"] * spec.teu_per_day * mu_y["dwell_days"]) if has_yard else 0.0
    yard_t = T0
    D = 0.0               # TEU/s discharged into the yard
    ymult = 1.0

    def yard_advance(t):
        nonlocal Y, yard_t
        dt = t - yard_t
        if dt <= 0 or not has_yard:
            yard_t = t
            return
        e = ctl.evac.at(yard_t)
        if e <= 0:
            Y = Y + D * dt
        else:
            dw = dwell_s / e
            eq = D * dw
            Y = eq + (Y - eq) * math.exp(-dt / dw)
        Y = round(max(Y, 0.0), 6)
        yard_t = t

    def throttle():
        if not has_yard:
            return 1.0
        f = Y / K
        th, mn = mu_y["threshold"], mu_y["min_throttle"]
        if f <= th:
            return 1.0
        return max(mn, 1.0 - (1.0 - mn) * (f - th) / (1.0 - th))

    def vessel_rate(v, t):
        r = v.rate_h / 3600.0 * ctl.svc(v.group, t)
        if v.kind == "container":
            r *= ymult
        return r

    def recompute_D():
        nonlocal D
        D = mu_y["import_share"] * sum(v.rate for v in working
                                       if v.kind == "container")

    def schedule_end(v, t):
        rem = v.work - v.done
        if rem <= 1e-9:
            push(t, WORK_END, v, v.ver)
        elif v.rate > 0:
            push(t + max(1, int(math.ceil(rem / v.rate))), WORK_END, v, v.ver)

    def retune(t):
        for v in working:
            v.done = min(v.work, v.done + v.rate * (t - v.t_upd))
            v.t_upd = t
            r = vessel_rate(v, t)
            if r != v.rate:
                v.rate = r
                v.ver += 1
                schedule_end(v, t)
        recompute_D()

    def promote(t):
        nonlocal anchored, next_slot
        while anchored < slots:
            v = None
            while outside:
                c = outside.popleft()
                if c.out_live:
                    v = c
                    break
            if v is None:
                break
            v.t_anch = t
            if free_slots:
                v.slot = heapq.heappop(free_slots)
            else:
                v.slot = next_slot
                next_slot += 1
            anchored += 1

    def find_berth(v):
        """First group (in route order) with enough free open berths for
        the vessel -> (the berths it takes, lowest numbers first, count)."""
        for gi in routes[v.kind]:
            row = berths_by_g[gi]
            n = need[(v.kind, v.size)][gi]
            if n == 1:
                for k in range(nopen[gi]):
                    if row[k].busy is None:
                        return [row[k]], 1
                continue
            free = [row[k] for k in range(nopen[gi]) if row[k].busy is None]
            if len(free) >= n:
                return free, n
        return None

    def dispatch(t):
        nonlocal epoch, anchored
        open_ch = ctl.channel_open(t)
        if open_ch and ready:
            for v in ready:
                v.t_dep = t
                for b in v.held:
                    b.busy = None
                epoch += 1
                push(t + pout, GONE, v)
            ready.clear()
        promote(t)
        if open_ch:
            while True:
                best = None
                for qk in qkeys:
                    q = queues[qk]
                    if not q or qblock[qk] == epoch:
                        continue
                    v = q[0]
                    fb = find_berth(v)
                    if fb is None:
                        qblock[qk] = epoch
                        continue
                    key = (v.t_req, v.i)
                    if best is None or key < best[0]:
                        best = (key, qk, v, fb)
                if best is None:
                    break
                _k, qk, v, (fl, nb) = best
                b = fl[0]
                queues[qk].popleft()
                if v.t_anch is not None:
                    anchored -= 1
                    heapq.heappush(free_slots, v.slot)
                else:
                    v.out_live = False
                v.t_move = t
                v.held = fl[:nb]
                for bb in v.held:
                    bb.busy = v
                v.nberths = nb
                v.berth, v.group = b.gidx, b.g
                push(t + pin, IN_DONE, v)
                promote(t)

    berth_first = []
    c0 = 0
    for row in berths_by_g:
        berth_first.append(c0)
        c0 += len(row)

    # ---- main loop ---------------------------------------------------------
    while heap:
        t = heap[0][0]
        if t > T1:
            break
        batch = []
        while heap and heap[0][0] == t:
            batch.append(heapq.heappop(heap))
        n_events += len(batch)
        if n_events > MAX_EVENTS:
            raise PortSimError("runaway", "event bound exceeded", 500)
        for (_t, prio, _s, v, ver) in batch:
            if prio == CTRL:
                yard_advance(t)
                for gi in range(len(spec.groups)):
                    n = spec.groups[gi].berths - min(
                        ctl.closed_berths(gi, t), spec.groups[gi].berths)
                    if n != nopen[gi]:
                        if n > nopen[gi]:
                            epoch += 1
                        nopen[gi] = n
                ymult = throttle()
                retune(t)
                if t % tick == 0:
                    ticks.append((t, Y, (Y / K) if has_yard else None, ymult))
            elif prio == WORK_END:
                if v.ver != ver or v.t_work_end is not None:
                    continue
                yard_advance(t)
                v.done = v.work
                v.t_work_end = t
                working.remove(v)
                ready.append(v)
                recompute_D()
            elif prio == IN_DONE:
                yard_advance(t)
                v.t_berth = t
                v.t_upd = t
                v.done = 0.0
                v.rate = vessel_rate(v, t)
                v.ver += 1
                working.append(v)
                schedule_end(v, t)
                recompute_D()
            elif prio == GONE:
                v.t_gone = t
            else:  # REQ
                v.t_req = t
                queues[(KIND_ORDER.index(v.kind), v.size)].append(v)
                outside.append(v)
        dispatch(t)
    if has_yard:
        yard_advance(T1)
    return Result(spec=spec, params=params, t0=T0, t1=T1, vessels=vessels,
                  berth_table=berth_table, ticks=ticks, scales=scales,
                  anchor_report=anchor_rep, oversize=oversize,
                  events=n_events, pin_s=pin, pout_s=pout,
                  class_table={k: [{kk: vv for kk, vv in c.items()
                                    if kk in ("name", "share", "work", "rate_per_h",
                                              "loa_m", "cranes")}
                                   for c in cl] for k, cl in classes.items()})
