#!/usr/bin/env python3
"""SupermAP IMPACT layer — country join table builder.

Writes data/impact/country_join.json: node_id ->
{iso3, country, m49, source}. The join lives ONLY in the impact store
(it is not part of the graph contract; the graph is read here, never
written).

Sources, in precedence order:
  1. Sea ports with a Daily_Ports_Data record: country + ISO3 exactly as
     the layer serves them by portid — from the
     portwatch_daily snapshot pack.
  2. Everything else with an overlay M49 (comtrade_overlay.py NODE_M49 —
     all graph nodes by construction): ISO3 + name from the UN Comtrade
     reporter reference cached by scripts/fetch_comtrade.py
     (data/impact/reporters_ref.json).
  3. Chokepoints carry no country (international waterways; the daily
     chokepoint layer has no country field) — recorded with iso3 null
     and the reason, except where the overlay assigns one anyway
     (recorded, source says so).
  4. Chokepoints are listed separately (no_country_by_kind): country
     null by kind, not by failure. Genuinely unmapped nodes (no source
     covers them — e.g. sea_longbeach, whose combined LA–Long Beach
     database record is carried by sea_losangeles, and air/ground
     places beyond the overlay's 80-entry map) land in `unmapped`,
     ABSENT downstream.

Run after the portwatch_daily snapshot exists for maximal source-1
coverage; the build is honest either way (source is printed per row).

    python3 data/impact/build_country_join.py
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
OUT = HERE / "country_join.json"


def _load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def main() -> int:
    graph = _load(REPO_ROOT / "data" / "graph" / "graph.json") or {}
    nodes = graph.get("nodes", [])
    pack = _load(REPO_ROOT / "data" / "snapshots" / "portwatch_daily.json") or {}
    daily_iso = {}
    for rec in pack.get("records", []):
        if isinstance(rec, dict) and rec.get("record_type") == "port_goods":
            if rec.get("node_id") and rec.get("iso3"):
                daily_iso[rec["node_id"]] = {"iso3": rec.get("iso3"),
                                              "country": rec.get("country")}
    spec = importlib.util.spec_from_file_location(
        "comtrade_overlay", REPO_ROOT / "data" / "graph" / "build" / "comtrade_overlay.py")
    overlay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(overlay)
    node_m49 = dict(overlay.NODE_M49)
    ref = _load(HERE / "reporters_ref.json") or {}
    reporters = ref.get("reporters") or {}

    out_nodes: dict = {}
    unmapped: list = []
    no_country_by_kind: list = []
    # The Comtrade reporter reference names the United States only under
    # its legacy code 842 (no 840 row exists in Reporters.json — the
    # same quirk the preview extracts record). The reference lookup —
    # and only the lookup — aliases 840 -> 842; the stored m49 stays
    # the overlay's.
    ref_alias = {840: 842}
    for node in nodes:
        nid = node.get("id")
        if not nid:
            continue
        m49 = node_m49.get(nid)
        ref_row = (reporters.get(str(ref_alias.get(m49, m49))) or {}) \
            if m49 is not None else {}
        if nid in daily_iso:
            out_nodes[nid] = {
                "iso3": daily_iso[nid]["iso3"],
                "country": daily_iso[nid]["country"],
                "m49": m49,
                "source": "IMF PortWatch Daily_Ports_Data (country/ISO3 as served by portid), via portwatch_daily snapshot",
            }
        elif node.get("kind") == "chokepoint":
            out_nodes[nid] = {
                "iso3": None, "country": None, "m49": m49,
                "source": None,
                "reason": ("chokepoint — international waterway; the daily "
                           "chokepoint layer serves no country for it, and "
                           "Tier-1 exposure counts ports, not straits"),
            }
            no_country_by_kind.append(nid)
        elif m49 is not None and ref_row.get("iso3"):
            out_nodes[nid] = {
                "iso3": ref_row["iso3"],
                "country": ref_row.get("name"),
                "m49": m49,
                "source": "UN Comtrade reporter reference (M49 -> ISO3), M49 from comtrade_overlay.py NODE_M49",
            }
        else:
            out_nodes[nid] = {"iso3": None, "country": None, "m49": m49,
                              "source": None,
                              "reason": "no country source covers this node"}
            unmapped.append(nid)
    doc = {
        "kind": "IMPACT-layer country join (impact store only; not part of the graph)",
        "sources": {
            "daily_pack_retrieved_at": (pack.get("provenance") or {}).get("retrieved_at"),
            "reporters_ref_retrieved_at": ref.get("retrieved_at"),
            "graph_version": graph.get("version"),
        },
        "nodes": out_nodes,
        "unmapped": sorted(unmapped),
        "no_country_by_kind": sorted(no_country_by_kind),
    }
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(OUT)
    mapped = sum(1 for v in out_nodes.values() if v.get("iso3"))
    print(f"country_join: {mapped}/{len(out_nodes)} nodes mapped, "
          f"unmapped={len(doc['unmapped'])}, "
          f"no_country_by_kind={len(doc['no_country_by_kind'])} -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
