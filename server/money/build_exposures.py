"""Build server/money/exposures.json from REAL EDGAR evidence.

Pipeline (every step a live EDGAR fetch, throttled, declared UA):
  1. company_tickers.json — verify each candidate ticker is an EDGAR
     filer and take its CIK + legal name from EDGAR, not from memory.
  2. submissions API per company — most recent annual report
     (20-F / 10-K / 40-F).
  3. the filing document itself (Archives) — text layer searched for
     the node-tying phrases in TARGETS; the excerpt stored is a
     verbatim slice of that text.

A target with no matching passage is either dropped (optional targets)
or emitted with excerpt=null + excerpt_missing_reason (required
targets) — never given an invented excerpt.

Run:  python3 -m money.build_exposures        (from server/)
Filing texts are cached under SUPERMAP_FILINGS_CACHE (default
~/.cache/supermap-filings) so re-runs are cheap; the cache is scratch,
never a source — delete it and the script refetches.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from money.common import utcnow_iso  # noqa: E402
from money.edgar import (EDGARClient, filing_url, find_excerpt,  # noqa: E402
                         latest_annual_filing)

CACHE_DIR = Path(os.environ.get(
    "SUPERMAP_FILINGS_CACHE", Path.home() / ".cache" / "supermap-filings"))
OUT_PATH = Path(__file__).resolve().parent / "exposures.json"

# (ticker, node_id, phrases, require_any, exposure_kind, required?)
# Phrases are tried in order; the excerpt is the first passage that
# matches (and, when require_any is set, contains one of those words).
TARGETS = [
    # Container liner — Suez / Bab el-Mandeb route exposure (20-F risk factors)
    ("ZIM", "chk_suez", ["Suez Canal"], None, "route_exposure", True),
    ("ZIM", "chk_babelmandeb", ["Bab el-Mandeb", "Red Sea"], None,
     "route_exposure", True),
    # NOTE: a ZIM@sea_newyork target was tried and REJECTED —
    # the only 'New York' passages in the 20-F concern the NYSE listing,
    # not port calls. No passage, no mapping.
    # Matson — US West Coast port operations + China express calls
    ("MATX", "sea_longbeach", ["Long Beach"], None, "port_operations", True),
    ("MATX", "sea_losangeles", ["Los Angeles"], None, "port_operations", False),
    ("MATX", "sea_ningbo", ["Ningbo"], ["service", "express", "calls"],
     "route_exposure", False),
    ("MATX", "sea_shanghai", ["Shanghai"], ["service", "express", "calls"],
     "route_exposure", False),
    # Air hubs
    ("FDX", "air_memphis", ["Memphis"], ["hub"], "hub_operations", True),
    ("FDX", "air_anchorage", ["Anchorage"], ["International"],
     "hub_operations", False),
    ("FDX", "air_indianapolis", ["Indianapolis"], ["hub"], "hub_operations", False),
    ("UPS", "air_louisville", ["Louisville"], ["Worldport", "hub"],
     "hub_operations", True),
    ("UPS", "air_ontario", ["Ontario"], ["California", "hub"],
     "hub_operations", False),
    ("UPS", "air_cologne", ["Cologne"], ["hub"], "hub_operations", False),
    # Containership lessors — chokepoint route exposure of the chartered fleet
    ("CMRE", "chk_suez", ["Suez Canal"], None, "charter_fleet_exposure", False),
    ("CMRE", "chk_panama", ["Panama Canal"], None, "charter_fleet_exposure", False),
    ("GSL", "chk_suez", ["Suez Canal"], None, "charter_fleet_exposure", False),
    # NOTE: a GSL@chk_panama target was tried and REJECTED —
    # the only passage says one vessel is NOT intended for Panama Canal
    # operation. Asserting it as exposure would invert the evidence.
    ("DAC", "chk_suez", ["Suez Canal"], None, "charter_fleet_exposure", False),
    ("DAC", "chk_panama", ["Panama Canal"], None, "charter_fleet_exposure", False),
    # Tanker fleets — 'Suezmax' alone was REJECTED as evidence
    # (segment tables / XBRL taxonomy noise; neither STNG, NAT, INSW nor
    # SFL filings define it against the canal in narrative). The canal
    # passages below are the real ties.
    ("STNG", "chk_suez", ["Suez Canal"], None, "route_exposure", True),
    ("STNG", "chk_panama", ["Panama Canal"], None, "route_exposure", False),
    ("NAT", "chk_suez", ["Suez Canal"], None, "route_exposure", True),
    ("NAT", "chk_babelmandeb", ["Red Sea"], ["reroute", "avoid"],
     "route_exposure", False),
    ("INSW", "chk_suez", ["Suezmax"], ["Suez Canal"], "fleet_segment", False),
    ("SFL", "chk_suez", ["Suezmax"], ["Suez Canal"], "fleet_segment", False),
    # Tanker routes — Hormuz
    ("STNG", "chk_hormuz", ["Strait of Hormuz", "Hormuz"], None,
     "route_exposure", False),
    ("INSW", "chk_hormuz", ["Strait of Hormuz", "Hormuz"], None,
     "route_exposure", False),
    ("TRMD", "chk_hormuz", ["Strait of Hormuz", "Hormuz"], None,
     "route_exposure", False),
    ("TRMD", "chk_suez", ["Suez Canal"], None, "route_exposure", False),
]

# Candidates checked against the tickers file for the record (no targets):
VERIFY_ONLY = ["ATSG", "AAWW", "HA", "MAERSK"]


def main() -> int:
    client = EDGARClient()
    tickers_res = client.company_tickers()
    tickers = tickers_res["records"]
    print(f"tickers file: {len(tickers)} tickers, "
          f"provenance={tickers_res['provenance']}")

    wanted = sorted({t[0] for t in TARGETS})
    verified, missing = {}, []
    for t in wanted + VERIFY_ONLY:
        if t in tickers:
            verified[t] = tickers[t]
            print(f"  VERIFIED {t}: CIK {tickers[t]['cik']} {tickers[t]['company']}")
        else:
            missing.append(t)
            print(f"  NOT IN EDGAR TICKERS FILE: {t}")

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    filings: dict[str, dict] = {}   # ticker -> {filing meta, url}
    texts: dict[str, str] = {}
    for t in wanted:
        if t not in verified:
            continue
        cik = verified[t]["cik"]
        subs = client.submissions(cik)["records"]
        annual = latest_annual_filing(subs)
        if annual is None:
            print(f"  {t}: NO annual filing (20-F/10-K/40-F) in submissions")
            continue
        url = filing_url(cik, annual["accession"], annual["primary_document"])
        filings[t] = {**annual, "url": url}
        cache = CACHE_DIR / f"{t}_{annual['accession']}.txt"
        if cache.exists():
            texts[t] = cache.read_text(encoding="utf-8")
            print(f"  {t}: {annual['form']} filed {annual['filed_date']} "
                  f"(text from cache, {len(texts[t])} chars)")
        else:
            texts[t] = client.filing_text(url)["records"]
            cache.write_text(texts[t], encoding="utf-8")
            print(f"  {t}: {annual['form']} filed {annual['filed_date']} "
                  f"fetched live, {len(texts[t])} chars text")

    mappings, without_excerpt = [], []
    for ticker, node_id, phrases, require_any, kind, required in TARGETS:
        if ticker not in filings:
            print(f"  SKIP {ticker}@{node_id}: no filing")
            continue
        hit = find_excerpt(texts[ticker], phrases, require_any)
        f = filings[ticker]
        base = {
            "node_id": node_id,
            "ticker": ticker,
            "company": verified[ticker]["company"],
            "cik": verified[ticker]["cik"],
            "exposure_kind": kind,
            "matched_phrase": hit[0] if hit else None,
            "filing": {
                "form": f["form"],
                "filed_date": f["filed_date"],
                "report_date": f["report_date"],
                "accession": f["accession"],
                "url": f["url"],
                "excerpt": hit[1] if hit else None,
            },
        }
        if hit:
            mappings.append(base)
            print(f"  MAP {ticker}@{node_id}: phrase {hit[0]!r} excerpt "
                  f"{len(hit[1])} chars")
        elif required:
            base["excerpt_missing_reason"] = (
                f"none of {phrases} found in the text layer of the "
                f"{f['form']} filed {f['filed_date']}; mapping retained "
                "from submissions metadata only, excerpt NOT invented")
            mappings.append(base)
            without_excerpt.append(f"{ticker}@{node_id}")
            print(f"  MAP-NOEXCERPT {ticker}@{node_id} (required target)")
        else:
            print(f"  DROP {ticker}@{node_id}: no passage for {phrases} "
                  "(optional target, dropped rather than asserted)")

    doc = {
        "artifact": "supermap-money-exposures",
        "generated_by": "server/money/build_exposures.py (live EDGAR harvest)",
        "harvest": {
            "retrieved_at": utcnow_iso(),
            "user_agent": "declared via SUPERMAP_EDGAR_UA (required — "
                          "live EDGAR fetch refuses without it, see "
                          "edgar.py); EDGAR fair-access, throttled",
            "tickers_file": {"source": "SEC EDGAR",
                             "url": "https://www.sec.gov/files/company_tickers.json",
                             "tickers_verified": len(verified),
                             "tickers_not_in_file": missing},
        },
        "method": "node->company mappings asserted ONLY where the cited "
                  "annual filing's own text ties the company to the node "
                  "(verbatim excerpt stored per mapping); optional targets "
                  "with no passage were dropped, not asserted",
        "mappings": mappings,
        "mappings_without_excerpts": without_excerpt,
    }
    OUT_PATH.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8")
    print(f"\nwrote {OUT_PATH} with {len(mappings)} mappings "
          f"({len(without_excerpt)} without excerpts)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
