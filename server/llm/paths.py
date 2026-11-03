"""Where the LLM layer keeps RUNTIME state.

``explain_cache.json`` and ``spend_ledger.json`` are never written next to
the source files (``server/llm/``), so a scratch run cannot rewrite TRACKED
files. Every runtime write goes under the data root:

    <SUPERMAP_DATA_DIR or <repo>/data>/llm/explain_cache.json
    <SUPERMAP_DATA_DIR or <repo>/data>/llm/spend_ledger.json

with explicit per-file overrides (``SUPERMAP_EXPLAIN_CACHE_PATH``,
``SUPERMAP_LLM_SPEND_PATH``). The tracked ``server/llm/explain_cache.json``
is a READ-ONLY seed: the explain cache consults it on a miss in the
runtime file and never writes it. ``server/llm/spend_ledger.json`` is
not tracked (it is in .gitignore); nothing reads or writes it.
"""
from __future__ import annotations

import os
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parent
REPO_ROOT = MODULE_DIR.parents[1]

# Tracked, read-only seed fixtures (never written by any code path).
SEED_EXPLAIN_CACHE = MODULE_DIR / "explain_cache.json"


def data_root() -> Path:
    return Path(os.environ.get("SUPERMAP_DATA_DIR") or REPO_ROOT / "data")


def runtime_dir() -> Path:
    return data_root() / "llm"


def default_explain_cache_path() -> Path:
    return Path(os.environ.get("SUPERMAP_EXPLAIN_CACHE_PATH")
                or runtime_dir() / "explain_cache.json")


def default_spend_path() -> Path:
    return Path(os.environ.get("SUPERMAP_LLM_SPEND_PATH")
                or runtime_dir() / "spend_ledger.json")


def default_spotcheck_path() -> Path:
    """The last classifier spot-check result (written by
    ``python -m llm.spotcheck --save``; read by ``LLMProvider.status``)."""
    return Path(os.environ.get("SUPERMAP_LLM_SPOTCHECK_PATH")
                or runtime_dir() / "spotcheck_last.json")
