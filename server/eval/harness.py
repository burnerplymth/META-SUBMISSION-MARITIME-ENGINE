"""Classifier evaluation on a labelled set.

Grades the LLM classifier against a hand-labelled set built from
stored feed items (data/eval/labelled_set.json; every item carries its
labels and its provenance). HARD RULE: grades are only ever
computed against a LIVE model transport. Under the fixture transport
no grade is produced and the result reports AWAITING LIVE KEY — fixture
outputs are recorded canned responses, and a grade against them would
be a fabricated measurement.
"""
from __future__ import annotations

import json
from pathlib import Path

AWAITING = "AWAITING LIVE KEY"


def load_labelled_set(path: str | Path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def set_summary(labelled: dict) -> dict:
    items = labelled.get("items", [])
    return {
        "size": len(items),
        "provenance": labelled.get("provenance"),
        "label_fields": labelled.get("label_fields", []),
        "sources": sorted({i.get("source") for i in items if i.get("source")}),
    }


def evaluate(labelled: dict, classify_fn, transport: str) -> dict:
    """classify_fn(item) -> predicted label dict. Refuses fixture."""
    summary = set_summary(labelled)
    if transport != "http":
        return {"status": AWAITING, "transport": transport,
                "labelled_set": summary, "grades": None}
    items = labelled.get("items", [])
    per_field: dict[str, dict] = {}
    for item in items:
        pred = classify_fn(item) or {}
        for field in labelled.get("label_fields", []):
            bucket = per_field.setdefault(field, {"correct": 0, "total": 0})
            bucket["total"] += 1
            if pred.get(field) == item["labels"].get(field):
                bucket["correct"] += 1
    for bucket in per_field.values():
        bucket["accuracy"] = (round(bucket["correct"] / bucket["total"], 6)
                              if bucket["total"] else None)
    return {"status": "GRADED", "transport": transport,
            "labelled_set": summary, "grades": per_field}
