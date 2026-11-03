"""HTTP transport seam for the history providers (stdlib urllib only).

Providers never call urllib directly: they build an ``HttpRequest`` and
hand it to an injectable ``transport``. A scripted fake can be injected in
place of the network; production uses
``default_transport``, which goes through the environment's HTTPS proxy
automatically (urllib honours HTTPS_PROXY / SSL_CERT_FILE).

Credentials ride in ``headers`` only and are attached by the provider at
call time from ``os.environ``; they are never stored in the cache entry
(``HttpRequest.public()`` is what gets recorded) and never logged.
"""
from __future__ import annotations

import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

MAX_RESPONSE_BYTES = 5 * 1024 * 1024  # same 5 MiB cap as adapters.http_util
USER_AGENT = "SupermAP-history/1.0 (research build; stdlib urllib)"


class TransportError(Exception):
    """Network-level failure (DNS, TLS, timeout, egress block)."""


@dataclass
class HttpRequest:
    method: str
    url: str
    params: dict = field(default_factory=dict)
    form: dict | None = None          # POST application/x-www-form-urlencoded
    headers: dict = field(default_factory=dict)   # may carry a credential

    def full_url(self) -> str:
        if not self.params:
            return self.url
        sep = "&" if "?" in self.url else "?"
        return self.url + sep + urllib.parse.urlencode(self.params, doseq=True)

    def public(self) -> dict:
        """What may be recorded in a cache entry: no headers at all."""
        out = {"method": self.method, "url": self.url,
               "params": dict(self.params)}
        if self.form is not None:
            out["form"] = dict(self.form)
        return out


@dataclass
class HttpResponse:
    status: int
    body: bytes
    headers: dict = field(default_factory=dict)


def default_transport(req: HttpRequest, timeout: float = 45.0) -> HttpResponse:
    if os.environ.get("SUPERMAP_BLOCK_NETWORK") == "1":
        raise TransportError("egress blocked by SUPERMAP_BLOCK_NETWORK")
    data = None
    if req.form is not None:
        data = urllib.parse.urlencode(req.form).encode("utf-8")
    r = urllib.request.Request(req.full_url(), data=data, method=req.method)
    r.add_header("User-Agent", USER_AGENT)
    r.add_header("Accept", "application/json, */*")
    if data is not None:
        r.add_header("Content-Type", "application/x-www-form-urlencoded")
    for k, v in req.headers.items():
        r.add_header(k, v)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            body = resp.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise TransportError(
                    f"response exceeds the {MAX_RESPONSE_BYTES}-byte cap")
            return HttpResponse(resp.status, body, dict(resp.headers.items()))
    except urllib.error.HTTPError as exc:
        body = exc.read(MAX_RESPONSE_BYTES + 1)[:MAX_RESPONSE_BYTES]
        return HttpResponse(exc.code, body,
                            dict(exc.headers.items() if exc.headers else []))
    except TransportError:
        raise
    except Exception as exc:  # URLError, ssl.SSLError, timeout, ...
        raise TransportError(f"{type(exc).__name__}: {str(exc)[:160]}") from None
