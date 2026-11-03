"""Vessel classes and service-time arithmetic (shared by profile and core).

Pure functions of the registry and a port's mean work per call. No state.
Work units: TEU for the container kind, tonnes for every other kind. The
two are never added (doctrine: no cross-unit sums).
"""
from __future__ import annotations

import math

from .registry import KIND_ORDER, normalised_classes, plug

HOUR = 3600.0


class PortSimError(Exception):
    """Refusal with a reason. `code` is the house error code; `status` the
    HTTP status a route should use."""

    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def size_classes(kind: str, work_per_call: float, loa_scale: float = 1.0):
    """Size classes of `kind` with absolute work (units), nominal rate
    (units/hour at base variant, before anchoring) and LOA (m)."""
    out = []
    for r in normalised_classes(kind):
        if kind == "container":
            rate = r["cranes"] * float(plug("crane_rate_teu_per_h"))
        else:
            rate = float(plug("bulk_rates_t_per_h")[kind]) * r["rate_rel"]
        out.append({"name": r["name"], "share": r["share"],
                    "work": work_per_call * r["work_rel"],
                    "rate_per_h": rate, "loa_m": r["loa_m"] * loa_scale,
                    "cranes": r["cranes"]})
    return out


def pilot_in_s(channel_present: bool) -> int:
    mv = plug("movement_times_s")
    return int(mv["pilot_in_channel" if channel_present else "mooring_in_no_channel"])


def pilot_out_s(channel_present: bool) -> int:
    mv = plug("movement_times_s")
    return int(mv["pilot_out_channel" if channel_present else "mooring_out_no_channel"])


def mean_work_s(kind: str, work_per_call: float) -> float:
    """Share-weighted mean work duration (seconds) at base variant,
    before anchoring."""
    return sum(c["share"] * c["work"] / c["rate_per_h"] * HOUR
               for c in size_classes(kind, work_per_call))


def berths_needed(loa_m: float, berth_length_m: float, berths: int) -> int:
    """Berths of a group a vessel of length `loa_m` holds when each berth is
    `berth_length_m` of quay (quay length / berth count). One when it fits
    within one berth (the pre-1.1 rule: a vessel up to the berth length took
    exactly one berth); otherwise as many berths as its length spans, at
    most the whole group. The port geometry counts berths as quay length /
    mean LOA of the kind, i.e. mean-length slots; a vessel longer than the
    mean takes more than one slot of quay, a shorter one does not give back
    the rest (a coarse, stated simplification)."""
    if berth_length_m <= 0 or loa_m <= berth_length_m + 1e-9:
        return 1
    return max(1, min(int(berths), int(math.ceil(loa_m / berth_length_m - 1e-9))))


def mean_loa_kind(kind: str) -> float:
    """Share-weighted mean LOA (m) of the registry size classes of a kind."""
    return sum(c["share"] * c["loa_m"] for c in size_classes(kind, 1.0))


def mean_loa(calls_per_day: dict, work_per_call: dict) -> float:
    tot = sum(calls_per_day.get(k, 0.0) for k in KIND_ORDER)
    if tot <= 0:
        return 0.0
    acc = 0.0
    for k in KIND_ORDER:
        lam = calls_per_day.get(k, 0.0)
        if lam > 0:
            acc += lam * sum(c["share"] * c["loa_m"]
                             for c in size_classes(k, work_per_call.get(k, 1.0)))
    return acc / tot
