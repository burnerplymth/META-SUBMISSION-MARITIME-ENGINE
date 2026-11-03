"""Bounded request budget for upstream history calls.

Cache hits are free; every upstream request is charged to its source's
daily cap. The counter persists in ``<data>/history_budget.json`` (git-
ignored) so a restart does not reset it. A source over budget is NOT
SERVED (``budget_exhausted``) - the layer never exceeds the cap, and
never falls back to anything simulated.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path

from .util import today_utc

# Per-source daily caps. Deliberately small: history is pulled once and
# then served from the cache. GDELT's published ask is one request per 5 s
# and it rate-limits shared IPs (HTTP 429 observed).
DEFAULT_CAPS = {
    "imf_portwatch": 40,
    "gdacs": 20,
    "eia": 30,
    "un_comtrade": 10,
    "gdelt": 4,
    "opensky": 12,
    "open_meteo_archive": 20,
}


class RequestBudget:
    def __init__(self, path: str | Path | None = None,
                 caps: dict | None = None, today=None):
        self.path = Path(path) if path else None
        self.caps = dict(DEFAULT_CAPS if caps is None else caps)
        self._today = today or (lambda: today_utc().isoformat())
        self._lock = threading.Lock()
        self._mem: dict = {}

    def _read(self) -> dict:
        day = self._today()
        data = self._mem
        if self.path is not None:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
        if not isinstance(data, dict) or data.get("date") != day:
            data = {"date": day, "used": {}}
        if not isinstance(data.get("used"), dict):
            data["used"] = {}
        return data

    def _write(self, data: dict) -> None:
        self._mem = data
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, sort_keys=True)
            os.replace(tmp, self.path)
        except OSError:
            pass  # a read-only data dir must not take history down

    def used(self, source: str) -> int:
        with self._lock:
            return int(self._read()["used"].get(source, 0))

    def cap(self, source: str) -> int:
        return int(self.caps.get(source, 0))

    def remaining(self, source: str) -> int:
        return max(0, self.cap(source) - self.used(source))

    def charge(self, source: str) -> bool:
        """Reserve one upstream request; False when the cap is reached."""
        with self._lock:
            data = self._read()
            used = int(data["used"].get(source, 0))
            if used >= self.cap(source):
                return False
            data["used"][source] = used + 1
            self._write(data)
            return True

    def snapshot(self) -> dict:
        with self._lock:
            data = self._read()
        return {s: {"cap": self.cap(s), "used": int(data["used"].get(s, 0)),
                    "remaining": max(0, self.cap(s) - int(data["used"].get(s, 0)))}
                for s in sorted(self.caps)} | {"date": data["date"]}
