"""Glue between the stores, the history service and the pure grader:
resolve a stored run, gather the observed series (cache first), grade,
and optionally store the card.

Resolution order for ``GET /realism?run=<id>``:
  1. the run is in the chain run store (``run_...``) or the sim store
     (``sim_...``): grade it LIVE against the observed series (cached
     PortWatch history, or a budgeted fetch when allowed);
  2. otherwise a STORED card for that run id (a tracked fixture or an
     earlier POST /realism): served as-is with ``source: STORED`` so the
     demo works offline on a checkout without the run files;
  3. otherwise 404 - the tester never grades a run it cannot read.
"""
from __future__ import annotations

from datetime import date, timedelta

from .fork import ForkLog
from .realism import (RULE, RealismStore, build_card, digest_chain,
                      digest_sim)
from .service import HistoryService
from .util import (HistoryError, add_days, iso_day, parse_day, utcnow_iso)

DEFAULT_LEAD_PLOT_D = 14
DEFAULT_TAIL_D = 14
MAX_WINDOW_D = 400


class RunNotFound(Exception):
    pass


class RealismService:
    def __init__(self, history: HistoryService, store: RealismStore,
                 chain_loader, sim_loader, fork_log: ForkLog | None = None):
        self.history = history
        self.store = store
        self.chain_loader = chain_loader      # run_id -> record | None
        self.sim_loader = sim_loader          # sim_run_id -> record | None
        self.fork_log = fork_log

    # ----------------------------------------------------------- events
    def _match_event(self, node_id: str, started_at: str):
        day = parse_day(started_at)
        for c in self.history.replay.catalogue():
            if c["node_id"] == node_id and parse_day(c["started_at"]) == day:
                return c
        return None

    def _places_for(self, node_id: str, matched, requested):
        book = self.history.book()
        places: list[dict] = []
        seen = set()

        def add(p, role):
            if p not in seen and p in book:
                seen.add(p)
                places.append({"place": p, "role": role})

        if requested:
            for p in requested:
                add(p, "seed" if p == node_id else "watch")
            return places
        add(node_id, "seed")
        if matched:
            from .providers.replay_catalogue import WATCH
            w = WATCH.get(matched["replay_id"], {})
            for p in w.get("watch", []):
                add(p, "watch")
            for p in w.get("controls", []):
                add(p, "control")
        else:
            near = book.hops_from(node_id, 1)
            for p in sorted(near, key=lambda x: (near[x], x)):
                if p != node_id and book.portwatch_series(p) is not None \
                        and len(places) < 6:
                    add(p, "watch")
        return places

    # --------------------------------------------------------- evaluate
    def evaluate(self, run_id: str, frm=None, to=None, places=None,
                 title: str | None = None, store: bool = False) -> dict:
        record, kind = None, None
        if isinstance(run_id, str) and run_id.startswith("sim_"):
            record, kind = self.sim_loader(run_id), "sim"
        elif isinstance(run_id, str) and run_id.startswith("run_"):
            record, kind = self.chain_loader(run_id), "chain"
        if record is None:
            stored = self.store.find_by_run(run_id)
            if stored:
                card = dict(stored[0])
                card["source"] = "STORED"
                card["served_note"] = (
                    "the run file is not in this run store; this is the stored "
                    "result card computed when the run was available (its "
                    "prediction digest and observed-series hashes are inside)")
                return card
            raise RunNotFound(run_id)

        if kind == "chain":
            pred_meta = record["journal"]["disruption"]
            node_id, started = pred_meta["node_id"], pred_meta.get("started_at")
            duration = pred_meta.get("duration_d")
            capacity_loss = pred_meta.get("capacity_loss")
        else:
            disr = record["journal"].get("disruptions") or []
            if not disr:
                raise HistoryError("this sim run has no disruption; nothing "
                                   "historical to grade", "no_disruption")
            first = sorted(disr, key=lambda d: (d.get("started_at") or "", d["node_id"]))[0]
            node_id, started = first["node_id"], first.get("started_at")
            duration = first.get("duration_d")
            capacity_loss = first.get("capacity_loss")
        if not started:
            raise HistoryError("the run's disruption has no started_at; only "
                               "dated (historical) runs can be graded",
                               "undated_run")
        event_start = parse_day(started)
        matched = self._match_event(node_id, started)
        plist = self._places_for(node_id, matched, places)
        if not plist:
            raise HistoryError("no places to compare", "no_places")

        window_from = parse_day(frm, "from") if frm else add_days(
            event_start, -DEFAULT_LEAD_PLOT_D)
        if to:
            window_to = parse_day(to, "to")
        else:
            window_to = add_days(event_start, int(min(
                (duration or 30) + DEFAULT_TAIL_D, 150)))
        if window_to < window_from:
            raise HistoryError("to must be on or after from")
        if (window_to - window_from).days > MAX_WINDOW_D:
            raise HistoryError(f"window longer than {MAX_WINDOW_D} days",
                               "window_too_large")
        notes: list[str] = []
        if kind == "sim":
            ids = [p["place"] for p in plist]
            pred = digest_sim(record, ids)
            sim_end = None
            if pred["series"]:
                sim_end = max(max(s) for s in pred["series"].values() if s)
            if sim_end and parse_day(sim_end) < window_to:
                window_to = parse_day(sim_end)
                notes.append(f"window end clipped to the sim horizon "
                             f"({sim_end})")
            if len(record["journal"].get("disruptions") or []) > 1:
                notes.append("the sim has several disruptions; the earliest "
                             "is the event whose start anchors the baseline")
        else:
            pred = digest_chain(record)
        book = self.history.book()
        hops = book.hops_from(node_id, 12)

        lo = add_days(event_start, -RULE["baseline_days"])
        observed = {}
        for pl in plist:
            r = self.history.portwatch.observations(
                pl["place"], "activity", min(lo, window_from), window_to)
            if r.get("served"):
                observed[pl["place"]] = {
                    "served": True, "metric": r["metric"], "unit": r["unit"],
                    "series": {x["date"]: x["value"] for x in r["series"]},
                    "provenance": r["provenance"], "cache": r["cache"]}
            else:
                observed[pl["place"]] = r
        event = None
        if matched:
            event = self.history.replay.to_event(matched)
            if event.get("mode") == "MODELED":
                notes.append("the event's capacity_loss basis is MODELED: "
                             + event["capacity_loss_basis"]["text"][:240])
            if event.get("watch_context"):
                notes.append(event["watch_context"])
        else:
            event = {"node_id": node_id, "started_at": started,
                     "note": "no curated catalogue entry matches this run's "
                             "disruption; the run's own journal is the event"}
        forked = None
        if self.fork_log is not None:
            forked = self.fork_log.find(run_id) or \
                self.fork_log.find_by_event(node_id, started)
        card = build_card(
            pred=pred, observed=observed, event_start=event_start,
            window_from=window_from, window_to=window_to, places=plist,
            hops=hops, seed_place=node_id, capacity_loss=capacity_loss,
            duration_d=duration,
            event=({**event, "event_id": event.get("id")} if event else None),
            title=title or (matched or {}).get("title"), notes=notes,
            forked_from=forked)
        card["source"] = "COMPUTED"
        if store:
            card = dict(card)
            card["stored_at"] = utcnow_iso()
            self.store.save(card)
            card["source"] = "COMPUTED+STORED"
        return card
