"""Chat export: HTML, Markdown, JSON and PDF from one canonical document.

The document holds the title, date, RECORDED or LIVE label, every message,
each tool call with its arguments and result summary, the citations with URLs
and retrieval dates, costs, per-message transport labels and the thread id. A
sha256 of the canonical JSON content (everything except the export time) is
printed in every format, so an exported copy can be checked against another.

Everything user- or model-supplied is escaped: the HTML is static (no script),
links are built only from URLs that passed ``websearch.safe_url``, and the
PDF writer is text only (no dependency). Pure functions, no I/O.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import datetime, timezone

from . import websearch as ws

FORMATS = ("html", "md", "json", "pdf")
MEDIA = {"html": "text/html; charset=utf-8", "md": "text/markdown; charset=utf-8",
         "json": "application/json", "pdf": "application/pdf"}
TOOL_WORD = {
    "search_places": "Search places", "get_place": "Read place", "list_scenarios": "List scenarios",
    "list_runs": "List runs", "run_summary": "Summarise run", "compare_runs": "Compare runs",
    "verify_run": "Verify run", "check_shock": "Check shock", "lane_plan": "Plan lane",
    "port_scenario": "Port scenario", "list_reports": "List reports", "explain_chain": "Explain chain",
    "start_run": "Start run", "apply_shock": "Apply shock", "start_sim_run": "Start simulation",
    "build_report": "Build report", "export_pack": "Export pack", "open_window": "Open window",
    "arrange_window": "Arrange window", "apply_layout": "Apply layout", "set_mode": "Switch mode",
    "focus_place": "Focus place", "show_run": "Show run", "web_search": "Web search",
    "satellite_sam": "Satellite SAM", "satellite_vision": "Satellite vision",
}


class ExportError(ValueError):
    pass


def tool_word(name: str | None) -> str:
    n = name or ""
    return TOOL_WORD.get(n) or n.replace("_", " ").capitalize()


def _args_text(args, limit: int = 600) -> str:
    try:
        s = json.dumps(args, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        s = str(args)
    return s if len(s) <= limit else s[: limit - 1] + "…"


# -------------------------------------------------------------------- document
def build_doc(view: dict, message_id: str | None = None) -> dict:
    msgs = list(view.get("messages") or [])
    scope = "thread"
    if message_id:
        idx = next((i for i, m in enumerate(msgs) if m.get("id") == message_id), None)
        if idx is None:
            raise ExportError("no such message")
        scope = "message"
        keep = [msgs[idx]]
        if msgs[idx]["role"] == "assistant":
            q = next((m for m in reversed(msgs[:idx]) if m["role"] == "user"), None)
            keep = ([q] if q else []) + keep
        msgs = keep
    live = view.get("transport") == "LIVE"
    items = []
    for m in msgs:
        it = {"id": m.get("id"), "role": m.get("role"), "turn": m.get("turn"), "time": m.get("ts"),
              "transport": m.get("transport") or view.get("transport"),
              "text": m.get("text") or "", "system": bool(m.get("system"))}
        if m.get("role") == "user":
            it["context"] = [{"kind": c.get("kind"), "label": c.get("label"),
                              "summary": c.get("summary")} for c in (m.get("context") or [])]
        else:
            it["model"] = m.get("model") if live else None
            it["cost_usd"] = round(float(m.get("cost_usd") or 0.0), 6)
            it["tool_calls"] = [{
                "tool": s.get("tool") or s.get("name"), "label": tool_word(s.get("tool") or s.get("name")),
                "arguments": s.get("arguments") or {}, "result": s.get("result_summary") or "",
                "status": s.get("status"), "confirmation": s.get("confirmation"),
                "duration_ms": s.get("duration_ms"), "cost_usd": round(float(s.get("cost_usd") or 0.0), 6),
                "transport": s.get("transport"), "executed_in": s.get("executed_in") or "server",
                "flags": s.get("flags") or []} for s in (m.get("tool_steps") or [])]
            it["citations"] = [{k: c.get(k) for k in ("n", "title", "url", "domain", "date", "retrieved_at")}
                               for c in (m.get("citations") or [])]
            it["uncited_paragraphs"] = list(m.get("uncited") or [])
        items.append(it)
    cost = round(sum(i.get("cost_usd") or 0.0 for i in items if i["role"] == "assistant"), 6) \
        if scope == "message" else round(float(view.get("cost_usd") or 0.0), 6)
    doc = {
        "schema": "supermap.chat.v1", "scope": scope, "title": view.get("title") or "Chat",
        "thread_id": view.get("id"), "created_at": view.get("created_at"),
        "label": view.get("transport"), "model": view.get("model") if live else None,
        "web_search": bool((view.get("web") or {}).get("enabled")),
        "messages": items,
        "totals": {"messages": len(items), "cost_usd": cost,
                   "tool_calls": sum(len(i.get("tool_calls") or []) for i in items),
                   "citations": sum(len(i.get("citations") or []) for i in items)},
    }
    return doc


def content_sha256(doc: dict) -> str:
    canon = json.dumps(doc, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _slug(s: str) -> str:
    return (re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "chat")[:40]


def _money(v) -> str:
    v = float(v or 0.0)
    if v == 0:
        return "$0.00"
    return f"${v:.4f}" if v < 1 else f"${v:.2f}"


def _dur(ms) -> str:
    try:
        ms = float(ms)
    except (TypeError, ValueError):
        return "-"
    return f"{ms:.0f} ms" if ms < 1000 else f"{ms / 1000:.1f} s"


def _when(ts) -> str:
    return str(ts or "").replace("T", " ").replace("Z", " UTC")


def _label_word(t) -> str:
    return "Recorded" if t == "RECORDED" else "Live" if t == "LIVE" else "-"


# ------------------------------------------------------------------- markdown
def _md_text(m: dict) -> str:
    return m["text"]


def to_markdown(doc: dict, digest: str, generated: str) -> str:
    L = [f"# {doc['title']}", ""]
    L += [f"- Thread: `{doc['thread_id']}`", f"- Created: {_when(doc['created_at'])}",
          f"- Label: {_label_word(doc['label'])} ({doc['label']})"
          + (f", model {doc['model']}" if doc.get("model") else ", recorded script, no live model"),
          f"- Web search: {'on' if doc['web_search'] else 'off'}",
          f"- Scope: {'one answer' if doc['scope'] == 'message' else 'full chat'}, "
          f"{doc['totals']['messages']} messages, {doc['totals']['tool_calls']} tool calls, "
          f"{doc['totals']['citations']} citations, cost {_money(doc['totals']['cost_usd'])}",
          f"- Content sha256: `{digest}`", ""]
    for m in doc["messages"]:
        who = "You" if m["role"] == "user" else "Meta AI"
        L += [f"## {who} ({_when(m['time'])}, {_label_word(m['transport'])})", ""]
        for c in m.get("context") or []:
            L.append(f"> Attached: {c['kind']}, {c['label']}")
        if m.get("context"):
            L.append("")
        L += [m["text"], ""]
        if m["role"] == "assistant":
            if m.get("uncited_paragraphs"):
                L += [f"> Uncited: paragraph(s) {', '.join(str(i + 1) for i in m['uncited_paragraphs'])} "
                      "state web facts without a citation.", ""]
            if m["tool_calls"]:
                L += ["### Tool calls", "",
                      "| Tool | Arguments | Result | Status | Duration | Cost | Mode |",
                      "|---|---|---|---|---|---|---|"]
                for t in m["tool_calls"]:
                    L.append("| " + " | ".join([
                        t["label"], "`" + _args_text(t["arguments"], 200).replace("|", "\\|").replace("`", "'") + "`",
                        str(t["result"]).replace("|", "\\|").replace("\n", " ")[:200],
                        str(t["status"]) + (f" ({t['confirmation']})" if t["confirmation"] not in (None, "not_needed") else ""),
                        _dur(t["duration_ms"]), _money(t["cost_usd"]), _label_word(t["transport"])]) + " |")
                L.append("")
            if m["citations"]:
                L += ["### Sources", ""]
                for c in m["citations"]:
                    when = c.get("date") or f"retrieved {c.get('retrieved_at')}"
                    L.append(f"{c['n']}. {c['title']}, {c['domain']}, {when}. <{c['url']}>")
                L.append("")
            L.append(f"Cost {_money(m['cost_usd'])}. Transport {m['transport']}.")
            L.append("")
    L += ["---", f"Exported {generated}. Content sha256 {digest}.", ""]
    return "\n".join(L)


# ----------------------------------------------------------------------- html
def _inline(text: str, ns: str, n_cites: int) -> str:
    out = html.escape(text, quote=True)
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    return re.sub(r"\[(\d{1,2})\]",
                  lambda m: (f'<sup><a href="#{ns}-src-{m.group(1)}">[{m.group(1)}]</a></sup>'
                             if 1 <= int(m.group(1)) <= n_cites else m.group(0)), out)


def md_to_html(text: str, ns: str, n_cites: int, uncited: list[int] | None = None) -> str:
    """Safe markdown subset: paragraphs, lists, bold, code, tables. Escapes first."""
    blocks = ws.paragraphs(text)
    out = []
    for bi, b in enumerate(blocks):
        lines = b.splitlines()
        if all(re.match(r"^\s*[-*]\s+", ln) for ln in lines):
            body = "".join(f"<li>{_inline(re.sub(r'^\s*[-*]\s+', '', ln), ns, n_cites)}</li>" for ln in lines)
            h = f"<ul>{body}</ul>"
        elif all(re.match(r"^\s*\d+[.)]\s+", ln) for ln in lines):
            body = "".join(f"<li>{_inline(re.sub(r'^\s*\d+[.)]\s+', '', ln), ns, n_cites)}</li>" for ln in lines)
            h = f"<ol>{body}</ol>"
        elif len(lines) >= 2 and all(ln.strip().startswith("|") for ln in lines):
            rows = [[c.strip() for c in ln.strip().strip("|").split("|")] for ln in lines
                    if not re.match(r"^\s*\|?[\s:\-|]+\|?\s*$", ln)]
            head, rest = (rows[0], rows[1:]) if rows else ([], [])
            h = ("<table><thead><tr>" + "".join(f"<th>{_inline(c, ns, n_cites)}</th>" for c in head)
                 + "</tr></thead><tbody>" + "".join(
                     "<tr>" + "".join(f"<td>{_inline(c, ns, n_cites)}</td>" for c in r) + "</tr>"
                     for r in rest) + "</tbody></table>")
        else:
            h = "<p>" + "<br>".join(_inline(ln, ns, n_cites) for ln in lines) + "</p>"
        if uncited and bi in uncited:
            h += '<p class="flag">Uncited</p>'
        out.append(h)
    return "\n".join(out)


_CSS = """
:root{--bg:#fff;--fg:#14181f;--mut:#566170;--line:#d5dae1;--panel:#f4f6f9;--acc:#1b4f9c;--warn:#8a5a00}
@media (prefers-color-scheme:dark){:root{--bg:#10141a;--fg:#e6e9ee;--mut:#9aa5b4;--line:#2b333e;--panel:#171c24;--acc:#7fb0ff;--warn:#e0b050}}
@media print{:root{--bg:#fff;--fg:#000;--mut:#333;--line:#999;--panel:#fff;--acc:#000;--warn:#000}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 -apple-system,"Segoe UI",Helvetica,Arial,sans-serif}
main{max-width:860px;margin:0 auto;padding:28px 20px 48px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:15px;margin:0}h3{font-size:12px;margin:14px 0 6px;color:var(--mut);text-transform:none}
.meta{width:100%;border-collapse:collapse;margin:14px 0 22px;font-size:12.5px}.meta th{text-align:left;font-weight:600;color:var(--mut);width:150px;padding:4px 8px 4px 0;vertical-align:top}.meta td{padding:4px 0;overflow-wrap:anywhere}
.msg{border-top:1px solid var(--line);padding:16px 0}.head{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}
.chip{display:inline-block;border:1px solid var(--line);border-radius:3px;padding:0 6px;font-size:11px;font-weight:600;color:var(--mut)}
.chip.live{color:var(--acc);border-color:var(--acc)}.time{color:var(--mut);font-size:12px}
.user .text{background:var(--panel);border:1px solid var(--line);padding:8px 12px;border-radius:4px}
p{margin:8px 0}ul,ol{margin:8px 0;padding-left:22px}code{font:12px ui-monospace,Menlo,Consolas,monospace;overflow-wrap:anywhere}
table{border-collapse:collapse;width:100%;font-size:12.5px}th,td{border:1px solid var(--line);padding:4px 8px;text-align:left;vertical-align:top;overflow-wrap:anywhere}th{background:var(--panel)}
.flag{display:inline-block;color:var(--warn);border:1px solid var(--warn);border-radius:3px;padding:0 6px;font-size:11px;font-weight:600;margin:0}
.src{font-size:12.5px;padding-left:24px}.src li{margin:3px 0}.mut{color:var(--mut)}a{color:var(--acc)}
footer{border-top:1px solid var(--line);margin-top:24px;padding-top:12px;font-size:12px;color:var(--mut);overflow-wrap:anywhere}
"""


def to_html(doc: dict, digest: str, generated: str) -> str:
    e = html.escape
    live = doc["label"] == "LIVE"
    meta = [("Thread", f"<code>{e(str(doc['thread_id']))}</code>"), ("Created", e(_when(doc["created_at"]))),
            ("Label", f'<span class="chip{" live" if live else ""}">{e(_label_word(doc["label"]).upper())}</span>'
             + (f" model {e(str(doc['model']))}" if doc.get("model") else " recorded script, no live model")),
            ("Web search", "On" if doc["web_search"] else "Off"),
            ("Scope", "One answer" if doc["scope"] == "message" else "Full chat"),
            ("Contents", f"{doc['totals']['messages']} messages, {doc['totals']['tool_calls']} tool calls, "
                         f"{doc['totals']['citations']} citations"),
            ("Cost", e(_money(doc["totals"]["cost_usd"]))),
            ("Content sha256", f"<code>{digest}</code>")]
    parts = [f"<h1>{e(doc['title'])}</h1>", '<div class="mut">Meta AI chat export</div>',
             '<table class="meta">' + "".join(f"<tr><th>{k}</th><td>{v}</td></tr>" for k, v in meta) + "</table>"]
    for mi, m in enumerate(doc["messages"]):
        ns = f"m{mi}"
        who = "You" if m["role"] == "user" else "Meta AI"
        chip = f'<span class="chip{" live" if m["transport"] == "LIVE" else ""}">{e(_label_word(m["transport"]).upper())}</span>'
        parts.append(f'<section class="msg {m["role"]}" id="{ns}"><div class="head"><h2>{who}</h2>{chip}'
                     f'<span class="time">{e(_when(m["time"]))}</span></div>')
        for c in m.get("context") or []:
            parts.append(f'<div class="mut">Attached: {e(str(c["kind"]))}, {e(str(c["label"]))}</div>')
        n_c = len(m.get("citations") or [])
        parts.append('<div class="text">' + md_to_html(m["text"], ns, n_c, m.get("uncited_paragraphs")) + "</div>")
        if m["role"] == "assistant":
            if m["tool_calls"]:
                rows = "".join(
                    "<tr>" + "".join(f"<td>{e(str(x))}</td>" for x in (
                        t["label"], _args_text(t["arguments"], 300), t["result"],
                        f"{t['status']}" + (f" ({t['confirmation']})" if t["confirmation"] not in (None, "not_needed") else ""),
                        _dur(t["duration_ms"]), _money(t["cost_usd"]), _label_word(t["transport"]))) + "</tr>"
                    for t in m["tool_calls"])
                parts.append("<h3>Tool calls</h3><table><thead><tr><th>Tool</th><th>Arguments</th><th>Result</th>"
                             "<th>Status</th><th>Duration</th><th>Cost</th><th>Mode</th></tr></thead>"
                             f"<tbody>{rows}</tbody></table>")
            if m["citations"]:
                lis = []
                for c in m["citations"]:
                    when = c.get("date") or f"retrieved {c.get('retrieved_at')}"
                    url = ws.safe_url(c.get("url"))
                    link = (f'<a href="{e(url)}" rel="noopener noreferrer">{e(str(c["title"]))}</a>'
                            if url else e(str(c["title"])))
                    lis.append(f'<li id="{ns}-src-{c["n"]}" value="{c["n"]}">{link}, {e(str(c["domain"]))}, '
                               f'{e(str(when))}<br><code>{e(str(c["url"]))}</code></li>')
                parts.append('<h3>Sources</h3><ol class="src">' + "".join(lis) + "</ol>")
            parts.append(f'<div class="mut">Cost {e(_money(m["cost_usd"]))}. Transport {e(str(m["transport"]))}.</div>')
        parts.append("</section>")
    parts.append(f"<footer>Exported {e(generated)}. Content sha256 <code>{digest}</code>. "
                 "Simulated and modelled values are labelled as such in the messages above.</footer>")
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<meta name=\"referrer\" content=\"no-referrer\">"
            f"<title>{e(doc['title'])}</title><style>{_CSS}</style></head><body><main>"
            + "\n".join(parts) + "</main></body></html>")


# ------------------------------------------------------------------------ pdf
_W = [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556,
      556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556, 1015, 667, 667, 722, 722, 667, 611, 778,
      722, 278, 500, 667, 556, 833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278,
      278, 278, 469, 556, 333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556,
      556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584]


def _cw(ch: str, size: float, bold: bool) -> float:
    o = ord(ch)
    w = _W[o - 32] if 32 <= o <= 126 else 556
    return w * size / 1000.0 * (1.06 if bold else 1.0)


def _wrap(text: str, size: float, width: float, bold: bool = False) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        cur, curw = "", 0.0
        for word in re.split(r"(\s+)", para):
            if not word:
                continue
            ww = sum(_cw(c, size, bold) for c in word)
            if curw + ww > width and cur.strip():
                lines.append(cur.rstrip())
                cur, curw = "", 0.0
                if word.isspace():
                    continue
            while ww > width:              # a single long token (a URL): hard split
                k = max(1, int(len(word) * width / ww))
                lines.append(word[:k])
                word = word[k:]
                ww = sum(_cw(c, size, bold) for c in word)
            cur += word
            curw += ww
        lines.append(cur.rstrip())
    return lines


def _pdf_esc(s: str) -> bytes:
    b = s.encode("cp1252", errors="replace")
    return b.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def to_pdf(doc: dict, digest: str, generated: str) -> bytes:
    PW, PH, M = 595.0, 842.0, 54.0
    width = PW - 2 * M
    pages: list[list[tuple]] = [[]]
    y = [PH - M]

    def put(text: str, size: float = 10, bold: bool = False, indent: float = 0.0, gap: float = 0.0):
        lead = size * 1.32
        for ln in _wrap(text, size, width - indent, bold):
            if y[0] - lead < M + 18:
                pages.append([])
                y[0] = PH - M
            y[0] -= lead
            pages[-1].append((M + indent, y[0], size, bold, ln))
        y[0] -= gap

    put(doc["title"], 16, True, gap=4)
    put("Meta AI chat export", 9, gap=6)
    put(f"Thread {doc['thread_id']}", 9)
    put(f"Created {_when(doc['created_at'])}", 9)
    put(f"Label {_label_word(doc['label']).upper()}" + (f", model {doc['model']}" if doc.get("model")
                                                          else ", recorded script, no live model"), 9)
    put(f"Web search {'on' if doc['web_search'] else 'off'}. {doc['totals']['messages']} messages, "
        f"{doc['totals']['tool_calls']} tool calls, {doc['totals']['citations']} citations, "
        f"cost {_money(doc['totals']['cost_usd'])}.", 9)
    put(f"Content sha256 {digest}", 8, gap=10)
    for m in doc["messages"]:
        who = "You" if m["role"] == "user" else "Meta AI"
        put(f"{who}   {_when(m['time'])}   {_label_word(m['transport']).upper()}", 10, True, gap=2)
        for c in m.get("context") or []:
            put(f"Attached: {c['kind']}, {c['label']}", 9, indent=10)
        put(m["text"], 10, indent=10, gap=4)
        if m["role"] == "assistant":
            if m.get("uncited_paragraphs"):
                put("Uncited: " + ", ".join(str(i + 1) for i in m["uncited_paragraphs"])
                    + " (paragraph numbers state web facts without a citation).", 9, indent=10)
            for t in m["tool_calls"]:
                put(f"Tool call: {t['label']}  [{t['status']}, {_dur(t['duration_ms'])}, "
                    f"{_money(t['cost_usd'])}, {_label_word(t['transport'])}]", 9, True, indent=10)
                put("Arguments " + _args_text(t["arguments"], 300), 8.5, indent=20)
                put("Result " + str(t["result"]), 8.5, indent=20)
            for c in m["citations"]:
                when = c.get("date") or f"retrieved {c.get('retrieved_at')}"
                put(f"[{c['n']}] {c['title']}, {c['domain']}, {when}", 9, indent=10)
                put(str(c["url"]), 8, indent=22)
            put(f"Cost {_money(m['cost_usd'])}. Transport {m['transport']}.", 8.5, indent=10)
        y[0] -= 8
    put(f"Exported {generated}. Content sha256 {digest}", 8)

    objs: list[bytes] = []

    def add(b: bytes) -> int:
        objs.append(b)
        return len(objs)

    add(b"")                                  # 1 catalog (filled below)
    add(b"")                                  # 2 pages
    add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")      # 3
    add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")  # 4
    kids = []
    total = len(pages)
    for pi, items in enumerate(pages, 1):
        parts = []
        for (x, yy, size, bold, ln) in items:
            parts.append(b"BT /F%d %.1f Tf %.1f %.1f Td (" % (2 if bold else 1, size, x, yy) + _pdf_esc(ln) + b") Tj ET")
        foot = f"Page {pi} of {total}   {doc['thread_id']}"
        parts.append(b"BT /F1 8 Tf %.1f %.1f Td (" % (M, M - 4) + _pdf_esc(foot) + b") Tj ET")
        stream = b"\n".join(parts)
        cid = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        pid = add(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] /Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> "
                  b"/Contents %d 0 R >>" % (PW, PH, cid))
        kids.append(pid)
    objs[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objs[1] = b"<< /Type /Pages /Kids [" + b" ".join(b"%d 0 R" % k for k in kids) + b"] /Count %d >>" % len(kids)
    title = re.sub(r"[^\x20-\x7e]", "", doc["title"]).replace("(", "").replace(")", "").replace("\\", "")[:80]
    info = add(b"<< /Title (" + title.encode("ascii") + b") /Producer (SupermAP) >>")
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += (b"trailer\n<< /Size %d /Root 1 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objs) + 1, info, xref))
    return bytes(out)


# ----------------------------------------------------------------------- main
def build_export(view: dict, fmt: str, message_id: str | None = None,
                 now: datetime | None = None) -> tuple[bytes, str, str]:
    """(body, content type, filename)."""
    fmt = (fmt or "").lower()
    if fmt not in FORMATS:
        raise ExportError(f"format must be one of {', '.join(FORMATS)}")
    doc = build_doc(view, message_id)
    digest = content_sha256(doc)
    gen = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M UTC")
    if fmt == "json":
        body = json.dumps({**doc, "content_sha256": digest, "exported_at": gen}, indent=2,
                          ensure_ascii=False, sort_keys=True).encode("utf-8")
    elif fmt == "md":
        body = to_markdown(doc, digest, gen).encode("utf-8")
    elif fmt == "html":
        body = to_html(doc, digest, gen).encode("utf-8")
    else:
        body = to_pdf(doc, digest, gen)
    day = (now or datetime.now(timezone.utc)).strftime("%Y%m%d")
    kind = "answer" if doc["scope"] == "message" else "chat"
    return body, MEDIA[fmt], f"meta-ai-{kind}-{_slug(doc['title'])}-{day}.{fmt}"
