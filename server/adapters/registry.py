"""Adapter registry — the single place the health board reads."""
from __future__ import annotations

from .base import (DEFAULT_HEALTH_MAX_AGE_S, BaseAdapter, health_freshness)

_REGISTRY: dict[str, BaseAdapter] = {}

# Health-only registrations: adapters that implement the
# Adapter health() dict but are NOT BaseAdapter fetchables — the money
# layer (server/money). They belong on the health board, but they
# must never enter the ingestion cycle: all_adapters() (which
# run_once iterates and calls fetch() on) stays BaseAdapter-only.
_HEALTH_ONLY: dict[str, object] = {}

# Per-adapter stale-LIVE thresholds for health-only entries.
_HEALTH_ONLY_MAX_AGE_S = {
    "stooq": 2 * 86400,        # daily bars; weekends are not staleness
    "edgar": 30 * 86400,       # filings are reference data
    "fred": 7 * 86400,         # macro series publish weekly or slower
    "frankfurter": 2 * 86400,  # ECB daily fixing; weekends as stooq
}


def register(adapter: BaseAdapter) -> BaseAdapter:
    _REGISTRY[adapter.name] = adapter
    return adapter


def register_health_only(adapter):
    """Register an adapter for the health board only (see above)."""
    _HEALTH_ONLY[adapter.name] = adapter
    return adapter


def clear_registry() -> None:
    """Empty the whole registry — the ONE clear/reset for this module.

    Clears BOTH the fetchable adapter set (``_REGISTRY``) and the
    health-only set (``_HEALTH_ONLY``) together: the two are
    one registry for health purposes, and clearing only the first
    leaves stale health-only entries on ``health_report()``.
    Both dicts are mutated in place,
    never rebound, so existing holders of them see the change."""
    _REGISTRY.clear()
    _HEALTH_ONLY.clear()


def get(name: str):
    if name in _REGISTRY:
        return _REGISTRY[name]
    return _HEALTH_ONLY[name]


def all_adapters() -> list[BaseAdapter]:
    return list(_REGISTRY.values())


def _health_only_entry(adapter) -> dict:
    """Normalise a health()-dict adapter onto the board shape,
    applying the same read-time freshness rule as AdapterHealth:
    gain fresh/age_s/max_age_s, a stale LIVE reads as
    SNAPSHOT, and the money layer's NEEDS-KEY state word maps to the
    Board's DOWN with the fact preserved in the note."""
    raw = adapter.health()
    entry = {
        "adapter": raw.get("adapter") or adapter.name,
        "state": raw.get("state"),
        "last_success_at": raw.get("last_success_at"),
        "mode": raw.get("mode"),
    }
    if raw.get("note"):
        entry["note"] = raw["note"]
    if entry["state"] == "NEEDS-KEY":
        entry["state"] = "DOWN"
    max_age = _HEALTH_ONLY_MAX_AGE_S.get(entry["adapter"],
                                         DEFAULT_HEALTH_MAX_AGE_S)
    age_s, fresh = health_freshness(entry["last_success_at"], max_age)
    if entry["state"] == "LIVE" and age_s is not None and not fresh:
        entry["state"] = "SNAPSHOT"
    entry["fresh"] = fresh
    entry["age_s"] = round(age_s, 3) if age_s is not None else None
    entry["max_age_s"] = max_age
    return entry


def health_report() -> list[dict]:
    """Per-adapter adapter health entries, in registration order: the
    fetchable adapters first, then health-only registrations."""
    return [a.health.to_dict() for a in _REGISTRY.values()] + \
        [_health_only_entry(a) for a in _HEALTH_ONLY.values()]


def build_default_registry() -> dict:
    """Instantiate every adapter. Imported lazily so a single broken
    adapter module can never take the registry (and /health) down."""
    from .aisstream import AISStreamAdapter
    from .cbp import CBPAdapter
    from .comtrade import ComtradeAdapter
    from .eia import EIAAdapter
    from .gdacs import GDACSAdapter
    from .gdelt import GDELTAdapter
    from .noaa import NOAAAdapter
    from .ntad import NTADAdapter
    from .opensky import OpenSkyAdapter
    from .portwatch import PortWatchAdapter
    from .portwatch_daily import PortWatchDailyAdapter

    for cls in (PortWatchAdapter, PortWatchDailyAdapter, GDELTAdapter,
                GDACSAdapter, NOAAAdapter,
                CBPAdapter, AISStreamAdapter, OpenSkyAdapter, NTADAdapter,
                EIAAdapter, ComtradeAdapter):  # EIA/Comtrade: CONTEXT feeds
        register(cls())

    # the four money adapters (each already implements
    # health()) join the board as health-only registrations. A money
    # module failing to import must not take the board down either.
    try:
        try:
            from ..money.edgar import EDGARClient
            from ..money.frankfurter import FrankfurterAdapter
            from ..money.fred import FredAdapter
            from ..money.stooq import StooqAdapter
        except ImportError:  # server/ on sys.path: top-level `money`
            from money.edgar import EDGARClient  # type: ignore
            from money.frankfurter import FrankfurterAdapter  # type: ignore
            from money.fred import FredAdapter  # type: ignore
            from money.stooq import StooqAdapter  # type: ignore
        for adapter in (StooqAdapter(), EDGARClient(), FredAdapter(),
                        FrankfurterAdapter()):
            register_health_only(adapter)
    except Exception:
        pass
    return {**_REGISTRY, **_HEALTH_ONLY}
