"""Canonical JSON + hashing.

Canonical form: object keys sorted lexicographically, no insignificant
whitespace, numbers in shortest round-trip form (Python's json encoder
already emits repr-shortest floats). The event stream hash is SHA-256 over
the canonical encoding of the event array ONLY.
"""
from __future__ import annotations

import hashlib
import json


def canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def stream_hash(events: list[dict]) -> str:
    """SHA-256 over the canonical event array."""
    return sha256_hex(canonical_json(events))


def graph_hash(graph_dict: dict) -> str:
    return sha256_hex(canonical_json(graph_dict))
