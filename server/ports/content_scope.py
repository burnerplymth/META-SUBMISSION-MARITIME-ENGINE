"""Content scope guard: war and political wording is held back from
public display surfaces.

One regular expression, mirrored byte for byte in
web/app/src/contentGuard.ts. It is a display
guard, not a classifier: a hit means "do not show this text", never
"this is a war story". Stored artefacts on disk are never edited by it.
"""
from __future__ import annotations

import re

OUT_OF_SCOPE_SOURCE = (
    r"\b(houthis?|iran(?:ian)?|missiles?|military|militias?|armed conflict|war"
    r"|warfare|wartime|attacks?|attacked|seiz(?:e|ed|ure|ures)"
    r"|hostilit(?:y|ies)|ukrain(?:e|ian)|russia(?:n)?|israel(?:i)?|gaza|hamas"
    r"|terror(?:ism|ist|ists)?|invasion|sanctions?|geopolitic(?:s|al)?"
    r"|insurgen(?:t|ts|cy)|ceasefire|truce|clashes"
    r"|blockades?|embargo(?:es)?|piracy|naval|yemen|syria|putin|drones?)\b"
)
_RE = re.compile(OUT_OF_SCOPE_SOURCE, re.IGNORECASE)

WITHHELD_REASON = "withheld: outside the content scope of this product"


def holds_out_of_scope(text: object) -> bool:
    return isinstance(text, str) and _RE.search(text) is not None


def guard_exposures(doc: dict) -> dict:
    """A copy of an exposures doc whose out-of-scope filing excerpts are
    withheld through the schema's own honest fields (excerpt null plus
    excerpt_missing_reason). The filing citation (form, date, URL) is
    kept. The input is not modified."""
    maps = doc.get("mappings")
    if not isinstance(maps, list):
        return doc
    out = []
    for m in maps:
        f = m.get("filing") if isinstance(m, dict) else None
        if isinstance(f, dict) and holds_out_of_scope(f.get("excerpt")):
            m = {**m, "filing": {**f, "excerpt": None},
                 "excerpt_missing_reason": WITHHELD_REASON}
        out.append(m)
    return {**doc, "mappings": out}
