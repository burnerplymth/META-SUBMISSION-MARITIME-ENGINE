"""Words the product never publishes in model-written text (product rule: no war
or political content). One list, used by the research-note verifier
(``agent/research_verify.py``) and the brief verifier (``brief/verify.py``).

Stems, not whole words, so inflections are caught ("attacked", "sanctioned",
"conflicts", "geopolitical"). A false positive only costs the model's wording
(the sentence is dropped or replaced by the template); a miss would put
political text in front of a visitor, so the list errs wide.
"""
from __future__ import annotations

import re

POLITICAL_RE = re.compile(
    r"\b(?:wars?|warfare|warlike|milit\w*|missiles?|invasion|invad\w*|sanction\w*|elections?|terror\w*|"
    r"attack\w*|houthi\w*|ukrain\w*|russia\w*|israel\w*|gaza|tariffs?|embargo\w*|"
    r"(?:geo)?politic\w*|government\s+polic\w*|armed|conflicts?|hostilit\w*|blockad\w*|troops?|regimes?|"
    r"coups?|protest\w*|rebel\w*|insurgen\w*|ceasefire|yemen\w*|iran\w*|iraq\w*|syria\w*|lebanon|"
    r"hezbollah|hamas|taliban|kremlin|nato|putin|trump|biden|zelensk\w*|netanyahu|xi\s+jinping|"
    r"naval|navy|bomb\w*|shell(?:ing|ed)|casualt\w*)\b", re.I)


def has_political_wording(text: str) -> bool:
    return bool(POLITICAL_RE.search(text or ""))
