"""Draft -> verified blocks.

Takes the writer's JSON (model or recorded), checks every statement with the
verifier, replaces each rejected block with the deterministic template block
for the same slot, fills sections the writer left out, and expands the
{Fn} placeholders from the facts table.

Counts reported in ``verifier``:
  checked   statements read from the writer's draft (summary included)
  passed    statements in blocks that were kept as written
  flagged   [{block, reason}] one per rejected block or missing section
  dropped   writer blocks discarded without a replacement (unknown section,
            duplicate, extra block, empty)
"""
from __future__ import annotations

import json
import re

from . import compose, verify
from .model import MAX_WORDS, SECTION_BY_ID

PH_RE = compose.PH_RE
MAX_BLOCKS_PER_SECTION = 3
MAX_ITEMS = 5


def expand(text: str, by_id: dict[str, dict]) -> tuple[str, list[dict], list[str]]:
    """Fill placeholders: (plain text, segments, fact ids in first-seen order)."""
    segs: list[dict] = []
    ids: list[str] = []
    out: list[str] = []
    pos = 0
    for m in PH_RE.finditer(text):
        if m.start() > pos:
            seg = text[pos:m.start()]
            segs.append({"t": seg})
            out.append(seg)
        fid = m.group(1)
        f = by_id.get(fid)
        disp = f["display"] if f else ""
        segs.append({"fact": fid, "t": disp})
        out.append(disp)
        if fid not in ids:
            ids.append(fid)
        pos = m.end()
    if pos < len(text):
        segs.append({"t": text[pos:]})
        out.append(text[pos:])
    return "".join(out), segs, ids


def parse_draft(text) -> tuple[dict | None, str | None]:
    """The writer's reply text -> dict, or (None, reason)."""
    if isinstance(text, dict):
        return text, None
    if not isinstance(text, str) or not text.strip():
        return None, "empty_reply"
    try:
        doc = json.loads(text)
    except ValueError:
        return None, "unreadable_reply"
    return (doc, None) if isinstance(doc, dict) else (None, "unreadable_reply")


def _clean_block(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    kind = raw.get("kind")
    if kind == "paragraph":
        text = raw.get("text")
        return {"kind": "paragraph", "text": text.strip(), "items": []} \
            if isinstance(text, str) and text.strip() else None
    if kind == "bullets":
        items = [i.strip() for i in (raw.get("items") or []) if isinstance(i, str) and i.strip()]
        return {"kind": "bullets", "text": "", "items": items[:MAX_ITEMS]} if items else None
    return None


def _final_block(sid: str, bid: str, kind: str, text: str, items: list[str],
                 by_id: dict, source: str) -> dict:
    blk = {"id": f"{sid}.{bid}", "kind": kind, "source": source, "fact_ids": []}
    if kind == "paragraph":
        plain, segs, ids = expand(text, by_id)
        blk.update({"text": plain, "segments": segs, "fact_ids": ids})
    else:
        its = []
        allids: list[str] = []
        for it in items:
            plain, segs, ids = expand(it, by_id)
            its.append({"text": plain, "segments": segs, "fact_ids": ids})
            allids.extend(i for i in ids if i not in allids)
        blk.update({"items": its, "fact_ids": allids})
    return blk


def assemble(draft, section_ids: list[str], facts: list[dict], ids_map: dict[str, str],
             graph_nodes, *, source: str = "model") -> dict:
    """-> {summary, blocks: {section_id: [block]}, verifier}."""
    by_id = {f["id"]: f for f in facts}
    vctx = verify.Context(facts, graph_nodes)
    comp = compose.Ids(ids_map)
    flagged: list[dict] = []
    checked = passed = dropped = 0
    draft_ok = isinstance(draft, dict)
    if not draft_ok:
        flagged.append({"block": "draft", "reason": "unreadable_reply"})
        draft = {}

    # ---- summary
    summ_text = draft.get("summary") if isinstance(draft.get("summary"), str) else ""
    summary = None
    if summ_text.strip():
        n, reasons = verify.check_block({"kind": "paragraph", "text": summ_text}, vctx)
        checked += n
        if reasons:
            flagged.append({"block": "summary", "reason": reasons[0].split(":")[0]})
        else:
            passed += n
            plain, segs, ids = expand(summ_text.strip(), by_id)
            summary = {"text": plain, "segments": segs, "fact_ids": ids, "source": source}
    elif draft_ok:
        flagged.append({"block": "summary", "reason": "section_missing"})
    if summary is None:
        t = compose.summary(comp)
        plain, segs, ids = expand(t, by_id)
        summary = {"text": plain, "segments": segs, "fact_ids": ids, "source": "template"}

    # ---- sections
    drafted: dict[str, list] = {}
    seen_sections: set[str] = set()
    for sec in draft.get("sections") or []:
        sid = sec.get("id") if isinstance(sec, dict) else None
        if sid not in section_ids or sid not in compose.COMPOSERS or sid in seen_sections:
            dropped += 1
            continue
        seen_sections.add(sid)
        drafted[sid] = [b for b in (sec.get("blocks") or [])]

    blocks_out: dict[str, list] = {}
    for sid in section_ids:
        if sid not in compose.COMPOSERS:
            continue
        template = {b["id"]: b for b in compose.compose_section(sid, comp)}
        kept: list[dict] = []
        counters = {"paragraph": 0, "bullets": 0}
        got = drafted.get(sid)
        if got is None:
            if draft_ok:
                flagged.append({"block": sid, "reason": "section_missing"})
            for b in template.values():
                kept.append(_final_block(sid, b["id"], b["kind"], b["text"], b["items"],
                                         by_id, "template"))
            blocks_out[sid] = kept
            continue
        used_slots: set[str] = set()
        for raw in got[:MAX_BLOCKS_PER_SECTION + 3]:
            blk = _clean_block(raw)
            if blk is None:
                dropped += 1
                continue
            kind = blk["kind"]
            counters[kind] += 1
            if counters[kind] > 1 or len(kept) >= MAX_BLOCKS_PER_SECTION:
                dropped += 1
                continue
            bid = "p1" if kind == "paragraph" else "b1"
            n, reasons = verify.check_block(blk, vctx)
            checked += n
            if reasons:
                flagged.append({"block": f"{sid}.{bid}", "reason": reasons[0].split(":")[0]})
                rep = template.get(bid)
                if rep is not None:
                    kept.append(_final_block(sid, bid, rep["kind"], rep["text"], rep["items"],
                                             by_id, "template"))
                else:
                    dropped += 1
                used_slots.add(bid)
                continue
            passed += n
            kept.append(_final_block(sid, bid, kind, blk["text"], blk["items"], by_id, source))
            used_slots.add(bid)
        # a lead paragraph the writer left out is filled from the template,
        # unflagged; bullets the writer chose not to write stay absent
        for bid, b in template.items():
            if bid == "p1" and bid not in used_slots:
                kept.append(_final_block(sid, bid, b["kind"], b["text"], b["items"],
                                         by_id, "template"))
        kept.sort(key=lambda b: (0 if b["kind"] == "paragraph" else 1))
        blocks_out[sid] = kept

    return {
        "summary": summary,
        "blocks": blocks_out,
        "words": words_in(blocks_out) + len(summary["text"].split()),
        "verifier": {"checked": checked, "passed": passed, "flagged": flagged,
                     "dropped": dropped},
    }


def words_in(blocks: dict[str, list]) -> int:
    n = 0
    for bl in blocks.values():
        for b in bl:
            n += len(b["text"].split()) if b["kind"] == "paragraph" else \
                sum(len(i["text"].split()) for i in b["items"])
    return n
