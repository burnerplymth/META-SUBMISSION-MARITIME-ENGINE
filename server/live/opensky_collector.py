"""Per-aircraft collector: authenticated OpenSky ``/states/all``.

One daemon thread under the live hub. Each poll is ONE global call
(4 credits; ``extended=1`` adds the emitter category at no extra cost)
or, when ``bboxes`` is given, one call per bbox (cost by area, see
``adapters.opensky.bbox_credit_cost``). The OAuth2 bearer comes from the
adapter's ``OpenSkyTokenProvider`` (cached in memory, refreshed before
expiry); the client id/secret are read from the environment by the
manager and never printed, logged, stored or put in a URL.

Pacing and budget: ``budget.CreditBudget`` (see its docstring for the
arithmetic). The interval is re-solved after every response from the
server's own ``X-Rate-Limit-Remaining``; a 429 stops polling until the
hinted time; exhausted budget stops polling until the next UTC midnight.

Honesty:
  * only aircraft with a reported position and a ``time_position`` no
    older than ``max_report_age_s`` are recorded; everything else is
    counted (``skipped_*``), nothing is estimated or interpolated;
  * the record time ``t`` is OpenSky's ``time_position`` (the observation
    time), ``retrieved_at`` the moment our call returned;
  * the feed reads LIVE only while the last successful call is younger
    than ``3 x interval + 30 s``.

Records are written with ``hub.ingest`` in chunks (the hub lock is never
held for a whole 6,000-aircraft batch).
"""
from __future__ import annotations

import json
import logging
import random
import sys
import threading
import time
import urllib.parse

try:
    from adapters.http_util import NetworkBlocked, http_get
    from adapters.opensky import (API, OpenSkyAuthError, OpenSkyTokenProvider,
                                  bbox_credit_cost)
except ImportError:                                  # pragma: no cover
    from server.adapters.http_util import NetworkBlocked, http_get
    from server.adapters.opensky import (API, OpenSkyAuthError,
                                         OpenSkyTokenProvider,
                                         bbox_credit_cost)

from .budget import CreditBudget
from .normalize import iso_z

log = logging.getLogger("supermap.live.opensky")

SOURCE = "OpenSky Network"
GLOBAL_COST = 4              # measured: area > 400 sq deg
CHUNK = 500


def _hdr(headers: dict | None, name: str):
    if not headers:
        return None
    low = name.lower()
    for k, v in headers.items():
        if k.lower() == low:
            return v
    return None


class OpenSkyStatesCollector:
    name = "opensky"
    feed = "opensky"

    def __init__(self, hub, *, budget: CreditBudget, token_provider=None,
                 transport=None, clock=time.time, bboxes=None,
                 max_report_age_s: float = 600.0, rng=None,
                 request_timeout_s: float = 60.0,
                 client_configured: bool = True):
        self.hub = hub
        self.budget = budget
        self.token_provider = token_provider
        self._transport = transport or http_get
        self.clock = clock
        # bboxes: iterable of (lamin, lamax, lomin, lomax) or None=global
        self.bboxes = [tuple(b) for b in bboxes] if bboxes else None
        self.call_cost = (sum(bbox_credit_cost(*b) for b in self.bboxes)
                          if self.bboxes else GLOBAL_COST)
        self.max_report_age_s = float(max_report_age_s)
        self.request_timeout_s = request_timeout_s
        self._rng = rng or random.Random()
        self.client_configured = client_configured
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._start_lock = threading.Lock()
        # status
        self.state = "STARTING" if client_configured else "DOWN"
        self.note = None if client_configured else (
            "NEEDS-KEY: set OPENSKY_CLIENT_ID and OPENSKY_CLIENT_SECRET "
            "(OpenSky API client) to collect per-aircraft positions")
        self.polls = 0
        self.ok_polls = 0
        self.errors = 0
        self.consecutive_errors = 0
        self.last_error: str | None = None
        self.last_ok_at: float | None = None
        self.last_status: int | None = None
        self.last_states = 0
        self.last_recorded = 0
        self.last_skipped = {"no_position": 0, "stale_report": 0,
                             "bad_row": 0}
        self.last_changed = 0
        self.last_call_s: float | None = None
        self.total_recorded = 0
        self.started_at: float | None = None
        self._next_at: float | None = None
        self._prune_at = 0.0
        self.publish_state()

    # -- lifecycle -------------------------------------------------------
    def start(self) -> None:
        if not self.client_configured:
            return
        with self._start_lock:
            if self._thread is not None:
                return
            self._stop.clear()
            self.started_at = self.clock()
            self._thread = threading.Thread(
                target=self._run, name="supermap-live-opensky", daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout=timeout)
        self._thread = None

    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # -- main loop -------------------------------------------------------
    def _run(self) -> None:
        first = True
        while not self._stop.is_set():
            try:
                wait = self.budget.next_delay(self.call_cost, first=first)
                first = False
                if self.consecutive_errors:
                    wait = max(wait, self._error_backoff())
                self._next_at = self.clock() + wait
                if wait > 0 and self.budget.exhausted(self.call_cost):
                    self.state = "SNAPSHOT"
                    self.note = ("daily credit budget reached "
                                 f"(reserve {self.budget.reserve} of "
                                 f"{self.budget.daily_limit} kept); "
                                 "polling resumes after the UTC day roll")
                    self.publish_state()
                # sleep in slices so stop() and the feed state stay prompt
                end = time.monotonic() + wait
                while not self._stop.is_set():
                    left = end - time.monotonic()
                    if left <= 0:
                        break
                    self._stop.wait(min(left, 5.0))
                    self.publish_state(throttle=True)
                if self._stop.is_set():
                    break
                self.poll_once()
            except Exception as exc:       # never kill the thread
                self._fail(f"{type(exc).__name__}")
                log.exception("opensky collector loop error")
                self._stop.wait(5.0)

    def _error_backoff(self) -> float:
        n = min(self.consecutive_errors, 6)
        return min(900.0, 30.0 * (2 ** (n - 1))) + self._rng.uniform(0, 5)

    # -- one poll --------------------------------------------------------
    def poll_once(self) -> dict:
        """One poll cycle (all bboxes). Returns a small result dict."""
        self.polls += 1
        t0 = time.monotonic()
        if self.token_provider is None:
            self._fail("no OAuth2 client configured")
            return {"ok": False}
        try:
            token = self.token_provider.token()
        except NetworkBlocked:
            self._fail("network blocked (SUPERMAP_BLOCK_NETWORK)")
            return {"ok": False}
        except OpenSkyAuthError as exc:
            self._fail(f"OAuth2 {exc}")
            return {"ok": False}
        except Exception as exc:
            self._fail(f"OAuth2 token request failed ({type(exc).__name__})")
            return {"ok": False}
        headers = {"Authorization": f"Bearer {token}"}
        urls = []
        if self.bboxes:
            for (lamin, lamax, lomin, lomax) in self.bboxes:
                q = urllib.parse.urlencode({
                    "lamin": lamin, "lamax": lamax, "lomin": lomin,
                    "lomax": lomax, "extended": 1})
                urls.append((API + "?" + q,
                             bbox_credit_cost(lamin, lamax, lomin, lomax)))
        else:
            urls.append((API + "?extended=1", GLOBAL_COST))
        states: dict[str, list] = {}
        got_time = None
        for url, cost in urls:
            try:
                status, body, rh = self._transport(
                    url, headers=headers, timeout=self.request_timeout_s)
            except NetworkBlocked:
                self._fail("network blocked (SUPERMAP_BLOCK_NETWORK)")
                return {"ok": False}
            except Exception as exc:
                self._fail(f"request failed ({type(exc).__name__})")
                return {"ok": False}
            self.last_status = status
            if status == 200:
                self.budget.record_call(cost, _hdr(rh, "X-Rate-Limit-Remaining"))
            elif status == 429:
                try:
                    ra = float(_hdr(rh, "X-Rate-Limit-Retry-After-Seconds")
                               or _hdr(rh, "Retry-After") or 0)
                except (TypeError, ValueError):
                    ra = 0.0
                self.budget.note_exhausted(ra or None)
                self._fail("HTTP 429: OpenSky credit budget exhausted",
                           state="SNAPSHOT", backoff=False)
                return {"ok": False}
            elif status in (401, 403):
                if self.token_provider is not None:
                    self.token_provider.invalidate()
                self._fail(f"HTTP {status}: bearer refused")
                return {"ok": False}
            else:
                self._fail(f"HTTP {status}")
                return {"ok": False}
            try:
                payload = json.loads(body.decode("utf-8", "replace"))
                rows = payload.get("states") or []
                if not isinstance(rows, list):
                    raise ValueError("states is not a list")
            except (ValueError, AttributeError):
                self._fail("response was not a states document")
                return {"ok": False}
            if isinstance(payload.get("time"), (int, float)):
                got_time = payload["time"]
            for r in rows:
                if isinstance(r, list) and r and isinstance(r[0], str):
                    states[r[0]] = r            # de-dupe across bboxes
        self.last_call_s = round(time.monotonic() - t0, 2)
        now = self.clock()
        stats = self.record_states(list(states.values()), now)
        self.ok_polls += 1
        self.consecutive_errors = 0
        self.last_error = None
        self.last_ok_at = now
        self.state = "LIVE"
        self.note = None
        self.publish_state(force=True)
        self.maybe_prune()
        stats["ok"] = True
        stats["server_time"] = got_time
        return stats

    # -- parsing ---------------------------------------------------------
    def record_states(self, rows: list, now: float) -> dict:
        """State vectors -> aircraft records -> hub (chunked)."""
        prov = {"source": SOURCE, "retrieved_at": iso_z(now), "mode": "LIVE"}
        skipped = {"no_position": 0, "stale_report": 0, "bad_row": 0}
        recs = []
        intern = sys.intern
        for r in rows:
            try:
                tp = r[3]
                lon, lat = r[5], r[6]
                if lat is None or lon is None or tp is None:
                    skipped["no_position"] += 1
                    continue
                if now - tp > self.max_report_age_s or tp - now > 120:
                    skipped["stale_report"] += 1
                    continue
                cs = r[1]
                recs.append({
                    "record_type": "aircraft_state", "icao24": r[0],
                    "callsign": cs.strip() if isinstance(cs, str) else None,
                    "origin_country": intern(r[2]) if isinstance(r[2], str)
                    else None,
                    "time_position": tp, "lat": lat, "lon": lon,
                    "baro_altitude": r[7], "geo_altitude": r[13],
                    "on_ground": r[8], "velocity": r[9], "true_track": r[10],
                    "vertical_rate": r[11],
                    "category": r[17] if len(r) > 17 else None,
                    "provenance": prov})
            except (IndexError, TypeError):
                skipped["bad_row"] += 1
        changed = rejected = 0
        for i in range(0, len(recs), CHUNK):
            st = self.hub.ingest(recs[i:i + CHUNK], prov)
            changed += st["changed"]
            rejected += st["rejected"]
        self.last_states = len(rows)
        self.last_recorded = len(recs) - rejected
        self.last_changed = changed
        self.last_skipped = skipped
        self.total_recorded += len(recs)
        return {"states": len(rows), "recorded": len(recs) - rejected,
                "changed": changed, "rejected": rejected,
                "skipped": skipped}

    def maybe_prune(self) -> None:
        now = time.monotonic()
        if now >= self._prune_at:
            self._prune_at = now + 30.0
            ttl = getattr(self.hub, "aircraft_ttl_s", None)
            if ttl:
                self.hub.prune_stale(aircraft_ttl_s=ttl)

    # -- failure ---------------------------------------------------------
    def _fail(self, msg: str, *, state: str = "SNAPSHOT",
              backoff: bool = True) -> None:
        self.errors += 1
        if backoff:
            self.consecutive_errors += 1
        self.last_error = msg[:160]
        self.state = state if self.last_ok_at is not None else "DOWN"
        self.note = f"OpenSky states collector: {self.last_error}"
        self.publish_state(force=True)

    # -- feed + status ---------------------------------------------------
    def interval_s(self) -> float:
        iv = self.budget.interval_s(self.call_cost)
        return iv if iv is not None else self.budget.max_interval_s

    def _live_window_s(self) -> float:
        return 3.0 * self.interval_s() + 30.0

    def publish_state(self, *, force: bool = False,
                      throttle: bool = False) -> None:
        now = self.clock()
        if throttle and now - getattr(self, "_pub_at", 0.0) < 10.0:
            return
        self._pub_at = now
        win = self._live_window_s()
        state = self.state
        if state == "LIVE" and (self.last_ok_at is None
                                or now - self.last_ok_at > win):
            state = "SNAPSHOT"
        prov = None
        if self.last_ok_at is not None:
            prov = {"source": SOURCE, "retrieved_at": iso_z(self.last_ok_at),
                    "mode": "LIVE"}
        note = self.note
        if state == "LIVE":
            note = (f"LIVE authenticated OpenSky /states/all, one "
                    f"{'global' if not self.bboxes else 'bbox'} call "
                    f"every ~{self.interval_s():.0f} s "
                    f"({self.call_cost} credits/call); per-aircraft "
                    "positions are the last report each aircraft sent "
                    "(OpenSky ADS-B coverage, not a census)")
        self.hub.set_collector_feed(
            self.feed, provenance=prov,
            records=len(self.hub.aircraft) if prov else None,
            note=note, adapter={"state": state, "note": note,
                                "max_age_s": win, "entity_max_age_s": win})

    def status(self) -> dict:
        now = self.clock()
        win = self._live_window_s()
        state = self.state
        if state == "LIVE" and (self.last_ok_at is None
                                or now - self.last_ok_at > win):
            state = "SNAPSHOT"
        return {
            "name": self.name, "state": state, "note": self.note,
            "thread_alive": self.alive(), "polls": self.polls,
            "ok_polls": self.ok_polls, "errors": self.errors,
            "consecutive_errors": self.consecutive_errors,
            "last_error": self.last_error, "last_http_status": self.last_status,
            "last_ok_age_s": None if self.last_ok_at is None
            else round(now - self.last_ok_at, 1),
            "last_call_duration_s": self.last_call_s,
            "states_in_last_response": self.last_states,
            "recorded_in_last_response": self.last_recorded,
            "changed_in_last_response": self.last_changed,
            "skipped_in_last_response": dict(self.last_skipped),
            "calls_shape": "global" if not self.bboxes else
            f"{len(self.bboxes)} bbox(es)",
            "interval_s": self.interval_s(),
            "next_poll_in_s": None if self._next_at is None
            else round(max(0.0, self._next_at - now), 1),
            "budget": self.budget.snapshot(self.call_cost),
        }
