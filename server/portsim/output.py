"""Outputs derived from a Result: KPI summary and hourly timeline, windowed
compact animation frames, comparison, and the result digest.

Everything is computed from the vessel records (timestamps) so it can be
recomputed for any window without re-running the simulation.
"""
from __future__ import annotations

import bisect
import math

from .classes import PortSimError
from .core import STATE_NAMES, Result
from .registry import KIND_ORDER, canonical, plug, sha256

HOUR = 3600
DAY = 86400


def result_digest(res: Result) -> str:
    rows = [v.row() for v in res.vessels]
    ticks = [(t, round(y, 4), None if f is None else round(f, 6), round(m, 6))
             for (t, y, f, m) in res.ticks]
    return sha256(canonical({"rows": rows, "ticks": ticks, "t0": res.t0,
                             "t1": res.t1}))


def _ceil_div(a, b):
    return -((-a) // b)


def _add_interval(diff, a, b, n):
    """Count the sample times s_k = k * HOUR (0 <= k < n) with a <= s_k < b."""
    lo = 0 if a is None else max(0, _ceil_div(a, HOUR))
    hi = n if b is None else min(n, _ceil_div(b, HOUR))
    if lo < hi:
        diff[lo] += 1
        diff[hi] -= 1


def _cum(diff, n):
    out, c = [], 0
    for k in range(n):
        c += diff[k]
        out.append(c)
    return out


def _pct(sorted_vals, p):
    if not sorted_vals:
        return None
    k = max(0, math.ceil(p * len(sorted_vals)) - 1)
    return sorted_vals[k]


def _r(x, nd=4):
    return None if x is None else round(x, nd)


def timeline(res: Result) -> dict:
    """Hourly KPI timeline over [0, days]: one sample per hour start."""
    n = res.params.days * 24
    T1 = res.t1
    ng = len(res.spec.groups)
    d_q = [0] * (n + 1)
    d_anch = [0] * (n + 1)
    d_berth = [0] * (n + 1)
    d_work = [0] * (n + 1)
    d_grp = [[0] * (n + 1) for _ in range(ng)]
    wait_sum = [0.0] * n
    wait_cnt = [0] * n
    arr = [0] * n
    for v in res.vessels:
        if v.t_req is None:
            continue
        _add_interval(d_q, v.t_req, v.t_move, n)
        if v.t_anch is not None:
            _add_interval(d_anch, v.t_anch, v.t_move, n)
        if v.t_move is not None:
            _add_interval(d_berth, v.t_move, v.t_dep, n)
            for _ in range(v.nberths):
                _add_interval(d_grp[v.group], v.t_move, v.t_dep, n)
            k = v.t_move // HOUR
            if 0 <= k < n:
                wait_sum[k] += (v.t_move - v.t_req) / HOUR
                wait_cnt[k] += 1
        if v.t_berth is not None:
            _add_interval(d_work, v.t_berth, v.t_work_end, n)
    for v in res.vessels:
        k = v.t_arr // HOUR
        if 0 <= k < n:
            arr[k] += 1
    q = _cum(d_q, n)
    an = _cum(d_anch, n)
    ber = _cum(d_berth, n)
    wk = _cum(d_work, n)
    grp = [_cum(d, n) for d in d_grp]
    yard = {t: (f, m) for (t, _y, f, m) in res.ticks}
    rows = []
    for k in range(n):
        f, m = yard.get(k * HOUR, (None, None))
        rows.append({
            "t": k * HOUR, "queue": q[k], "anchored": an[k],
            "outside": q[k] - an[k], "at_berth": ber[k], "working": wk[k],
            "util": {res.spec.groups[g].id: _r(grp[g][k] / res.spec.groups[g].berths)
                     for g in range(ng)},
            "yard_fill": _r(f), "yard_throttle": _r(m),
            "wait_mean_h": _r(wait_sum[k] / wait_cnt[k]) if wait_cnt[k] else None,
            "arrivals": arr[k]})
    return {"step_s": HOUR, "samples": rows}


def summary(res: Result, tl: dict | None = None) -> dict:
    tl = tl or timeline(res)
    T1 = res.t1
    days = res.params.days
    spec = res.spec
    waits, censored = [], 0
    port_time = []
    arrivals = departed = 0
    teu = 0.0
    tonnes: dict = {}
    calls_by_kind: dict = {}
    busy = [0.0] * len(spec.groups)
    for v in res.vessels:
        if 0 <= v.t_arr < T1:
            arrivals += 1
            calls_by_kind[v.kind] = calls_by_kind.get(v.kind, 0) + 1
        if v.t_req is not None and 0 <= v.t_req < T1:
            end = v.t_move if v.t_move is not None else T1
            waits.append((end - v.t_req) / HOUR)
            if v.t_move is None:
                censored += 1
        if v.t_gone is not None and 0 <= v.t_gone <= T1:
            departed += 1
            port_time.append((v.t_gone - v.t_arr) / HOUR)
        if v.t_work_end is not None and 0 <= v.t_work_end < T1:
            if v.kind == "container":
                teu += v.work
            else:
                tonnes[v.kind] = tonnes.get(v.kind, 0.0) + v.work
        if v.t_move is not None:
            a = max(v.t_move, 0)
            b = min(v.t_dep if v.t_dep is not None else T1, T1)
            if b > a:
                busy[v.group] += (b - a) * v.nberths
    total_all = len(res.vessels)
    gone_all = sum(1 for v in res.vessels if v.t_gone is not None)
    in_sys = total_all - gone_all
    # Conservation is checked against an INDEPENDENT partition of the calls by their state at the
    # horizon (`in_sys = total - gone` would make `balanced` true by construction). Every call is
    # exactly one of: clear of the berth, at a berth, queued, or still approaching.
    st_gone = st_berth = st_queue = st_appr = 0
    for v in res.vessels:
        if v.t_gone is not None and v.t_gone <= T1:
            st_gone += 1
        elif v.t_move is not None and v.t_move <= T1:
            st_berth += 1
        elif v.t_req is not None and v.t_req <= T1:
            st_queue += 1
        else:
            st_appr += 1
    queue_at_horizon = st_queue
    ws = sorted(waits)
    samples = tl["samples"]
    qs = [s["queue"] for s in samples]
    outs = [s["outside"] for s in samples]
    fills = [f for (t, _y, f, _m) in res.ticks if f is not None and 0 <= t <= T1]
    util = {g.id: _r(busy[i] / (g.berths * T1)) for i, g in enumerate(spec.groups)}
    return {
        "days": days,
        "calls": {"arrived": arrivals, "departed": departed,
                  "note": "arrived: calls reaching the port limit in [0, horizon); "
                          "departed: calls clear of the berth in [0, horizon), "
                          "which includes calls that arrived during the warm-up; "
                          "conservation is checked over the whole run",
                  "arrived_by_kind": dict(sorted(calls_by_kind.items())),
                  "in_system_at_end": in_sys,
                  "conservation": {"arrived_incl_warmup": total_all,
                                   "departed_incl_warmup": gone_all,
                                   "in_system_at_end": in_sys,
                                   "in_system_by_state": {"approaching": st_appr, "queued": st_queue,
                                                          "at_berth": st_berth},
                                   "balanced": (total_all == st_gone + st_berth + st_queue + st_appr
                                                and gone_all == st_gone
                                                and in_sys == st_berth + st_queue + st_appr)}},
        "queue": {"peak": max(qs) if qs else 0,
                  "mean": _r(sum(qs) / len(qs)) if qs else 0.0,
                  "outside_peak": max(outs) if outs else 0,
                  "outside_vessel_hours": sum(outs),
                  # the queue AT the horizon T1 (calls requesting a berth and not yet moving in);
                  # the hourly timeline stops one hour earlier, so qs[-1] is the state at T1 - 1 h
                  "at_end": queue_at_horizon},
        "wait_h": {"mean": _r(sum(ws) / len(ws)) if ws else None,
                   "p95": _r(_pct(ws, 0.95)), "max": _r(ws[-1]) if ws else None,
                   "calls": len(ws), "censored_still_waiting": censored,
                   "note": "wait = time at anchorage / outside before leaving "
                           "for the berth, for calls reaching the anchorage "
                           "in [0, horizon); a call still waiting at the end "
                           "counts its wait so far (lower bound)"},
        "port_time_h": {"mean": _r(sum(port_time) / len(port_time)) if port_time else None,
                        "p95": _r(_pct(sorted(port_time), 0.95)),
                        "calls": len(port_time),
                        "note": "arrival at the port limit to clear of the "
                                "berth (the World Bank CPPI port-time "
                                "definition), departed calls only"},
        "throughput": {"container_teu_per_day": _r(teu / days, 2),
                       "tonnes_per_day_by_class": {k: _r(v / days, 2)
                                                   for k, v in sorted(tonnes.items())},
                       "note": "TEU and tonnes are never added"},
        "berth_utilisation": util,
        "yard": {"mean_fill": _r(sum(fills) / len(fills)) if fills else None,
                 "peak_fill": _r(max(fills)) if fills else None,
                 "min_throttle": _r(min((m for (t, _y, _f, m) in res.ticks
                                         if 0 <= t <= T1), default=None))},
        "result_digest": result_digest(res),
    }


# ---------------------------------------------------------------- frames
def _legend(res: Result):
    labels = []
    for kind in KIND_ORDER:
        for c in res.class_table.get(kind, []):
            labels.append(f"{kind}/{c['name']}")
    return labels


def frames(res: Result, t_from: int, t_to: int, step_s: int, max_frames: int,
           pw_start_at=None) -> dict:
    if step_s < 60 or step_s > 7 * DAY:
        raise PortSimError("invalid_request", "step must be 1..10080 minutes")
    if t_from < res.t0 or t_to > res.t1 or t_to < t_from:
        raise PortSimError("invalid_request",
                           f"window must satisfy {res.t0 // 3600}h <= from <= "
                           f"to <= {res.t1 // 3600}h (hours; negative = warm-up)")
    n_fr = (t_to - t_from) // step_s + 1
    if n_fr > max_frames:
        raise PortSimError("window_too_large",
                           f"{n_fr} frames requested, at most {max_frames}: "
                           "narrow from/to or raise step")
    labels = _legend(res)
    lab_ix = {l: i for i, l in enumerate(labels)}
    pin = res.pin_s
    spec = res.spec
    ng = len(spec.groups)
    cand = [v for v in res.vessels
            if v.t_arr <= t_to and (v.t_gone is None or v.t_gone > t_from)]
    cand.sort(key=lambda v: (v.t_arr, v.i))
    rows_max = int(plug("run_limits")["frame_rows_max"])
    total_rows = 0
    pending = 0
    active: list = []
    yt = [t for (t, *_r) in res.ticks]
    out = []
    for k in range(n_fr):
        s = t_from + k * step_s
        while pending < len(cand) and cand[pending].t_arr <= s:
            active.append(cand[pending])
            pending += 1
        active = [v for v in active if v.t_gone is None or v.t_gone > s]
        rows = []
        nq = na = 0
        busy = [0] * ng
        for v in active:
            prog = None
            loc = None
            if v.t_req is None or s < v.t_req:
                st = 0
                if v.t_req is not None and v.t_req > v.t_arr:
                    prog = (s - v.t_arr) / (v.t_req - v.t_arr)
            elif v.t_move is None or s < v.t_move:
                nq += 1
                if v.t_anch is not None and s >= v.t_anch:
                    st, loc = 2, v.slot
                    na += 1
                else:
                    st = 1
            else:
                loc = v.berth
                if v.t_dep is None or s < v.t_dep:
                    busy[v.group] += v.nberths
                if v.t_berth is None or s < v.t_berth:
                    st = 3
                    prog = (s - v.t_move) / pin if pin else 1.0
                elif v.t_work_end is None or s < v.t_work_end:
                    st = 4
                    if v.t_work_end is not None:
                        prog = (s - v.t_berth) / max(1, v.t_work_end - v.t_berth)
                elif v.t_dep is None or s < v.t_dep:
                    st = 5
                else:
                    st = 6
                    prog = (s - v.t_dep) / res.pout_s if res.pout_s else 1.0
            rows.append([v.i, lab_ix.get(f"{v.kind}/{v.cls}", -1), st, loc,
                         None if prog is None else round(min(1.0, max(0.0, prog)), 3)])
        total_rows += len(rows)
        if total_rows > rows_max:
            raise PortSimError("window_too_large",
                               f"more than {rows_max} vessel rows in the window: "
                               "narrow from/to or raise step")
        j = bisect.bisect_right(yt, s) - 1
        fill = res.ticks[j][2] if j >= 0 else None
        out.append({"t": s, "queue": nq, "anchored": na, "outside": nq - na,
                    "util": [round(busy[g] / spec.groups[g].berths, 3)
                             for g in range(ng)],
                    "yard_fill": _r(fill, 3), "vessels": rows})
    return {
        "from": t_from, "to": t_to, "step_s": step_s, "n_frames": n_fr,
        "legend": {
            "vessel_fields": ["id", "class", "state", "location", "progress"],
            "states": list(STATE_NAMES),
            "classes": labels,
            "location": "state anchored: anchorage slot number; moving_in / "
                        "working / ready_to_depart / moving_out: berth index "
                        "into `berths`; otherwise null (waiting_outside has no slot)",
            "progress": "fraction of the current movement (approach, moving_in, "
                        "moving_out) or elapsed fraction of the berth stay "
                        "(working); null when not defined"},
        "berths": [{"index": i, "group": spec.groups[g].id, "slot": k,
                    "length_m": ln} for i, (g, k, ln) in enumerate(res.berth_table)],
        "groups": [{"index": i, "id": g.id, "name": g.name, "kind": g.kind,
                    "berths": g.berths} for i, g in enumerate(spec.groups)],
        "anchorage_slots": spec.anchorage_slots,
        "frames": out}


# ------------------------------------------------------- baseline state
def baseline_state(anchor_report: dict, base_sum: dict | None = None) -> dict:
    """Is the undisturbed run itself congested, and why? Read from the
    engine2 anchor report (and, when given, the baseline run's own KPIs).
    A kind is `over capacity` when the berths the profile maps for it
    cannot serve its calls at the cited productivity even with the service
    time scaled to its bound (implied utilisation >= 1): the baseline queue
    then grows with the horizon, and a what-if moves a number that is
    already large. Additive reading aid; it changes no physics."""
    over = sorted(k for k, r in anchor_report.items()
                  if (r.get("implied_utilisation") or 0.0) >= 1.0)
    bound = sorted(k for k, r in anchor_report.items() if r.get("scale_capped"))
    sat = bool(over)
    out = {
        "structurally_saturated": sat,
        "kinds_over_capacity": over,
        "kinds_at_service_time_bound": bound,
        "max_implied_utilisation": max(
            (r.get("implied_utilisation") or 0.0 for r in anchor_report.values()),
            default=None),
        "reading": (
            "baseline berth utilisation is below 1 for every kind: a what-if "
            "can be read against a baseline that is not itself queuing"
            if not sat else
            "the berths mapped for " + ", ".join(over) + " cannot serve the "
            "baseline calls at the cited productivity even at the service-time "
            "bound: the baseline already queues and grows with the horizon, so "
            "read the delta against the baseline run, not the level")}
    if base_sum is not None:
        out["baseline_wait_mean_h"] = _dig(base_sum, ("wait_h", "mean"))
        out["baseline_in_system_at_end"] = _dig(base_sum, ("calls", "in_system_at_end"))
    return out


# ---------------------------------------------------------------- compare
KPI_ROWS = (
    ("wait_mean_h", ("wait_h", "mean"), "h", "lower is better"),
    ("wait_p95_h", ("wait_h", "p95"), "h", "lower is better"),
    ("wait_max_h", ("wait_h", "max"), "h", "lower is better"),
    ("port_time_mean_h", ("port_time_h", "mean"), "h", "lower is better"),
    ("queue_peak", ("queue", "peak"), "vessels", "lower is better"),
    ("queue_mean", ("queue", "mean"), "vessels", "lower is better"),
    ("waiting_outside_peak", ("queue", "outside_peak"), "vessels", "lower is better"),
    ("waiting_outside_vessel_hours", ("queue", "outside_vessel_hours"), "vessel-h", "lower is better"),
    ("calls_arrived", ("calls", "arrived"), "calls", ""),
    ("calls_departed", ("calls", "departed"), "calls", ""),
    ("in_system_at_end", ("calls", "in_system_at_end"), "calls", "lower is better"),
    ("throughput_container_teu_per_day", ("throughput", "container_teu_per_day"), "TEU/day", ""),
    ("yard_peak_fill", ("yard", "peak_fill"), "fraction", "lower is better"),
    ("yard_mean_fill", ("yard", "mean_fill"), "fraction", "lower is better"),
)


def _dig(d, path):
    for p in path:
        if d is None:
            return None
        d = d.get(p)
    return d


def first_divergence(a: Result, b: Result):
    """Earliest time (s) at which the two runs differ, or None."""
    best = None

    def upd(t):
        nonlocal best
        if t is not None and (best is None or t < best):
            best = t
    n = max(len(a.vessels), len(b.vessels))
    for i in range(n):
        ra = a.vessels[i].row() if i < len(a.vessels) else None
        rb = b.vessels[i].row() if i < len(b.vessels) else None
        if ra == rb:
            continue
        if ra is None or rb is None:
            r = ra or rb
            upd(r[3])
            continue
        # fields 3.. are timestamps (arr, req, anch, move, berth, work_end, dep, gone)
        for fa, fb in zip(ra[3:11], rb[3:11]):
            if fa != fb:
                cand = [x for x in (fa, fb) if x is not None]
                upd(min(cand))
                break
        else:
            if ra[11] != rb[11]:
                upd(ra[6] if ra[6] is not None else ra[3])
    for (ta, ya, _fa, _ma), (tb, yb, _fb, _mb) in zip(a.ticks, b.ticks):
        if ta != tb or abs(ya - yb) > 1e-6:
            upd(min(ta, tb))
            break
    return best


def compare(base: Result, other: Result, base_sum: dict, other_sum: dict) -> dict:
    div = first_divergence(base, other)
    rows = []
    for name, path, unit, sense in KPI_ROWS:
        a, b = _dig(base_sum, path), _dig(other_sum, path)
        row = {"measure": name, "unit": unit, "baseline": a, "scenario": b,
               "basis": "SIMULATED"}
        if a is None or b is None:
            row["delta"] = None
            row["omitted"] = "not defined for one of the runs (never zero-filled)"
        else:
            row["delta"] = _r(b - a)
            row["delta_pct"] = _r(100.0 * (b - a) / a, 2) if a else None
            if not sense:
                row["note"] = ("volume follows the arrival schedule and the end "
                               "of the window: read it next to calls_arrived, "
                               "no better / worse judgement is made")
            if sense:
                row["direction"] = ("worse" if ((b > a) == (sense == "lower is better")
                                                and b != a) else
                                    "better" if b != a else "same")
        rows.append(row)
    util = {}
    bu, ou = base_sum["berth_utilisation"], other_sum["berth_utilisation"]
    for g in sorted(bu):
        util[g] = {"baseline": bu[g], "scenario": ou.get(g),
                   "delta": _r(ou[g] - bu[g]) if g in ou else None}
    return {"baseline_state": baseline_state(base.anchor_report, base_sum),
            "diverges_at_s": div,
            "diverges_at_h": None if div is None else round(div / 3600.0, 4),
            "identical": div is None and base_sum["result_digest"] == other_sum["result_digest"],
            "table": rows, "berth_utilisation": util}
