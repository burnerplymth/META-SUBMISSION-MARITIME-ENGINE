"""HistoryProvider interface + the shared cache-through-fetch plumbing.

Every provider answers two questions, both optional:

  * ``events(start, end, places)``   -> DisruptionEvents (mode
    SNAPSHOT for a recorded source, MODELED for inferred ones);
  * ``observations(place, metric, start, end)`` -> a per-place series.

and describes itself for the capability board (``board_entry``) with the
three honest states:

  HISTORY-CAPABLE  the source serves dated history and a real call
                   returned it (``verified``);
  CURRENT-ONLY     the source serves the present (or a rolling few days),
                   nothing a historical date can use;
  BLOCKED          the source has history in principle but could not be
                   served (network block, rate limit, missing
                   credential) - NOT SERVED, never backfilled.

A call that cannot be served returns ``{"served": False, "state":
"NOT SERVED", "reason": <code>, "detail": ...}``: the reason codes are
stable (``not_cached_fetch_disabled``, ``budget_exhausted``,
``rate_limited``, ``policy_denied``, ``unreachable``, ``no_credential``,
``current_only``, ``unknown_place``, ``no_series_for_place``,
``unsupported_metric``, ``http_<status>``, ``bad_payload``).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable

from ..budget import RequestBudget
from ..cache import HistoryCache
from ..places import PlaceBook
from ..transport import HttpRequest, HttpResponse, TransportError, default_transport
from ..util import utcnow_iso

CAPABLE = "HISTORY-CAPABLE"
CURRENT_ONLY = "CURRENT-ONLY"
BLOCKED = "BLOCKED"
NOT_SERVED = "NOT SERVED"


class NotServed(Exception):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


@dataclass
class Services:
    """Shared dependencies, injected once."""
    cache: HistoryCache
    budget: RequestBudget
    places: PlaceBook | None = None
    transport: Callable[[HttpRequest], HttpResponse] = default_transport
    fetch_enabled: bool = True          # False = cache-only (offline)
    now: Callable[[], str] = utcnow_iso
    getenv: Callable[[str], str | None] = field(
        default=lambda name: __import__("os").environ.get(name))
    graph_loader: Callable[[], PlaceBook] | None = None
    data_dir: "Path | None" = None      # repo data/ (replay records)
    sleep: Callable[[float], None] = field(default=lambda s: __import__("time").sleep(s))
    clock: Callable[[], float] = field(default=lambda: __import__("time").monotonic())

    def book(self) -> PlaceBook:
        if self.places is None and self.graph_loader is not None:
            self.places = self.graph_loader()
        if self.places is None:
            raise NotServed("graph_unavailable", "no graph loaded")
        return self.places


def not_served(source: str, reason: str, detail: str = "", **extra) -> dict:
    out = {"served": False, "state": NOT_SERVED, "source": source,
           "reason": reason, "detail": detail,
           "provenance": {"source": source, "retrieved_at": None,
                          "mode": None},
           "note": "no data served; nothing simulated stands in for it"}
    out.update(extra)
    return out


class HistoryProvider:
    """Subclass contract. ``name`` is the stable source id."""

    name = "base"
    label = "base"
    capability = CURRENT_ONLY
    budget_key: str | None = None        # key into RequestBudget caps
    supports_events = False
    supports_observations = False
    # what a real call showed (filled by subclasses,
    # surfaced verbatim on GET /history/sources)
    verified: dict = {}
    notes: str = ""
    history_window: str = ""

    def __init__(self, svc: Services):
        self.svc = svc

    # ------------------------------------------------------------ board
    def board_entry(self) -> dict:
        key = self.budget_key or self.name
        cached = self.svc.cache.entries(self.name)
        return {
            "source": self.name, "label": self.label,
            "state": self.capability,
            "history_window": self.history_window,
            "supports": [k for k, on in (("events", self.supports_events),
                                         ("observations", self.supports_observations))
                         if on],
            "verified": self.verified, "notes": self.notes,
            "credential": self.credential_state(),
            "cache": {"entries": len(cached),
                      "files": [c["file"] for c in cached]},
            "budget": ({"cap": self.svc.budget.cap(key),
                        "used_today": self.svc.budget.used(key),
                        "remaining": self.svc.budget.remaining(key)}
                       if self.capability != CURRENT_ONLY else None),
            "fetch_enabled": bool(self.svc.fetch_enabled),
        }

    def credential_state(self) -> str | None:
        """'present' | 'missing' | None (no credential needed). Never the
        value."""
        return None

    # --------------------------------------------------------- queries
    def events(self, start: date, end: date, places=None) -> dict:
        return not_served(self.name, "unsupported",
                          f"{self.name} serves no events")

    def observations(self, place: str, metric: str, start: date,
                     end: date) -> dict:
        return not_served(self.name, "unsupported",
                          f"{self.name} serves no observation series")

    def metrics(self) -> list[dict]:
        return []

    # ----------------------------------------------- cache-through fetch
    def http(self, req: HttpRequest) -> HttpResponse:
        """One budgeted upstream request with honest failure mapping."""
        key = self.budget_key or self.name
        if not self.svc.budget.charge(key):
            raise NotServed("budget_exhausted",
                            f"daily request budget for {key} is spent "
                            f"(cap {self.svc.budget.cap(key)})")
        try:
            resp = self.svc.transport(req)
        except TransportError as exc:
            raise NotServed("unreachable", str(exc)) from None
        if resp.status in (403, 407):
            raise NotServed("policy_denied",
                            f"HTTP {resp.status} (access policy or auth); "
                            "not retried")
        if resp.status == 401:
            raise NotServed("auth_rejected", "HTTP 401")
        if resp.status == 429:
            raise NotServed("rate_limited",
                            "HTTP 429 from the upstream; not retried")
        return resp

    @staticmethod
    def json_body(resp: HttpResponse, what: str):
        if resp.status != 200:
            raise NotServed(f"http_{resp.status}",
                            f"{what}: HTTP {resp.status}")
        try:
            return json.loads(resp.body.decode("utf-8", "replace"))
        except ValueError:
            raise NotServed("bad_payload",
                            f"{what}: response is not JSON") from None

    def through_cache(self, query: dict, fetcher, *, status_ok=("OK", "EMPTY")):
        """(doc, hit). ``fetcher()`` -> (payload, request_public, status,
        note) is called only on a cache miss with fetch enabled."""
        series = query.get("series")
        if query.get("from") and query.get("to") and series:
            from ..util import parse_day
            doc = self.svc.cache.find_covering(
                self.name, series, parse_day(query["from"]),
                parse_day(query["to"]))
        else:
            doc = self.svc.cache.get(self.name, query)
        if doc is not None and doc.get("status") in status_ok:
            return doc, True
        if not self.svc.fetch_enabled:
            raise NotServed("not_cached_fetch_disabled",
                            "not in the history cache and live fetching is "
                            "off (SUPERMAP_HISTORY_FETCH=off)")
        payload, request_public, status, note = fetcher()
        doc = self.svc.cache.put(
            self.name, query, payload, retrieved_at=self.svc.now(),
            provenance_source=self.label, request=request_public,
            status=status, note=note)
        return doc, False

    def cache_info(self, doc: dict, hit: bool) -> dict:
        return {"hit": hit, "retrieved_at": doc["provenance"]["retrieved_at"],
                "query": doc.get("query"),
                "payload_sha256": doc.get("payload_sha256")}
