"""Exposure mapping: node -> listed companies, EDGAR-evidenced.

Artifact: server/money/exposures.json, produced ONLY by
build_exposures.py from real EDGAR fetches. Every mapping cites its
filing {form, filed_date, url, excerpt}; the excerpt is a verbatim slice
of the filing's text layer. A mapping whose filing text yielded no
excerpt carries excerpt=null plus excerpt_missing_reason — it is kept
visible as weaker evidence, never silently upgraded.
"""
from __future__ import annotations

import json
import urllib.parse
from pathlib import Path


def _strip_query(url: str) -> str:
    """URL -> scheme+host+path (error text in the money layer
    never carries a query string, even for public filing URLs)."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return url.split("?", 1)[0]
    if parts.scheme and parts.netloc:
        return urllib.parse.urlunsplit(
            (parts.scheme, parts.netloc, parts.path, "", ""))
    return url.split("?", 1)[0]

EXPOSURES_PATH = Path(__file__).resolve().parent / "exposures.json"

REQUIRED_KEYS = ("node_id", "ticker", "company", "exposure_kind", "filing")
REQUIRED_FILING_KEYS = ("form", "filed_date", "url", "excerpt")


def validate_mapping(m: dict) -> list[str]:
    """Schema check for one mapping; returns a list of problems ([] = ok)."""
    problems = []
    for key in REQUIRED_KEYS:
        if key not in m:
            problems.append(f"missing key: {key}")
    filing = m.get("filing") or {}
    for key in REQUIRED_FILING_KEYS:
        if key not in filing:
            problems.append(f"missing filing key: {key}")
    url = filing.get("url") or ""
    if not url.startswith("https://www.sec.gov/Archives/edgar/data/"):
        problems.append(
            f"filing url is not an EDGAR Archives URL: {_strip_query(url)!r}")
    if filing.get("excerpt") is None and not m.get("excerpt_missing_reason"):
        problems.append("excerpt is null without excerpt_missing_reason")
    if filing.get("excerpt") is not None and len(str(filing["excerpt"])) < 40:
        problems.append("excerpt suspiciously short (<40 chars)")
    return problems


def validate_exposures(doc: dict, graph_node_ids: set[str] | None = None) -> list[str]:
    problems = []
    if doc.get("artifact") != "supermap-money-exposures":
        problems.append("artifact tag missing/wrong")
    mappings = doc.get("mappings")
    if not isinstance(mappings, list) or not mappings:
        problems.append("mappings missing or empty")
        return problems
    seen = set()
    for i, m in enumerate(mappings):
        for p in validate_mapping(m):
            problems.append(f"mappings[{i}] ({m.get('ticker')}@{m.get('node_id')}): {p}")
        key = (m.get("node_id"), m.get("ticker"))
        if key in seen:
            problems.append(f"duplicate mapping: {key}")
        seen.add(key)
        if graph_node_ids is not None and m.get("node_id") not in graph_node_ids:
            problems.append(f"mappings[{i}]: node_id {m.get('node_id')!r} not in graph")
    return problems


def load_exposures(path: str | Path = EXPOSURES_PATH) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def mappings_by_node(doc: dict) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for m in doc.get("mappings", []):
        out.setdefault(m["node_id"], []).append(m)
    return out
