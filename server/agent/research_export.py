"""Export of a research note: HTML, PDF and JSON.

HTML is self-contained (one file, inline styles, inline SVG figures), in the
house report style (``reports.render``): title, question, label chips, the
verification chips, findings with their evidence ids and numbers, figures,
the data-used table, the evidence table (tool, arguments hash, analysis id,
n, result hash), limitations and methods. Findings are code-written sentences from checked
claims; any other model wording sits under "Context (unchecked)". PDF renders that HTML through the
existing reports pipeline (headless Chromium in a subprocess; when it is not
installed the caller gets ``PdfError`` and says so, the print-ready HTML
still works). JSON carries the note, the full evidence entries and the
hashes, so a reader can recompute that every number sits in an entry.

Figures: the research routes return a ``chart_spec`` per analysis (line, bar,
histogram, lag chart, segments); ``figure_spec`` maps it onto the report
ChartSpec and ``reports.charts.render_svg`` draws it. A spec that does not
map is left out; nothing is drawn from the model's text.
"""
from __future__ import annotations

import hashlib
import json

try:
    from reports import render as _rr
    from reports.charts import render_svg, caption as chart_caption
    from reports.charts.spec import SpecError
    from reports.pdf import PdfError, render_pdf
except ImportError:  # imported as server.agent.* from the repo root
    from server.reports import render as _rr  # type: ignore
    from server.reports.charts import render_svg, caption as chart_caption  # type: ignore
    from server.reports.charts.spec import SpecError  # type: ignore
    from server.reports.pdf import PdfError, render_pdf  # type: ignore

from .research_evidence import canonical, sha256_hex

_esc, _chip = _rr._esc, _rr._chip
MAX_FIGURE_POINTS = 1500
STATE_CHIP = {"verified": "Verified", "flagged": "Flagged"}


# ----------------------------------------------------------------- figures
def figure_spec(chart: dict, title: str, source: str) -> dict | None:
    """Map a research chart_spec onto a report ChartSpec, or None."""
    if not isinstance(chart, dict):
        return None
    ctype = chart.get("type")
    rtype = {"line": "line", "segments": "line", "bar": "bar", "histogram": "bar",
             "heatmap_lag": "bar"}.get(ctype)
    if rtype is None:
        return None
    series, total = [], 0
    simulated = False
    for s in chart.get("series") or []:
        pts = []
        for p in s.get("points") or []:
            if not isinstance(p, (list, tuple)) or len(p) < 2:
                continue
            x, y = p[0], p[1]
            if y is not None and not isinstance(y, (int, float)):
                continue
            pts.append({"x": (str(x) if rtype == "bar" else x), "y": y})
        if not pts:
            continue
        total += len(pts)
        sim = s.get("kind") == "simulated"
        simulated = simulated or sim
        series.append({"name": str(s.get("label") or s.get("id") or "series")[:80],
                       "source": source, "chip": "SIMULATED" if sim else "SNAPSHOT",
                       "points": pts})
    if not series or total > MAX_FIGURE_POINTS:
        return None
    names = [s["name"] for s in series]
    for i, n in enumerate(names):          # series names must be unique
        if names.count(n) > 1:
            series[i]["name"] = f"{n} {i + 1}"
    x = chart.get("x") or {}
    spec: dict = {"type": rtype, "title": title[:140], "chip": "SNAPSHOT", "series": series,
                  "axes": {"x": {k: str(x[k])[:80] for k in ("label", "unit") if x.get(k)}, "y": {}}}
    if rtype == "line":
        marks = []
        for m in chart.get("markers") or []:
            if isinstance(m, dict) and isinstance(m.get("x"), (int, float, str)) \
                    and not isinstance(m.get("x"), bool):
                marks.append({"kind": "marker", "x": m["x"], "label": str(m.get("label") or "marker")[:60]})
        spec["annotations"] = marks[:20]
    if simulated:
        spec["note"] = "Simulated values are drawn beside, not merged with, observed values."
    return spec


def render_figures(note: dict, evidence_by_id: dict) -> list[dict]:
    """[{evidence, title, svg, caption}] for the evidence the retained
    findings cite, in citation order, at most 6."""
    out, seen = [], set()
    for f in note.get("findings") or []:
        for eid in f.get("evidence") or []:
            e = evidence_by_id.get(eid)
            if not e or eid in seen or not e.get("figure"):
                continue
            seen.add(eid)
            title = f"{e['kind'].replace('_', ' ').capitalize()} ({eid})"
            spec = figure_spec(e["figure"], title, "Cached historical datasets (research workbench)")
            if spec is None:
                continue
            try:
                svg = render_svg(spec, embed=True, uid=f"rn-{eid.lower()}")
                cap = chart_caption(spec)
            except SpecError:
                continue
            out.append({"evidence": eid, "title": title, "svg": svg, "caption": cap})
            if len(out) >= 6:
                return out
    return out


# -------------------------------------------------------------------- HTML
def _num(v) -> str:
    if isinstance(v, float):
        return f"{v:,.6f}".rstrip("0").rstrip(".")
    if isinstance(v, int) and not isinstance(v, bool):
        return f"{v:,}"
    return str(v)


def render_html(note: dict, evidence: list[dict]) -> str:
    by_id = {e["id"]: e for e in evidence}
    ver = note.get("verification") or {}
    lab = note.get("label") or {}
    figs = render_figures(note, by_id)
    parts: list[str] = []
    chips = "".join(_chip(c) for c in [lab.get("state", "RECORDED"), "Descriptive statistics"])
    vchips = "".join(f'<span class="chip modelled">{_esc(c)}</span>' for c in ver.get("chips") or [])
    parts.append(f'<header><h1>{_esc(note.get("title"))}</h1>'
                 f'<p class="sub">{_esc(note.get("question"))}</p>'
                 f'<p>{chips}{vchips}</p>'
                 f'<p class="note">{_esc(lab.get("text"))} {_esc(lab.get("statistics"))}</p></header>')
    # findings
    items = []
    for f in note.get("findings") or []:
        rows = "".join(
            f'<tr><td>{_esc(n.get("name"))}</td><td class="num">{_esc(_num(n.get("value")))}</td>'
            f'<td>{_esc(n.get("unit"))}</td><td>{_esc(n.get("evidence"))}</td></tr>'
            for n in f.get("numbers") or [])
        tbl = ('<div class="tablewrap"><table><thead><tr><th>Quantity</th><th>Value</th>'
               f'<th>Unit</th><th>Evidence</th></tr></thead><tbody>{rows}</tbody></table></div>'
               if rows else "")
        state = STATE_CHIP.get(f.get("state"), f.get("state"))
        items.append(f'<li id="{_esc(f.get("id"))}"><p><strong>{_esc(f.get("id"))}</strong> '
                     f'{_chip(state)} {_esc(f.get("text"))} '
                     f'<span class="unit">[{_esc(", ".join(f.get("evidence") or []))}]</span></p>{tbl}</li>')
    parts.append('<section><h2>Findings</h2>'
                 + ('<ol style="list-style:none;padding-left:0">' + "".join(items) + "</ol>"
                    if items else '<p class="note">No finding passed the evidence check.</p>')
                 + "</section>")
    ctx = [c for c in note.get("context_unchecked") or [] if isinstance(c, dict)]
    if ctx:
        lis = "".join(
            f'<li id="{_esc(c.get("id"))}"><p><strong>{_esc(c.get("id"))}</strong> {_chip("Unchecked wording")} '
            f'{_esc(c.get("text"))}'
            + (f' <span class="unit">[{_esc(", ".join(c.get("evidence") or []))}]</span>' if c.get("evidence") else "")
            + "</p></li>" for c in ctx)
        parts.append('<section><h2>Context (unchecked)</h2><ol style="list-style:none;padding-left:0">'
                     f"{lis}</ol></section>")
    if figs:
        body = "".join(f'<figure class="fig" id="fig-{_esc(g["evidence"])}">{g["svg"]}'
                       f'<figcaption>{_esc(g["caption"])} Evidence {_esc(g["evidence"])}.</figcaption></figure>'
                       for g in figs)
        parts.append(f"<section><h2>Figures</h2>{body}</section>")
    # data used
    rows = "".join(
        f'<tr><td>{_esc(d.get("title"))}</td><td>{_esc(d.get("source"))}</td>'
        f'<td>{_esc(", ".join(d.get("places") or []))}</td>'
        f'<td>{_esc(d.get("first_obs") or "-")} to {_esc(d.get("last_obs") or "-")}</td>'
        f'<td>{_chip(str(d.get("state") or "").replace("_", " ").upper() or "NOT SERVED")}</td>'
        f'<td>{_esc(", ".join(d.get("evidence") or []))}</td></tr>'
        for d in note.get("data_used") or [])
    parts.append('<section><h2>Data used</h2><div class="tablewrap"><table><thead><tr>'
                 '<th>Dataset</th><th>Source</th><th>Places</th><th>Coverage</th><th>State</th>'
                 f'<th>Evidence</th></tr></thead><tbody>{rows or "<tr><td colspan=6>None.</td></tr>"}'
                 '</tbody></table></div></section>')
    # evidence
    erows = "".join(
        f'<tr><td>{_esc(e["id"])}</td><td>{_esc(e["tool"])}</td><td>{_esc(e["kind"])}</td>'
        f'<td><code>{_esc(e.get("analysis_id") or "-")}</code></td>'
        f'<td class="num">{_esc(_num(e["n"]) if e.get("n") is not None else "-")}</td>'
        f'<td><code>{_esc(e["args_sha256"])}</code></td>'
        f'<td><code>{_esc(str(e["result_sha256"])[:16])}</code></td>'
        f'<td><code>{_esc(json.dumps(e.get("args"), sort_keys=True))}</code></td></tr>'
        for e in (by_id[i] for i in note.get("evidence_ids") or [] if i in by_id))
    parts.append('<section><h2>Evidence</h2><div class="tablewrap"><table><thead><tr>'
                 '<th>Id</th><th>Tool</th><th>Kind</th><th>Analysis</th><th>n</th>'
                 '<th>Arguments hash</th><th>Result hash</th><th>Arguments</th></tr></thead>'
                 f'<tbody>{erows}</tbody></table></div></section>')
    parts.append("<section><h2>Limitations</h2><ul>"
                 + "".join(f"<li>{_esc(x)}</li>" for x in note.get("limitations") or []) + "</ul></section>")
    parts.append("<section><h2>Methods</h2><ul>"
                 + "".join(f"<li>{_esc(x)}</li>" for x in note.get("methods") or []) + "</ul></section>")
    parts.append(f'<p class="foot">Note {_esc(note.get("id"))}. Model {_esc(lab.get("model"))}, '
                 f'prompt {_esc(lab.get("prompt_id"))}. Each finding is a sentence written by code '
                 'from a claim checked against a listed evidence entry; context lines are not checked.</p>')
    return ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"<title>{_esc(note.get('title'))}</title><style>{_rr.CSS}</style></head>"
            "<body><main>" + "".join(parts) + "</main></body></html>\n")


# -------------------------------------------------------------------- JSON
def render_json(note: dict, evidence: list[dict]) -> str:
    ids = set(note.get("evidence_ids") or [])
    ev = [{k: v for k, v in e.items() if k != "figure"} | {
        "figure_sha256": sha256_hex(e["figure"]) if e.get("figure") else None}
        for e in evidence if e["id"] in ids]
    body = {k: v for k, v in note.items() if k != "source_signature"}
    doc = {"note": body, "evidence": ev,
           "hashes": {"note_sha256": sha256_hex(body),
                      "evidence_sha256": sha256_hex(ev),
                      "results": {e["id"]: e["result_sha256"] for e in ev},
                      "arguments": {e["id"]: e["args_sha256"] for e in ev}},
           "label": (note.get("label") or {}).get("statistics")}
    return json.dumps(doc, sort_keys=True, indent=1, ensure_ascii=False, default=str) + "\n"


def render_pdf_bytes(note: dict, evidence: list[dict]) -> bytes:
    return render_pdf(render_html(note, evidence).encode("utf-8"))


def filename(note: dict, fmt: str) -> str:
    return f"research_note_{note.get('id', 'note')}.{fmt}"


def content_hash(data: bytes | str) -> str:
    b = data.encode("utf-8") if isinstance(data, str) else data
    return hashlib.sha256(b).hexdigest()
