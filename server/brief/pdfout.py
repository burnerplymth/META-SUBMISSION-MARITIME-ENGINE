"""PDF of a brief through the existing Chromium pipeline (reports.pdf:
headless Chromium via node + playwright in a subprocess with a hard
timeout), behind a concurrency limit so a handful of exports cannot take
the one shared CPU."""
from __future__ import annotations

import os
import threading

try:
    from reports import pdf as _pdf
except ImportError:  # imported as server.* from the repo root
    from server.reports import pdf as _pdf  # type: ignore

TIMEOUT_S = 60


def _limit() -> int:
    try:
        return max(1, int(os.environ.get("SUPERMAP_BRIEF_PDF_CONCURRENCY", "2")))
    except ValueError:
        return 2


_SEM = threading.BoundedSemaphore(_limit())


class Busy(Exception):
    """All PDF slots are taken."""


def available() -> tuple[bool, str]:
    return _pdf.available()


def render(html: bytes, *, wait_s: float = 20.0) -> bytes:
    """HTML -> PDF bytes. Raises Busy when no slot frees within ``wait_s``,
    and reports.pdf.PdfError when the renderer is missing or fails."""
    if not _SEM.acquire(timeout=wait_s):
        raise Busy()
    try:
        return _pdf.render_pdf(html, timeout_s=TIMEOUT_S)
    finally:
        _SEM.release()


PdfError = _pdf.PdfError
