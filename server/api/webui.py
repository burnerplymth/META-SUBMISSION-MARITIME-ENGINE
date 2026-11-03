"""Single-origin web UI: serve the built web app (web/app/dist) beside the API.

Why a wrapper and not a mount inside ``create_app``: the API already owns
``GET /`` (service info) and the rule "``offload_blocking_routes`` and
``install_security`` are the last two statements of ``create_app``" stays
untouched when the UI is layered on from outside. ``WebUI`` is a pure ASGI
wrapper used by ``scripts/serve_api.py --web`` (and so by ``run.sh`` and the
Dockerfile). Everything it does not recognise is passed
through to the API unchanged, so the API's own security middleware (CORS,
rate limits, body cap) and error shapes still apply to every API request.

Behaviour
  * ``GET``/``HEAD /`` and ``/index.html`` return the app shell. A client that
    sends ``Accept: application/json`` (and not ``text/html``) on ``/`` still
    gets the API service-info document.
  * ``GET``/``HEAD`` of a path that names a real file under the dist folder
    returns that file. Nothing else is answered here: an unknown path, an API
    prefix, or any other method goes to the API, so an unserved route is a 404
    JSON error and never ``index.html`` (the UI reads a 2xx non-JSON answer
    as NOT SERVED and a SPA fallback would hide that).
  * Files are read from inside the dist folder only (no ``..``, no dotfiles,
    no symlink escape).
  * ``assets/*`` carry content hashes: ``Cache-Control: immutable``. The shell
    is ``no-cache``. Text assets are gzipped once and cached in memory.
  * Static responses carry a Content-Security-Policy and the usual hardening
    headers. The policy allows exactly what the app loads: its own files, the
    map basemap tile/style hosts (CARTO, EOX Sentinel-2 cloudless 2016,
    NASA GIBS, MapLibre demo glyphs), blob workers for the map, data: images. No inline script, no
    ``eval``. Override the whole policy with ``SUPERMAP_CSP`` (empty string =
    send none).
"""
from __future__ import annotations

import gzip
import mimetypes
import os
import threading
from pathlib import Path

from starlette.responses import Response

_GZIP_TYPES = ("text/", "application/javascript", "application/json",
               "image/svg+xml", "application/xml", "text/javascript")
_GZIP_MIN = 1024
_PREWARM_MAX = 2 * 1024 * 1024

DEFAULT_CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self' blob:",
    "worker-src 'self' blob:",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob: https://*.basemaps.cartocdn.com "
    "https://basemaps.cartocdn.com https://tiles.maps.eox.at "
    "https://gibs.earthdata.nasa.gov",
    "font-src 'self' data: https://demotiles.maplibre.org",
    "connect-src 'self' blob: data: https://*.basemaps.cartocdn.com "
    "https://basemaps.cartocdn.com https://tiles.maps.eox.at "
    "https://gibs.earthdata.nasa.gov https://demotiles.maplibre.org",
    "object-src 'none'",
    "base-uri 'self'",
    "frame-ancestors 'self'",
    "form-action 'self'",
])

# Browser features the app does not use are denied outright; the microphone is
# the app's own (voice input records in the page), nobody else's.
PERMISSIONS_POLICY = ("camera=(), geolocation=(), microphone=(self), payment=(), usb=(), "
                      "serial=(), bluetooth=(), accelerometer=(), gyroscope=(), magnetometer=()")
HSTS = "max-age=15552000"          # 180 days, no includeSubDomains/preload


def hsts_enabled() -> bool:
    """SUPERMAP_HSTS=1 on a deploy that is only ever reached over https (Fly
    with force_https). Browsers ignore the header over plain http."""
    return os.environ.get("SUPERMAP_HSTS", "").strip().lower() in ("1", "true", "yes")


mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("application/javascript", ".mjs")
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("application/manifest+json", ".webmanifest")


def _csp() -> str:
    raw = os.environ.get("SUPERMAP_CSP")
    return DEFAULT_CSP if raw is None else raw.strip()


class WebUI:
    """ASGI wrapper: static files for the built UI, everything else -> ``app``."""

    def __init__(self, app, dist_dir: str | Path):
        self.app = app
        self.root = Path(dist_dir).resolve()
        if not (self.root / "index.html").is_file():
            raise FileNotFoundError(
                f"{self.root}/index.html not found: build the web app first "
                "(cd web/app && npm ci && npm run build)")
        self.csp = _csp()
        self._gz: dict[tuple[str, float], bytes] = {}
        if os.environ.get("SUPERMAP_WEB_PREWARM", "1") != "0":
            threading.Thread(target=self._prewarm, name="webui-prewarm", daemon=True).start()

    # ------------------------------------------------------------ helpers
    def _gzipped(self, path: Path, mtime: float) -> bytes:
        """The gzip body of a static file, computed once per (path, mtime); a cached copy is served without
        reading the file again."""
        key = (str(path), mtime)
        gz = self._gz.get(key)
        if gz is None:
            gz = self._gz[key] = gzip.compress(path.read_bytes(), compresslevel=6, mtime=0)
        return gz

    def _prewarm(self) -> None:
        """Compress the text assets once, in the background, so the first visitor does not wait for the 1 MB
        entry chunk to be compressed on the request path (about 60 ms idle, seconds on a busy small host). The
        bundled offline network copy (graph-*.js, several MB, only read when the API cannot serve it) is left to
        the first request."""
        try:
            for p in sorted(self.root.rglob("*")):
                if not p.is_file() or p.stat().st_size < _GZIP_MIN or p.stat().st_size > _PREWARM_MAX:
                    continue
                ctype = mimetypes.guess_type(p.name)[0] or ""
                if ctype.startswith(_GZIP_TYPES):
                    self._gzipped(p, p.stat().st_mtime)
        except Exception:
            pass    # best effort: a miss is just compressed on first use

    def _resolve(self, path: str) -> Path | None:
        rel = path.lstrip("/")
        if not rel or "\x00" in rel or "\\" in rel:
            return None
        parts = rel.split("/")
        if any(p in ("", ".", "..") or p.startswith(".") for p in parts):
            return None
        target = (self.root / rel).resolve()
        try:
            target.relative_to(self.root)
        except ValueError:
            return None
        return target if target.is_file() else None

    def _headers(self, path: Path, *, shell: bool) -> dict[str, str]:
        h = {
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "X-Frame-Options": "SAMEORIGIN",
            "Cross-Origin-Opener-Policy": "same-origin",
            "Cross-Origin-Resource-Policy": "same-origin",
            "Permissions-Policy": PERMISSIONS_POLICY,
            "Cache-Control": "no-cache" if shell else (
                "public, max-age=31536000, immutable"
                if "assets" in path.relative_to(self.root).parts[:1]
                else "public, max-age=3600"),
        }
        if self.csp:
            h["Content-Security-Policy"] = self.csp
        if hsts_enabled():
            h["Strict-Transport-Security"] = HSTS
        return h

    def _file_response(self, path: Path, scope, *, shell: bool) -> Response:
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        headers = self._headers(path, shell=shell)
        headers["Vary"] = "Accept-Encoding"
        st = path.stat()
        etag = f'"{st.st_size:x}-{int(st.st_mtime):x}"'
        headers["ETag"] = etag
        want_gz = (st.st_size >= _GZIP_MIN and ctype.startswith(_GZIP_TYPES)
                   and b"gzip" in _header(scope, b"accept-encoding"))
        if _header(scope, b"if-none-match").decode("latin-1") == etag:
            return Response(status_code=304, headers=headers)
        if want_gz:
            body = self._gzipped(path, st.st_mtime)
            headers["Content-Encoding"] = "gzip"
        else:
            body = path.read_bytes()
        if scope["method"] == "HEAD":
            headers["Content-Length"] = str(len(body))
            return Response(status_code=200, headers=headers,
                            media_type=ctype)
        return Response(content=body, media_type=ctype, headers=headers)

    # --------------------------------------------------------------- ASGI
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in ("GET", "HEAD"):
            return await self.app(scope, receive, send)
        path = scope["path"]
        if path in ("/", "/index.html"):
            accept = _header(scope, b"accept").decode("latin-1").lower()
            if path == "/" and "application/json" in accept \
                    and "text/html" not in accept:
                return await self.app(scope, receive, send)
            target, shell = self.root / "index.html", True
        else:
            target, shell = self._resolve(path), False
            if target is None:
                return await self.app(scope, receive, send)
        # File reads and gzip run off the event loop.
        from anyio import to_thread
        response = await to_thread.run_sync(
            lambda: self._file_response(target, scope, shell=shell))
        await response(scope, receive, send)



def _header(scope, name: bytes) -> bytes:
    for k, v in scope.get("headers") or ():
        if k == name:
            return v
    return b""
