"""IMPACT derivations. Every number served by the API is
computed here, from the snapshot inputs + the run record + the
graph, and leaves as a Figure (figures.py). Every figure carries its working as a SHORT `derivation`
one-liner plus a `label` and `chips` (state tokens such as `30D MEAN`,
`AIS ESTIMATE`): the working is shown with the number (the TEU IN PATH
precedent), as labelled fields, never as paragraphs of prose.

Vocabulary note: display copy authored here
names places by name/kind and speaks of legs/routes — never of
"nodes", "edges" or "graphs". Field names are server internals and
are unaffected.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from .figures import absent, assert_payload_honest, figure
from .comtrade_agg import UNASSIGNED, build_region_of, has_partners, region_shares
from .store import ImpactStore

PORTWATCH_SOURCE = "IMF PortWatch daily layers (AIS-based estimates, snapshot)"
GOODS_LABEL = "SOURCED — PORTWATCH AIS ESTIMATE"
CLASSES = ("container", "dry_bulk", "general_cargo", "roro", "tanker")
CLASS_LABELS = {"container": "container", "dry_bulk": "dry bulk",
                "general_cargo": "general cargo", "roro": "RoRo",
                "tanker": "tanker"}
CMD_LABELS = {"TOTAL": "all goods (TOTAL)", "27": "mineral fuels & oils (HS 27)",
              "2709": "crude petroleum oils (HS 2709)",
              "2711": "petroleum gases incl. LNG (HS 2711)",
              "85": "electrical machinery & electronics (HS 85)",
              "8542": "electronic integrated circuits (HS 8542)",
              "87": "vehicles & parts (HS 87)",
              "10": "cereals (HS 10)"}
WATCHLIST_THRESHOLD = 0.40
WATCHLIST_HEADER = "Concentration through affected places · exposure only"
NO_FORWARD_CLAIM = "Context only · no price forecast"
CHIPS_30D = ["30D MEAN", "AIS ESTIMATE"]


COMTRADE_SOURCE = ("UN Comtrade (derived shares, cached at build; "
                   "not the original records)")


def _r(value: float, nd: int = 6) -> float:
    return round(float(value), nd)


def _parse_ts(text):
    if not text:
        return None
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None


def _shares(block: dict, total_key: str = "total") -> dict | None:
    total = block.get(total_key) or 0
    if total <= 0:
        return None
    return {c: _r((block.get(c) or 0) / total) for c in CLASSES}


def _mix_figures(mix: dict | None, denominator: str, window: dict,
                 source: str, retrieved_at, weight_note: str = "") -> dict:
    """Per-class share figures for one mix, or one ABSENT figure."""
    if mix is None:
        return {"mix": absent(
            "window total is zero in the source",
            unit=f"share of {denominator}", source=source, as_of=retrieved_at,
            label="Mix")}
    out = {}
    for c in CLASSES:
        out[c] = figure(
            mix[c], f"share of {denominator}", "SOURCED", source, retrieved_at,
            f"{CLASS_LABELS[c]} ÷ total · 30d mean {window['start']} → "
            f"{window['end']} · {window['days_present']}/"
            f"{window['window_days']} days{weight_note}",
            label=f"{CLASS_LABELS[c]} share", chips=CHIPS_30D)
    return out


class ImpactService:
    def __init__(self, store: ImpactStore):
        self.store = store

    # ------------------------------------------------------------------
    # Goods at risk — per place
    # ------------------------------------------------------------------
    def node_goods(self, node_id: str) -> dict:
        nodes = self.store.graph_nodes()
        node = nodes.get(node_id)
        daily = self.store.daily()
        retrieved = daily["retrieved_at"]
        if node is not None and node.get("kind") == "port":
            rec = daily["ports"].get(node_id)
            if rec is not None:
                return self._port_goods(rec, retrieved)
            return self._port_goods_fallback(node_id, retrieved)
        if node is not None and node.get("kind") == "chokepoint":
            rec = daily["chokepoints"].get(node_id)
            if rec is not None:
                return self._chokepoint_goods(rec, retrieved)
            return {"state": "ABSENT",
                    "goods": absent(
                        "not in the PortWatch chokepoint layer",
                        source=PORTWATCH_SOURCE, as_of=retrieved,
                        label="Transit mix")}
        # Air and ground places print ABSENT for goods composition —
        # forbidden to allocate sea shares onto them.
        return {"state": "ABSENT",
                "goods": absent(
                    "goods mix is served for sea places only",
                    source=PORTWATCH_SOURCE, as_of=retrieved,
                    label="Goods mix")}

    def _port_goods(self, rec: dict, retrieved) -> dict:
        window = rec["window"]
        imp, exp = rec["import_tonnes_per_day"], rec["export_tonnes_per_day"]
        combined = {k: _r((imp.get(k) or 0) + (exp.get(k) or 0), 2)
                    for k in list(CLASSES) + ["cargo", "total"]}
        src = PORTWATCH_SOURCE
        tonnage = {}
        for direction, block in (("imports", imp), ("exports", exp)):
            for c in CLASSES:
                tonnage[f"{direction}_{c}"] = figure(
                    block[c], "tonnes/day (PortWatch AIS estimate)", "SOURCED",
                    src, retrieved,
                    f"30d mean · {direction} {CLASS_LABELS[c]} · "
                    f"{window['start']} → {window['end']}",
                    label=f"{direction} {CLASS_LABELS[c]}", chips=CHIPS_30D)
        return {
            "state": "SOURCED",
            "figure_class": GOODS_LABEL,
            "window": window,
            "port": rec.get("portname"),
            "country": rec.get("country"),
            "iso3": rec.get("iso3"),
            "tonnes_per_day": tonnage,
            "total_tonnes_per_day": figure(
                combined["total"], "tonnes/day (PortWatch AIS estimate)",
                "SOURCED", src, retrieved,
                f"imports + exports · 30d mean · {window['start']} → "
                f"{window['end']}", label="Total tonnage", chips=CHIPS_30D),
            "portcalls_per_day": figure(
                rec["portcalls_per_day"]["total"], "port calls/day",
                "SOURCED", src, retrieved,
                f"daily port calls · 30d mean · {window['start']} → "
                f"{window['end']}", label="Port calls", chips=["30D MEAN"]),
            "import_mix": _mix_figures(_shares(imp), "import tonnes/day",
                                       window, src, retrieved),
            "export_mix": _mix_figures(_shares(exp), "export tonnes/day",
                                       window, src, retrieved),
            "combined_mix": _mix_figures(_shares(combined),
                                         "import+export tonnes/day",
                                         window, src, retrieved),
        }

    def _port_goods_fallback(self, node_id: str, retrieved) -> dict:
        """The pinned call-mix shares from the graph
        build's PortWatch join input, labelled with its own source and
        retrieval date — never upgraded to daily-data status."""
        join = self.store.join_input()
        rec = join["records"].get(node_id)
        cov = self.store.daily()["coverage"] or {}
        if rec is None:
            if node_id in (cov.get("ports_unmatched_no_portid") or []):
                reason = "no PortWatch match · no call mix"
            elif node_id in (cov.get("ports_mapped_no_daily_rows") or []):
                reason = "no daily rows · no call mix"
            else:
                reason = "no daily record · no call mix"
            return {"state": "ABSENT",
                    "goods": absent(reason, source=PORTWATCH_SOURCE,
                                    as_of=retrieved, label="Goods mix")}
        counts = {"container": rec.get("vessel_count_container") or 0,
                  "dry_bulk": rec.get("vessel_count_dry_bulk") or 0,
                  "general_cargo": rec.get("vessel_count_general_cargo") or 0,
                  "tanker": rec.get("vessel_count_tanker") or 0}
        total = sum(counts.values())
        src = ("IMF PortWatch ports database — pinned call-mix shares "
               "(network build join input)")
        mix = {}
        for c, n in counts.items():
            mix[c] = figure(
                _r(n / total) if total > 0 else 0.0,
                "share of vessel calls (call mix)", "SOURCED", src,
                join["retrieved_at"],
                f"FALLBACK · {CLASS_LABELS[c]} calls ÷ all calls · pinned "
                f"join input {join['retrieved_at']}",
                label=f"{CLASS_LABELS[c]} call share",
                chips=["FALLBACK", "CALL MIX"])
        mix["roro"] = absent(
            "no RoRo split in the call mix",
            unit="share of vessel calls (call mix)", source=src,
            as_of=join["retrieved_at"], label="RoRo call share")
        return {"state": "FALLBACK",
                "figure_class": "SOURCED — PORTWATCH PORTS DATABASE (call mix)",
                "window": None,
                "port": rec.get("portname"),
                "combined_mix": mix,
                "tonnes_per_day": absent(
                    "call mix carries shares only", source=src,
                    as_of=join["retrieved_at"], label="Tonnage")}

    def _chokepoint_goods(self, rec: dict, retrieved) -> dict:
        window = rec["window"]
        transits, tonnes = rec["transits_per_day"], rec["transit_tonnes_per_day"]
        src = PORTWATCH_SOURCE
        per_class = {}
        for c in CLASSES:
            per_class[c] = {
                "transits_per_day": figure(
                    transits[c], "vessel transits/day", "SOURCED", src,
                    retrieved,
                    f"30d mean · transits {CLASS_LABELS[c]} · "
                    f"{window['start']} → {window['end']}",
                    label=f"{CLASS_LABELS[c]} transits", chips=["30D MEAN"]),
                "transit_tonnes_per_day": figure(
                    tonnes[c], "tonnes/day (PortWatch AIS estimate)",
                    "SOURCED", src, retrieved,
                    f"30d mean · transit tonnage {CLASS_LABELS[c]} · "
                    f"{window['start']} → {window['end']}",
                    label=f"{CLASS_LABELS[c]} transit tonnage",
                    chips=CHIPS_30D),
            }
        return {
            "state": "SOURCED",
            "figure_class": GOODS_LABEL,
            "window": window,
            "place": rec.get("portname"),
            "transits_per_day_total": figure(
                transits["total"], "vessel transits/day", "SOURCED", src,
                retrieved,
                f"all transits · 30d mean · {window['start']} → "
                f"{window['end']}", label="Transits", chips=["30D MEAN"]),
            "transit_tonnes_per_day_total": figure(
                tonnes["total"], "tonnes/day (PortWatch AIS estimate)",
                "SOURCED", src, retrieved,
                f"all transit tonnage · 30d mean · {window['start']} → "
                f"{window['end']}", label="Transit tonnage", chips=CHIPS_30D),
            "per_class": per_class,
            "transit_mix": _mix_figures(_shares(tonnes),
                                        "transit tonnes/day",
                                        window, src, retrieved),
        }

    # ------------------------------------------------------------------
    # Volume at risk + slowdown (per run)
    # ------------------------------------------------------------------
    @staticmethod
    def _worst_per_place(record: dict) -> dict:
        """mag(n) = max magnitude, delay(n) = max delay_d over the run's
        events at n — each place counted once, by
        construction of this dict."""
        worst: dict = {}
        events = record.get("events") or []
        start = _parse_ts((record.get("journal") or {})
                          .get("disruption", {}).get("started_at"))
        if start is None and events:
            stamps = [t for t in (_parse_ts(e.get("at")) for e in events)
                      if t is not None]
            start = min(stamps) if stamps else None
        for ev in events:
            nid = ev.get("node_id")
            if not nid:
                continue
            w = worst.setdefault(nid, {"magnitude": 0.0, "delay_d": 0.0,
                                       "hits": 0, "channels": set(),
                                       "last_clear": None})
            w["magnitude"] = max(w["magnitude"],
                                 float(ev.get("magnitude") or 0.0))
            w["delay_d"] = max(w["delay_d"], float(ev.get("delay_d") or 0.0))
            w["hits"] += 1
            if ev.get("channel"):
                w["channels"].add(ev["channel"])
            at = _parse_ts(ev.get("at"))
            if at is not None and start is not None:
                clear = at + timedelta(days=float(ev.get("delay_d") or 0.0))
                if w["last_clear"] is None or clear > w["last_clear"]:
                    w["last_clear"] = clear
        return {"worst": worst, "start": start}

    def _capacity_figure(self, node: dict) -> dict:
        """The place's capacity with the basis it really has. Shell and
        extra places carry an explicit ``capacity_basis`` (SOURCED /
        ESTIMATE / NOT_PUBLISHED). Core places carry NO such field (the
        graph validator forbids it): their capacity is the throughput
        proxy built from a named source in ``sources``, so it is SOURCED
        unless that source is a ``GAP:`` placeholder (no source retrieved),
        which is a MODELLED placeholder, never a sourced figure."""
        basis = node.get("capacity_basis")
        unit = node.get("capacity_unit")
        srcs = node.get("sources") or []
        src = srcs[0] if srcs else "SupermAP build census"
        if basis == "NOT_PUBLISHED" or node.get("capacity") is None:
            return absent("not published in the build census",
                          unit=unit, source=src, label="Capacity")
        if basis == "SOURCED":
            return figure(node.get("capacity"), unit, "SOURCED", src, None,
                          "build census · named source", label="Capacity",
                          chips=["SOURCED"])
        if basis == "ESTIMATE":
            return figure(node.get("capacity"), unit, "MODELLED", src, None,
                          "build census · ESTIMATE (median factor)",
                          label="Capacity", chips=["ESTIMATE"])
        # no per-place basis field: a core place
        if not srcs or str(srcs[0]).startswith("GAP:"):
            return figure(node.get("capacity"), unit, "MODELLED", src, None,
                          "placeholder · no source retrieved",
                          label="Capacity", chips=["PLACEHOLDER"])
        return figure(node.get("capacity"), unit, "SOURCED", src, None,
                      "build census · named source", label="Capacity",
                      chips=["SOURCED"])

    def _volume_block(self, record: dict, worst_map: dict) -> dict:
        nodes = self.store.graph_nodes()
        run_id = record.get("run_id")
        by_unit: dict = {}
        horizon = None
        for nid, w in worst_map["worst"].items():
            node = nodes.get(nid)
            if node is None:
                continue
            cap_fig = self._capacity_figure(node)
            unit = node.get("capacity_unit")
            entry = by_unit.setdefault(unit, {
                "unit": unit, "places": [], "_daily": 0.0, "_capdays": 0.0})
            if cap_fig["class"] == "ABSENT" or cap_fig["value"] is None:
                entry["places"].append({
                    "node_id": nid, "name": node.get("name"),
                    "kind": node.get("kind"),
                    "capacity": cap_fig,
                    "capacity_at_risk": absent(
                        "no published capacity",
                        unit=f"{unit} impaired/day",
                        label="Capacity at risk"),
                    "worst_magnitude": figure(
                        w["magnitude"], "share of capacity", "MODELLED",
                        "Chain journal (deterministic replay)", None,
                        f"max over {w['hits']} chain event(s) · run {run_id}",
                        label="Worst magnitude", chips=["WORST CASE"]),
                    "max_delay": figure(
                        w["delay_d"], "days", "MODELLED",
                        "Chain journal (deterministic replay)", None,
                        f"max delay over this place's events · run {run_id}",
                        label="Max delay", chips=["WORST CASE"]),
                    "hits": w["hits"],
                })
                continue
            cap = float(cap_fig["value"])
            daily = cap * w["magnitude"]
            capdays = daily * w["delay_d"]
            entry["_daily"] += daily
            entry["_capdays"] += capdays
            entry["places"].append({
                "node_id": nid, "name": node.get("name"),
                "kind": node.get("kind"),
                "capacity": cap_fig,
                "capacity_at_risk": figure(
                    _r(daily, 2), f"{unit} impaired/day", "MODELLED",
                    "SupermAP impact derivation", None,
                    f"capacity {cap:g} {unit} × worst magnitude "
                    f"{w['magnitude']:g} · run {run_id}",
                    label="Capacity at risk", chips=["MODELLED"]),
                "worst_magnitude": figure(
                    w["magnitude"], "share of capacity", "MODELLED",
                    "Chain journal (deterministic replay)", None,
                    f"max over {w['hits']} chain event(s) · run {run_id}",
                    label="Worst magnitude", chips=["WORST CASE"]),
                "max_delay": figure(
                    w["delay_d"], "days", "MODELLED",
                    "Chain journal (deterministic replay)", None,
                    f"max delay over this place's events · run {run_id}",
                    label="Max delay", chips=["WORST CASE"]),
                "hits": w["hits"],
            })
            if w["last_clear"] is not None and worst_map["start"] is not None:
                span = (w["last_clear"] - worst_map["start"]).total_seconds() / 86400.0
                if horizon is None or span > horizon:
                    horizon = span
        units_out = []
        for unit in sorted(by_unit):
            entry = by_unit[unit]
            daily = entry.pop("_daily")
            capdays = entry.pop("_capdays")
            n_places = len(entry["places"])
            entry["daily_volume_at_risk"] = figure(
                _r(daily, 2), f"{unit} impaired/day", "MODELLED",
                "SupermAP impact derivation", None,
                f"Σ capacity × worst magnitude · {n_places} place(s) · "
                f"{unit} only", label="Volume at risk",
                chips=["MODELLED", "NO UNIT MIX"])
            entry["capacity_days_impaired"] = figure(
                _r(capdays, 2), f"{unit} × days (capacity integral)",
                "MODELLED", "SupermAP impact derivation", None,
                f"Σ capacity × worst magnitude × max delay · {unit}",
                label="Capacity-days impaired",
                chips=["MODELLED", "SLOWDOWN MEASURE"])
            entry["places"].sort(key=lambda p: p["node_id"])
            units_out.append(entry)
        if horizon is not None:
            horizon_fig = figure(
                _r(horizon, 2), "days from run start", "MODELLED",
                "SupermAP impact derivation", None,
                "last arrival + delay − run start",
                label="Clearance horizon", chips=["MODELLED"])
        else:
            horizon_fig = absent(
                "run journal has no timestamps",
                unit="days from run start", label="Clearance horizon")
        return {"by_unit": units_out, "clearance_horizon": horizon_fig}

    # ------------------------------------------------------------------
    # Goods profile of a chain (impaired-volume-weighted, per class)
    # ------------------------------------------------------------------
    def _chain_goods(self, worst_map: dict) -> dict:
        nodes = self.store.graph_nodes()
        daily = self.store.daily()
        retrieved = daily["retrieved_at"]
        groups = {"port": {"weights": {}, "mixes": {}, "places": []},
                  "chokepoint": {"weights": {}, "mixes": {}, "places": []}}
        for nid, w in worst_map["worst"].items():
            node = nodes.get(nid)
            if node is None or node.get("kind") not in groups:
                continue
            cap = node.get("capacity")
            if cap is None:
                continue
            weight = float(cap) * w["magnitude"]
            if weight <= 0:
                continue
            grp = groups[node["kind"]]
            rec = (daily["ports"].get(nid) if node["kind"] == "port"
                   else daily["chokepoints"].get(nid))
            if rec is None:
                grp["places"].append({"node_id": nid, "name": node.get("name"),
                                      "goods": "ABSENT"})
                continue
            if node["kind"] == "port":
                imp, exp = (rec["import_tonnes_per_day"],
                            rec["export_tonnes_per_day"])
                combined = {k: (imp.get(k) or 0) + (exp.get(k) or 0)
                            for k in list(CLASSES) + ["total"]}
                mix = _shares(combined)
                window = rec["window"]
            else:
                mix = _shares(rec["transit_tonnes_per_day"])
                window = rec["window"]
            if mix is None:
                grp["places"].append({"node_id": nid, "name": node.get("name"),
                                      "goods": "ABSENT"})
                continue
            grp["weights"][nid] = weight
            grp["mixes"][nid] = mix
            grp["places"].append({"node_id": nid, "name": node.get("name"),
                                  "goods": "SOURCED", "window": window})
        out = {}
        for kind, grp in groups.items():
            unit = ("TEU/day" if kind == "port" else "vessels/day")
            label = "ports" if kind == "port" else "strait/canal transits"
            total_w = sum(grp["weights"].values())
            if total_w <= 0:
                out[kind] = {"state": "ABSENT",
                             "places": grp["places"],
                             "mix": absent(
                                 f"no affected {label} place has capacity "
                                 f"and a goods record",
                                 unit=f"share of {label} goods",
                                 source=PORTWATCH_SOURCE, as_of=retrieved,
                                 label="Weighted mix")}
                continue
            mix = {}
            for c in CLASSES:
                share = sum(grp["mixes"][nid][c] * grp["weights"][nid]
                            for nid in grp["weights"]) / total_w
                mix[c] = figure(
                    _r(share), f"share of goods through affected {label}",
                    "MODELLED", PORTWATCH_SOURCE, retrieved,
                    f"mix weighted by impaired volume ({unit}) · 30d "
                    f"combined mix", label=f"{CLASS_LABELS[c]} share",
                    chips=["MODELLED", "VOLUME-WEIGHTED"])
            out[kind] = {"state": "MODELLED", "places": grp["places"],
                         "total_impaired_volume": figure(
                             _r(total_w, 2), f"{unit} impaired/day",
                             "MODELLED",
                             "SupermAP impact derivation", None,
                             f"Σ impaired volume · {label} · {unit}",
                             label="Impaired volume", chips=["MODELLED"]),
                         "mix": mix}
        return out

    # ------------------------------------------------------------------
    # Economy exposure
    # ------------------------------------------------------------------
    def _tier1_rows(self, affected_ports: list[str]) -> list[dict]:
        daily = self.store.daily()
        retrieved = daily["retrieved_at"]
        by_country: dict[str, list[str]] = {}
        for nid in affected_ports:
            entry = self.store.country_of(nid) or {}
            iso = entry.get("iso3")
            if iso:
                by_country.setdefault(iso, []).append(nid)
        rows = []
        for iso in sorted(by_country):
            nids = sorted(by_country[iso])
            crec = daily["countries"].get(iso)
            num = 0.0
            window = None
            for nid in nids:
                rec = daily["ports"].get(nid)
                if rec is None:
                    continue
                num += (rec["tier1_import_tonnes"]["total"]
                        + rec["tier1_export_tonnes"]["total"])
                window = rec["tier1_window"]
            if crec is None or window is None:
                rows.append({
                    "iso3": iso, "places": nids,
                    "share": absent(
                        "no country total in the daily layer",
                        unit="share of national port tonnage",
                        source=PORTWATCH_SOURCE, as_of=retrieved,
                        label="National share")})
                continue
            den = (crec["import_tonnes"]["total"]
                   + crec["export_tonnes"]["total"])
            rows.append({
                "iso3": iso,
                "country": crec.get("country"),
                "places": nids,
                "affected_tonnage": figure(
                    _r(num, 2), "tonnes (PortWatch AIS estimate, 30d window)",
                    "SOURCED", PORTWATCH_SOURCE, retrieved,
                    f"Σ imports + exports · affected ports · "
                    f"{window['start']} → {window['end']}",
                    label="Affected tonnage", chips=["30D WINDOW", "AIS ESTIMATE"]),
                "national_tonnage": figure(
                    _r(den, 2), "tonnes (PortWatch AIS estimate, 30d window)",
                    "SOURCED", PORTWATCH_SOURCE, retrieved,
                    f"Σ imports + exports · {crec['ports_in_layer']} ports "
                    f"in layer · {window['start']} → {window['end']}",
                    label="National tonnage", chips=["30D WINDOW", "AIS ESTIMATE"]),
                "share": figure(
                    _r(num / den) if den > 0 else 0.0,
                    "share of national port tonnage", "MODELLED",
                    PORTWATCH_SOURCE, retrieved,
                    f"affected ÷ national · denominator "
                    f"{crec['ports_in_layer']} ports in layer · "
                    f"{crec['graph_ports_in_country']} in build · "
                    f"{len(nids)} affected",
                    label="National share", chips=["MODELLED", "AIS ESTIMATE"]),
            })
        return rows

    def _tier2_rows(self, affected_chokepoints: list[str]) -> tuple[list[dict], dict]:
        routing = self.store.routing_sets()
        sets = {s["node_id"]: s for s in routing.get("sets", [])}
        used = [nid for nid in affected_chokepoints if nid in sets]
        coverage = {"routing_sets_version": routing.get("version"),
                    "affected_with_sets": sorted(used),
                    "affected_without_sets": sorted(
                        set(affected_chokepoints) - set(used))}
        regions = routing.get("regions", {})
        region_of = build_region_of(routing)

        if not used:
            # No affected strait/canal carries a seeded routing set, so
            # no Tier-2 rows exist — but the coverage block still serves
            # the full field set the seeded path serves (one uniform
            # contract): the extracts metadata is known regardless of
            # the affected set, and the outside-region-table scan below
            # applies the row loop's own predicate to the extracts +
            # region table only, so it reports the same set the seeded
            # path would report for these extracts.
            comtrade = self.store.comtrade()
            outside_region_table = {
                rep_code
                for (rep_code, cmd), rec in comtrade["extracts"].items()
                if cmd in CMD_LABELS and has_partners(rec)
                and region_of(rep_code) is None}
            coverage["reporters_outside_region_table"] = sorted(
                outside_region_table)
            coverage["extracts_retrieved_at"] = comtrade["retrieved_at"]
            coverage["extracts_empty"] = [
                {"reporter_code": r, "cmd_code": c}
                for (r, c) in sorted(comtrade["empty"])]
            return [], coverage

        exposed_pairs: set[tuple[str, str]] = set()
        for nid in used:
            for pair in sets[nid].get("region_pairs", []):
                a, b = pair
                exposed_pairs.add((a, b))
                exposed_pairs.add((b, a))
        comtrade = self.store.comtrade()
        rows = []
        outside_region_table: set = set()
        for (rep_code, cmd), rec in sorted(comtrade["extracts"].items()):
            if cmd not in CMD_LABELS:
                continue
            if not has_partners(rec):
                continue
            rep_iso = rec.get("reporter_iso3")
            reporter_region = region_of(rep_code)
            if reporter_region is None:
                outside_region_table.add(rep_code)
                continue
            got = region_shares(rec, region_of)
            if got is None:
                continue
            basis, shares = got
            share = sum(v for region, v in shares.items()
                        if region != UNASSIGNED
                        and (reporter_region, region) in exposed_pairs)
            if share <= 0:
                continue
            via = [self.store.graph_nodes().get(nid, {}).get("name", nid)
                   for nid in used]
            rows.append({
                "reporter_iso3": rep_iso,
                "reporter": rec.get("reporter_name"),
                "reporter_code": rep_code,
                "good": cmd,
                "good_label": CMD_LABELS[cmd],
                "period": rec.get("period"),
                "basis": basis,
                "share": figure(
                    _r(share),
                    f"share of the reporter's imports of this good "
                    f"({basis} basis)", "MODELLED",
                    f"{COMTRADE_SOURCE} + printed routing-allocation sets "
                    f"{routing.get('version')}",
                    comtrade["retrieved_at"],
                    f"partners routed via {', '.join(via)} ÷ all partners "
                    f"· {basis} · Comtrade {rec.get('period')}",
                    label="Import share via affected places",
                    chips=["MODELLED ROUTING", "ANNUAL DATA"]),
                "via": via,
            })
        rows.sort(key=lambda r: (r["reporter_iso3"] or "", r["good"]))
        coverage["reporters_outside_region_table"] = sorted(outside_region_table)
        coverage["extracts_retrieved_at"] = comtrade["retrieved_at"]
        coverage["extracts_empty"] = [
            {"reporter_code": r, "cmd_code": c}
            for (r, c) in sorted(comtrade["empty"])]
        return rows, coverage

    def _watchlist(self, tier2_rows: list[dict]) -> dict:
        entries = [r for r in tier2_rows
                   if (r["share"].get("value") or 0) >= WATCHLIST_THRESHOLD]
        return {"header": WATCHLIST_HEADER,
                "label": "EXPOSURE ONLY",
                "threshold": WATCHLIST_THRESHOLD,
                "entries": [{"reporter_iso3": r["reporter_iso3"],
                             "reporter": r["reporter"],
                             "good": r["good"], "good_label": r["good_label"],
                             "share": r["share"], "via": r["via"]}
                            for r in entries]}

    # ------------------------------------------------------------------
    # Price context
    # ------------------------------------------------------------------
    def rates_payload(self, today: date | None = None) -> dict:
        rates = self.store.rates()
        today = today or datetime.now(timezone.utc).date()
        citations = []
        for c in rates.get("citations", []):
            as_of = c.get("as_of")
            age = None
            if as_of:
                try:
                    age = (today - date.fromisoformat(as_of)).days
                except ValueError:
                    age = None
            citations.append({
                "index": c.get("index"), "route": c.get("route"),
                "value": figure(c.get("value"), c.get("unit"), "SOURCED",
                                f"{c.get('publisher')} — reporting the "
                                f"index publisher's dated assessment",
                                as_of,
                                "dated press citation · no feed · no change "
                                "computed",
                                label="Index citation",
                                chips=["CITED", "NO FEED"]),
                "publisher": c.get("publisher"), "url": c.get("url"),
                "corroborating_publisher": c.get("corroborating_publisher"),
                "corroborating_url": c.get("corroborating_url"),
                "week": c.get("week"),
                "age_days": age,
                "stale": (age is not None and age > 10),
                "stale_stamp": ("STALE · over 10 days"
                                if age is not None and age > 10 else None),
            })
        payload = {
            "schema": "impact/1",
            "state": rates.get("state", "UNAVAILABLE"),
            "statement": NO_FORWARD_CLAIM,
            "label": "NO PRICE FORECAST",
            "licence_states": rates.get("licence_states", {}),
            # short state tokens for the chips (the data file's sentences stay as the record)
            "licence_chips": {k: str(v).split(" \u2014 ")[0].split(";")[0].strip()
                              for k, v in (rates.get("licence_states") or {}).items()},
            "licence_check": rates.get("licence_check"),
            "citations": citations,
            "index_links": [dict(x) for x in (rates.get("index_links") or [])
                            if isinstance(x, dict)],
            "citations_note": "STALE AFTER 10 DAYS · NO RATE COMPUTED",
        }
        assert_payload_honest(payload)
        return payload

    def analogues_payload(self, channel: str | None = None) -> dict:
        doc = self.store.analogues()
        entries = doc.get("entries", [])
        if channel:
            entries = [e for e in entries
                       if channel in (e.get("channels") or [])]
        payload = {"schema": "impact/1", "version": doc.get("version"),
                   "class": "HISTORICAL",
                   "matching_rule": doc.get("matching_rule"),
                   "channel": channel,
                   "entries": entries}
        assert_payload_honest(payload)
        return payload

    def _price_context(self, record: dict) -> dict:
        channels = set()
        for ev in record.get("events") or []:
            if ev.get("channel"):
                channels.add(ev["channel"])
        doc = self.store.analogues()
        matched = [e for e in doc.get("entries", [])
                   if channels & set(e.get("channels") or [])]
        return {
            "statement": NO_FORWARD_CLAIM,
            "label": "NO PRICE FORECAST",
            "channels_in_run": sorted(channels),
            "analogues": matched,
            "analogues_note": (
                "Historical · matched by channel" if matched else
                "No analogue matches this run's channels"),
            "rates": self.rates_payload(),
        }

    # ------------------------------------------------------------------
    # Payload assemblers (endpoints)
    # ------------------------------------------------------------------
    def node_payload(self, node_id: str) -> dict | None:
        nodes = self.store.graph_nodes()
        node = nodes.get(node_id)
        if node is None:
            return None
        country = self.store.country_of(node_id) or {}
        payload = {
            "schema": "impact/1",
            "node_id": node_id,
            "name": node.get("name"),
            "kind": node.get("kind"),
            "tier": node.get("tier"),
            "country": {"iso3": country.get("iso3"),
                        "name": country.get("country"),
                        "source": country.get("source"),
                        "reason": country.get("reason")},
            "capacity": self._capacity_figure(node),
            "goods": self.node_goods(node_id),
        }
        if node.get("kind") == "port" and country.get("iso3"):
            payload["country_exposure"] = self._tier1_rows([node_id])
        assert_payload_honest(payload)
        return payload

    def run_payload(self, record: dict) -> dict:
        worst_map = self._worst_per_place(record)
        volume = self._volume_block(record, worst_map)
        goods = self._chain_goods(worst_map)
        nodes = self.store.graph_nodes()
        affected_ports = sorted(
            nid for nid in worst_map["worst"]
            if nodes.get(nid, {}).get("kind") == "port")
        affected_chokes = sorted(
            nid for nid in worst_map["worst"]
            if nodes.get(nid, {}).get("kind") == "chokepoint")
        tier1 = self._tier1_rows(affected_ports)
        tier2, tier2_cov = self._tier2_rows(affected_chokes)
        journal = record.get("journal") or {}
        disruption = journal.get("disruption") or {}
        seed_node = nodes.get(disruption.get("node_id"), {})
        payload = {
            "schema": "impact/1",
            "run_id": record.get("run_id"),
            "seed": {"node_id": disruption.get("node_id"),
                     "name": seed_node.get("name"),
                     "kind": disruption.get("kind"),
                     "started_at": disruption.get("started_at")},
            "places_affected": len(worst_map["worst"]),
            "volume_at_risk": volume,
            "goods_profile": goods,
            "economy_exposure": {
                "tier1_port_share": tier1,
                "tier2_trade_share": tier2,
                "tier2_coverage": tier2_cov,
                "watchlist": self._watchlist(tier2),
            },
            "price_context": self._price_context(record),
        }
        assert_payload_honest(payload)
        return payload

    def exposure_payload(self, iso3: str) -> dict:
        daily = self.store.daily()
        retrieved = daily["retrieved_at"]
        crec = daily["countries"].get(iso3)
        nodes = self.store.graph_nodes()
        ports = sorted(nid for nid, n in nodes.items()
                       if n.get("kind") == "port"
                       and (self.store.country_of(nid) or {}).get("iso3") == iso3)
        if crec is None:
            payload = {
                "schema": "impact/1", "iso3": iso3,
                "country_tonnage": absent(
                    "country not in the daily layer",
                    source=PORTWATCH_SOURCE, as_of=retrieved,
                    label="Country tonnage"),
                "ports": [],
                "tier2": absent(
                    "computed per run",
                    source=COMTRADE_SOURCE,
                    label="Trade share"),
            }
            assert_payload_honest(payload)
            return payload
        window = None
        port_rows = []
        den = (crec["import_tonnes"]["total"] + crec["export_tonnes"]["total"])
        for nid in ports:
            rec = daily["ports"].get(nid)
            if rec is None:
                port_rows.append({
                    "node_id": nid, "name": nodes[nid].get("name"),
                    "share": absent(
                        "no daily-layer record",
                        unit="share of national port tonnage",
                        source=PORTWATCH_SOURCE, as_of=retrieved,
                        label="National share")})
                continue
            window = rec["tier1_window"]
            num = (rec["tier1_import_tonnes"]["total"]
                   + rec["tier1_export_tonnes"]["total"])
            port_rows.append({
                "node_id": nid, "name": nodes[nid].get("name"),
                "tonnage": figure(
                    _r(num, 2), "tonnes (PortWatch AIS estimate, 30d window)",
                    "SOURCED", PORTWATCH_SOURCE, retrieved,
                    f"imports + exports · {window['start']} → "
                    f"{window['end']}", label="Port tonnage",
                    chips=["30D WINDOW", "AIS ESTIMATE"]),
                "share": figure(
                    _r(num / den) if den > 0 else 0.0,
                    "share of national port tonnage", "MODELLED",
                    PORTWATCH_SOURCE, retrieved,
                    "port ÷ national tonnage · same window",
                    label="National share", chips=["MODELLED", "AIS ESTIMATE"]),
            })
        payload = {
            "schema": "impact/1", "iso3": iso3,
            "country": crec.get("country"),
            "window": window or crec.get("window"),
            "country_tonnage": {
                "imports": figure(
                    crec["import_tonnes"]["total"],
                    "tonnes (PortWatch AIS estimate, 30d window)", "SOURCED",
                    PORTWATCH_SOURCE, retrieved,
                    "Σ imports · all ports in layer · shared window",
                    label="Imports", chips=["30D WINDOW", "AIS ESTIMATE"]),
                "exports": figure(
                    crec["export_tonnes"]["total"],
                    "tonnes (PortWatch AIS estimate, 30d window)", "SOURCED",
                    PORTWATCH_SOURCE, retrieved,
                    "Σ exports · all ports in layer · shared window",
                    label="Exports", chips=["30D WINDOW", "AIS ESTIMATE"]),
                "ports_in_layer": crec["ports_in_layer"],
                "ports_in_build": crec["graph_ports_in_country"],
            },
            "ports": port_rows,
            "tier2": absent(
                "computed per run",
                source=COMTRADE_SOURCE,
                label="Trade share"),
        }
        assert_payload_honest(payload)
        return payload
