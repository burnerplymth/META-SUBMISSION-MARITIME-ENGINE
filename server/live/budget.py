"""OpenSky credit budget: explicit, persisted, self-pacing.

OpenSky's authenticated tier gives 4,000 credits per UTC day. A
``/states/all`` call costs 1-4 credits by bbox area (a global call is 4;
MEASURED: three consecutive global calls moved
``X-Rate-Limit-Remaining`` 3301 -> 3297 -> 3293, ``extended=1`` costs the
same). The response header ``X-Rate-Limit-Remaining`` is the authority —
the same account is also spent by the hub-count adapter and by anyone
else holding the client — so pacing is driven by what the server says is
left, not by our own tally.

Pacing law (``next_delay``), for a call costing ``c`` credits:

    spendable = remaining - reserve - adapter_future
    interval  = seconds_left_in_utc_day * c / spendable   (clamped)

  * ``reserve`` = ``reserve_fraction`` x daily limit (default 20 percent,
    800 credits): never touched, headroom for the adapter, other
    consumers and measurement error;
  * ``adapter_future`` = the hub-count adapter's expected remaining spend
    today (default 480 credits/day: 5 credits per 900 s cycle);
  * clamped to [``min_interval_s``, ``max_interval_s``];
  * ``spendable < c`` => stop until the next UTC midnight (+ a margin).

At the start of a UTC day: (4000 - 800 - 480) / 4 = 680 global calls =
one every 127 s. The interval re-solves after every response, so spend
of other consumers is absorbed instead of overrun.

Persistence: a small JSON file (no secrets) so a restart does not forget
what was spent; the first call of a fresh process (empty fleet) keeps only
a 30 s floor from the persisted last call, later calls follow the interval.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

DAY_S = 86400.0


def utc_day(now: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(now))


def next_utc_midnight(now: float) -> float:
    return (int(now) // 86400 + 1) * 86400.0


class CreditBudget:
    def __init__(self, *, daily_limit: int = 4000,
                 reserve_fraction: float = 0.20,
                 adapter_daily_credits: int = 480,
                 min_interval_s: float = 60.0, max_interval_s: float = 900.0,
                 first_call_floor_s: float = 30.0,
                 clock=time.time, state_path=None):
        self.daily_limit = int(daily_limit)
        self.reserve_fraction = float(reserve_fraction)
        self.adapter_daily_credits = int(adapter_daily_credits)
        self.min_interval_s = float(min_interval_s)
        self.max_interval_s = float(max_interval_s)
        self.first_call_floor_s = float(first_call_floor_s)
        self.clock = clock
        self.state_path = Path(state_path) if state_path else None
        self._lock = threading.Lock()
        self.day = utc_day(clock())
        self.spent = 0                 # credits this collector spent today
        self.calls = 0                 # calls this collector made today
        self.remaining: int | None = None   # server-reported, last seen
        self.remaining_at: float | None = None
        self.last_call_at: float | None = None
        self.last_cost: int | None = None   # measured delta (info only)
        self.stopped_until: float | None = None
        self._load()

    # -- persistence -----------------------------------------------------
    def _load(self) -> None:
        if self.state_path is None:
            return
        try:
            d = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(d, dict) or d.get("date") != self.day:
            return
        try:
            self.spent = int(d.get("spent") or 0)
            self.calls = int(d.get("calls") or 0)
            rem = d.get("remaining")
            self.remaining = int(rem) if rem is not None else None
            self.remaining_at = d.get("remaining_at")
            self.last_call_at = d.get("last_call_at")
        except (TypeError, ValueError):
            self.spent = self.calls = 0

    def _save(self) -> None:
        if self.state_path is None:
            return
        pack = {"date": self.day, "spent": self.spent, "calls": self.calls,
                "remaining": self.remaining, "remaining_at": self.remaining_at,
                "last_call_at": self.last_call_at,
                "daily_limit": self.daily_limit}
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(pack), encoding="utf-8")
            os.replace(tmp, self.state_path)
        except OSError:
            pass            # bookkeeping only; never take the poll down

    def _roll(self, now: float) -> None:
        day = utc_day(now)
        if day != self.day:
            self.day, self.spent, self.calls = day, 0, 0
            self.remaining = self.remaining_at = None
            self.stopped_until = None

    # -- accounting ------------------------------------------------------
    def record_call(self, cost: int, remaining_header=None) -> None:
        """A call was made and answered. ``cost`` is the documented
        credit cost; ``remaining_header`` the raw X-Rate-Limit-Remaining
        value (None when absent)."""
        with self._lock:
            now = self.clock()
            self._roll(now)
            self.spent += int(cost)
            self.calls += 1
            self.last_call_at = now
            try:
                rem = int(str(remaining_header).strip())
            except (TypeError, ValueError):
                rem = None
            if rem is not None:
                if self.remaining is not None and self.remaining_at and \
                        utc_day(self.remaining_at) == self.day:
                    self.last_cost = self.remaining - rem
                self.remaining, self.remaining_at = rem, now
            elif self.remaining is not None:
                self.remaining = max(0, self.remaining - int(cost))
            self._save()

    def note_exhausted(self, retry_after_s: float | None = None) -> None:
        """HTTP 429: the server says we are out. Stop until the hinted
        time (capped at the next UTC midnight + margin)."""
        with self._lock:
            now = self.clock()
            self._roll(now)
            self.remaining, self.remaining_at = 0, now
            until = next_utc_midnight(now) + 30.0
            if retry_after_s is not None and retry_after_s > 0:
                until = min(until, now + retry_after_s + 5.0)
            self.stopped_until = until
            self._save()

    # -- pacing ----------------------------------------------------------
    @property
    def reserve(self) -> int:
        return int(self.daily_limit * self.reserve_fraction)

    def remaining_estimate(self) -> int:
        """Server-reported remaining when known, else the daily limit
        less our own spend and the adapter's expected spend so far."""
        if self.remaining is not None:
            return self.remaining
        now = self.clock()
        elapsed = (now % DAY_S) / DAY_S
        return max(0, self.daily_limit - self.spent
                   - int(self.adapter_daily_credits * elapsed))

    def spendable(self, now: float | None = None) -> int:
        now = self.clock() if now is None else now
        left_frac = max(0.0, (next_utc_midnight(now) - now) / DAY_S)
        adapter_future = int(self.adapter_daily_credits * left_frac)
        return self.remaining_estimate() - self.reserve - adapter_future

    def next_delay(self, cost: int, first: bool = False) -> float:
        """Seconds to wait before the next call (>= 0). Honours the
        spacing from the previous call, so a restart cannot fire early.
        ``first`` is the first call of a fresh process: its in-memory
        fleet is empty, so waiting a whole pacing interval would leave
        the aircraft layer blank; it only keeps ``first_call_floor_s``
        (30 s) from the persisted last call. Budget stops and the
        reserve still apply (a restart loop cannot spend the reserve)."""
        with self._lock:
            now = self.clock()
            self._roll(now)
            if self.stopped_until and now < self.stopped_until:
                return self.stopped_until - now
            spendable = self.spendable(now)
            if spendable < cost:
                # budget used up for today: sleep to the next UTC day
                return next_utc_midnight(now) + 30.0 - now
            secs_left = max(1.0, next_utc_midnight(now) - now)
            interval = secs_left * cost / spendable
            interval = min(self.max_interval_s,
                           max(self.min_interval_s, interval))
            if self.last_call_at is None:
                return 0.0
            if first:
                interval = min(interval, self.first_call_floor_s)
            return max(0.0, self.last_call_at + interval - now)

    def interval_s(self, cost: int) -> float | None:
        """The pacing interval right now (None when stopped)."""
        with self._lock:
            now = self.clock()
            spendable = self.spendable(now)
            if spendable < cost:
                return None
            secs_left = max(1.0, next_utc_midnight(now) - now)
            return round(min(self.max_interval_s, max(
                self.min_interval_s, secs_left * cost / spendable)), 1)

    def exhausted(self, cost: int) -> bool:
        with self._lock:
            now = self.clock()
            self._roll(now)
            return bool(self.stopped_until and now < self.stopped_until) \
                or self.spendable(now) < cost

    def snapshot(self, cost: int = 4) -> dict:
        with self._lock:
            now = self.clock()
            self._roll(now)
            spendable = self.spendable(now)
            return {
                "day": self.day, "daily_limit": self.daily_limit,
                "remaining": self.remaining,
                "remaining_age_s": None if self.remaining_at is None
                else round(now - self.remaining_at, 1),
                "remaining_estimate": self.remaining_estimate(),
                "reserve": self.reserve,
                "adapter_daily_credits": self.adapter_daily_credits,
                "spendable_today": max(0, spendable),
                "collector_spent_today": self.spent,
                "collector_calls_today": self.calls,
                "last_call_cost_measured": self.last_cost,
                "call_cost_assumed": cost,
                "pacing_interval_s": None if spendable < cost else round(
                    min(self.max_interval_s, max(
                        self.min_interval_s,
                        max(1.0, next_utc_midnight(now) - now) * cost
                        / spendable)), 1),
                "stopped_until": self.stopped_until,
                "resets_at_utc": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime(next_utc_midnight(now))),
            }
