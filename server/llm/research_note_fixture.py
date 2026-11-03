"""RECORDED research note (fixture transport): no model, no key.

A pure function of the payload the note prompt receives (the session's
evidence entries). It narrates what the entries hold as STRUCTURED CLAIMS
(template id, entity, direction, unit, values copied exactly from the
entries), the same shape a live reply must have, so the code-side verifier
binds each claim to the evidence and writes the sentence itself
(``server/agent/research_claims.py``). It never computes a statistic, never
states a cause and writes no free text. The reply is labelled RECORDED by
the caller (``server/agent/research_note.py``): the label is not part of
the JSON.
"""
from __future__ import annotations

import json


def _entry(raw: dict) -> dict:
    """The payload view of one entry as the verifier's entry shape."""
    return {"id": raw.get("id"), "kind": raw.get("kind"),
            "numbers": {x["name"]: x["value"] for x in raw.get("values") or [] if isinstance(x, dict) and "name" in x},
            "strings": {x["name"]: x["value"] for x in raw.get("text") or [] if isinstance(x, dict) and "name" in x}}


def fixture_research_note_json(payload: dict) -> str:
    try:
        from ..agent.research_claims import suggest_for_all
    except ImportError:  # server/ on sys.path (top-level "llm" package)
        from agent.research_claims import suggest_for_all  # type: ignore

    entries = [_entry(r) for r in payload.get("evidence") or [] if isinstance(r, dict) and "id" in r]
    findings = []
    for i, c in enumerate(suggest_for_all(entries)[:8], 1):
        findings.append({"id": f"F{i}", **c})
    return json.dumps({"title": "Research note", "findings": findings, "context": [], "limitations": []})
