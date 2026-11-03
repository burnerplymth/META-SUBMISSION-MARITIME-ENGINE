#!/usr/bin/env python3
"""Record the causal chain each engine configuration produces on the preregistered scenario of a curated
past event (Replay window). Output: data/replay_episodes/chains/<episode>__<config>.json, one
``GET /chain/{run_id}``-shaped document per pair, plus a MANIFEST.json of digests.

Nothing here grades anything and no outcome file is read. The engines are called unmodified through the VAL-01
helpers (``server/eval/val01.py``), on the scenario stored in ``data/eval/val01/episodes.json``. Each recorded
chain is checked against the prediction digest already published in the VAL-01 result files (first lag, first
delay and maximum delay per place); a mismatch aborts the build. Re-running writes byte-identical files.

    .venv/bin/python scripts/build_replay_chains.py [--check]

``--check`` rebuilds in memory and fails when a stored file differs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "server"))

from eval import val01 as v  # noqa: E402
from chain_edges import materialise_edges  # noqa: E402
from engine.canonical import stream_hash  # noqa: E402

OUT = REPO / "data" / "replay_episodes" / "chains"
SCHEMA = "supermap.replay.chain/1"

# episode id (VAL-01) -> replay episode id
EPISODES = {
    "evergiven_2021": "ever_given",
    "la_2021": "la_lb",
    "baltimore_2024": "baltimore",
    "panama_2023_24": "panama",
    "redsea_2024": "red_sea",
}
# TEST episodes are never scored here; the chain is only recorded for configurations that were scored on it once.
TEST_CONFIGS = {"la_2021": ("v1", "rev2", "rev2_services", "rev2_calibrated")}
KEEP = ("seq", "parent_id", "node_id", "channel", "mechanism", "delay_d", "magnitude", "at", "tick")


def published_digest(config: str, vid: str) -> dict | None:
    """The prediction digest stored in the published VAL-01 result of (config, episode), or None."""
    d = REPO / "data" / "eval" / "val01"
    for p in sorted(d.glob("results_*.json")):
        doc = json.loads(p.read_text(encoding="utf-8"))
        if doc.get("config") != config:
            continue
        for e in doc.get("episodes", []):
            if e.get("episode_id") == vid:
                return e.get("prediction_digest")
    return None


def build_one(engine, ep: dict, replay_id: str, config: str) -> dict:
    spec = v.disruption_spec(ep)
    run_id = f"replay_{replay_id}_{config}"
    state = engine.run(run_id, spec, None)
    events = [{k: e.get(k) for k in KEEP} for e in state.events]
    digest = v.digest_events(state.events, spec)
    pub = published_digest(config, ep["id"])
    if pub is not None and json.dumps(pub, sort_keys=True) != json.dumps(digest, sort_keys=True):
        raise SystemExit(f"digest mismatch for {ep['id']} / {config}: the recorded chain differs from the published result")
    return {
        "schema": SCHEMA, "episode": replay_id, "config": config, "run_id": run_id, "status": "complete",
        "disruption": spec, "event_count": len(events), "stream_hash": stream_hash(state.events),
        "events": events, "edges": materialise_edges(state.events, spec["node_id"]),
        "digest": digest, "digest_checked_against_published": pub is not None,
    }


def canonical(doc: dict) -> str:
    return json.dumps(doc, sort_keys=True, indent=1, ensure_ascii=False) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    eps = {e["id"]: e for e in v.load_episodes()}
    out: dict[str, str] = {}
    refused: dict[str, str] = {}
    for config in v.CONFIGS:
        engine, _g = v.build_engine(config)
        for vid, replay_id in EPISODES.items():
            ep = eps[vid]
            if vid in TEST_CONFIGS and config not in TEST_CONFIGS[vid]:
                continue
            try:
                doc = build_one(engine, ep, replay_id, config)
            except SystemExit:
                raise
            except Exception as exc:  # a SHELL-tier seed: the engines accept core seeds only
                refused[f"{replay_id}__{config}"] = f"{type(exc).__name__}: {exc}"
                continue
            out[f"{replay_id}__{config}.json"] = canonical(doc)
    manifest = {"schema": SCHEMA + ".manifest", "files": {k: hashlib.sha256(s.encode()).hexdigest() for k, s in sorted(out.items())},
                "not_run": dict(sorted(refused.items()))}
    out["MANIFEST.json"] = canonical(manifest)
    if args.check:
        bad = [k for k, s in out.items() if not (OUT / k).exists() or (OUT / k).read_text(encoding="utf-8") != s]
        extra = [p.name for p in OUT.glob("*.json") if p.name not in out]
        if bad or extra:
            print("stale:", bad, "extra:", extra)
            return 1
        print(f"ok: {len(out)} files")
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    for k, s in out.items():
        (OUT / k).write_text(s, encoding="utf-8")
    print(f"wrote {len(out)} files to {OUT.relative_to(REPO)}; not run: {sorted(refused)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
