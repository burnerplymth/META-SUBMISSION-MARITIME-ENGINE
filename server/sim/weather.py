"""Open-Meteo weather snapshot.

At run creation the server fetches, in batched multi-location calls:
forecast wind (speed + direction) at every graph node point, and
marine wave height at every sea node point and every sea lane
midpoint. The series are pinned into the run record with source +
retrieved_at + a snapshot hash that folds into the journal core, so
replay reads only the pinned snapshot (determinism).

Honesty rules: on ANY failure — network, HTTP error, malformed
payload, or the ``SUPERMAP_SIM_WEATHER=0`` kill switch — this module
returns ``(None, reason)``. It never raises into run creation and never
fabricates a value. Data licence: CC BY 4.0, attribution "Weather data
by Open-Meteo.com" travels with the snapshot.

The fetch function is injectable (``fetch_json(url, params) -> parsed
JSON``) so the module can run fully offline against recorded-shape data.
"""
from __future__ import annotations

import math
import os
from datetime import datetime, timezone

try:
    from engine.canonical import canonical_json, sha256_hex
except Exception:  # imported as server.sim.* from the repo root instead
    from server.engine.canonical import canonical_json, sha256_hex  # type: ignore

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"
SOURCE = "Open-Meteo"
ATTRIBUTION = "Weather data by Open-Meteo.com (CC BY 4.0)"
BATCH = 50

# --- Storm-cell field ------------------------------------------------
# The pinned cell field: the snapshot's point series are ALSO binned
# into a regular lat/lon cell grid at absolute 6-hour steps. INTERFACE
# LAW: cell identity / quantisation lives ONLY in this module — every
# other consumer (simulate, the journal, runner, stored segments)
# sees weather exclusively through sample_at / leg_sample /
# cells_at_step below, never a cell id, the grid resolution, or a
# step index. NOTE: this field is unrelated to the journal
# ``storm_cells`` constructs (authored moving circles); those keep
# working byte-identically alongside it.
CELL_SIZE_DEG = 2.5
CELL_STEP_H = 6
CELL_STEP_S = CELL_STEP_H * 3600


# Run creation happens inside a request, so a dead network must fail FAST
# into the labelled "weather unavailable" absence (fetch_snapshot returns
# (None, reason)): 3 s to connect, 8 s for the whole call. A flat
# 30 s per phase would hold POST /sim/run for ~66 s with no route out. The
# thread deadline also covers what httpx's phase timeouts cannot (a
# resolver or black-holed route that never returns). Injected fetchers
# (offline fixtures) never pass through here.
FETCH_CONNECT_S = 3.0
FETCH_TOTAL_S = 8.0


def _default_fetch_json(url: str, params: dict):
    import threading
    import httpx
    out: dict = {}

    def work():
        try:
            resp = httpx.get(url, params=params, timeout=httpx.Timeout(
                FETCH_TOTAL_S, connect=FETCH_CONNECT_S))
            resp.raise_for_status()
            out["json"] = resp.json()
        except BaseException as exc:  # handed to the caller's thread
            out["exc"] = exc

    t = threading.Thread(target=work, name="sim-weather-fetch", daemon=True)
    t.start()
    t.join(FETCH_TOTAL_S)
    if t.is_alive():
        raise TimeoutError(f"weather fetch exceeded {FETCH_TOTAL_S:g} s")
    if "exc" in out:
        raise out["exc"]
    return out["json"]


def _chunks(seq: list, n: int):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _series(block: dict, var: str) -> list:
    hourly = block.get("hourly") or {}
    vals = hourly.get(var)
    if not isinstance(vals, list):
        raise ValueError(f"missing hourly.{var}")
    return vals


def fetch_snapshot(graph_data: dict, lanes: list[dict] | None = None,
                   fetch_json=None, retrieved_at: str | None = None
                   ) -> tuple[dict | None, str | None]:
    """Fetch + pin a weather snapshot. Returns ``(snapshot, None)`` on
    success or ``(None, reason)`` on any failure — never raises.

    Sea lane midpoints are the direct great-circle midpoints of the
    graph's sea edges (sampling points for the wave series)."""
    if os.environ.get("SUPERMAP_SIM_WEATHER") == "0":
        return None, "weather disabled by SUPERMAP_SIM_WEATHER=0"
    fetch = fetch_json or _default_fetch_json
    nodes = {n["id"]: n for n in graph_data["nodes"]}
    node_ids = sorted(nodes)
    sea_node_ids = sorted(nid for nid in node_ids if nodes[nid]["mode"] == "sea")
    if lanes is None:
        from .derive import lane_key
        from .geometry import slerp_samples
        lanes = []
        for e in graph_data["edges"]:
            if e["mode"] != "sea":
                continue
            a = (float(nodes[e["from"]]["lat"]), float(nodes[e["from"]]["lon"]))
            b = (float(nodes[e["to"]]["lat"]), float(nodes[e["to"]]["lon"]))
            pts = slerp_samples(a, b)
            mid = pts[len(pts) // 2]
            lanes.append({"key": lane_key("sea", e["from"], e["to"]),
                          "mode": "sea", "flow_only": False,
                          "midpoint": [mid[0], mid[1]]})
    sea_lanes = [ln for ln in lanes if ln["mode"] == "sea" and not ln["flow_only"]]
    try:
        # Wind at every node point (batched multi-location).
        wind: dict[str, dict] = {}
        for batch in _chunks(node_ids, BATCH):
            lats = ",".join(str(float(nodes[i]["lat"])) for i in batch)
            lons = ",".join(str(float(nodes[i]["lon"])) for i in batch)
            data = fetch(FORECAST_URL, {
                "latitude": lats, "longitude": lons,
                "hourly": "wind_speed_10m,wind_direction_10m",
                "wind_speed_unit": "kn", "forecast_days": 16,
                "timezone": "UTC"})
            blocks = data if isinstance(data, list) else [data]
            if len(blocks) != len(batch):
                raise ValueError("forecast batch size mismatch")
            for nid, block in zip(batch, blocks):
                wind[nid] = {
                    "times": _series(block, "time"),
                    "wind_kn": _series(block, "wind_speed_10m"),
                    "wind_dir": _series(block, "wind_direction_10m"),
                }
        # Waves at sea node points + sea lane midpoints (marine API).
        wave_points: list[tuple[str, float, float]] = [
            (f"node:{nid}", float(nodes[nid]["lat"]), float(nodes[nid]["lon"]))
            for nid in sea_node_ids]
        wave_points += [
            (f"lane:{ln['key']}", float(ln["midpoint"][0]), float(ln["midpoint"][1]))
            for ln in sea_lanes]
        waves: dict[str, dict] = {}
        for batch in _chunks(wave_points, BATCH):
            lats = ",".join(str(p[1]) for p in batch)
            lons = ",".join(str(p[2]) for p in batch)
            data = fetch(MARINE_URL, {
                "latitude": lats, "longitude": lons,
                "hourly": "wave_height", "forecast_days": 16,
                "timezone": "UTC"})
            blocks = data if isinstance(data, list) else [data]
            if len(blocks) != len(batch):
                raise ValueError("marine batch size mismatch")
            for (key, _la, _lo), block in zip(batch, blocks):
                waves[key] = {"times": _series(block, "time"),
                              "wave_m": _series(block, "wave_height")}
    except Exception as exc:  # any failure -> honest absence, never a raise
        return None, f"Open-Meteo fetch failed: {type(exc).__name__}: {exc}"

    retrieved = retrieved_at or datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    node_series: dict[str, dict] = {}
    for nid in node_ids:
        entry = {"lat": float(nodes[nid]["lat"]), "lon": float(nodes[nid]["lon"]),
                 **wind[nid], "wave_m": None}
        w = waves.get(f"node:{nid}")
        if w is not None:
            entry["wave_m"] = w["wave_m"]
            entry["wave_times"] = w["times"]
        node_series[nid] = entry
    lane_series = {ln["key"]: waves[f"lane:{ln['key']}"] for ln in sea_lanes}
    snapshot = {
        "source": SOURCE, "attribution": ATTRIBUTION,
        "retrieved_at": retrieved,
        "nodes": node_series, "lane_midpoints": lane_series,
    }
    # Storm-cell field, derived from the point series above (zero new
    # fetches): node points carry wind + wave, sea-lane midpoints
    # (threaded through via sea_lanes' midpoint coordinates) carry
    # wave only. The cells block folds into snapshot_hash below (the
    # hash covers the whole snapshot minus the hash field), so a
    # cells-carrying snapshot hashes differently from a point-only
    # one and weather run ids minted by this build differ from the
    # dormant point-only build's.
    snapshot["cells"] = _build_cells(node_series, lane_series, sea_lanes)
    snapshot["hash"] = snapshot_hash(snapshot)
    return snapshot, None


def snapshot_hash(snapshot: dict) -> str:
    """sha256 over the canonical snapshot (hash field excluded)."""
    core = {k: v for k, v in snapshot.items() if k != "hash"}
    return sha256_hex(canonical_json(core))


def hour_index(times: list[str], epoch: float) -> int | None:
    """Index of the snapshot hour containing ``epoch`` (ISO times)."""
    if not times:
        return None
    # Snapshot times are hourly ISO strings; binary search on the
    # parsed hour floor keeps this O(log n) and allocation-light.
    from datetime import datetime as _dt
    target = _dt.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:00")
    lo, hi = 0, len(times) - 1
    if target[:13] < times[0][:13] or target[:13] > times[-1][:13]:
        return None
    while lo <= hi:
        mid = (lo + hi) // 2
        cur = times[mid][:13]
        if cur == target[:13]:
            return mid
        if cur < target[:13]:
            lo = mid + 1
        else:
            hi = mid - 1
    return None


def node_weather_at(snapshot: dict | None, node_id: str,
                    epoch: float) -> dict | None:
    """Weather in force at a node at ``epoch``: {wind_kn, wind_dir,
    wave_m} or None when absent / outside the forecast window."""
    if not snapshot:
        return None
    entry = (snapshot.get("nodes") or {}).get(node_id)
    if not entry:
        return None
    idx = hour_index(entry["times"], epoch)
    if idx is None:
        return None
    out = {"wind_kn": entry["wind_kn"][idx], "wind_dir": entry["wind_dir"][idx],
           "wave_m": None}
    if entry.get("wave_m") and entry.get("wave_times"):
        widx = hour_index(entry["wave_times"], epoch)
        if widx is not None:
            out["wave_m"] = entry["wave_m"][widx]
    return out


def lane_weather_at(snapshot: dict | None, lane_key: str, node_id: str,
                    epoch: float) -> dict | None:
    """Weather in force for a leg: wave from the lane midpoint series
    (sea), wind from the origin node's series (direction for the air
    headwind rule). Falls back to node series for wave."""
    if not snapshot:
        return None
    base = node_weather_at(snapshot, node_id, epoch)
    if base is None:
        return None
    out = dict(base)
    mid = (snapshot.get("lane_midpoints") or {}).get(lane_key)
    if mid is not None:
        idx = hour_index(mid["times"], epoch)
        if idx is not None:
            out["wave_m"] = mid["wave_m"][idx]
    return out


# --- Storm-cell field: construction (all cell logic stays here) ------

def _epoch_of_iso(text: str) -> float:
    dt = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _iso_of_epoch(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _cell_indices(lat: float, lon: float) -> tuple[int, int]:
    return (math.floor(float(lat) / CELL_SIZE_DEG),
            math.floor(float(lon) / CELL_SIZE_DEG))


def _cell_key(indices: tuple[int, int]) -> str:
    return f"{indices[0]},{indices[1]}"


def _parse_cell_key(key: str) -> tuple[int, int]:
    a, b = key.split(",")
    return int(a), int(b)


def _cell_bbox(indices: tuple[int, int]) -> list:
    """[west, south, east, north] of a cell, degrees."""
    i, j = indices
    south, west = i * CELL_SIZE_DEG, j * CELL_SIZE_DEG
    return [west, south, west + CELL_SIZE_DEG, south + CELL_SIZE_DEG]


def _cell_centre(indices: tuple[int, int]) -> tuple[float, float]:
    """(lat, lon) of the cell centre."""
    i, j = indices
    return ((i + 0.5) * CELL_SIZE_DEG, (j + 0.5) * CELL_SIZE_DEG)


def _cell_radius_km(indices: tuple[int, int]) -> float:
    """Half the cell diagonal at the centre latitude, km."""
    centre_lat, _ = _cell_centre(indices)
    dx = CELL_SIZE_DEG * 111.320 * math.cos(math.radians(centre_lat))
    dy = CELL_SIZE_DEG * 110.574
    return 0.5 * math.sqrt(dx * dx + dy * dy)


def _build_cells(node_series: dict, lane_series: dict,
                 sea_lanes: list[dict]) -> dict:
    """Bin the pinned point series into the storm-cell field.

    Points: every node series entry (wind + wave) and every sea-lane
    midpoint (wave only — lane points carry no wind in the snapshot,
    so their wind stays absent and aggregation treats it as missing).
    Steps are ABSOLUTE 6-hour steps (step = floor(epoch / 21600));
    ``times`` holds the sorted ISO start of every step containing at
    least one hourly sample, and each cell's sample list is aligned
    to ``times`` (None where the cell has no sample that step).

    Aggregation per (cell, step) is deterministic: points iterate in
    sorted point-key order, hours in series order; wind_kn / wave_m
    take the max, and wind_dir is the direction of the max-wind
    sample (first wins on ties)."""
    # Point = (point_key, lat, lon, wind_obs, wave_obs) with obs as
    # (epoch, value...) tuples in series order.
    points: list[tuple] = []
    for nid, entry in node_series.items():
        wind_obs = [(_epoch_of_iso(t), wk, wd) for t, wk, wd in zip(
            entry["times"], entry["wind_kn"], entry["wind_dir"])]
        wave_obs = []
        if entry.get("wave_m") is not None:
            wave_obs = [(_epoch_of_iso(t), wv) for t, wv in zip(
                entry.get("wave_times") or entry["times"],
                entry["wave_m"])]
        points.append((f"node:{nid}", float(entry["lat"]),
                       float(entry["lon"]), wind_obs, wave_obs))
    lane_coords = {ln["key"]: ln["midpoint"] for ln in sea_lanes}
    for key, series in lane_series.items():
        coord = lane_coords.get(key)
        if coord is None:
            continue
        wave_obs = [(_epoch_of_iso(t), wv) for t, wv in zip(
            series["times"], series["wave_m"])]
        points.append((f"lane:{key}", float(coord[0]), float(coord[1]),
                       [], wave_obs))
    points.sort(key=lambda p: p[0])

    acc: dict[tuple[str, int], dict] = {}
    steps: set[int] = set()
    cells_seen: set[str] = set()
    for _key, lat, lon, wind_obs, wave_obs in points:
        indices = _cell_indices(lat, lon)
        ck = _cell_key(indices)
        cells_seen.add(ck)
        for epoch, wind_kn, wind_dir in wind_obs:
            step = math.floor(epoch / CELL_STEP_S)
            steps.add(step)
            sample = acc.setdefault(
                (ck, step),
                {"wind_kn": None, "wind_dir": None, "wave_m": None})
            if wind_kn is not None and (sample["wind_kn"] is None
                                        or float(wind_kn)
                                        > sample["wind_kn"]):
                sample["wind_kn"] = float(wind_kn)
                sample["wind_dir"] = (None if wind_dir is None
                                      else float(wind_dir))
        for epoch, wave_m in wave_obs:
            step = math.floor(epoch / CELL_STEP_S)
            steps.add(step)
            sample = acc.setdefault(
                (ck, step),
                {"wind_kn": None, "wind_dir": None, "wave_m": None})
            if wave_m is not None and (sample["wave_m"] is None
                                       or float(wave_m)
                                       > sample["wave_m"]):
                sample["wave_m"] = float(wave_m)
    step_list = sorted(steps)
    times = [_iso_of_epoch(s * CELL_STEP_S) for s in step_list]
    pos = {s: i for i, s in enumerate(step_list)}
    field: dict[str, list] = {}
    for ck in sorted(cells_seen, key=_parse_cell_key):
        field[ck] = [None] * len(times)
    for (ck, step), sample in acc.items():
        field[ck][pos[step]] = sample
    return {"size_deg": CELL_SIZE_DEG, "step_h": CELL_STEP_H,
            "times": times, "field": field}


# --- Storm-cell field: sampling interface ----------------------------
# The ONLY cell-aware surface outside this module. Callers pass plain
# coordinates / epochs and receive plain samples; no cell id, grid
# resolution, or step index ever crosses this boundary.

def _cells_block(snapshot: dict | None) -> dict | None:
    if not snapshot:
        return None
    cells = snapshot.get("cells")
    if not isinstance(cells, dict) or not cells.get("field"):
        return None
    return cells


def _step_pos(cells: dict, epoch: float) -> int | None:
    """Position in the cells ``times`` list of the step containing
    ``epoch``, or None when that step has no samples anywhere."""
    step_iso = _iso_of_epoch(math.floor(epoch / CELL_STEP_S) * CELL_STEP_S)
    try:
        return cells["times"].index(step_iso)
    except ValueError:
        return None


def sample_at(snapshot: dict | None, lat: float, lon: float,
              epoch: float) -> dict | None:
    """The cell sample {wind_kn, wind_dir, wave_m} in force at
    (lat, lon) at ``epoch`` — the sample of the containing cell at
    the step containing epoch — or None when there are no cells, no
    sample for that cell/step, or the point is outside coverage."""
    cells = _cells_block(snapshot)
    if cells is None:
        return None
    pos = _step_pos(cells, epoch)
    if pos is None:
        return None
    series = cells["field"].get(_cell_key(_cell_indices(lat, lon)))
    if not series or pos >= len(series) or series[pos] is None:
        return None
    return dict(series[pos])


def leg_sample(snapshot: dict | None, points: list,
               epoch: float) -> dict | None:
    """Sample a leg's polyline points against the cell field.

    None when the snapshot carries no cells (or no point is sampled —
    the caller then falls back to point weather). Otherwise
    {sample, bend, points_sampled}: the GOVERNING sample is the point
    sample maximal by (wave_m or 0, wind_kn or 0) lexicographic (its
    wind_dir travels with it; first point wins ties), and ``bend`` is
    that governing point's cell — centre (lat, lon) + radius_km (half
    the cell diagonal at the centre latitude) — returned only when
    the governing sample is non-null. Whether the sample is severe
    enough to bend for is the caller's judgement."""
    cells = _cells_block(snapshot)
    if cells is None:
        return None
    pos = _step_pos(cells, epoch)
    if pos is None:
        return None
    best = None  # (key, sample, indices)
    sampled = 0
    for lat, lon in points:
        indices = _cell_indices(lat, lon)
        series = cells["field"].get(_cell_key(indices))
        if not series or pos >= len(series) or series[pos] is None:
            continue
        sample = series[pos]
        sampled += 1
        key = (sample.get("wave_m") or 0.0, sample.get("wind_kn") or 0.0)
        if best is None or key > best[0]:
            best = (key, sample, indices)
    if best is None:
        return None
    _key, sample, indices = best
    centre_lat, centre_lon = _cell_centre(indices)
    bend = {"lat": centre_lat, "lon": centre_lon,
            "radius_km": _cell_radius_km(indices)}
    return {"sample": dict(sample), "bend": bend,
            "points_sampled": sampled}


def cells_at_step(snapshot: dict | None, epoch: float) -> dict:
    """All cells with a non-null sample at the step containing
    ``epoch``: {step: {from, to} | None, cells: [{bbox, wind_kn,
    wave_m, severe}]}. Empty list + step None when there are no cells
    or the step is outside coverage. ``severe`` uses the SEA display
    thresholds (wind > 40 kn or wave > 3.5 m) — display grading only.
    """
    cells = _cells_block(snapshot)
    if cells is None:
        return {"step": None, "cells": []}
    pos = _step_pos(cells, epoch)
    if pos is None:
        return {"step": None, "cells": []}
    step_from = cells["times"][pos]
    step = {"from": step_from,
            "to": _iso_of_epoch(_epoch_of_iso(step_from) + CELL_STEP_S)}
    out = []
    for ck in sorted(cells["field"], key=_parse_cell_key):
        series = cells["field"][ck]
        if pos >= len(series) or series[pos] is None:
            continue
        sample = series[pos]
        wind, wave = sample.get("wind_kn"), sample.get("wave_m")
        out.append({
            "bbox": _cell_bbox(_parse_cell_key(ck)),
            "wind_kn": wind, "wave_m": wave,
            # Display grading only (sea thresholds), not a sim input.
            "severe": (wind or 0.0) > 40.0 or (wave or 0.0) > 3.5,
        })
    return {"step": step, "cells": out}
