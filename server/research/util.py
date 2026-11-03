"""Shared constants, validation and the research error type.

Labels are verbatim contract text.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, timedelta

LABEL_ANALYSIS = ("Descriptive statistics on historical data. Associations, not causes. "
                  "Not a forecast.")
LABEL_ASSEMBLE = "Historical data assembled for description. Not a forecast."
LABEL_OVERLAY = "SIMULATED beside OBSERVED. Not a forecast."
LABEL_MODELLED = "MODELLED"
VERIFICATION = "Detected, not verified"     # a detector count wears this until a human reviews that place

MAX_PLACES = 5
MAX_SPAN_DAYS = 1826            # 5 years
MAX_SCENES = 40
MAX_ANALYSES = 12
MAX_JSON_DEPTH = 8
MAX_STR = 200
MAX_LIST = 64

ID_RE = re.compile(r"^[a-z0-9_:.\-]{1,64}$")
SERIES_KEY_RE = re.compile(r"^[a-z0-9_.\-]{1,64}:[a-z0-9_.\-]{1,64}$")
ASSEMBLY_RE = re.compile(r"^asm_[0-9a-f]{16}$")
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

CATEGORIES = ("weather", "congestion", "labour_action", "accident_blockage",
              "canal_restriction", "infrastructure", "economic_release",
              "trade_policy", "security")
# Excluded from the default library, the episodes route and the demo (content
# rule: no war or political content).
EXCLUDED_CATEGORIES = ("trade_policy", "security")
LIBRARY_CATEGORIES = tuple(c for c in CATEGORIES if c not in EXCLUDED_CATEGORIES)

SCENE_CLOUD_MAX = 35.0
SCENE_NODATA_MAX = 20.0


class ResearchError(Exception):
    """A request the research layer refuses. ``code`` is the house error code."""

    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code = code
        self.status = status


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(data) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def parse_day(value, field: str) -> date:
    if not isinstance(value, str) or not DAY_RE.match(value):
        raise ResearchError("invalid_request", f"{field} must be a YYYY-MM-DD date")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ResearchError("invalid_request", f"{field} is not a real calendar date") from None


def day_list(a: date, b: date) -> list[str]:
    return [(a + timedelta(days=i)).isoformat() for i in range((b - a).days + 1)]


def check_depth(obj, limit: int = MAX_JSON_DEPTH, _d: int = 0) -> None:
    """Reject deeply nested JSON (and absurdly long strings / lists)."""
    if _d > limit:
        raise ResearchError("invalid_request", f"JSON nesting deeper than {limit}")
    if isinstance(obj, dict):
        if len(obj) > 64:
            raise ResearchError("invalid_request", "object with too many keys")
        for k, v in obj.items():
            if not isinstance(k, str) or len(k) > 64:
                raise ResearchError("invalid_request", "bad key")
            check_depth(v, limit, _d + 1)
    elif isinstance(obj, list):
        if len(obj) > 256:
            raise ResearchError("invalid_request", "list too long")
        for v in obj:
            check_depth(v, limit, _d + 1)
    elif isinstance(obj, str):
        if len(obj) > 2000:
            raise ResearchError("invalid_request", "string too long")


def only_keys(body: dict, allowed: set, where: str) -> None:
    extra = sorted(set(body) - allowed)
    if extra:
        raise ResearchError("invalid_request", f"unknown field(s) in {where}: {extra[:5]}")


def str_list(v, field: str, *, max_len: int = MAX_LIST, pattern=ID_RE) -> list[str]:
    if not isinstance(v, list) or len(v) > max_len:
        raise ResearchError("invalid_request", f"{field} must be a list of at most {max_len} ids")
    out = []
    for x in v:
        if not isinstance(x, str) or not pattern.match(x):
            raise ResearchError("invalid_request", f"{field} holds an invalid id")
        out.append(x)
    return out
