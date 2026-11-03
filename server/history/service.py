"""HistoryService: the one object the routes talk to.

Wires the providers to a cache, a request budget and a transport, and
offers the four read operations the routes expose:

  sources()       the capability board (states + what was VERIFIED);
  events(...)     DisruptionEvents for a window, merged across the
                  capable providers, every one provenance-stamped;
  observations()  one per-place daily series, dispatched by metric;
  timeline(date)  everything known on a date (events + observations +
                  the live-equivalent view), as-of that date: no
                  look-ahead - nothing dated after ``date`` is shown.

Nothing here simulates. A provider that cannot serve answers NOT SERVED
and the response says so; the merge never fills the hole.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from pathlib import Path

from .budget import RequestBudget
from .cache import HistoryCache
from .places import PlaceBook
from .providers.base import (BLOCKED, CAPABLE, CURRENT_ONLY, HistoryProvider,
                             NOT_SERVED, Services, not_served)
from .providers.comtrade import ComtradeProvider
from .providers.current_only import AisStreamProvider, NoaaNwsProvider
from .providers.eia import EiaProvider
from .providers.gdacs import GdacsProvider
from .providers.gdelt import GdeltProvider
from .providers.openmeteo import OpenMeteoArchiveProvider
from .providers.opensky import OpenSkyProvider
from .providers.portwatch import PortWatchProvider, metric_table
from .providers.replay_catalogue import CuratedReplayProvider
from .transport import default_transport
from .util import HistoryError, add_days, iso_day, parse_day, today_utc

try:  # house convention: server/ on sys.path
    from store.store import validate_c3_event
except Exception:  # pragma: no cover
    try:
        from server.store.store import validate_c3_event  # type: ignore
    except Exception:
        validate_c3_event = None

# metric -> provider routing
_PORTWATCH_METRICS = set(metric_table("chokepoint")) | set(metric_table("port")) | {"activity"}


def fetch_mode_from_env(getenv=os.environ.get) -> bool:
    """SUPERMAP_HISTORY_FETCH: 'off'/'0'/'false' = cache-only (offline);
    anything else (default) = cache first, then a budgeted upstream call."""
    v = (getenv("SUPERMAP_HISTORY_FETCH") or "auto").strip().lower()
    return v not in ("off", "0", "false", "no")


class HistoryService:
    def __init__(self, *, data_dir: str | Path, cache_dir: str | Path | None = None,
                 graph_path: str | Path | None = None,
                 transport=default_transport, fetch_enabled: bool | None = None,
                 budget: RequestBudget | None = None, now=None, getenv=None,
                 live_events=None, repo_data_dir: str | Path | None = None):
        self.data_dir = Path(data_dir)
        self.cache = HistoryCache(cache_dir or self.data_dir / "history_cache")
        self.graph_path = Path(graph_path) if graph_path else \
            self.data_dir / "graph" / "graph.json"
        self.budget = budget or RequestBudget(self.data_dir / "history_budget.json")
        self._book: PlaceBook | None = None
        self._book_mtime = None
        self.live_events = live_events    # callable -> list[dict] (folded)
        kw = {}
        if now is not None:
            kw["now"] = now
        if getenv is not None:
            kw["getenv"] = getenv
        self.svc = Services(
            cache=self.cache, budget=self.budget, transport=transport,
            fetch_enabled=(fetch_mode_from_env() if fetch_enabled is None
                           else fetch_enabled),
            graph_loader=self._load_book,
            data_dir=Path(repo_data_dir) if repo_data_dir else self.data_dir, **kw)
        self.portwatch = PortWatchProvider(self.svc)
        self.gdacs = GdacsProvider(self.svc)
        self.eia = EiaProvider(self.svc)
        self.comtrade = ComtradeProvider(self.svc)
        self.gdelt = GdeltProvider(self.svc)
        self.opensky = OpenSkyProvider(self.svc)
        self.openmeteo = OpenMeteoArchiveProvider(self.svc)
        self.noaa = NoaaNwsProvider(self.svc)
        self.ais = AisStreamProvider(self.svc)
        self.replay = CuratedReplayProvider(self.svc)
        self.providers: list[HistoryProvider] = [
            self.portwatch, self.gdacs, self.replay, self.eia, self.comtrade,
            self.opensky, self.openmeteo, self.gdelt, self.noaa, self.ais]

    # ----------------------------------------------------------- places
    def _load_book(self) -> PlaceBook:
        try:
            mtime = self.graph_path.stat().st_mtime
        except OSError:
            mtime = None
        if self._book is None or mtime != self._book_mtime:
            join = self.graph_path.parent / "portwatch_join_input.json"
            self._book = PlaceBook.load(self.graph_path, join)
            self._book_mtime = mtime
        return self._book

    def book(self) -> PlaceBook:
        return self._load_book()

    # ------------------------------------------------------------ board
    def sources(self) -> dict:
        entries = [p.board_entry() for p in self.providers]
        counts = {s: sum(1 for e in entries if e["state"] == s)
                  for s in (CAPABLE, CURRENT_ONLY, BLOCKED)}
        return {
            "sources": entries, "summary": counts,
            "fetch_mode": "auto (cache first, then a budgeted upstream call)"
            if self.svc.fetch_enabled else "off (cache only, offline)",
            "budget": self.budget.snapshot(),
            "cache_dir": "history_cache",   # a name, not the absolute server path
            "legend": {
                CAPABLE: "serves dated history; a real call "
                         "returned it (see verified)",
                CURRENT_ONLY: "serves the present (or a rolling few days); "
                              "nothing a historical date can use",
                BLOCKED: "history exists in principle but could not be "
                         "served - NOT SERVED, never "
                         "backfilled",
            },
            "provenance_rule": ("every record carries {source, retrieved_at, "
                                "mode}; history is SNAPSHOT (a recorded "
                                "fetch) or MODELED (inferred), never LIVE"),
            "checked_on": "2026-10-25",
        }

    # ----------------------------------------------------------- events
    def events(self, start: date, end: date, places=None, sources=None,
               infer: bool | None = None) -> dict:
        if end < start:
            raise HistoryError("to must be on or after from")
        if (end - start).days > 3660:
            raise HistoryError("window longer than 3660 days", "window_too_large")
        want = set(sources) if sources else None
        merged: list[dict] = []
        per_source: list[dict] = []
        for prov in (self.replay, self.gdacs, self.portwatch):
            if want is not None and prov.name not in want:
                continue
            if prov is self.portwatch:
                # inference needs places and is opt-out via infer=False
                if infer is False or not places:
                    if want is not None and prov.name in want and not places:
                        per_source.append(not_served(
                            prov.name, "places_required",
                            "PortWatch events are inferred per place; pass places="))
                    continue
            res = prov.events(start, end, places)
            if res.get("served"):
                merged.extend(res["events"])
                per_source.append({
                    "source": prov.name, "served": True,
                    "count": len(res["events"]),
                    "mode": res["provenance"]["mode"],
                    "unmapped_count": res.get("unmapped_count"),
                    "skipped_places": res.get("skipped_places"),
                    "provenance": res["provenance"]})
            else:
                per_source.append({k: res[k] for k in
                                   ("source", "served", "state", "reason",
                                    "detail", "provenance") if k in res})
        if validate_c3_event is not None:
            valid = []
            for ev in merged:
                try:
                    validate_c3_event(ev)
                    valid.append(ev)
                except Exception as exc:   # a malformed record is dropped LOUDLY
                    per_source.append({"source": ev.get("source"), "served": False,
                                       "reason": "c3_invalid",
                                       "detail": f"{ev.get('id')}: {exc}"})
            merged = valid
        merged.sort(key=lambda e: (e["started_at"], e["id"]))
        return {"from": iso_day(start), "to": iso_day(end),
                "places": sorted(places) if places else None,
                "count": len(merged), "events": merged, "sources": per_source,
                "label": ("HISTORICAL — recorded or inferred; never LIVE. "
                          "Each event carries provenance.mode SNAPSHOT or "
                          "MODELED.")}

    def event_by_id(self, event_id: str, start: date | None = None,
                    end: date | None = None, places=None) -> dict | None:
        """Resolve a historical event id (curated catalogue first - no
        network - then the GDACS window when given, then PortWatch
        inference when ``places`` given)."""
        for c in self.replay.catalogue():
            ev = self.replay.to_event(c)
            if ev["id"] == event_id:
                return ev
        if start and end:
            for prov in (self.gdacs, self.portwatch):
                if prov is self.portwatch and not places:
                    continue
                res = prov.events(start, end, places)
                if res.get("served"):
                    for ev in res["events"]:
                        if ev["id"] == event_id:
                            return ev
        return None

    # ----------------------------------------------------- observations
    def provider_for_metric(self, metric: str) -> HistoryProvider | None:
        base = metric.split(":", 1)[0]
        if metric in _PORTWATCH_METRICS:
            return self.portwatch
        if base == "spot":
            return self.eia
        if base in ("trade_exports_usd", "trade_imports_usd"):
            return self.comtrade
        if metric == "news_volume":
            return self.gdelt
        if metric in ("flights_arrivals", "flights_departures"):
            return self.opensky
        from .providers.openmeteo import METRICS as OM
        if metric in OM:
            return self.openmeteo
        return None

    def observations(self, place: str, metric: str, start: date, end: date) -> dict:
        if end < start:
            raise HistoryError("to must be on or after from")
        if (end - start).days > 3660:
            raise HistoryError("window longer than 3660 days", "window_too_large")
        prov = self.provider_for_metric(metric or "activity")
        if prov is None:
            return not_served("history", "unsupported_metric",
                              f"unknown metric {metric!r}; see GET "
                              "/history/sources for each source's metrics",
                              place=place, metric=metric)
        return prov.observations(place, metric or "activity", start, end)

    def metrics(self) -> list[dict]:
        out = []
        for p in self.providers:
            for m in p.metrics():
                out.append({**m, "source": p.name})
        return out

    # --------------------------------------------------------- timeline
    def timeline(self, day: date, places=None, lookback_days: int = 7,
                 include_context: bool = True) -> dict:
        """Everything known on ``day``, as of ``day`` (no look-ahead)."""
        start = add_days(day, -90)
        ev_res = self.events(start, day, places=places, infer=False)
        active = []
        for ev in ev_res["events"]:
            s = parse_day(ev["started_at"])
            if s > day:
                continue
            end_day = None
            if ev.get("expires_at"):
                end_day = parse_day(ev["expires_at"])
            elif ev.get("expected_duration_d"):
                end_day = s + timedelta(days=float(ev["expected_duration_d"]))
            is_active = end_day is None or end_day >= day
            if not is_active:
                continue
            active.append({**ev, "status_as_of": "active",
                           "day_of_event": (day - s).days})
        # places to observe: requested, else the active events' own places
        # and their curated watch lists
        watch: list[str] = list(places) if places else []
        if not watch:
            for ev in active:
                for p in [ev["node_id"]] + list(ev.get("watch_places") or []):
                    if p not in watch:
                        watch.append(p)
        observed = []
        lb = add_days(day, -lookback_days)
        for p in watch[:12]:
            r = self.portwatch.observations(p, "activity", lb, day)
            if r.get("served"):
                last = r["series"][-1] if r["series"] else None
                observed.append({
                    "place": p, "metric": r["metric"], "unit": r["unit"],
                    "on_date": next((x["value"] for x in r["series"]
                                     if x["date"] == iso_day(day)), None),
                    "trailing": r["series"], "last_observation": last,
                    "provenance": r["provenance"], "cache": r["cache"]})
            else:
                observed.append({"place": p, "served": False,
                                 "reason": r.get("reason"), "detail": r.get("detail"),
                                 "state": NOT_SERVED})
        context = []
        if include_context:
            for sid in ("RBRTE", "RWTC"):
                r = self.eia.observations("market", f"spot:{sid}", lb, day)
                if r.get("served") and r["series"]:
                    context.append({"metric": r["metric"], "label": r["label"],
                                    "unit": r["unit"], "last": r["series"][-1],
                                    "provenance": r["provenance"]})
                else:
                    context.append({"metric": f"spot:{sid}", "served": False,
                                    "reason": r.get("reason"),
                                    "state": NOT_SERVED})
        return {
            "date": iso_day(day), "as_of": iso_day(day),
            "label": "HISTORICAL VIEW — as of the date; nothing later shown",
            "events": active, "event_sources": ev_res["sources"],
            "observations": observed, "market_context": context,
            "live_equivalent": self.live_equivalent(day),
        }

    def live_equivalent(self, day: date) -> dict:
        """What the LIVE terminal's events store held on ``day``: events
        whose detection precedes the end of the day and that had not
        expired. Labelled RECORDED (never LIVE): it is the live store's own
        record, covering only the dates the store has been running."""
        if self.live_events is None:
            return {"available": False, "reason": "no live events store attached"}
        try:
            rows = self.live_events() or []
        except Exception as exc:
            return {"available": False, "reason": f"events store unreadable: {exc}"}
        det = sorted(r.get("detected_at") for r in rows if r.get("detected_at"))
        cover = {"first_detected_at": det[0] if det else None,
                 "last_detected_at": det[-1] if det else None,
                 "n_events_in_store": len(rows)}
        eod = iso_day(day) + "T23:59:59Z"
        out = []
        for r in rows:
            if not r.get("detected_at") or r["detected_at"] > eod:
                continue
            exp = r.get("expires_at") or (r.get("nws") or {}).get("expires")
            if exp and str(exp)[:10] < iso_day(day):
                continue
            if r.get("status") == "resolved" and not exp:
                continue
            item = {k: r[k] for k in ("id", "node_id", "kind", "severity",
                                      "started_at", "detected_at", "source",
                                      "status") if k in r}
            # History is never LIVE: a stored event is a RECORDED item,
            # whatever mode it carried when the live store fetched it.
            item["mode"] = "RECORDED"
            if r.get("mode"):
                item["recorded_mode"] = r["mode"]
            out.append(item)
        out.sort(key=lambda e: (e.get("detected_at"), e["id"]))
        return {"available": True, "mode": "RECORDED (as stored by the live "
                "ingestion)", "store_coverage": cover, "count": len(out),
                "events": out[:200],
                "note": ("This is the live events store as it stood; it only "
                         "covers the dates ingestion was running. Outside "
                         "that coverage it is empty - nothing is "
                         "reconstructed or invented.")}
