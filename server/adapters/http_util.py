"""Minimal HTTP helper over stdlib urllib (no third-party dependencies).

Kill-network switch: if env ``SUPERMAP_BLOCK_NETWORK=1`` every call
raises :class:`NetworkBlocked`, which adapters treat exactly like a real
egress failure — fetch() falls back to the labelled snapshot pack.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

USER_AGENT = "SupermAP/0.1 (supply-chain research tool; stdlib urllib)"
DEFAULT_TIMEOUT_S = 30
MAX_RESPONSE_BYTES = 5 * 1024 * 1024  # 5 MiB


class NetworkBlocked(Exception):
    pass


class ResponseTooLarge(Exception):
    """A response exceeded MAX_RESPONSE_BYTES; aborted, never served."""


def _read_capped(stream) -> bytes:
    body = stream.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise ResponseTooLarge(
            f"response exceeds the {MAX_RESPONSE_BYTES}-byte cap; aborted")
    return body


def network_blocked() -> bool:
    return os.environ.get("SUPERMAP_BLOCK_NETWORK") == "1"


def http_get(url: str, *, headers: dict | None = None, timeout: int = DEFAULT_TIMEOUT_S):
    """GET -> (status:int, body:bytes, headers:dict). Raises on transport
    errors; an HTTP >= 400 answer is RETURNED with its real status (urllib's
    HTTPError is unwrapped), so callers must check the status."""
    if network_blocked():
        raise NetworkBlocked(f"egress blocked by SUPERMAP_BLOCK_NETWORK for {url}")
    req = urllib.request.Request(url, method="GET")
    req.add_header("User-Agent", USER_AGENT)
    req.add_header("Accept", "application/json, application/xml, text/xml, */*")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, _read_capped(resp), dict(resp.headers.items())
    except urllib.error.HTTPError as e:  # has a real status + body
        return e.code, _read_capped(e), dict(e.headers.items() if e.headers else [])


def http_post_form(url: str, fields: dict, *, headers: dict | None = None,
                   timeout: int = DEFAULT_TIMEOUT_S):
    """POST ``application/x-www-form-urlencoded`` -> (status, body, headers).

    Used for OAuth2 client-credentials token requests. The form body
    (which carries the client secret) is never logged or put in a URL;
    kill-network (``SUPERMAP_BLOCK_NETWORK=1``) applies exactly as for
    ``http_get``."""
    if network_blocked():
        raise NetworkBlocked(f"egress blocked by SUPERMAP_BLOCK_NETWORK for {url}")
    import urllib.parse as _urlparse
    data = _urlparse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("User-Agent", USER_AGENT)
    req.add_header("Accept", "application/json")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, _read_capped(resp), dict(resp.headers.items())
    except urllib.error.HTTPError as e:
        return e.code, _read_capped(e), dict(e.headers.items() if e.headers else [])


def http_get_json(url: str, **kw):
    status, body, headers = http_get(url, **kw)
    return status, json.loads(body.decode("utf-8", "replace")), headers


# ---------------------------------------------------------------------------
# Bounded retry with exponential backoff + jitter.
#
# Rate-limit citizenship for polite pollers (GDELT, OpenSky): a 429/5xx or a
# dropped connection is retried a bounded number of times with growing
# delays instead of being treated as an instant failure — and instead of
# hammering. ``Retry-After`` (seconds or HTTP-date) is honoured when the
# server sends it. Jitter comes from a caller-supplied ``random.Random``
# (a seeded Random makes delay sequences deterministic).
# ``SUPERMAP_BLOCK_NETWORK=1`` (NetworkBlocked) is NEVER retried: the
# kill-network switch must degrade instantly and cleanly.
# ---------------------------------------------------------------------------
import random as _random
import time as _time
from dataclasses import dataclass, field as _field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

RETRYABLE_STATUSES = (429, 500, 502, 503, 504)


class RetryExhausted(Exception):
    """Every attempt raised a transport exception (no HTTP response)."""

    def __init__(self, attempts: int, delays: list, last_error: BaseException):
        super().__init__(
            f"transport failed after {attempts} attempt(s): {last_error}")
        self.attempts = attempts
        self.delays = delays
        self.last_error = last_error


@dataclass
class RetryOutcome:
    status: int
    body: bytes
    headers: dict
    attempts: int = 1
    delays: list = _field(default_factory=list)


def parse_retry_after(headers: dict | None) -> float | None:
    """Retry-After header -> seconds (delta or HTTP-date), else None."""
    if not headers:
        return None
    raw = None
    for k, v in headers.items():
        if k.lower() == "retry-after":
            raw = v
            break
    if raw is None:
        return None
    raw = str(raw).strip()
    if raw.isdigit():
        return float(raw)
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())


def http_get_with_retries(
    url: str,
    *,
    transport=None,
    max_attempts: int = 3,
    base_delay_s: float = 1.0,
    max_delay_s: float = 60.0,
    jitter_s: float = 1.0,
    rng: "_random.Random | None" = None,
    sleep=None,
    retry_if=None,
    **transport_kw,
) -> RetryOutcome:
    """GET with bounded retries. Returns the final HTTP response (even a
    retryable status, once attempts are exhausted — the caller words the
    error). Raises RetryExhausted only when every attempt raised a
    transport exception, and re-raises NetworkBlocked immediately."""
    get = transport or http_get
    do_sleep = sleep or _time.sleep
    # the default jitter RNG is entropy-seeded per process
    # A caller may inject a seeded Random when it needs a
    # deterministic delay sequence.
    rand = rng if rng is not None else _random.Random()
    should_retry = retry_if or (
        lambda status, body, headers: status in RETRYABLE_STATUSES)
    delays: list[float] = []
    last_error: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            status, body, headers = get(url, **transport_kw)
        except NetworkBlocked:
            raise
        except Exception as exc:  # URLError, RemoteDisconnected, timeouts…
            last_error = exc
            if attempt >= max_attempts:
                break
            delay = min(base_delay_s * (2 ** (attempt - 1)), max_delay_s)
            delay += rand.uniform(0, jitter_s) if jitter_s else 0.0
            delays.append(delay)
            do_sleep(delay)
            continue
        if attempt < max_attempts and should_retry(status, body, headers):
            retry_after = parse_retry_after(headers)
            if retry_after is not None:
                delay = min(retry_after, max_delay_s)
            else:
                delay = min(base_delay_s * (2 ** (attempt - 1)), max_delay_s)
            delay += rand.uniform(0, jitter_s) if jitter_s else 0.0
            delays.append(delay)
            do_sleep(delay)
            continue
        return RetryOutcome(status=status, body=body, headers=headers,
                            attempts=attempt, delays=delays)
    raise RetryExhausted(max_attempts, delays, last_error)  # type: ignore[arg-type]
