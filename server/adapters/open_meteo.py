"""Resilient Open-Meteo JSON fetcher.

Two separate failure modes, one code-side and one provider-side:

1. CODE: roughly 40 % of NEW connections to ``api.open-meteo.com`` stall
   at connect (``httpx.ConnectTimeout`` / curl exit 28 after 30 s, no byte
   ever returned; ``marine-api`` showed 0/10 in the same window). A retry
   on a fresh connection succeeds. ``sim.weather`` makes one 30 s attempt
   per batch with no retry, so a single stall would fail the whole snapshot
   ("Open-Meteo fetch failed: ConnectTimeout"). Cure: short connect
   timeout, connect-level retries, one shared keep-alive client.
2. QUOTA: a full sim snapshot is ~60 sequential 50-location x 16-day
   batches. The free tier allows ~600 weighted calls per MINUTE (a batch
   weighs ~55): measured, request 12 of a burst returned HTTP 429
   "Minutely API request limit exceeded. Please try again in one minute."
   and the daily budget (10,000) covers only ~3 full snapshots. Cure:
   wait out a minutely 429 (bounded window), fail fast on an hourly/daily
   one, and cache every successful batch on disk (TTL ``CACHE_TTL_S``) so
   a retry after a bounded failure RESUMES instead of starting over, and
   a second sim run inside the TTL spends nothing.

``robust_fetch_json(url, params)`` is a drop-in for the
``fetch_json(url, params) -> parsed JSON`` seam of
``sim.weather.fetch_snapshot`` (``create_app(sim_weather_fetcher=...)``).
It never fabricates: an exhausted failure raises, and fetch_snapshot
turns that into its labelled "weather unavailable" absence. Cached
payloads are provider responses served unchanged within the TTL; the sim
pins the resulting snapshot (and its hash) into the run journal exactly
as before.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path

# OFFLINE SIM STALL: this fetcher runs INSIDE POST /sim/run. With
# the network dead a long retry ladder would delay the labelled
# "weather unavailable" absence. Limits: 3 s to connect, 8 s per response, one
# transport-level connect retry, and a CALL_BUDGET_S wall-clock budget for the
# whole call's retries (quota waits are outside it). A flaky host that stalls
# one connect in a few is still retried inside the budget.
CONNECT_TIMEOUT_S = 3.0
READ_TIMEOUT_S = 8.0
CALL_BUDGET_S = 8.0
CONNECT_RETRIES = 1          # transport-level (connect errors only)
ATTEMPTS = 4                 # request-level (read errors, 429/5xx)
RETRY_STATUSES = (500, 502, 503, 504)
CACHE_TTL_S = 6 * 3600
MINUTELY_WAIT_S = 61.0        # "try again in one minute"
MAX_TOTAL_WAIT_S = 150.0      # per quota window; then fail honestly
_QUOTA_WINDOW_RESET_S = 600.0

_lock = threading.Lock()
_client = None
_window_start: float | None = None   # monotonic start of the quota wait window


class OpenMeteoError(Exception):
    pass


def _proxy_url():
    for var in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        value = os.environ.get(var)
        if value:
            return value
    return None


def _shared_client():
    """One keep-alive httpx client for the process (lazy, thread-safe)."""
    global _client
    with _lock:
        if _client is None:
            import httpx
            transport = httpx.HTTPTransport(
                retries=CONNECT_RETRIES, proxy=_proxy_url())
            _client = httpx.Client(
                transport=transport,
                timeout=httpx.Timeout(READ_TIMEOUT_S,
                                      connect=CONNECT_TIMEOUT_S),
                headers={"User-Agent": "SupermAP/0.1 (research build)"})
        return _client


def reset_client() -> None:
    """Drop the shared client (or after a fork)."""
    global _client
    with _lock:
        if _client is not None:
            try:
                _client.close()
            except Exception:
                pass
        _client = None


def _cache_path(url: str, params: dict) -> Path:
    root = Path(os.environ.get("SUPERMAP_DATA_DIR") or
                Path(__file__).resolve().parents[2] / "data")
    key = json.dumps([url, sorted((str(k), str(v)) for k, v in params.items())],
                     separators=(",", ":"))
    digest = hashlib.sha256(key.encode()).hexdigest()[:40]
    return root / "cache" / "open_meteo" / f"{digest}.json"


def _cache_get(path: Path, ttl_s: float, now: float):
    try:
        if now - path.stat().st_mtime >= ttl_s:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _cache_put(path: Path, payload) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(payload, separators=(",", ":")),
                       encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass   # the cache is an optimisation; never fail a fetch over it


def _quota_reason(resp) -> str:
    try:
        body = resp.json()
        if isinstance(body, dict):
            return str(body.get("reason") or "")
    except ValueError:
        pass
    return ""


def robust_fetch_json(url: str, params: dict, *, client=None, sleep=None,
                      attempts: int = ATTEMPTS, cache: bool = True,
                      cache_ttl_s: float = CACHE_TTL_S, clock=None):
    """GET ``url`` with ``params`` -> parsed JSON.

    * disk cache first (``cache``), keyed by url+params, TTL ``cache_ttl_s``;
    * transient failures (timeouts, resets, 5xx) retried on fresh
      connections; HTTP 4xx other than 429 raises at once with
      Open-Meteo's own ``reason``;
    * a MINUTELY 429 waits ``MINUTELY_WAIT_S`` and retries, within a
      ``MAX_TOTAL_WAIT_S`` window shared by all calls; an hourly/daily
      429 (or an exhausted window) raises immediately, quoting the
      provider's reason;
    * honours SUPERMAP_BLOCK_NETWORK=1 (raises, no retry)."""
    global _window_start
    clock = clock or time.monotonic
    path = _cache_path(url, params) if cache else None
    if path is not None:
        hit = _cache_get(path, cache_ttl_s, time.time())
        if hit is not None:
            return hit
    if os.environ.get("SUPERMAP_BLOCK_NETWORK") == "1":
        raise OpenMeteoError("egress blocked by SUPERMAP_BLOCK_NETWORK")
    do_sleep = sleep or time.sleep
    http = client or _shared_client()
    last = "no attempt made"
    attempt = 0
    deadline = clock() + CALL_BUDGET_S
    while attempt < attempts:
        attempt += 1
        try:
            resp = http.get(url, params=params)
        except Exception as exc:   # ConnectTimeout, ReadTimeout, reset...
            last = type(exc).__name__
        else:
            status = resp.status_code
            if status == 200:
                try:
                    payload = resp.json()
                except ValueError:
                    last = "non-JSON 200 body"
                else:
                    if path is not None:
                        _cache_put(path, payload)
                    return payload
            elif status == 429:
                reason = _quota_reason(resp)
                now = clock()
                with _lock:
                    if _window_start is None or \
                            now - _window_start > _QUOTA_WINDOW_RESET_S:
                        _window_start = now
                    spent = now - _window_start
                if "minutely" in reason.lower() and \
                        spent + MINUTELY_WAIT_S <= MAX_TOTAL_WAIT_S:
                    do_sleep(MINUTELY_WAIT_S)
                    deadline += MINUTELY_WAIT_S   # a quota wait is not budget spent
                    attempt -= 1   # a quota wait is not a failed attempt
                    continue
                raise OpenMeteoError(
                    f"HTTP 429: {reason[:160] or 'rate limited'} "
                    "(quota wait budget spent; cached batches are kept, "
                    "a later attempt resumes)")
            elif status in RETRY_STATUSES:
                last = f"HTTP {status}"
            else:
                reason = _quota_reason(resp)
                raise OpenMeteoError(
                    f"HTTP {status}" + (f": {reason[:160]}" if reason else ""))
        if attempt < attempts:
            delay = min(0.5 * 2 ** (attempt - 1), 4.0)
            if clock() + delay + CONNECT_TIMEOUT_S > deadline:
                break   # another try cannot finish inside the call budget
            do_sleep(delay)
    raise OpenMeteoError(
        f"Open-Meteo unreachable after {attempt} attempt(s): {last}")
