"""Compose one brief document: facts, writer, verifier, tables, figures,
sources, content hash. Pure given its inputs (the provider, the app context,
the fetchers and a clock), so a stored brief regenerates to the same bytes."""
from __future__ import annotations

from datetime import datetime

from . import assemble as asm
from . import compose, facts as factmod, figures, fmt, sections as secmod, writer
from .model import (LABEL, PROMPT_NAME, SCHEMA, SECTION_BY_ID, STYLE, BriefError,
                    canonical, included_sections, sha256_hex)


def _graph_nodes(ctx):
    try:
        try:
            from llm import grounding
        except ImportError:
            from server.llm import grounding  # type: ignore
        return grounding.graph_places(ctx.graph_path)
    except Exception:
        return None


def inputs_digest(built, req: dict, section_ids: list[str], prompt_version: str) -> str:
    """Hash of the canonical inputs plus the prompt version: the brief id."""
    core = {"parts": built.digest_parts, "sections": section_ids, "style": STYLE,
            "prompt": f"{PROMPT_NAME}@{prompt_version}", "schema": SCHEMA,
            "lane": req.get("lane_plan"), "mc": req.get("mc_job_id"),
            "ctx": req.get("research_episode_id")}
    return "brf_" + sha256_hex(canonical(core))[:16]


def prepare(ctx, bundle, req: dict, fetch, prompt_version: str):
    """Facts first (they fix the brief id). -> (built, section_ids, brief_id)."""
    section_ids = included_sections(req)
    built = factmod.build_facts(ctx, bundle, req, section_ids, fetch)
    brief_id = inputs_digest(built, req, section_ids, prompt_version)
    return built, section_ids, brief_id


def served_sections(built, section_ids: list[str]) -> list[dict]:
    return [SECTION_BY_ID[s] for s in section_ids
            if SECTION_BY_ID[s]["model"] and built.states.get(s, {}).get("state") == "SERVED"
            and s in compose.COMPOSERS]


def build_document(ctx, bundle, req: dict, built, section_ids: list[str], brief_id: str, *,
                   draft, writer_info: dict, now: datetime) -> dict:
    """Assemble the final document from a writer draft (a dict or None)."""
    facts_list = built.facts.items
    ids_map = {f["key"]: f["id"] for f in facts_list}
    served = [s["id"] for s in served_sections(built, section_ids)]
    a = asm.assemble(draft, served, facts_list, ids_map, _graph_nodes(ctx),
                     source=writer_info.get("source", "model"))
    # figures
    figs: dict[str, dict] = {}
    by_section: dict[str, dict] = {}
    f1 = figures.chain_delays(bundle)
    if f1:
        figs[f1["id"]] = f1
        by_section["chain"] = f1
    if built.states.get("delay", {}).get("state") == "SERVED":
        f2 = figures.lane_ranges(built.raw.get("lane_routes") or [], built.raw.get("lane_pair") or "")
        if f2:
            figs[f2["id"]] = f2
            by_section["delay"] = f2
    if built.states.get("uncertainty", {}).get("state") == "SERVED":
        f3 = figures.mc_band(built.raw.get("mc_series"), req.get("mc_job_id") or "")
        if f3:
            figs[f3["id"]] = f3
            by_section["uncertainty"] = f3
    secs = secmod.build_sections(built, built.facts, section_ids, a["blocks"], by_section)
    for s in secs:
        s["title"] = SECTION_BY_ID[s["id"]]["title"]

    prov = writer_info["provenance"]
    d = bundle.journal["disruption"]
    seed_name = bundle.name(d["node_id"])
    kind_word = factmod.KIND_WORDS.get(str(d.get("kind")), "disruption")
    out_facts = [{k: v for k, v in f.items() if k != "names"} for f in facts_list]
    doc = {
        "schema": SCHEMA,
        "brief_id": brief_id,
        "title": "Disruption brief",
        "subject": f"{kind_word.capitalize()} at {seed_name}",
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "label": "Recorded replay. Modelled from a recorded chain. Not a forecast." if getattr(bundle, "recorded_replay", False) else LABEL,
        "style": STYLE,
        "transport": prov["transport"],
        "provenance": prov,
        "writer": {"source": writer_info.get("source", "model"),
                   "fallback_reason": writer_info.get("fallback_reason"),
                   "prompt_id": prov.get("prompt_id"), "model": prov.get("model")},
        "cost": {"usd": prov.get("cost_usd", 0.0),
                 "estimated_usd": prov.get("estimated_cost_usd", 0.0)},
        "run": {"run_id": bundle.run_id, "stream_hash": bundle.record.get("stream_hash"),
                "engine_label": bundle.journal.get("engine_label") or "ENGINE REV 1",
                "map_version": bundle.journal.get("graph_version"),
                "recorded_replay": bool(getattr(bundle, "recorded_replay", False))},
        "request": {k: req.get(k) for k in ("run_id", "mc_job_id", "lane_plan",
                                             "research_episode_id", "style")},
        "include": section_ids,
        "summary": a["summary"],
        "key_figures": secmod.key_figures(built.facts),
        "sections": secs,
        "figures": list(figs.values()),
        "facts": out_facts,
        "sources": secmod.build_sources(facts_list),
        "verifier": a["verifier"],
        "words": a["words"],
    }
    doc["sha256"] = content_hash(doc)
    doc["exports"] = export_urls(brief_id)
    return doc


def content_hash(doc: dict) -> str:
    core = {k: v for k, v in doc.items() if k not in ("sha256", "exports")}
    return sha256_hex(canonical(core))


def export_urls(brief_id: str) -> dict:
    base = f"/brief/{brief_id}/export"
    return {"html_url": f"{base}?format=html", "pdf_url": f"{base}?format=pdf",
            "json_url": f"{base}?format=json"}
