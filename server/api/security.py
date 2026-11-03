"""API hardening middleware: rate limits + body cap.

Installed once, at the end of ``create_app``, by ``install_security``.
Pure ASGI (no BaseHTTPMiddleware: no extra task per request, no body
re-streaming surprises) and placed just inside the CORS middleware, so
a 429/413 still carries CORS headers and the ``X-Request-Id`` the outer
request-context middleware adds.

RATE LIMITS. The bucket key is never the first ``X-Forwarded-For`` hop
copied straight from the request header (rotating that header would give
every request a fresh bucket).

* the client key is the ASGI peer address (``scope["client"]``). Behind
  the production loopback proxy, ``scripts/serve_api.py`` runs uvicorn with
  ``proxy_headers=True, forwarded_allow_ips=127.0.0.1``, which already
  rewrites that peer to the real client; the app never reads
  ``X-Forwarded-For`` by itself;
* ``X-Forwarded-For`` is honoured ONLY if the direct peer is listed in
  ``SUPERMAP_TRUSTED_PROXIES`` (comma separated IPs / CIDRs; ``*`` =
  the app is reachable solely through one proxy). Default: empty, i.e.
  never trusted. Even then the hop used is the right-most one that is not
  itself a trusted proxy (the entry our own proxy appended), never the
  left-most one the client controls;
* IPv6 clients are keyed by their /64 so a host cannot rotate addresses
  inside its own prefix;
* every mutating request (POST/PUT/PATCH/DELETE) is limited per client,
  with tighter buckets for the expensive groups below. GET routes stay
  open apart from ``/api/v1`` (its own limiter, with the same key).

Groups (burst, refill/s; env ``SUPERMAP_RL_<GROUP>_BURST`` /
``SUPERMAP_RL_<GROUP>_REFILL``; ``SUPERMAP_RATE_LIMIT=off`` disables):

    sim_run   POST /sim/run               20   0.2   (each new run is a
              POST /scenarios/.../run              minutes-long build)
    llm       POST /llm/*                 30   0.5   (each call costs money)
    voice     POST /voice/*               10   0.2   (audio upload, paid ASR)
    compute   POST /run /fork /ensemble*  60   2     (engine work)
              /desks/*
    montecarlo POST/DELETE /montecarlo*    6   0.05  (each POST queues a
                                                      minutes-long simulation)
    agent     POST /agent/*               30   0.5   (each message can start a
                                                      model-driven tool loop)
    brief     POST /brief*                 6   0.05  (one model call and one
                                                      PDF render per brief)
    research  POST /research/*, GET       30   0.5   (assemble/analyze CPU work,
              /research/export                       scene scans can spend)
    exposure  POST /exposure/*            12   0.2   (up to 60 lane plans per
                                                      shipment list)
    export_bundle POST /exports/bundle    12   0.1   (up to 24 files, Chromium work)
    report    POST /reports/build|pdf,    12   0.1   (HTML/PDF render, zip pack,
              /exports/pack|chart,                   chart PNG, XLSX build)
              /replay/export/xlsx
    lanes     POST /lanes/plan,           30   1     (lane planner, memoised;
              /shocks/check                          one plan is engine work)
    portsim   POST /ports/{id}/sim         8   0.05  (a port sim build, minutes
                                                      when cold)
    history   POST /history/fork,         10   0.1   (provider reads, forks that
              /realism                               can create chain runs)
    crit      POST /desks/crit/run         4   0.02  (whole-graph criticality
                                                      sweep; also cool-down)
    brief_export GET /brief/{id}/export   12   0.1   (?format=pdf is a Chromium
                                                      render)
    satwatch  POST /satwatch/*            12   0.2   (STAC reads, imagery fetches,
                                                      detector runs)
    brief_export also GET /agent/sessions/{id}/research-report/export (PDF)
    alerts    mutations of /alerts*,      60   1
              /watchlists*
    write     any other mutation         120   2

The report, lanes, portsim, history, crit and brief_export groups have their
own buckets because one request can be minutes of CPU or a Chromium
render. These routes do not hold the app-wide mutation lock (offload.py).
The agent's in-process calls share one peer for every visitor; in these
groups that peer gets its own bucket INTERNAL_SCALE times larger, so a visitor's
tight bucket is never the agent's bottleneck and the agents of many visitors do
not starve each other on a single tight bucket.
    read      GET/HEAD except /health  1200  80   (CPU fairness only; the
              and /api/v1                          agent's in-process calls
                                                   are exempt)

BODY CAP. The header alone is not trusted (a chunked upload has none, a
bogus one could lie): mutating requests are read up to the cap before
the app sees them; one byte over is a 413 in the standard error shape.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
from typing import Iterable

try:
    from .public_api import TokenBucketLimiter
except ImportError:  # imported as a top-level module in some harnesses
    from public_api import TokenBucketLimiter  # type: ignore

MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# The in-process API client of agent mode (server/agent/apicall.py). Its reads
# are exempt from the read bucket (every visitor's agent shares this one peer);
# its writes keep their buckets.
INTERNAL_PEER = "agent-internal"
# In the groups listed in _AGENT_SCALED the internal peer draws on a bucket this many times larger.
INTERNAL_SCALE = 4
_AGENT_SCALED = frozenset({"report", "lanes", "portsim", "history"})

# Routes whose request body may legitimately exceed the global cap. Voice
# input uploads a recording (Meta's WAV limit is 32 MB; a few KB of slack
# for the container). The route validates the exact audio limits itself.
#
# The web app sends 16 kHz mono 16-bit WAV, at most 10 minutes =
# 19,200,044 bytes (web/app/src/voice/wav.ts), and compressed browser formats are far smaller, so the cap is
# 20 MiB + 4 KiB. A declared Content-Length over the cap is refused before any byte is read, and the guard keeps one
# copy of an accepted body (not three).
VOICE_BODY_MAX = 20 * 1024 * 1024 + 4096
PATH_BODY_LIMITS: dict[str, int] = {"/voice/transcribe": VOICE_BODY_MAX,
                                    # a shipment list of up to 500 rows (server/exposure/model.py)
                                    "/exposure/evaluate": 384 * 1024, "/exposure/export": 384 * 1024,
                                    "/exposure/resolve": 192 * 1024}


def body_limit_for(path: str, default: int) -> int:
    """The mutating-body cap for ``path`` (``default`` unless listed above)."""
    return PATH_BODY_LIMITS.get(path.rstrip("/") or "/", default)


# group -> (methods, path regex, default burst, default refill/s)
_GROUPS: list[tuple[str, frozenset, "re.Pattern", int, float]] = [
    ("sim_run", frozenset({"POST"}),
     re.compile(r"^/(sim/run(/[^/]+/(fork|verify))?|scenarios/run|scenarios/[^/]+/run)/?$"), 20, 0.2),
    ("llm", frozenset({"POST"}), re.compile(r"^/llm(/|$)"), 30, 0.5),
    ("voice", frozenset({"POST"}), re.compile(r"^/voice(/|$)"), 10, 0.2),
    # The whole-graph criticality sweep is the one /desks/* route that is minutes of CPU: tighter than compute,
    # and listed before it (first match wins).
    ("crit", frozenset({"POST"}), re.compile(r"^/desks/crit/run/?$"), 4, 0.02),
    ("compute", frozenset({"POST"}), re.compile(
        r"^/(run|fork|ensemble|ensemble-stats|desks)(/|$)"), 60, 2.0),
    # Monte Carlo (input-uncertainty simulation): each POST queues minutes of
    # CPU. Tight bucket; the service itself also caps running jobs per client
    # and globally. GETs of /montecarlo use the read bucket.
    ("montecarlo", frozenset({"POST", "DELETE"}), re.compile(r"^/montecarlo(/|$)"), 6, 0.05),
    ("agent", frozenset({"POST"}), re.compile(r"^/agent(/|$)"), 30, 0.5),
    # A brief can make one paid model call and one Chromium render.
    ("brief", frozenset({"POST"}), re.compile(r"^/brief(/|$)"), 6, 0.05),
    # Research workbench: assemble/analyze are CPU work, scene scans can spend.
    ("research", frozenset({"POST", "GET"}),
     re.compile(r"^/research/(assemble|analyze|scenes/scan|export)/?$"), 30, 0.5),
    ("exposure", frozenset({"POST"}), re.compile(r"^/exposure(/|$)"), 12, 0.2),
    # One request can render up to 24 files, PNGs and PDFs through Chromium.
    ("export_bundle", frozenset({"POST"}), re.compile(r"^/exports/bundle/?$"), 12, 0.1),
    # Report / export builders: HTML + Chromium PDF, zip pack, chart PNG, XLSX.
    ("report", frozenset({"POST"}), re.compile(
        r"^/(reports/(build|pdf)|exports/(pack|chart)|replay/export/xlsx)/?$"), 12, 0.1),
    ("lanes", frozenset({"POST"}), re.compile(r"^/(lanes/plan|shocks/check)/?$"), 30, 1.0),
    ("portsim", frozenset({"POST"}), re.compile(r"^/ports/[^/]+/sim/?$"), 8, 0.05),
    ("history", frozenset({"POST"}), re.compile(r"^/(history/fork|realism)/?$"), 10, 0.1),
    # GET /brief/{id}/export?format=pdf is a Chromium render; the query string is not part of the match.
    ("brief_export", frozenset({"GET"}), re.compile(r"^/brief/[^/]+/export/?$"), 12, 0.1),
    # Satellite desk POSTs (scan, S1 scan, SAM / aerial / vision runs, NAIP and Landsat crops, signal mint): STAC
    # reads, imagery fetches and detector runs. Admin-gated when they spend or fetch, but one client must not
    # queue them back to back in the shared write bucket.
    ("satwatch", frozenset({"POST"}), re.compile(r"^/satwatch(/|$)"), 12, 0.2),
    # The research note's PDF export is a Chromium render too (reports.render_gate bounds the renders).
    ("brief_export", frozenset({"GET"}), re.compile(r"^/agent/sessions/[^/]+/research-report/export/?$"), 12, 0.1),
    ("alerts", MUTATING, re.compile(r"^/(alerts|watchlists)(/|$)"), 60, 1.0),
    # The unlock link takes a secret in its query string: throttle guessing.
    ("demo_unlock", frozenset({"GET"}), re.compile(r"^/demo/unlock/?$"), 10, 0.1),
    # Reads: generous, so a person never meets it,
    # but a loop on /graph, /chain, /sim/... cannot take the one shared CPU.
    # /health (the platform check) and /api/v1 (its own limiter) are exempt.
    ("read", frozenset({"GET", "HEAD"}),
     re.compile(r"^/(?!health/?$|api/v1(/|$))"), 1200, 80.0),
]
_DEFAULT_GROUP = ("write", 120, 2.0)


# --------------------------------------------------------------- client key

class TrustedProxies:
    """Parsed ``SUPERMAP_TRUSTED_PROXIES``."""

    def __init__(self, spec: str | Iterable[str] | None = None):
        if spec is None:
            spec = os.environ.get("SUPERMAP_TRUSTED_PROXIES", "")
        items = spec.split(",") if isinstance(spec, str) else list(spec)
        self.any = False
        self.networks: list = []
        self.literals: set[str] = set()
        for raw in items:
            item = str(raw).strip()
            if not item:
                continue
            if item == "*":
                self.any = True
                continue
            try:
                self.networks.append(ipaddress.ip_network(item, strict=False))
            except ValueError:
                self.literals.add(item)  # e.g. a client's host name

    def __bool__(self) -> bool:
        return self.any or bool(self.networks) or bool(self.literals)

    def trusts(self, addr: str | None) -> bool:
        if not addr:
            return False
        if self.any or addr in self.literals:
            return True
        ip = _parse_ip(addr)
        return ip is not None and any(ip in net for net in self.networks
                                      if net.version == ip.version)


def _parse_ip(addr: str):
    try:
        ip = ipaddress.ip_address(addr.strip().strip("[]"))
    except ValueError:
        return None
    if ip.version == 6 and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def normalise_client(addr: str | None) -> str:
    """Bucket identity of an address: IPv6 collapses to its /64."""
    if not addr:
        return "unknown"
    ip = _parse_ip(addr)
    if ip is None:
        return addr.strip()[:64] or "unknown"
    if ip.version == 6:
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return str(ip)


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def client_header_name() -> str:
    """Lower-case name of the single-valued header the platform proxy sets to
    the client IP. ``SUPERMAP_CLIENT_IP_HEADER`` names it; unset, it defaults to
    ``fly-client-ip`` when ``FLY_APP_NAME`` is set (the process runs on Fly),
    else "" (no such header is read)."""
    explicit = os.environ.get("SUPERMAP_CLIENT_IP_HEADER", "").strip().lower()
    if explicit:
        return explicit
    return "fly-client-ip" if os.environ.get("FLY_APP_NAME", "").strip() else ""


def _proxy_only() -> bool:
    """The deployer says every request arrives through a platform proxy and the
    proxy's headers may be believed from any peer: ``SUPERMAP_TRUST_PROXY=1``
    (Render and similar), or a Fly machine (``FLY_APP_NAME`` set). Off by
    default; a bare host never believes a client-sent header."""
    return _flag("SUPERMAP_TRUST_PROXY") or bool(os.environ.get("FLY_APP_NAME", "").strip())


def client_ip(peer: str | None, xff: str | None,
              trusted: TrustedProxies | None = None,
              direct: str | None = None) -> str:
    """The rate-limit identity of a request.

    ``peer`` is the ASGI peer address. Headers are believed only from a peer
    in ``SUPERMAP_TRUSTED_PROXIES`` or, when the deploy declares itself
    proxy-only (``SUPERMAP_TRUST_PROXY=1`` or Fly), from any peer. Order:
    the single-valued header from ``client_header_name()`` (``direct``, must
    parse as an IP); then ``X-Forwarded-For`` (right-most hop that is not a
    trusted proxy for an explicit trusted peer; the FIRST hop for
    ``SUPERMAP_TRUST_PROXY=1``); else the peer itself."""
    listed = bool(trusted and trusted.trusts(peer))
    proxy_only = _proxy_only()
    if (listed or proxy_only) and direct and client_header_name():
        first = direct.split(",")[0].strip()
        if _parse_ip(first) is not None:
            return normalise_client(first)
    hops = [h.strip() for h in (xff or "").split(",") if h.strip()]
    if not hops:
        return normalise_client(peer)
    if listed:
        if trusted.any:
            return normalise_client(hops[-1])
        for hop in reversed(hops):
            if not trusted.trusts(hop):
                return normalise_client(hop)
        return normalise_client(hops[0])  # every hop was a trusted proxy
    if _flag("SUPERMAP_TRUST_PROXY") and _parse_ip(hops[0]) is not None:
        return normalise_client(hops[0])
    return normalise_client(peer)


def cookie_secure(request) -> bool:
    """Whether a cookie set on this response should carry ``Secure``: the
    request is https, a TRUSTED proxy says it was (``X-Forwarded-Proto``), or
    ``SUPERMAP_COOKIE_SECURE=1`` (a deploy reached only over https, where the
    app itself sees plain http from the proxy)."""
    if os.environ.get("SUPERMAP_COOKIE_SECURE", "").strip().lower() in ("1", "true", "yes"):
        return True
    if request.url.scheme == "https":
        return True
    trusted = getattr(request.app.state, "trusted_proxies", None)
    peer = request.client.host if request.client else None
    proto = request.headers.get("x-forwarded-proto", "").split(",")[-1].strip().lower()
    return bool(trusted and trusted.trusts(peer) and proto == "https")


def request_client(request, trusted: TrustedProxies | None = None) -> str:
    """Same as ``client_ip`` for a Starlette Request (used by /api/v1)."""
    peer = request.client.host if request.client else None
    if trusted is None:
        trusted = getattr(request.app.state, "trusted_proxies", None)
    name = client_header_name()
    return client_ip(peer, request.headers.get("x-forwarded-for"), trusted,
                     request.headers.get(name) if name else None)


# ------------------------------------------------------------- rate limiter

def _env_num(name: str, default, cast):
    try:
        return cast(os.environ[name])
    except (KeyError, ValueError):
        return default


class RateLimits:
    """The per-group token buckets plus the path/method classifier."""

    def __init__(self, enabled: bool | None = None):
        if enabled is None:
            enabled = os.environ.get("SUPERMAP_RATE_LIMIT", "").strip() \
                .lower() not in ("off", "0", "false", "no")
        self.enabled = enabled
        self.rules = []
        self.limiters: dict[str, TokenBucketLimiter] = {}
        self.internal_limiters: dict[str, TokenBucketLimiter] = {}
        self.params: dict[str, dict] = {}
        for name, methods, pattern, burst, refill in _GROUPS:
            self._add(name, burst, refill)
            self.rules.append((name, methods, pattern))
        self._add(*_DEFAULT_GROUP)

    def _add(self, name: str, burst: int, refill: float) -> None:
        up = name.upper()
        burst = _env_num(f"SUPERMAP_RL_{up}_BURST", burst, int)
        refill = _env_num(f"SUPERMAP_RL_{up}_REFILL", refill, float)
        self.params[name] = {"burst": burst, "refill_per_sec": refill}
        self.limiters[name] = TokenBucketLimiter(burst=burst,
                                                 refill_per_sec=refill)
        if name in _AGENT_SCALED:
            self.internal_limiters[name] = TokenBucketLimiter(
                burst=burst * INTERNAL_SCALE,
                refill_per_sec=refill * INTERNAL_SCALE)

    def group_for(self, method: str, path: str) -> str | None:
        """Limiter group of a request, or None when it is not limited."""
        for name, methods, pattern in self.rules:
            if method in methods and pattern.match(path):
                return name
        if method in MUTATING:
            return _DEFAULT_GROUP[0]
        return None

    def allow(self, group: str, client: str) -> bool:
        if client == INTERNAL_PEER and group in self.internal_limiters:
            return self.internal_limiters[group].allow(client)
        return self.limiters[group].allow(client)


def _error_body(code: str, message: str) -> bytes:
    return json.dumps({"error": {"code": code, "message": message}},
                      separators=(",", ":")).encode("utf-8")


async def _send_json(send, status: int, code: str, message: str,
                     extra_headers: list | None = None) -> None:
    body = _error_body(code, message)
    headers = [(b"content-type", b"application/json"),
               (b"content-length", str(len(body)).encode())]
    headers += extra_headers or []
    await send({"type": "http.response.start", "status": status,
                "headers": headers})
    await send({"type": "http.response.body", "body": body})


class RateLimitMiddleware:
    def __init__(self, app, limits: RateLimits, trusted: TrustedProxies):
        self.app = app
        self.limits = limits
        self.trusted = trusted

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self.limits.enabled:
            return await self.app(scope, receive, send)
        group = self.limits.group_for(scope["method"], scope["path"])
        if group is None:
            return await self.app(scope, receive, send)
        peer = scope["client"][0] if scope.get("client") else None
        if group == "read" and peer == INTERNAL_PEER:
            return await self.app(scope, receive, send)
        xff = direct = None
        want = client_header_name().encode("latin-1", "ignore")
        for k, v in scope.get("headers") or ():
            if k == b"x-forwarded-for":
                xff = v.decode("latin-1")
            elif want and k == want:
                direct = v.decode("latin-1")
        client = client_ip(peer, xff, self.trusted, direct)
        if self.limits.allow(group, client):
            return await self.app(scope, receive, send)
        p = self.limits.params[group]
        retry = max(1, int(round(1.0 / p["refill_per_sec"]))) \
            if p["refill_per_sec"] > 0 else 60
        await _send_json(
            send, 429, "rate_limited",
            f"rate limit exceeded for {scope['method']} {group} requests "
            f"from this client (burst {p['burst']}, refill "
            f"{p['refill_per_sec']}/s); retry after a short pause",
            [(b"retry-after", str(retry).encode())])


# ------------------------------------------------------ 404 versus 405

def _literal_methods(routes, prefix: str = "") -> dict:
    """{path without parameters: methods} over every route, included routers too."""
    out: dict = {}
    for r in routes:
        path = getattr(r, "path", None)
        if path is not None:
            if "{" not in path:
                key = (prefix + path).rstrip("/") or "/"
                out.setdefault(key, set()).update(getattr(r, "methods", None) or ())
        elif hasattr(r, "original_router"):
            inc = getattr(r, "include_context", None)
            for k, v in _literal_methods(r.original_router.routes,
                                         prefix + (getattr(inc, "prefix", "") or "")).items():
                out.setdefault(k, set()).update(v)
    return out


class MethodGuardMiddleware:
    """A path that exists for another method is 405 with an ``Allow`` header, not a 404 from a parameterised
    sibling (``GET /scenarios/organise`` would otherwise be read as scenario id "organise"). Only literal routes
    (no ``{param}``) are considered; the table is built on the first request, after every route is registered."""

    def __init__(self, app, fastapi_app):
        self.app = app
        self._fastapi = fastapi_app
        self._table: dict | None = None

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] in ("OPTIONS", "HEAD"):
            return await self.app(scope, receive, send)
        if self._table is None:
            self._table = _literal_methods(self._fastapi.routes)
        allowed = self._table.get((scope.get("path") or "/").rstrip("/") or "/")
        if allowed and scope["method"] not in allowed:
            allow = ", ".join(sorted(allowed))
            return await _send_json(send, 405, "method_not_allowed", "method not allowed",
                                    [(b"allow", allow.encode("latin-1"))])
        await self.app(scope, receive, send)


# ------------------------------------------------- response hardening headers

# CSP for an HTML document the API itself serves (public ledger page, API
# docs, report HTML). The built web app is served by webui.py with its own
# policy; these pages need no script, no network and no framing by others.
HTML_CSP = ("default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'self'")


# FastAPI's built-in Swagger / ReDoc pages load their scripts from a CDN, which
# the locked-down policy would break; they keep nosniff and no-referrer.
_CSP_EXEMPT = frozenset({"/docs", "/redoc", "/docs/oauth2-redirect"})


class SecurityHeadersMiddleware:
    """Add ``X-Content-Type-Options: nosniff`` and ``Referrer-Policy:
    no-referrer`` to every response (never overriding one a route set), and
    a locked-down CSP + ``X-Frame-Options: SAMEORIGIN`` to an HTML document.
    Outermost of the security middleware, so a 403/413/429 carries them."""

    def __init__(self, app):
        self.app = app
        # SUPERMAP_HSTS=1: only for a deploy reached solely over https.
        self.hsts = b"max-age=15552000" if os.environ.get(
            "SUPERMAP_HSTS", "").strip().lower() in ("1", "true", "yes") else b""

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_wrapped(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                have = {k.lower() for k, _ in headers}
                ctype = next((v for k, v in headers if k.lower() == b"content-type"), b"")
                add = [(b"x-content-type-options", b"nosniff"),
                       (b"referrer-policy", b"no-referrer")]
                if self.hsts:
                    add.append((b"strict-transport-security", self.hsts))
                if ctype.lower().startswith(b"text/html") and scope.get("path") not in _CSP_EXEMPT:
                    add += [(b"content-security-policy", HTML_CSP.encode()),
                            (b"x-frame-options", b"SAMEORIGIN")]
                headers += [(k, v) for k, v in add if k not in have]
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_wrapped)


# ------------------------------------------------------- cross-site writes

def cors_origins() -> list[str]:
    """The browser origins allowed to call the API (same rule app.py uses for
    CORSMiddleware), plus SUPERMAP_ALLOWED_ORIGINS for a front end hosted on
    another origin."""
    env = os.environ.get("SUPERMAP_CORS_ORIGINS")
    # Default: the Vite dev server (5173) and `vite preview` (4173), both of which
    # proxy to this API with the browser's own Origin.
    base = [o.strip() for o in env.split(",") if o.strip()] if env \
        else ["http://localhost:5173", "http://127.0.0.1:5173",
              "http://localhost:4173", "http://127.0.0.1:4173"]
    extra = [o.strip() for o in
             os.environ.get("SUPERMAP_ALLOWED_ORIGINS", "").split(",") if o.strip()]
    return base + extra


def _is_loopback_netloc(netloc: str) -> bool:
    host = netloc.rsplit("@", 1)[-1]
    if host.startswith("["):
        host = host[1:].split("]", 1)[0]
    else:
        host = host.split(":", 1)[0]
    return host in ("localhost", "127.0.0.1", "::1")


class OriginCheckMiddleware:
    """Refuse a state-changing request that a page on ANOTHER site made.

    Browsers always send ``Origin`` on a cross-site POST. A ``text/plain``
    POST is a "simple request" (no CORS preflight) and every route parses its
    body as JSON whatever the content type, so CORS alone does not stop a
    hostile page from making a visitor's browser write. A request with no
    ``Origin`` (curl, scripts, the in-process agent) is not a
    browser cross-site request and passes. Allowed: the request's own host
    (``Host``, or ``X-Forwarded-Host`` from a trusted proxy), the CORS list,
    ``SUPERMAP_ALLOWED_ORIGINS``. ``SUPERMAP_ORIGIN_CHECK=off`` disables."""

    def __init__(self, app, trusted: "TrustedProxies"):
        self.app = app
        self.trusted = trusted
        self.enabled = os.environ.get("SUPERMAP_ORIGIN_CHECK", "").strip() \
            .lower() not in ("off", "0", "false", "no")
        self.allowed = {o.rstrip("/").lower() for o in cors_origins()}

    def _permitted(self, scope, origin: str) -> bool:
        if origin.rstrip("/").lower() in self.allowed:
            return True
        from urllib.parse import urlsplit
        try:
            netloc = urlsplit(origin).netloc.lower()
        except ValueError:
            return False
        if not netloc:
            return False
        hosts = set()
        # Local development: `vite preview` on a random
        # port proxies to this API with the browser's own Origin. A loopback
        # origin is accepted only when the request itself arrived on a
        # loopback Host, never for a public deploy.
        if _is_loopback_netloc(netloc) and any(
                _is_loopback_netloc(h.decode("latin-1").strip().lower())
                for k, h in (scope.get("headers") or ()) if k == b"host"):
            return True
        peer = scope["client"][0] if scope.get("client") else None
        for k, v in scope.get("headers") or ():
            if k == b"host":
                hosts.add(v.decode("latin-1").strip().lower())
            elif k == b"x-forwarded-host" and self.trusted.trusts(peer):
                hosts.add(v.decode("latin-1").split(",")[-1].strip().lower())
        return netloc in hosts

    async def __call__(self, scope, receive, send):
        if (scope["type"] != "http" or not self.enabled
                or scope["method"] not in MUTATING):
            return await self.app(scope, receive, send)
        origin = None
        for k, v in scope.get("headers") or ():
            if k == b"origin":
                origin = v.decode("latin-1").strip()
                break
        if origin is None or self._permitted(scope, origin):
            return await self.app(scope, receive, send)
        await _send_json(send, 403, "forbidden_origin",
                         "state-changing requests from another site are refused")


# ---------------------------------------------------------------- body cap

MAX_JSON_DEPTH = 64


def json_too_deep(body: bytes, limit: int = MAX_JSON_DEPTH) -> bool:
    """True when a JSON-looking body nests arrays/objects deeper than
    ``limit``. One linear pass (strings and escapes skipped), no regex,
    so a hostile body cannot make it superlinear.

    Why at the edge: a handler may store a caller-supplied structure
    (a port-twin scenario ``source``, an alert rule, ...) and a later
    GET hands it to FastAPI's recursive ``jsonable_encoder``; at ~1000
    levels that is a RecursionError, i.e. a run that was accepted with
    201 and then answered 500 on every read, for ever. No honest body
    nests more than a handful of levels."""
    start = len(body) - len(body.lstrip())
    if start >= len(body) or body[start] not in (0x7B, 0x5B):  # { [
        return False
    depth = 0
    in_str = esc = False
    for b in body:
        if in_str:
            if esc:
                esc = False
            elif b == 0x5C:            # backslash
                esc = True
            elif b == 0x22:            # closing quote
                in_str = False
        elif b == 0x22:
            in_str = True
        elif b == 0x7B or b == 0x5B:
            depth += 1
            if depth > limit:
                return True
        elif (b == 0x7D or b == 0x5D) and depth:
            depth -= 1
    return False


class BodyLimitMiddleware:
    """Buffer mutating request bodies up to ``max_bytes`` before the app
    runs; one byte more is 413. Covers chunked uploads and false or
    missing Content-Length headers. A JSON body nested deeper than
    ``MAX_JSON_DEPTH`` is a 422 (see ``json_too_deep``)."""

    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in MUTATING:
            return await self.app(scope, receive, send)
        chunks: list[bytes] = []
        total = 0
        limit = body_limit_for(scope.get("path", ""), self.max_bytes)
        for k, v in scope.get("headers") or ():
            if k == b"content-length":
                try:
                    declared = int(v)
                except ValueError:
                    declared = 0
                if declared > limit:        # refuse before reading a single byte
                    return await _send_json(
                        send, 413, "payload_too_large",
                        f"request body exceeds the {limit}-byte "
                        "limit for mutating routes")
                break
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"") or b""
            total += len(chunk)
            if total > limit:
                return await _send_json(
                    send, 413, "payload_too_large",
                    f"request body exceeds the {limit}-byte "
                    "limit for mutating routes")
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        joined = b"".join(chunks)
        chunks.clear()          # one copy only: a 20 MiB upload is not held two or three times over
        del chunks
        if json_too_deep(joined):
            return await _send_json(
                send, 422, "invalid_request",
                f"JSON body is nested deeper than {MAX_JSON_DEPTH} levels")
        held = [joined]
        del joined

        async def replay():
            if held:
                return {"type": "http.request", "body": held.pop(),
                        "more_body": False}
            return await receive()  # disconnect detection

        await self.app(scope, replay, send)


# ---------------------------------------------------- error-message hygiene

_PATH_RE = re.compile(
    r"(?<![\w.])(?:/(?:home|root|tmp|var|usr|etc|opt|mnt|srv|app|Users|"
    r"workspace|private|data)/[^\s'\"<>:,;()\[\]{}]+)")


def scrub_paths(text) -> str:
    """Replace absolute filesystem paths in an exception text with
    ``<path>`` before it is served: error bodies must not disclose the
    server's directory layout."""
    return _PATH_RE.sub("<path>", str(text))


def public_source(source: str, repo_root=None) -> str:
    """A served form of a file-path 'source' label: repo-relative when it
    lies inside the repo, else just the file name -- never an absolute
    server path (never echoed verbatim, e.g. ``graph_source``)."""
    from pathlib import Path
    p = Path(str(source))
    if not p.is_absolute():
        return str(source)
    root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[2]
    try:
        return str(p.resolve().relative_to(root.resolve()))
    except ValueError:
        return p.name


def _install_error_hygiene(app) -> None:
    import errno
    import logging

    from fastapi.responses import JSONResponse

    log = logging.getLogger("supermap.api")

    async def _os_error(request, exc: OSError):
        # A path-shaped id that is too long for the filesystem cannot
        # name a stored object: unknown resource, not a server fault.
        if exc.errno == errno.ENAMETOOLONG:
            return JSONResponse(status_code=404, content={"error": {
                "code": "not_found", "message": "resource not found"}})
        log.error("unhandled OS error method=%s path=%s",
                  request.method, request.url.path, exc_info=exc)
        return JSONResponse(status_code=500, content={"error": {
            "code": "internal_error", "message": "internal error"}})

    app.add_exception_handler(OSError, _os_error)

    from .store import StoreFull

    async def _store_full(request, exc: StoreFull):
        return JSONResponse(status_code=507, content={"error": {
            "code": "store_full", "message": str(exc)}})

    app.add_exception_handler(StoreFull, _store_full)


# ------------------------------------------------------------------ install

def install_security(app, *, max_body_bytes: int = 64 * 1024) -> None:
    """Attach the rate limiter and body cap to ``app`` (call once, after
    the CORS middleware has been added, before the first request)."""
    from starlette.middleware import Middleware
    from starlette.middleware.cors import CORSMiddleware

    trusted = TrustedProxies()
    limits = RateLimits()
    app.state.trusted_proxies = trusted
    app.state.rate_limits = limits
    _install_error_hygiene(app)

    # Innermost of the user middleware list: just inside CORS, so
    # 429/413 responses are CORS-decorated and carry X-Request-Id.
    entries = [Middleware(OriginCheckMiddleware, trusted=trusted),
               Middleware(MethodGuardMiddleware, fastapi_app=app),
               Middleware(RateLimitMiddleware, limits=limits,
                          trusted=trusted),
               Middleware(BodyLimitMiddleware, max_bytes=max_body_bytes)]
    stack = app.user_middleware
    # The header middleware is the OUTERMOST layer (index 0): the early
    # replies of the request-context middleware (declared Content-Length over
    # the cap) are produced outside the stack below and must carry them too.
    stack.insert(0, Middleware(SecurityHeadersMiddleware))
    idx = next((i for i, m in enumerate(stack) if m.cls is CORSMiddleware),
               None)
    at = len(stack) if idx is None else idx + 1
    for offset, entry in enumerate(entries):
        stack.insert(at + offset, entry)
