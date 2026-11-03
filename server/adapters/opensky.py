"""OpenSky Network adapter.

Real API: https://opensky-network.org/api/states/all?lamin=..&lamax=..
Anonymous access parameters, verified against OpenSky's published REST
API terms: anonymous use is still offered — 400 credits/day,
10 s time resolution, current states only. ``/states/all`` credit cost is
area-based (<=25 sq deg = 1, 25-100 = 2, 100-400 = 3, global/>400 = 4).
The four watched bboxes below cost 2+1+1+1 = 5 credits per cycle, far
inside the anonymous budget. Authenticated access (4,000 credits/day) is
now via an OpenSky API client (OAuth2 client credentials; basic auth was
retired for accounts created since mid-March 2025) — no client exists
here, so authenticated-only surfaces stay NEEDS-KEY in the health note
while the anonymous states poll runs and reports its own truthful state.

OAuth2: when OPENSKY_CLIENT_ID and
OPENSKY_CLIENT_SECRET are both set the adapter requests a bearer token via
the OAuth2 client-credentials grant (TOKEN_URL), caches it in memory only
until ``TOKEN_REFRESH_MARGIN_S`` before its stated expiry, and sends it as
``Authorization: Bearer`` on every states call. The token and the client
secret are never logged, written to a snapshot, put in a URL or echoed in
a health note / error text. If the token request fails, or the API refuses
the bearer (401/403), the adapter falls back to the anonymous tier and the
health note says so ("anonymous (OAuth2 ... failed)") — it never claims
authenticated access it did not use. Authenticated budget: 4,000
credits/day (the 80% stop applies to whichever budget is in force).

Retry discipline: each bbox request gets bounded retries with
exponential backoff + jitter (http_util.http_get_with_retries);
if a bbox exhausts its attempts the whole fetch stops — the per-cycle
request budget is MAX_ATTEMPTS x len(HUB_BOXES) and a dead first bbox
must not burn it four times over. An HTTP 401/403 would mean anonymous
access is refused: that is reported as DOWN + NEEDS-KEY, never as a
silent empty snapshot.

Availability: the endpoint may drop connections (the origin closes
without any HTTP response, so no status and never an auth challenge).
The bounded retries ride out short drops; longer ones fall back to the
last-good SNAPSHOT pack with the precise failure in the note.

Records: ``air_traffic`` summaries per watched hub bbox — aircraft count
is *observed* from the returned state vectors, never modelled.
"""
from __future__ import annotations

import json
import os
import random
import time
import urllib.parse

from .base import AdapterError, BaseAdapter, FetchResult, provenance
from .http_util import (NetworkBlocked, RetryExhausted, http_get,
                        http_get_with_retries, http_post_form)

API = "https://opensky-network.org/api/states/all"
TOKEN_URL = ("https://auth.opensky-network.org/auth/realms/opensky-network/"
             "protocol/openid-connect/token")
TOKEN_REFRESH_MARGIN_S = 120.0   # refresh this long before stated expiry
DEFAULT_TOKEN_TTL_S = 300.0      # if the server states no expires_in
SOURCE = "OpenSky Network"
# Watched air-hub bboxes (small, to respect the anonymous budget).
HUB_BOXES = {
    "air_anchorage": (58.0, 63.0, -155.0, -145.0),
    "air_memphis": (34.0, 36.5, -91.5, -88.5),
    "air_louisville": (37.5, 39.0, -86.5, -84.5),
    "air_hongkong": (21.5, 23.0, 113.0, 115.0),
}
MAX_ATTEMPTS = 3          # per bbox request; cycle budget = 3 x 4 boxes
BASE_DELAY_S = 2.0
MAX_DELAY_S = 30.0
JITTER_S = 1.0
DAILY_CREDIT_BUDGET = 400       # anonymous tier, credits/day
AUTH_DAILY_CREDIT_BUDGET = 4000  # authenticated (API client) tier
CREDIT_STOP_FRACTION = 0.8      # stop live fetching at 80% of budget


def bbox_credit_cost(lamin: float, lamax: float,
                     lomin: float, lomax: float) -> int:
    """OpenSky /states/all area-based cost (module docstring table)."""
    area = abs(lamax - lamin) * abs(lomax - lomin)
    if area <= 25:
        return 1
    if area <= 100:
        return 2
    if area <= 400:
        return 3
    return 4


def cycle_credit_cost() -> int:
    return sum(bbox_credit_cost(*box) for box in HUB_BOXES.values())


class OpenSkyAuthError(Exception):
    """Token request failed. The message never contains a credential."""


class OpenSkyTokenProvider:
    """OAuth2 client-credentials bearer token, cached in memory only.

    ``token()`` returns the cached token until ``TOKEN_REFRESH_MARGIN_S``
    before expiry, then requests a new one. ``invalidate()`` drops it
    (used when the API refuses the bearer). Nothing here ever writes the
    token or secret anywhere."""

    def __init__(self, client_id: str, client_secret: str, *,
                 post=None, clock=None) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._post = post or http_post_form
        self._clock = clock or time.monotonic
        self._token: str | None = None
        self._expires_at = 0.0

    def invalidate(self) -> None:
        self._token = None
        self._expires_at = 0.0

    def token(self) -> str:
        now = self._clock()
        if self._token and now < self._expires_at:
            return self._token
        try:
            status, body, _hdrs = self._post(
                TOKEN_URL,
                {"grant_type": "client_credentials",
                 "client_id": self._client_id,
                 "client_secret": self._client_secret},
                timeout=30)
        except NetworkBlocked:
            raise
        except Exception as exc:
            # type name only: a transport error text must not be able
            # to carry request data into logs/health.
            raise OpenSkyAuthError(
                f"token request failed ({type(exc).__name__})") from None
        if status != 200:
            reason = ""
            try:
                err = json.loads(body.decode("utf-8", "replace"))
                if isinstance(err, dict) and isinstance(err.get("error"), str):
                    reason = f": {err['error'][:40]}"
            except (ValueError, UnicodeDecodeError):
                pass
            raise OpenSkyAuthError(f"token request HTTP {status}{reason}")
        try:
            payload = json.loads(body.decode("utf-8", "replace"))
            token = payload["access_token"]
            if not isinstance(token, str) or not token:
                raise ValueError
        except (ValueError, KeyError, TypeError):
            raise OpenSkyAuthError(
                "token response was not a bearer token") from None
        try:
            ttl = float(payload.get("expires_in") or DEFAULT_TOKEN_TTL_S)
        except (TypeError, ValueError):
            ttl = DEFAULT_TOKEN_TTL_S
        self._token = token
        self._expires_at = now + max(0.0, ttl - TOKEN_REFRESH_MARGIN_S)
        return token


class OpenSkyAdapter(BaseAdapter):
    name = "opensky"
    source = SOURCE
    health_max_age_s = 3600  # stale-LIVE threshold
    # Not hard key-gated (anonymous works); the note carries NEEDS-KEY
    # for the authenticated surfaces until credentials land.

    def __init__(self, *, transport=None, sleep=None, rng=None,
                 token_post=None, clock=None) -> None:
        super().__init__()
        self._transport = transport or http_get
        self._token_post = token_post
        self._clock = clock
        self._token_provider: OpenSkyTokenProvider | None = None
        # truthful auth state of the LAST fetch attempt:
        #   "authenticated" | "anonymous" | "anonymous-fallback"
        self._auth_state = "anonymous"
        self._auth_detail: str | None = None
        self._sleep = sleep or time.sleep
        # entropy-seeded by default (no hard-coded seed);
        # a seeded Random may be injected for deterministic delays.
        self._rng = rng if rng is not None else random.Random()
        self._auth_required = False
        self._partial_note: str | None = None
        self._credit_path = None  # override point
        self.last_attempts: int | None = None
        self.last_delays: list[float] = []

    # -- persistent daily credit counter --------------------
    @property
    def credit_path(self):
        if self._credit_path is not None:
            return self._credit_path
        from . import base as _base
        return _base.SNAPSHOT_DIR.parent / "opensky_credits.json"

    def _credits_today(self) -> tuple[str, int]:
        today = time.strftime("%Y-%m-%d", time.gmtime())
        try:
            saved = json.loads(self.credit_path.read_text(encoding="utf-8"))
            if saved.get("date") == today:
                return today, int(saved.get("credits") or 0)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
        return today, 0

    def _charge_credits(self, amount: int) -> None:
        today, used = self._credits_today()
        pack = {"date": today, "credits": used + amount,
                "budget": self._daily_budget(),
                "stop_at": int(self._daily_budget() * CREDIT_STOP_FRACTION)}
        try:
            self.credit_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.credit_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(pack), encoding="utf-8")
            tmp.replace(self.credit_path)
        except OSError:
            pass  # counter is bookkeeping; a read-only data dir must
            # not take the fetch itself down

    def fetch(self):  # annotate health note with the truthful auth state
        self._auth_state = "anonymous"
        self._auth_detail = None
        result = super().fetch()
        if self._auth_required:
            # Refused even anonymously (HTTP 401/403): truthful state
            # is DOWN + NEEDS-KEY, not a silent empty snapshot.
            self.health.state = "DOWN"
            self.health.mode = None
            self.health.note = (
                "NEEDS-KEY: OpenSky refused the request (HTTP 401/403) — "
                "set OPENSKY_CLIENT_ID/OPENSKY_CLIENT_SECRET (OpenSky API "
                "client) to enable live fetch")
            return result
        if self._auth_state == "authenticated":
            suffix = ("authenticated (OAuth2 client credentials; "
                      f"{AUTH_DAILY_CREDIT_BUDGET} credits/day budget)")
        elif self._has_client():
            suffix = (f"anonymous ({self._auth_detail or 'OAuth2 client configured but not used this cycle'}); "
                      f"{DAILY_CREDIT_BUDGET} credits/day budget")
        elif self._env("OPENSKY_USERNAME") and self._env("OPENSKY_PASSWORD"):
            suffix = ("anonymous: OPENSKY_USERNAME/PASSWORD are set but "
                      "OpenSky retired basic auth — set OPENSKY_CLIENT_ID/"
                      "OPENSKY_CLIENT_SECRET (API client) for authenticated "
                      "access; anonymous budget in use")
        else:
            suffix = ("anonymous: authenticated OpenSky surfaces NEEDS-KEY "
                      "(set OPENSKY_CLIENT_ID/OPENSKY_CLIENT_SECRET); "
                      "anonymous budget in use")
        self.health.note = (self.health.note + "; " + suffix) if self.health.note else suffix
        if self._partial_note:
            self.health.note = (self.health.note + "; " + self._partial_note) \
                if self.health.note else self._partial_note
        return result

    # -- OAuth2 -----------------------------------------------------------
    def _has_client(self) -> bool:
        return bool(self._env("OPENSKY_CLIENT_ID")
                    and self._env("OPENSKY_CLIENT_SECRET"))

    def _bearer_headers(self) -> dict:
        """Authorization header for this request, or {} (anonymous).
        Sets ``_auth_state``/``_auth_detail`` truthfully."""
        if not self._has_client():
            return {}
        if self._auth_state == "anonymous-fallback":
            return {}  # already fell back this cycle; do not hammer auth
        if self._token_provider is None:
            self._token_provider = OpenSkyTokenProvider(
                self._env("OPENSKY_CLIENT_ID"), self._env("OPENSKY_CLIENT_SECRET"),
                post=self._token_post, clock=self._clock)
        try:
            tok = self._token_provider.token()
        except NetworkBlocked:
            raise
        except OpenSkyAuthError as exc:
            self._auth_state = "anonymous-fallback"
            self._auth_detail = f"OAuth2 {exc}; fell back to anonymous"
            return {}
        self._auth_state = "authenticated"
        return {"Authorization": f"Bearer {tok}"}

    def _daily_budget(self) -> int:
        return (AUTH_DAILY_CREDIT_BUDGET if self._has_client()
                and self._auth_state != "anonymous-fallback"
                else DAILY_CREDIT_BUDGET)

    def _get_bbox(self, url: str, headers: dict):
        kw = {"headers": headers} if headers else {}
        return http_get_with_retries(
            url, transport=self._transport, sleep=self._sleep,
            rng=self._rng, max_attempts=MAX_ATTEMPTS,
            base_delay_s=BASE_DELAY_S, max_delay_s=MAX_DELAY_S,
            jitter_s=JITTER_S, timeout=30, **kw)

    @staticmethod
    def _env(k: str):
        return os.environ.get(k)

    def _fetch_live(self) -> FetchResult:
        # a fresh attempt clears the auth flag — it is set
        # again only if THIS attempt is actually refused (401/403).
        self._auth_required = False
        self._partial_note = None
        # Credit gate: stop live fetching at 80% of the
        # 400/day anonymous budget, charged 5 credits/cycle for the
        # four bboxes (bbox_credit_cost over HUB_BOXES).
        headers = self._bearer_headers()   # token acquired up front
        _today, _used = self._credits_today()
        _budget = self._daily_budget()
        _stop = int(_budget * CREDIT_STOP_FRACTION)
        if _used + cycle_credit_cost() > _stop:
            raise AdapterError(
                f"OpenSky daily credit budget stop: {_used}/"
                f"{_budget} credits already used today; "
                f"live fetching stops at {_stop} (80%)")
        prov = provenance(SOURCE, "LIVE")
        records = []
        for hub, (lamin, lamax, lomin, lomax) in HUB_BOXES.items():
            params = {"lamin": lamin, "lamax": lamax, "lomin": lomin, "lomax": lomax}
            url = API + "?" + urllib.parse.urlencode(params)
            try:
                outcome = self._get_bbox(url, headers)
                if outcome.status in (401, 403) and headers:
                    # The bearer was refused: drop the cached token and
                    # retry ONCE anonymously; say so in the health note.
                    if self._token_provider is not None:
                        self._token_provider.invalidate()
                    self._auth_state = "anonymous-fallback"
                    self._auth_detail = (
                        f"OAuth2 bearer refused (HTTP {outcome.status}); "
                        "fell back to anonymous")
                    headers = {}
                    outcome = self._get_bbox(url, headers)
            except RetryExhausted as exc:
                self.last_attempts, self.last_delays = exc.attempts, exc.delays
                if records:
                    # a bbox failure after at least one success
                    # returns the partial results WITH a note, instead
                    # of discarding everything as an AdapterError.
                    self._partial_note = (
                        f"partial results: bbox {hub} unreachable after "
                        f"{exc.attempts} attempt(s) ({exc.last_error}); "
                        f"returning {len(records)}/{len(HUB_BOXES)} bboxes")
                    break
                raise AdapterError(
                    f"OpenSky unreachable after {exc.attempts} attempt(s) "
                    f"for bbox {hub} (backoff delays "
                    f"{[round(d, 2) for d in exc.delays]}s): {exc.last_error} "
                    f"— cycle budget stop") from exc
            self.last_attempts, self.last_delays = outcome.attempts, outcome.delays
            status, body = outcome.status, outcome.body
            if status in (401, 403):
                self._auth_required = True
                raise AdapterError(
                    f"OpenSky HTTP {status}: access refused")
            if status != 200:
                if records:
                    self._partial_note = (
                        f"partial results: bbox {hub} returned HTTP "
                        f"{status} after {outcome.attempts} attempt(s); "
                        f"returning {len(records)}/{len(HUB_BOXES)} bboxes")
                    break
                if status == 429:
                    tier = ("authenticated" if self._auth_state == "authenticated"
                            else "anonymous")
                    raise AdapterError(
                        f"OpenSky {tier} rate budget exhausted (HTTP 429 "
                        f"after {outcome.attempts} attempt(s))")
                raise AdapterError(
                    f"OpenSky HTTP {status} after {outcome.attempts} attempt(s)")
            try:
                payload = json.loads(body.decode("utf-8", "replace"))
            except json.JSONDecodeError as exc:
                raise AdapterError(f"OpenSky returned non-JSON body: {exc}") from exc
            if not isinstance(payload, dict):
                raise AdapterError("OpenSky payload is not an object")
            states = payload.get("states") or []
            self._charge_credits(bbox_credit_cost(lamin, lamax, lomin, lomax))
            records.append({
                "record_type": "air_traffic",
                "node_id": hub,
                "aircraft_in_bbox": len(states),
                "observed_at_epoch": payload.get("time"),
                "dedupe_key": f"opensky:{hub}:{payload.get('time')}",
                "provenance": dict(prov),
            })
        return FetchResult(records=records, provenance=prov)
