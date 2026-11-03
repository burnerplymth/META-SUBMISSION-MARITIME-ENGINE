"""Resolve an episode spec (scripts/research_episode_spec.py) into the library entry served by
GET /research/episodes: event sources are looked up in the actuals files they cite."""
from __future__ import annotations

import json
from pathlib import Path


def _figure(repo: Path, directory: str, name: str) -> dict:
    doc = json.loads((repo / "data" / "sim_backtest" / directory / "actuals.json").read_text("utf-8"))
    for f in doc["figures"]:
        if f["name"] == name:
            return f
    raise KeyError(f"{directory}/{name}")


def resolve_episode(ep: dict, repo: Path) -> dict:
    events = []
    for e in ep["events"]:
        src = e["src"]
        if isinstance(src, tuple):
            f = _figure(repo, src[1], src[2])
            source = {"publisher": f["source_name"], "url": f["source_url"]}
        else:
            source = {"publisher": src["publisher"], "url": src["url"]}
        source["date"] = e["date"]
        source["date_basis"] = e["date_basis"]
        events.append({"id": f"{ep['id']}:{e['slug']}", "t": e["t"], "t_end": e["t_end"],
                       "category": e["category"], "title": e["title"], "place": e["place"],
                       "source": source})
    events.sort(key=lambda x: (x["t"], x["id"]))
    return {"id": ep["id"], "title": ep["title"], "places": list(ep["places"]),
            "window": dict(ep["window"]), "fetch_window": dict(ep["fetch"]),
            "categories": sorted({e["category"] for e in events}), "events": events,
            "sim_runs": list(ep.get("sim_runs") or []), "notes_chip": ep["notes_chip"]}
