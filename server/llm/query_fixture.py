"""Recorded-fixture heuristic for the NL query prompt (RECORDED transport).

A deterministic keyword reader that stands in for the model when no key is set.
It is a FIXTURE, labelled RECORDED by the provenance block - it
exercises plumbing and the post-checks, not Muse Spark's language ability.
It only ever drops words from the user's text and maps keywords to the
closed enums; it cannot add a token the user did not type.
"""
from __future__ import annotations

import json
import re

CATEGORY_WORDS = {
    "events": "events", "event": "events", "disruptions": "events",
    "disruption": "events", "incidents": "events", "news": "events",
    "places": "places", "ports": "places", "port": "places", "airports": "places",
    "airport": "places", "chokepoints": "places",
    "runs": "runs", "ledger": "ledger", "companies": "companies",
    "watchlists": "watchlists", "goods": "goods", "commodities": "goods",
    "panels": "panels",
}
MODE_WORDS = {
    "air": "air", "airport": "air", "airports": "air", "flights": "air",
    "sea": "sea", "maritime": "sea", "shipping": "sea", "vessels": "sea",
    "ground": "ground", "road": "ground", "rail": "ground", "truck": "ground",
    "trucks": "ground",
}
SEVERITY_WORDS = {"critical": 4, "severe": 4, "major": 4, "serious": 4}
DATE_PHRASES = [
    (("today", "24h", "24 hours", "last day"), 1),
    (("this week", "last week", "past week", "7 days", "last 7 days"), 7),
    (("this month", "last month", "past month", "30 days"), 30),
]
STOP = {"show", "me", "find", "the", "a", "an", "of", "in", "at", "for", "with",
        "all", "any", "that", "are", "is", "what", "where", "from", "last",
        "past", "this", "recent", "latest", "only", "and", "or", "to", "my",
        "week", "month", "today", "days", "day", "severity", "sev", "level",
        "plus", "above", "over", "than", "more"}


def fixture_query(payload: dict) -> dict:
    raw = str(payload.get("text", ""))
    low = raw.lower()
    consumed: set[str] = set()
    cats: list[str] = []
    modes: list[str] = []
    sev = None
    days = None
    for phrase_set, d in DATE_PHRASES:
        for ph in phrase_set:
            if ph in low:
                days = d if days is None else min(days, d)
                consumed.update(ph.split())
    m = re.search(r"\b(?:sev(?:erity)?\s*)?([1-5])\s*\+", low) \
        or re.search(r"\bsev(?:erity)?\s*([1-5])\b", low)
    if m:
        sev = int(m.group(1))
        consumed.add(m.group(1))
    words = re.findall(r"[a-z0-9][a-z0-9\-']*", low)
    for w in words:
        if w in SEVERITY_WORDS and sev is None:
            sev = SEVERITY_WORDS[w]
            consumed.add(w)
        if w in MODE_WORDS and MODE_WORDS[w] not in modes:
            modes.append(MODE_WORDS[w])
            if w in ("air", "sea", "ground", "maritime", "shipping", "vessels",
                     "road", "rail", "truck", "trucks", "flights"):
                consumed.add(w)
        if w in CATEGORY_WORDS and CATEGORY_WORDS[w] not in cats:
            cats.append(CATEGORY_WORDS[w])
            consumed.add(w)
    kept = [w for w in words if w not in consumed and w not in STOP
            and not re.fullmatch(r"[1-5]\+?", w)]
    return {
        "text": " ".join(kept),
        "categories": cats or None,
        "modes": modes or None,
        "severity_min": sev,
        "date_floor_days": days,
    }


def fixture_query_json(payload: dict) -> str:
    return json.dumps(fixture_query(payload), sort_keys=True)
