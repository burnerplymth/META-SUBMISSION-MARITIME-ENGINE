"""Batch export bundle: a list of builder requests answered by one ZIP.

`POST /exports/bundle` (registered in routes.py) accepts a closed list of
items, each one a request to a builder that already exists:

  {"kind": "dataset", "id": "chain_events", "format": "csv"|"xlsx", "filters": {}}
  {"kind": "figure",  "id": "cascade_timeline", "format": "svg"|"csv"|"json"|"png"}
  {"kind": "report",  "template": "full"|"brief", "format": "html"|"md"|"pdf"}

This module holds the pure parts: body validation (closed keys, bounds), the
member file names and the manifest. The builders are the single-item routes'
own (CSV through `exports.to_csv_bytes`, the one formula guard; XLSX through
`exports.dataset_workbook`; figures through the chart engine; reports through
`centre.build_report`; PNG and PDF through the bounded Chromium subprocesses),
so a bundle holds exactly the bytes the single routes serve.

Bounds: at most MAX_ITEMS items, at most MAX_HEAVY of them PNG or PDF
(each costs a browser launch or a page render), at most MAX_BYTES in all.
Failures are listed in the manifest (`errors`), never dropped silently.
"""
from __future__ import annotations

import source_credits as _credits
import json
import re

from . import exports, pack
from .data import ReportError

MAX_ITEMS = 24
MAX_HEAVY = 4
MAX_BYTES = 48 * 1024 * 1024
SCHEMA = "exports-bundle/1"

_TOP = {"items", "run_id", "sim_run_id", "tick", "as_of", "rows_cap",
        "theme"}
_KINDS = {
    "dataset": ({"kind", "id", "format", "filters"}, {"csv", "xlsx"}),
    "figure": ({"kind", "id", "format", "theme", "scale"},
               {"svg", "csv", "json", "png"}),
    "report": ({"kind", "template", "sections", "format"},
               {"html", "md", "pdf"}),
}
_HEAVY = {("figure", "png"), ("report", "pdf")}
_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


def _fail(msg: str):
    raise ReportError("invalid_request", msg)


def check_body(body) -> dict:
    """Validate a bundle request. Returns the body with items normalised."""
    if not isinstance(body, dict):
        _fail("body must be a JSON object")
    extra = sorted(set(body) - _TOP)
    if extra:
        _fail(f"unknown field {extra[0]!r}")
    items = body.get("items")
    if not isinstance(items, list) or not items:
        _fail("items must be a non-empty list")
    if len(items) > MAX_ITEMS:
        _fail(f"at most {MAX_ITEMS} items per bundle")
    for key in ("run_id", "sim_run_id", "as_of"):
        v = body.get(key)
        if v is not None and not isinstance(v, str):
            _fail(f"{key} must be a string")
    theme = body.get("theme")
    if theme is not None and theme not in ("light", "dark", "auto"):
        _fail("theme must be light, dark or auto")
    heavy = 0
    out = []
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            _fail(f"items[{i}] must be an object")
        kind = it.get("kind")
        if kind not in _KINDS:
            _fail(f"items[{i}].kind must be one of {sorted(_KINDS)}")
        keys, formats = _KINDS[kind]
        extra = sorted(set(it) - keys)
        if extra:
            _fail(f"items[{i}]: unknown field {extra[0]!r}")
        fmt = it.get("format")
        if fmt not in formats:
            _fail(f"items[{i}].format must be one of {sorted(formats)}")
        if kind in ("dataset", "figure"):
            if not isinstance(it.get("id"), str) or not _ID.match(it["id"]):
                _fail(f"items[{i}].id must be a short identifier")
        else:
            t = it.get("template", "full")
            if not isinstance(t, str) or not _ID.match(t):
                _fail(f"items[{i}].template must be a short identifier")
            s = it.get("sections")
            if s is not None and (not isinstance(s, list) or len(s) > 64
                                  or not all(isinstance(x, str) for x in s)):
                _fail(f"items[{i}].sections must be a list of ids")
        if kind == "dataset" and not isinstance(it.get("filters", {}), dict):
            _fail(f"items[{i}].filters must be an object")
        if (kind, fmt) in _HEAVY:
            heavy += 1
        out.append(dict(it))
    if heavy > MAX_HEAVY:
        _fail(f"at most {MAX_HEAVY} PNG or PDF items per bundle")
    return {**body, "items": out}


def member_names(items: list[dict], run_id: str | None) -> list[str]:
    """One zip path per item, unique, in request order."""
    seen: dict[str, int] = {}
    names = []
    for it in items:
        if it["kind"] == "dataset":
            base, d = it["id"], "datasets"
        elif it["kind"] == "figure":
            base, d = it["id"], "figures"
        else:
            base = f"{run_id or 'run'}-{it.get('template', 'full')}"
            d = "reports"
        base = re.sub(r"[^A-Za-z0-9_.-]+", "-", base)
        name = f"{d}/{base}.{it['format']}"
        seen[name] = seen.get(name, 0) + 1
        if seen[name] > 1:
            name = f"{d}/{base}-{seen[name]}.{it['format']}"
        names.append(name)
    return names


def build_manifest(files: dict[str, bytes], meta: list[dict],
                   errors: list[dict], context: dict,
                   generated_at: str) -> dict:
    """The manifest; `files` lists every zip member except manifest.json
    with bytes and sha256, like the pack manifests."""
    return {
        "pack": "supermap-export-bundle", "schema": SCHEMA,
        "generated_at": generated_at, "context": context,
        "items": meta,
        "files": {n: {"bytes": len(b), "sha256": pack.sha256_hex(b)}
                  for n, b in sorted(files.items())},
        "credits": _credits.source_lines(_credits.doc_text(meta)),
        "errors": errors,
    }


def zip_with_manifest(files: dict[str, bytes], manifest: dict) -> bytes:
    body = (json.dumps(manifest, indent=2, sort_keys=True,
                       ensure_ascii=False) + "\n").encode("utf-8")
    return exports.zip_bytes({**files, "manifest.json": body})
