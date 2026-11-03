"""Engine constants registry.

Every model constant of the propagation engine lives in
``constants.json`` beside this module: each entry carries a value, a
unit, a source citation OR the explicit label ``ASSUMPTION``, and
bounds (``null`` only for composite values). Nothing in the registry
is a bare number: if a constant has no defensible source, it says so.

The registry's hash — SHA-256 over the canonical JSON of the parsed
registry — is included in every run record the engine emits
(``RunState.constants_hash``, plus ``RunState.run_record()``), so a
stored run can always be re-derived against the exact model constants
that produced it.

Adding or changing a constant means editing ``constants.json`` (with
its provenance); ``engine.py`` contains no model-constant numeric
literals outside this loader.
"""
from __future__ import annotations

import json
from pathlib import Path

from .canonical import canonical_json, sha256_hex

_REGISTRY_PATH = Path(__file__).resolve().parent / "constants.json"


def _load() -> dict:
    with open(_REGISTRY_PATH, "r", encoding="utf-8") as fh:
        reg = json.load(fh)
    if not isinstance(reg, dict) or not isinstance(reg.get("constants"), dict):
        raise ValueError("constants.json: registry must be an object with 'constants'")
    for name, entry in reg["constants"].items():
        if not isinstance(entry, dict) or "value" not in entry:
            raise ValueError(f"constants.json: entry {name!r} must carry a value")
        if not entry.get("source"):
            raise ValueError(
                f"constants.json: entry {name!r} has no source citation / "
                "ASSUMPTION label — every constant must state its provenance")
        if "bounds" not in entry:
            raise ValueError(f"constants.json: entry {name!r} has no bounds field")
        bounds = entry["bounds"]
        value = entry["value"]
        if bounds is not None and isinstance(value, (int, float)) \
                and not isinstance(value, bool):
            lo, hi = bounds
            if not (float(lo) <= float(value) <= float(hi)):
                raise ValueError(
                    f"constants.json: entry {name!r} value {value!r} outside "
                    f"its stated bounds {bounds!r}")
    return reg


REGISTRY = _load()
CONSTANTS: dict = REGISTRY["constants"]
CONSTANTS_VERSION: str = str(REGISTRY.get("version", ""))
CONSTANTS_HASH: str = sha256_hex(canonical_json(REGISTRY))


def const(name: str):
    """The value of registry constant ``name`` (KeyError if absent)."""
    return CONSTANTS[name]["value"]


def const_entry(name: str) -> dict:
    return CONSTANTS[name]
