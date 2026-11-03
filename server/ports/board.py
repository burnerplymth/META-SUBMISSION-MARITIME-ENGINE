"""The ports board: one row per place, every cell with its own
provenance. Built per request from stored packs (no network, no
background state); deterministic for a fixed data root and clock.
"""
from __future__ import annotations

from collections import Counter
from datetime import timedelta

try:  # source citations
    from store.citations import derive_citation
except ImportError:  # imported as server.ports.* from the repo root
    from server.store.citations import derive_citation  # type: ignore
from impact.derive import ImpactService
from impact.store import ImpactStore

from . import regions, rules
from .cells import assert_cells_honest, cell, from_figure, not_served
from .feeds import Feeds, iso, parse_ts
from .sources import (ACTIVE_STATUSES, TICK_SECONDS, Ctx, load_graph,
                      read_events, read_exposures, read_satwatch)

PW_SRC = "IMF PortWatch daily layers (AIS-based estimates)"
PW_DB_SRC = "IMF PortWatch ports database"
CBP_SRC = "CBP Border Wait Times"
OPENSKY_SRC = "OpenSky Network"
AIS_SRC = "AISStream"
EVENTS_SRC = "SupermAP events store (NOAA/NWS, GDACS)"
SAT_SRC = "Microsoft Planetary Computer / Sentinel-2 L2A (satwatch scan)"
EVENT_FRESH_S = {"NOAA/NWS": 3600, "GDACS": 21600}

TIER_LABELS = {
    "core": "CORE: calibration-grade place",
    "shell": ("SHELL: not calibration-grade; capacity is a labelled "
              "ESTIMATE or NOT PUBLISHED"),
}

SORT_KEYS = ("name", "id", "status", "capacity", "activity", "ratio",
             "events", "chain")
STATUS_FILTERS = (tuple(l["label"] for l in rules.RULE["levels"])
                  + (rules.RULE["no_data_label"],))

# which cells a place carries, by kind (cells that cannot apply to a
# kind are not served for it at all — they are not "absent data")
CELLS_BY_KIND = {
    "port": ("activity", "activity_ratio", "ais_samples", "satwatch",
             "events"),
    "chokepoint": ("activity", "ais_samples", "events"),
    "air_hub": ("hub_aircraft", "events"),
    "ground_node": ("border_wait", "events"),
}
CLASS_KEYS = ("container", "dry_bulk", "general_cargo", "roro", "tanker")
RUN_CELLS = {"chain_exposure": "run_id", "simulated": "sim_run_id"}


class BoardError(Exception):
    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _event_headline(ev: dict) -> str | None:
    if isinstance(ev.get("nws"), dict):
        return ev["nws"].get("headline") or ev["nws"].get("event")
    if isinstance(ev.get("gdacs"), dict):
        return ev["gdacs"].get("name")
    return None


class Board:
    def __init__(self, ctx: Ctx, run_id: str | None = None,
                 sim_run_id: str | None = None, tick: int | None = None):
        self.ctx = ctx
        self.now = ctx.now
        self.graph = load_graph(ctx.graph_path)
        self.feeds = Feeds(ctx.snapshots_dir, self.now)
        self.impact_store = ImpactStore(
            snapshots_dir=ctx.snapshots_dir, impact_dir=ctx.impact_dir,
            graph_path=ctx.graph_path)
        self.impact = ImpactService(self.impact_store)
        self.run_id, self.sim_run_id, self.tick = run_id, sim_run_id, tick
        self._events = None
        self._chain = None
        self._sim = None
        self._indexes = None
        self._ev_all = None     # (feed state, newest stamp) over ALL events: one value per board, not per place
        self._sat = None
        self._run_record = None
        self.states = {n: self.feeds.state(n) for n in
                       ("portwatch", "portwatch_daily", "cbp", "opensky",
                        "aisstream", "noaa", "gdacs")}
        if run_id is not None:
            self._load_chain()
        if sim_run_id is not None:
            self._load_sim()

    # ------------------------------------------------------------ runs
    def _load_chain(self):
        store = self.ctx.run_store
        rec = store.load(self.run_id) if store is not None else None
        if rec is None:
            raise BoardError("run_not_found",
                             f"unknown run_id: {self.run_id!r}", 404)
        self._run_record = rec
        try:
            payload = self.impact.run_payload(rec)
        except ValueError as exc:
            raise BoardError("impact_unavailable",
                             f"impact derivation failed: {exc}", 500)
        per_place = {}
        for unit in payload["volume_at_risk"]["by_unit"]:
            for p in unit["places"]:
                per_place[p["node_id"]] = {**p, "unit_class": unit["unit"]}
        self._chain = {"payload": payload, "places": per_place}

    def _load_sim(self):
        store = self.ctx.sim_store
        rec = store.load(self.sim_run_id) if store is not None else None
        if rec is None:
            raise BoardError("unknown_sim_run",
                             f"unknown sim_run_id: {self.sim_run_id!r}", 404)
        ticks = rec["journal"]["ticks"]
        tick = self.tick if self.tick is not None else ticks - 1
        if not 0 <= tick < ticks:
            raise BoardError("invalid_request",
                             f"tick out of range 0..{ticks - 1}")
        self.tick = tick
        self._sim = rec

    # ------------------------------------------------------------ data
    def events(self) -> list[dict] | None:
        if self._events is None:
            evs = read_events(self.ctx.events_path, self.now)
            self._events = evs if evs is not None else False
        return None if self._events is False else self._events

    def _event_feed_state(self, evs: list[dict]) -> str:
        newest = None
        for e in evs:
            ts = parse_ts(e.get("retrieved_at"))
            if ts and (newest is None or ts > newest):
                newest = ts
        if newest is None:
            return "SNAPSHOT"
        return "LIVE" if (self.now - newest).total_seconds() <= 3600 \
            else "SNAPSHOT"

    def _idx(self) -> dict:
        if self._indexes is not None:
            return self._indexes
        f = self.feeds
        ix: dict = {}
        ix["activity"] = {r["node_id"]: r for r in
                          f.records("portwatch", "port_activity")
                          if r.get("node_id")}
        ix["cbp"] = {}
        for r in f.records("cbp", "border_wait"):
            if r.get("node_id"):
                ix["cbp"].setdefault(r["node_id"], []).append(r)
        ix["air"] = {r["node_id"]: r for r in
                     f.records("opensky", "air_traffic") if r.get("node_id")}
        ix["ais"] = {}
        for r in f.records("aisstream", "ais_position"):
            if r.get("node_id"):
                ix["ais"].setdefault(r["node_id"], []).append(r)
        ix["pw_disruptions"] = f.records("portwatch", "portwatch_disruption")
        daily = self.impact_store.daily()
        ix["daily"] = daily
        evs = self.events()
        by_node: dict = {}
        if evs:
            for e in evs:
                if e.get("status") in ACTIVE_STATUSES and e.get("node_id"):
                    by_node.setdefault(e["node_id"], []).append(e)
        ix["events_by_node"] = by_node
        self._indexes = ix
        return ix

    def satwatch(self) -> dict:
        if self._sat is None:
            self._sat = read_satwatch(self.ctx.satwatch_dir)
        return self._sat

    # ------------------------------------------------------------ cells
    def _feed_state(self, name: str) -> str:
        return self.states[name]["state"]

    def _activity_cell(self, node: dict):
        nid, kind = node["id"], node["kind"]
        daily = self._idx()["daily"]
        st = self.states["portwatch_daily"]
        if kind == "port":
            rec = daily["ports"].get(nid)
            if rec is None:
                return not_served(
                    "no PortWatch daily-layer record for this port in the "
                    "stored pack", unit="port calls/day (30-day mean)",
                    source=PW_SRC, feed_state=st["state"])
            return cell(
                rec["portcalls_per_day"]["total"],
                "port calls/day (30-day mean)", "SOURCED", PW_SRC,
                rec["window"]["end"], feed_state=st["state"],
                note=(f"AIS-based estimate; window {rec['window']['start']} "
                      f"to {rec['window']['end']} "
                      f"({rec['window']['days_present']} of "
                      f"{rec['window']['window_days']} days present)"))
        rec = daily["chokepoints"].get(nid)
        if rec is None:
            return not_served(
                "this strait is not in the PortWatch daily chokepoint "
                "layer", unit="vessel transits/day (30-day mean)",
                source=PW_SRC, feed_state=st["state"])
        return cell(
            rec["transits_per_day"]["total"],
            "vessel transits/day (30-day mean)", "SOURCED", PW_SRC,
            rec["window"]["end"], feed_state=st["state"],
            note=(f"AIS-based estimate; window {rec['window']['start']} to "
                  f"{rec['window']['end']} ({rec['window']['days_present']} "
                  f"of {rec['window']['window_days']} days present)"))

    def _ratio(self, node: dict):
        """(ratio|None, reason|None, inputs) for PS-1."""
        nid = node["id"]
        ix = self._idx()
        rec = ix["daily"]["ports"].get(nid)
        act = ix["activity"].get(nid)
        if rec is None:
            return None, "no PortWatch daily-layer record", {}
        if act is None:
            return None, ("no PortWatch ports-database record "
                          "(annual baseline)"), {}
        calls = rec["portcalls_per_day"]["total"]
        days = rec["window"]["days_present"]
        base = act.get("vessel_count_total")
        ratio, reason = rules.ratio_inputs(calls, days, base)
        return ratio, reason, {"portcalls_per_day": calls,
                               "days_present": days,
                               "baseline_calls": base}

    def _ratio_cell(self, node: dict):
        ratio, reason, inp = self._ratio(node)
        if ratio is None:
            return not_served(reason, unit="ratio to annual baseline",
                              source=PW_DB_SRC + " + daily layers")
        return cell(
            round(ratio, 4), "ratio to annual baseline", "MODELLED",
            "SupermAP rule PORTS-STATUS PS-1 over " + PW_SRC + " and "
            + PW_DB_SRC, self.states["portwatch_daily"]["retrieved_at"],
            feed_state=self._feed_state("portwatch_daily"),
            note=(f"{inp['portcalls_per_day']} calls/day x 365 / "
                  f"{inp['baseline_calls']} annual baseline calls. "
                  "A ratio of the 30-day window to the ports-database "
                  "annual count, not a forecast."))

    def _wait(self, nid: str):
        """(worst_wait|None, reason|None, crossings[])"""
        recs = self._idx()["cbp"].get(nid, [])
        st = self.states["cbp"]
        if st["state"] == "NOT SERVED":
            return None, st["reason"], recs
        if not recs:
            return None, ("not a CBP-reported crossing (CBP covers US "
                          "land-border ports only)"), recs
        waits = [r["wait_min"] for r in recs
                 if r.get("lanes") == "commercial"
                 and isinstance(r.get("wait_min"), (int, float))]
        if not waits:
            return None, ("CBP reports no commercial-lane wait for any "
                          "crossing here"), recs
        return max(waits), None, recs

    def _border_cell(self, node: dict):
        worst, reason, recs = self._wait(node["id"])
        st = self.states["cbp"]
        if worst is None:
            return not_served(reason, unit="minutes (commercial lanes)",
                              source=CBP_SRC, as_of=st.get("retrieved_at"),
                              feed_state=st["state"])
        n = sum(1 for r in recs
                if isinstance(r.get("wait_min"), (int, float))
                and r.get("lanes") == "commercial")
        return cell(worst, "minutes (commercial lanes, worst crossing)",
                    "SOURCED", CBP_SRC, st["retrieved_at"],
                    feed_state=st["state"],
                    note=f"worst of {n} reporting commercial crossing(s)")

    def _air_cell(self, node: dict):
        st = self.states["opensky"]
        rec = self._idx()["air"].get(node["id"])
        if st["state"] == "NOT SERVED":
            return not_served(st["reason"], unit="aircraft in hub box",
                              source=OPENSKY_SRC, feed_state="NOT SERVED")
        if rec is None:
            return not_served("this hub is not in the OpenSky watch set",
                              unit="aircraft in hub box", source=OPENSKY_SRC,
                              as_of=st.get("retrieved_at"),
                              feed_state=st["state"])
        return cell(rec["aircraft_in_bbox"], "aircraft in hub box",
                    "SOURCED", OPENSKY_SRC,
                    (rec.get("provenance") or {}).get("retrieved_at")
                    or st["retrieved_at"], feed_state=st["state"],
                    note="aircraft count inside the hub's box at observation "
                         "time; not a cargo-flight count")

    def _ais_cell(self, node: dict):
        st = self.states["aisstream"]
        if st["state"] == "NOT SERVED":
            return not_served(st["reason"], unit="vessel position samples",
                              source=AIS_SRC, as_of=st.get("retrieved_at"),
                              feed_state="NOT SERVED")
        recs = self._idx()["ais"].get(node["id"])
        if not recs:
            return not_served(
                "no AIS sample fell in this place's watch area (AIS covers "
                "a watch list only)", unit="vessel position samples",
                source=AIS_SRC, as_of=st.get("retrieved_at"),
                feed_state=st["state"])
        return cell(len(recs), "vessel position samples", "SOURCED", AIS_SRC,
                    st["retrieved_at"], feed_state=st["state"],
                    note="latest position per vessel in the watch area, "
                         "capped per area by the adapter: a sample, not a "
                         "vessel count")

    def _sat_cell(self, node: dict):
        scan = self.satwatch().get(node["id"])
        if scan is None:
            return not_served("no satellite scan stored for this place",
                              unit="large vessels detected", source=SAT_SRC)
        det = scan.get("detection") or {}
        dates = scan.get("imagery_dates") or {}
        if det.get("counts") is None:
            return not_served(
                f"scan stored (imagery {dates.get('baseline')} to "
                f"{dates.get('now')}) but detection is "
                f"{det.get('status') or 'not run'}: "
                f"{det.get('note') or 'no counts served'}",
                unit="large vessels detected", source=SAT_SRC,
                as_of=scan.get("retrieved_at"), feed_state="SNAPSHOT")
        return cell(det["counts"], "large vessels detected", "SOURCED",
                    SAT_SRC, dates.get("now"), feed_state="SNAPSHOT",
                    note=f"provider {det.get('provider')}; "
                         f"{det.get('countable_class')}")

    def _events_cell(self, node: dict):
        evs = self.events()
        if evs is None:
            return not_served("events store is absent or unreadable",
                              unit="active events", source=EVENTS_SRC)
        mine = self._idx()["events_by_node"].get(node["id"], [])
        if self._ev_all is None:
            # Both values depend on the whole event list only (and the board's fixed `now`): computing them for every
            # one of the ~1,000 places re-parsed every timestamp ~1,000 times per request (profiled, 55 percent of /ports).
            stamps = [e.get("retrieved_at") for e in evs if e.get("retrieved_at")]
            self._ev_all = (self._event_feed_state(evs), max(stamps) if stamps else None)
        fs, newest_stamp = self._ev_all
        sev = max((int(e.get("severity") or 0) for e in mine), default=None)
        return cell(len(mine), "active events", "SOURCED", EVENTS_SRC,
                    newest_stamp, feed_state=fs,
                    note=(f"max severity {sev} of 5" if mine else
                          "no active event at this place in the store"),
                    max_severity=sev)

    def _chain_cell(self, node: dict):
        p = self._chain["places"].get(node["id"])
        src = f"SupermAP chain run {self.run_id} + impact derivation"
        if p is None:
            return cell({"on_chain": False}, "chain exposure", "MODELLED",
                        src, None,
                        note="this place carries no event in the run's "
                             "causal chain")
        cap = p["capacity_at_risk"]
        out = {"on_chain": True,
               "worst_magnitude": p["worst_magnitude"]["value"],
               "max_delay_d": p["max_delay"]["value"],
               "hits": p["hits"], "unit_class": p["unit_class"],
               "capacity_at_risk": cap["value"],
               "capacity_at_risk_unit": cap.get("unit")}
        if cap["value"] is None:
            out["capacity_at_risk_reason"] = cap.get("reason")
        return cell(out, "share of capacity + days", "MODELLED", src, None,
                    note="per-place, in the place's own unit class; no "
                         "total across places or classes is computed")

    def _sim_cell(self, node: dict):
        rec = self._sim
        series = (rec.get("node_series") or {}).get(node["id"])
        src = f"SupermAP simulation run {self.sim_run_id}"
        if series is None:
            return not_served("this place is not in the simulation run's "
                              "place set", unit="simulated queue state",
                              source=src)
        t = self.tick
        j = rec["journal"]
        start = parse_ts(j["start_at"])
        at = iso(start + timedelta(seconds=TICK_SECONDS[j["tick"]] * t))
        return cell({"tick": t, "queue": series["queue"][t],
                     "berthed": series["berthed"][t],
                     "arrivals": series["arrivals"][t],
                     "departures": series["departures"][t]},
                    "simulated entities", "SIMULATED", src, at,
                    note="SIMULATED: a value from a stored simulation, not "
                         "an observation")

    # ------------------------------------------------------------ rows
    def _status(self, node: dict) -> dict:
        kind = node["kind"]
        ratio, ratio_reason, _ = (self._ratio(node) if kind == "port"
                                  else (None, None, {}))
        worst, wait_reason, _ = (self._wait(node["id"])
                                 if kind == "ground_node"
                                 else (None, None, []))
        mine = self._idx()["events_by_node"].get(node["id"], [])
        sev = max((int(e.get("severity") or 0) for e in mine), default=None)
        out = rules.derive(kind, activity_ratio=ratio,
                           ratio_reason=ratio_reason, worst_wait_min=worst,
                           wait_reason=wait_reason, max_event_severity=sev)
        out["chip"] = "MODELLED"
        return out

    def _capacity_cell(self, node: dict):
        fig = self.impact._capacity_figure(node)
        c = from_figure(fig)
        basis = node.get("capacity_basis")
        c["basis"] = basis or "CENSUS"
        if c["chip"] != "NOT SERVED" and node.get("tier") == "shell":
            c["note"] = ("SHELL place: labelled ESTIMATE, not "
                         "calibration-grade. " + (c.get("note") or ""))
        if c["chip"] != "NOT SERVED" and node.get("tier") == "core":
            c["note"] = ("CORE place (calibration-grade tier): capacity "
                         "from the build census sources listed.")
        return c

    def row(self, node: dict) -> dict:
        kind = node["kind"]
        cells = {}
        for name in CELLS_BY_KIND.get(kind, ("events",)):
            cells[name] = {
                "activity": self._activity_cell,
                "activity_ratio": self._ratio_cell,
                "ais_samples": self._ais_cell,
                "satwatch": self._sat_cell,
                "events": self._events_cell,
                "hub_aircraft": self._air_cell,
                "border_wait": self._border_cell,
            }[name](node)
        if self._chain is not None:
            cells["chain_exposure"] = self._chain_cell(node)
        if self._sim is not None:
            cells["simulated"] = self._sim_cell(node)
        country = node.get("country")
        return {
            "place_id": node["id"], "name": node.get("name"),
            "kind": kind, "mode": node.get("mode"),
            "country": country,
            "region": regions.region_of(node["id"], country),
            "lat": node.get("lat"), "lon": node.get("lon"),
            "tier": node.get("tier"),
            "tier_label": TIER_LABELS.get(node.get("tier"), None),
            "capacity": self._capacity_cell(node),
            "status": self._status(node),
            "cells": cells,
        }

    def all_nodes(self) -> list[dict]:
        if self.graph is None:
            return []
        return [self.graph.nodes[k] for k in sorted(self.graph.nodes)]

    # ------------------------------------------------------------ list
    def list_rows(self, *, mode=None, kind=None, tier=None, region=None,
                  country=None, status=None, q=None, has_events=None,
                  sort="name", order="asc", limit=50, offset=0) -> dict:
        if sort not in SORT_KEYS:
            raise BoardError("invalid_request",
                             f"sort must be one of {list(SORT_KEYS)}")
        if sort == "chain" and self._chain is None:
            raise BoardError("invalid_request",
                             "sort=chain needs a run_id")
        if order not in ("asc", "desc"):
            raise BoardError("invalid_request", "order must be asc or desc")
        for name, vals, allowed in (
                ("mode", mode, ("sea", "air", "ground")),
                ("kind", kind, tuple(rules_kinds())),
                ("tier", tier, ("core", "shell")),
                ("region", region, regions.REGIONS),
                ("status", status, STATUS_FILTERS)):
            if vals:
                bad = [v for v in vals if v not in allowed]
                if bad:
                    raise BoardError(
                        "invalid_request",
                        f"{name} must be one of {list(allowed)} "
                        f"(got {bad[0]!r})")
        ql = q.strip().lower() if q else None
        rows = []
        for node in self.all_nodes():
            if mode and node.get("mode") not in mode:
                continue
            if kind and node.get("kind") not in kind:
                continue
            if tier and node.get("tier") not in tier:
                continue
            if country and (node.get("country") or "").lower() \
                    not in [c.lower() for c in country]:
                continue
            if region and regions.region_of(
                    node["id"], node.get("country")) not in region:
                continue
            if ql:
                hay = " ".join([node["id"], node.get("name") or "",
                                node.get("country") or ""]
                               + [str(a) for a in node.get("aliases") or []]
                               ).lower()
                if ql not in hay:
                    continue
            r = self.row(node)
            if status and r["status"]["label"] not in status:
                continue
            if has_events is not None:
                n = r["cells"].get("events", {}).get("value") or 0
                if (n > 0) != has_events:
                    continue
            rows.append(r)

        def key(r):
            c = r["cells"]
            if sort == "name":
                v = (r["name"] or "").lower()
            elif sort == "id":
                v = r["place_id"]
            elif sort == "status":
                v = r["status"]["level"]
            elif sort == "capacity":
                v = r["capacity"]["value"]
            elif sort == "activity":
                v = c.get("activity", {}).get("value")
            elif sort == "ratio":
                v = c.get("activity_ratio", {}).get("value")
            elif sort == "events":
                v = c.get("events", {}).get("value")
            else:  # chain
                cv = c["chain_exposure"]["value"]
                v = cv.get("worst_magnitude") if cv.get("on_chain") else None
            return v

        present = [r for r in rows if key(r) is not None]
        absent = [r for r in rows if key(r) is None]
        # stable: place_id ascending inside equal keys, whatever the
        # direction; places with no value for the key always sort last
        present.sort(key=lambda r: r["place_id"])
        present.sort(key=key, reverse=(order == "desc"))
        absent.sort(key=lambda r: r["place_id"])
        ordered = present + absent
        total = len(ordered)
        page = ordered[offset:offset + limit]
        return {"total": total, "limit": limit, "offset": offset,
                "places": page}

    # ------------------------------------------------------------ detail
    def detail(self, place_id: str) -> dict | None:
        node = self.graph.nodes.get(place_id) if self.graph else None
        if node is None:
            return None
        base = self.row(node)
        ix = self._idx()
        kind = node["kind"]
        series: dict = {}

        # --- PortWatch (sea ports + chokepoints)
        st_d = self.states["portwatch_daily"]
        st_p = self.states["portwatch"]
        if kind == "port":
            rec = ix["daily"]["ports"].get(place_id)
            act = ix["activity"].get(place_id)
            pw: dict = {}
            if rec is None:
                pw["window_means"] = not_served(
                    "no PortWatch daily-layer record", source=PW_SRC,
                    feed_state=st_d["state"])
            else:
                w = rec["window"]
                pw["window"] = w
                for label, block, unit in (
                        ("port_calls_per_day", rec["portcalls_per_day"],
                         "port calls/day (30-day mean)"),
                        ("import_tonnes_per_day", rec["import_tonnes_per_day"],
                         "tonnes/day (30-day mean, AIS estimate)"),
                        ("export_tonnes_per_day", rec["export_tonnes_per_day"],
                         "tonnes/day (30-day mean, AIS estimate)")):
                    # every block is served per vessel class only: the
                    # layer's own class-merged "cargo"/"total" keys are
                    # dropped (goods classes are never merged; the board
                    # row's activity cell carries the call count)
                    keep = CLASS_KEYS
                    pw[label] = {
                        k: cell(v, unit, "SOURCED", PW_SRC, w["end"],
                                feed_state=st_d["state"],
                                note="PortWatch class label: "
                                     + str(rec.get("figure_class")))
                        for k, v in block.items() if k in keep}
            if act is None:
                pw["annual_baseline_calls"] = not_served(
                    "no PortWatch ports-database record", source=PW_DB_SRC,
                    feed_state=st_p["state"])
            else:
                pw["annual_baseline_calls"] = {
                    k.replace("vessel_count_", ""): cell(
                        v, "vessel calls per year (baseline year not "
                        "served in the pack)", "SOURCED", PW_DB_SRC,
                        st_p["retrieved_at"], feed_state=st_p["state"])
                    for k, v in act.items()
                    if k.startswith("vessel_count_")
                    and k.replace("vessel_count_", "") in CLASS_KEYS}
            pw["daily_series"] = not_served(
                "the repository mirrors 30-day window means only; the raw "
                "daily rows are not stored, so no daily series is served",
                source=PW_SRC, feed_state=st_d["state"])
            series["portwatch"] = pw
        elif kind == "chokepoint":
            rec = ix["daily"]["chokepoints"].get(place_id)
            if rec is None:
                series["portwatch"] = {"window_means": not_served(
                    "not in the PortWatch daily chokepoint layer",
                    source=PW_SRC, feed_state=st_d["state"])}
            else:
                w = rec["window"]
                series["portwatch"] = {
                    "window": w,
                    "transits_per_day": {
                        k: cell(v, "vessel transits/day (30-day mean)",
                                "SOURCED", PW_SRC, w["end"],
                                feed_state=st_d["state"])
                        for k, v in rec["transits_per_day"].items()
                        if k in CLASS_KEYS},
                    "daily_series": not_served(
                        "30-day window means only; no daily series is "
                        "mirrored", source=PW_SRC,
                        feed_state=st_d["state"])}

        # --- CBP crossings
        if kind == "ground_node":
            st = self.states["cbp"]
            crossings = []
            for r in ix["cbp"].get(place_id, []):
                def wcell(val, unit):
                    if isinstance(val, (int, float)):
                        return cell(val, unit, "SOURCED", CBP_SRC,
                                    st["retrieved_at"],
                                    feed_state=st["state"],
                                    note=f"reported {r.get('date')} "
                                         f"{r.get('time')} (CBP local)")
                    return not_served("CBP reports no value for this lane",
                                      unit=unit, source=CBP_SRC,
                                      as_of=st["retrieved_at"],
                                      feed_state=st["state"])
                crossings.append({
                    "crossing": r.get("crossing"),
                    "port_name": r.get("port_name"),
                    "lanes": r.get("lanes"),
                    "wait": wcell(r.get("wait_min"), "minutes"),
                    "ready_lane_wait": wcell(r.get("fast_wait_min"),
                                             "minutes"),
                    "lanes_open": wcell(r.get("lanes_open"), "lanes")})
            series["border_crossings"] = crossings if crossings else \
                not_served("not a CBP-reported crossing", source=CBP_SRC,
                           feed_state=st["state"])

        # --- AIS samples
        if kind in ("port", "chokepoint"):
            st = self.states["aisstream"]
            recs = ix["ais"].get(place_id, [])
            if st["state"] == "NOT SERVED" or not recs:
                series["ais_samples"] = not_served(
                    st["reason"] or "no AIS sample in this place's watch "
                    "area", source=AIS_SRC, feed_state=st["state"])
            else:
                series["ais_samples"] = {
                    "count": len(recs),
                    "sample": [{
                        "mmsi": r.get("mmsi"), "ship_name": r.get("ship_name"),
                        "lat": r.get("lat"), "lon": r.get("lon"),
                        "sog_kn": r.get("sog"),
                        "reported_at": r.get("reported_at"),
                        "provenance": cell(
                            True, "AIS position", "SOURCED", AIS_SRC,
                            r.get("reported_at"), feed_state=st["state"])}
                        for r in sorted(recs, key=lambda x: str(x.get("mmsi")))
                        [:20]]}

        # --- satwatch
        if kind == "port":
            scan = self.satwatch().get(place_id)
            series["satwatch"] = ({
                "scan": {"name": scan.get("name"),
                         "imagery_dates": scan.get("imagery_dates"),
                         "usable_pair": scan.get("usable_pair"),
                         "resolution_m": scan.get("resolution_m"),
                         "crops": scan.get("crops"),
                         "retrieved_at": scan.get("retrieved_at")},
                "count": self._sat_cell(node)} if scan else
                {"count": self._sat_cell(node)})

        # --- events
        evs = self.events()
        if evs is None:
            series["events"] = not_served("events store absent or "
                                          "unreadable", source=EVENTS_SRC)
        else:
            out = []
            for e in ix["events_by_node"].get(place_id, []):
                fresh = EVENT_FRESH_S.get(e.get("source"), 3600)
                ts = parse_ts(e.get("retrieved_at"))
                live = (e.get("mode") == "LIVE" and ts is not None
                        and (self.now - ts).total_seconds() <= fresh)
                cit = derive_citation(e)  # only when a page exists
                out.append({
                    **({"citation": cit} if cit else {}),
                    "id": e.get("id"), "kind": e.get("kind"),
                    "severity": e.get("severity"), "status": e.get("status"),
                    "source": e.get("source"),
                    "headline": _event_headline(e),
                    "started_at": e.get("started_at"),
                    "expires_at": e.get("expires_at"),
                    "detected_at": e.get("detected_at"),
                    "evidence_url": e.get("evidence_url"),
                    "provenance": cell(
                        e.get("severity"), "severity 1-5", "SOURCED",
                        e.get("source"), e.get("retrieved_at"),
                        feed_state="LIVE" if live else "SNAPSHOT")
                    if e.get("severity") is not None else not_served(
                        "event carries no severity", source=e.get("source"))})
            series["events"] = {"active": out, "count": len(out)}

        # --- PortWatch historical disruptions naming this port
        if kind == "port":
            pid = (ix["daily"]["ports"].get(place_id) or {}).get("portid") \
                or (ix["activity"].get(place_id) or {}).get("portid")
            hist = []
            if pid:
                tag = f"({pid})"
                for d in ix["pw_disruptions"]:
                    if tag in str(d.get("affected_ports") or ""):
                        hist.append({
                            "event": d.get("eventname"),
                            "type": d.get("eventtype"),
                            "alert_level": d.get("alertlevel"),
                            "from": d.get("fromdate"), "to": d.get("todate"),
                            "provenance": cell(
                                d.get("alertlevel"), "GDACS alert level",
                                "SOURCED", PW_DB_SRC, st_p["retrieved_at"],
                                feed_state=st_p["state"],
                                note="historical disruption listed by "
                                     "PortWatch")})
            series["portwatch_disruption_history"] = hist[:10]

        # --- goods composition (impact layer figures, as cells)
        impact_node = self.impact.node_payload(place_id)
        if impact_node is not None:
            g = impact_node["goods"]
            series["goods_composition"] = _figure_tree_to_cells(g)

        # --- nearby legs
        legs = []
        for e in self.graph.legs_of(place_id):
            outbound = e.get("from") == place_id
            other = e.get("to") if outbound else e.get("from")
            on = self.graph.nodes.get(other, {})
            cal = e.get("calibration") or {}
            legs.append({
                "direction": "outbound" if outbound else "inbound",
                "other_place_id": other, "other_name": on.get("name"),
                "other_tier": on.get("tier"),
                "mode": e.get("mode"),
                "base_transit_d": e.get("base_transit_d"),
                "capacity_share": cell(
                    e.get("capacity_share"), "share of origin capacity",
                    "MODELLED", "SupermAP network build (leg share)", None,
                    note=cal.get("share_method"))
                if e.get("capacity_share") is not None else not_served(
                    "leg carries no capacity share", source="network build"),
                "basis": cal.get("basis")})
        legs.sort(key=lambda l: (
            -(l["capacity_share"]["value"] or 0)
            if l["capacity_share"]["value"] is not None else 0,
            l["other_place_id"], l["direction"]))
        series["legs"] = {"count": len(legs), "nearby": legs[:40],
                          "shown": min(len(legs), 40)}

        # --- exposures (filing-verbatim, unpriced)
        exp = read_exposures(self.ctx.exposures_path)
        if exp is not None:
            mine = [m for m in exp["mappings"] if m.get("node_id") == place_id]
            if mine:
                series["money_exposure"] = [{
                    "ticker": m.get("ticker"), "company": m.get("company"),
                    "exposure_kind": m.get("exposure_kind"),
                    "filing_excerpt_verbatim": (m.get("filing") or {})
                    .get("excerpt"),
                    "filing": cell(
                        (m.get("filing") or {}).get("url"), "filing URL",
                        "SOURCED", f"SEC EDGAR {(m.get('filing') or {}).get('form')}",
                        (m.get("filing") or {}).get("filed_date"),
                        feed_state="SNAPSHOT"),
                    "price": not_served(
                        "UNPRICED: this product serves no market price "
                        "for this company", source="none")}
                    for m in mine]

        # --- run overlays
        if self._chain is not None:
            evs_here = [e for e in self._run_record["events"]
                        if e.get("node_id") == place_id]
            series["chain_events"] = {
                "run_id": self.run_id,
                "events": evs_here,
                "provenance": cell(len(evs_here), "chain events",
                                   "MODELLED",
                                   f"SupermAP chain run {self.run_id}", None)}
        if self._sim is not None:
            s = (self._sim.get("node_series") or {}).get(place_id)
            if s is None:
                series["simulated_series"] = not_served(
                    "place not in the simulation run",
                    source=f"SupermAP simulation run {self.sim_run_id}")
            else:
                n = len(s["queue"])
                stride = max(1, n // 200)
                idx = list(range(0, n, stride))
                series["simulated_series"] = {
                    "sim_run_id": self.sim_run_id, "stride": stride,
                    "ticks": idx,
                    "queue": [s["queue"][i] for i in idx],
                    "berthed": [s["berthed"][i] for i in idx],
                    "provenance": cell(
                        n, "ticks in stored series", "SIMULATED",
                        f"SupermAP simulation run {self.sim_run_id}", None,
                        note="SIMULATED: not an observation")}

        out = {**base, "series": series, "rule": rules.RULE,
               "feeds": [self.states[k] for k in sorted(self.states)]}
        assert_cells_honest(out)
        return out

    # ------------------------------------------------------------ summary
    def summary(self) -> dict:
        counts: Counter = Counter()
        by_kind: Counter = Counter()
        by_tier: Counter = Counter()
        by_status_kind: dict = {}
        for node in self.all_nodes():
            st = self._status(node)
            counts[st["label"]] += 1
            by_kind[node["kind"]] += 1
            by_tier[node.get("tier")] += 1
            by_status_kind.setdefault(node["kind"], Counter())[st["label"]] += 1
        order = [l["label"] for l in rules.RULE["levels"]] + \
            [rules.RULE["no_data_label"]]
        return {
            "places": sum(by_kind.values()),
            "by_status": {k: counts.get(k, 0) for k in order},
            "by_status_and_kind": {
                k: {s: v.get(s, 0) for s in order}
                for k, v in sorted(by_status_kind.items())},
            "by_kind": dict(sorted(by_kind.items())),
            "by_tier": dict(sorted(by_tier.items())),
            "rule": rules.RULE,
            "feeds": [self.states[k] for k in sorted(self.states)],
            "events_store": self._events_store_state(),
            "satwatch": {"scans": len(self.satwatch()),
                         "with_counts": sum(
                             1 for s in self.satwatch().values()
                             if (s.get("detection") or {}).get("counts")
                             is not None)},
            "as_of": iso(self.now),
        }

    def _events_store_state(self) -> dict:
        evs = self.events()
        if evs is None:
            return {"state": "NOT SERVED", "records": 0,
                    "reason": "events store absent or unreadable"}
        active = sum(1 for e in evs if e.get("status") in ACTIVE_STATUSES)
        return {"state": self._event_feed_state(evs), "records": len(evs),
                "active": active, "reason": None}


def rules_kinds():
    return ("port", "chokepoint", "air_hub", "ground_node")


# fields of the impact layer's per-place goods block that merge goods
# classes into one tonnage/transit total: never re-served by the ports
# tracker (goods classes are never merged into one total)
CLASS_MERGED_FIELDS = frozenset({
    "total_tonnes_per_day", "transits_per_day_total",
    "transit_tonnes_per_day_total"})


def _figure_tree_to_cells(obj):
    """Impact figure objects -> cells, preserving the tree (class-merged
    totals dropped)."""
    from impact.figures import is_figure
    if is_figure(obj):
        return from_figure(obj, feed_state="SNAPSHOT")
    if isinstance(obj, dict):
        return {k: _figure_tree_to_cells(v) for k, v in obj.items()
                if k not in CLASS_MERGED_FIELDS}
    if isinstance(obj, list):
        return [_figure_tree_to_cells(v) for v in obj]
    return obj
