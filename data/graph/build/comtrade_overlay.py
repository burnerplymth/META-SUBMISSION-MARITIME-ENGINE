#!/usr/bin/env python3
"""SupermAP — UN Comtrade calibration overlay builder (READY-TO-RUN).

Purpose: the air/ground edges of data/graph/graph.json still carry MODELED
capacity_share allocations whose assigned calibration dataset is
UN Comtrade 2024. This script performs that join — as a CANDIDATE OVERLAY,
never as a silent rewrite:

  * It reads the API key from the environment variable UN_COMTRADE_API_KEY.
  * WITHOUT the key it prints a NEEDS-KEY message and exits 2, having
    changed nothing (no files written, no network calls).
  * WITH a key it fetches bilateral merchandise flows from the UN Comtrade
    API and writes two candidate artifacts next to this script:
    comtrade_overlay.json + comtrade_overlay.md (coverage + per-edge
    candidates + what could NOT be derived and why). graph.json is NOT
    modified; applying an overlay is a separate, human-reviewed generator
    change.

API (as described in the published documentation,
https://uncomtrade.org/docs/un-comtrade-api/ and the API Explorer):
  GET https://comtradeapi.un.org/data/v1/get/C/A/HS
      ?reporterCode=<M49>&period=2024&partnerCode=<M49,...>
      &cmdCode=TOTAL&flowCode=X,M&maxrecords=500
  with the subscription key sent in the `Ocp-Apim-Subscription-Key` header
  (the docs' examples pass it as the `subscription-key` query parameter;
  the header form keeps the key out of logged URLs and is accepted by the
  same Azure APIM gateway). Response JSON: {"count": N, "data": [ {record} ],
  "error": ""} where each record carries reporterCode, partnerCode, period,
  flowCode ("X" export / "M" import), cmdCode, primaryValue (current USD)
  and netWgt (kg). This parser reads exactly those fields.

Method (candidate derivation, stated in full):
  * Node -> reporter country: NODE_M49 below. Source: the base census
    `country` field per node. Border nodes whose
    census country names two countries take the FIRST-named country
    (e.g. gnd_laredo "United States / Mexico" -> US 840) — a visible
    modeling choice, listed again in the overlay report. Two documented
    exceptions: air_hongkong / sea_hongkong map to 344 (China, Hong Kong
    SAR), not the census's "China" (156), because Comtrade reports Hong
    Kong separately from the mainland and the node's flows ARE Hong Kong's;
    air_taipei maps to 158, Comtrade's code for Taiwan ("Other Asia, nes").
    chk_capeofgoodhope has no census country and stays unmapped.
  * Bilateral flow for edge O->D = reporter(O) exports to partner(D)
    (flowCode X, primaryValue, cmdCode TOTAL, period 2024). If the export
    record is absent, the mirror is used — partner(D)'s imports from O
    (flowCode M in D's reporter response) — and flagged `mirror: true`.
  * Candidate share per (from, mode) group = flow(O->D) / SUM(flow over the
    group's edges that returned data) * S_group, where S_group is the sum
    of the CURRENT graph shares over the edges that returned data (the
    covered subtotal is preserved, mirroring the PortWatch join's rule;
    uncovered edges keep no candidate). Rounded to 4 dp by the same
    largest-remainder apportionment as the PortWatch join.
  * Known limits, also printed in the report: same-country (domestic) edges
    have NO Comtrade coverage (Comtrade records international flows only)
    and get candidate=null; country-pair flows cannot distinguish an air
    lane from a ground leg between the same two countries, so both inherit
    the same country-pair split within their own (from, mode) group.
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
GRAPH_PATH = HERE.parent / "graph.json"
OVERLAY_JSON = HERE / "comtrade_overlay.json"
OVERLAY_MD = HERE / "comtrade_overlay.md"
API_BASE = "https://comtradeapi.un.org/data/v1/get/C/A/HS"
PERIOD = "2024"
ENV_KEY = "UN_COMTRADE_API_KEY"

# node_id -> UN M49 / Comtrade reporter code. Provenance + rules: see docstring.
NODE_M49 = {
    "air_addisababa": 231, "air_amsterdam": 528, "air_anchorage": 840,
    "air_bangkok": 764, "air_beijing": 156, "air_chicago": 840,
    "air_cincinnati": 840, "air_delhi": 356, "air_doha": 634,
    "air_dubai": 784, "air_dubaiworldcentral": 784, "air_frankfurt": 276,
    "air_guangzhou": 156, "air_hongkong": 344, "air_incheon": 410,
    "air_indianapolis": 840, "air_istanbul": 792, "air_jakarta": 360,
    "air_london": 826, "air_losangeles": 840, "air_louisville": 840,
    "air_memphis": 840, "air_miami": 840, "air_narita": 392,
    "air_ontario": 840, "air_paris": 250, "air_shanghaipudong": 156,
    "air_shenzhen": 156, "air_singapore": 702, "air_taipei": 158,
    "gnd_atlanta": 840, "gnd_bettembourg": 442, "gnd_blaine": 840,
    "gnd_budapest": 348, "gnd_buffalo": 840, "gnd_channeltunnel": 826,
    "gnd_chicago": 840, "gnd_citydeep": 710, "gnd_columbus": 840,
    "gnd_dadri": 356, "gnd_dallas": 840, "gnd_detroit": 840,
    "gnd_dostyk": 156, "gnd_duisburg": 276, "gnd_elpaso": 840,
    "gnd_erenhot": 156, "gnd_halkali": 792, "gnd_inlandempire": 840,
    "gnd_kansascity": 840, "gnd_khorgos": 398, "gnd_laredo": 840,
    "gnd_madrid": 724, "gnd_malaszewicze": 616, "gnd_manzhouli": 156,
    "gnd_memphis": 840, "gnd_milan": 380, "gnd_montreal": 124,
    "gnd_nogales": 840, "gnd_otaymesa": 840, "gnd_paris": 250,
    "gnd_porthuron": 840, "gnd_riyadh": 682, "gnd_toronto": 124,
    "gnd_vancouver": 124, "gnd_warsaw": 616,
    "sea_antwerp": 56, "sea_busan": 410, "sea_hamburg": 276,
    "sea_hongkong": 344, "sea_houston": 840, "sea_jeddah": 682,
    "sea_longbeach": 840, "sea_losangeles": 840, "sea_mundra": 356,
    "sea_newyork": 840, "sea_nhavasheva": 356, "sea_rotterdam": 528,
    "sea_savannah": 840, "sea_shanghai": 156, "sea_tanjungpriok": 360,
}

NEEDS_KEY_MSG = """NEEDS-KEY: UN_COMTRADE_API_KEY is not set — the Comtrade join cannot run.

Nothing was fetched and nothing was written. To enable it:
  1. Create a free UN Comtrade account and request an API subscription key
     (https://comtradeplus.un.org -> My API Portal; docs:
     https://uncomtrade.org/docs/un-comtrade-api/).
  2. Export the key in the environment (do NOT commit it):
         export UN_COMTRADE_API_KEY=<your key>
  3. Re-run:  python3 data/graph/build/comtrade_overlay.py
With the key present, the script fetches bilateral flows for the graph's
air/ground edges and writes a CANDIDATE overlay (comtrade_overlay.json +
comtrade_overlay.md next to this script). It never modifies graph.json."""


def parse_flows(payload: dict) -> dict:
    """Parse one Comtrade API v1 response payload into
    {(reporter, partner, flowCode): primaryValue}. Schema per the published
    docs: payload["data"] is a list of records with reporterCode,
    partnerCode, flowCode, primaryValue fields."""
    if not isinstance(payload, dict) or payload.get("error"):
        raise ValueError(f"Comtrade error response: {payload.get('error') if isinstance(payload, dict) else payload!r}")
    flows = {}
    for rec in payload.get("data") or []:
        try:
            key = (int(rec["reporterCode"]), int(rec["partnerCode"]), str(rec["flowCode"]))
            val = float(rec["primaryValue"])
        except (KeyError, TypeError, ValueError):
            continue  # skip malformed/aggregate rows defensively
        flows[key] = flows.get(key, 0.0) + val
    return flows


def bilateral_flow(flows: dict, reporter: int, partner: int):
    """(value, mirror) for reporter->partner: exports preferred, partner's
    import record as mirror fallback; (None, False) if neither exists."""
    if (reporter, partner, "X") in flows:
        return flows[(reporter, partner, "X")], False
    if (partner, reporter, "M") in flows:
        return flows[(partner, reporter, "M")], True
    return None, False


def _apportion_1e4(total_units: int, weights: list) -> dict:
    total_w = sum(w for _, w in weights)
    raw = {k: total_units * w / total_w for k, w in weights}
    units = {k: math.floor(v) for k, v in raw.items()}
    remainder = total_units - sum(units.values())
    by_rem = sorted(weights, key=lambda kw: (-(raw[kw[0]] - units[kw[0]]), kw[0]))
    for k, _ in by_rem[:remainder]:
        units[k] += 1
    return units


def build_candidates(edges: list, flows: dict) -> list:
    """Candidate overlay rows for air/ground edges (see method in docstring)."""
    groups = {}
    for e in edges:
        if e["mode"] in ("air", "ground"):
            groups.setdefault((e["from"], e["mode"]), []).append(e)
    rows = []
    for (frm, mode), group in sorted(groups.items()):
        enriched = []
        for e in group:
            key = f'{e["from"]}->{e["to"]}:{e["mode"]}'
            row = {"edge": key, "current_share": e["capacity_share"],
                   "candidate_share": None, "flow_usd": None,
                   "mirror": False, "reason": None}
            r, p = NODE_M49.get(e["from"]), NODE_M49.get(e["to"])
            if r is None or p is None:
                row["reason"] = "unmapped node country (no census country; no candidate)"
            elif r == p:
                row["reason"] = "domestic edge — Comtrade records international flows only"
            else:
                val, mirror = bilateral_flow(flows, r, p)
                if val is None:
                    row["reason"] = "no Comtrade record for this country pair / period"
                else:
                    row["flow_usd"] = val
                    row["mirror"] = mirror
            enriched.append((e, row))
        covered = [(e, row) for e, row in enriched if row["flow_usd"]]
        if len(covered) >= 2:
            s_cov = round(sum(e["capacity_share"] for e, _ in covered), 4)
            units = _apportion_1e4(int(round(s_cov * 10000)),
                                   [(row["edge"], row["flow_usd"]) for _, row in covered])
            for _, row in covered:
                row["candidate_share"] = units[row["edge"]] / 10000
        elif len(covered) == 1:
            covered[0][1]["reason"] = "only covered edge in its (from, mode) group — no distribution to derive"
        rows.extend(row for _, row in enriched)
    return sorted(rows, key=lambda r: r["edge"])


def fetch_flows(key: str, reporters: list, partners: list) -> dict:
    """One request per reporter (free-tier friendly), key in header only."""
    flows = {}
    for rep in reporters:
        params = {"reporterCode": rep, "period": PERIOD,
                  "partnerCode": ",".join(str(p) for p in partners),
                  "cmdCode": "TOTAL", "flowCode": "X,M", "maxrecords": 500}
        url = API_BASE + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={
            "Ocp-Apim-Subscription-Key": key, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        flows.update(parse_flows(payload))
        time.sleep(1)  # stay well inside free-tier rate limits
    return flows


def main(argv: list) -> int:
    key = os.environ.get(ENV_KEY, "").strip()
    if not key:
        print(NEEDS_KEY_MSG)
        return 2
    graph = json.loads(GRAPH_PATH.read_text(encoding="utf-8"))
    edges = graph["edges"]
    ag = [e for e in edges if e["mode"] in ("air", "ground")]
    reporters = sorted({NODE_M49[e["from"]] for e in ag if e["from"] in NODE_M49})
    partners = sorted({NODE_M49[e["to"]] for e in ag if e["to"] in NODE_M49})
    flows = fetch_flows(key, reporters, partners)
    rows = build_candidates(edges, flows)
    derived = [r for r in rows if r["candidate_share"] is not None]
    overlay = {
        "kind": "CANDIDATE calibration overlay — NOT applied to graph.json",
        "source": "UN Comtrade API v1 (data/v1/get/C/A/HS), cmdCode TOTAL, period " + PERIOD,
        "method": "candidate_share per (from, mode) group = S_cov * bilateral flow / SUM(bilateral flows over covered edges); exports preferred, partner imports as flagged mirror; S_cov = current graph subtotal over covered edges; 1e-4 largest-remainder",
        "edges": rows,
    }
    OVERLAY_JSON.write_text(json.dumps(overlay, sort_keys=True, indent=2,
                                       ensure_ascii=False) + "\n", encoding="utf-8")
    lines = ["# Comtrade candidate overlay — NOT applied to graph.json\n",
             f"Period {PERIOD}, cmdCode TOTAL, primaryValue USD. "
             f"{len(derived)} of {len(rows)} air/ground edges have a candidate share; "
             "the rest are listed with their reason in comtrade_overlay.json.\n",
             "Limits: domestic edges have no Comtrade coverage; country-pair "
             "flows cannot split an air lane from a ground leg between the same "
             "two countries; border nodes use the census's first-named country "
             "(Hong Kong nodes use Comtrade reporter 344, not mainland 156).\n",
             "| edge | current | candidate | flow USD | mirror |\n|---|---|---|---|---|\n"]
    for r in rows:
        cand = f'{r["candidate_share"]:.4f}' if r["candidate_share"] is not None else "-"
        flow = f'{r["flow_usd"]:,.0f}' if r["flow_usd"] else "-"
        lines.append(f'| {r["edge"]} | {r["current_share"]:.4f} | {cand} | {flow} | {r["mirror"]} |\n')
    OVERLAY_MD.write_text("".join(lines), encoding="utf-8")
    print(f"overlay written: {len(derived)}/{len(rows)} air/ground edges derived "
          f"-> {OVERLAY_JSON.name} + {OVERLAY_MD.name} (graph.json untouched)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
