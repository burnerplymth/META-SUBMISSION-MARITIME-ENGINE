"""Self-contained HTML for a brief (also the source of the PDF).

One file: inline CSS (light, dark, print), inline SVG figures, no script, no
external request. Header with title, date, run id and hash, label chips; a
key-figures strip; sections; the sources list with links; the fact register;
a footer with the method line and the content sha256. A pure function of the
document, so the same brief renders to the same bytes.
"""
from __future__ import annotations

import html

from . import fmt

try:
    from reports.charts import page_css as _chart_css
except ImportError:  # imported as server.* from the repo root
    from server.reports.charts import page_css as _chart_css  # type: ignore

CHIP_CLASS = {"INPUT": "input", "MODELLED": "modelled", "SIMULATED": "simulated",
              "SOURCED": "sourced", "SNAPSHOT": "snapshot", "LIVE": "live",
              "NOT SERVED": "notserved", "RECORDED": "recorded"}

CSS = """
:root{--bg:#ffffff;--paper:#ffffff;--fg:#14181d;--fg2:#3a444e;--muted:#5d6872;--rule:#d4d9df;
--rule2:#e6e9ed;--strip:#f3f5f7;--accent:#1d4f91;--accent-bg:#e8eef6;
--input:#44505c;--modelled:#4b43a8;--simulated:#8f5300;--sourced:#16703f;--snapshot:#245a9a;
--live:#16703f;--notserved:#68717b;--recorded:#8f5300;--ok:#16703f;--warn:#8f5300}
@media (prefers-color-scheme:dark){:root:not([data-print]){--bg:#0e1319;--paper:#121920;--fg:#e7ebef;
--fg2:#c4ccd4;--muted:#98a4af;--rule:#2a343e;--rule2:#1f2832;--strip:#18212a;--accent:#86b4ec;
--accent-bg:#172536;--input:#aab6c2;--modelled:#a9a1f3;--simulated:#e2a94f;--sourced:#5cc88e;
--snapshot:#86b4ec;--live:#5cc88e;--notserved:#9aa4af;--recorded:#e2a94f;--ok:#5cc88e;--warn:#e2a94f}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 Inter,system-ui,-apple-system,
"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;font-variant-numeric:tabular-nums}
.note{max-width:900px;margin:0 auto;padding:28px 16px 40px;background:var(--paper)}
.mast{border-bottom:2px solid var(--fg);padding-bottom:12px;margin-bottom:14px}
.kicker{display:flex;gap:8px;flex-wrap:wrap;align-items:center;font-size:11px;letter-spacing:.08em;
text-transform:uppercase;color:var(--muted);font-weight:600}
.kicker .sp{flex:1}
h1{font:600 27px/1.2 "Iowan Old Style","Palatino Linotype",Palatino,Georgia,"Liberation Serif",
"Times New Roman",serif;margin:8px 0 6px;letter-spacing:-.005em}
.deck{font-size:15px;line-height:1.5;color:var(--fg2);margin:0 0 10px;max-width:70ch}
dl.meta{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:6px 16px;margin:10px 0 0}
dl.meta div{min-width:0}
dl.meta dt{font-size:10px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);font-weight:600}
dl.meta dd{margin:1px 0 0;font-size:12.5px;overflow-wrap:anywhere}
.chip{display:inline-block;font-size:10px;font-weight:700;letter-spacing:.05em;padding:0 5px;line-height:16px;
border:1px solid currentColor;border-radius:3px;white-space:nowrap;vertical-align:middle}
.chip.input{color:var(--input)}.chip.modelled{color:var(--modelled)}.chip.simulated{color:var(--simulated)}
.chip.sourced{color:var(--sourced)}.chip.snapshot{color:var(--snapshot)}.chip.live{color:var(--live)}
.chip.notserved{color:var(--notserved);border-style:dashed}.chip.recorded{color:var(--recorded)}
.chip.ok{color:var(--ok)}.chip.warn{color:var(--warn)}
.strip{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));border:1px solid var(--rule);
background:var(--strip);margin:14px 0 6px}
.strip .kf{padding:9px 12px;border-right:1px solid var(--rule);min-width:0}
.strip .kf:last-child{border-right:0}
.kf .l{font-size:10px;letter-spacing:.07em;text-transform:uppercase;color:var(--muted);font-weight:600}
.kf .v{font-size:21px;font-weight:600;line-height:1.2;margin-top:2px;overflow-wrap:anywhere}
.kf .c{margin-top:3px}
section.sec{margin-top:18px;break-inside:auto}
h2{font-size:12px;letter-spacing:.09em;text-transform:uppercase;font-weight:700;margin:0 0 6px;padding:5px 0;
border-top:1px solid var(--fg);display:flex;gap:8px;align-items:center;flex-wrap:wrap;break-after:avoid;page-break-after:avoid}
h2 .n{color:var(--muted);font-weight:600;min-width:1.4em}
h2 .chips{margin-left:auto;display:inline-flex;gap:4px}
p{margin:0 0 6px;max-width:80ch}
ul.items{margin:2px 0 8px;padding-left:18px;max-width:80ch}
ul.items li{margin:2px 0}
.fact{border-bottom:1px dotted var(--muted);white-space:nowrap}
.fact sup.fid{font-size:8px;color:var(--muted);margin-left:1px;font-weight:600;letter-spacing:.02em}
.tablewrap{overflow-x:auto;margin:8px 0}
table{border-collapse:collapse;width:100%;font-size:12px}
caption{caption-side:top;text-align:left;font-size:10.5px;letter-spacing:.06em;text-transform:uppercase;
color:var(--muted);font-weight:600;padding:2px 0 4px}
th,td{padding:3px 8px;border-bottom:1px solid var(--rule2);text-align:left;vertical-align:top}
th{font-size:10.5px;letter-spacing:.05em;text-transform:uppercase;color:var(--muted);font-weight:600;
border-bottom:1px solid var(--rule);background:transparent}
td.num,th.num{text-align:right}
th .u{font-weight:400;text-transform:none;letter-spacing:0}
tr{break-inside:avoid;page-break-inside:avoid}
.note-line{font-size:11px;color:var(--muted);margin:2px 0 6px}
.ns{display:flex;gap:8px;align-items:center;color:var(--muted);font-size:12.5px;margin:2px 0 6px}
figure.fig{margin:10px 0}
figure.fig figcaption{font-size:11px;color:var(--muted);margin-top:4px}
ol.sources{margin:4px 0;padding-left:0;list-style:none;font-size:12px;counter-reset:s}
ol.sources li{display:grid;grid-template-columns:2.6em 1fr;gap:0 4px;padding:3px 0;border-bottom:1px solid var(--rule2)}
ol.sources .sid{color:var(--muted);font-weight:600}
ol.sources .refs{color:var(--muted);font-size:11px}
a{color:var(--accent);text-decoration:none;overflow-wrap:anywhere}
a:hover{text-decoration:underline}
table.reg{font-size:10.5px}
table.reg td.id{font-weight:600;white-space:nowrap}
.foot{margin-top:22px;border-top:1px solid var(--rule);padding-top:8px;font-size:11px;color:var(--muted)}
.foot code{font:11px/1.4 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;overflow-wrap:anywhere}
.appendix{margin-top:22px}
@media (max-width:600px){.note{padding:16px}h1{font-size:22px}.strip .kf{border-right:0;border-bottom:1px solid var(--rule)}}
@page{size:A4;margin:13mm 13mm 15mm}
@media print{
:root{--bg:#fff;--paper:#fff;--fg:#000;--fg2:#222;--muted:#4a525a;--rule:#9aa3ab;--rule2:#d5d9dd;--strip:#f2f4f6;
--accent:#1d4f91;--input:#44505c;--modelled:#4b43a8;--simulated:#8f5300;--sourced:#16703f;--snapshot:#245a9a;
--live:#16703f;--notserved:#68717b;--recorded:#8f5300}
body{font-size:8.9pt;line-height:1.38}
.note{max-width:none;padding:0}
h1{font-size:20pt;margin:5px 0 4px}
.deck{font-size:10pt;margin-bottom:6px}
.mast{padding-bottom:8px;margin-bottom:8px}
dl.meta dd{font-size:8.5pt}
.kf .v{font-size:14pt}
.strip{margin:8px 0 2px}.strip .kf{padding:5px 8px}
section.sec{margin-top:10px}
h2{font-size:8.2pt;padding:3px 0;margin-bottom:4px}
table{font-size:7.9pt}th,td{padding:1.5px 6px}th{font-size:6.9pt}
ul.items{margin:1px 0 4px}
p{margin-bottom:3px}
figure.fig{margin:6px 0}
.appendix{break-before:page}
table.reg{font-size:7pt}
a{color:inherit}
}
"""


def _e(x) -> str:
    return html.escape("" if x is None else str(x), quote=True)


def chip(label: str, cls: str | None = None) -> str:
    c = cls or CHIP_CLASS.get(label, "notserved")
    return f'<span class="chip {c}">{_e(label)}</span>'


def _segments(segs: list[dict], brief_id: str) -> str:
    out = []
    for s in segs:
        if "fact" in s:
            fid = _e(s["fact"])
            out.append(f'<span class="fact" data-fact="{fid}" data-prov="brief:{_e(brief_id)}:{fid}">'
                       f'{_e(s["t"])}<sup class="fid">{fid}</sup></span>')
        else:
            out.append(_e(s["t"]))
    return "".join(out)


def _link(url, text) -> str:
    u = fmt.safe_url(url)
    if not u:
        return _e(text)
    return f'<a href="{_e(u)}" rel="noopener noreferrer" target="_blank">{_e(text)}</a>'


def _table(b: dict, brief_id: str) -> str:
    cols = b["columns"]
    head = "".join(
        f'<th class="{"num" if c.get("num") else ""}">{_e(c["label"])}'
        + (f' <span class="u">[{_e(c["unit"])}]</span>' if c.get("unit") else "") + "</th>"
        for c in cols)
    body = []
    for r in b["rows"]:
        cells = []
        for i, v in enumerate(r["cells"]):
            cls = "num" if cols[i].get("num") else ""
            if r.get("state") and cols[i]["label"] == "State":
                cells.append(f'<td class="{cls}">{chip(r["state"])}</td>')
            else:
                inner = _e(v)
                if i == 0 and r.get("fact_id"):
                    inner += f'<sup class="fid" style="font-size:8px;color:var(--muted);margin-left:2px">{_e(r["fact_id"])}</sup>'
                cells.append(f'<td class="{cls}">{inner}</td>')
        body.append("<tr>" + "".join(cells) + "</tr>")
    cap = f"<caption>{_e(b['title'])}" + (f" {chip(b['chip'])}" if b.get("chip") else "") + "</caption>"
    note = f'<p class="note-line">{_e(b["note"])}</p>' if b.get("note") else ""
    return (f'<div class="tablewrap"><table>{cap}<thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>{note}')


def _block(b: dict, doc: dict, fig_nums: dict, figs: dict) -> str:
    k = b["kind"]
    bid = doc["brief_id"]
    if k == "paragraph":
        return f"<p>{_segments(b['segments'], bid)}</p>"
    if k == "bullets":
        lis = "".join(f"<li>{_segments(i['segments'], bid)}</li>" for i in b["items"])
        return f'<ul class="items">{lis}</ul>'
    if k == "table":
        return _table(b, bid)
    if k == "figure":
        f = figs.get(b["figure_id"])
        if not f:
            return ""
        n = fig_nums[b["figure_id"]]
        return (f'<figure class="fig" id="fig-{_e(f["id"])}">{f["svg"]}'
                f'<figcaption>Figure {n}. {_e(f["title"])} {chip(f["chip"])}</figcaption></figure>')
    return ""


def _sources(doc: dict) -> str:
    items = []
    for s in doc["sources"]:
        refs = ", ".join(s["fact_ids"][:12]) + (" ..." if len(s["fact_ids"]) > 12 else "")
        if s["kind"] == "citation":
            head = _link(s.get("url"), f"{s.get('publisher') or ''}")
            tail = []
            if s.get("title"):
                tail.append(_e(s["title"]))
            if s.get("date"):
                tail.append(_e(s["date"]))
            body = head + (", " + ", ".join(tail) if tail else "") + f" {chip('SOURCED')}"
        elif s["kind"] in ("feed", "feed_event"):
            body = _e(s.get("label"))
            if s.get("state"):
                body += " " + chip(s["state"])
            if s.get("retrieved_at"):
                body += f", retrieved {_e(fmt.stamp(s['retrieved_at']))}"
        else:
            body = _e(s.get("label"))
        items.append(f'<li id="{_e(s["id"])}"><span class="sid">{_e(s["id"])}</span>'
                     f'<span>{body}<br><span class="refs">Facts {_e(refs)}</span></span></li>')
    return f'<ol class="sources">{"".join(items)}</ol>'


def _register(doc: dict) -> str:
    rows = []
    for f in doc["facts"]:
        s = f["source"]
        label = s.get("label") or ""
        if s.get("type") == "citation" and fmt.safe_url(s.get("url")):
            label = None
        src = _link(s["url"], s.get("label")) if label is None else _e(label)
        rows.append(f'<tr><td class="id">{_e(f["id"])}</td><td>{_e(f["label"])}</td>'
                    f'<td>{_e(f["display"])}</td><td>{chip(f["chip"])}</td><td>{src}</td></tr>')
    return ('<table class="reg"><thead><tr><th>Id</th><th>Fact</th><th>Value</th><th>Basis</th>'
            f'<th>Source</th></tr></thead><tbody>{"".join(rows)}</tbody></table>')


def render_html(doc: dict) -> bytes:
    bid = doc["brief_id"]
    prov = doc["provenance"]
    live = prov.get("transport") == "LIVE"
    v = doc["verifier"]
    writer_txt = "Meta AI" + (f", {prov.get('model')}" if live and prov.get("model") else "")
    figs = {f["id"]: f for f in doc["figures"]}
    fig_nums: dict[str, int] = {}
    for s in doc["sections"]:
        for b in s["blocks"]:
            if b["kind"] == "figure" and b["figure_id"] not in fig_nums:
                fig_nums[b["figure_id"]] = len(fig_nums) + 1

    meta = [("Generated", fmt.stamp(doc["generated_at"])),
            ("Run", doc["run"]["run_id"]),
            ("Chain hash", str(doc["run"].get("stream_hash") or "")[:12] or "-"),
            ("Brief", bid),
            ("Writer", f'{_e(writer_txt)} {chip("LIVE" if live else "RECORDED")}'),
            ("Checks", f'Verified {v["passed"]} of {v["checked"]} statements')]
    meta_html = "".join(
        f"<div><dt>{_e(k)}</dt><dd>{x if k == 'Writer' else _e(x)}</dd></div>" for k, x in meta)
    strip = "".join(
        f'<div class="kf"><div class="l">{_e(k["label"])}</div><div class="v">{_e(k["value"])}</div>'
        f'<div class="c">{chip(k["chip"])}</div></div>' for k in doc["key_figures"])
    secs = []
    for n, s in enumerate(doc["sections"], 1):
        chips = "".join(chip(c) for c in s.get("chips") or [])
        head = (f'<h2><span class="n">{n}</span><span>{_e(s["title"])}</span>'
                f'<span class="chips">{chips}</span></h2>')
        if s["state"] != "SERVED":
            body = f'<div class="ns">{chip("NOT SERVED")}<span>{_e(s.get("reason") or "")}</span></div>'
        else:
            body = "".join(_block(b, doc, fig_nums, figs) for b in s["blocks"])
        secs.append(f'<section class="sec" id="sec-{_e(s["id"])}">{head}{body}</section>')
    summary = doc["summary"]
    chips_top = chip("MODELLED") + chip("LIVE" if live else "RECORDED")
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>Disruption brief: {_e(doc["subject"])}</title>
<style>{CSS}{_chart_css()}</style></head>
<body><main class="note" data-brief="{_e(bid)}">
<header class="mast">
<div class="kicker"><span>SupermAP</span><span>Disruption brief</span><span class="sp"></span>{chips_top}</div>
<h1>{_e(doc["subject"])}</h1>
<p class="deck">{_segments(summary["segments"], bid)}</p>
<dl class="meta">{meta_html}</dl>
</header>
<div class="strip">{strip}</div>
{"".join(secs)}
<section class="sec" id="sec-sources"><h2><span class="n">{len(doc["sections"]) + 1}</span><span>Sources</span></h2>
{_sources(doc)}</section>
<section class="sec appendix" id="sec-register"><h2><span class="n">{len(doc["sections"]) + 2}</span><span>Fact register</span></h2>
<p class="note-line">Every figure in this note is registered here with its source. Statements are checked against this register.</p>
{_register(doc)}</section>
<footer class="foot"><p>{_e(doc["label"])}</p>
<p>Content sha256 <code>{_e(doc["sha256"])}</code></p></footer>
</main></body></html>
"""
    return page.encode("utf-8")
