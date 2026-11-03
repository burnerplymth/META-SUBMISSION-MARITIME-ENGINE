"""Renderers for report documents: HTML (screen + print stylesheet),
Markdown, and the print view (HTML with the print profile forced on).
Pure functions of the document structure: no clock, no randomness, so
the same document renders to the same bytes.
"""
from __future__ import annotations

import html
import json
import source_credits as _credits

from reports import charts as _charts

CHIP_CLASS = {"SOURCED": "sourced", "SNAPSHOT": "snapshot",
              "MODELLED": "modelled", "SIMULATED": "simulated",
              "NOT SERVED": "notserved", "BACKTEST": "backtest",
              "LEDGER": "ledger"}

CSS = """
:root{--bg:#ffffff;--fg:#17202a;--muted:#5b6672;--rule:#d5dbe1;--card:#f6f8fa;
--sourced:#1b7a46;--snapshot:#2a5d9f;--modelled:#4b43a8;--simulated:#9a5b00;
--notserved:#6b7280;--backtest:#7a2f86;--ledger:#0f6b73}
@media (prefers-color-scheme:dark){:root:not([data-print]){--bg:#10151a;
--fg:#e6ebf0;--muted:#9aa6b2;--rule:#2a3540;--card:#18202a;--sourced:#58c58a;
--snapshot:#7fb0ee;--modelled:#a59cf2;--simulated:#e0a64a;--notserved:#9aa3ad;
--backtest:#d28be0;--ledger:#5cc6cf}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:980px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:1.7rem;margin:0 0 4px}
h2{font-size:1.25rem;margin:28px 0 8px;padding-top:12px;border-top:2px solid var(--rule)}
h3{font-size:1rem;margin:18px 0 6px}
.sub,.foot,.note{color:var(--muted);font-size:.88rem}
footer.sources ul{margin:4px 0 0;padding-left:1.1em;color:var(--muted);font-size:.8rem}
.chip{display:inline-block;font-size:.68rem;font-weight:700;letter-spacing:.04em;
padding:1px 6px;border:1px solid currentColor;border-radius:3px;margin-left:6px;
vertical-align:middle;white-space:nowrap}
.sourced{color:var(--sourced)}.snapshot{color:var(--snapshot)}
.modelled{color:var(--modelled)}.simulated{color:var(--simulated)}
.notserved{color:var(--notserved);border-style:dashed}
.backtest{color:var(--backtest)}.ledger{color:var(--ledger)}
.tablewrap{overflow-x:auto;margin:8px 0}
table{border-collapse:collapse;width:100%;font-size:.85rem}
th,td{border:1px solid var(--rule);padding:4px 6px;text-align:left;vertical-align:top;overflow-wrap:break-word}
th{background:var(--card)}
td.num{text-align:right;font-variant-numeric:tabular-nums}
.unit{color:var(--muted);font-weight:400}
dl.kv{display:grid;grid-template-columns:minmax(150px,26%) 1fr;gap:4px 12px;margin:8px 0}
dl.kv dt{color:var(--muted)}dl.kv dd{margin:0;overflow-wrap:anywhere}
pre{background:var(--card);border:1px solid var(--rule);padding:8px;
overflow-x:auto;font-size:.78rem}
.locked{font-size:.7rem;color:var(--muted);margin-left:8px}
nav.toc{background:var(--card);border:1px solid var(--rule);padding:8px 12px;
margin:12px 0}
nav.toc ol{margin:0;padding-left:20px;columns:2}
.printbar{background:var(--card);border:1px solid var(--rule);padding:6px 10px;
margin:10px 0;font-size:.85rem}
@media (max-width:600px){dl.kv{grid-template-columns:1fr}
dl.kv dt{margin-top:6px}nav.toc ol{columns:1}}
@page{size:A4;margin:16mm 14mm}
@media print{
:root{--bg:#fff;--fg:#000;--muted:#444;--rule:#999;--card:#eee}
body{font-size:10.5pt}main{max-width:none;padding:0}
body.compact{font-size:8.6pt;line-height:1.3}
body.compact h1{font-size:15pt}
body.compact table{font-size:7.4pt}
body.compact th,body.compact td{padding:1px 3px}
body.compact h2{margin:9px 0 3px;padding-top:6px;font-size:11pt}
body.compact h3{margin:6px 0 2px;font-size:9pt}
body.compact p{margin:3px 0}
body.compact .chip{font-size:.5rem;padding:0 3px;margin-left:3px}
body.compact dl.kv{gap:1px 10px;margin:4px 0}
body.compact .tablewrap{margin:3px 0}
body.compact figure.fig{margin:4px 0}
body.compact figure.fig figcaption{font-size:7pt;margin-top:1px}
body.compact .note{margin:2px 0;font-size:7.5pt}
h2{break-after:avoid;page-break-after:avoid}
table,pre,dl.kv{break-inside:auto}
tr,.chip{break-inside:avoid;page-break-inside:avoid}
thead{display:table-header-group}
nav.toc,.printbar{display:none}
a{color:inherit;text-decoration:none}
}
"""


CSS = CSS + _charts.page_css()


def _esc(x) -> str:
    return html.escape("" if x is None else str(x), quote=True)


def _chip(chip) -> str:
    return (f'<span class="chip {CHIP_CLASS.get(chip, "notserved")}">'
            f'{_esc(chip)}</span>')


def _val_html(v):
    """-> (html, is_numeric)"""
    if isinstance(v, dict) and "chip" in v:
        inner = v.get("v")
        if inner is None:
            return f'<span class="notserved">-</span>{_chip("NOT SERVED")}', False
        return f"{_esc(inner)}{_chip(v['chip'])}", isinstance(
            inner, (int, float)) and not isinstance(inner, bool)
    if v is None:
        return f'<span class="notserved">-</span>{_chip("NOT SERVED")}', False
    return _esc(v), isinstance(v, (int, float)) and not isinstance(v, bool)


def column_chips(b) -> list:
    """Per column: the single chip shared by every served value in it
    (shown once in the header), or None when the chips vary (shown per
    cell). NOT SERVED cells always show their own chip."""
    out = []
    for i in range(len(b["columns"])):
        chips = {row[i]["chip"] for row in b["rows"]
                 if isinstance(row[i], dict) and "chip" in row[i]
                 and row[i].get("v") is not None}
        out.append(next(iter(chips)) if len(chips) == 1 else None)
    return out


def _cell_html(v, col_chip):
    if col_chip is not None and isinstance(v, dict) and "chip" in v \
            and v.get("v") is not None:
        return _esc(v["v"]), isinstance(v["v"], (int, float)) \
            and not isinstance(v["v"], bool)
    return _val_html(v)


def _figure_links(b):
    cid = b["chart_id"]
    return (f"charts/{cid}.svg", f"charts/{cid}.csv",
            f"/exports/chart/{cid}.csv?run_id={b['run_id']}")


def _block_html(b, nums=None) -> str:
    t = b["t"]
    if t == "figure":
        n = (nums or {}).get(b["chart_id"], 0)
        svg = _charts.render_svg(b["spec"], embed=True,
                                 uid="f-" + b["chart_id"])
        _, csv_f, csv_r = _figure_links(b)
        cap = _charts.caption(b["spec"])
        return (f'<figure class="fig" id="fig-{_esc(b["chart_id"])}">{svg}'
                f'<figcaption>Figure {n}. {_esc(cap)} Data behind this '
                f'figure: <code>{_esc(csv_f)}</code> in the data pack, or '
                f'<code>{_esc(csv_r)}</code>.</figcaption></figure>')
    if t == "p":
        return f"<p>{_esc(b['text'])}</p>"
    if t == "list":
        return "<ul>" + "".join(f"<li>{_esc(i)}</li>"
                                for i in b["items"]) + "</ul>"
    if t == "code":
        return f"<pre>{_esc(b['text'])}</pre>"
    if t == "kv":
        title = f"<h3>{_esc(b['title'])}</h3>" if b.get("title") else ""
        rows = "".join(f"<dt>{_esc(k)}</dt><dd>{_val_html(v)[0]}</dd>"
                       for k, v in b["rows"])
        return f'{title}<dl class="kv">{rows}</dl>'
    if t == "table":
        title = f"<h3>{_esc(b['title'])}</h3>" if b.get("title") else ""
        cc = column_chips(b)
        head = "".join(
            f"<th>{_esc(c['label'])}" + (
                f' <span class="unit">[{_esc(c["unit"])}]</span>'
                if c.get("unit") else "")
            + (_chip(cc[i]) if cc[i] else "") + "</th>"
            for i, c in enumerate(b["columns"]))
        body = []
        for row in b["rows"]:
            cells = []
            for i, v in enumerate(row):
                h, num = _cell_html(v, cc[i])
                cells.append(f'<td class="num">{h}</td>' if num
                             else f"<td>{h}</td>")
            body.append("<tr>" + "".join(cells) + "</tr>")
        if not body:
            body.append(f'<tr><td colspan="{len(b["columns"])}">'
                        f'No rows.</td></tr>')
        cap = ""
        if b["shown"] != b["of"]:
            cap = (f'<p class="note">Showing {b["shown"]} of {b["of"]} '
                   f'rows; the rest are cut to keep the report readable '
                   f'(the data pack carries them).</p>')
        note = f'<p class="note">{_esc(b["note"])}</p>' if b.get("note") else ""
        return (f'{title}<div class="tablewrap"><table><thead><tr>{head}'
                f'</tr></thead><tbody>{"".join(body)}</tbody></table></div>'
                f'{cap}{note}')
    raise ValueError(f"unknown block type {t!r}")


def figure_numbers(doc: dict) -> dict:
    """{chart_id: n}: figures numbered in document order."""
    nums = {}
    for s in doc["sections"]:
        for b in s["blocks"]:
            if b["t"] == "figure" and b["chart_id"] not in nums:
                nums[b["chart_id"]] = len(nums) + 1
    return nums


def source_lines_of(doc: dict) -> list:
    """The credit lines of every registered source the document names (never empty)."""
    return _credits.source_lines(_credits.doc_text(doc))


def source_footer_html(doc: dict) -> str:
    items = "".join(f"<li>{_esc(t)}</li>" for t in source_lines_of(doc))
    return f'<footer class="sources" aria-label="Sources"><ul>{items}</ul></footer>'


def render_html(doc: dict, print_view: bool = False) -> str:
    secs = doc["sections"]
    toc = ""
    if len(secs) > 3:
        toc = ('<nav class="toc" aria-label="Sections"><ol>' + "".join(
            f'<li><a href="#s-{_esc(s["id"])}">{_esc(s["title"])}</a></li>'
            for s in secs) + "</ol></nav>")
    nums = figure_numbers(doc)
    parts = []
    for s in secs:
        lock = '<span class="locked">always included</span>' \
            if s["locked"] else ""
        parts.append(
            f'<section id="s-{_esc(s["id"])}"><h2>{_esc(s["title"])}'
            f'{_chip(s["chip"])}{lock}</h2>'
            + "".join(_block_html(b, nums) for b in s["blocks"])
            + "</section>")
    bar = ('<p class="printbar">Print view: use the browser print dialog '
           '(Save as PDF) for a paginated copy.</p>' if print_view else "")
    attr = ' data-print="1"' if print_view else ""
    return (
        "<!doctype html>\n"
        f'<html lang="en"{attr}><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>{_esc(doc["title"])}</title><style>{CSS}</style></head>'
        f'<body{" class=compact" if doc.get("compact") else ""}><main><header><h1>{_esc(doc["title"])}</h1>'
        f'<p class="sub">{_esc(doc["subtitle"])}</p></header>{bar}{toc}'
        + "".join(parts) +
        '<p class="foot">Generated by SupermAP from stored data. The same '
        'inputs give the same document; the build time is recorded in the '
        'data-pack manifest, not here.</p>'
        + source_footer_html(doc) + '</main></body></html>\n')


# ---------------------------------------------------------------- markdown
def _md_cell(v) -> str:
    if isinstance(v, dict) and "chip" in v:
        inner = v.get("v")
        if inner is None:
            return "- [NOT SERVED]"
        return f"{_md_esc(inner)} [{v['chip']}]"
    if v is None:
        return "- [NOT SERVED]"
    return _md_esc(v)


def _md_esc(x) -> str:
    return str(x).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _md_cell_col(v, col_chip) -> str:
    if col_chip is not None and isinstance(v, dict) and "chip" in v \
            and v.get("v") is not None:
        return _md_esc(v["v"])
    return _md_cell(v)


def _block_md(b, nums=None) -> str:
    t = b["t"]
    if t == "figure":
        n = (nums or {}).get(b["chart_id"], 0)
        svg_f, csv_f, csv_r = _figure_links(b)
        alt = b["spec"]["title"].replace("[", "(").replace("]", ")")
        return (f"![{alt}]({svg_f})\n\n*Figure {n}. "
                f"{_charts.caption(b['spec'])} Files: `{svg_f}` and "
                f"`{csv_f}` in the data pack, or `{csv_r}`.*\n")
    if t == "p":
        return b["text"] + "\n"
    if t == "list":
        return "\n".join(f"- {i}" for i in b["items"]) + "\n"
    if t == "code":
        return "```json\n" + b["text"] + "\n```\n"
    if t == "kv":
        head = f"### {b['title']}\n\n" if b.get("title") else ""
        return head + "\n".join(f"- **{k}:** {_md_cell(v)}"
                                for k, v in b["rows"]) + "\n"
    if t == "table":
        head = f"### {b['title']}\n\n" if b.get("title") else ""
        cc = column_chips(b)
        cols = [c["label"] + (f" [{c['unit']}]" if c.get("unit") else "")
                + (f" [{cc[i]}]" if cc[i] else "")
                for i, c in enumerate(b["columns"])]
        lines = ["| " + " | ".join(_md_esc(c) for c in cols) + " |",
                 "|" + "|".join("---" for _ in cols) + "|"]
        for row in b["rows"]:
            lines.append("| " + " | ".join(
                _md_cell_col(v, cc[i]) for i, v in enumerate(row)) + " |")
        out = head + "\n".join(lines) + "\n"
        if b["shown"] != b["of"]:
            out += (f"\n*Showing {b['shown']} of {b['of']} rows; the rest "
                    f"are cut (the data pack carries them).*\n")
        if b.get("note"):
            out += f"\n*{b['note']}*\n"
        return out
    raise ValueError(f"unknown block type {t!r}")


def render_md(doc: dict) -> str:
    nums = figure_numbers(doc)
    out = [f"# {doc['title']}", "", f"*{doc['subtitle']}*", ""]
    for s in doc["sections"]:
        lock = " (always included)" if s["locked"] else ""
        out += [f"## {s['title']} [{s['chip']}]{lock}", ""]
        for b in s["blocks"]:
            out += [_block_md(b, nums).rstrip("\n"), ""]
    out += ["---", "Generated by SupermAP from stored data. The same inputs "
            "give the same document; the build time is recorded in the "
            "data-pack manifest, not here.", ""]
    out += ["Sources:"] + [f"- {t}" for t in source_lines_of(doc)] + [""]
    return "\n".join(out)


def render_json(doc: dict) -> str:
    return json.dumps(doc, sort_keys=True, indent=1, ensure_ascii=False) \
        + "\n"
