"""Give memory back to the operating system after a heavy build.

A cold sim build allocates 0.9 to 1.25 GB of small Python objects. When the build ends, CPython frees them but the
process keeps the pages until the allocator is asked to release them: measured on the recorded stack, the resident set
after the two demo builds read 492 MB, 406 MB after ``gc.collect()`` (cycles left by the build) and 282 MB after
``malloc_trim(0)``. On the 2 GB public machine that headroom is what the next build, a Monte Carlo job or a burst of
visitors needs. Both calls are best effort and never raise; ``malloc_trim`` exists only on glibc.
"""
from __future__ import annotations

import ctypes
import gc
import os
import sys


def release_free_memory() -> bool:
    """Collect cycles and ask glibc to return free heap pages. True when malloc_trim ran (and found something to do)."""
    if os.environ.get("SUPERMAP_MEMTRIM", "1").strip().lower() in ("0", "off", "false", "no"):
        return False
    try:
        gc.collect()
    except Exception:
        pass
    if not sys.platform.startswith("linux"):
        return False
    try:
        return bool(ctypes.CDLL("libc.so.6").malloc_trim(0))
    except Exception:
        return False
