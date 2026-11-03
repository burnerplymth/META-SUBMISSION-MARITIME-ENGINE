"""Public chain-ledger page generator.

Renders data/ledger.jsonl (via LedgerStore's merged read view, the same
store, no separate data path) to a standalone static HTML page at
data/ledger-public.html. Read-only render: locked entries, grades and
evidence, the grading rule in plain words, and a note separating the counts
on this page from backtest results. An empty ledger says so plainly.
"""
from __future__ import annotations

import html
import json
import re
from functools import lru_cache
from pathlib import Path

import source_credits as _credits

from .ledger import LedgerStore, DEFAULT_LEDGER_PATH

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PAGE_PATH = REPO_ROOT / "data" / "ledger-public.html"

HONESTY_LINE = (
    "Counts on this page come only from the graded ledger entries below. "
    "Backtest results use replayed past events, are a different measure and "
    "are never mixed into the numbers here. All counts are descriptive."
)

# The fixed grading rule (server/llm/ledger.py GRADING_RULE_V1) in plain
# words: same thresholds and conditions, without internal field names.
RULE_PLAIN = (
    "A locked step is matched when outcome evidence shows an effect at the "
    "same place within the window from 0.5 x to 2 x its locked delay (d), "
    "measured from the disruption start on the same clock as the chain, and "
    "backed by queue or capacity evidence (for example a PortWatch activity "
    "or queue anomaly, a border wait spike, vessel density or queue counts, "
    "or satellite anchorage counts). Otherwise the step is not matched. The "
    "delay difference is the mean, over matched steps, of the absolute gap "
    "between observed and locked delay. Every grade carries dated evidence "
    "with source, retrieval time and mode; a grade without evidence is not "
    "published. Lock-before-knowable is a hard rule: each entry is locked "
    "before the evidence that grades it was retrieved. Grading is deterministic and uses no language model: "
    "re-running it on the same evidence reproduces the same grade. Entries "
    "are never edited or deleted; a grade is written once; a correction is "
    "a new entry that references the original entry id."
)


GRAPH_PATH = REPO_ROOT / "data" / "graph" / "graph.json"


@lru_cache(maxsize=1)
def _place_names() -> dict:
    try:
        doc = json.loads(GRAPH_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {n["id"]: n.get("name") for n in doc.get("nodes", [])
            if isinstance(n, dict) and n.get("id") and n.get("name")}


def place_name(node_id) -> str:
    """The place's name; an id the map does not hold reads as an unnamed place, never the raw id."""
    return _place_names().get(node_id) or "Place not in the map"


def disruption_label(disruption_id) -> str:
    """A readable label for an event id such as evt_20261020_sea_savannah_weather_200da99e:
    kind, place name and date. An id that does not parse reads as an event, never the raw id."""
    m = re.match(r"^evt_(\d{4})(\d{2})(\d{2})_(.+)_[0-9a-f]{6,}$", str(disruption_id))
    if not m:
        return "Event"
    names = _place_names()
    for pid in sorted(names, key=len, reverse=True):
        if m.group(4).startswith(pid + "_"):
            kind = m.group(4)[len(pid) + 1:].replace("_", " ")
            return f"{kind.capitalize()} at {names[pid]}, {m.group(1)}-{m.group(2)}-{m.group(3)}"
    return f"Event, {m.group(1)}-{m.group(2)}-{m.group(3)}"


def _esc(x) -> str:
    return html.escape(str(x))


def _count(x) -> str:
    """A step list prints as its length; a plain count prints as is."""
    return _esc(len(x) if isinstance(x, (list, tuple)) else x)


def render_page(store: LedgerStore) -> str:
    entries = store.entries()
    acc = store.accuracy()
    parts = [
        "<!DOCTYPE html>",
        '<html lang="en"><head><meta charset="utf-8">',
        "<title>SupermAP chain ledger (public)</title>",
        "<style>body{font-family:ui-monospace,Menlo,monospace;background:#0b0b0c;"
        "color:#e5e5e5;margin:2rem;line-height:1.5}h1{color:#f59e0b}"
        "table{border-collapse:collapse;margin:1rem 0}td,th{border:1px solid #333;"
        "padding:.35rem .6rem;text-align:left;vertical-align:top}"
        ".rule{background:#111;border:1px solid #333;padding:1rem;white-space:pre-wrap}"
        ".chip{border:1px solid #555;padding:0 .4rem;font-size:.8em;color:#9ca3af}"
        ".muted{color:#9ca3af}</style></head><body>",
        "<h1>SupermAP chain ledger</h1>",
        "<p>Append-only public record of modelled chains, locked before "
        "outcomes were knowable, and graded against observed evidence under "
        "the frozen rule below. Entries are never edited or deleted.</p>",
        f"<p>{_esc(HONESTY_LINE)}</p>",
        "<h2>Grading rule v1 (frozen)</h2>",
        f'<div class="rule">{_esc(RULE_PLAIN)}</div>',
    ]
    if acc.get("graded_entries"):
        parts.append(
            f'<p><span class="chip">DESCRIPTIVE</span> Graded so far: '
            f"{_esc(acc['graded_entries'])} entr"
            f"{'y' if acc['graded_entries'] == 1 else 'ies'}; "
            f"{_esc(acc['hit_steps'])} steps matched, "
            f"{_esc(acc['missed_steps'])} not matched.</p>")
    if not entries:
        parts.append("<h2>Entries</h2><p><strong>No chains have been "
                     "locked yet.</strong> The ledger is empty.</p>")
    else:
        parts.append("<h2>Entries</h2>")
        for e in entries:
            parts.append(f"<h3>{_esc(e['entry_id'])}</h3>")
            parts.append(
                "<table><tr><th>Locked at (UTC)</th>"
                f"<td>{_esc(e['locked_at'])}</td></tr>"
                "<tr><th>Disruption</th>"
                f"<td>{_esc(disruption_label(e['disruption_id']))}</td></tr>"
                "<tr><th>Run</th>"
                f"<td>{_esc(e['run_id'])}</td></tr>"
                "<tr><th>Locked steps</th>"
                f"<td>{len(e['predicted_chain'])}</td></tr></table>")
            parts.append(
                "<table><tr><th>Step</th><th>Place</th><th>Channel</th>"
                "<th>Delay (d)</th><th>At (UTC)</th></tr>")
            for ev in e["predicted_chain"]:
                parts.append(
                    f"<tr><td>{_esc(ev.get('seq'))}</td>"
                    f"<td>{_esc(place_name(ev.get('node_id')))}</td>"
                    f"<td>{_esc(ev.get('channel'))}</td>"
                    f"<td>{_esc(ev.get('delay_d'))}</td>"
                    f"<td>{_esc(ev.get('at'))}</td></tr>")
            parts.append("</table>")
            grade = e.get("grade")
            if grade is None:
                parts.append('<p class="muted">Status: Open, not yet graded '
                             "(no outcome evidence graded yet).</p>")
            else:
                parts.append(
                    f"<p>Grade (graded at {_esc(grade['graded_at'])}): "
                    f"steps matched {_count(grade['hit_steps'])}, steps not "
                    f"matched {_count(grade['missed_steps'])}"
                    + (f", ungradeable steps "
                       f"{_count(grade['ungradeable_steps'])}"
                       if grade.get("ungradeable_steps") else "")
                    + f", delay difference (d) "
                      f"{_esc(grade['delay_error_d'])}.</p>")
                parts.append("<table><tr><th>Evidence source</th>"
                             "<th>Retrieved at (UTC)</th><th>Mode</th></tr>")
                for prov in grade.get("evidence", []):
                    parts.append(
                        f"<tr><td>{_esc(prov.get('source'))}</td>"
                        f"<td>{_esc(prov.get('retrieved_at'))}</td>"
                        f"<td>{_esc(prov.get('mode'))}</td></tr>")
                parts.append("</table>")
            if e.get("supersedes"):
                parts.append(f"<p class='muted'>Supersedes "
                             f"{_esc(e['supersedes'])}.</p>")
    evidence = [p.get("source") for e in entries
                for p in ((e.get("grade") or {}).get("evidence") or [])]
    parts.append('<footer aria-label="Sources"><h2>Sources</h2><ul>'
                 + "".join(f"<li>{_esc(t)}</li>" for t in _credits.source_lines(
                     " ".join(str(x) for x in evidence)))
                 + "</ul></footer>")
    parts.append("</body></html>")
    return "\n".join(parts)


def generate_page(ledger_path: Path | None = None,
                  out_path: Path | None = None) -> Path:
    store = LedgerStore(ledger_path or DEFAULT_LEDGER_PATH)
    out = Path(out_path or DEFAULT_PAGE_PATH)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_page(store), encoding="utf-8")
    return out


def main() -> int:
    out = generate_page()
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
