"""Source credits for the server: reads the same registry as the web app
(``data/sources/credits.json``, the single source of truth) and builds the source line
that every report, workbook, pack and dataset carries.

A credit is a display line, an optional statement of the transformation applied (IMF terms
ask for it next to the citation) and a licence. Matching is by whole words in the free
source strings the server already carries. Not legal advice.
"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FALLBACK = "Source: SupermAP stored data"


def _registry_path() -> Path:
    data = Path(os.environ.get("SUPERMAP_DATA_DIR", REPO_ROOT / "data"))
    p = data / "sources" / "credits.json"
    return p if p.exists() else REPO_ROOT / "data" / "sources" / "credits.json"


@lru_cache(maxsize=4)
def _load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def registry() -> dict:
    return _load(str(_registry_path()))


def credits() -> list[dict]:
    return list(registry()["sources"])


@lru_cache(maxsize=4)
def _patterns(path: str) -> tuple:
    out = []
    for c in _load(path)["sources"]:
        words = "|".join(re.escape(m) for m in c["match"])
        out.append((c, re.compile(rf"(?<![\w]){'(?:' + words + ')'}(?![\w])", re.I)))
    return tuple(out)


def credit_text(c: dict) -> str:
    return f"{c['line']} · {c['transformed']}" if c.get("transformed") else c["line"]


def credits_for(*texts) -> list[dict]:
    """Credits whose words occur in any of the strings (registry order, no repeats)."""
    hay = " \n ".join(t for t in texts if isinstance(t, str) and t)
    if not hay:
        return []
    return [c for c, rx in _patterns(str(_registry_path())) if rx.search(hay)]


def source_line(*texts) -> str:
    """One line: the credit text of every registered source named, else the stated source,
    else the stored-data line. Never empty."""
    hit = credits_for(*texts)
    if hit:
        return " | ".join(credit_text(c) for c in hit)
    own = next((t for t in texts if isinstance(t, str) and t.strip()), None)
    if own:
        return "Source: " + re.sub(r"^source:\s*", "", own.strip(), flags=re.I)
    return FALLBACK


def source_lines(*texts) -> list[str]:
    """The credit lines as a list (one per source) for a workbook or a text file."""
    hit = credits_for(*texts)
    return [credit_text(c) for c in hit] or [source_line(*texts)]


def doc_text(obj) -> str:
    """All strings of a nested document, for matching."""
    return json.dumps(obj, ensure_ascii=False, default=str)


def display(source) -> str:
    """A stated source as it should read in a caption: the credit text when it names a
    registered source, else the string unchanged."""
    if not isinstance(source, str):
        return str(source)
    hit = credits_for(source)
    return " | ".join(credit_text(c) for c in hit) if hit else source


def header_safe(text: str) -> str:
    """The line as an HTTP header value: ASCII only (the middle dot reads as a hyphen, the copyright sign as (c))."""
    t = text.replace("\u00b7", "-").replace("\u00a9", "(c)")
    return t.encode("ascii", "replace").decode("ascii")
