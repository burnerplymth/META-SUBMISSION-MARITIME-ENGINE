"""Our own argument validation for model-supplied tool arguments.

The model's arguments are untrusted input. Nothing here coerces: a wrong
type, an unknown key, an out-of-range number, a too-long string is refused
with a message the model can read and fix. Supports the JSON-Schema subset
the tool registry uses: type (object/string/number/integer/boolean/array),
enum, minimum/maximum, exclusiveMinimum, minLength/maxLength, pattern,
items, minItems/maxItems, required, properties. Objects never accept keys
that are not declared (additionalProperties is always false).
"""
from __future__ import annotations

import math
import re

MAX_DEPTH = 6


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) \
        and math.isfinite(v)


def validate(value, schema: dict, path: str = "$", _depth: int = 0) -> list[str]:
    """Return a list of error strings; empty means valid."""
    if _depth > MAX_DEPTH:
        return [f"{path}: nested too deeply"]
    t = schema.get("type")
    errs: list[str] = []
    if t == "object":
        if not isinstance(value, dict):
            return [f"{path}: must be an object"]
        props = schema.get("properties", {})
        for k in value:
            if k not in props:
                errs.append(f"{path}: unknown key {str(k)[:40]!r}")
        for k in schema.get("required", []):
            if k not in value:
                errs.append(f"{path}: missing required key {k!r}")
        for k, sub in props.items():
            if k in value:
                errs += validate(value[k], sub, f"{path}.{k}", _depth + 1)
        return errs
    if t == "array":
        if not isinstance(value, list):
            return [f"{path}: must be an array"]
        if len(value) < schema.get("minItems", 0):
            errs.append(f"{path}: needs at least {schema['minItems']} item(s)")
        if len(value) > schema.get("maxItems", 1000):
            errs.append(f"{path}: at most {schema.get('maxItems', 1000)} item(s)")
            return errs
        item = schema.get("items")
        if item:
            for i, v in enumerate(value):
                errs += validate(v, item, f"{path}[{i}]", _depth + 1)
        return errs
    if t == "string":
        if not isinstance(value, str):
            return [f"{path}: must be a string"]
        if len(value) < schema.get("minLength", 0):
            errs.append(f"{path}: too short")
        if len(value) > schema.get("maxLength", 500):
            errs.append(f"{path}: longer than {schema.get('maxLength', 500)} characters")
        pat = schema.get("pattern")
        if pat and not re.fullmatch(pat, value):
            errs.append(f"{path}: has an invalid format")
    elif t == "integer":
        if not _is_int(value):
            return [f"{path}: must be an integer"]
    elif t == "number":
        if not _is_num(value):
            return [f"{path}: must be a finite number"]
    elif t == "boolean":
        if not isinstance(value, bool):
            return [f"{path}: must be true or false"]
        return errs
    else:
        return [f"{path}: unsupported schema type {t!r}"]
    if "enum" in schema and value not in schema["enum"]:
        errs.append(f"{path}: must be one of {schema['enum']}")
    if t in ("integer", "number") and not errs:
        if "minimum" in schema and value < schema["minimum"]:
            errs.append(f"{path}: must be >= {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errs.append(f"{path}: must be <= {schema['maximum']}")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            errs.append(f"{path}: must be > {schema['exclusiveMinimum']}")
    return errs
