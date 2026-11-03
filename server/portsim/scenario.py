"""What-if scenarios: strict validation (refuse with a reason, never clamp),
canonical form (the journal), and compilation to piecewise-constant
control functions of time.

Scenario items (all times in hours from the start of the run, t = 0 being
the end of the warm-up; a window must lie inside the run horizon):

  surge          {factor, start_h, duration_h}         arrivals x factor
  berth_closure  {group, berths|"all", start_h, duration_h}
  crane_scale    {factor, groups?, start_h?, duration_h?}   productivity x factor
  strike         {factor, groups?, start_h, duration_h}     service rate x factor (0 <= factor < 1)
  channel_closure{start_h, duration_h}                 no movements start
  yard_outage    {factor, start_h, duration_h}         yard evacuation x factor (0 <= factor < 1)
  chain_path     {step_h, arrival_mult?, capacity_mult?}    per-step multipliers
                 from the chain engine (see chain.py)

A scenario is a list of items. The canonical form sorts the items, so the
same set always gives the same run id.
"""
from __future__ import annotations

import bisect
import math

from .classes import PortSimError
from .registry import canonical

TYPES = ("surge", "berth_closure", "crane_scale", "strike", "channel_closure",
         "yard_outage", "chain_path")

KEYS = {
    "surge": {"factor", "start_h", "duration_h"},
    "berth_closure": {"group", "berths", "start_h", "duration_h"},
    "crane_scale": {"factor", "groups", "start_h", "duration_h"},
    "strike": {"factor", "groups", "start_h", "duration_h"},
    "channel_closure": {"start_h", "duration_h"},
    "yard_outage": {"factor", "start_h", "duration_h"},
    "chain_path": {"step_h", "arrival_mult", "capacity_mult", "evac_mult", "source"},
}
REQUIRED = {
    "surge": {"factor", "start_h", "duration_h"},
    "berth_closure": {"group", "berths", "start_h", "duration_h"},
    "crane_scale": {"factor"},
    "strike": {"factor", "start_h", "duration_h"},
    "channel_closure": {"start_h", "duration_h"},
    "yard_outage": {"factor", "start_h", "duration_h"},
    "chain_path": {"step_h"},
}
MAX_ITEMS = 16
PATH_MAX = 4000


def bad(i, msg):
    raise PortSimError("invalid_scenario", f"scenario[{i}]: {msg}")


def _num(v, i, name):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        bad(i, f"{name} must be a number, got {type(v).__name__}")
    if not math.isfinite(v):
        bad(i, f"{name} must be finite")
    return float(v)


def _rng(v, i, name, lo, hi, lo_open=False, hi_open=False):
    x = _num(v, i, name)
    ok_lo = x > lo if lo_open else x >= lo
    ok_hi = x < hi if hi_open else x <= hi
    if not (ok_lo and ok_hi):
        bad(i, f"{name}={x:g} is outside "
               f"{'(' if lo_open else '['}{lo:g}, {hi:g}{')' if hi_open else ']'}; "
               "refused, not clamped")
    return x


def _window(it, i, horizon_h, optional=False):
    if optional and "start_h" not in it and "duration_h" not in it:
        return None
    if "start_h" not in it or "duration_h" not in it:
        bad(i, "start_h and duration_h go together")
    s = _rng(it["start_h"], i, "start_h", 0.0, horizon_h, hi_open=True)
    d = _rng(it["duration_h"], i, "duration_h", 0.0, horizon_h, lo_open=True)
    if s + d > horizon_h + 1e-9:
        bad(i, f"window {s:g}h + {d:g}h ends after the run horizon "
               f"({horizon_h:g}h); shorten it or lengthen `days` "
               "(refused, not clamped)")
    return round(s, 6), round(d, 6)


def validate(raw, spec, days: int):
    """Return the canonical scenario (list of dicts) or raise PortSimError."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise PortSimError("invalid_scenario", "scenario must be a list of items")
    if len(raw) > MAX_ITEMS:
        raise PortSimError("invalid_scenario",
                           f"at most {MAX_ITEMS} scenario items (got {len(raw)})")
    horizon_h = days * 24.0
    group_ids = [g.id for g in spec.groups]
    out = []
    for i, it in enumerate(raw):
        if not isinstance(it, dict):
            bad(i, "must be an object")
        t = it.get("type")
        if t not in TYPES:
            bad(i, f"unknown type {t!r}; one of {list(TYPES)}")
        extra = set(it) - KEYS[t] - {"type"}
        if extra:
            bad(i, f"unknown key(s) {sorted(extra)} for type {t}; allowed "
                   f"{sorted(KEYS[t])}")
        miss = REQUIRED[t] - set(it)
        if miss:
            bad(i, f"missing {sorted(miss)} for type {t}")
        c = {"type": t}
        if t == "surge":
            c["factor"] = _rng(it["factor"], i, "factor", 0.1, 5.0)
            c["start_h"], c["duration_h"] = _window(it, i, horizon_h)
        elif t == "berth_closure":
            g = it["group"]
            if g not in group_ids:
                bad(i, f"unknown berth group {g!r}; this place has {group_ids}")
            n_all = spec.groups[group_ids.index(g)].berths
            b = it["berths"]
            if b == "all":
                nb = n_all
            else:
                if isinstance(b, bool) or not isinstance(b, int):
                    bad(i, "berths must be an integer or \"all\"")
                if not (1 <= b <= n_all):
                    bad(i, f"berths={b} is outside 1..{n_all} (group {g} has "
                           f"{n_all} berths); refused, not clamped")
                nb = b
            c["group"], c["berths"] = g, nb
            c["start_h"], c["duration_h"] = _window(it, i, horizon_h)
        elif t in ("crane_scale", "strike"):
            if t == "crane_scale":
                c["factor"] = _rng(it["factor"], i, "factor", 0.0, 2.0, lo_open=True)
            else:
                c["factor"] = _rng(it["factor"], i, "factor", 0.0, 1.0, hi_open=True)
            gs = it.get("groups")
            if gs is not None:
                if (not isinstance(gs, list) or not gs
                        or any(not isinstance(x, str) for x in gs)):
                    bad(i, "groups must be a non-empty list of group ids")
                for x in gs:
                    if x not in group_ids:
                        bad(i, f"unknown berth group {x!r}; this place has {group_ids}")
                c["groups"] = sorted(set(gs))
            w = _window(it, i, horizon_h, optional=(t == "crane_scale"))
            if w is not None:
                c["start_h"], c["duration_h"] = w
        elif t == "channel_closure":
            if not spec.channel_present:
                bad(i, "no channel is mapped for this place in OpenStreetMap "
                       "(channel.present is false); that is absence of a map "
                       "feature, not evidence of no channel")
            c["start_h"], c["duration_h"] = _window(it, i, horizon_h)
        elif t == "yard_outage":
            if spec.yard_capacity_teu is None:
                bad(i, "this place has no container yard to take offline")
            c["factor"] = _rng(it["factor"], i, "factor", 0.0, 1.0, hi_open=True)
            c["start_h"], c["duration_h"] = _window(it, i, horizon_h)
        else:  # chain_path
            c["step_h"] = _rng(it["step_h"], i, "step_h", 0.0, 168.0, lo_open=True)
            n_steps = None
            for key, lo, hi in (("arrival_mult", 0.0, 20.0),
                                ("capacity_mult", 0.0, 1.0),
                                ("evac_mult", 0.0, 2.0)):
                if key in it:
                    arr = it[key]
                    if not isinstance(arr, list) or not arr:
                        bad(i, f"{key} must be a non-empty list")
                    if len(arr) > PATH_MAX:
                        bad(i, f"{key} has more than {PATH_MAX} entries")
                    c[key] = [round(_rng(v, i, f"{key}[{j}]", lo, hi), 6)
                              for j, v in enumerate(arr)]
                    if n_steps is not None and len(arr) != n_steps:
                        bad(i, "the multiplier lists must have the same length")
                    n_steps = len(arr)
            if n_steps is None:
                bad(i, "give arrival_mult, capacity_mult and/or evac_mult")
            if n_steps * c["step_h"] > horizon_h + 1e-9:
                bad(i, f"path covers {n_steps * c['step_h']:g}h, longer than "
                       f"the run horizon {horizon_h:g}h; refused, not clamped")
            if "source" in it:
                if not isinstance(it["source"], (str, dict)):
                    bad(i, "source must be a string or object")
                c["source"] = it["source"]
        out.append(c)
    out.sort(key=canonical)
    return out


# --------------------------------------------------------------- step funcs
class StepFn:
    """Piecewise-constant function of integer seconds: base value, with
    windows [a, b) combined by product ('mul') or sum ('add')."""

    def __init__(self, windows, op="mul"):
        base = 1.0 if op == "mul" else 0.0
        pts = sorted({w[0] for w in windows} | {w[1] for w in windows})
        self.ts = pts
        vs = []
        for p in pts:
            v = base
            for a, b, val in windows:
                if a <= p < b:
                    v = v * val if op == "mul" else v + val
            vs.append(v)
        self.vs = vs
        self.base = base

    def at(self, t: int) -> float:
        i = bisect.bisect_right(self.ts, t) - 1
        return self.base if i < 0 else self.vs[i]

    def breaks(self):
        return list(self.ts)


class Controls:
    def __init__(self):
        self.arr = StepFn([])
        self.svc_all = StepFn([])
        self.svc_group: dict = {}
        self.evac = StepFn([])
        self.channel_windows: list = []
        self.closed: dict = {}
        self._breaks: set = set()

    def breaks(self):
        return sorted(self._breaks)

    def channel_open(self, t: int) -> bool:
        for a, b in self.channel_windows:
            if a <= t < b:
                return False
        return True

    def svc(self, gi: int, t: int) -> float:
        f = self.svc_group.get(gi)
        return self.svc_all.at(t) * (f.at(t) if f is not None else 1.0)

    def closed_berths(self, gi: int, t: int) -> int:
        f = self.closed.get(gi)
        return int(round(f.at(t))) if f is not None else 0


def _s(h):
    return int(round(h * 3600.0))


def compile_controls(items, spec) -> Controls:
    C = Controls()
    arr_w, svc_w, evac_w = [], [], []
    grp_w: dict = {}
    clo_w: dict = {}
    gids = [g.id for g in spec.groups]
    for it in items:
        t = it["type"]
        if t == "surge":
            arr_w.append((_s(it["start_h"]), _s(it["start_h"] + it["duration_h"]),
                          it["factor"]))
        elif t == "berth_closure":
            gi = gids.index(it["group"])
            clo_w.setdefault(gi, []).append(
                (_s(it["start_h"]), _s(it["start_h"] + it["duration_h"]),
                 float(it["berths"])))
        elif t in ("crane_scale", "strike"):
            if "start_h" in it:
                a, b = _s(it["start_h"]), _s(it["start_h"] + it["duration_h"])
            else:
                a, b = -10 ** 9, 10 ** 9
            if "groups" in it:
                for g in it["groups"]:
                    grp_w.setdefault(gids.index(g), []).append((a, b, it["factor"]))
            else:
                svc_w.append((a, b, it["factor"]))
        elif t == "channel_closure":
            C.channel_windows.append((_s(it["start_h"]),
                                      _s(it["start_h"] + it["duration_h"])))
        elif t == "yard_outage":
            evac_w.append((_s(it["start_h"]), _s(it["start_h"] + it["duration_h"]),
                           it["factor"]))
        elif t == "chain_path":
            step = _s(it["step_h"])
            for key, tgt in (("arrival_mult", arr_w), ("capacity_mult", svc_w),
                             ("evac_mult", evac_w)):
                for j, v in enumerate(it.get(key, [])):
                    tgt.append((j * step, (j + 1) * step, v))
    C.channel_windows.sort()
    C.arr = StepFn(arr_w)
    C.svc_all = StepFn(svc_w)
    C.svc_group = {gi: StepFn(w) for gi, w in sorted(grp_w.items())}
    C.evac = StepFn(evac_w)
    C.closed = {gi: StepFn(w, "add") for gi, w in sorted(clo_w.items())}
    pts = set(C.arr.breaks()) | set(C.svc_all.breaks()) | set(C.evac.breaks())
    for f in list(C.svc_group.values()) + list(C.closed.values()):
        pts |= set(f.breaks())
    for a, b in C.channel_windows:
        pts |= {a, b}
    C._breaks = {p for p in pts if abs(p) < 10 ** 8}
    return C
