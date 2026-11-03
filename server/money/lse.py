"""LSE feed — DOCUMENTED STUB ONLY.

The LSE market-data feed is BYOK (bring-your-own-key) and LOCAL-ONLY:
the hosted demo NEVER touches it. There is deliberately no client here
— no endpoint, no key handling, no fetch path. This module exists so
the money layer can state that refusal in code (and health) instead of
silently omitting the source.

To use LSE data locally, a developer runs their own BYOK integration
outside the hosted demo and outside this repository's money layer.
"""
from __future__ import annotations

from .common import health_dict


class LSELocalOnlyError(Exception):
    """Raised by every LSE entry point: BYOK local-only, by design."""


class LSEStub:
    name = "lse"
    source = "LSE (BYOK local-only)"

    def fetch(self, *args, **kwargs):
        raise LSELocalOnlyError(
            "LSE is BYOK local-only: the hosted demo never touches the "
            "LSE feed and no LSE client is implemented in this layer")

    def health(self) -> dict:
        return health_dict(
            self.name, "DOWN", None, None,
            "BYOK local-only: hosted demo never touches the LSE feed; "
            "stub refuses by design (no client implemented)")
