"""Optional PDF rendering of the print view.

Renderer: headless Chromium through node + playwright, run in a
SUBPROCESS with a hard timeout. When node or playwright is missing the
route answers 501 renderer_unavailable and says so; the print-optimised
HTML (format pdf-print-view) is always available and prints to PDF from
any browser. PDF bytes are NOT deterministic (the renderer embeds a
creation time); the HTML they are made from is.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import render_gate

SCRIPT = Path(__file__).resolve().parent / "pdf_render.js"
TIMEOUT_S = 60
_AVAIL: dict = {}


class PdfError(Exception):
    def __init__(self, message: str = "", busy: bool = False):
        super().__init__(message)
        self.busy = busy


def available() -> tuple[bool, str]:
    if "v" in _AVAIL:
        return _AVAIL["v"]
    node = shutil.which("node")
    if not node:
        res = (False, "node is not installed on this server")
    else:
        try:
            r = subprocess.run(
                [node, "-e", "require.resolve('playwright')"],
                capture_output=True, timeout=15)
            res = ((True, "chromium via playwright (node subprocess)")
                   if r.returncode == 0 else
                   (False, "the node 'playwright' package is not installed"))
        except (OSError, subprocess.TimeoutExpired):
            res = (False, "node could not be run")
    _AVAIL["v"] = res
    return res


def render_pdf(html: bytes, timeout_s: int = TIMEOUT_S) -> bytes:
    ok, why = available()
    if not ok:
        raise PdfError(why)
    with tempfile.TemporaryDirectory(prefix="supermap-pdf-") as td:
        src, out = Path(td) / "report.html", Path(td) / "report.pdf"
        src.write_bytes(html)
        try:
            with render_gate.slot():
                r = subprocess.run(
                    [shutil.which("node"), str(SCRIPT), str(src), str(out)],
                    capture_output=True, timeout=timeout_s,
                    env={k: v for k, v in os.environ.items()
                         if k in ("PATH", "HOME", "NODE_PATH",
                                  "PLAYWRIGHT_BROWSERS_PATH", "LANG")})
        except render_gate.RendererBusy as exc:
            raise PdfError(str(exc), busy=True)
        except subprocess.TimeoutExpired:
            raise PdfError(f"PDF render timed out after {timeout_s}s")
        if r.returncode != 0 or not out.exists():
            raise PdfError("PDF render failed: "
                           + r.stderr.decode("utf-8", "replace")[:200])
        return out.read_bytes()
