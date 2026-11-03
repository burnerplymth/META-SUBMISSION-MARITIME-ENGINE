"""Strict payload validation for alert rules and watchlists.

Wrong types are rejected, never coerced: a severity of "3" (string) or
True (bool) is a 422, exactly like the /run and /ensemble surfaces.
Unknown keys — top-level or inside ``params`` — are rejected, the same
rule engine.validate_params applies. The API maps RuleValidationError
to the standard error shape via its ``code`` ("invalid_request" 422,
or "unknown_node" 422 when a node id does not resolve to a graph node).
"""
from __future__ import annotations

RULE_TYPES = {"adapter_state", "event", "run_outcome", "ledger"}
ADAPTER_MATCHES = {"state_is", "state_is_not"}
ADAPTER_STATES = {"LIVE", "SNAPSHOT", "DOWN"}          # Adapter health enum
NODE_CLASSES = {"sea", "air", "ground"}                # Graph node modes
LEDGER_EVENTS = {"lock", "grade"}
RUN_METRICS = {"worst_delay_d", "node_delay_d"}
C3_KINDS = {
    "closure", "capacity_cut", "congestion", "strike", "weather",
    "grounding", "accident", "policy", "other",
}

_RULE_KEYS = {"name", "type", "enabled", "params"}
_PARAMS_KEYS = {
    "adapter_state": {"adapter", "match", "state"},
    "event": {"min_severity", "node_id", "node_class", "kind"},
    "run_outcome": {"metric", "node_id", "threshold_d"},
    "ledger": {"event"},
}
_WATCHLIST_KEYS = {"name"}


class RuleValidationError(ValueError):
    """Carries the standard error-shape code for the API layer."""

    def __init__(self, message: str, code: str = "invalid_request"):
        super().__init__(message)
        self.code = code
        self.message = message


def _member(value, allowed) -> bool:
    """Enum membership that is False, never a TypeError, for an
    unhashable value (a JSON list or object): `[] in {set}` raises."""
    return isinstance(value, str) and value in allowed


def _is_number(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _check_name(value, field: str = "name") -> str:
    if not isinstance(value, str):
        raise RuleValidationError(f"{field} must be a string")
    name = value.strip()
    if not 1 <= len(name) <= 80:
        raise RuleValidationError(f"{field} must be 1..80 characters")
    return name


def _check_node_id(value, node_ids: set | None, field: str = "node_id") -> str:
    if not isinstance(value, str) or not value:
        raise RuleValidationError(f"{field} must be a non-empty string")
    if node_ids is not None and value not in node_ids:
        raise RuleValidationError(f"unknown node_id: {value!r}", code="unknown_node")
    return value


def _reject_unknown(mapping: dict, allowed: set, where: str) -> None:
    extra = sorted(set(mapping) - allowed)
    if extra:
        raise RuleValidationError(f"unknown {where} keys: {extra}")


def validate_rule_payload(body, *, node_ids: set | None = None,
                          adapter_names: set | None = None) -> dict:
    """Validate a create/replace rule body. Returns the normalized
    ``{name, type, enabled, params}`` (id/timestamps are the store's)."""
    if not isinstance(body, dict):
        raise RuleValidationError("request body must be an object")
    _reject_unknown(body, _RULE_KEYS, "rule")
    name = _check_name(body.get("name"))
    rtype = body.get("type")
    if not _member(rtype, RULE_TYPES):
        raise RuleValidationError(f"type must be one of {sorted(RULE_TYPES)}")
    enabled = body.get("enabled", True)
    if not isinstance(enabled, bool):
        raise RuleValidationError("enabled must be a boolean")
    params_in = body.get("params")
    if params_in is None:
        params_in = {}
    if not isinstance(params_in, dict):
        raise RuleValidationError("params must be an object")
    _reject_unknown(params_in, _PARAMS_KEYS[rtype], "params")

    params: dict = {}
    if rtype == "adapter_state":
        adapter = params_in.get("adapter")
        if adapter is not None:
            if not isinstance(adapter, str) or not 1 <= len(adapter) <= 64:
                raise RuleValidationError("params.adapter must be a string of 1..64 characters")
            if adapter_names is not None and adapter not in adapter_names:
                raise RuleValidationError(f"unknown adapter: {adapter!r}")
        match = params_in.get("match", "state_is")
        if not _member(match, ADAPTER_MATCHES):
            raise RuleValidationError(f"params.match must be one of {sorted(ADAPTER_MATCHES)}")
        state = params_in.get("state")
        if not _member(state, ADAPTER_STATES):
            raise RuleValidationError(f"params.state must be one of {sorted(ADAPTER_STATES)}")
        params = {"adapter": adapter, "match": match, "state": state}

    elif rtype == "event":
        sev = params_in.get("min_severity", 1)
        if not isinstance(sev, int) or isinstance(sev, bool) or not 1 <= sev <= 5:
            raise RuleValidationError("params.min_severity must be an integer 1..5")
        node_id = params_in.get("node_id")
        if node_id is not None:
            node_id = _check_node_id(node_id, node_ids, "params.node_id")
        node_class = params_in.get("node_class")
        if node_class is not None and not _member(node_class, NODE_CLASSES):
            raise RuleValidationError(
                f"params.node_class must be one of {sorted(NODE_CLASSES)}")
        kind = params_in.get("kind")
        if kind is not None and not _member(kind, C3_KINDS):
            raise RuleValidationError("params.kind is not an event kind")
        params = {"min_severity": sev, "node_id": node_id,
                  "node_class": node_class, "kind": kind}

    elif rtype == "run_outcome":
        metric = params_in.get("metric", "worst_delay_d")
        if not _member(metric, RUN_METRICS):
            raise RuleValidationError(f"params.metric must be one of {sorted(RUN_METRICS)}")
        threshold = params_in.get("threshold_d")
        if not _is_number(threshold):
            raise RuleValidationError("params.threshold_d must be a number")
        threshold = float(threshold)
        if not 0.0 < threshold <= 3650.0:
            raise RuleValidationError("params.threshold_d must be within (0, 3650]")
        node_id = params_in.get("node_id")
        if metric == "node_delay_d":
            if node_id is None:
                raise RuleValidationError(
                    "params.node_id is required when metric is node_delay_d")
            node_id = _check_node_id(node_id, node_ids, "params.node_id")
        elif node_id is not None:
            raise RuleValidationError(
                "params.node_id is only valid when metric is node_delay_d")
        params = {"metric": metric, "node_id": node_id, "threshold_d": threshold}

    elif rtype == "ledger":
        event = params_in.get("event")
        if not _member(event, LEDGER_EVENTS):
            raise RuleValidationError(f"params.event must be one of {sorted(LEDGER_EVENTS)}")
        params = {"event": event}

    return {"name": name, "type": rtype, "enabled": enabled, "params": params}


def validate_watchlist_payload(body) -> dict:
    """Validate a create/rename watchlist body -> ``{name}``."""
    if not isinstance(body, dict):
        raise RuleValidationError("request body must be an object")
    _reject_unknown(body, _WATCHLIST_KEYS, "watchlist")
    return {"name": _check_name(body.get("name"))}


def validate_node_add_payload(body, *, node_ids: set | None = None) -> str:
    """Validate a watchlist add-node body -> the node_id."""
    if not isinstance(body, dict):
        raise RuleValidationError("request body must be an object")
    _reject_unknown(body, {"node_id"}, "watchlist node")
    return _check_node_id(body.get("node_id"), node_ids)
