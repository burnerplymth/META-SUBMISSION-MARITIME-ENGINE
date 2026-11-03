"""Server-Sent Events framing and the per-client stream generator."""
from __future__ import annotations

import asyncio
import time

from .hub import Client, LiveHub, Msg
from .normalize import iso_z
from .payload import dumps

SSE_HEADERS = {
    # no-transform: proxies must not re-encode or buffer the stream.
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",          # nginx-style proxies
    "Connection": "keep-alive",
}


def frame(event: str, data: str, eid: str | None = None) -> str:
    out = []
    if eid:
        out.append(f"id: {eid}")
    out.append(f"event: {event}")
    out.append(f"data: {data}")
    return "\n".join(out) + "\n\n"


def frame_msg(m: Msg) -> str:
    return f"id: {m.eid}\nevent: {m.topic}\ndata: {m.text}\n\n"


async def stream(hub: LiveHub, client: Client, replay: list[Msg],
                 reset: str | None, head_id: str, request, *,
                 heartbeat_s: float, max_stream_s: float,
                 resuming: bool):
    """Async generator of SSE text frames for one client.

    Order: retry hint, hello (or reset), replay, then live messages.
    Drop policy: when the client's bounded queue overflowed, the OLDEST
    messages were discarded; a ``gap`` frame says how many and points at
    /live/snapshot, then the retained messages follow in order."""
    evt = asyncio.Event()
    client.bind(asyncio.get_running_loop(), evt)
    started = last_hb = time.monotonic()
    try:
        yield "retry: 3000\n\n"
        hello = {"epoch": hub.epoch, "last_event_id": head_id,
                 "topics": sorted(client.topics),
                 "heartbeat_s": heartbeat_s, "resumed": bool(resuming
                                                             and not reset),
                 "replayed": len(replay),
                 "snapshot": "/live/snapshot", "at": iso_z(time.time())}
        if reset:
            yield frame("reset", dumps({
                "reason": reset, "resync": "/live/snapshot",
                "last_event_id": head_id}), head_id)
        yield frame("hello", dumps(hello), None if resuming and not reset
                    else head_id)
        for m in replay:
            yield frame_msg(m)
        while True:
            msgs, dropped, more = client.drain()
            if dropped:
                yield frame("gap", dumps({
                    "dropped": dropped, "resync": "/live/snapshot",
                    "reason": "client_queue_overflow"}))
            for m in msgs:
                yield frame_msg(m)
            if not hub.closed and hub.stop_probe is not None and \
                    hub.stop_probe():
                hub.close()                  # server is shutting down
            if client.closed or hub.closed:
                yield frame("bye", dumps({"reason": "server_shutdown"}))
                break
            if more:
                await asyncio.sleep(0)       # let other tasks run
                continue
            if max_stream_s and time.monotonic() - started > max_stream_s:
                yield frame("bye", dumps({"reason": "max_stream_age",
                                          "reconnect": True}))
                break
            evt.clear()
            if client.q:                      # raced with a push
                continue
            # uvicorn waits for open streams BEFORE it runs lifespan
            # shutdown, so when a stop probe exists we wake at least
            # once a second to see the shutdown flag.
            wait_s = heartbeat_s if hub.stop_probe is None \
                else min(heartbeat_s, 1.0)
            try:
                await asyncio.wait_for(evt.wait(), timeout=wait_s)
            except asyncio.TimeoutError:
                if request is not None and await request.is_disconnected():
                    break
                now = time.monotonic()
                if now - last_hb >= heartbeat_s - 1e-6:
                    last_hb = now
                    yield frame("heartbeat", dumps({
                        "t": iso_z(time.time()), "seq": hub.seq}))
    finally:
        hub.unsubscribe(client)
