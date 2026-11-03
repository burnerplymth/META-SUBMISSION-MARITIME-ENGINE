"""Build the inventory-days DATA sidecar from the stored source extracts.

    cd server && python -m realism_data.build            # dry run, prints the stamps
    cd server && python -m realism_data.build --write    # writes the data file + its sha256 pin
    cd server && python -m realism_data.build --check    # exits 1 unless the files on disk match

Pure and deterministic: no network, no clock, no RNG. Inputs are
``data/graph_sidecar_sources/`` (written by ``python -m realism_data.fetch``)
and ``data/graph/graph.json`` (never edited; its sha256 prefix is asserted).

Outputs
  data/graph/inventory_days.json          EIA days of supply, Census inventory ratios (US only)
  data/graph/inventory_days.sha256

There is no CPPI port-service sidecar: its source is CC BY-NC 3.0 IGO.

A number is copied from a fetched file or it is not in the output. There is no
imputation, no interpolation, no extrapolation to other places.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

try:
    from engine.canonical import graph_hash
except Exception:  # imported as server.realism_data.* from the repo root
    from server.engine.canonical import graph_hash  # type: ignore

from . import common as C

SRC = C.SOURCES_DIR
GRAPH = C.REPO_ROOT / "data" / "graph" / "graph.json"
OUT_INV = C.DATA_GRAPH / "inventory_days.json"
PIN_INV = C.DATA_GRAPH / "inventory_days.sha256"

DAYS_PER_MONTH = 30.4375  # 365.25 / 12: the only conversion applied to a published ratio


def load_manifest() -> dict:
    return json.loads((SRC / "manifest.json").read_text(encoding="utf-8"))


def _graph() -> tuple[dict, str, str]:
    raw = GRAPH.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    if not sha.startswith(C.GRAPH_PREFIX):
        raise SystemExit(f"graph.json sha256 {sha[:16]} != pinned prefix {C.GRAPH_PREFIX}")
    g = json.loads(raw)
    return g, sha, graph_hash(g)


def _graph_pin(sha: str, ghash: str) -> dict:
    return {"path": "data/graph/graph.json", "sha256": sha, "canonical_hash": ghash,
            "canonical_hash_note": "sha256 of the canonical JSON of the graph dict = engine.graph.Graph.hash"}


def _source_block(man_src: dict, sid: str, licence: dict, **extra) -> dict:
    return {"id": sid, "name": man_src["name"], "retrieved_at": man_src["retrieved_at"], **extra, "licence": licence}


# ------------------------------------------------------------------ inventory days
EIA_META = {
    "W_EPC0_VSD_NUS_DAYS": ("crude_oil", "Crude oil (commercial, excluding the Strategic Petroleum Reserve)"),
    "W_EPM0_VSD_NUS_DAYS": ("motor_gasoline", "Total motor gasoline"),
    "W_EPJK_VSD_NUS_DAYS": ("jet_fuel", "Kerosene-type jet fuel"),
    "W_EPD0_VSD_NUS_DAYS": ("distillate_fuel_oil", "Distillate fuel oil (total)"),
    "W_EPLLPZ_VSD_NUS_DAYS": ("propane_propylene", "Propane and propylene"),
}
CENSUS_GROUP = {"MTIS": "total business (manufacturers, retailers, merchant wholesalers)",
                "MRTS": "retail trade (retailers by kind of business)",
                "MWTS": "merchant wholesale trade (by kind of business)",
                "M3": "manufacturing (by industry)"}
CENSUS_SCOPE = ("United States only (national total of the Census Bureau survey universe). It describes the "
                "inventory held by US firms in this kind of business relative to their own sales or shipments; it is "
                "not a port stock, not a stock in any other country, and is not extrapolated to any graph place.")


def build_inventory() -> dict:
    graph, gsha, ghash = _graph()
    man = load_manifest()
    msrc = man["sources"]
    eia_m, cen_m = msrc["eia_days_of_supply"], msrc["census_inventory_ratios"]
    eia = json.loads((SRC / eia_m["extract"]).read_bytes())
    if hashlib.sha256((SRC / eia_m["extract"]).read_bytes()).hexdigest() != eia_m["extract_sha256"]:
        raise SystemExit("eia extract does not match its manifest hash")
    cen_bytes = (SRC / cen_m["extract"]).read_bytes()
    if hashlib.sha256(cen_bytes).hexdigest() != cen_m["extract_sha256"]:
        raise SystemExit("census extract does not match its manifest hash")
    cen = json.loads(cen_bytes)

    sources = [{
        "id": "eia_wpsr_dos", "name": eia_m["name"], "retrieved_at": eia_m["retrieved_at"],
        "url": C.EIA_PAGE_URL, "api": "https://api.eia.gov/v2/petroleum/sum/sndw/data/ (key sent in a header, "
                                      "never stored)",
        "page_release_date": eia["page_release_date"], "extract": "data/graph_sidecar_sources/" + eia_m["extract"],
        "extract_sha256": eia_m["extract_sha256"],
        "licence": {"name": "US government work, public domain", "url": "https://www.eia.gov/about/copyrights_reuse.php",
                    "note": "EIA states its information products are in the public domain and asks for an "
                            "acknowledgment with the publication date (\"Source: U.S. Energy Information "
                            "Administration (Oct 2026).\"). The EIA logo is a registered trademark."}}]
    prog_name = {"MTIS": "Manufacturing and Trade Inventories and Sales (MTIS)",
                 "MRTS": "Monthly Retail Trade (MRTS)", "MWTS": "Monthly Wholesale Trade (MWTS)",
                 "M3": "Manufacturers' Shipments, Inventories, and Orders (M3)"}
    for ex in cen["extracts"]:
        p = ex["program"]
        sources.append({
            "id": "census_" + p.lower(), "name": f"U.S. Census Bureau, {prog_name[p]}, time-series bulk file",
            "retrieved_at": ex["retrieved_at"], "url": C.CENSUS_ZIP_URL.format(p=p),
            "landing_url": C.CENSUS_PAGE_URL.format(p=p), "data_updated_on": ex["data_updated_on"],
            "raw_zip_sha256": ex["zip_sha256"], "raw_csv_sha256": ex["csv_sha256"],
            "extract": "data/graph_sidecar_sources/" + cen_m["extract"],
            "extract_sha256": cen_m["extract_sha256"],
            "licence": {"name": "US government work (Census Bureau)", "url": "https://www.census.gov/about/policies/citation.html",
                        "note": "Federal statistical data; the Census Bureau asks to be cited. The fetched pages did "
                                "not state a licence sentence as explicit as EIA's, so the public-domain status of a US "
                                "federal work is the working assumption, not a quotation."}})
    src_by = {s["id"]: s for s in sources}

    rows = []
    for s in eia["series"]:
        cls, label = EIA_META[s["series"]]
        pts = s["points"]
        period, val = pts[0]
        rows.append({
            "row_id": "eia." + cls, "source_id": "eia_wpsr_dos", "source_name": src_by["eia_wpsr_dos"]["name"],
            "source_url": C.EIA_HIST_URL.format(sid=s["series"]), "retrieved_at": eia_m["retrieved_at"],
            "figure_label": "SOURCED", "series_id": s["series"],
            "commodity_class": cls, "commodity_label": label, "class_group": "petroleum (US weekly supply estimates)",
            "metric": "days_of_supply", "value": float(val), "unit": "days", "as_of": period, "year": int(period[:4]),
            "recent_values": [[a, float(b)] for a, b in reversed(pts)],
            "definition": s["description"],
            "days_basis": "EIA: stocks divided by recent product supplied (refinery/consumption proxy); crude "
                          "excludes the SPR. EIA's own definition is at the EIA notes page linked from the table.",
            "geography": {"level": "country", "code": "US", "label": "United States",
                          "graph_country_label": "United States"},
            "scope": "United States national total only (all PADDs together). Not a port, PADD or terminal stock; "
                     "not valid for any other country, product or time; never extrapolated."})
    for ex in cen["extracts"]:
        p = ex["program"]
        ratio_metric = "inventory_to_shipments_ratio" if p == "M3" else "inventory_to_sales_ratio"
        for s in ex["series"]:
            if not s["seasonally_adjusted"]:
                continue
            period, val = s["points"][-1]
            m, y = period.split("-")
            asof = f"{y}-{['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'].index(m) + 1:02d}"
            code = s["cat_code"]
            label = re.sub(r"^[A-Za-z0-9,]+:\s*", "", s["cat_desc"])
            ratio = float(val)
            rows.append({
                "row_id": f"census.{p.lower()}.{code.lower()}", "source_id": "census_" + p.lower(),
                "source_name": src_by["census_" + p.lower()]["name"], "source_url": C.CENSUS_ZIP_URL.format(p=p),
                "retrieved_at": ex["retrieved_at"], "figure_label": "SOURCED",
                "commodity_class": f"{p.lower()}.{code}", "commodity_label": label,
                "class_group": CENSUS_GROUP[p], "class_system": "Census kind-of-business code (NAICS-based)",
                "class_code": code, "metric": ratio_metric, "value": ratio,
                "unit": "months (month-end inventory / that month's " + ("shipments" if p == "M3" else "sales") + ")",
                "as_of": asof, "year": int(y), "seasonally_adjusted": True,
                "days_equivalent": round(ratio * DAYS_PER_MONTH, 1),
                "days_equivalent_derivation": f"published ratio x {DAYS_PER_MONTH} days/month (365.25/12); unit "
                                              "conversion only, no other adjustment",
                "recent_values": [[a, float(b)] for a, b in s["points"]],
                "geography": {"level": "country", "code": "US", "label": "United States",
                              "graph_country_label": "United States"},
                "scope": CENSUS_SCOPE})
    rows.sort(key=lambda r: r["row_id"])

    # what the graph assumes today (MODELLED), described, never applied
    by_kind: dict = defaultdict(lambda: defaultdict(int))
    for n in graph["nodes"]:
        by_kind[n["kind"]][n["inventory_days"]] += 1
    assumptions = {
        "figure_label": "MODELLED",
        "where": "data/graph/graph.json, the inventory_days field of each place (this sidecar does not touch it)",
        "values_by_kind": {k: {str(d): c for d, c in sorted(v.items())} for k, v in sorted(by_kind.items())},
        "note": "One undifferentiated value per place kind (7 days at every port). The rows above are reference "
                "figures for particular US classes; they do not replace this value anywhere and say nothing about "
                "the cover held at a port or in any other country."}
    doc = {
        "schema": C.SCHEMA_INVENTORY,
        "generated": man["fetched_on"],
        "graph_pin": _graph_pin(gsha, ghash),
        "policy": {
            "opt_in": "Read only when a caller asks (GET /graph/inventory-days, or the loader). No engine, sim or "
                      "portsim code reads this file; every id and byte of every run is unchanged without it.",
            "adds_only": "Reference figures per commodity class and geography. Edits no graph.json field.",
            "scope": "Each row applies only to the class and geography in its `scope` field (here: United States). "
                     "Nothing is extrapolated to another country, port or class.",
            "labels": "Every row is SOURCED (the published number, with a unit conversion shown where used). The "
                      "graph's own inventory_days defaults are MODELLED and are only described in "
                      "`engine_assumptions`, not as rows.",
            "ratio_vs_days": "EIA publishes days of supply. Census publishes inventory-to-sales (or to-shipments) "
                             "ratios in months; `days_equivalent` is the ratio times 30.4375 and is a unit "
                             "conversion, not a separate measurement."},
        "sources": sources,
        "rows": rows,
        "engine_assumptions": assumptions,
        "not_served": [n for n in man["not_served"] if n.get("sidecar") == "inventory_days"]
                       + [{"source": "Days of cover outside the United States (EU, Asia, OECD) and per-port or "
                                     "per-container-goods-class cover",
                           "reason": "no fetched open source publishes a days-of-cover figure for them; Eurostat "
                                     "nrg_stk_oilm gives stock LEVELS only (see not_served entry above), IEA oil "
                                     "stocks was not served. A days figure would need a consumption series and "
                                     "would be MODELLED, so none is shipped."}],
        "counts": {"rows": len(rows),
                   "eia_rows": sum(1 for r in rows if r["source_id"] == "eia_wpsr_dos"),
                   "census_rows": sum(1 for r in rows if r["source_id"].startswith("census_")),
                   "by_source": {k: sum(1 for r in rows if r["source_id"] == k) for k in sorted(src_by)}},
        "built_from": {"builder": "server/realism_data/build.py",
                       "manifest_sha256": C.sha256_file(SRC / "manifest.json"),
                       "eia_extract_sha256": eia_m["extract_sha256"],
                       "census_extract_sha256": cen_m["extract_sha256"]},
    }
    doc["content_sha256"] = C.content_sha(doc)
    return doc


INV_ROWS = ("rows",)


def render() -> dict[Path, str]:
    inv = build_inventory()
    inv_t = C.dumps(inv, INV_ROWS)
    return {OUT_INV: inv_t, PIN_INV: hashlib.sha256(inv_t.encode("utf-8")).hexdigest() + "\n"}


def main(argv: list[str]) -> int:
    files = render()
    if "--write" in argv:
        for p, t in files.items():
            p.write_text(t, encoding="utf-8")
            print("wrote", p.relative_to(C.REPO_ROOT), len(t), "bytes")
        return 0
    if "--check" in argv:
        bad = [p for p, t in files.items() if not p.exists() or p.read_text(encoding="utf-8") != t]
        for p in bad:
            print("DIFFERS", p.relative_to(C.REPO_ROOT))
        print("files:", len(files), "differ:", len(bad))
        return 1 if bad else 0
    for p, t in files.items():
        print(p.name, len(t), hashlib.sha256(t.encode("utf-8")).hexdigest()[:16])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
