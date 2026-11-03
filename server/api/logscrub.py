"""Keep secrets out of the access log.

uvicorn's access log prints the request target including the query string.
``/demo/unlock?token=...`` carries the demo token, so the log (``fly logs``,
a container's stdout) would hold it. ``RedactSecrets`` rewrites the values of
secret-shaped query parameters to ``[redacted]`` before the record is
formatted. Installed by ``scripts/serve_api.py``.
"""
from __future__ import annotations

import logging
import re

# an optional lower-case prefix covers owner_token, scenario_token ... (the scenario delete token travelled as ?owner_token=)
_SECRET_KEYS = r"(?:[a-z]+_)?(?:token|demo_token|admin_token|access_token|api_key|apikey|key|secret|password|passwd|authorization)"
_RX = re.compile(r"(?i)([?&]" + _SECRET_KEYS + r"=)[^&\s\"']*")


def scrub(text: str) -> str:
    return _RX.sub(r"\1[redacted]", text)


class RedactSecrets(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.args, tuple):
                record.args = tuple(scrub(a) if isinstance(a, str) else a for a in record.args)
            elif isinstance(record.msg, str):
                record.msg = scrub(record.msg)
        except Exception:                        # a logging filter must never break logging
            pass
        return True


def install() -> None:
    """Attach the filter to uvicorn's access logger (idempotent)."""
    lg = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, RedactSecrets) for f in lg.filters):
        lg.addFilter(RedactSecrets())
