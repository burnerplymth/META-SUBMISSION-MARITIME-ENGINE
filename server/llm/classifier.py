"""Classifier: feed text -> structured disruption candidate.

Output: {node_id|null, kind, severity, confidence,
expected_duration_d?} plus bookkeeping: mintable (confidence >= the fixed
0.70 threshold AND node grounded AND relevant), cost preview/actual,
transport, model.

Reply contract: the model's reply is validated
against a STRICT schema before any use —

  * top level must be a JSON object (list/scalar -> invalid);
  * no extra fields beyond the contract;
  * kind must be in VALID_KINDS;
  * severity must be an integer 1..5 (the string "high" is invalid,
    never coerced);
  * confidence must be a finite float in [0, 1] — NaN/Inf are rejected
    explicitly via math.isfinite (Python's json accepts the NaN
    literal, and a naive clamp turned NaN into confidence 1.0);
  * `relevant` (boolean) and `reason` (string) are required:
    relevant=false makes the candidate ALWAYS non-mintable.

On invalid output: ONE retry, then a deterministic unclassified
fallback (mintable=false, unclassified=true). No exception from a
malformed model reply ever escapes classify() — nothing leaks to the
HTTP layer as a 500 or as raw exception text. Provider-level errors
(cap, kill switch, corrupt spend store) are NOT malformed replies and
still propagate as their typed errors.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from . import grounding
from . import provenance as prov
from .provider import LLMProvider

# Classifier auto-mint threshold;
# tuned only with a journal note.
MINT_THRESHOLD = 0.70

VALID_KINDS = {"closure", "capacity_cut", "congestion", "strike", "weather",
               "grounding", "accident", "policy", "other"}

_ALLOWED_FIELDS = {"node_id", "kind", "severity", "confidence",
                   "expected_duration_d", "relevant", "reason"}
_REQUIRED_FIELDS = {"node_id", "kind", "severity", "confidence",
                    "relevant", "reason"}


class _InvalidOutput(Exception):
    """Internal only: the model reply failed schema validation. The
    message is a clean, fixed-shape description — it never reaches a
    client (classify retries, then falls back)."""


def _validate_output(raw: object) -> dict:
    """Validate one parsed model reply against the strict contract.
    Returns the normalised candidate fields or raises _InvalidOutput."""
    if not isinstance(raw, dict):
        raise _InvalidOutput("classifier output is not a JSON object")
    extra = set(raw) - _ALLOWED_FIELDS
    if extra:
        raise _InvalidOutput(
            f"classifier output has unexpected fields: {sorted(extra)}")
    missing = _REQUIRED_FIELDS - set(raw)
    if missing:
        raise _InvalidOutput(
            f"classifier output is missing fields: {sorted(missing)}")
    node_id = raw["node_id"]
    if node_id is not None and not isinstance(node_id, str):
        raise _InvalidOutput("node_id must be a string or null")
    kind = raw["kind"]
    if kind not in VALID_KINDS:
        raise _InvalidOutput("kind is not in the classifier kind enum")
    severity = raw["severity"]
    if isinstance(severity, bool) or not isinstance(severity, int) \
            or not 1 <= severity <= 5:
        raise _InvalidOutput("severity must be an integer in 1..5")
    confidence = raw["confidence"]
    if isinstance(confidence, bool) \
            or not isinstance(confidence, (int, float)) \
            or not math.isfinite(confidence) \
            or not 0.0 <= float(confidence) <= 1.0:
        raise _InvalidOutput("confidence must be a finite number in [0, 1]")
    relevant = raw["relevant"]
    if not isinstance(relevant, bool):
        raise _InvalidOutput("relevant must be a boolean")
    reason = raw["reason"]
    if not isinstance(reason, str):
        raise _InvalidOutput("reason must be a string")
    out = {
        "node_id": node_id,
        "kind": kind,
        "severity": severity,
        "confidence": float(confidence),
        "relevant": relevant,
        "reason": reason,
    }
    dur = raw.get("expected_duration_d")
    if dur is not None:
        if isinstance(dur, bool) or not isinstance(dur, (int, float)) \
                or not math.isfinite(dur) or dur <= 0:
            raise _InvalidOutput(
                "expected_duration_d must be a finite number > 0")
        out["expected_duration_d"] = float(dur)
    return out


def _graph_node_ids(graph_path: Path | None) -> set[str] | None:
    """Valid place ids (cached by grounding.graph_places), or None when the
    graph cannot be read; None makes every model id fail closed."""
    ids = grounding.place_ids(graph_path)
    return None if ids is None else set(ids)


class Classifier:
    def __init__(self, provider: LLMProvider | None = None,
                 graph_path: Path | None = None):
        self.provider = provider or LLMProvider.from_env()
        self._graph_path = graph_path
        self._node_ids = _graph_node_ids(graph_path)

    def cost_preview(self, text: str, source: str, retrieved_at: str) -> dict:
        return self.provider.cost_preview(
            "classifier",
            {"text": text, "source": source, "retrieved_at": retrieved_at})

    def _unclassified(self, res: dict | None) -> dict:
        """Deterministic fallback after a failed validation + one retry:
        an explicitly unclassified, never-mintable candidate."""
        out: dict = {
            "node_id": None,
            "kind": "other",
            "severity": 1,
            "confidence": 0.0,
            "relevant": False,
            "reason": "unclassified: model output failed validation",
            "unclassified": True,
            "grounding": {"status": "unclassified",
                          "reason": "model_output_failed_validation"},
            "mintable": False,
            "threshold": MINT_THRESHOLD,
            "cost_usd": res["cost_usd"] if res else 0.0,
            "estimated_cost_usd": res["estimated_cost_usd"] if res else 0.0,
            "transport": res["transport"] if res else "none",
            "model": res["model"] if res else "none",
        }
        out["provenance"] = prov.from_result(res, "classifier") if res \
            else prov.none("classifier produced no usable reply")
        return out

    def classify(self, text: str, source: str, retrieved_at: str) -> dict:
        payload = {"text": text, "source": source, "retrieved_at": retrieved_at}
        res: dict | None = None
        parsed: dict | None = None
        for _attempt in range(2):  # initial call + ONE retry
            res = self.provider.complete("classifier", payload)
            try:
                parsed = _validate_output(json.loads(res["text"]))
            except (json.JSONDecodeError, TypeError, ValueError,
                    _InvalidOutput):
                parsed = None
                continue
            break
        if parsed is None:
            return self._unclassified(res)
        assert res is not None
        node_id = parsed["node_id"]
        # Referential integrity: a place id that is not in the graph is
        # ungrounded -> null with a labelled reason, never minted against
        # a dangling reference and never echoed downstream. Checked here
        # whether or not the request was schema-constrained, and failing
        # closed when the graph cannot be read.
        node_id, ground_reason = grounding.ground_id(node_id, self._node_ids)
        if ground_reason == grounding.OK:
            ground_status = "grounded"
        elif ground_reason == grounding.NO_PLACE:
            ground_status = "no_place"
        else:
            ground_status = "id_rejected"
        out: dict = {
            "node_id": node_id,
            "kind": parsed["kind"],
            "severity": parsed["severity"],
            "confidence": parsed["confidence"],
            "relevant": parsed["relevant"],
            "reason": parsed["reason"],
            "unclassified": False,
            "grounding": {"status": ground_status, "reason": ground_reason},
        }
        if "expected_duration_d" in parsed:
            out["expected_duration_d"] = parsed["expected_duration_d"]
        out["mintable"] = bool(node_id is not None
                               and parsed["relevant"]
                               and parsed["confidence"] >= MINT_THRESHOLD)
        out["threshold"] = MINT_THRESHOLD
        out["cost_usd"] = res["cost_usd"]
        out["estimated_cost_usd"] = res["estimated_cost_usd"]
        out["transport"] = res["transport"]
        out["model"] = res["model"]
        out["provenance"] = prov.from_result(res, "classifier")
        return out

    def brief(self, candidate: dict, text: str = "") -> dict:
        """One-line briefing for a classified candidate (brief prompt)."""
        payload = dict(candidate)
        valid = self._node_ids
        # The candidate's own place id is re-checked here (a caller may hand
        # in anything); an id that is not in the graph never reaches the model.
        node_id, _why = grounding.ground_id(payload.get("node_id"), valid)
        payload["node_id"] = node_id
        node = next((n for n in grounding.graph_places(self._graph_path) or []
                     if n["id"] == node_id), None)
        if node is not None:
            payload["node_name"] = node.get("name")
        if text:
            payload["text"] = text
        res = self.provider.complete("brief", payload)
        # Prose may name no id except the candidate's own place.
        allowed = {node_id} if node_id else set()
        unknown = grounding.find_unknown_ids(res["text"], allowed)
        out = {"brief": res["text"], "brief_source": "model",
               "grounding": {"status": "grounded" if node_id else "no_place",
                             "reason": grounding.OK if node_id
                             else grounding.NO_PLACE},
               "cost_usd": res["cost_usd"],
               "transport": res["transport"], "model": res["model"],
               "provenance": prov.from_result(res, "brief")}
        if unknown:
            where = (node or {}).get("name") or "an unplaced report"
            out["brief"] = (
                f"TEMPLATE: {payload.get('kind', 'other')} at {where}, "
                f"severity {payload.get('severity', '?')}"
                + (f", expected {payload['expected_duration_d']} d."
                   if payload.get("expected_duration_d") else "."))
            out["brief_source"] = "template"
            out["grounding"] = {"status": "id_rejected",
                                "reason": "brief_named_unknown_place_id"}
            out["provenance"] = {**out["provenance"],
                                 "narrative_source": "template"}
        return out
