"""PORT TWIN plug-in registry (machine-readable).

Every number in server/portsim that is not an identity (Little's law,
stock balance, FIFO order) lives here with: name, value, unit, where it is
used, why, status, source and what real data would replace it. The
registry hash is part of every run id, so a stored run is re-derivable
against the exact values that produced it.

Status vocabulary:
  SOURCED    taken from a cited dataset (the dataset is in the repo or the
             citation is given)
  CAL        derived from pinned repo data by a stated rule
  LINKED     value read from the chain engine's own registry so the two
             models cannot drift (server/engine2/plugins.py)
  TEMPORARY  behavioural / placeholder assumption, bracketed by the cited
             range in `source`, to be replaced
  NUMERICAL  discretisation choice, not a model claim

There is no RNG anywhere in server/portsim. Ranges are named variants
(low / base / high service times), never probabilities.
"""
from __future__ import annotations

import hashlib
import json
import math

MODEL_VERSION = "portsim/1.1.0"
MODEL_LABEL = "PORT TWIN (deterministic discrete-event simulation)"

PW_CLASS_TO_KIND = {"container": "container", "dry_bulk": "bulk",
                    "general_cargo": "general", "roro": "ro-ro",
                    "tanker": "tanker"}
KIND_ORDER = ("container", "bulk", "tanker", "ro-ro", "general")
KINDS = KIND_ORDER + ("unknown",)

VARIANTS = ("low", "base", "high")

_P: list[dict] = []


def _add(name, value, unit, where, why, status, source, replace_with,
         bounds=None):
    _P.append({"name": name, "value": value, "unit": unit,
               "where_used": where, "why_needed": why, "status": status,
               "source": source, "replace_with": replace_with,
               "bounds": bounds})


def _engine2_value(name, fallback):
    try:
        try:
            from engine2.plugins import plug
        except Exception:  # pragma: no cover - alternate import root
            from server.engine2.plugins import plug  # type: ignore
        return plug(name)
    except Exception:
        return fallback


_MU = float(_engine2_value("mu_max_ratio", 1.3))
_PSI = float(_engine2_value("teu_per_call", 2359.35))

# ---------------------------------------------------------------- link to
# the chain engine ---------------------------------------------------------
_add("mu_max_ratio", _MU, "service capacity / baseline load (dimensionless)",
     "profile.anchor: with anchor=engine2 the berth service times are scaled "
     "per cargo kind so that total berth capacity = mu_max_ratio x baseline "
     "calls/day, the same mu_max the chain engine uses at place level",
     "makes the port twin and the chain engine agree in aggregate; a port "
     "whose profile berths would give another capacity is rescaled, not "
     "silently trusted (anchor=structural turns this off)",
     "LINKED",
     "server/engine2/plugins.py mu_max_ratio (TEMPORARY there: assumed "
     "default 1.3 x lambda0; the Ever Given consistency check "
     "is the only evidence)",
     "per-place demonstrated peak service rate (PortWatch daily 99th pct / mean)",
     [1.0, 3.0])

_add("teu_per_call_fallback", _PSI, "TEU per vessel call",
     "profile.default: TEU per container call when the port has no "
     "container call rate or its place capacity is not in TEU/day; also the "
     "conversion that turns place queue_cap into an anchorage size",
     "one global conversion keeps DEFAULT PROFILE ports consistent with "
     "the chain engine's vessel-call unit",
     "LINKED",
     "server/engine2/plugins.py teu_per_call (CAL there: median of place "
     "capacity / (PortWatch container calls / 365) over 77 core ports)",
     "per-port TEU per call from PortWatch daily series by vessel type",
     [500.0, 10000.0])

_add("teu_per_call_bounds", [200.0, 25000.0], "TEU per vessel call",
     "profile.default: a per-port TEU/call derived as place capacity / "
     "PortWatch container calls outside these bounds is replaced by the "
     "fallback and the replacement is reported in the profile notes",
     "PortWatch AIS call counts on tiny ports can give absurd ratios",
     "NUMERICAL", "largest container ships carry about 24,000 TEU",
     "none needed once per-port series are used")

# ------------------------------------------------------------ vessel classes
_add("vessel_classes",
     {
         "container": [
             {"name": "feeder", "share": 0.35, "work_rel": 0.30,
              "rate_rel": 1.0, "cranes": 2, "loa_m": 180.0},
             {"name": "neopanamax", "share": 0.40, "work_rel": 1.00,
              "rate_rel": 1.0, "cranes": 3, "loa_m": 300.0},
             {"name": "ultra-large", "share": 0.25, "work_rel": 2.20,
              "rate_rel": 1.0, "cranes": 5, "loa_m": 380.0}],
         "bulk": [
             {"name": "handysize", "share": 0.45, "work_rel": 0.35,
              "rate_rel": 0.5, "cranes": 0, "loa_m": 175.0},
             {"name": "panamax", "share": 0.35, "work_rel": 1.00,
              "rate_rel": 0.8, "cranes": 0, "loa_m": 225.0},
             {"name": "capesize", "share": 0.20, "work_rel": 2.40,
              "rate_rel": 1.0, "cranes": 0, "loa_m": 290.0}],
         "tanker": [
             {"name": "coastal", "share": 0.40, "work_rel": 0.35,
              "rate_rel": 0.5, "cranes": 0, "loa_m": 150.0},
             {"name": "aframax", "share": 0.40, "work_rel": 1.00,
              "rate_rel": 0.8, "cranes": 0, "loa_m": 245.0},
             {"name": "vlcc", "share": 0.20, "work_rel": 2.60,
              "rate_rel": 1.0, "cranes": 0, "loa_m": 330.0}],
         "ro-ro": [
             {"name": "short-sea", "share": 0.50, "work_rel": 0.60,
              "rate_rel": 0.7, "cranes": 0, "loa_m": 150.0},
             {"name": "deep-sea", "share": 0.50, "work_rel": 1.40,
              "rate_rel": 1.0, "cranes": 0, "loa_m": 200.0}],
         "general": [
             {"name": "coaster", "share": 0.60, "work_rel": 0.50,
              "rate_rel": 0.6, "cranes": 0, "loa_m": 120.0},
             {"name": "multipurpose", "share": 0.40, "work_rel": 1.50,
              "rate_rel": 1.0, "cranes": 0, "loa_m": 180.0}]},
     "share, relative work, relative rate, cranes, metres",
     "arrivals.py: every arrival of a PortWatch class is assigned a size "
     "class by a fixed deterministic interleave of these shares (largest "
     "deficit first, no RNG); work_rel is normalised so the share-weighted "
     "mean is exactly 1 and the port's mean work per call (TEU or tonnes) "
     "comes from the profile / PortWatch, not from here",
     "a fixed class mix and fixed quantile work content replace random "
     "draws (doctrine: no Monte Carlo); the ladder gives service-time "
     "variability without randomness",
     "TEMPORARY",
     "round size-class conventions: container feeder < 3,000 TEU, "
     "neo-panamax ~ 10-14,000 TEU, ultra-large ~ 18-24,000 TEU; dry bulk "
     "handysize / panamax / capesize; tanker coastal / aframax / VLCC; LOA "
     "typical values. The share vectors are placeholders (no port-level "
     "size-mix dataset is in the repo)",
     "PortWatch daily vessel-type series and AIS draught / length classes "
     "per port; terminal call-size distribution")

# -------------------------------------------------------------- productivity
_add("crane_rate_teu_per_h", 30.0, "TEU (container moves) per crane-hour",
     "core.py: container work rate = cranes assigned x this value (cranes "
     "per size class above); crane_scale scenarios multiply it",
     "gross ship-to-shore crane productivity is the standard terminal "
     "service-rate measure; moves are treated as TEU-equivalent",
     "TEMPORARY",
     "bracketed by published gross crane rates: 19 moves/crane-hour "
     "(APM Terminals Apapa, 75.6 moves/h berth rate) to 43 (Cai Mep "
     "International Terminal, 183 moves/h berth rate on a 9,038 TEU ship, "
     "5 cranes) per port-trade press reports; BITRE (Australia) five-port "
     "average 26.3 containers/h; World Bank CPPI keeps calls with 10-650 "
     "moves/h berth productivity. Search-snippet level, not re-opened",
     "per-terminal gross crane productivity (JOC / Drewry / terminal "
     "reports); PortWatch-derived berth hours",
     [19.0, 43.0])

_add("bulk_rates_t_per_h",
     {"bulk": 2000.0, "tanker": 3000.0, "ro-ro": 600.0, "general": 300.0},
     "tonnes per berth-hour at rate_rel = 1",
     "core.py: non-container work rate = this x size class rate_rel",
     "non-container cargo has no crane-move unit; tonnes per hour is the "
     "unit the PortWatch tonnage estimates are in",
     "TEMPORARY",
     "round figures inside commonly quoted terminal ranges (dry bulk "
     "loaders and grabs: ~1,000-4,000 t/h; product tanker pumping: "
     "~1,000-5,000 t/h); not individually sourced",
     "terminal-specific loading rates from the port's tariff book / PortWatch "
     "tonnes per call vs berth hours")

_add("service_variants", {"low": 0.85, "base": 1.0, "high": 1.15},
     "service-time multiplier (low = faster, high = slower)",
     "core.py: variant divides the work rate; anchor scaling is computed "
     "for base and then the variant applies on top",
     "ranges are named variants, not probabilities: low / base / high bracket "
     "service times with fixed quantile multipliers",
     "TEMPORARY",
     "+/-15% around base: about half of the 19-43 moves/crane-hour reported "
     "spread around 30 (see crane_rate_teu_per_h), halved because the engine2 "
     "anchor already fixes the level; baseline berth utilisation is "
     "1/mu_max_ratio = 0.77, so the high variant (x1.15) stays below 1 "
     "(0.88) and the low variant gives 0.65",
     "quartiles of the observed per-port berth-hour distribution")

# ----------------------------------------------------------------- movements
_add("movement_times_s",
     {"approach": 7200, "pilot_in_channel": 5400, "pilot_out_channel": 3600,
      "mooring_in_no_channel": 1800, "mooring_out_no_channel": 1800},
     "seconds",
     "core.py: approach = outer limit to anchorage; inbound / outbound "
     "movement between anchorage and alongside (pilotage through the "
     "channel when the profile says a channel is present, mooring only "
     "otherwise)",
     "gives the animation a visible approach and a berth reservation "
     "interval; ships in transit do not contend for the channel",
     "TEMPORARY",
     "typical pilotage / mooring durations of 1-2 h; no dataset in repo",
     "AIS-derived anchorage-to-berth transit times per port")

_add("yard",
     {"import_share": 0.5, "dwell_days": 4.0, "threshold": 0.70,
      "min_throttle": 0.6, "target_fill": 0.55,
      "profile_fill_range": [0.10, 0.90], "tick_s": 3600},
     "import_share dimensionless; dwell_days; fill fractions; tick seconds",
     "core.py yard stock: discharged TEU enter the yard at import_share x "
     "berth TEU rate, leave at stock / dwell_days (first-order evacuation, "
     "times the yard-outage factor); above `threshold` fill the berth work "
     "rate falls linearly to `min_throttle` at fill 1; with no usable "
     "profile yard capacity the capacity is set so baseline fill = "
     "target_fill; a profile yard whose baseline fill falls outside "
     "profile_fill_range is anchored the same way and the replacement is "
     "reported; the throttle is evaluated every tick_s (fixed-step "
     "controller)",
     "yard occupancy throttling quay productivity is how LA/Long Beach "
     "saturated in 2021",
     "TEMPORARY",
     "threshold 0.70: Philippine Ports Authority calls 70% the optimal yard "
     "utilisation and >80% a congestion risk; Indonesian port regulation "
     "sets a 65% yard occupancy ceiling (search snippets); dwell 4.0 d "
     "inside the San Pedro Bay series in data/sim_backtest/la_lb/actuals.json "
     "(PMSA truck dwell 2.5 d pre-surge, 7.6-8.4 d at peak; Hapag-Lloyd "
     "import dwell peak 11 d). target_fill is a placeholder. min_throttle "
     "0.6 was CALIBRATED ON ONE EPISODE (Los Angeles / Long Beach, "
     "Jul-Nov 2021): the a-priori value "
     "0.35 over-predicted the autumn-2021 queue 1.8-2.3x; 0.6 gives "
     "1.15-1.3x in sample and 0.83x on the Feb 2021 episode that was not "
     "used. Single-episode fit: treat as TEMPORARY",
     "terminal yard-occupancy vs crane-productivity regression (TOS data); "
     "per-port dwell (PMSA, port authorities)",
     {"threshold": [0.5, 0.9], "dwell_days": [1.0, 12.0]})

_add("anchorage_default",
     {"min_slots": 4, "max_slots": 2000, "rule": "ceil(place queue_cap / teu_per_call)"},
     "vessel slots",
     "profile.default: anchorage capacity when the profile gives none; "
     "waiting vessels beyond it are 'waiting outside' (spillback)",
     "the chain engine's queue_cap is a soft anchorage limit in vessel "
     "calls; using the same number keeps "
     "the spillback point consistent between the two models",
     "LINKED",
     "place queue_cap (3 days of throughput) / engine2 teu_per_call",
     "profile.anchorage.capacity_vessels_est from the port geometry / AIS "
     "anchorage occupancy")

_add("berth_geometry",
     {"default_length_margin": 1.15, "loa_scale_bounds": [0.6, 1.6],
      "berth_count_cap": 400},
     "factor / bounds / count",
     "profile.py: default berth length = margin x longest LOA of the kind; "
     "the profile's mean LOA of a kind (params.mean_loa_m_by_kind, the figure "
     "the port geometry divided the quay by; params.mean_loa_m is the "
     "container value) rescales that kind's LOAs when the ratio is inside "
     "the bounds (else ignored and reported); default berth counts capped. "
     "core.py: a call longer than one berth of a group holds as many berths "
     "of that group as its length spans (classes.berths_needed), up to the "
     "whole group",
     "first-fit by kind needs a length for every berth; a berth count "
     "derived as quay length / mean LOA is a count of mean-length slots, so "
     "vessels longer than the mean must take more than one",
     "TEMPORARY", "engineering margin; no dataset. Berth allocation is ship "
     "length plus about 10% of quay (a 453 m ship on 498 m, MDPI JMSE 7(5) "
     "131, 2019): the model adds no "
     "margin; a berth is quay length / count, at least the mean LOA",
     "surveyed berth lengths")

_add("default_tonnes_per_call",
     {"bulk": 30000.0, "tanker": 50000.0, "ro-ro": 3000.0, "general": 5000.0},
     "tonnes per call",
     "profile.default: used only when PortWatch gives no tonnage for a class "
     "that has calls",
     "work content needs a value", "TEMPORARY",
     "round figures; PortWatch per-class tonnes per call replace them "
     "whenever present", "PortWatch tonnes / calls per class")

_add("arrival_shapes",
     {"even": [1.0] * 24,
      "diurnal": [round(1.0 + 0.3 * math.cos(
          2 * math.pi * (h - 14) / 24.0), 6) for h in range(24)]},
     "relative hourly intensity (mean 1)",
     "arrivals.py: even = constant intensity; diurnal = a fixed daylight "
     "bunching profile (no randomness)",
     "deterministic equal spacing under-states baseline queueing that "
     "arrival bunching causes; the diurnal shape is a deterministic, "
     "labelled way to include some",
     "TEMPORARY", "illustrative +/-30% cosine peaking at 14:00; no dataset",
     "PortWatch hourly / AIS arrival-hour histograms per port")

_add("run_limits",
     {"days_max": 180, "warmup_days_default": 2, "warmup_days_max": 7,
      "sync_vessel_budget": 10000, "frames_max": 2000, "frame_rows_max": 250000, "scenario_items_max": 16},
     "limits",
     "service.py / routes.py: request bounds; runs whose estimated vessel "
     "count exceeds sync_vessel_budget go to the async job path",
     "bounded runtime and response size", "NUMERICAL",
     "typical run time 0.2-0.35 s for 1,900 simulated vessels (Singapore, 14 d, every scenario kind); 10,000 vessels is about 1.5 s with margin for a loaded host",
     "-")

REGISTRY: list[dict] = list(_P)
BY_NAME: dict[str, dict] = {p["name"]: p for p in REGISTRY}


def plug(name: str):
    return BY_NAME[name]["value"]


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


REGISTRY_HASH = sha256(canonical(REGISTRY))


def registry_table() -> list[dict]:
    return [dict(p) for p in REGISTRY]


def normalised_classes(kind: str) -> list[dict]:
    """Size classes of a kind with work_rel normalised so that the
    share-weighted mean work is exactly 1 and shares sum to 1."""
    rows = plug("vessel_classes")[kind]
    ssum = sum(r["share"] for r in rows)
    wmean = sum(r["share"] / ssum * r["work_rel"] for r in rows)
    return [{**r, "share": r["share"] / ssum, "work_rel": r["work_rel"] / wmean}
            for r in rows]
