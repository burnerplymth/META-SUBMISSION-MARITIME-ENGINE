"""Run assembly: derivation + simulation +
record + provenance, shared by the API routes.

The journal core is hashed BEFORE simulation (the id depends only on
the pinned inputs); the record then stores everything state(tick)
needs. Provenance is computed from the pinned graph's calibration
bases at derivation time — never hard-coded copy.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from . import buildcache
from .derive import derive
from .simulate import fmt_ts, simulate

try:
    from engine.canonical import canonical_json, sha256_hex
except Exception:  # imported as server.sim.* from the repo root instead
    from server.engine.canonical import canonical_json, sha256_hex  # type: ignore

PROVENANCE_BASIS = (
    "headways, speeds, dwells, weather factors: MODELED (spec constants); "
    "all entities SIMULATED")


def provenance_line(graph_version, split: dict, weather: dict | None,
                    weather_reason: str | None,
                    air: dict | None = None) -> str:
    if weather is not None:
        wtxt = (f"weather: Open-Meteo snapshot {weather.get('retrieved_at')} "
                "(CC BY 4.0)")
    else:
        wtxt = f"weather: UNAVAILABLE — {weather_reason or 'not fetched'}"
    by_tier = split.get("by_tier") or {}
    if by_tier:
        c, s = by_tier["core"], by_tier["shell"]
        lanes_txt = (
            f"lane shares feeding headways, per tier — CORE: "
            f"{c['sourced_edges']} SOURCED · {c['modeled_edges']} MODELED "
            f"edges; SHELL (routing-grade): {s['sourced_edges']} SOURCED · "
            f"{s['modeled_edges']} MODELED edges")
    else:
        lanes_txt = (
            f"lane shares feeding headways: {split['sourced_edges']} "
            f"SOURCED · {split['modeled_edges']} MODELED edges")
    # The tier split in places/routes plus
    # the statistics-scope sentence, in the product's vocabulary.
    places = split.get("places_by_tier") or {}
    lanes_cls = split.get("lanes_by_tier_class") or {}
    if places and lanes_cls:
        shell_routes = (lanes_cls.get("shell_touching", 0)
                        + lanes_cls.get("shell_shell", 0))
        tier_txt = (
            f"places in this run — CORE: {places.get('core', 0)} · "
            f"SHELL: {places.get('shell', 0)}; routes in this run — "
            f"CORE: {lanes_cls.get('core', 0)} · SHELL-TOUCHING: "
            f"{shell_routes}; shell-route frequencies: MODELLED "
            f"(regional inheritance); statistics scope: CORE places "
            f"only — shell-inclusive figures, where shown, are "
            f"separate and labelled SHELL-INCLUSIVE (MODELLED); ")
    else:
        tier_txt = ""
    # The air class split, computed at
    # derivation, printed in the provenance split.
    air_txt = ""
    if air:
        cc = air.get("class_counts") or {}
        hubs = air.get("integrator_hubs") or []
        air_txt = (
            f"air classes (MODELLED, split from the freight-"
            f"capacity quartile): {cc.get('cargo', 0)} cargo · "
            f"{cc.get('passenger', 0)} passenger aircraft; integrator "
            f"hubs: {len(hubs)}; air rotations, banks and night sorts "
            f"MODELLED; ")
    return (
        f"TRAFFIC MODEL — topology + capacities: graph v{graph_version} "
        f"(SOURCED/MODELED per node); {lanes_txt}; {tier_txt}{air_txt}"
        f"{PROVENANCE_BASIS}; {wtxt}.")


def _weather_window(weather: dict | None):
    """The record's weather window: when weather is pinned,
    a dict carrying the snapshot's coverage — from the earliest node
    series start to the latest node series end, its source, and
    whether the snapshot carries the storm-cell field. Unpinned stays
    the bare string "UNAVAILABLE"."""
    if weather is None:
        return "UNAVAILABLE"
    series = [entry.get("times") or [] for entry in
              (weather.get("nodes") or {}).values()]
    series = [t for t in series if t]
    return {
        "state": "PINNED",
        "from": min(t[0] for t in series) if series else None,
        "to": max(t[-1] for t in series) if series else None,
        "source": weather.get("source"),
        "cells": bool(weather.get("cells")),
    }


def _trim_segments(segments: dict, start_epoch: float) -> dict:
    """Collapse pre-roll spin-up out of the stored segment
    record: per entity, keep only the OPEN episode at the run's
    start (the last pre-start event — plus its arrival when the
    entity sits berthed, so queue accounting re-derived from the
    record stays paired) and everything after. Completed pre-start
    cycles are derivation, not run data (the marker rule makes
    the same cut); node_series is computed from the full event
    record before trimming, so tick-0 queue/berth state is exact.
    Without this, a large record (6,000 entities over a
    pre-roll of 2x the longest lane) stores millions of spin-up
    segments and run creation exhausts memory."""
    out: dict = {}
    for eid, evs in segments.items():
        last = -1
        for j, ev in enumerate(evs):
            if ev["t"] <= start_epoch:
                last = j
            else:
                break
        if last < 0:
            out[eid] = evs
            continue
        keep = last
        if evs[last]["kind"] == "berth" and last > 0 \
                and evs[last - 1]["kind"] == "arrive":
            keep = last - 1
        out[eid] = evs[keep:]
    return out


# --- Content-addressed build caches ------------------------------------
# Two pure sub-computations of build_record are cached on disk when the
# caller passes ``cache_dir`` (the API passes <sim_runs_dir>/_cache):
#   derive  the roster derivation, a pure function of (graph content,
#           seed, start, horizon end, derive/geometry code, land data);
#   twin    the undisrupted twin's leg-arrival events, a pure function
#           of (derivation key, weather snapshot hash, airspace,
#           storm_cells, tick, ticks, start, simulation code) - it does
#           NOT depend on the disruptions, so every run and every fork
#           of one base request shares one entry.
# Keys carry a sha256 of the source files that compute the value, so
# editing the model can never serve a stale entry. A cache entry is only
# ever a stored copy of the value the computation returns; reads that
# fail to parse or to validate are misses. Entries are bounded, written
# atomically and computed single-flight (see buildcache). With
# ``cache_dir`` None (the default) none of this runs.
#
# An UNDISRUPTED run IS the twin of every disrupted
# run of the same base, so it seeds the twin entry for free; a missing
# twin is computed in a second process while the disrupted run simulates
# here (buildcache.TwinProcess; sequential fallback), and only its
# ``arrival_events`` are produced (``simulate(light=True)``).
_SIM_DIR = Path(__file__).resolve().parent
_CACHE_KEEP = buildcache.CACHE_KEEP
_code_fp_memo: dict = {}
_graph_fp_memo: list = []  # [(graph_data object, fingerprint)] newest last
_DERIVE_CODE = ("derive.py", "geometry.py", "data/ne_land_110m.geojson")
_SIM_CODE = _DERIVE_CODE + ("simulate.py", "weather.py")
TWIN_FORMAT = "twin-v2"


def _code_fp(names: tuple) -> str:
    fp = _code_fp_memo.get(names)
    if fp is None:
        h = hashlib.sha256()
        for name in names:
            h.update(name.encode())
            h.update((_SIM_DIR / name).read_bytes())
        fp = h.hexdigest()
        _code_fp_memo[names] = fp
    return fp


def _graph_fp(graph_data: dict) -> str:
    for obj, fp in _graph_fp_memo:
        if obj is graph_data:
            return fp
    fp = hashlib.sha256(json.dumps(graph_data, sort_keys=True,
                                   separators=(",", ":")).encode()).hexdigest()
    _graph_fp_memo.append((graph_data, fp))
    del _graph_fp_memo[:-3]
    return fp


def _cache_key(payload: dict) -> str:
    return sha256_hex(canonical_json(payload))[:32]


def _cache_file(cache_dir, kind: str, payload: dict) -> Path:
    return Path(cache_dir) / f"{kind}-{_cache_key(payload)}.json"


def _cache_read(path: Path):
    return buildcache.read_json(path)


def _cache_write(path: Path, kind: str, value) -> None:
    buildcache.write_json_atomic(path, kind, json.dumps(value))


def _services_table(graph_data: dict, journal_core: dict) -> dict:
    """The leg table of the journaled services selection (the sidecar's
    file hash must match the journaled one; ServicesError otherwise)."""
    try:
        from graph_services.runtime import sim_services
    except Exception:  # imported as server.sim.* from the repo root instead
        from server.graph_services.runtime import sim_services  # type: ignore
    return sim_services(graph_data, journal_core["graph_hash"],
                        journal_core["services"])


def _lane_provider(journal_core: dict):
    """The lane polyline provider of the journaled lane_geometry selection."""
    try:
        from lanegeo.sim import provider_for_journal
    except Exception:  # imported as server.sim.* from the repo root instead
        from server.lanegeo.sim import provider_for_journal  # type: ignore
    return provider_for_journal(journal_core["lane_geometry"])


def _services_code_fp() -> str:
    h = hashlib.sha256()
    pkg = _SIM_DIR.parent / "graph_services"
    for name in ("legs.py", "zones.py", "loader.py", "runtime.py"):
        h.update(name.encode())
        h.update((pkg / name).read_bytes())
    return h.hexdigest()


def _extra_sim_graph(graph_data: dict, graph_hash: str, journal_core: dict):
    """(merged graph dict, meta) of the journaled extra selection (the
    sidecar's file hash must match the journaled one; ExtraError otherwise)."""
    try:
        from graph_extra.runtime import sim_graph
    except Exception:  # imported as server.sim.* from the repo root instead
        from server.graph_extra.runtime import sim_graph  # type: ignore
    return sim_graph(graph_data, graph_hash, journal_core["extra"])


def _derive_cached(graph_data, journal_core, start_epoch, horizon_end,
                   cache_dir, stats):
    """(derivation, key payload). A cache hit returns the stored copy
    of what derive() returned (JSON round trip: a derivation holds no
    tuples or sets, and key order is preserved)."""
    t0 = time.perf_counter()
    # Services layer (opt-in): only a journal core that carries a
    # ``services`` selection reaches derive() with a leg table; every
    # other body calls derive() exactly as before.
    svc_kw: dict = {}
    if journal_core.get("services"):
        svc_kw["services"] = _services_table(graph_data, journal_core)
    # Lane geometry (opt-in): only a journal core that carries a ``lane_geometry`` selection derives on the
    # lane polylines; the sidecars must still hash to the journaled values (LaneGeometryError otherwise).
    if journal_core.get("lane_geometry"):
        svc_kw["lane_geometry"] = _lane_provider(journal_core)
    if cache_dir is None:
        derivation = derive(graph_data, journal_core["seed"], start_epoch,
                            horizon_end, **svc_kw)
        if stats is not None:
            stats["derive_cache"] = "off"
            stats["derive_s"] = round(time.perf_counter() - t0, 3)
        return derivation, None
    payload = {"v": 1, "code": _code_fp(_DERIVE_CODE),
               "graph": _graph_fp(graph_data), "seed": journal_core["seed"],
               "start": start_epoch, "end": horizon_end}
    if journal_core.get("services"):
        payload["services"] = journal_core["services"]
        payload["services_code"] = _services_code_fp()
    if journal_core.get("lane_geometry"):
        payload["lane_geometry"] = journal_core["lane_geometry"]
    if journal_core.get("extra"):
        payload["extra"] = journal_core["extra"]
    path = _cache_file(cache_dir, "derive", payload)

    def valid(value) -> bool:
        return isinstance(value, dict) and "entities" in value \
            and "lanes" in value

    hit = _cache_read(path)
    state = "hit"
    if not valid(hit):
        with buildcache.FlightLock(path):  # single flight per key
            hit = _cache_read(path)
            if not valid(hit):
                hit = derive(graph_data, journal_core["seed"], start_epoch,
                             horizon_end, **svc_kw)
                _cache_write(path, "derive", hit)
                state = "miss"
    else:
        buildcache.touch(path)
    if stats is not None:
        stats["derive_cache"] = state
        stats["derive_s"] = round(time.perf_counter() - t0, 3)
    return hit, payload


def _twin_payload(derive_payload, journal_core, weather, start_epoch, tick_s,
                  airspace, storm_cells) -> dict:
    return {"v": 1, "code": _code_fp(_SIM_CODE), "derive": derive_payload,
            "weather": weather["hash"] if weather else None,
            "airspace": airspace, "storm_cells": storm_cells,
            "tick_s": tick_s, "ticks": journal_core["ticks"],
            "start": start_epoch}


def _count_events(arrival_events) -> int:
    return sum(len(v) for v in arrival_events.values())


def _twin_text(key: str, arrival_events: dict) -> str:
    return json.dumps({"format": TWIN_FORMAT, "key": key,
                       "events": _count_events(arrival_events),
                       "arrival_events": arrival_events})


def _twin_read(path: Path, key: str):
    """The cached arrival_events, or None for a missing, unparsable or
    inconsistent entry (wrong key, wrong event count)."""
    got = buildcache.read_json(path)
    if not isinstance(got, dict) or got.get("format") != TWIN_FORMAT \
            or got.get("key") != key:
        return None
    events = got.get("arrival_events")
    if not isinstance(events, dict) \
            or not all(isinstance(v, list) for v in events.values()) \
            or got.get("events") != _count_events(events):
        return None
    buildcache.touch(path)
    return events


class _TwinSource:
    """The undisrupted twin's arrival_events for one build: from the
    cache, from a second process running beside the disrupted run, or
    computed here. ``begin`` before the disrupted simulation, ``finish``
    after it; ``abort`` on any error in between."""

    def __init__(self, graph_data, derivation, derive_payload, journal_core,
                 weather, start_epoch, tick_s, airspace, storm_cells,
                 cache_dir, stats, parallel):
        self.args = (graph_data, derivation, weather, start_epoch, tick_s,
                     journal_core["ticks"], airspace, storm_cells)
        self.cache_dir = cache_dir
        self.stats = stats
        self.parallel = parallel
        self.events = None
        self.proc = None
        self.lock = None
        self.path = None
        self.key = None
        self.t_begin = 0.0
        self.horizon_days = tick_s * journal_core["ticks"] / 86400.0
        if cache_dir is not None:
            payload = _twin_payload(derive_payload, journal_core, weather,
                                    start_epoch, tick_s, airspace,
                                    storm_cells)
            self.key = _cache_key(payload)
            self.path = Path(cache_dir) / f"twin-{self.key}.json"

    def _note(self, **kv) -> None:
        if self.stats is not None:
            self.stats.update(kv)

    def _compute_here(self):
        (graph_data, derivation, weather, start_epoch, tick_s, ticks,
         airspace, storm_cells) = self.args
        twin = simulate(graph_data, derivation, [], weather, start_epoch,
                        tick_s, ticks, airspace=airspace,
                        storm_cells=storm_cells, light=True)
        return twin["arrival_events"]

    def _store(self, events) -> None:
        if self.path is not None:
            buildcache.write_json_atomic(
                self.path, "twin", _twin_text(self.key, events))

    def _child_done(self, value) -> None:
        """Waiter thread: the child finished. Publish its result and
        release the single-flight lock at once (followers need not wait
        for the parent's own simulation)."""
        try:
            if isinstance(value, dict):
                self._store(value)
        finally:
            if self.lock is not None:
                self.lock.release()

    def begin(self) -> None:
        self.t_begin = time.perf_counter()
        if self.path is not None:
            self.events = _twin_read(self.path, self.key)
            if self.events is None:
                self.lock = buildcache.FlightLock(self.path).acquire()
                self.events = _twin_read(self.path, self.key)
                if self.events is not None:
                    self.lock.release()
                    self.lock = None
            if self.events is not None:
                self._note(twin_cache="hit", twin_mode="hit",
                           twin_s=round(time.perf_counter() - self.t_begin,
                                        3))
                return
        self._note(twin_cache="miss" if self.path is not None else "off")
        want = self.parallel if self.parallel is not None \
            else self.path is not None
        if want:
            refusal = buildcache.parallel_refusal(self.horizon_days)
            if refusal is None:
                (graph_data, derivation, weather, start_epoch, tick_s,
                 ticks, airspace, storm_cells) = self.args
                self.proc = buildcache.TwinProcess.start(
                    {"graph_data": graph_data, "derivation": derivation,
                     "snapshot": weather, "start_epoch": start_epoch,
                     "tick_s": tick_s, "ticks": ticks, "airspace": airspace,
                     "storm_cells": storm_cells},
                    self.horizon_days, on_done=self._child_done)
                if self.proc is None:
                    refusal = "no_slot"
            self._note(twin_parallel_refusal=refusal)
            if self.proc is not None:
                self._note(twin_mode="parallel_started")
                return
        # Sequential: compute now, so followers waiting on the lock get
        # the entry as early as possible.
        try:
            self.events = self._compute_here()
            self._store(self.events)
        finally:
            if self.lock is not None:
                self.lock.release()
                self.lock = None
        self._note(twin_mode="sequential",
                   twin_s=round(time.perf_counter() - self.t_begin, 3))

    def finish(self):
        if self.events is not None:
            return self.events
        t_wait = time.perf_counter()
        value = self.proc.result() if self.proc is not None else None
        if value is not None:
            self.events = value
            self._note(twin_mode="parallel",
                       twin_wait_s=round(time.perf_counter() - t_wait, 3),
                       twin_s=round(time.perf_counter() - self.t_begin, 3))
            return self.events
        # The child failed (or never ran): compute it here, exactly as a
        # sequential build would. The lock, if any, is still ours.
        try:
            self.events = self._compute_here()
            self._store(self.events)
        finally:
            if self.lock is not None:
                self.lock.release()
                self.lock = None
        self._note(twin_mode="parallel_failed_sequential",
                   twin_s=round(time.perf_counter() - self.t_begin, 3))
        return self.events

    def abort(self) -> None:
        if self.proc is not None:
            self.proc.cancel()
        if self.lock is not None:
            self.lock.release()
            self.lock = None


def _seed_twin_cache(cache_dir, derive_payload, journal_core, weather,
                     start_epoch, tick_s, airspace, storm_cells,
                     arrival_events) -> bool:
    """An undisrupted run's own arrival events are exactly the twin of
    every disrupted run with the same base: store them (if absent)."""
    payload = _twin_payload(derive_payload, journal_core, weather,
                            start_epoch, tick_s, airspace, storm_cells)
    key = _cache_key(payload)
    path = Path(cache_dir) / f"twin-{key}.json"
    if _twin_read(path, key) is not None:
        return False
    return buildcache.write_json_atomic(path, "twin",
                                        _twin_text(key, arrival_events))


def build_record(graph_data: dict, graph_hash: str, graph_version,
                 journal_core: dict, weather: dict | None,
                 weather_reason: str | None, cache_dir=None,
                 stats: dict | None = None,
                 parallel: bool | None = None) -> dict:
    """Derive + simulate + assemble the stored run record for an
    already-validated journal core.

    ``cache_dir`` (optional): enables the content-addressed derive and
    twin caches above; the returned record is byte-identical either
    way. ``parallel``: run a missing undisrupted twin in a second
    process while this one simulates the disrupted run (None = when a
    cache is in use; True/False force it; resource limits and any
    failure fall back to sequential). ``stats`` (optional): filled with
    per-stage timings and cache hit/miss labels - it never enters the
    record."""
    from .simulate import parse_ts

    # Graph extra sidecar (opt-in): only a journal core that carries an
    # ``extra`` selection derives and simulates on the merged sim view;
    # every other body runs on ``graph_data`` exactly as before.
    extra_meta = None
    if journal_core.get("extra"):
        graph_data, extra_meta = _extra_sim_graph(graph_data, graph_hash,
                                                  journal_core)
    start_epoch = parse_ts(journal_core["start_at"])
    tick_s = {"hour": 3600, "day": 86400, "week": 604800,
              "month": 2592000}[journal_core["tick"]]
    horizon_end = start_epoch + tick_s * journal_core["ticks"]
    derivation, derive_payload = _derive_cached(
        graph_data, journal_core, start_epoch, horizon_end, cache_dir,
        stats)
    # SIM-native shocks (journal keys, additive; absent on
    # bodies without them): environment for BOTH the run and its twin —
    # the twin differs only in the served disruption set.
    airspace = journal_core.get("airspace") or []
    storm_cells = journal_core.get("storm_cells") or []
    # Twin accounting, computed AT CREATION:
    # the undisrupted twin (same derivation, empty disruption set) is
    # simulated once here and only the per-node added-delay statistics
    # are stored — reads never re-simulate. Undisrupted runs skip it
    # (their twin is themselves; every figure would be zero).
    twin_src = None
    if journal_core["disruptions"]:
        twin_src = _TwinSource(graph_data, derivation, derive_payload,
                               journal_core, weather, start_epoch, tick_s,
                               airspace, storm_cells, cache_dir, stats,
                               parallel)
        try:
            twin_src.begin()
        except BaseException:
            twin_src.abort()
            raise
    try:
        t_sim = time.perf_counter()
        # trim_before: the pre-roll spin-up is dropped as it is
        # produced (what _trim_segments below keeps, nothing more).
        sim = simulate(graph_data, derivation, journal_core["disruptions"],
                       weather, start_epoch, tick_s, journal_core["ticks"],
                       airspace=airspace, storm_cells=storm_cells,
                       trim_before=start_epoch)
        if stats is not None:
            stats["simulate_s"] = round(time.perf_counter() - t_sim, 3)
        sim_delay_by_node: dict = {}
        if twin_src is not None:
            from .twin import added_delay_by_node

            twin_events = twin_src.finish()
            # The consistency block's
            # inputs are CORE-scope statistics. Derivation has already
            # enforced the tier contract, so an untiered graph is all
            # CORE here and a tiered graph's CORE set is exact.
            core_nodes = {n["id"] for n in graph_data["nodes"]
                          if n.get("tier", "core") == "core"}
            sim_delay_by_node = added_delay_by_node(
                sim, {"arrival_events": twin_events},
                core_nodes=core_nodes)
            twin_events = None
        elif cache_dir is not None:
            seeded = _seed_twin_cache(
                cache_dir, derive_payload, journal_core, weather,
                start_epoch, tick_s, airspace, storm_cells,
                sim["arrival_events"])
            if stats is not None:
                stats["twin_seeded"] = seeded
    except BaseException:
        if twin_src is not None:
            twin_src.abort()
        raise
    prov = provenance_line(graph_version, derivation["split"], weather,
                           weather_reason, air=derivation.get("air"))
    svc_prov: dict = {}
    if derivation.get("services"):
        sv = derivation["services"]
        sel = sv.get("selection") or {}
        prov += (
            f" SERVICES LAYER (opt-in, sidecar sha256 "
            f"{str(sel.get('file_sha256'))[:12]}): {sel.get('services_selected')} "
            f"service rotations ({', '.join(sel.get('eras') or [])}) give "
            f"{sv.get('lanes_created')} created and {sv.get('lanes_replaced')} "
            f"re-routed through routes; rotations are SOURCED with provenance, "
            f"chokepoint placement beyond a printed marker and the frequency "
            f"of created routes are MODELLED.")
        svc_prov = {"services": sv}
    if derivation.get("lane_geometry"):
        lgb = derivation["lane_geometry"]
        sel = lgb.get("selection") or {}
        prov += (
            f" LANE GEOMETRY (opt-in, sidecar sha256 {str(sel.get('lane_geometry_content_sha256'))[:12]}, "
            f"sea routes {str(sel.get('sea_routes_content_sha256'))[:12]}): "
            f"{lgb.get('lanes_on_geometry')} of {lgb.get('sea_lanes')} sea lanes carry vessels placed "
            f"along MODELLED lane polylines; the other {lgb.get('lanes_without_geometry')} carry none. "
            f"All vessels are SIMULATED.")
        svc_prov = {**svc_prov, "lane_geometry": lgb}
    if extra_meta is not None:
        prov += (
            f" GRAPH EXTRA SIDECAR (opt-in, sha256 "
            f"{str(extra_meta.get('file_sha256'))[:12]}): "
            f"{extra_meta.get('places_added')} places and "
            f"{extra_meta.get('legs_added')} legs added as SHELL (routing "
            f"and simulation only, shares MODELED); "
            f"{extra_meta.get('legs_withheld_core_ext')} core-core legs "
            f"withheld from the sim view.")
        svc_prov = {**svc_prov, "extra": extra_meta}
    return {
        "sim_run_id": None,  # filled by the caller (id from journal core)
        "status": "complete",
        "journal": journal_core,
        "derivation": derivation,
        "segments": _trim_segments(sim["segments"], start_epoch),
        "node_series": sim["node_series"],
        "aux_paths": sim.get("aux_paths", {}),
        "reassignment_accounting": sim.get("reassignment_accounting", {}),
        "sim_delay_by_node": sim_delay_by_node,
        "weather": weather,
        "weather_reason": weather_reason,
        "weather_window": _weather_window(weather),
        "node_positions": {n["id"]: [float(n["lat"]), float(n["lon"])]
                           for n in graph_data["nodes"]},
        "provenance": {"line": prov, "split": derivation["split"],
                       "basis": derivation["basis"],
                       "tier": derivation.get("tier"),
                       "statistics_scope": derivation.get(
                           "statistics_scope", "CORE"),
                       "headway_coarsening": derivation["headway_coarsening"],
                       **svc_prov},
        "created_sim_time": fmt_ts(start_epoch),
    }
