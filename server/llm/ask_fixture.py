"""Recorded-fixture heuristic for the chain-grounded ask prompt (RECORDED).

A deterministic reader of the chain that stands in for the model until the
key exists. It is a FIXTURE, labelled RECORDED by the provenance block: it
exercises the plumbing and the post-checks, not Muse Spark's language
ability. It answers a small closed set of question shapes from the step
records alone (largest delay, count, first/last step, why was <place> hit,
a summary) and REFUSES everything else with ``not_in_chain``.

One shape is deliberately a trap for the post-check, exactly as a real
model would fall into it: "total / sum / combined" delay. The fixture
adds the delays up, the sum is a number no step record holds, the
server's post-check rejects it and serves the deterministic template.
"""
from __future__ import annotations

import json
import re

_MAX_WORDS = ("most", "largest", "biggest", "longest", "greatest", "highest",
              "worst", "maximum", "slowest")
_SUM_WORDS = ("total", "sum", "combined", "altogether", "overall delay", "add up")
_COUNT_WORDS = ("how many", "number of")
_FIRST_WORDS = ("first", "start", "origin", "root", "begin", "trigger", "source")
_LAST_WORDS = ("last", "final", "end ", "ends", "end?", "ultimately")
_WHY_WORDS = ("why", "how did", "how was", "hit", "affected", "reach", "caus")
_SUMMARY_WORDS = ("summar", "overview", "what happened", "explain", "chain")


def _names(payload: dict) -> dict:
    return {n.get("id"): (n.get("name") or n.get("id"))
            for n in (payload.get("subgraph", {}).get("nodes") or [])
            if n.get("id")}


def _mentioned(question: str, names: dict) -> list:
    q = question.lower()
    words = set(re.findall(r"[a-z0-9]+", q))
    out = []
    for nid, name in names.items():
        suffix = nid.split("_", 1)[-1]
        if (name and name.lower() in q) or (len(suffix) >= 4 and suffix in words):
            out.append(nid)
    return out


def _step(ev: dict, names: dict) -> str:
    return (f"seq {ev.get('seq')}, {names.get(ev.get('node_id'), ev.get('node_id'))} "
            f"via {ev.get('channel')} ({ev.get('mechanism')}), "
            f"delay {ev.get('delay_d')} d")


def fixture_ask_chain_json(payload: dict) -> str:
    q = str(payload.get("question", "")).lower()
    chain = payload.get("chain") or {}
    events = chain.get("events") or []
    run_id = chain.get("run_id")
    names = _names(payload)
    if not events:
        return json.dumps({"refused": True, "reason": "not_in_chain"})
    by_seq = {e.get("seq"): e for e in events}

    def reply(text: str, nodes: list) -> str:
        seen = []
        for n in nodes:
            if n and n not in seen:
                seen.append(n)
        return json.dumps({
            "answer": "FIXTURE ANSWER (transport=fixture - recorded-fixture "
                      "response, not a live model call). " + text,
            "cited_node_ids": seen, "run_ids": [run_id] if run_id else []})

    mentioned = _mentioned(q, names)
    if any(w in q for w in _SUM_WORDS) and "delay" in q:
        total = round(sum(float(e.get("delay_d") or 0.0) for e in events), 2)
        return reply(f"The steps add up to {total} d of delay in total.",
                     [e.get("node_id") for e in events])
    if "delay" in q and any(w in q for w in _MAX_WORDS) or "which step" in q:
        top = max(events, key=lambda e: (float(e.get("delay_d") or 0.0), -e.get("seq", 0)))
        return reply(f"The step with the largest delay is {_step(top, names)}.",
                     [top.get("node_id")])
    if any(w in q for w in _COUNT_WORDS):
        return reply(f"The chain has {len(events)} steps.", [events[0].get("node_id")])
    if mentioned and any(w in q for w in _WHY_WORDS):
        target = next((e for e in events if e.get("node_id") in mentioned), None)
        if target is None:
            return json.dumps({"refused": True, "reason": "not_in_chain"})
        path, cur = [target], target
        while cur.get("parent_id") is not None and cur.get("parent_id") in by_seq:
            cur = by_seq[cur["parent_id"]]
            path.append(cur)
        path.reverse()
        if len(path) == 1:
            text = f"{names.get(target.get('node_id'))} is the root of the chain: {_step(target, names)}."
        else:
            text = (f"{names.get(target.get('node_id'))} was hit at {_step(target, names)}. "
                    "It follows " + ", then ".join(
                        f"seq {e.get('seq')} ({names.get(e.get('node_id'))})"
                        for e in path[:-1]) + ".")
        return reply(text, [e.get("node_id") for e in path])
    if any(w in q for w in _FIRST_WORDS):
        return reply(f"The chain starts at {_step(events[0], names)}.", [events[0].get("node_id")])
    if any(w in q for w in _LAST_WORDS):
        last = max(events, key=lambda e: e.get("seq", 0))
        return reply(f"The last step is {_step(last, names)}.", [last.get("node_id")])
    if any(w in q for w in _SUMMARY_WORDS):
        top = max(events, key=lambda e: (float(e.get("delay_d") or 0.0), -e.get("seq", 0)))
        return reply(f"The chain has {len(events)} steps from seq {events[0].get('seq')} "
                     f"({names.get(events[0].get('node_id'))}); the largest delay is "
                     f"{top.get('delay_d')} d at seq {top.get('seq')}.",
                     [e.get("node_id") for e in events])
    return json.dumps({"refused": True, "reason": "not_in_chain"})
