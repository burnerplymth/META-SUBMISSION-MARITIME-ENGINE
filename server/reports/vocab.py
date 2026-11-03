"""House vocabulary for report text.

A report is a user deliverable. It says "places", "legs" and "causal
chain": never "node", "edge" or "graph". Text that reaches a report from
an upstream artifact (the stored backtest scorecard's honesty caveats are
written in the engine's own vocabulary) passes through `plain` on its way
in, so the sentence keeps every fact and the engine-room words change.

Identifiers are untouched: `\\b` does not fire inside `node_id`,
`capacity_share` or `queue_cap`, so field names keep their spelling.
The substitution is deterministic and idempotent, so report bytes stay
byte-identical across regenerations.
"""
from __future__ import annotations

import re

_WORDS = {"nodes": "places", "node": "place", "edges": "legs",
          "edge": "leg", "graph": "map"}
_RE = re.compile(r"\b(nodes|node|edges|edge|graph)\b", re.IGNORECASE)

# Words a report must never carry (identifiers like node_id are not words).
FORBIDDEN_RE = re.compile(r"\b(nodes?|edges?|graphs?)\b", re.IGNORECASE)


def _swap(m: re.Match) -> str:
    word = m.group(0)
    new = _WORDS[word.lower()]
    if len(word) > 1 and word.isupper():
        return new.upper()
    if word[0].isupper():
        return new[0].upper() + new[1:]
    return new


def plain(text: str) -> str:
    """Replace node / edge / graph with place / leg / map, keeping case."""
    return _RE.sub(_swap, text)
