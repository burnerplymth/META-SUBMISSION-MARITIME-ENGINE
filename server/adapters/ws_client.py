"""Minimal RFC 6455 websocket client over stdlib socket/ssl.

Exists to avoid a third-party ``websocket-client`` dependency. Supports exactly what AISStream
needs: wss connect + Upgrade handshake, one text subscribe frame, and
reading (possibly fragmented, server-unmasked) text frames — sufficient
for a bounded position-sample fetch, not a general streaming client.

Hardening:
  * ``ws_collect`` runs against ONE monotonic TOTAL deadline — a peer
    trickling a frame every few seconds cannot hold the collect open
    past ``timeout_s`` in aggregate;
  * frames are capped at 1 MiB (declared length checked before the
    payload is read, cumulative across fragments);
  * ping frames (0x9) are answered with a pong (0xA);
  * when an HTTP(S) proxy is configured in the environment, the TCP
    connection is tunnelled through it with CONNECT before the TLS
    wrap (proxy auth is not supported — a proxy demanding it fails
    the handshake, it is never silently bypassed).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import ssl
import struct
import time
from urllib.parse import urlparse

from .http_util import NetworkBlocked, network_blocked

MAX_FRAME_BYTES = 1024 * 1024  # 1 MiB


class WebSocketError(Exception):
    pass


def _recv_exact(sock: ssl.SSLSocket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise WebSocketError("connection closed by peer")
        buf += chunk
    return buf


def _send_frame(sock: ssl.SSLSocket, payload: bytes, opcode: int = 0x1) -> None:
    mask = os.urandom(4)
    header = bytearray([0x80 | opcode])
    n = len(payload)
    if n < 126:
        header.append(0x80 | n)
    elif n < 65536:
        header.append(0x80 | 126)
        header += struct.pack(">H", n)
    else:
        header.append(0x80 | 127)
        header += struct.pack(">Q", n)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    sock.sendall(bytes(header) + mask + masked)


def _read_frame(sock, _budget: int = MAX_FRAME_BYTES) -> tuple[int, bytes]:
    b0, b1 = _recv_exact(sock, 2)
    fin, opcode = b0 & 0x80, b0 & 0x0F
    masked, length = b1 & 0x80, b1 & 0x7F
    if length == 126:
        length = struct.unpack(">H", _recv_exact(sock, 2))[0]
    elif length == 127:
        length = struct.unpack(">Q", _recv_exact(sock, 8))[0]
    if length > _budget:
        raise WebSocketError(
            f"frame length {length} exceeds the 1 MiB frame cap")
    mask = _recv_exact(sock, 4) if masked else None
    payload = _recv_exact(sock, length) if length else b""
    if mask:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    if not fin:  # fragmented: keep reading continuation frames
        op2, rest = _read_frame(sock, _budget - length)
        return opcode, payload + rest
    return opcode, payload


def _collect_messages(sock, *, max_messages: int, deadline: float,
                      clock=time.monotonic,
                      partial_ok: bool = False) -> list[dict]:
    """Read frames until ``max_messages`` JSON messages, a close frame,
    or the TOTAL monotonic ``deadline`` — whichever comes first. The
    per-recv timeout is re-armed to the remaining budget each iteration
    so a trickling peer aborts AT the deadline, not after it.

    ``partial_ok``: a SAMPLING collector asks
    for "up to N messages in T seconds". On a sparse stream the window
    ending (deadline or socket read timeout) with N not yet reached is
    the normal way a sample ends — the messages already received are
    returned instead of being discarded behind a timeout error. The
    default (False) keeps the strict behaviour."""
    out: list[dict] = []
    while len(out) < max_messages:
        remaining = deadline - clock()
        if remaining <= 0:
            if partial_ok:
                break
            raise WebSocketError(
                "collect deadline exceeded: total time budget exhausted")
        try:
            sock.settimeout(remaining)
        except Exception:
            pass
        try:
            opcode, payload = _read_frame(sock)
        except (TimeoutError, socket.timeout):
            if partial_ok:
                break
            raise
        if opcode == 0x9:  # ping -> pong, never counted as a message
            _send_frame(sock, payload, opcode=0xA)
            continue
        if opcode == 0x8:
            break
        if opcode in (0x1, 0x2):
            try:
                out.append(json.loads(payload.decode("utf-8", "replace")))
            except json.JSONDecodeError:
                continue
    return out


def _proxy_for(url_scheme: str) -> str | None:
    for var in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        value = os.environ.get(var)
        if value:
            return value
    return None


def _connect_tcp(host: str, port: int, timeout_s: float):
    """Direct TCP connect, or a CONNECT tunnel through the configured
    proxy. Returns a raw (not yet TLS-wrapped) socket.

    Kill switch: SUPERMAP_BLOCK_NETWORK=1 raises NetworkBlocked before any
    socket is created, so every websocket path (ws_collect, WsStream.open
    and the collectors) honours the offline demo."""
    if network_blocked():
        raise NetworkBlocked(
            f"egress blocked by SUPERMAP_BLOCK_NETWORK for {host}")
    proxy = _proxy_for("wss")
    if not proxy:
        return socket.create_connection((host, port), timeout=timeout_s)
    pu = urlparse(proxy if "://" in proxy else f"http://{proxy}")
    proxy_host, proxy_port = pu.hostname, pu.port or 8080
    raw = socket.create_connection((proxy_host, proxy_port),
                                   timeout=timeout_s)
    raw.sendall(
        f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n"
        .encode())
    resp = b""
    while b"\r\n\r\n" not in resp:
        chunk = raw.recv(4096)
        if not chunk:
            raw.close()
            raise WebSocketError("proxy CONNECT closed")
        resp += chunk
        if len(resp) > 65536:
            raw.close()
            raise WebSocketError("proxy CONNECT response too large")
    status_line = resp.split(b"\r\n", 1)[0].decode("latin-1")
    if " 200" not in status_line:
        raw.close()
        raise WebSocketError(f"proxy CONNECT failed: {status_line}")
    return raw


def _handshake(sock, host: str, path: str, deadline: float) -> bytes:
    """Upgrade handshake on a TLS socket. Returns any bytes the server
    sent after the response headers (the start of the frame stream)."""
    key = base64.b64encode(os.urandom(16)).decode()
    handshake = (
        f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\n"
        f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n"
    )
    sock.sendall(handshake.encode())
    resp = b""
    while b"\r\n\r\n" not in resp:
        if time.monotonic() >= deadline:
            raise WebSocketError(
                "collect deadline exceeded during handshake")
        chunk = sock.recv(4096)
        if not chunk:
            raise WebSocketError("handshake closed")
        resp += chunk
    status_line = resp.split(b"\r\n", 1)[0].decode("latin-1")
    if "101" not in status_line:
        raise WebSocketError(f"handshake failed: {status_line}")
    expect = base64.b64encode(hashlib.sha1(
        (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
    if expect.encode() not in resp:
        raise WebSocketError("handshake accept key mismatch")
    return resp.split(b"\r\n\r\n", 1)[1]


def ws_collect(url: str, subscribe: dict, *, max_messages: int = 50,
               timeout_s: float = 20.0, partial_ok: bool = False) -> list[dict]:
    """Connect, send one JSON subscribe frame, collect JSON text messages.

    ``timeout_s`` is a TOTAL monotonic deadline for the whole call
    (connect + handshake + collect), not a per-read timeout. With
    ``partial_ok`` the collect window ending early returns what was
    received (see ``_collect_messages``); connect/handshake failures
    still raise."""
    deadline = time.monotonic() + timeout_s
    u = urlparse(url)
    host, port = u.hostname, u.port or 443
    path = u.path or "/"
    raw = _connect_tcp(host, port, timeout_s)
    try:
        ctx = ssl.create_default_context()
        sock = ctx.wrap_socket(raw, server_hostname=host)
    except Exception:
        raw.close()
        raise
    try:
        sock.settimeout(max(0.1, deadline - time.monotonic()))
        _handshake(sock, host, path, deadline)
    except Exception:
        sock.close()
        raise
    try:
        _send_frame(sock, json.dumps(subscribe).encode())
        return _collect_messages(sock, max_messages=max_messages,
                                  deadline=deadline, partial_ok=partial_ok)
    finally:
        try:
            _send_frame(sock, b"", opcode=0x8)
        except Exception:
            pass
        sock.close()


class WsStream:
    """A PERSISTENT websocket connection (one per collector thread).

    Unlike ``ws_collect`` (a bounded sample), this keeps the socket open
    and hands messages out one at a time:

      * ``recv(timeout_s)`` returns the next text/binary message payload
        (bytes), or ``None`` when ``timeout_s`` passes with no complete
        message (the partial bytes stay buffered, nothing is lost);
      * reads are buffered (one ``recv`` syscall per ~64 KiB, not two
        per frame) and frames are capped at ``MAX_FRAME_BYTES`` including
        fragments;
      * pings are answered with pongs; a close frame or a closed socket
        raises ``WebSocketError`` so the caller reconnects.

    The same proxy tunnelling and TLS verification as ``ws_collect``
    apply (the system trust store, never disabled)."""

    def __init__(self, url: str, *, connect_timeout_s: float = 20.0):
        self.url = url
        self.connect_timeout_s = connect_timeout_s
        self._sock = None
        self._buf = bytearray()
        self._frag = bytearray()
        self.bytes_in = 0

    # -- lifecycle -------------------------------------------------------
    def open(self) -> None:
        deadline = time.monotonic() + self.connect_timeout_s
        u = urlparse(self.url)
        host, port = u.hostname, u.port or 443
        raw = _connect_tcp(host, port, self.connect_timeout_s)
        try:
            ctx = ssl.create_default_context()
            sock = ctx.wrap_socket(raw, server_hostname=host)
        except Exception:
            raw.close()
            raise
        try:
            sock.settimeout(max(0.1, deadline - time.monotonic()))
            rest = _handshake(sock, host, u.path or "/", deadline)
        except Exception:
            sock.close()
            raise
        self._sock = sock
        self._buf = bytearray(rest)
        self._frag = bytearray()

    def send_json(self, obj) -> None:
        _send_frame(self._sock, json.dumps(obj).encode())

    def close(self) -> None:
        sock, self._sock = self._sock, None
        if sock is None:
            return
        try:
            sock.settimeout(1.0)
            _send_frame(sock, b"", opcode=0x8)
        except Exception:
            pass
        try:
            sock.close()
        except Exception:
            pass

    # -- reading ---------------------------------------------------------
    def _frame(self):
        """Parse one complete frame from the buffer, else None."""
        buf = self._buf
        n = len(buf)
        if n < 2:
            return None
        b0, b1 = buf[0], buf[1]
        length = b1 & 0x7F
        pos = 2
        if length == 126:
            if n < 4:
                return None
            length = (buf[2] << 8) | buf[3]
            pos = 4
        elif length == 127:
            if n < 10:
                return None
            length = struct.unpack(">Q", bytes(buf[2:10]))[0]
            pos = 10
        if length > MAX_FRAME_BYTES:
            raise WebSocketError(
                f"frame length {length} exceeds the 1 MiB frame cap")
        masked = b1 & 0x80
        if masked:
            pos += 4
        if n < pos + length:
            return None
        payload = bytes(buf[pos:pos + length])
        if masked:
            mask = buf[pos - 4:pos]
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        del buf[:pos + length]
        return b0 & 0x80, b0 & 0x0F, payload

    def recv(self, timeout_s: float):
        """Next data-message payload, or None on timeout."""
        deadline = time.monotonic() + timeout_s
        sock = self._sock
        if sock is None:
            raise WebSocketError("connection is closed")
        while True:
            fr = self._frame()
            if fr is not None:
                fin, opcode, payload = fr
                if opcode == 0x9:
                    _send_frame(sock, payload, opcode=0xA)
                    continue
                if opcode == 0xA:
                    continue
                if opcode == 0x8:
                    raise WebSocketError("connection closed by peer")
                if opcode in (0x1, 0x2, 0x0):
                    if opcode != 0x0 and not self._frag and fin:
                        return payload
                    if len(self._frag) + len(payload) > MAX_FRAME_BYTES:
                        raise WebSocketError("fragmented message exceeds "
                                             "the 1 MiB frame cap")
                    self._frag += payload
                    if fin:
                        out, self._frag = bytes(self._frag), bytearray()
                        return out
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                sock.settimeout(remaining)
                chunk = sock.recv(65536)
            except (TimeoutError, socket.timeout):
                return None
            if not chunk:
                raise WebSocketError("connection closed by peer")
            self.bytes_in += len(chunk)
            self._buf += chunk
