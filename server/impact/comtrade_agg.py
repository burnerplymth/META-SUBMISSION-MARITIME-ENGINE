"""Derived aggregates of UN Comtrade extracts.

UN Comtrade's terms (comtrade.un.org/licenseagreement.html) do not allow
redistribution of the original records without written permission. The
public repository therefore ships only *derived* aggregates: for each
(reporter, HS group) the share of the reporter's imports that comes from each
routing region of ``data/impact/routing_sets.json``, on the basis (net weight
or value) the impact code would have used. No partner-level record and no
absolute value is kept. ``derive.py`` reads either input and returns the same
numbers.

The record-level form (``partners`` list per extract) is produced locally by
``scripts/fetch_comtrade.py`` with the user's own key, into a git-ignored file.
"""
from __future__ import annotations

from typing import Callable, Optional

NOTICE = "Derived from UN Comtrade; not the original records"
CITATION = "Source: UN Comtrade (comtrade.un.org), UN Statistics Division"
UNASSIGNED = "unassigned"   # partners in no routing region: in the denominator, never exposed


def build_region_of(routing: dict) -> Callable[[object], Optional[str]]:
    """The impact code's partner-code -> region mapping, from the routing sets."""
    us_codes = set(routing.get("us_partner_codes", [840, 842]))
    partner_region: dict[int, str] = {}
    for rname, rdef in (routing.get("regions") or {}).items():
        for code in rdef.get("partner_codes", []):
            partner_region[int(code)] = rname

    def region_of(code):
        if code in us_codes:
            return "north_america"
        return partner_region.get(int(code))
    return region_of


def has_partners(rec: dict) -> bool:
    """True when the extract holds at least one partner (record-level or derived)."""
    if rec.get("partners"):
        return True
    return bool(rec.get("partner_count"))


def is_aggregate(rec: dict) -> bool:
    return isinstance(rec.get("region_shares"), dict)


def region_shares(rec: dict, region_of: Callable) -> Optional[tuple[str, dict]]:
    """``(basis, {region|UNASSIGNED: share})`` for one extract, or ``None`` when
    no positive total exists. Shares sum to 1 over the basis (net weight when
    every positive-value partner carries one, else value as reported)."""
    if is_aggregate(rec):
        shares = {k: float(v) for k, v in rec["region_shares"].items()}
        if not shares:
            return None
        return rec.get("basis") or "value (CIF, as reported)", shares
    partners = rec.get("partners") or []
    if not partners:
        return None
    total_val = sum((p.get("value_usd") or 0) for p in partners)
    if total_val <= 0:
        return None
    weight_ok = all(p.get("net_weight_kg") is not None
                    for p in partners if (p.get("value_usd") or 0) > 0)
    if weight_ok:
        basis = "net weight"

        def amount(p):
            return p.get("net_weight_kg") or 0
    else:
        basis = "value (CIF, as reported)"

        def amount(p):
            return p.get("value_usd") or 0
    total = sum(amount(p) for p in partners)
    if total <= 0:
        return None
    out: dict = {}
    for p in partners:
        code = p.get("partner_code")
        region = region_of(code if code is not None else -1) or UNASSIGNED
        out[region] = out.get(region, 0.0) + amount(p)
    return basis, {k: v / total for k, v in out.items()}


def aggregate_extracts(doc: dict, routing: dict, derived_at: str) -> dict:
    """Record-level extracts document -> derived aggregates document."""
    region_of = build_region_of(routing)
    aggregates, empty = [], []
    for rec in doc.get("extracts", []):
        if not isinstance(rec, dict):
            continue
        if rec.get("empty"):
            empty.append({"reporter_code": rec.get("reporter_code"),
                          "cmd_code": rec.get("cmd_code")})
            continue
        got = region_shares(rec, region_of)
        if got is None:
            continue
        basis, shares = got
        aggregates.append({
            "reporter_code": rec.get("reporter_code"),
            "reporter_iso3": rec.get("reporter_iso3"),
            "reporter_name": rec.get("reporter_name"),
            "cmd_code": rec.get("cmd_code"),
            "period": rec.get("period"),
            "flow": rec.get("flow"),
            "basis": basis,
            "partner_count": len(rec.get("partners") or []),
            "region_shares": {k: round(v, 9) for k, v in sorted(shares.items())},
        })
    aggregates.sort(key=lambda r: (r["reporter_code"], r["cmd_code"]))
    return {
        "kind": NOTICE,
        "source": "UN Comtrade",
        "citation": CITATION,
        "derivation": ("Share of each reporter's imports per HS group that comes from each routing "
                       "region, on net weight where every partner carries one, else value. "
                       "No partner record and no absolute value is kept."),
        "routing_sets_version": routing.get("version"),
        "period_policy": doc.get("period_policy"),
        "retrieved_at": doc.get("retrieved_at"),
        "derived_at": derived_at,
        "aggregates": aggregates,
        "empty": sorted(empty, key=lambda e: (e["reporter_code"], e["cmd_code"])),
        "failures": doc.get("failures") or [],
    }
