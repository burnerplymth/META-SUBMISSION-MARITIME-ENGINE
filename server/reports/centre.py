"""Report centre: template + section checklist -> a deterministic
document in HTML, Markdown, or print view (and structured JSON).

Determinism law: build(run, template, sections, format) is a pure
function of stored inputs. The document carries no wall-clock time; the
build time lives in the API response / data-pack manifest only.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from . import render
from .data import ReportError, RunBundle
from .sections import LOCKED, ORDER, REGISTRY, TEMPLATES

FORMATS = ("html", "md", "pdf-print-view", "json")
CSS_BYTES = 10300      # inline stylesheet (report + figure rules) + shell
DEFAULT_STATS = {"events": 30, "places": 20, "money": 10, "ledger": 2,
                 "top": 5}


@dataclass
class Report:
    content: bytes
    media_type: str
    filename: str
    meta: dict


def template_ids():
    return list(TEMPLATES)


def resolve_sections(template: str, sections) -> tuple[list[str], bool]:
    """-> (ordered section ids, locked_added). Unknown ids raise."""
    # str check first: `[] in {dict}` is a TypeError (a 500), not a 422
    if not isinstance(template, str) or template not in TEMPLATES:
        raise ReportError("invalid_request",
                          f"template must be one of {template_ids()}")
    if sections is None:
        chosen = list(TEMPLATES[template]["sections"])
    else:
        if not isinstance(sections, list) or not all(
                isinstance(s, str) for s in sections):
            raise ReportError("invalid_request",
                              "sections must be a list of section ids")
        bad = [s for s in sections if s not in REGISTRY]
        if bad:
            raise ReportError(
                "invalid_request",
                f"unknown section {bad[0]!r}; known: {ORDER}")
        chosen = list(sections)
    added = False
    for locked in LOCKED:
        if locked not in chosen:
            chosen.append(locked)
            added = True
    return [s for s in ORDER if s in chosen], added


def stats_of(bundle: RunBundle | None) -> dict:
    if bundle is None:
        return dict(DEFAULT_STATS)
    on_chain = set(bundle.chain_place_ids())
    money = [m for m in (bundle.exposures or {}).get("mappings", [])
             if m.get("node_id") in on_chain]
    return {"events": len(bundle.events), "places": len(bundle.place_rows),
            "money": len(money), "ledger": len(bundle.ledger_entries()),
            "top": 5}


def estimate_bytes(template: str, section_ids: list[str],
                   stats: dict | None = None) -> int:
    """ESTIMATE of the HTML size: stylesheet/shell + per-section base +
    per-row cost x rows the template's caps allow."""
    stats = stats or DEFAULT_STATS
    caps = TEMPLATES[template]["caps"]
    total = CSS_BYTES
    for sid in section_ids:
        spec = REGISTRY[sid]
        n = 0
        sc = spec["scales"]
        if sc == "events":
            n = min(stats["events"], caps.get("timeline") or 10 ** 9)
        elif sc == "places":
            n = min(stats["places"], caps.get("places") or 10 ** 9)
        elif sc == "money":
            n = min(stats["money"], caps.get("money") or 10 ** 9)
        elif sc == "ledger":
            n = stats["ledger"]
        elif sc == "top":
            n = min(stats["places"], caps.get("exec_top", 5))
        total += spec["base"] + spec["per"] * n
        fig = spec.get("fig") or {}
        total += fig.get(template, 0) + (
            fig.get("per", 0) * min(n, caps.get("cascade_rows", n))
            if sc == "events" else 0)
    return int(total)


def templates_payload(bundle: RunBundle | None = None) -> dict:
    stats = stats_of(bundle)
    out = []
    for tid, t in TEMPLATES.items():
        out.append({
            "id": tid, "title": t["title"], "description": t["description"],
            "caps": t["caps"],
            "sections": [{
                "id": sid, "title": REGISTRY[sid]["title"],
                "chip": REGISTRY[sid]["chip"],
                "locked": bool(REGISTRY[sid].get("locked")),
                "default_on": sid in t["sections"],
                "estimate_bytes": estimate_bytes(tid, [sid], stats)
                - CSS_BYTES} for sid in ORDER],
            "estimate_bytes_default": estimate_bytes(
                tid, list(t["sections"]), stats),
            "estimate_note": "ESTIMATE of the HTML size from per-section "
                             "base cost and per-row cost under the "
                             "template's row caps; the build returns the "
                             "actual size.",
        })
    return {"schema": "reports/1", "formats": list(FORMATS),
            "stats_basis": ("run" if bundle is not None
                            else "typical run (30 events, 20 places)"),
            "templates": out}


def build_document(bundle: RunBundle, template: str, section_ids: list[str]
                   ) -> dict:
    caps = TEMPLATES[template]["caps"]
    secs = [REGISTRY[sid]["build"](bundle, caps) for sid in section_ids]
    j = bundle.journal["disruption"]
    return {
        "schema": "reports/1", "template": template,
        "title": f"SupermAP {TEMPLATES[template]['title']}: "
                 f"{bundle.name(j['node_id'])} {j.get('kind')}",
        "subtitle": f"Run {bundle.run_id}",
        "run_id": bundle.run_id,
        "compact": bool(caps.get("compact")),
        "sections": secs,
    }


def build_report(bundle: RunBundle, template: str = "full",
                 sections=None, fmt: str = "html") -> Report:
    if fmt not in FORMATS:
        raise ReportError("invalid_request",
                          f"format must be one of {list(FORMATS)}")
    ids, added = resolve_sections(template, sections)
    doc = build_document(bundle, template, ids)
    if fmt == "html":
        data, mt, ext = render.render_html(doc).encode("utf-8"), \
            "text/html; charset=utf-8", "html"
    elif fmt == "pdf-print-view":
        data, mt, ext = render.render_html(doc, print_view=True).encode(
            "utf-8"), "text/html; charset=utf-8", "print.html"
    elif fmt == "md":
        data, mt, ext = render.render_md(doc).encode("utf-8"), \
            "text/markdown; charset=utf-8", "md"
    else:
        data, mt, ext = render.render_json(doc).encode("utf-8"), \
            "application/json", "json"
    meta = {"run_id": bundle.run_id, "template": template, "format": fmt,
            "sections": ids, "locked_sections_added": added,
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "estimate_bytes": estimate_bytes(template, ids, stats_of(bundle))
            if fmt != "json" else None}
    return Report(data, mt, f"{bundle.run_id}-{template}.{ext}", meta)
