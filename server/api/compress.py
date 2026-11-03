"""Response compression and validators for the served API.

Large JSON answers (``/ports``, ``/events``, ``/portwatch``, ``/scorecard``, ``/desks/coverage``) are the
first-screen cost on a slow link, so this wrapper compresses them. ``Starlette.GZipMiddleware`` is deliberately not used (see the note in
``create_app``: it buffers streams and rewrites headers on every response). This wrapper is narrower:

* it acts on GET responses with status 200, a text or JSON content type, a ``Content-Length`` of at least
  ``MIN_BYTES`` and at most ``MAX_BYTES`` (the whole body is held once, never a stream), and no ``Content-Encoding``,
  ``ETag`` or ``Cache-Control: no-store`` of its own. Everything else (server-sent events, ``/live/stream``,
  downloads, images, archives, already-gzipped bodies, 304s, errors) passes through byte for byte;
* the decoded body is the application's, unchanged: only ``Content-Encoding: gzip``, ``Content-Length`` and
  ``Vary: Accept-Encoding`` differ, so no stored hash changes;
* a weak ``ETag`` (SHA-256 prefix of the decoded body) lets a repeat request answer ``304`` with no body when the
  bytes are unchanged. The browser still revalidates on every request, so nothing is ever served stale;
* compression of a large body runs on a worker thread, never on the event loop.

Used from ``scripts/serve_api.py`` around the (optional) web UI wrapper, outside ``create_app``: the security
middleware stays the last statement there and every API request still passes through it first.
"""
from __future__ import annotations

import hashlib
import os
import zlib

from anyio import to_thread

MIN_BYTES = 1024
MAX_BYTES = 24 * 1024 * 1024
OFFLOAD_BYTES = 48 * 1024            # compress larger bodies off the event loop
_TYPES = ("application/json", "application/javascript", "text/", "application/xml", "image/svg+xml",
          "application/x-ndjson", "application/geo+json")
_NEVER = ("text/event-stream",)


def _level() -> int:
    try:
        return min(9, max(1, int(os.environ.get("SUPERMAP_GZIP_LEVEL", "5"))))
    except ValueError:
        return 5


def accepts_gzip(value: str) -> bool:
    """An explicit ``gzip`` token decides by its q-value (q=0 refuses); ``*`` counts when gzip is not named."""
    star = None
    for part in value.lower().split(","):
        token, _, params = part.strip().partition(";")
        token = token.strip()
        q = 1.0
        for p in params.split(";"):
            k, _, v = p.strip().partition("=")
            if k.strip() == "q":
                try:
                    q = float(v)
                except ValueError:
                    q = 0.0
        if token == "gzip":
            return q > 0
        if token == "*":
            star = q > 0
    return bool(star)


def _gzip(body: bytes, level: int) -> bytes:
    c = zlib.compressobj(level, zlib.DEFLATED, 31)
    return c.compress(body) + c.flush()


def _etag(body: bytes) -> str:
    return 'W/"' + hashlib.sha256(body).hexdigest()[:20] + '"'


def _matches(if_none_match: str, etag: str) -> bool:
    if not if_none_match:
        return False
    if if_none_match.strip() == "*":
        return True
    weak = etag[2:] if etag.startswith("W/") else etag
    for cand in if_none_match.split(","):
        c = cand.strip()
        if (c[2:] if c.startswith("W/") else c) == weak:
            return True
    return False


class Compress:
    """Pure ASGI wrapper; see the module docstring."""

    def __init__(self, app, *, min_bytes: int = MIN_BYTES, max_bytes: int = MAX_BYTES):
        self.app = app
        self.min_bytes = min_bytes
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "GET":
            return await self.app(scope, receive, send)
        req = {k: v for k, v in scope.get("headers") or ()}
        want_gzip = accepts_gzip(req.get(b"accept-encoding", b"").decode("latin-1"))
        inm = req.get(b"if-none-match", b"").decode("latin-1")
        if b"range" in req:
            return await self.app(scope, receive, send)
        level = _level()
        st: dict = {"mode": None, "start": None, "chunks": [], "size": 0}

        async def out(message):
            kind = message["type"]
            if kind == "http.response.start":
                headers = [(k.lower(), v) for k, v in message.get("headers") or ()]
                h = dict(headers)
                ctype = h.get(b"content-type", b"").decode("latin-1").lower()
                clen = h.get(b"content-length", b"").decode("latin-1")
                ok = (message["status"] == 200 and clen.isdigit()
                      and self.min_bytes <= int(clen) <= self.max_bytes
                      and ctype.startswith(_TYPES) and not ctype.startswith(_NEVER)
                      and b"content-encoding" not in h and b"etag" not in h
                      and b"no-store" not in h.get(b"cache-control", b"").lower())
                if not ok:
                    st["mode"] = "pass"
                    return await send(message)
                st["mode"], st["start"], st["headers"] = "hold", message, headers
                return None
            if st["mode"] != "hold":
                return await send(message)
            if kind != "http.response.body":      # pathsend, trailers, ...: not ours to buffer, send untouched
                await self._flush_plain(st, send)
                return await send(message)
            chunk = message.get("body", b"")
            st["chunks"].append(chunk)
            st["size"] += len(chunk)
            if message.get("more_body"):
                if st["size"] > self.max_bytes:   # the length header lied: stop holding, send what we have as is
                    return await self._flush_plain(st, send)
                return None
            return await self._finish(st, send, want_gzip, inm, level)

        await self.app(scope, receive, out)

    @staticmethod
    async def _flush_plain(st, send) -> None:
        await send(st["start"])
        for c in st["chunks"]:
            await send({"type": "http.response.body", "body": c, "more_body": True})
        st["chunks"] = []
        st["mode"] = "pass"

    async def _finish(self, st, send, want_gzip: bool, inm: str, level: int) -> None:
        body = b"".join(st["chunks"])
        headers = [(k, v) for k, v in st["headers"] if k not in (b"content-length",)]
        etag = _etag(body)
        vary = next((v for k, v in headers if k == b"vary"), b"")
        if b"accept-encoding" not in vary.lower():
            headers = [(k, v) for k, v in headers if k != b"vary"]
            headers.append((b"vary", (vary + b", Accept-Encoding") if vary else b"Accept-Encoding"))
        headers.append((b"etag", etag.encode("latin-1")))
        if _matches(inm, etag):
            drop = (b"content-type", b"content-encoding", b"content-length")
            await send({"type": "http.response.start", "status": 304, "headers": [(k, v) for k, v in headers if k not in drop]})
            await send({"type": "http.response.body", "body": b""})
            return
        if want_gzip:
            gz = await to_thread.run_sync(_gzip, body, level) if len(body) >= OFFLOAD_BYTES else _gzip(body, level)
            if len(gz) < len(body):
                body = gz
                headers.append((b"content-encoding", b"gzip"))
        headers.append((b"content-length", str(len(body)).encode("ascii")))
        start = dict(st["start"])
        start["headers"] = headers
        await send(start)
        await send({"type": "http.response.body", "body": body})
