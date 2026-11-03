"""What a stored event may be CALLED now.

The stored `mode` of an event says how it was obtained when it was
obtained; a genuine NOAA fetch from last week keeps `mode: LIVE` in
data/events.jsonl forever, and that file is never rewritten. A response
that is read now may only say LIVE when all three hold:

  1. the stored mode is LIVE,
  2. the feed that produced the item is LIVE on the health board now,
  3. the item was retrieved within that feed's max age.

Otherwise the display word is SNAPSHOT (the feed is serving a stored copy)
or RECORDED (the feed is down / unknown, or the item is old). Served as the
additive field `display_mode`; the stored `mode` is untouched.

The tables mirror server/live/hub.py so the tape,
the board and the event list can never disagree. The web app applies the
same rule in web/app/src/shell/truthLabel.ts.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

SOURCE_FEED = {"NOAA/NWS": "noaa", "GDACS": "gdacs",
               "OpenSky Network": "opensky", "AISStream": "aisstream",
               "IMF PortWatch": "portwatch", "CBP Border Wait Times": "cbp"}
DEFAULT_MAX_AGE_S = {"noaa": 3600.0, "gdacs": 21600.0, "opensky": 3600.0,
                     "aisstream": 3600.0, "portwatch": 86400.0,
                     "cbp": 3600.0}
FALLBACK_MAX_AGE_S = 21600.0
SKEW_S = 300.0


def _now() -> float:
    return time.time()


def _parse(ts) -> float | None:
    if not isinstance(ts, str) or not ts:
        return None
    try:
        d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.timestamp()


def feed_of(source) -> str | None:
    return SOURCE_FEED.get(source) if isinstance(source, str) else None


def display_mode(stored_mode, feed_state, retrieved_at, now, source=None):
    """The word an item may wear now, or None when it states no mode."""
    stored = str(stored_mode or "").strip().upper()
    if not stored:
        return None
    if stored in ("MODELED", "MODELLED"):
        return "MODELLED"
    if stored == "SNAPSHOT":
        return "SNAPSHOT"
    if stored != "LIVE":
        return "RECORDED"
    t = _parse(retrieved_at)
    max_age = DEFAULT_MAX_AGE_S.get(feed_of(source) or "", FALLBACK_MAX_AGE_S)
    fresh = t is not None and -SKEW_S <= now - t <= max_age
    if feed_state == "LIVE" and fresh:
        return "LIVE"
    if feed_state == "SNAPSHOT":
        return "SNAPSHOT"
    return "RECORDED"


def with_display_mode(ev: dict, feed_states: dict, now: float | None = None) -> dict:
    """A copy of ``ev`` with the additive `display_mode` (omitted when the
    event states no mode). ``feed_states`` maps adapter name -> health state."""
    if not isinstance(ev, dict):
        return ev
    prov = ev.get("provenance") if isinstance(ev.get("provenance"), dict) else {}
    source = ev.get("source") or prov.get("source")
    word = display_mode(
        ev.get("mode") or prov.get("mode"),
        feed_states.get(feed_of(source) or ""),
        ev.get("retrieved_at") or prov.get("retrieved_at"),
        _now() if now is None else now, source)
    if word is None:
        return ev
    return {**ev, "display_mode": word}
