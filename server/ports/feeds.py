"""Feed access for the ports tracker: stored snapshot packs under the
app's snapshots dir, with a published state per feed.

State derivation (mechanical, no network):
  NOT SERVED  pack file absent/unreadable, or an adapter note says it
              needs a key and holds no records
  LIVE        the pack was fetched live (provenance.mode == LIVE) AND
              its retrieved_at is within the feed's freshness window
  SNAPSHOT    anything else that has a pack (a stored dated copy; a
              LIVE record that aged past the window reads SNAPSHOT and
              carries stale=True)

The freshness windows mirror the adapters' own health_max_age_s
(server/adapters/*.py) so this board never calls a stale pack LIVE.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

# feed name -> (file stem, label, freshness window seconds, serves)
FEEDS = {
    "portwatch": ("portwatch", "IMF PortWatch (ports database)", 86400,
                  "annual vessel-call baseline per sea port"),
    "portwatch_daily": ("portwatch_daily",
                        "IMF PortWatch (daily layers, 30-day window means)",
                        172800,
                        "port calls + goods tonnage by class; strait transits"),
    "cbp": ("cbp", "CBP Border Wait Times", 3600,
            "commercial-lane waits at US land border crossings"),
    "opensky": ("opensky", "OpenSky Network", 3600,
                "aircraft counted in each air hub's box"),
    "aisstream": ("aisstream", "AISStream", 3600,
                  "vessel position samples in watched areas"),
    "noaa": ("noaa", "NOAA/NWS alerts", 3600, "weather hazard events"),
    "gdacs": ("gdacs", "GDACS", 21600, "disaster alert events"),
}


def parse_ts(text):
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


class Feeds:
    """Reads the packs once per instance (one request = one instance)."""

    def __init__(self, snapshots_dir: Path, now: datetime | None = None):
        self.dir = Path(snapshots_dir)
        self.now = now or datetime.now(timezone.utc)
        self._packs: dict = {}

    def pack(self, name: str) -> dict | None:
        if name not in self._packs:
            stem = FEEDS[name][0]
            doc = load_json(self.dir / f"{stem}.json")
            self._packs[name] = doc if isinstance(doc, dict) else None
        return self._packs[name]

    def records(self, name: str, record_type: str | None = None) -> list:
        doc = self.pack(name)
        if not doc or not isinstance(doc.get("records"), list):
            return []
        recs = [r for r in doc["records"] if isinstance(r, dict)]
        if record_type is not None:
            recs = [r for r in recs if r.get("record_type") == record_type]
        return recs

    def state(self, name: str) -> dict:
        """The published state of one feed."""
        stem, label, max_age, serves = FEEDS[name]
        base = {"feed": name, "label": label, "serves": serves,
                "max_age_s": max_age}
        doc = self.pack(name)
        if doc is None:
            return {**base, "state": "NOT SERVED", "retrieved_at": None,
                    "age_s": None, "stale": None, "records": 0,
                    "reason": f"no stored pack for this feed "
                              f"(expected snapshots/{stem}.json)"}
        recs = doc.get("records") if isinstance(doc.get("records"), list) \
            else []
        prov = doc.get("provenance") or {}
        retrieved = prov.get("retrieved_at") or doc.get("saved_at")
        note = doc.get("note")
        if not recs and isinstance(note, str) and "NEEDS-KEY" in note:
            return {**base, "state": "NOT SERVED", "retrieved_at": retrieved,
                    "age_s": None, "stale": None, "records": 0,
                    "reason": note}
        ts = parse_ts(retrieved)
        age = (self.now - ts).total_seconds() if ts else None
        fresh = age is not None and age <= max_age
        live = prov.get("mode") == "LIVE" and fresh
        return {**base, "state": "LIVE" if live else "SNAPSHOT",
                "mode_recorded": prov.get("mode"),
                "retrieved_at": retrieved,
                "age_s": None if age is None else round(age, 1),
                "stale": (not fresh) if age is not None else None,
                "records": len(recs), "reason": None}

    def all_states(self) -> list[dict]:
        return [self.state(n) for n in sorted(FEEDS)]
