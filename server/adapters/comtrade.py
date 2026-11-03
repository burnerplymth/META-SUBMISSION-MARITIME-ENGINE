"""UN Comtrade v1 adapter — CONTEXT feed.

API: https://comtradeapi.un.org/data/v1/get/C/A/HS (classic goods, annual,
HS). Key: env ``UN_COMTRADE_API_KEY``, sent in the
``Ocp-Apim-Subscription-Key`` header — never in a URL, error string,
pack or record.

Scope (small, fixed — one fan-out of two calls):
  * Call A: reporters {USA, China, Singapore, Netherlands, Egypt, Panama}
    vs partner World, flows import + export, HS chapters {27 mineral
    fuels, 84 machinery, 85 electrical machinery, 87 vehicles, 10
    cereals}. These reporters are the gateway economies of the graph's
    chokepoints/ports (Suez/Egypt, Panama, Malacca/Singapore,
    Rotterdam/Netherlands, trans-Pacific China-US).
  * Call B: BILATERAL corridors China <-> USA and China -> {Netherlands,
    Singapore} (and USA -> {China, Netherlands, Singapore}) for the same
    chapters.
  Years requested: ``PERIODS``; for each (reporter, partner, flow,
  chapter) the LATEST year Comtrade has is kept, and its year is on the
  record. Values are USD, Comtrade ``primaryValue``, total transport mode
  and total customs procedure only.

Rate limits (free tier ~500 calls/day, 1 call/s): 2 calls per live fetch,
``MIN_CALL_GAP_S`` spacing, a persistent daily call counter capped at
``DAILY_CALL_BUDGET`` (data/comtrade_calls.json), and the whole result is
served from the last-good pack for ``CACHE_TTL_S`` (24 h), so steady
state is ~2 calls/day. A 429 is honoured (Retry-After), not hammered.

Doctrine: figures are labelled SOURCED, and are NEVER summed into a headline
"trade at risk" number here or in the route. Context only — market
quarantine applies.

Licence: UN Comtrade's terms do not allow redistributing the
original records. The adapter therefore turns the fetched values into DERIVED
SHARES before anything leaves the fetch (``derive_records``): a chapter's share
of the reporter's five-chapter total, and a partner's share of the reporter's
world value. Only those shares are returned, cached, written to the snapshot
pack and served; no USD value is persisted. ``scripts/fetch_comtrade.py``
holds the local record-level path (git-ignored) for the impact layer.
"""
from __future__ import annotations

import json
import os
import random
import time
import urllib.parse
from typing import Optional

from . import base as _base
from .base import AdapterError, BaseAdapter, FetchResult, provenance
from .feeds_common import context_fields, fresh_live_pack, to_float
from .http_util import RetryExhausted, http_get, http_get_with_retries

API = "https://comtradeapi.un.org/data/v1/get/C/A/HS"
SOURCE = "UN Comtrade (UN Statistics Division) v1 API"
PERIODS = (2023, 2024, 2025)
CACHE_TTL_S = 24 * 3600
MIN_CALL_GAP_S = 2.0
DAILY_CALL_BUDGET = 40          # free tier allows ~500/day
CHAPTERS = {
    "27": "Mineral fuels, oils",
    "84": "Machinery and mechanical appliances",
    "85": "Electrical machinery and equipment",
    "87": "Vehicles (other than rail)",
    "10": "Cereals",
}
# reporter code -> (ISO3, name, graph places that reporter gates)
REPORTERS = {
    842: ("USA", "United States", ("sea_losangeles", "sea_newyork")),
    156: ("CHN", "China", ("sea_shanghai", "sea_ningbo", "chk_taiwan")),
    702: ("SGP", "Singapore", ("sea_singapore", "chk_malacca")),
    528: ("NLD", "Netherlands", ("sea_rotterdam", "chk_dover")),
    818: ("EGY", "Egypt", ("chk_suez", "sea_portsaid")),
    591: ("PAN", "Panama", ("chk_panama", "sea_balboa")),
}
# (reporter codes, partner codes)
CALLS = (
    ((842, 156, 702, 528, 818, 591), (0,)),
    ((156, 842), (842, 156, 528, 702)),
)
PARTNER_NAMES = {0: ("W00", "World"), 842: ("USA", "United States"),
                 156: ("CHN", "China"), 528: ("NLD", "Netherlands"),
                 702: ("SGP", "Singapore")}
FLOWS = {"M": "import", "X": "export"}
NOTICE = "Derived from UN Comtrade; not the original records"
CITATION = "UN Comtrade"
MEASURES = {
    "chapter_share": "Share of the reporter's five-chapter total, same flow and year",
    "partner_share": "Share of the reporter's world value in this chapter, same flow and year",
}


def derive_records(raw: list) -> list:
    """Record-level ``comtrade_trade`` records -> derived share records.

    ``chapter_share``: World-partner value of a chapter / the sum of the five
    chapters, only when all five exist for that reporter, flow and year.
    ``partner_share``: a named partner's value / the reporter's World value for
    the same chapter, flow and year. Everything else is dropped; no USD
    amount is carried."""
    world: dict = {}
    for r in raw:
        if r["partner"]["code"] == 0:
            world[(r["reporter"]["code"], r["flow"], r["hs_chapter"],
                   r["period"])] = r["value_usd"]
    out = []
    for r in raw:
        rep, flow = r["reporter"]["code"], r["flow"]
        chapter, period = r["hs_chapter"], r["period"]
        if r["partner"]["code"] == 0:
            parts = [world.get((rep, flow, c, period)) for c in CHAPTERS]
            if any(v is None for v in parts) or sum(parts) <= 0:
                continue
            share, measure = r["value_usd"] / sum(parts), "chapter_share"
        else:
            base = world.get((rep, flow, chapter, period))
            if not base or base <= 0:
                continue
            share, measure = r["value_usd"] / base, "partner_share"
            if share > 1.000001:      # inconsistent mirror data: not a share
                continue
        rec = {k: v for k, v in r.items() if k not in ("value_usd", "value_field", "record_type")}
        rec.update({"record_type": "comtrade_derived", "measure": measure,
                    "share": round(share, 6), "share_field": MEASURES[measure],
                    "derived": True, "notice": NOTICE, "citation": CITATION})
        out.append(rec)
    return out


class ComtradeAdapter(BaseAdapter):
    name = "comtrade"
    source = SOURCE
    key_env = "UN_COMTRADE_API_KEY"
    health_max_age_s = 72 * 3600

    def __init__(self, *, transport=None, sleep=None, rng=None,
                 cache_ttl_s: float = CACHE_TTL_S, call_gap_s: float =
                 MIN_CALL_GAP_S) -> None:
        super().__init__()
        self._transport = transport or http_get
        self._sleep = sleep or time.sleep
        self._rng = rng if rng is not None else random.Random()
        self._cache_ttl_s = cache_ttl_s
        self._call_gap_s = call_gap_s
        self._credit_path = None   # override point
        self.calls = 0

    # -- daily call counter ---------------------------------------------
    @property
    def counter_path(self):
        return self._credit_path or (_base.SNAPSHOT_DIR.parent /
                                     "comtrade_calls.json")

    def _calls_today(self) -> tuple:
        today = time.strftime("%Y-%m-%d", time.gmtime())
        try:
            saved = json.loads(self.counter_path.read_text(encoding="utf-8"))
            if saved.get("date") == today:
                return today, int(saved.get("calls") or 0)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
        return today, 0

    def _charge_call(self) -> None:
        today, used = self._calls_today()
        try:
            self.counter_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.counter_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps({"date": today, "calls": used + 1,
                                       "budget": DAILY_CALL_BUDGET}),
                           encoding="utf-8")
            tmp.replace(self.counter_path)
        except OSError:
            pass

    # -- upstream ---------------------------------------------------------
    def _call(self, reporters, partners) -> list:
        key = os.environ[self.key_env]
        _today, used = self._calls_today()
        if used >= DAILY_CALL_BUDGET:
            raise AdapterError(
                f"Comtrade daily call budget reached ({used}/"
                f"{DAILY_CALL_BUDGET}); serving the last-good pack")
        params = {
            "reporterCode": ",".join(str(r) for r in reporters),
            "partnerCode": ",".join(str(p) for p in partners),
            "period": ",".join(str(p) for p in PERIODS),
            "cmdCode": ",".join(CHAPTERS),
            "flowCode": "M,X",
            "motCode": "0", "customsCode": "C00", "partner2Code": "0",
            "includeDesc": "true",
        }
        url = API + "?" + urllib.parse.urlencode(params, safe=",")
        self.calls += 1
        self._charge_call()
        try:
            outcome = http_get_with_retries(
                url, transport=self._transport, sleep=self._sleep,
                rng=self._rng, max_attempts=2, base_delay_s=5.0,
                max_delay_s=30.0, jitter_s=1.0, timeout=60,
                headers={"Ocp-Apim-Subscription-Key": key})
        except RetryExhausted as exc:
            raise AdapterError(
                f"Comtrade unreachable after {exc.attempts} attempt(s): "
                f"{type(exc.last_error).__name__}") from None
        st = outcome.status
        if st in (401, 403):
            raise AdapterError(
                f"Comtrade rejected the request (HTTP {st}): check "
                "UN_COMTRADE_API_KEY")
        if st == 429:
            raise AdapterError("Comtrade rate limit (HTTP 429); will retry "
                               "next cycle")
        if st != 200:
            raise AdapterError(f"Comtrade HTTP {st}")
        try:
            doc = json.loads(outcome.body.decode("utf-8", "replace"))
            rows = doc["data"]
            if not isinstance(rows, list):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            raise AdapterError("Comtrade returned an unusable body") from None
        return rows

    # -- record building --------------------------------------------------
    @staticmethod
    def _records_from_rows(rows: list, prov: dict) -> list:
        best: dict = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                rep = int(row.get("reporterCode"))
                par = int(row.get("partnerCode"))
                year = int(row.get("period"))
            except (TypeError, ValueError):
                continue
            flow = row.get("flowCode")
            chapter = str(row.get("cmdCode") or "")
            value = to_float(row.get("primaryValue"))
            if (rep not in REPORTERS or flow not in FLOWS
                    or chapter not in CHAPTERS or value is None
                    or value < 0 or par not in PARTNER_NAMES):
                continue
            if row.get("motCode") not in (0, "0", None) or \
                    row.get("customsCode") not in ("C00", None):
                continue
            key = (rep, par, flow, chapter)
            if key not in best or year > best[key][0]:
                best[key] = (year, value)
        records = []
        for (rep, par, flow, chapter), (year, value) in sorted(best.items()):
            iso, name, places = REPORTERS[rep]
            p_iso, p_name = PARTNER_NAMES[par]
            records.append({
                "record_type": "comtrade_trade",
                "reporter": {"code": rep, "iso": iso, "name": name},
                "partner": {"code": par, "iso": p_iso, "name": p_name},
                "flow": FLOWS[flow],
                "hs_chapter": chapter,
                "hs_chapter_name": CHAPTERS[chapter],
                "period": str(year),
                "frequency": "annual",
                "value_usd": value,
                "value_field": "Comtrade primaryValue (USD, current)",
                "related_place_ids": list(places),
                "dedupe_key": (f"comtrade:{iso}:{p_iso}:{flow}:"
                               f"{chapter}:{year}"),
                **context_fields(),
                "provenance": dict(prov),
            })
        return records

    # -- adapter interface ------------------------------------------------
    def _fetch_live(self) -> FetchResult:
        cached = fresh_live_pack(self, self._cache_ttl_s)
        if cached is not None:
            return cached
        prov = provenance(SOURCE, "LIVE")
        rows: list = []
        errors: list = []
        for i, (reporters, partners) in enumerate(CALLS):
            if i:
                self._sleep(self._call_gap_s)
            try:
                rows += self._call(reporters, partners)
            except AdapterError as exc:
                errors.append(str(exc))
                if "429" in str(exc) or "budget" in str(exc):
                    break
        records = derive_records(self._records_from_rows(rows, prov))
        if not records:
            raise AdapterError("; ".join(errors) or
                               "Comtrade returned no usable rows")
        if errors:
            self._partial_note = "partial: " + "; ".join(errors)
        return FetchResult(records=records, provenance=prov)

    _partial_note: Optional[str] = None

    def fetch(self):
        self._partial_note = None
        result = super().fetch()
        if self._partial_note and self.health.state == "LIVE":
            self.health.note = self._partial_note
        return result
