"""Concurrency gate for the Chromium renderers (PDF and PNG).

Both routes are public and each launches a headless browser in a node
subprocess. At most ``SUPERMAP_RENDER_CONCURRENCY`` (default 2) run at once;
a request that cannot get a slot within ``WAIT_S`` seconds is refused with
``RendererBusy`` (HTTP 503 ``renderer_busy``) instead of piling up browsers.
Each render also has its own hard subprocess timeout (pdf.TIMEOUT_S /
charts.png.TIMEOUT_S, 60 s)."""
from __future__ import annotations

import os
import threading
from contextlib import contextmanager

WAIT_S = 3.0


def _limit() -> int:
    try:
        return max(1, int(os.environ.get("SUPERMAP_RENDER_CONCURRENCY", "2")))
    except ValueError:
        return 2


_sem = threading.BoundedSemaphore(_limit())


class RendererBusy(Exception):
    pass


@contextmanager
def slot(wait_s: float | None = None):
    if not _sem.acquire(timeout=WAIT_S if wait_s is None else wait_s):
        raise RendererBusy("the renderer is busy; try again in a moment")
    try:
        yield
    finally:
        _sem.release()
