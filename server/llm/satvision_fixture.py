"""Fixture transport for the ``satvision`` prompt (vision read of one crop).

A fixture cannot look at an image, so it never invents a read: it serves a
REAL reply recorded from a live Muse Spark call, and only for the exact crop
(by sha256 of its bytes) that call was made on. Any other crop raises
``NoRecordedRead`` (the route reports NOT SERVED). The manifest lives beside
the satellite fixtures: server/satwatch/fixtures/vision/manifest.json.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_DIR = "SUPERMAP_SATVISION_FIXTURES"


class NoRecordedRead(RuntimeError):
    """No recorded vision read exists for this crop."""


def fixtures_dir() -> Path:
    return Path(os.environ.get(ENV_DIR) or REPO_ROOT / "server" / "satwatch" / "fixtures" / "vision")


def load_manifest(directory: Path | None = None) -> list[dict]:
    d = Path(directory or fixtures_dir())
    try:
        doc = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    entries = doc.get("entries") if isinstance(doc, dict) else None
    return [e for e in entries or [] if isinstance(e, dict)]


def recorded_entry(sha256: str, directory: Path | None = None) -> dict | None:
    for e in load_manifest(directory):
        if e.get("crop_sha256") == sha256:
            return e
    return None


def recorded_reply(sha256: str, directory: Path | None = None) -> dict:
    """The recorded reply document {text, usage, model, recorded_at, ...}."""
    d = Path(directory or fixtures_dir())
    entry = recorded_entry(sha256, d)
    if entry is None:
        raise NoRecordedRead("no recorded vision read for this crop")
    try:
        doc = json.loads((d / entry["reply"]).read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError) as exc:
        raise NoRecordedRead("the recorded vision read is unreadable") from exc
    doc["_entry"] = entry
    return doc


def fixture_satvision_json(payload: dict) -> str:
    sha = payload.get("image_sha256") if isinstance(payload, dict) else None
    if not isinstance(sha, str):
        raise NoRecordedRead("no crop identity in the payload")
    return recorded_reply(sha)["text"]
