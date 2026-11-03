"""Loader + validator for the inventory-days reference sidecar (data/graph/inventory_days.json).

Strict, non-repairing, rows that fail are
dropped with reasons, a document-level failure refuses the file.

Rules
  I0  document: schema, content_sha256, graph pin equals the graph in use
  I1  sources: id, name, retrieved_at, https url on a declared publisher, licence
  I2  rows: unique row_id; provenance fields (source_id, name, https url, retrieved_at, label); figure_label
      SOURCED; metric in the closed vocabulary; numeric positive value; unit; as_of (YYYY-MM or YYYY-MM-DD)
      whose year equals `year`; recent_values end at the value; a non-empty `scope` that names the geography
      label; geography is a country that exists as a `country` label in the graph (so a later reader can
      match it without extrapolating); EIA unit is days
  I3  Census ratio rows: days_equivalent equals ratio x 30.4375 rounded to 0.1 (unit conversion only)
  I4  the MODELLED block (engine_assumptions) is a block, not rows, and is labelled MODELLED
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from . import common as C
from .common import Checks, SidecarError, num

ENV = "SUPERMAP_INVENTORY_DAYS_PATH"
FILENAME = "inventory_days.json"
METRICS = ("days_of_supply", "inventory_to_sales_ratio", "inventory_to_shipments_ratio")
DAYS_PER_MONTH = 30.4375
ASOF_RE = re.compile(r"^\d{4}-\d{2}(-\d{2})?$")


def inventory_days_path() -> Path:
    return C.env_path(ENV, FILENAME)


def file_sha256(path: Path | None = None) -> str:
    return hashlib.sha256((path or inventory_days_path()).read_bytes()).hexdigest()


class InventoryDays:
    def __init__(self, doc: dict, file_hash: str, rows: list, dropped: list, chk: Checks):
        self.doc = doc
        self.file_sha256 = file_hash
        self.rows = rows
        self.dropped = dropped
        self.checks_passed = chk.passed
        self.checks_failed = list(chk.failed)

    def for_country(self, graph_country_label: str) -> list[dict]:
        """Read-only accessor for a later engine version: the validated rows whose declared geography is exactly this
        graph country label. A place in another country gets nothing (no extrapolation)."""
        return [dict(r) for r in self.rows if r["geography"]["graph_country_label"] == graph_country_label]

    def get(self, row_id: str) -> dict | None:
        for r in self.rows:
            if r["row_id"] == row_id:
                return dict(r)
        return None

    def summary(self) -> dict:
        by_src: dict = {}
        by_metric: dict = {}
        for r in self.rows:
            by_src[r["source_id"]] = by_src.get(r["source_id"], 0) + 1
            by_metric[r["metric"]] = by_metric.get(r["metric"], 0) + 1
        return {
            "schema": C.SCHEMA_INVENTORY,
            "label": "REFERENCE DATA (SOURCED), not an engine input",
            "file_sha256": self.file_sha256,
            "content_sha256": self.doc.get("content_sha256"),
            "graph_pin": self.doc.get("graph_pin"),
            "generated": self.doc.get("generated"),
            "rows": len(self.rows),
            "rows_by_source": dict(sorted(by_src.items())),
            "rows_by_metric": dict(sorted(by_metric.items())),
            "geographies": sorted({r["geography"]["label"] for r in self.rows}),
            "class_groups": sorted({r["class_group"] for r in self.rows}),
            "sources": self.doc.get("sources"),
            "engine_assumptions": self.doc.get("engine_assumptions"),
            "not_served": self.doc.get("not_served"),
            "policy": self.doc.get("policy"),
            "dropped": self.dropped,
            "checks_passed": self.checks_passed,
            "checks_failed": self.checks_failed,
            "built_from": self.doc.get("built_from"),
        }


def validate_row(r: dict, sources: dict, countries: set, chk: Checks) -> list[str]:
    bad: list[str] = []
    if not isinstance(r, dict):
        chk(False, "I2 row: not an object", bad)
        return bad
    tag = f"I2 {r.get('row_id')}"
    need = ("row_id", "source_id", "source_name", "source_url", "retrieved_at", "figure_label", "commodity_class",
            "commodity_label", "class_group", "metric", "value", "unit", "as_of", "year", "recent_values",
            "geography", "scope")
    miss = [f for f in need if f not in r]
    if not chk(not miss, f"{tag}: fields {miss}", bad):
        return bad
    C.validate_row_provenance(r, chk, bad, tag, sources)
    chk(r["figure_label"] == "SOURCED", f"{tag}: every shipped row is SOURCED", bad)
    chk(r["metric"] in METRICS, f"{tag}: metric in {METRICS}", bad)
    chk(num(r["value"]) and r["value"] > 0, f"{tag}: value is a positive number", bad)
    chk(isinstance(r["unit"], str) and bool(r["unit"]), f"{tag}: unit", bad)
    chk(isinstance(r["as_of"], str) and bool(ASOF_RE.match(r["as_of"])) and isinstance(r["year"], int)
        and r["as_of"].startswith(str(r["year"])), f"{tag}: as_of and year agree", bad)
    rv = r["recent_values"]
    chk(isinstance(rv, list) and len(rv) >= 1 and all(isinstance(x, list) and len(x) == 2 and num(x[1]) for x in rv)
        and rv[-1][1] == r["value"], f"{tag}: recent_values are oldest-first and end at the value", bad)
    g = r["geography"]
    chk(isinstance(g, dict) and g.get("level") == "country" and g.get("graph_country_label") in countries,
        f"{tag}: geography is a country label the graph uses", bad)
    chk(isinstance(r["scope"], str) and isinstance(g, dict) and str(g.get("label", "")) in r["scope"],
        f"{tag}: scope names the geography it applies to", bad)
    if r["metric"] == "days_of_supply":
        chk(r["unit"] == "days", f"{tag}: EIA unit is days", bad)
        chk(isinstance(r.get("series_id"), str) and r["series_id"].startswith("W_"), f"{tag}: EIA series id", bad)
    else:
        chk(r.get("seasonally_adjusted") is True, f"{tag}: Census rows are the seasonally adjusted series", bad)
        de = r.get("days_equivalent")
        chk(num(de) and abs(de - round(r["value"] * DAYS_PER_MONTH, 1)) < 1e-9,
            f"I3 {r.get('row_id')}: days_equivalent is the ratio x {DAYS_PER_MONTH}", bad)
    return bad


def validate_document(doc: dict, graph_data: dict, graph_hash: str | None = None, file_hash: str = "") -> InventoryDays:
    chk = Checks()
    if not isinstance(doc, dict) or doc.get("schema") != C.SCHEMA_INVENTORY:
        raise SidecarError(f"unsupported inventory_days schema: {doc.get('schema') if isinstance(doc, dict) else None!r}")
    if doc.get("content_sha256") != C.content_sha(doc):
        raise SidecarError("inventory_days content_sha256 does not match its content")
    pin = (doc.get("graph_pin") or {}).get("canonical_hash")
    if graph_hash is not None and pin != graph_hash:
        raise SidecarError("inventory_days graph_pin does not match the graph in use")
    chk(True, "I0 document: schema, content hash, graph pin")
    dropped: list[dict] = []
    sources: dict = {}
    for s in doc.get("sources", []):
        bad: list[str] = []
        sid = s.get("id") if isinstance(s, dict) else None
        chk(isinstance(s, dict) and bool(sid) and bool(s.get("name")) and bool(C.TS_RE.match(str(s.get("retrieved_at", "")))),
            f"I1 source {sid}: id, name, retrieved_at", bad)
        chk(isinstance(s, dict) and C.url_ok(s.get("url")), f"I1 source {sid}: url is https on a declared publisher", bad)
        chk(C.licence_ok(s.get("licence") if isinstance(s, dict) else None),
            f"I1 source {sid}: licence name, note and https url", bad)
        if bad:
            dropped.append({"kind": "source", "id": sid, "reasons": bad})
        else:
            sources[sid] = s
    countries = {n.get("country") for n in graph_data["nodes"] if n.get("country")}
    rows: list[dict] = []
    seen: set = set()
    for r in doc.get("rows", []):
        why = validate_row(r, sources, countries, chk)
        if isinstance(r, dict) and r.get("row_id") in seen:
            why.append("duplicate row_id")
        if why:
            dropped.append({"kind": "row", "id": r.get("row_id") if isinstance(r, dict) else None, "reasons": why})
            continue
        seen.add(r["row_id"])
        rows.append(r)
    ea = doc.get("engine_assumptions") or {}
    chk(ea.get("figure_label") == "MODELLED" and "values_by_kind" in ea, "I4 engine_assumptions is a MODELLED block")
    return InventoryDays(doc, file_hash, rows, dropped, chk)


def load_inventory_days(graph_data: dict, graph_hash: str | None = None, path: Path | None = None) -> InventoryDays:
    p = Path(path) if path else inventory_days_path()
    raw = p.read_bytes()
    try:
        doc = json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise SidecarError(f"inventory_days file is not JSON: {exc}") from exc
    return validate_document(doc, graph_data, graph_hash, hashlib.sha256(raw).hexdigest())
