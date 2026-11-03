"""GDELT DOC 2.0 poller — no key.

Endpoint (GDELT rate-limits shared IPs: HTTP 429 with the plain-text
notice "Please limit requests to one every 5 seconds", no Retry-After
header):
  https://api.gdeltproject.org/api/v2/doc/doc?...&mode=ArtList&format=json

Output discipline: GDELT items are *feed records only*.
This adapter never mints events — unstructured news text becomes a
event only via the classifier with its confidence threshold.

Rate-limit citizenship:
  * >= 6 s spacing between requests, enforced by a module-level clock
    (GDELT's own published ask is one request every 5 s);
  * per-cycle request budget: at most ``MAX_ATTEMPTS`` (=4) HTTP requests
    for the one query a cycle makes — the budget is the retry cap;
  * bounded retries with exponential backoff (5 s base, doubling, 60 s
    cap) + jitter on 429/5xx, on transport failures, and on
    GDELT's plain-text rate-limit notice (which can arrive as HTTP 200);
    a ``Retry-After`` header, when sent, is honoured (capped at 60 s);
  * on exhaustion the AdapterError says exactly what happened (status,
    attempts, delays) and ``BaseAdapter.fetch()`` falls back to the
    last-good SNAPSHOT pack — stale data is never relabelled LIVE.
Transport, sleep and RNG are injectable so the backoff
sequence can be driven with a scripted transport and zero real waiting.
"""
from __future__ import annotations

import json
import random
import time
import urllib.parse

from .base import AdapterError, BaseAdapter, FetchResult, provenance
from .http_util import RetryExhausted, http_get, http_get_with_retries

API = "https://api.gdeltproject.org/api/v2/doc/doc"
SOURCE = "GDELT"
QUERY = (
    '("port strike" OR "port closure" OR "canal blocked" OR "shipping disruption" '
    'OR "supply chain disruption" OR "container ship" OR "border crossing closed")'
)
MIN_INTERVAL_S = 6.0
MAX_ATTEMPTS = 4          # per-cycle request budget for the cycle's one query
BASE_DELAY_S = 5.0        # GDELT's own spacing ask; doubles per retry
MAX_DELAY_S = 60.0
JITTER_S = 1.0
_last_call = 0.0


def _is_rate_limit_notice(status: int, body: bytes, headers: dict) -> bool:
    """Retryable response? 429/5xx always; GDELT's plain-text notice page
    (not JSON) is a rate-limit answer even when it arrives as HTTP 200."""
    if status in (429, 500, 502, 503, 504):
        return True
    return status == 200 and not body.lstrip().startswith(b"{")


class GDELTAdapter(BaseAdapter):
    name = "gdelt"
    source = SOURCE
    health_max_age_s = 3600  # stale-LIVE threshold

    def __init__(self, *, transport=None, sleep=None, rng=None) -> None:
        super().__init__()
        self._transport = transport or http_get
        self._sleep = sleep or time.sleep
        # entropy-seeded by default (no hard-coded seed);
        # a seeded Random may be injected for deterministic delays.
        self._rng = rng if rng is not None else random.Random()
        self.last_attempts: int | None = None   # observability for logs
        self.last_delays: list[float] = []

    def _fetch_live(self) -> FetchResult:
        global _last_call
        wait = MIN_INTERVAL_S - (time.monotonic() - _last_call)
        if wait > 0:
            self._sleep(wait)
        params = {
            "query": QUERY, "mode": "ArtList", "maxrecords": 50,
            "format": "json", "timespan": "3d", "sort": "DateDesc",
        }
        url = API + "?" + urllib.parse.urlencode(params)
        _last_call = time.monotonic()
        try:
            outcome = http_get_with_retries(
                url, transport=self._transport, sleep=self._sleep,
                rng=self._rng, max_attempts=MAX_ATTEMPTS,
                base_delay_s=BASE_DELAY_S, max_delay_s=MAX_DELAY_S,
                jitter_s=JITTER_S, retry_if=_is_rate_limit_notice,
                timeout=45)
        except RetryExhausted as exc:
            self.last_attempts, self.last_delays = exc.attempts, exc.delays
            raise AdapterError(
                f"GDELT transport failed after {exc.attempts} attempt(s) "
                f"(backoff delays {[round(d, 2) for d in exc.delays]}s): "
                f"{exc.last_error}") from exc
        self.last_attempts, self.last_delays = outcome.attempts, outcome.delays
        status, body = outcome.status, outcome.body
        text = body.decode("utf-8", "replace")
        if status != 200:
            raise AdapterError(
                f"GDELT HTTP {status} after {outcome.attempts} attempt(s) "
                f"(backoff delays {[round(d, 2) for d in outcome.delays]}s; "
                f"per-cycle budget {MAX_ATTEMPTS} exhausted)")
        if not text.lstrip().startswith("{"):
            # GDELT's rate-limit / notice page is plain text, not JSON.
            raise AdapterError(
                f"GDELT returned a non-JSON notice (rate limit?) after "
                f"{outcome.attempts} attempt(s)")
        payload = json.loads(text)
        prov = provenance(SOURCE, "LIVE")
        records = []
        for art in payload.get("articles", []):
            url = art.get("url")
            if not url:
                continue
            records.append({
                "record_type": "feed_item",
                "title": art.get("title"),
                "url": url,
                "domain": art.get("domain"),
                "language": art.get("language"),
                "source_country": art.get("sourcecountry"),
                "seendate": art.get("seendate"),
                "tone": art.get("tone"),
                "dedupe_key": f"gdelt:{url}",
                "provenance": dict(prov),
            })
        return FetchResult(records=records, provenance=prov)
