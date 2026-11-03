"""ENGINE REV 2 CALIBRATED (profile ``rev2c``) plug-in registry.

The default ENGINE REV 2 registry (plugins.py) is NOT edited:
its hash is in every rev-2 run id and pin. This module composes the profile's
registry from it: entries the calibration replaces are overridden, new entries
(the data derivations) are appended, every other entry is carried over as is.
A rev2c run is stamped with this registry's hash and the label
"ENGINE REV 2 CALIBRATED".

Status vocabulary as in plugins.py: SOURCED / CAL / CONVENTION / TEMPORARY /
NUMERICAL. CAL here means "derived from cited, cached data by a stated
derivation that can be reproduced".
"""
from __future__ import annotations

from engine.canonical import canonical_json, sha256_hex

from .plugins import ENGINE_REV, PLUGINS as BASE_PLUGINS

PROFILE = "rev2c"
PROFILE_LABEL = "ENGINE REV 2 CALIBRATED"
CAL_VERSION = "2.1.0"

_OVERRIDE: dict = {}
_NEW: list = []


def _entry(name, value, unit, where, why, status, source, replace_with, bounds=None):
    return {"name": name, "value": value, "unit": unit, "where_used": where,
            "why_needed": why, "status": status, "source": source,
            "replace_with": replace_with, "bounds": bounds}


# ---- entries the calibration replaces -------------------------------------
_OVERRIDE["mu_max_ratio"] = _entry(
    "mu_max_ratio", 1.3, "service rate / baseline load (dimensionless)",
    "engine.py (profile rev2c): ONLY places with no PortWatch series - ground "
    "nodes and air hubs; sea places use mu_max_portwatch / mu_max_fallback",
    "graph capacity is a throughput proxy (annual/365) so it equals the baseline "
    "load; a queue only drains if the demonstrated peak service rate exceeds it",
    "TEMPORARY",
    "assumed default ratio 1.3 (as in the default registry); no "
    "public daily series exists in the repo for ground hubs or air hubs",
    "per-place demonstrated peak service rate for rail/road hubs and air hubs "
    "(daily gate counts, ADS-B movements): 99th percentile of the 7-day mean / its median",
    [1.0, 3.0])

_OVERRIDE["inventory_days_cover"] = _entry(
    "inventory_days_cover", "graph field inventory_days", "days of baseline load",
    "engine.py (profile rev2c): every place EXCEPT those in cover_days_sourced; "
    "S0 = inventory_days x lambda0",
    "inventory is a stock balance; the cover the stock starts with must come from "
    "somewhere",
    "TEMPORARY",
    "graph field (MODELED): 7 d ports, 5 d ground, 3 d air, 0 d chokepoints. Candidate "
    "public figures found and NOT applied (retrieved 2026-10-25, FRED/Census "
    "inventories-to-sales, 2026-08): total business 1.33 months "
    "(https://fred.stlouisfed.org/series/ISRATIO), merchant wholesalers 1.18 months "
    "(https://fred.stlouisfed.org/series/WHLSLRIRSA), retailers 1.36 months "
    "(https://fred.stlouisfed.org/series/RETAILIRSA). They describe downstream "
    "business stocks, not the stock held at a graph port, rail hub or border "
    "crossing, and the graph has no commodity class per place, so applying them "
    "would be a category error",
    "per-place, per-commodity-class stock: terminal dwell time for every port "
    "(only LA/LB are public), warehouse stock for hubs, with a commodity class on "
    "each graph place")

_OVERRIDE["inventory_delay_definition"] = _entry(
    "inventory_delay_definition",
    "delay_d = peak cumulative arrival deficit in days: max_t D(t)/lambda0, "
    "D(t) = integral of (lambda0 - arrivals) dt (+ queue wait)", "days",
    "engine.py (profile rev2c): delay_d of an inventory event. Default rev 2 "
    "reported the length of the run of steps with onward supply short by >= the "
    "hit threshold",
    "delay_d is days late. The old value is the persistence of a shortfall: "
    "a sustained cut returns about the cut length whatever its size (Panama, "
    "215 d half cut: 173 d at New York). The deficit in days is the horizontal "
    "distance between the baseline and the actual cumulative inflow curves, the "
    "same kind of quantity as the FIFO wait used for queue events",
    "CONVENTION", "definition (identity on the cumulative curves)",
    "per-commodity delivery-lateness distribution")

# ---- new entries (data derivations) ------------------------------------------
_NEW.append(_entry(
    "mu_max_portwatch",
    {"window_start": "2022-07-01", "window_end": "2023-10-29", "mean_window_d": 7,
     "quantile": 0.99, "min_points": 300, "container_min_calls_per_day": 2.0,
     "metric_chokepoint": "n_total",
     "metric_port": "portcalls_container if window mean >= container_min_calls_per_day else portcalls"},
    "derivation parameters (dimensionless ratio per place)",
    "engine.py (profile rev2c): mu_max(place) = ratio(place) x lambda0 with "
    "ratio = quantile of the trailing 7-day mean / median of the trailing 7-day "
    "mean of the PortWatch daily count over the window",
    "the peak service rate is the rate the place has demonstrated; a queue drains "
    "only at the spare rate mu_max - lambda0. Replaces the 1.3 guess for every "
    "place that has a PortWatch daily series (11 of 12 chokepoints, all but the "
    "Mozambique Channel; 99 of 100 core ports, all but Tanger Med)",
    "CAL",
    "IMF PortWatch daily layers (Daily_Chokepoints_Data, Daily_Ports_Data on "
    "services9.arcgis.com), retrieved 2026-10-25 by scripts/fetch_portwatch_calibration.py, "
    "cached in data/calibration/portwatch_daily_window.json. The window ends the "
    "day before the earliest 2023 backtest episode. A count is a demonstrated "
    "call/transit rate, not a design capacity; a port's call count is not its TEU "
    "throughput (the ratio is applied to the graph's own lambda0)",
    "berth/lock design capacity or terminal-level throughput peaks where published; "
    "a longer window with disruptions filtered by an event list",
    [1.0, 3.0]))

_NEW.append(_entry(
    "mu_max_fallback",
    "median of the derived ratios of the same class (chokepoint, port)", "dimensionless",
    "engine.py (profile rev2c): sea places with no usable PortWatch series (Mozambique "
    "Channel; Tanger Med; ports whose records are missing or have too few valid days)",
    "every sea place needs a ratio; the class median is the least informative "
    "data-derived value",
    "CAL", "derived from the same cached window (class median)",
    "the place's own daily series"))

_NEW.append(_entry(
    "cover_days_sourced",
    {"sea_losangeles": 4.0, "sea_longbeach": 4.0}, "days of baseline load",
    "engine.py (profile rev2c): S0 = value x lambda0 at these two places",
    "stock held at a terminal = flow x dwell time (Little); the Port of Los Angeles "
    "publishes the dwell time",
    "SOURCED",
    "Port of Los Angeles news release, 29 Nov 2021 "
    "(https://portoflosangeles.org/references/2021-News-Releases/news_112921_DwellFeeHold, "
    "retrieved 2026-10-25): before the mid-2020 import surge local-delivery containers "
    "stayed on terminals 'under four days' and rail containers 'less than two days'; "
    "the 4.0 d upper bound is used for the Los Angeles / Long Beach complex (the "
    "release covers the complex; applied to both places)",
    "terminal dwell time for every port (public for LA/LB only)"))

_NEW.append(_entry(
    "cape_detour_days",
    "route-length difference (km along graph legs) x the bound leg's own days per km",
    "days",
    "routes.py (opt-in routes table): added days of the Cape alternative. UNCHANGED by "
    "the calibration",
    "the services sidecar carries rotations and chokepoint markers, not transit days, "
    "so a published figure cannot replace it per origin-destination pair",
    "TEMPORARY",
    "MODELLED chord geometry. Consistency note only: trade-press claims of 10 to 14 "
    "extra days Far East to North Europe (secondary; no primary UNCTAD or carrier "
    "figure with days was retrievable on 2026-10-25); the chord method gives 14 to 15 d "
    "for Shanghai/Ningbo to the Cape (routes table, rev2_services card)",
    "carrier-published rotation days per service, or a sea-lane network with great-"
    "circle-corrected distances and a design speed"))


def _compose() -> list:
    out = []
    for p in BASE_PLUGINS:
        out.append(dict(_OVERRIDE.get(p["name"], p)))
    out.extend(dict(p) for p in _NEW)
    return out


CAL_PLUGINS = _compose()
CAL_BY_NAME = {p["name"]: p for p in CAL_PLUGINS}


def cal_plug(name):
    return CAL_BY_NAME[name]["value"]


def registry() -> dict:
    return {"engine_rev": ENGINE_REV, "label": PROFILE_LABEL, "profile": PROFILE,
            "version": CAL_VERSION, "plugins": CAL_PLUGINS}


CAL_HASH = sha256_hex(canonical_json(registry()))
CHANGED = sorted(set(_OVERRIDE) | {p["name"] for p in _NEW})
