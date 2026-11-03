"""Shared helpers for the money layer: provenance envelope + stdlib HTTP.

Deliberately self-contained (no import of server/adapters): the money
layer is a separate module and must stay importable on its own.
The shapes mirror the adapter framework (server/adapters/base.py) so
health/provenance read identically across layers.

Error text in this layer must never carry a full URL. Adapters
put secrets in query strings (FRED's api_key, by that API's design), so
every message built here reduces URLs to scheme+host+path via
sanitize_url(), and sanitize_text() strips query strings / redacts
secret-style parameters out of any third-party text (transport errors,
upstream error messages) before it can reach an exception, a health
note, or a report.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

MODES = ("LIVE", "SNAPSHOT", "MODELED")

# Hard response-size cap, identical to the server/adapters
# layer's 5 MB cap. A body over the cap aborts the fetch with
# AdapterError instead of being buffered whole into memory.
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
_READ_CHUNK_BYTES = 64 * 1024

_URL_RE = re.compile(r"https?://[^\s'\"<>\]\)]+")
_SECRET_PARAM_RE = re.compile(
    r"(?i)\b(api[_-]?key|apikey|access[_-]?token|auth[_-]?token|token|"
    r"client[_-]?secret|secret|password)\s*=\s*[^\s&'\",;\]]+")


def sanitize_url(url: object) -> str:
    """URL -> scheme+host+path only: no query string, no fragment.

    This is the only form of a URL that may appear in error text in
    this layer — query strings carry API keys (FRED) and search terms.
    """
    text = str(url)
    try:
        parts = urllib.parse.urlsplit(text)
    except ValueError:
        return text.split("?", 1)[0].split("#", 1)[0]
    if parts.scheme and parts.netloc:
        return urllib.parse.urlunsplit(
            (parts.scheme, parts.netloc, parts.path, "", ""))
    return text.split("?", 1)[0].split("#", 1)[0]


def sanitize_text(text: object) -> str:
    """Scrub arbitrary text for safe use in an exception / note / report:
    every URL in it is reduced to scheme+host+path, and any remaining
    secret-style `name=value` parameter has its value redacted."""
    out = _URL_RE.sub(lambda m: sanitize_url(m.group(0)), str(text))
    return _SECRET_PARAM_RE.sub(lambda m: f"{m.group(1)}=REDACTED", out)


def utcnow_iso() -> str:
    """ISO 8601 UTC, seconds precision, Z suffix."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def provenance(source: str, mode: str, retrieved_at: str | None = None) -> dict:
    if mode not in MODES:
        raise ValueError(f"invalid provenance mode: {mode!r}")
    return {
        "source": source,
        "retrieved_at": retrieved_at or utcnow_iso(),
        "mode": mode,
    }


class AdapterError(Exception):
    """A live fetch failed (network, HTTP status, unparseable payload)."""


class NeedsKeyError(AdapterError):
    """The adapter cannot run at all until a user-supplied key exists."""


class NetworkBlocked(Exception):
    """Raised when SUPERMAP_BLOCK_NETWORK=1 (the same kill switch as the adapters)."""


def network_blocked() -> bool:
    return os.environ.get("SUPERMAP_BLOCK_NETWORK") == "1"


def _read_capped(stream, url: str) -> bytes:
    """Read a response body with the 5 MB cap enforced.

    Reads at most MAX_RESPONSE_BYTES + 1 bytes: the moment the body is
    known to exceed the cap the fetch aborts with AdapterError (the
    remainder is never buffered). Works for both read paths in
    http_get — the success response and the HTTPError body.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        budget = MAX_RESPONSE_BYTES + 1 - total
        if budget <= 0:
            break
        try:
            chunk = stream.read(min(_READ_CHUNK_BYTES, budget))
        except TypeError:
            # A stream whose read() takes no size argument (some
            # addinfourl wrappers): one uncapped read, then
            # the same size verdict below.
            chunk = stream.read()
            if chunk:
                chunks.append(chunk)
                total += len(chunk)
            break
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > MAX_RESPONSE_BYTES:
            break
    if total > MAX_RESPONSE_BYTES:
        raise AdapterError(
            f"response from {sanitize_url(url)} exceeds the "
            f"{MAX_RESPONSE_BYTES} byte (5 MB) response cap; aborted")
    return b"".join(chunks)


def http_get(url: str, *, user_agent: str, headers: dict | None = None,
             timeout: int = 30) -> tuple[int, bytes, dict]:
    """GET -> (status, body, headers). HTTP >= 400 is returned, not raised,
    so callers can record the real status as evidence. Transport errors
    raise AdapterError with the exception type named. No error raised
    here ever contains the full URL: only its scheme+host+path. Bodies
    over MAX_RESPONSE_BYTES (5 MB) abort with AdapterError on
    both the success and the HTTP-error read paths."""
    if network_blocked():
        raise NetworkBlocked(
            "egress blocked by SUPERMAP_BLOCK_NETWORK for "
            f"{sanitize_url(url)}")
    req = urllib.request.Request(url, method="GET")
    req.add_header("User-Agent", user_agent)
    req.add_header("Accept-Encoding", "identity")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, _read_capped(resp, url), \
                dict(resp.headers.items())
    except urllib.error.HTTPError as e:  # real status + body
        return e.code, _read_capped(e, url), \
            dict(e.headers.items() if e.headers else [])
    except AdapterError:
        raise  # the cap verdict is already worded + sanitized
    except Exception as exc:
        # The transport exception's own text can embed the request URL
        # (urllib does this) — scrub it, and do not chain the original:
        # a printed traceback of the chain would re-expose the query.
        raise AdapterError(
            f"{type(exc).__name__}: {sanitize_text(exc)}") from None


def http_get_json(url: str, **kw) -> tuple[int, object, dict]:
    status, body, headers = http_get(url, **kw)
    try:
        return status, json.loads(body.decode("utf-8", "replace")), headers
    except json.JSONDecodeError as exc:
        raise AdapterError(
            f"non-JSON response from {sanitize_url(url)}: {exc}") from None


def health_dict(adapter: str, state: str, last_success_at: str | None,
                mode: str | None, note: str | None = None) -> dict:
    """Adapter-shaped health entry: {adapter, state, last_success_at, mode, note?}."""
    out = {"adapter": adapter, "state": state,
           "last_success_at": last_success_at, "mode": mode}
    if note:
        out["note"] = note
    return out
