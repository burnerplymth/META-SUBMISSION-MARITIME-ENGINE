"""Curated historical events from the repo's own replay records - offline.

``data/replay/*.json`` (the chain replay datasets) and
``data/sim_backtest/run_configs.json`` (the pre-registered sim backtest
configs) each pin a documented real disruption: node, kind, start,
duration, capacity loss and - the part that matters for honesty - the
BASIS of the capacity-loss figure (sourced / derived / modeled), with
its citation. This provider turns them into events so a user can
backplay the famous cases without any network call.

Mode: SNAPSHOT when the figure is sourced or derived arithmetic on
sourced figures; MODELED when the record says the figure is a modeled
assumption (la_2021's capacity_loss 0.78). The modeled basis text is
carried on the event verbatim - it must travel with any result.

Each entry also names ``watch_places``: the graph places whose PortWatch
daily series the realism tester compares (the seed place, its documented
spill-over places, and control places that should NOT move).
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone

from ..util import iso_day, parse_day
from .base import CAPABLE, HistoryProvider, NotServed, not_served

SOURCE_LABEL = "SupermAP curated replay record"
# The episode files state "retrieved 2026-10-20"; history
# derived from them is stamped with that fixed date, never "now".
DOSSIER_RETRIEVED = "2026-10-20T00:00:00Z"

# replay id -> (watch places, controls). Curated, small, public.
WATCH = {
    "red_sea_2024": {
        "watch": ["chk_babelmandeb", "chk_suez", "chk_capeofgoodhope",
                  "chk_gibraltar"],
        "controls": ["chk_malacca", "chk_hormuz"],
        "context": ("Gulf of Aden gross tonnage fell >70% (UNCTAD); Suez "
                    "transits fell, Cape of Good Hope rose. Malacca and "
                    "Hormuz are controls expected NOT to fall.")},
    "panama_2023_24": {
        "watch": ["chk_panama", "sea_balboa", "sea_colon", "chk_suez",
                  "chk_capeofgoodhope"],
        "controls": ["chk_malacca"],
        "context": ("Panama slots cut 36->18/day. NOTE: the Red Sea route diversion "
                    "overlaps this window from Dec 2023 and confounds Suez "
                    "and Cape of Good Hope; the grade says so.")},
    "la_2021": {
        "watch": ["sea_losangeles", "sea_oakland", "sea_nwsa", "sea_savannah",
                  "sea_newyork"],
        "controls": [],
        "context": ("A QUEUE/DWELL event: ships waited at anchor, berth "
                    "throughput did not collapse, so port-call counts need "
                    "not fall. Oakland/NWSA/Savannah/New York are the "
                    "documented diversion destinations.")},
    "ever_given": {
        "watch": ["chk_suez", "chk_capeofgoodhope"],
        "controls": ["chk_malacca"], "context": "Suez blocked 6.3 days."},
    "baltimore": {
        "watch": ["sea_baltimore", "sea_virginia"], "controls": [],
        "context": "Key Bridge collapse; Virginia = diversion destination."},
}


def _ts(day_iso: str) -> str:
    return day_iso if "T" in day_iso else day_iso + "T00:00:00Z"


class CuratedReplayProvider(HistoryProvider):
    name = "curated_replay"
    label = SOURCE_LABEL
    capability = CAPABLE
    budget_key = None
    supports_events = True
    history_window = "5 documented cases (offline, from the repo records)"
    verified = {
        "checked_on": "2026-10-25", "result": "OK (local files)",
        "how": "read data/replay/*.json and data/sim_backtest/run_configs.json",
    }
    notes = ("Local, offline, hand-curated from cited sources. A figure the "
             "curated record calls modeled makes the event mode MODELED.")

    def board_entry(self) -> dict:
        e = super().board_entry()
        e["budget"] = None
        return e

    # --------------------------------------------------------- loading
    def _catalogue(self) -> list[dict]:
        base = self.svc.data_dir
        out: list[dict] = []
        if base is None:
            return out
        for path in sorted((base / "replay").glob("*.json")):
            if path.name == "scorecard.json":
                continue
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            d = doc.get("disruption") if isinstance(doc, dict) else None
            if not isinstance(d, dict) or not doc.get("id"):
                continue
            basis = d.get("capacity_loss_basis") or {}
            out.append({
                "replay_id": doc["id"], "title": doc.get("title"),
                "node_id": d["node_id"], "kind": d["kind"],
                "capacity_loss": d.get("capacity_loss"),
                "severity": d.get("severity", 3),
                "started_at": _ts(d["started_at"]),
                "duration_d": d.get("expected_duration_d"),
                "basis_kind": basis.get("kind"),
                "basis_source": basis.get("source"),
                "basis_text": basis.get("derivation") or basis.get("basis"),
                "notes": doc.get("disruption_notes"),
                "origin": f"data/replay/{path.name}",
            })
        cfg_path = base / "sim_backtest" / "run_configs.json"
        try:
            cfgs = json.loads(cfg_path.read_text(encoding="utf-8")).get("events", {})
        except (OSError, ValueError):
            cfgs = {}
        have = {c["replay_id"] for c in out}
        for key in ("ever_given", "baltimore"):
            cfg = cfgs.get(key)
            if not cfg or key in have:
                continue
            dd = cfg["disruptions"][0]
            out.append({
                "replay_id": key,
                "title": {"ever_given": "Ever Given / Suez Canal closure, Mar 2021",
                          "baltimore": "Baltimore Key Bridge collapse, Mar-Jun 2024"}[key],
                "node_id": dd["node_id"], "kind": dd["kind"],
                "capacity_loss": dd.get("capacity_loss"), "severity": 5,
                "started_at": _ts(dd["started_at"]),
                "duration_d": dd.get("duration_d"),
                "basis_kind": "sourced", "basis_source":
                    "data/sim_backtest/preregistration.md (dated facts)",
                "basis_text": dd.get("basis"), "notes": None,
                "origin": "data/sim_backtest/run_configs.json",
            })
        out.sort(key=lambda c: (c["started_at"], c["replay_id"]))
        return out

    def catalogue(self) -> list[dict]:
        return self._catalogue()

    def entry(self, replay_id: str) -> dict | None:
        for c in self._catalogue():
            if c["replay_id"] == replay_id:
                return c
        return None

    # ----------------------------------------------------------- events
    def to_event(self, c: dict) -> dict:
        modeled = (c.get("basis_kind") == "modeled")
        mode = "MODELED" if modeled else "SNAPSHOT"
        started = c["started_at"]
        key = f"curated_replay:{c['replay_id']}"
        dur = c.get("duration_d")
        ev = {
            "id": "evt_%s_%s_%s_%s" % (
                started[:10].replace("-", ""), c["node_id"], c["kind"],
                hashlib.sha256(key.encode()).hexdigest()[:8]),
            "node_id": c["node_id"], "kind": c["kind"],
            "severity": int(c.get("severity") or 3),
            "started_at": started, "detected_at": started,
            "retrieved_at": DOSSIER_RETRIEVED,
            "source": SOURCE_LABEL + f" ({c['origin']})",
            "evidence_url": c.get("basis_source") or c["origin"],
            "status": "resolved", "mode": mode, "dedupe_key": key,
            "historical": True, "replay_id": c["replay_id"],
            "title": c.get("title"),
            "capacity_loss_basis": {"kind": c.get("basis_kind"),
                                    "source": c.get("basis_source"),
                                    "text": c.get("basis_text")},
            "provenance": {"source": SOURCE_LABEL + f" ({c['origin']})",
                           "retrieved_at": DOSSIER_RETRIEVED, "mode": mode},
        }
        if c.get("capacity_loss") is not None:
            ev["capacity_loss"] = float(c["capacity_loss"])
        if dur:
            ev["expected_duration_d"] = float(dur)
        if modeled:
            ev["modeled_notice"] = ("capacity_loss is a MODELED assumption, "
                                    "not a sourced figure - see "
                                    "capacity_loss_basis")
        w = WATCH.get(c["replay_id"])
        if w:
            ev["watch_places"] = w["watch"]
            ev["control_places"] = w["controls"]
            ev["watch_context"] = w["context"]
        return ev

    def events(self, start: date, end: date, places=None) -> dict:
        evs = []
        for c in self._catalogue():
            s = parse_day(c["started_at"])
            e = s + timedelta(days=float(c.get("duration_d") or 1))
            if s > end or e < start:
                continue
            if places and c["node_id"] not in set(places):
                continue
            evs.append(self.to_event(c))
        evs.sort(key=lambda e: (e["started_at"], e["id"]))
        return {"served": True, "source": self.name, "events": evs,
                "provenance": {"source": SOURCE_LABEL,
                               "retrieved_at": DOSSIER_RETRIEVED,
                               "mode": "SNAPSHOT"}}
