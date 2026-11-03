"""The writer call: Meta AI through the single provider seam.

The model is shown the facts table (id, label, display) and the slots it
may fill, and answers in a closed JSON schema whose fact ids and section ids
are enums built from the table (Meta structured output on api.meta.ai). On
the fixture transport the same call returns the deterministic recorded draft
composed from the table, labelled RECORDED. Either way the draft goes
through the verifier before any word reaches a brief.
"""
from __future__ import annotations

import json

from . import compose
from .model import MAX_WORDS, PROMPT_NAME

SLOT_LIMITS = {"paragraph": {"max_words": 60},
               "bullets": {"max_items": 3, "max_words_each": 24}}


def build_payload(served: list[dict], facts: list[dict]) -> dict:
    """The data block the model reads. Facts carry display strings only."""
    return {
        "task": "disruption_brief",
        "limits": {"max_words_total": MAX_WORDS,
                   "summary": {"max_words": 40},
                   "paragraph": SLOT_LIMITS["paragraph"], "bullets": SLOT_LIMITS["bullets"]},
        "sections": [{"id": s["id"], "title": s["title"],
                      "slots": [{"kind": "paragraph"}, {"kind": "bullets"}]} for s in served],
        "facts": [{"id": f["id"], "key": f["key"], "label": f["label"], "display": f["display"],
                   "section": f["section"]} for f in facts],
    }


def call(provider, payload: dict) -> dict:
    """-> {text, cost_usd, estimated_cost_usd, transport, model, prompt_version, ...}.
    Raises the provider's own errors (CapExceeded, CallTooLarge, ProviderError)."""
    return provider.complete(PROMPT_NAME, payload)


def estimate(provider, payload: dict) -> dict:
    return provider.cost_preview(PROMPT_NAME, payload)


def fixture_brief_json(payload: dict) -> str:
    """The recorded draft: the template writer over the payload's own facts."""
    ids_map = {f["key"]: f["id"] for f in payload.get("facts") or []
               if isinstance(f, dict) and isinstance(f.get("key"), str)}
    section_ids = [s["id"] for s in payload.get("sections") or [] if isinstance(s, dict)]
    return json.dumps(compose.compose_all(section_ids, compose.Ids(ids_map)),
                      ensure_ascii=False, sort_keys=True)
