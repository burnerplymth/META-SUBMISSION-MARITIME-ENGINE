"""Call the app's OWN routes in-process (ASGI), no socket.

Why routes and not service functions: the run, fork, compare, verify, lane
planner, port twin and report handlers are closures inside ``create_app``
and registered elsewhere; importing them would mean refactoring
``app.py`` and would bypass the validation, error shape,
rate limits and body cap that every route already enforces. Going through
the ASGI app means the agent can do exactly what the HTTP API can do and
nothing else (least privilege), with one source of truth.

The call enters through the full middleware stack (request context, CORS,
``install_security``). The client identity is a fixed internal peer so the
agent has its own rate-limit bucket and cannot starve a browser's.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from urllib.parse import urlencode

INTERNAL_CLIENT = ("agent-internal", 0)
MAX_RESPONSE_BYTES = 8_000_000


@dataclass
class ApiResult:
    status: int
    body: object          # parsed JSON, or None when not JSON
    raw_len: int
    content_type: str = ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def error(self) -> tuple[str, str]:
        b = self.body
        if isinstance(b, dict) and isinstance(b.get("error"), dict):
            e = b["error"]
            return str(e.get("code", f"http_{self.status}")), str(e.get("message", ""))[:300]
        return f"http_{self.status}", ""


class AsgiApi:
    """Callable API client bound to one ASGI app."""

    def __init__(self, app, extra_headers=None):
        self.app = app
        self.extra_headers = list(extra_headers or [])

    def unlocked(self) -> "AsgiApi":
        """A client for an agent session that holds the demo token: its
        in-process calls carry the token so /llm/* serves them live too. The
        token stays inside this process. With the gate off it is a no-op."""
        try:
            from llm import demo_gate
        except ImportError:  # imported as server.agent.* from the repo root
            from server.llm import demo_gate  # type: ignore
        return AsgiApi(self.app, demo_gate.internal_headers())

    def get(self, path: str, params: dict | None = None) -> ApiResult:
        return self.request("GET", path, params=params)

    def post(self, path: str, body: dict | None = None) -> ApiResult:
        return self.request("POST", path, body=body)

    def request(self, method: str, path: str, params: dict | None = None,
                body: dict | None = None) -> ApiResult:
        return asyncio.run(self._call(method, path, params, body))

    async def _call(self, method, path, params, body) -> ApiResult:
        payload = json.dumps(body).encode("utf-8") if body is not None else b""
        headers = [(b"host", b"agent.internal"), (b"accept", b"application/json")]
        headers += self.extra_headers
        if body is not None:
            headers += [(b"content-type", b"application/json"),
                        (b"content-length", str(len(payload)).encode())]
        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": method, "scheme": "http", "path": path,
            "raw_path": path.encode(), "root_path": "",
            "query_string": urlencode(params or {}).encode(),
            "headers": headers, "client": INTERNAL_CLIENT,
            "server": ("agent.internal", 80), "state": {},
        }
        sent = False

        async def receive():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": payload, "more_body": False}
            return {"type": "http.disconnect"}

        status = 500
        resp_headers: dict[str, str] = {}
        chunks: list[bytes] = []
        size = 0

        async def send(msg):
            nonlocal status, size
            if msg["type"] == "http.response.start":
                status = msg["status"]
                for k, v in msg.get("headers", []):
                    resp_headers[k.decode().lower()] = v.decode("latin-1")
            elif msg["type"] == "http.response.body":
                b = msg.get("body", b"")
                size += len(b)
                if size <= MAX_RESPONSE_BYTES:
                    chunks.append(b)

        await self.app(scope, receive, send)
        raw = b"".join(chunks)
        parsed = None
        ctype = resp_headers.get("content-type", "")
        if "json" in ctype and size <= MAX_RESPONSE_BYTES:
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except ValueError:
                parsed = None
        return ApiResult(status=status, body=parsed, raw_len=size, content_type=ctype)
