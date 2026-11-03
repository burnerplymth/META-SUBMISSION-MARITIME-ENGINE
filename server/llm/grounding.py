"""Grounding: a model reply may only name places that exist in the graph.

Two independent layers, both fed by the SAME runtime graph read (never a
hard-coded list):

1. Request side (Meta endpoint only): ``response_format_for`` builds a
   Chat Completions ``response_format`` json_schema whose place-id fields
   are an ``enum`` of valid place ids, so decoding is constrained.
   Meta's documented limits: depth 10, 1,000 enum values by default. The
   graph holds 992 places, so the full set fits; when it ever outgrows the
   limit the enum is dropped (``None`` is returned for the place field's
   enum) and the code-side layer carries the whole load.

2. Code side (every endpoint, every transport): ``ground_id`` /
   ``ground_id_list`` / ``find_unknown_ids`` check every place id in any
   reply against the graph and return a LABELLED reason when one is
   rejected. Nothing here trusts layer 1: a constrained reply is checked
   exactly like an unconstrained one.

Reason codes are fixed strings; the rejected model value is never echoed.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Meta's documented default enum limit for structured output.
META_ENUM_LIMIT = 1000

# Id-shaped token as the graph spells them (sea_losangeles, chk_suez, ...).
ID_RE = re.compile(r"\b(?:sea|air|gnd|chk|rail|road)_[a-z0-9_]+\b")

# Reason codes.
OK = "grounded"
NO_PLACE = "no_place"
NOT_IN_GRAPH = "node_id_not_in_graph"
NOT_A_STRING = "node_id_not_a_string"
GRAPH_UNAVAILABLE = "graph_unavailable"

_cache: dict[str, tuple[int, list[dict] | None]] = {}


def graph_path(path: Path | None = None) -> Path:
    return Path(path or os.environ.get(
        "SUPERMAP_GRAPH", REPO_ROOT / "data" / "graph" / "graph.json"))


def graph_places(path: Path | None = None) -> list[dict] | None:
    """The graph's place records, cached by (path, mtime_ns); None when the
    graph cannot be read (callers then fail closed)."""
    p = graph_path(path)
    key = str(p)
    try:
        mtime_ns = p.stat().st_mtime_ns
    except OSError:
        return None
    hit = _cache.get(key)
    if hit is not None and hit[0] == mtime_ns:
        return hit[1]
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        nodes = [n for n in doc.get("nodes", []) if isinstance(n, dict)
                 and isinstance(n.get("id"), str)]
    except Exception:
        nodes = None
    _cache[key] = (mtime_ns, nodes)
    return nodes


def place_ids(path: Path | None = None) -> list[str] | None:
    """Sorted valid place ids, or None when the graph is unreadable."""
    nodes = graph_places(path)
    if nodes is None:
        return None
    return sorted({n["id"] for n in nodes})


# --------------------------------------------------------------------------
# Code-side validation
# --------------------------------------------------------------------------

def ground_id(value: object, valid: set[str] | None) -> tuple[str | None, str]:
    """One place id from a model reply -> (id | None, reason code).

    Exact match only: no case folding, trimming or fuzzy matching. An
    unreadable graph (valid is None) rejects every id (fail closed)."""
    if value is None:
        return None, NO_PLACE
    if not isinstance(value, str):
        return None, NOT_A_STRING
    if valid is None:
        return None, GRAPH_UNAVAILABLE
    if value in valid:
        return value, OK
    return None, NOT_IN_GRAPH


def ground_id_list(values: object, allowed: set[str] | None
                   ) -> tuple[list[str], int]:
    """A list of cited ids -> (kept ids in first-seen order, rejected count).
    ``allowed`` is the set the ids may come from (scope / chain nodes, already
    a subset of the graph); None rejects everything."""
    if not isinstance(values, list):
        return [], 0 if values is None else 1
    kept: list[str] = []
    rejected = 0
    for v in values:
        if isinstance(v, str) and allowed is not None and v in allowed:
            if v not in kept:
                kept.append(v)
        else:
            rejected += 1
    return kept, rejected


def find_unknown_ids(text: str, allowed: set[str] | None) -> list[str]:
    """Id-shaped tokens in free text that are not in ``allowed``."""
    if not isinstance(text, str):
        return []
    return sorted({t for t in ID_RE.findall(text)
                   if allowed is None or t not in allowed})


def foreign_place_names(text: str, in_scope: set[str],
                        nodes: list[dict]) -> list[str]:
    """Graph place ids whose NAME appears in ``text`` although the place is
    not in ``in_scope`` (a name that is part of an in-scope place's longer
    name is not foreign)."""
    low = text.lower()
    scope_names = [str(n.get("name") or "").lower()
                   for n in nodes if n["id"] in in_scope]
    out = []
    for n in nodes:
        name = str(n.get("name") or "").lower()
        if n["id"] in in_scope or len(name) < 5 or name not in low:
            continue
        if any(name in sn for sn in scope_names):
            continue
        out.append(n["id"])
    return out


# --------------------------------------------------------------------------
# Request side: Meta structured output
# --------------------------------------------------------------------------

CLASSIFIER_KINDS = ["closure", "capacity_cut", "congestion", "strike",
                    "weather", "grounding", "accident", "policy", "other"]


# Satellite crop vision read (prompts/satvision.txt). No place ids: nothing to
# ground here; the code side re-validates the reply.
SATVISION_QUALITY = ["clear", "hazy", "cloud_affected", "unusable"]
SATVISION_LEVELS = ["sparse", "moderate", "dense", "cannot_assess"]
SATVISION_SCHEMA = {
    "type": "object",
    "properties": {
        "scene": {"type": "string"},
        "image_quality": {"type": "string", "enum": SATVISION_QUALITY},
        "visible_features": {
            "type": "array", "maxItems": 8,
            "items": {"type": "object",
                      "properties": {"label": {"type": "string"},
                                     "detail": {"type": "string"}},
                      "required": ["label", "detail"],
                      "additionalProperties": False}},
        "congestion_cues": {"type": "array", "maxItems": 5,
                            "items": {"type": "string"}},
        "congestion_level": {"type": "string", "enum": SATVISION_LEVELS},
        "confidence": {"type": "number"},
        "cannot_see": {"type": "array", "minItems": 3,
                       "items": {"type": "string"}},
        "limits": {"type": "array", "minItems": 2,
                   "items": {"type": "string"}},
    },
    "required": ["scene", "image_quality", "visible_features",
                 "congestion_cues", "congestion_level", "confidence",
                 "cannot_see", "limits"],
    "additionalProperties": False,
}


def _id_schema(ids: list[str], nullable: bool) -> dict:
    """Schema for ONE place-id value, enum-constrained when it fits."""
    if not ids or len(ids) + (1 if nullable else 0) > META_ENUM_LIMIT:
        return {"type": ["string", "null"] if nullable else "string"}
    return {"type": ["string", "null"] if nullable else "string",
            "enum": list(ids) + ([None] if nullable else [])}


def _json_schema(name: str, schema: dict) -> dict:
    return {"type": "json_schema",
            "json_schema": {"name": name, "schema": schema}}


def _payload_ids(payload: dict) -> list[str]:
    """Place ids a cited_node_ids list may use: the chain's nodes when a
    chain is supplied, otherwise the scoped subgraph's nodes."""
    chain = payload.get("chain")
    if isinstance(chain, dict):
        ids = {ev.get("node_id") for ev in chain.get("events") or []
               if isinstance(ev, dict)}
    else:
        sub = payload.get("subgraph") or {}
        ids = {n.get("id") for n in sub.get("nodes") or []
               if isinstance(n, dict)}
    return sorted(i for i in ids if isinstance(i, str))


def response_format_for(prompt_name: str, payload: dict,
                        path: Path | None = None) -> dict | None:
    """The ``response_format`` for a prompt whose JSON names places, built
    from the graph at call time; None for prompts that need none."""
    if prompt_name == "classifier":
        ids = place_ids(path) or []
        return _json_schema("disruption_candidate", {
            "type": "object",
            "properties": {
                "node_id": _id_schema(ids, nullable=True),
                "kind": {"type": "string", "enum": CLASSIFIER_KINDS},
                "severity": {"type": "integer", "enum": [1, 2, 3, 4, 5]},
                "confidence": {"type": "number"},
                "relevant": {"type": "boolean"},
                "reason": {"type": "string"},
                "expected_duration_d": {"type": "number"},
            },
            "required": ["node_id", "kind", "severity", "confidence",
                         "relevant", "reason"],
            "additionalProperties": False,
        })
    if prompt_name in ("ask", "ask_chain"):
        cited = _payload_ids(payload) or (place_ids(path) or [])
        if prompt_name == "ask_chain":
            chain = payload.get("chain") or {}
            run_ids = [chain.get("run_id")] if isinstance(
                chain.get("run_id"), str) else []
        else:
            run_ids = [r for r in (payload.get("subgraph") or {})
                       .get("run_ids") or [] if isinstance(r, str)]
        run_items: dict = {"type": "string"}
        run_arr: dict = {"type": "array", "items": run_items}
        if run_ids:
            run_items["enum"] = sorted(set(run_ids))
        else:
            run_arr["maxItems"] = 0
        props = {
            "answer": {"type": "string"},
            "cited_node_ids": {"type": "array",
                               "items": _id_schema(cited, nullable=False)},
            "run_ids": run_arr,
        }
        required = ["answer", "cited_node_ids", "run_ids"]
        if prompt_name == "ask_chain":
            # The prompt's refusal reply {"refused": true, "reason": ...}
            # carries no answer, so nothing is required here; the code
            # side treats an absent or empty answer as not usable.
            props["refused"] = {"type": "boolean"}
            props["reason"] = {"type": "string"}
            required = []
        return _json_schema(prompt_name, {
            "type": "object", "properties": props, "required": required,
            "additionalProperties": False})
    if prompt_name == "satvision":
        return _json_schema("satvision", SATVISION_SCHEMA)
    if prompt_name == "research_note":
        return _json_schema("research_note", research_note_schema(payload))
    if prompt_name == "disruption_brief":
        return _json_schema("disruption_brief", _brief_schema(payload))
    return None


def _brief_schema(payload: dict) -> dict:
    """The disruption brief's reply schema (server/brief). Section ids and
    fact ids are enums built from the payload, so the model can name only
    the sections it was offered and cite only facts in the table. The text
    carries {Fn} placeholders; the server fills them and verifies the rest."""
    fact_ids = [f["id"] for f in payload.get("facts") or []
                if isinstance(f, dict) and isinstance(f.get("id"), str)]
    section_ids = [s["id"] for s in payload.get("sections") or []
                   if isinstance(s, dict) and isinstance(s.get("id"), str)]
    fid: dict = {"type": "string"}
    if fact_ids and len(fact_ids) <= META_ENUM_LIMIT:
        fid["enum"] = fact_ids
    sid: dict = {"type": "string"}
    if section_ids:
        sid["enum"] = section_ids
    block = {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["paragraph", "bullets"]},
            "text": {"type": "string"},
            "items": {"type": "array", "maxItems": 3, "items": {"type": "string"}},
            "fact_ids": {"type": "array", "maxItems": 8, "items": fid},
        },
        "required": ["kind", "text", "items", "fact_ids"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "sections": {"type": "array", "maxItems": 12, "items": {
                "type": "object",
                "properties": {"id": sid,
                               "blocks": {"type": "array", "maxItems": 2, "items": block}},
                "required": ["id", "blocks"],
                "additionalProperties": False}},
        },
        "required": ["summary", "sections"],
        "additionalProperties": False,
    }


def research_note_schema(payload: dict) -> dict:
    """Structured output for the research note: findings are STRUCTURED CLAIMS
    (a closed template id, entity, direction, unit, one evidence id, slot
    values), not prose. Template ids, slot names and evidence ids are enums,
    so a reply cannot name a template, slot or entry that does not exist; the
    code-side verifier (agent/research_claims.py, agent/research_verify.py)
    still binds every claim to the evidence and writes the sentence itself,
    and does not trust this layer. ``context`` is free text, shown unchecked."""
    ids = sorted({e.get("id") for e in payload.get("evidence") or []
                  if isinstance(e, dict) and isinstance(e.get("id"), str)})
    ev_id = {"type": "string", "enum": ids} if ids else {"type": "string"}
    templates = [t for t in payload.get("templates") or [] if isinstance(t, dict)]
    tids = sorted({t["template_id"] for t in templates if isinstance(t.get("template_id"), str)})
    slots = sorted({s["slot"] for t in templates for s in t.get("slots") or []
                    if isinstance(s, dict) and isinstance(s.get("slot"), str)})
    value = {"type": "object", "properties": {
        "slot": {"type": "string", "enum": slots} if slots else {"type": "string"},
        "value": {"type": "number"}},
        "required": ["slot", "value"], "additionalProperties": False}
    finding = {"type": "object", "properties": {
        "id": {"type": "string"},
        "template_id": {"type": "string", "enum": tids} if tids else {"type": "string"},
        "metric": {"type": "string"}, "entity": {"type": "string"}, "entity2": {"type": "string"},
        "direction": {"type": "string", "enum": ["up", "down", "unchanged", "none"]},
        "unit": {"type": "string"}, "evidence": ev_id,
        "values": {"type": "array", "items": value}},
        "required": ["id", "template_id", "metric", "entity", "entity2", "direction", "unit",
                     "evidence", "values"], "additionalProperties": False}
    context = {"type": "object", "properties": {
        "text": {"type": "string"}, "evidence": {"type": "array", "items": ev_id}},
        "required": ["text", "evidence"], "additionalProperties": False}
    return {"type": "object", "properties": {
        "title": {"type": "string"},
        "findings": {"type": "array", "items": finding},
        "context": {"type": "array", "items": context},
        "limitations": {"type": "array", "items": {"type": "string"}}},
        "required": ["title", "findings", "context", "limitations"], "additionalProperties": False}
