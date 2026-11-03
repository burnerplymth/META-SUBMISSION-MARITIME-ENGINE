"""Consistency with the chain engine (ENGINE REV 2).

`chain_path(engine, place_id)` reads what a finished Engine2 run implies at
one place: the arrival-rate multiplier path (arrivals / baseline lambda0)
and the capacity multiplier path (1 - capacity loss) as step functions of
time, plus the fluid queue and FIFO wait the engine itself computed. The
port twin replays them as a scenario item (`chain_path`), so the operational
picture at the port is the chain's picture of the same place.

Reads Engine2 internals that exist for introspection (`_last_sim`,
`_profile`, `_fifo_waits`); engine2 is not modified. CORE places only: the
chain engine does not carry SHELL places.
"""
from __future__ import annotations

from .classes import PortSimError


def _sim_of(engine):
    sim = getattr(engine, "_last_sim", None)
    if sim is None:
        raise PortSimError("no_chain_run",
                           "the engine has no finished run to read; call "
                           "Engine2.run first")
    return sim


def chain_path(engine, place_id: str, step_h: float = 24.0,
               horizon_days: float | None = None) -> dict:
    """Step paths implied by the engine's last run at `place_id`."""
    sim = _sim_of(engine)
    if place_id not in engine.ix:
        raise PortSimError(
            "not_in_chain",
            f"{place_id!r} is not a place of the chain engine's CORE places; "
            "SHELL places have no chain picture", 422)
    i = engine.ix[place_id]
    dt = float(sim.dt)
    lam0 = float(engine.lam0[i])
    mumax = float(engine.mumax[i])
    series = {s[0]: s for s in (sim.series[i] or [])}
    profiles = sim.profiles.get(i, [])
    k_end = int(sim.k_end)
    total_d = k_end * dt if horizon_days is None else float(horizon_days)
    n_eng = max(1, int(round(total_d / dt)))

    arr, cap, qlen, wait = [], [], [], []
    for k in range(n_eng):
        s = series.get(k)
        arr.append((s[3] / lam0) if (s is not None and lam0 > 0) else 1.0)
        qlen.append(s[1] if s is not None else 0.0)
        t = k * dt
        cur = None
        for p in profiles:
            if t >= p[0]:
                cur = p
        loss = engine._profile(cur[1], cur[2], cur[3], t - cur[0]) if cur else 0.0
        cap.append(1.0 - loss)
    waits = dict(engine._fifo_waits(sim.series[i] or [], dt))
    for k in range(n_eng):
        wait.append(waits.get(k, 0.0))

    per = max(1, int(round(step_h / 24.0 / dt)))
    out_arr, out_cap, out_q, out_w = [], [], [], []
    for a in range(0, n_eng, per):
        seg = slice(a, min(a + per, n_eng))
        n = len(arr[seg])
        out_arr.append(round(sum(arr[seg]) / n, 6))
        out_cap.append(round(sum(cap[seg]) / n, 6))
        out_q.append(round(sum(qlen[seg]) / n, 6))
        out_w.append(round(sum(wait[seg]) / n, 6))
    return {
        "basis": "ENGINE REV 2 fluid stock-and-flow, read at one place; MODELLED",
        "place_id": place_id,
        "engine_dt_d": dt,
        "lambda0_calls_per_day": lam0,
        "mu_max_calls_per_day": mumax,
        "step_h": per * dt * 24.0,
        "arrival_mult": out_arr,
        "capacity_mult": out_cap,
        "fluid_queue_calls": out_q,
        "fluid_wait_d": out_w,
        "fluid_peak_wait_d": max(wait) if wait else 0.0,
        "horizon_d": n_eng * dt,
    }


def to_scenario_item(path: dict, days: int | None = None) -> dict:
    """The `chain_path` scenario item for a path. If `days` is given and the
    path is longer, the item is truncated at the horizon and says so in its
    source block (the caller chose the horizon)."""
    step_h = path["step_h"]
    a, c = list(path["arrival_mult"]), list(path["capacity_mult"])
    truncated = False
    if days is not None:
        n = int(days * 24.0 // step_h + 1e-9)
        if n < len(a):
            a, c, truncated = a[:n], c[:n], True
    return {"type": "chain_path", "step_h": step_h, "arrival_mult": a,
            "capacity_mult": c,
            "source": {"engine": "ENGINE REV 2", "place_id": path["place_id"],
                       "lambda0_calls_per_day": path["lambda0_calls_per_day"],
                       "truncated_to_horizon": truncated}}


def engine_wait_stats(engine, place_id: str, t0_d: float = 0.0,
                      t1_d: float | None = None) -> dict:
    """Fluid-model aggregates at a place over arrivals in [t0_d, t1_d):
    arrivals (calls), the arrival-weighted mean FIFO wait (days; units
    that meet no backlog count as zero wait), the peak wait and the calls
    served in the window. Used to validate the port twin against the chain
    engine."""
    sim = _sim_of(engine)
    i = engine.ix[place_id]
    dt = float(sim.dt)
    lam0 = float(engine.lam0[i])
    series = {s[0]: s for s in (sim.series[i] or [])}
    waits = dict(engine._fifo_waits(sim.series[i] or [], dt))
    k_end = int(sim.k_end)
    k0 = int(round(t0_d / dt))
    k1 = k_end if t1_d is None else int(round(t1_d / dt))
    arr = wsum = peak = served = 0.0
    for k in range(k0, k1):
        s = series.get(k)
        a = (s[3] if s is not None else lam0) * dt
        w = waits.get(k, 0.0)
        arr += a
        wsum += a * w
        peak = max(peak, w)
        served += (s[2] if s is not None else lam0) * dt
    return {"dt_d": dt, "arrivals_calls": arr, "served_calls": served,
            "mean_wait_d": (wsum / arr) if arr > 0 else 0.0, "peak_wait_d": peak}


