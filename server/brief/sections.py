"""Section documents: the writer's verified blocks plus the server-built
tables and chips, all read from the facts table. A table cell is a
formatted string; a row names the fact it was read from."""
from __future__ import annotations

from . import fmt
from .model import METHOD_LINES, SECTION_BY_ID

PROV_CHIPS = ("INPUT", "MODELLED", "SIMULATED", "SOURCED", "SNAPSHOT", "LIVE", "NOT SERVED")


def _col(label: str, unit: str | None = None, num: bool = False) -> dict:
    return {"label": label, "unit": unit, "num": num}


def _table(title: str, columns: list[dict], rows: list[dict], chip: str | None = None,
           note: str | None = None) -> dict:
    return {"id": "t:" + title.lower().replace(" ", "_")[:24], "kind": "table", "title": title,
            "columns": columns, "rows": rows, "chip": chip, "note": note, "fact_ids": [
                r["fact_id"] for r in rows if r.get("fact_id")]}


def _fid(facts, key):
    f = facts.get(key)
    return f["id"] if f else None


def chain_table(built, facts) -> dict | None:
    rows = []
    for r in built.raw.get("event_rows") or []:
        rows.append({"fact_id": _fid(facts, f"chain.ev.{r['seq']}"), "cells": [
            str(r["seq"]), r["place"], r["channel"], r["mechanism"] or "-",
            fmt.num(r["added_d"]), "-" if r["arrival_d"] is None else fmt.num(r["arrival_d"])]})
    if not rows:
        return None
    return _table("Chain events in order",
                  [_col("Seq", None, True), _col("Place"), _col("Channel"), _col("Mechanism"),
                   _col("Added", "d", True), _col("Reached on day", "d", True)],
                  rows, "MODELLED")


def places_tables(built, facts) -> list[dict]:
    out = []
    prow = built.raw.get("place_rows") or []
    if prow:
        has_range = any(r.get("range") for r in prow)
        has_cap = any(r.get("capacity_at_risk") is not None for r in prow)
        cols = [_col("Place"), _col("Channel"), _col("Largest added delay", "d", True)]
        if has_range:
            cols.append(_col("Simulated range, p10 to p90", "d", True))
        if has_cap:
            cols.append(_col("Capacity at risk", None, True))
        rows = []
        for r in prow:
            cells = [r["place"], r["channel"], fmt.num(r["added_d"])]
            if has_range:
                cells.append(fmt.rng(r["range"]["low"], r["range"]["high"], "").strip()
                             if r.get("range") else "-")
            if has_cap:
                cells.append("-" if r.get("capacity_at_risk") is None else
                             f"{fmt.num(r['capacity_at_risk'], 0)} {r.get('unit_class') or ''}".strip())
            rows.append({"fact_id": _fid(facts, f"place.{r['place_id']}"), "cells": cells})
        out.append(_table("Places by largest modelled delay", cols, rows,
                          "SIMULATED" if has_range else "MODELLED",
                          None if has_range else "Single run; no range supplied."))
    lrow = built.raw.get("leg_rows") or []
    if lrow:
        rows = [{"fact_id": _fid(facts, f"leg.{r['seq']}"),
                 "cells": [r["from"], r["to"], r["channel"], fmt.num(r["added_d"])]} for r in lrow]
        out.append(_table("Legs by delay added",
                          [_col("From"), _col("To"), _col("Channel"), _col("Added", "d", True)],
                          rows, "MODELLED"))
    return out


def delay_table(built, facts) -> dict | None:
    routes = built.raw.get("lane_routes") or []
    if not routes:
        return None
    rows = []
    for r in routes:
        f = facts.get(f"lane.route.{r['route_id']}")
        rows.append({"fact_id": f["id"] if f else None, "cells": [
            r["label"] + (" (baseline)" if r["baseline"] else ""),
            fmt.rng(r["sail"]["low"], r["sail"]["high"], "") if r["sail"] else "-",
            fmt.rng(r["wait"]["low"], r["wait"]["high"], "") if r["wait"] else "-",
            fmt.num(r["total"]["low"]), fmt.num(r["total"]["median"]), fmt.num(r["total"]["high"])]})
    return _table("Named variants, low to high",
                  [_col("Route"), _col("Sail", "d", True), _col("Wait at arrival", "d", True),
                   _col("Total low", "d", True), _col("Total median", "d", True),
                   _col("Total high", "d", True)], rows, "SIMULATED",
                  "Low is the smallest and high the largest over the named variants; "
                  "median is the base variant.")


def mc_tables(built, facts) -> list[dict]:
    out = []
    ins = facts.keys_with_prefix("mc.in.")
    if ins:
        rows = [{"fact_id": f["id"], "cells": [
            f["value"]["input"], str(f["value"].get("dist") or "-"),
            fmt.rng(f["value"]["low"], f["value"]["high"], f["value"].get("unit") or "").strip()
            + (f", mode {fmt.num(f['value']['mode'])}" if f["value"].get("mode") is not None else "")]}
            for f in ins]
        out.append(_table("Input ranges", [_col("Input"), _col("Distribution"), _col("Range")],
                          rows, "INPUT"))
    mrows = built.raw.get("mc_rows") or []
    if mrows:
        rows = []
        for r in mrows:
            k, n = r.get("affected_k"), r.get("n")
            rows.append({"fact_id": _fid(facts, f"mc.place.{r['place_id']}"), "cells": [
                r["place"], fmt.num(r["p10"]), fmt.num(r["p50"]), fmt.num(r["p90"]),
                "-" if k is None or n is None else f"{fmt.num(k, 0)} of {fmt.num(n, 0)}"]})
        out.append(_table("Places by simulated delay",
                          [_col("Place"), _col("p10", "d", True), _col("p50", "d", True),
                           _col("p90", "d", True), _col("Simulations affected", None, True)],
                          rows, "SIMULATED"))
    return out


def exposure_table(built, facts) -> dict | None:
    rows = [{"fact_id": _fid(facts, f"exp.place.{i + 1}"),
             "cells": [r["place"], fmt.num(r["shipments"], 0)]}
            for i, r in enumerate(built.raw.get("exposure_rows") or [])]
    if not rows:
        return None
    return _table("Most exposed places", [_col("Place"), _col("Shipments", None, True)], rows,
                  "MODELLED")


def context_table(built, facts) -> dict | None:
    rows = []
    for i, r in enumerate(built.raw.get("context_rows") or []):
        f = facts.get(f"ctx.ev.{i + 1}")
        rows.append({"fact_id": f["id"] if f else None, "cells": [
            fmt.date(r.get("date")), r["title"], r["publisher"]]})
    if not rows:
        return None
    return _table("Dated events", [_col("Date"), _col("Event"), _col("Publisher")], rows, "SOURCED")


def feed_table(built, facts) -> dict | None:
    rows = []
    for r in built.raw.get("feed_rows") or []:
        rows.append({"fact_id": _fid(facts, f"feed.{r['feed']}"), "state": r["state"], "cells": [
            r["label"], r["state"], fmt.stamp(r["retrieved_at"]) if r["retrieved_at"] else "-"]})
    if not rows:
        return None
    return _table("Feed board at generation", [_col("Feed"), _col("State"), _col("Retrieved")], rows,
                  None)


def section_chips(facts_in_section: list[dict]) -> list[str]:
    seen = []
    for f in facts_in_section:
        c = f.get("chip")
        if c in PROV_CHIPS and c not in seen and c not in ("LIVE", "SNAPSHOT"):
            seen.append(c)
    return seen


def build_sections(built, facts, section_ids: list[str], blocks: dict, figures: dict) -> list[dict]:
    """facts: the FactTable. blocks: {section_id: assembled blocks}."""
    out = []
    for sid in section_ids:
        meta = SECTION_BY_ID[sid]
        st = built.states.get(sid, {"state": "SERVED", "reason": None})
        sec = {"id": sid, "title": meta["title"], "state": st["state"], "reason": st["reason"],
               "chips": [], "blocks": []}
        if sid == "method":
            sec["state"] = "SERVED"
            sec["chips"] = ["MODELLED"]
            sec["blocks"] = [{"id": "method.b1", "kind": "bullets", "source": "static", "fact_ids": [],
                              "items": [{"text": t, "segments": [{"t": t}], "fact_ids": []}
                                        for t in METHOD_LINES]}]
            out.append(sec)
            continue
        if st["state"] != "SERVED":
            sec["chips"] = ["NOT SERVED"]
            out.append(sec)
            continue
        sec["chips"] = [] if sid == "data" else section_chips(facts.in_section(sid))
        sec["blocks"] = list(blocks.get(sid, []))
        extra: list[dict] = []
        if sid == "chain":
            t = chain_table(built, facts)
            extra = [t] if t else []
        elif sid == "places":
            extra = places_tables(built, facts)
        elif sid == "delay":
            t = delay_table(built, facts)
            extra = [t] if t else []
        elif sid == "uncertainty":
            extra = mc_tables(built, facts)
        elif sid == "exposure":
            t = exposure_table(built, facts)
            extra = [t] if t else []
        elif sid == "context":
            t = context_table(built, facts)
            extra = [t] if t else []
        elif sid == "data":
            t = feed_table(built, facts)
            extra = [t] if t else []
        sec["blocks"].extend(extra)
        fig = figures.get(sid)
        if fig:
            sec["blocks"].append({"id": f"{sid}.fig", "kind": "figure", "figure_id": fig["id"],
                                  "title": fig["title"], "chip": fig["chip"], "fact_ids": []})
        out.append(sec)
    return out


KEY_FIGURES = (
    ("sit.duration", "Disruption duration"),
    ("sit.loss", "Capacity lost"),
    ("chain.places", "Places on the chain"),
    ("chain.worst", "Largest added delay"),
    ("lane.base.total", "Lane, total at arrival"),
    ("mc.worst.band", "Simulated range, p10 to p90"),
    ("exp.affected", "Shipments hit"),
)


def key_figures(facts, limit: int = 6) -> list[dict]:
    out = []
    for key, label in KEY_FIGURES:
        f = facts.get(key)
        if f is None:
            continue
        out.append({"label": label, "value": f["display"], "fact_id": f["id"], "chip": f["chip"]})
        if len(out) >= limit:
            break
    return out


def build_sources(facts: list[dict]) -> list[dict]:
    """Distinct sources behind the facts, numbered S1.. in first-use order."""
    order: list[str] = []
    book: dict[str, dict] = {}
    for f in facts:
        s = f["source"]
        t = s.get("type")
        if t == "citation":
            key = "cite:" + str(s.get("url"))
            entry = {"kind": "citation", "label": s.get("label"), "publisher": s.get("publisher"),
                     "title": s.get("title"), "url": s.get("url"), "date": fmt.date(s["date"])
                     if isinstance(s.get("date"), str) and s["date"][:2].isdigit() and "-" in s["date"]
                     else s.get("date")}
        elif t in ("run", "chain_event", "scenario"):
            rid = s.get("run_id")
            key = f"run:{rid}"
            entry = {"kind": "run", "label": (f"Recorded replay chain {rid}" if str(rid).startswith("replay_")
                                              else f"Stored run {rid}: journal and event stream")}
        elif t == "event":
            key = f"event:{s.get('event_id')}"
            entry = {"kind": "feed_event", "label": s.get("label"), "retrieved_at": s.get("retrieved_at")}
        elif t == "lane_plan":
            key = f"plan:{s.get('plan_id')}"
            entry = {"kind": "lane_plan", "label": f"Lane planner, plan {s.get('plan_id')}"}
        elif t == "montecarlo":
            key = f"mc:{s.get('job_id')}"
            entry = {"kind": "montecarlo", "label": f"Monte Carlo job {s.get('job_id')}"}
        elif t == "exposure":
            key = "exposure"
            entry = {"kind": "exposure", "label": "Shipment exposure summary"}
        elif t == "research":
            key = f"research:{s.get('episode_id')}"
            entry = {"kind": "research", "label": f"Research episode {s.get('episode_id')}"}
        elif t == "feed":
            key = f"feed:{s.get('feed') or 'board'}"
            entry = {"kind": "feed", "label": s.get("label"), "state": s.get("state"),
                     "retrieved_at": s.get("retrieved_at")}
        else:
            continue
        if key not in book:
            book[key] = {**entry, "fact_ids": []}
            order.append(key)
        book[key]["fact_ids"].append(f["id"])
    out = []
    for n, key in enumerate(order, 1):
        e = book[key]
        e["id"] = f"S{n}"
        out.append(e)
    return out
