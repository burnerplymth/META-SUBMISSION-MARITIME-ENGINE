"""Sources the repo touches that have NO usable history.

They are listed on the capability board so the answer is explicit, and
they answer every historical query NOT SERVED (``current_only``) - the
live events store (``GET /events``) and the live adapters are where their
present-tense data lives, and the timeline shows that as the
"live-equivalent view", whose stored events are labelled RECORDED -
never LIVE, never history.
"""
from __future__ import annotations

from datetime import date

from .base import CURRENT_ONLY, HistoryProvider, not_served


class _CurrentOnly(HistoryProvider):
    capability = CURRENT_ONLY
    supports_events = True
    supports_observations = True

    def events(self, start: date, end: date, places=None) -> dict:
        return not_served(self.name, "current_only", self.notes)

    def observations(self, place, metric, start, end) -> dict:
        return not_served(self.name, "current_only", self.notes,
                          place=place, metric=metric)

    def board_entry(self) -> dict:
        e = super().board_entry()
        e["supports"] = []
        return e


class NoaaNwsProvider(_CurrentOnly):
    name = "noaa_nws"
    label = "NOAA/NWS alerts"
    history_window = "rolling ~7 days only"
    verified = {
        "checked_on": "2026-10-25", "result": "CURRENT-ONLY (rolling 7 days)",
        "how": ("api.weather.gov/alerts?start=&end= returned 294-471 alerts "
                "for 6-hour windows 1, 2, 3, 5 and 7 days back and 0 for "
                "8 days back, 10 days back, 2026-01-01 and 2021-08-29"),
    }
    notes = ("The NWS alert API keeps roughly the last 7 days; anything "
             "older is gone upstream. What the live adapter recorded is in "
             "GET /events (mode LIVE) and appears in the timeline's "
             "live-equivalent view only.")


class AisStreamProvider(_CurrentOnly):
    name = "aisstream"
    label = "AISStream"
    history_window = "none (live WebSocket only)"
    verified = {
        "checked_on": "2026-10-25", "result": "CURRENT-ONLY",
        "how": ("wss://stream.aisstream.io/v0/stream is a live push "
                "stream; it has no history endpoint, so none was probed"),
    }
    notes = ("AISStream offers a live position stream only. Vessel history "
             "for a past date comes from PortWatch's AIS-derived daily "
             "counts, not from here.")
