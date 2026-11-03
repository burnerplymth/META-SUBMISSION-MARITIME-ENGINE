"""Remove secrets from text that is about to be logged or shown.

An upstream error body can echo the bearer it was sent. ``redact`` removes (a) the exact values of every secret
the server holds in its environment and (b) anything shaped like a bearer header or a long key. Pure; no I/O."""
from __future__ import annotations

import os
import re

_SECRET_ENVS = ("META_API_KEY", "META_MODEL_API_KEY", "SUPERMAP_META_API_KEY", "SUPERMAP_LLM_API_KEY",
                "SUPERMAP_DEMO_TOKEN", "SUPERMAP_ADMIN_TOKEN", "SUPERMAP_VOICE_API_KEY")
_KEYLIKE = re.compile(r"(?i)(bearer\s+\S+|\b(?:sk|pk|key|tok|llm|meta|mk)[-_][A-Za-z0-9_\-]{8,}|\b[A-Za-z0-9_\-]{32,}\b)")


def redact(text, limit: int | None = None) -> str:
    out = str(text)
    for name in _SECRET_ENVS:
        val = (os.environ.get(name) or "").strip()
        if len(val) >= 6:
            out = out.replace(val, "[redacted]")
    out = _KEYLIKE.sub("[redacted]", out)
    return out if limit is None else out[:limit]
