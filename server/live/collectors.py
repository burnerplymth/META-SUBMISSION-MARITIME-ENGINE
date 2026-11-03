"""Builds, starts and stops the continuous collectors of one live hub.

Opt-in, on top of the LIVE TWIN flag: ``SUPERMAP_LIVE_COLLECT`` =
``1`` / ``all`` (both), or a comma list of ``ais`` and ``opensky``.
Unset / ``0`` => no collector, no extra thread, no network from this
package (flag-off still means no hub at all; see routes.py).

Credentials are read from the environment HERE and handed to the
collector objects in memory; they are never logged, written to a file,
put in a URL or echoed in a status field:

  * ``AISSTREAM_API_KEY``                       -> AIS collector
  * ``OPENSKY_CLIENT_ID`` / ``OPENSKY_CLIENT_SECRET`` -> OpenSky collector

A collector whose credentials are missing is registered with state
``DOWN`` and a ``NEEDS-KEY`` note (so the section is hidden / NOT SERVED
honestly) and no thread is started for it.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

try:
    from adapters import aisstream as _ais_adapter
    from adapters.opensky import OpenSkyTokenProvider
except ImportError:                                  # pragma: no cover
    from server.adapters import aisstream as _ais_adapter
    from server.adapters.opensky import OpenSkyTokenProvider

from .ais_collector import AisCollector
from .budget import CreditBudget
from .opensky_collector import OpenSkyStatesCollector

log = logging.getLogger("supermap.live.collectors")

_ALIASES = {"ais": "ais", "aisstream": "ais", "vessels": "ais",
            "opensky": "opensky", "aircraft": "opensky", "air": "opensky"}


def parse_collect(raw) -> frozenset:
    """Env / state value -> set of collector names."""
    if raw is None:
        return frozenset()
    if isinstance(raw, (set, frozenset, list, tuple)):
        parts = [str(x).strip().lower() for x in raw]
    else:
        text = str(raw).strip().lower()
        if text in ("", "0", "no", "false", "off"):
            return frozenset()
        if text in ("1", "yes", "true", "on", "all"):
            return frozenset({"ais", "opensky"})
        parts = [x.strip() for x in text.split(",")]
    return frozenset(_ALIASES[p] for p in parts if p in _ALIASES)


def collectors_enabled(raw) -> frozenset:
    """``parse_collect`` under the offline kill switch: with
    SUPERMAP_BLOCK_NETWORK=1 (the recorded / offline mode) no continuous
    collector is built, whatever SUPERMAP_LIVE_COLLECT says, so the
    OpenSky and AISStream collectors stay off in offline configs."""
    if os.environ.get("SUPERMAP_BLOCK_NETWORK") == "1":
        return frozenset()
    which = parse_collect(raw)
    only = {x.strip().lower() for x in os.environ.get("SUPERMAP_SNAPSHOT_ONLY_FEEDS", "").split(",") if x.strip()}
    if "all" in only:
        return frozenset()
    # a feed served from its stored pack by policy gets no continuous collector either
    return frozenset(w for w in which if {"ais": "aisstream"}.get(w, w) not in only)


def _env_f(name: str, default: float) -> float:
    try:
        v = float(os.environ.get(name, ""))
        return v if v == v else default
    except ValueError:
        return default


class CollectorSet:
    def __init__(self, hub, which, data_root, *, ais_connect=None,
                 opensky_transport=None, opensky_token_post=None,
                 ais_kwargs=None, opensky_kwargs=None, env=None,
                 budget_clock=None):
        env = os.environ if env is None else env
        self.hub = hub
        self.collectors: dict = {}
        self.data_root = Path(data_root)
        if "ais" in which:
            self.collectors["aisstream"] = AisCollector(
                hub, api_key=env.get("AISSTREAM_API_KEY") or None,
                connect=ais_connect, **(ais_kwargs or {}))
        if "opensky" in which:
            cid = env.get("OPENSKY_CLIENT_ID") or ""
            sec = env.get("OPENSKY_CLIENT_SECRET") or ""
            budget = CreditBudget(
                daily_limit=int(_env_f("SUPERMAP_LIVE_OS_DAILY_LIMIT", 4000)),
                reserve_fraction=_env_f("SUPERMAP_LIVE_OS_RESERVE", 0.20),
                adapter_daily_credits=int(
                    _env_f("SUPERMAP_LIVE_OS_ADAPTER_CREDITS", 480)),
                min_interval_s=_env_f("SUPERMAP_LIVE_OS_MIN_S", 60.0),
                max_interval_s=_env_f("SUPERMAP_LIVE_OS_MAX_S", 900.0),
                state_path=self.data_root / "opensky_states_budget.json",
                **({"clock": budget_clock} if budget_clock else {}))
            provider = OpenSkyTokenProvider(
                cid, sec, post=opensky_token_post) if cid and sec else None
            self.collectors["opensky"] = OpenSkyStatesCollector(
                hub, budget=budget, token_provider=provider,
                transport=opensky_transport,
                client_configured=bool(cid and sec),
                **(opensky_kwargs or {}))
        hub.collectors = dict(self.collectors)
        self.started = False

    def start(self) -> None:
        if self.started:
            return
        self.started = True
        for c in self.collectors.values():
            try:
                c.start()
            except Exception:
                log.exception("collector %s failed to start", c.name)
        ais = self.collectors.get("aisstream")
        if ais is not None and ais.alive():
            # the legacy sampling adapter must not open a second AISStream
            # connection while this collector holds the only one
            try:
                _ais_adapter.set_sample_provider(ais.adapter_sample)
            except Exception:
                log.exception("could not register the adapter sample hook")

    def stop(self) -> None:
        try:
            _ais_adapter.set_sample_provider(None)
        except Exception:
            pass
        for c in self.collectors.values():
            try:
                c.stop()
            except Exception:
                log.exception("collector %s failed to stop", c.name)
        self.started = False

    def status(self) -> dict:
        return {n: c.status() for n, c in self.collectors.items()}
