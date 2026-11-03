"""U.S. EIA Open Data API v2 adapter — CONTEXT feed.

API: https://api.eia.gov/v2 . Key: env ``EIA_API_KEY``, sent in the
``X-Api-Key`` request header (header auth works), so
the key is never part of a URL, an error string, a snapshot pack or a
record. Nothing in this module logs or stores it.

Series (small, fixed, shipping-relevant energy context):
  * ``eia_spot_price`` — daily spot prices, last ``HISTORY_POINTS`` daily
    observations each: Europe Brent and Cushing WTI crude ($/bbl), New
    York Harbor ULSD diesel and US Gulf Coast kerosene jet fuel ($/gal)
    (petroleum/pri/spt), Henry Hub natural gas spot ($/MMBtu,
    natural-gas/pri/fut series RNGWHHD). Observed values only — no
    forecast, no forward price.
  * ``eia_crude_imports_by_origin`` — US crude oil imports by country of
    origin for the latest published month (EIA-814, crude-oil-imports,
    destination type Port PADD), thousand barrels, summed over grades and
    destination PADDs. Origins arriving via the Strait of Hormuz, Suez,
    Panama etc. are visible to the UI as a list; this adapter does NOT
    sum them into any "at risk" figure.

Budget: 5 upstream calls per live fetch, served from the last-good pack
for ``CACHE_TTL_S`` (4 h) — EIA publishes daily/monthly, so a 15-minute
ingestion cadence costs ~6 live fetches a day, not ~96.
"""
from __future__ import annotations

import json
import random
import time
import urllib.parse
from typing import Optional

from .base import AdapterError, BaseAdapter, FetchResult, provenance
from .feeds_common import context_fields, fresh_live_pack, to_float
from .http_util import RetryExhausted, http_get, http_get_with_retries

BASE = "https://api.eia.gov/v2"
SOURCE = "U.S. Energy Information Administration (EIA) Open Data API v2"
HISTORY_POINTS = 30
CACHE_TTL_S = 4 * 3600
TOP_ORIGINS = 15
MAX_PAGES = 3
PAGE_LEN = 5000

# (series id, label, route, units, shipping relevance)
SPOT_SERIES = (
    ("RBRTE", "Brent crude oil spot (Europe)", "petroleum/pri/spt", "$/bbl",
     "crude benchmark for tanker cargoes and bunker fuel"),
    ("RWTC", "WTI crude oil spot (Cushing, OK)", "petroleum/pri/spt",
     "$/bbl", "US crude benchmark; US Gulf export parcels"),
    ("EER_EPD2DXL0_PF4_Y35NY_DPG", "New York Harbor ULSD diesel spot",
     "petroleum/pri/spt", "$/gal", "distillate / marine gasoil proxy"),
    ("EER_EPJK_PF4_RGC_DPG", "US Gulf Coast jet fuel spot",
     "petroleum/pri/spt", "$/gal", "air-freight fuel cost context"),
    ("RNGWHHD", "Henry Hub natural gas spot", "natural-gas/pri/fut",
     "$/MMBtu", "LNG feedgas price context"),
)


class EIAAdapter(BaseAdapter):
    name = "eia"
    source = SOURCE
    key_env = "EIA_API_KEY"
    health_max_age_s = 12 * 3600

    def __init__(self, *, transport=None, sleep=None, rng=None,
                 cache_ttl_s: float = CACHE_TTL_S) -> None:
        super().__init__()
        self._transport = transport or http_get
        self._sleep = sleep or time.sleep
        self._rng = rng if rng is not None else random.Random()
        self._cache_ttl_s = cache_ttl_s
        self._partial_note: Optional[str] = None
        self.calls = 0

    # -- plumbing -----------------------------------------------------
    def _get_json(self, path: str, params: list) -> dict:
        """GET api.eia.gov/v2/<path>?<params> with the key in a header."""
        import os
        key = os.environ[self.key_env]
        url = f"{BASE}/{path}?" + "&".join(params)
        self.calls += 1
        try:
            outcome = http_get_with_retries(
                url, transport=self._transport, sleep=self._sleep,
                rng=self._rng, max_attempts=3, base_delay_s=2.0,
                max_delay_s=20.0, jitter_s=1.0, timeout=40,
                headers={"X-Api-Key": key})
        except RetryExhausted as exc:
            raise AdapterError(
                f"EIA unreachable after {exc.attempts} attempt(s): "
                f"{type(exc.last_error).__name__}") from None
        if outcome.status in (401, 403):
            raise AdapterError(
                f"EIA rejected the request (HTTP {outcome.status}): "
                "check EIA_API_KEY")
        if outcome.status != 200:
            raise AdapterError(f"EIA HTTP {outcome.status} for {path}")
        try:
            doc = json.loads(outcome.body.decode("utf-8", "replace"))
            response = doc["response"]
            if not isinstance(response, dict):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            raise AdapterError(f"EIA returned an unusable body for {path}") \
                from None
        return response

    # -- sections -----------------------------------------------------
    def _spot_records(self, prov: dict) -> list:
        records = []
        by_route: dict = {}
        for sid, label, route, units, why in SPOT_SERIES:
            by_route.setdefault(route, []).append(sid)
        rows_by_series: dict = {}
        for route, sids in by_route.items():
            params = ["frequency=daily", "data[0]=value",
                      "sort[0][column]=period", "sort[0][direction]=desc",
                      f"length={HISTORY_POINTS * len(sids) + 20}"]
            params += [f"facets[series][]={urllib.parse.quote(s)}"
                       for s in sids]
            resp = self._get_json(f"{route}/data/", params)
            for row in resp.get("data") or []:
                rows_by_series.setdefault(row.get("series"), []).append(row)
        for sid, label, route, units, why in SPOT_SERIES:
            pts = []
            for row in rows_by_series.get(sid, []):
                val = to_float(row.get("value"))
                if val is None or not row.get("period"):
                    continue
                pts.append((str(row["period"]), val))
            pts = sorted(set(pts))[-HISTORY_POINTS:]
            if not pts:
                continue
            latest_period, latest_value = pts[-1]
            records.append({
                "record_type": "eia_spot_price",
                "series_id": sid,
                "label": label,
                "units": units,
                "shipping_relevance": why,
                "latest_period": latest_period,
                "latest_value": latest_value,
                "history": [{"period": p, "value": v} for p, v in pts],
                "frequency": "daily",
                "eia_route": route,
                "dedupe_key": f"eia:{sid}:{latest_period}",
                **context_fields(),
                "provenance": dict(prov),
            })
        return records

    def _import_records(self, prov: dict) -> list:
        meta = self._get_json("crude-oil-imports", [])
        period = meta.get("endPeriod")
        if not period:
            raise AdapterError("EIA crude-oil-imports gave no endPeriod")
        totals: dict = {}
        names: dict = {}
        offset = 0
        for _page in range(MAX_PAGES):
            params = ["frequency=monthly", "data[0]=quantity",
                      "facets[destinationType][]=PP",
                      f"start={period}", f"end={period}",
                      "sort[0][column]=period", "sort[0][direction]=desc",
                      f"length={PAGE_LEN}", f"offset={offset}"]
            resp = self._get_json("crude-oil-imports/data/", params)
            rows = resp.get("data") or []
            for row in rows:
                qty = to_float(row.get("quantity"))
                oid = row.get("originId")
                if qty is None or not oid:
                    continue
                totals[oid] = totals.get(oid, 0.0) + qty
                names[oid] = row.get("originName") or oid
            offset += len(rows)
            total_rows = int(to_float(resp.get("total")) or 0)
            if not rows or offset >= total_rows:
                break
        else:
            self._partial_note = (
                "crude import rows exceeded the page budget; origin totals "
                "may be understated")
        if not totals:
            return []
        grand = sum(totals.values())
        ranked = sorted(totals.items(), key=lambda kv: -kv[1])[:TOP_ORIGINS]
        return [{
            "record_type": "eia_crude_imports_by_origin",
            "period": period,
            "origin_id": oid,
            "origin_name": names[oid],
            "quantity_kbbl": round(qty, 1),
            "us_total_all_origins_kbbl": round(grand, 1),
            "units": "thousand barrels per month",
            "aggregation": ("sum of EIA-814 rows by origin over grades and "
                            "destination PADDs"),
            "dedupe_key": f"eia:crude_imports:{period}:{oid}",
            **context_fields(),
            "provenance": dict(prov),
        } for oid, qty in ranked]

    # -- adapter interface --------------------------------------------
    def fetch(self):
        self._partial_note = None
        result = super().fetch()
        if self._partial_note and self.health.state == "LIVE":
            self.health.note = self._partial_note
        return result

    def _fetch_live(self) -> FetchResult:
        cached = fresh_live_pack(self, self._cache_ttl_s)
        if cached is not None:
            return cached
        prov = provenance(SOURCE, "LIVE")
        records: list = []
        errors: list = []
        for section in (self._spot_records, self._import_records):
            try:
                records += section(prov)
            except AdapterError as exc:
                errors.append(str(exc))
        if not records:
            raise AdapterError("; ".join(errors) or "EIA returned no records")
        if errors:
            self._partial_note = "partial: " + "; ".join(errors)
        return FetchResult(records=records, provenance=prov)
