"""PNG rendering of figures: the SVG goes through the same
node + playwright subprocess path as the PDF route (one browser launch for
any number of figures, hard timeout). Raises PngError when the renderer is
missing or fails; the routes turn that into 501 / 502. PNG bytes are the
renderer's output and are not promised byte-identical across machines; the
SVG they come from is."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .. import pdf, render_gate

SCRIPT = Path(__file__).resolve().parents[1] / "svg_render.js"
TIMEOUT_S = 60


class PngError(Exception):
    def __init__(self, message: str = "", busy: bool = False):
        super().__init__(message)
        self.busy = busy


def available() -> tuple[bool, str]:
    return pdf.available()


def render_pngs(svgs: dict[str, str], scale: float = 2.0,
                timeout_s: int = TIMEOUT_S) -> dict[str, bytes]:
    """{name: svg text} -> {name: png bytes}"""
    ok, why = available()
    if not ok:
        raise PngError(why)
    if not svgs:
        return {}
    with tempfile.TemporaryDirectory(prefix="supermap-png-") as td:
        td = Path(td)
        args, outs = [], {}
        for i, name in enumerate(sorted(svgs)):
            src, dst = td / f"f{i}.svg", td / f"f{i}.png"
            src.write_text(svgs[name], encoding="utf-8")
            args += [str(src), str(dst)]
            outs[name] = dst
        try:
            with render_gate.slot():
                r = subprocess.run(
                    [shutil.which("node"), str(SCRIPT), str(scale)] + args,
                    capture_output=True, timeout=timeout_s,
                    env={k: v for k, v in os.environ.items()
                         if k in ("PATH", "HOME", "NODE_PATH",
                                  "PLAYWRIGHT_BROWSERS_PATH", "LANG")})
        except render_gate.RendererBusy as exc:
            raise PngError(str(exc), busy=True)
        except subprocess.TimeoutExpired:
            raise PngError(f"PNG render timed out after {timeout_s}s")
        if r.returncode != 0 or not all(p.exists() for p in outs.values()):
            raise PngError("PNG render failed: "
                           + r.stderr.decode("utf-8", "replace")[:200])
        return {n: p.read_bytes() for n, p in outs.items()}
