"""IMPACT layer data access (snapshot inputs).

Loaders are corrupt-tolerant in the adapter idiom: a missing or
unparseable input degrades that input to ABSENT downstream — never to
an exception and never to invented data. All paths are injectable; production defaults point at the canonical locations.
"""
from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


class ImpactStore:
    def __init__(self, snapshots_dir: Path | None = None,
                 impact_dir: Path | None = None,
                 graph_path: Path | None = None,
                 join_input_path: Path | None = None):
        self.snapshots_dir = snapshots_dir or (REPO_ROOT / "data" / "snapshots")
        self.impact_dir = impact_dir or (REPO_ROOT / "data" / "impact")
        self.graph_path = graph_path or (REPO_ROOT / "data" / "graph" / "graph.json")
        self.join_input_path = (join_input_path
                                or (REPO_ROOT / "data" / "graph" / "portwatch_join_input.json"))
        self._cache: dict = {}

    # ---- graph --------------------------------------------------------
    def graph_nodes(self) -> dict:
        if "graph" not in self._cache:
            doc = _load_json(self.graph_path) or {}
            self._cache["graph"] = {n["id"]: n for n in doc.get("nodes", [])
                                    if isinstance(n, dict) and n.get("id")}
        return self._cache["graph"]

    # ---- PortWatch daily pack (data/snapshots/portwatch_daily.json) ---
    def daily(self) -> dict:
        """{available, retrieved_at, ports{node_id:rec}, chokepoints{...},
        countries{iso3:rec}, coverage{rec|None}}"""
        if "daily" not in self._cache:
            pack = _load_json(self.snapshots_dir / "portwatch_daily.json")
            out = {"available": False, "retrieved_at": None, "ports": {},
                   "chokepoints": {}, "countries": {}, "coverage": None}
            if isinstance(pack, dict) and isinstance(pack.get("records"), list):
                prov = pack.get("provenance") or {}
                out["retrieved_at"] = prov.get("retrieved_at")
                for rec in pack["records"]:
                    if not isinstance(rec, dict):
                        continue
                    kind = rec.get("record_type")
                    if kind == "port_goods" and rec.get("node_id"):
                        out["ports"][rec["node_id"]] = rec
                    elif kind == "chokepoint_transit" and rec.get("node_id"):
                        out["chokepoints"][rec["node_id"]] = rec
                    elif kind == "country_goods" and rec.get("iso3"):
                        out["countries"][rec["iso3"]] = rec
                    elif kind == "daily_coverage":
                        out["coverage"] = rec
                out["available"] = bool(out["ports"] or out["chokepoints"])
            self._cache["daily"] = out
        return self._cache["daily"]

    # ---- pinned call-mix fallback --------------------------
    def join_input(self) -> dict:
        """{node_id: record} from the graph build's pinned PortWatch join
        input (vessel-call mix by class; the fallback source)."""
        if "join" not in self._cache:
            doc = _load_json(self.join_input_path) or {}
            out = {}
            for rec in doc.get("records", []) if isinstance(doc, dict) else []:
                if isinstance(rec, dict) and rec.get("node_id"):
                    out[rec["node_id"]] = rec
            self._cache["join"] = {"records": out,
                                   "retrieved_at": doc.get("retrieved_at")
                                   if isinstance(doc, dict) else None}
        return self._cache["join"]

    # ---- country join (data/impact/country_join.json) ------------------
    def country_join(self) -> dict:
        if "cjoin" not in self._cache:
            doc = _load_json(self.impact_dir / "country_join.json") or {}
            self._cache["cjoin"] = doc if isinstance(doc, dict) else {}
        return self._cache["cjoin"]

    def country_of(self, node_id: str) -> dict | None:
        nodes = (self.country_join().get("nodes") or {})
        entry = nodes.get(node_id)
        return entry if isinstance(entry, dict) else None

    # ---- Comtrade input: derived aggregates (committed) or local record-level extracts
    def comtrade(self) -> dict:
        """{available, retrieved_at, derived, extracts{(reporter_code, cmd): rec},
        by_reporter{reporter_code: {iso3, name}}, empty: [(reporter, cmd)], failures}

        Reads the git-ignored record-level ``comtrade_extracts.json`` when a
        local ``scripts/fetch_comtrade.py`` run has produced it, else the
        committed ``comtrade_aggregates.json`` (derived shares per routing
        region; UN Comtrade terms do not allow shipping the original records).
        ``derive.py`` returns the same numbers for either input."""
        if "comtrade" not in self._cache:
            out = {"available": False, "retrieved_at": None, "derived": False,
                   "extracts": {}, "by_reporter": {}, "empty": [], "failures": []}
            doc = _load_json(self.impact_dir / "comtrade_extracts.json")
            rows = None
            if isinstance(doc, dict) and isinstance(doc.get("extracts"), list):
                rows = doc["extracts"]
            else:
                doc = _load_json(self.impact_dir / "comtrade_aggregates.json")
                if isinstance(doc, dict) and isinstance(doc.get("aggregates"), list):
                    rows = list(doc["aggregates"]) + [
                        {"reporter_code": e.get("reporter_code"),
                         "cmd_code": e.get("cmd_code"), "empty": True}
                        for e in (doc.get("empty") or []) if isinstance(e, dict)]
                    out["derived"] = True
            if rows is not None:
                out["retrieved_at"] = doc.get("retrieved_at")
                out["failures"] = doc.get("failures") or []
                for rec in rows:
                    if not isinstance(rec, dict):
                        continue
                    key = (rec.get("reporter_code"), rec.get("cmd_code"))
                    if rec.get("empty"):
                        out["empty"].append(key)
                        continue
                    out["extracts"][key] = rec
                    code = rec.get("reporter_code")
                    if code is not None and code not in out["by_reporter"]:
                        out["by_reporter"][code] = {
                            "iso3": rec.get("reporter_iso3"),
                            "name": rec.get("reporter_name")}
                out["available"] = bool(out["extracts"])
            self._cache["comtrade"] = out
        return self._cache["comtrade"]

    # ---- static printed data -------------------------------------------
    def analogues(self) -> dict:
        if "analogues" not in self._cache:
            self._cache["analogues"] = (
                _load_json(self.impact_dir / "analogues.json") or {})
        return self._cache["analogues"]

    def routing_sets(self) -> dict:
        if "routing" not in self._cache:
            self._cache["routing"] = (
                _load_json(self.impact_dir / "routing_sets.json") or {})
        return self._cache["routing"]

    def rates(self) -> dict:
        if "rates" not in self._cache:
            self._cache["rates"] = (
                _load_json(self.impact_dir / "rates.json") or {})
        return self._cache["rates"]
