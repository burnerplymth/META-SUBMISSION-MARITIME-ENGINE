"""The deterministic template writer.

Composes the prose of every section from the facts table, with {Fn}
placeholders only. It is used three ways: as the recorded (fixture) writer
that answers when no live model is called, as the replacement for any block
the verifier rejects, and as the fallback when a model reply cannot be read.

Wording is descriptive and conditional: it says what the model returns for
the stated inputs. It uses no number words and no causal, accuracy or
prediction wording, so it passes the verifier it is the fallback for.
"""
from __future__ import annotations

import re

PH_RE = re.compile(r"\{(F\d+)\}")


class Ids:
    """key -> placeholder lookups over a {key: id} mapping."""

    def __init__(self, mapping: dict[str, str]) -> None:
        self.m = mapping

    def has(self, *keys: str) -> bool:
        return all(k in self.m for k in keys)

    def ph(self, key: str) -> str:
        return "{" + self.m[key] + "}"

    def prefixed(self, prefix: str) -> list[str]:
        return [k for k in self.m if k.startswith(prefix)]


def _blk(bid: str, kind: str, text: str = "", items: list[str] | None = None) -> dict:
    ids = []
    for s in [text] + list(items or []):
        for f in PH_RE.findall(s):
            if f not in ids:
                ids.append(f)
    return {"id": bid, "kind": kind, "text": text, "items": list(items or []), "fact_ids": ids}


def summary(i: Ids) -> str:
    parts = []
    if i.has("chain.events", "chain.places"):
        parts.append(f"The model returns a causal chain of {i.ph('chain.events')} events across "
                     f"{i.ph('chain.places')} places")
        if i.has("chain.worst", "chain.worst_place"):
            parts.append(f", with a largest single added delay of {i.ph('chain.worst')} at "
                         f"{i.ph('chain.worst_place')}")
        parts.append(".")
    elif i.has("sit.place"):
        parts.append(f"The model is run for a disruption at {i.ph('sit.place')}.")
    return "".join(parts)


def situation(i: Ids) -> list[dict]:
    s = ""
    if i.has("sit.kind", "sit.place"):
        s = f"A {i.ph('sit.kind')} at {i.ph('sit.place')} is modelled"
        if i.has("sit.loss"):
            s += f" with {i.ph('sit.loss')} of capacity lost"
        if i.has("sit.duration"):
            s += f" for {i.ph('sit.duration')}"
        if i.has("sit.start"):
            s += f", starting {i.ph('sit.start')}"
        s += "."
    items = []
    if i.has("sit.basis"):
        items.append(f"Basis: {i.ph('sit.basis')}.")
    if i.has("sit.cite"):
        items.append(f"Source: {i.ph('sit.cite')}.")
    if i.has("sit.engine"):
        items.append(f"Produced by {i.ph('sit.engine')}, run {i.ph('sit.run')}.")
    out = []
    if s:
        out.append(_blk("p1", "paragraph", s))
    if items:
        out.append(_blk("b1", "bullets", items=items))
    return out


def chain(i: Ids) -> list[dict]:
    out = []
    if i.has("chain.places", "chain.events"):
        s = (f"The causal chain reaches {i.ph('chain.places')} places through "
             f"{i.ph('chain.events')} events")
        if i.has("chain.span"):
            s += f" within {i.ph('chain.span')} of the start"
        s += "."
        out.append(_blk("p1", "paragraph", s))
    items = []
    if i.has("chain.channels"):
        items.append(f"Channels in the chain: {i.ph('chain.channels')}.")
    if i.has("chain.worst", "chain.worst_place"):
        items.append(f"Largest single added delay: {i.ph('chain.worst')} at {i.ph('chain.worst_place')}.")
    if items:
        out.append(_blk("b1", "bullets", items=items))
    return out


def places(i: Ids) -> list[dict]:
    out = []
    if i.has("places.count"):
        out.append(_blk("p1", "paragraph",
                        f"{i.ph('places.count')} places are hit apart from the disrupted place; "
                        "the largest modelled delays by place and by leg follow."))
    items = [f"{i.ph(k)}." for k in i.prefixed("place.")[:3]]
    if items:
        out.append(_blk("b1", "bullets", items=items))
    return out


def delay(i: Ids) -> list[dict]:
    out = []
    if i.has("lane.pair", "lane.base.total", "lane.base.median"):
        s = (f"On the {i.ph('lane.pair')} lane the baseline route totals {i.ph('lane.base.total')} "
             f"at arrival, with a median of {i.ph('lane.base.median')}")
        if i.has("lane.n_speed", "lane.n_queue"):
            s += (f", over {i.ph('lane.n_speed')} speed variants and {i.ph('lane.n_queue')} "
                  "queue variants")
        s += "."
        out.append(_blk("p1", "paragraph", s))
    items = []
    if i.has("lane.base.sail"):
        items.append(f"Sail time: {i.ph('lane.base.sail')} (modelled).")
    if i.has("lane.base.wait"):
        items.append(f"Wait at arrival: {i.ph('lane.base.wait')} (simulated).")
    if i.has("lane.end"):
        items.append(f"The disruption ends {i.ph('lane.end')} after the start across the duration variants.")
    if items:
        out.append(_blk("b1", "bullets", items=items))
    return out


def uncertainty(i: Ids) -> list[dict]:
    out = []
    if i.has("mc.n", "mc.worst.band", "mc.worst.p50"):
        out.append(_blk("p1", "paragraph",
                        f"Across {i.ph('mc.n')}, the largest added delay lies between "
                        f"{i.ph('mc.worst.band')} (p10 to p90), with a median of {i.ph('mc.worst.p50')}."))
    items = [f"{i.ph(k)}." for k in i.prefixed("mc.exceed.")[:2]]
    if i.has("mc.sens"):
        items.append(f"Largest input swing: {i.ph('mc.sens')}.")
    if i.has("mc.label"):
        items.append(f"{i.ph('mc.label')}")
    if items:
        out.append(_blk("b1", "bullets", items=items))
    return out


def exposure(i: Ids) -> list[dict]:
    out = []
    if i.has("exp.affected", "exp.n"):
        s = f"{i.ph('exp.affected')} of {i.ph('exp.n')} shipments are hit"
        if i.has("exp.added", "exp.added_median"):
            s += (f", with added days of {i.ph('exp.added')} and a median of "
                  f"{i.ph('exp.added_median')}")
        s += "."
        out.append(_blk("p1", "paragraph", s))
    items = []
    if i.has("exp.value"):
        items.append(f"Value in affected shipments: {i.ph('exp.value')} (listed value, not a loss estimate).")
    if i.has("exp.not_served"):
        items.append(f"Shipments on lanes that are not served: {i.ph('exp.not_served')}.")
    if items:
        out.append(_blk("b1", "bullets", items=items))
    return out


def context(i: Ids) -> list[dict]:
    out = []
    if i.has("ctx.title"):
        s = f"{i.ph('ctx.title')}"
        if i.has("ctx.window"):
            s += f" ({i.ph('ctx.window')})"
        s += " is listed as dated history for comparison."
        out.append(_blk("p1", "paragraph", s))
    items = [f"Source: {i.ph(k)}." for k in i.prefixed("ctx.ev.")[:2]]
    if items:
        out.append(_blk("b1", "bullets", items=items))
    return out


def data(i: Ids) -> list[dict]:
    out = []
    if i.has("data.live", "data.snapshot", "data.not_served"):
        out.append(_blk("p1", "paragraph",
                        f"Feed states at generation: {i.ph('data.live')} live, "
                        f"{i.ph('data.snapshot')} snapshot, {i.ph('data.not_served')} not served."))
    return out


COMPOSERS = {"situation": situation, "chain": chain, "places": places, "delay": delay,
             "uncertainty": uncertainty, "exposure": exposure, "context": context, "data": data}


def compose_section(section_id: str, i: Ids) -> list[dict]:
    fn = COMPOSERS.get(section_id)
    return fn(i) if fn else []


def compose_block(section_id: str, block_id: str, i: Ids) -> dict | None:
    for b in compose_section(section_id, i):
        if b["id"] == block_id:
            return b
    return None


def compose_all(section_ids: list[str], i: Ids) -> dict:
    """The full recorded draft in the writer's JSON shape."""
    return {"summary": summary(i),
            "sections": [{"id": s, "blocks": compose_section(s, i)}
                         for s in section_ids if s in COMPOSERS]}
