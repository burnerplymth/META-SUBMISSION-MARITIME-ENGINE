"""BACKTEST ENGINE REV 2: the scorecard protocol re-run on engine2.

The protocol, the grading rules and every metric definition are engine v1's
(server/engine/scorecard.py is imported, not copied): coverage, first-hit
delay window [0.5x, 2x], false positives, the budget-matched naive k-hop
baseline, legacy grading and the Clopper-Pearson intervals are unchanged.
Only the engine under test differs. Each replay episode is run ONCE; the
result is published whatever it says. The v1 files data/replay/scorecard.json
and scorecard.md are NOT touched; the rev-2 artifact is written to
data/replay/rev2/ (a subdirectory, because engine v1's scorecard CLI and the
desks replay list glob data/replay/*.json and would read a second scorecard
there as a replay dataset).

CLI: python -m engine2.scorecard_rev2 [--out-dir D]
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from engine.graph import load_graph
from engine.scorecard import (HONESTY_CAVEATS, build_scorecard, load_entries,
                              render_markdown)

from .engine import Engine2
from .plugins import ENGINE_LABEL, PLUGINS_HASH

REPO_ROOT = Path(__file__).resolve().parents[2]
LABEL = "BACKTEST ENGINE REV 2"

REV2_MODEL_CAVEAT = (
    "Engine model (ENGINE REV 2, server/engine2): a stock-and-flow world "
    "model. Places hold a queue stock and a cover stock and serve at "
    "mu_max x (1 - loss); legs are FIFO delay lines of base_transit_d; wait "
    "= backlog / service rate (Little) evaluated on the cumulative curves; "
    "nothing above queue_cap is dropped (spillback holds it at the sender); "
    "blocked flow is re-routed onto the authored bypass legs up to their "
    "headroom; cover days against the shortfall rate give stockout times; "
    "sea flows are in vessel-calls/day (ports: TEU/day / teu_per_call), "
    "removing engine v1's mixed-unit sum. Every non-identity number is a "
    "registered plug-in (server/engine2/plugins.py); mu_max = 1.3 x baseline load, the 7-day cover "
    "and the cross-layer equivalence are TEMPORARY placeholders.")

REV2_INSAMPLE_CAVEAT = (
    "The replay episodes are in-sample for this engine revision: it was "
    "configured with sight of them, and one definition was adopted after "
    "viewing a run on them. No plug-in value was set or changed to move a "
    "replay number; plug-in defaults come from the published sources cited "
    "in each plug-in's registry entry or from pinned graph/PortWatch files. "
    "The adopted definition: an inventory (stockout) event reports the "
    "length of the supply gap as delay_d, because it reported 0.0 d and "
    "could never be window-graded. This is therefore an in-sample check on "
    "the same three episodes, not a held-out evaluation.")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def generate(out_dir: Path | None = None) -> dict:
    out_dir = Path(out_dir) if out_dir else REPO_ROOT / "data" / "replay" / "rev2"
    graph_path = REPO_ROOT / "data" / "graph" / "graph.json"
    replay_dir = REPO_ROOT / "data" / "replay"
    paths = sorted(p for p in replay_dir.glob("*.json") if p.name != "scorecard.json")
    engine = Engine2(load_graph(graph_path))
    card = build_scorecard(engine, load_entries(paths))
    v1_json = replay_dir / "scorecard.json"
    v1 = json.loads(v1_json.read_text(encoding="utf-8"))
    card["artifact"] = "supermap-scorecard-engine-rev2"
    card["label"] = LABEL
    card["engine_rev"] = 2
    card["engine_label"] = ENGINE_LABEL
    card["plugin_registry_hash"] = PLUGINS_HASH
    caveats = [c for c in HONESTY_CAVEATS if not c.startswith("Engine model (revision 2")]
    caveats = [c for c in caveats if not c.startswith("IN-SAMPLE")] + [
        REV2_MODEL_CAVEAT, REV2_INSAMPLE_CAVEAT,
        ("IN-SAMPLE (inherited): the three episodes are the same set both "
         "engines are checked against; the 25-event protocol v2 is still "
         "deferred and has NOT been run.")]
    card["honesty_caveats"] = caveats
    card["v1_reference"] = {
        "file": "data/replay/scorecard.json", "sha256": _sha(v1_json),
        "note": "engine v1 scorecard, byte-untouched",
        "aggregate_headline": {
            "coverage": v1["aggregate"]["coverage"],
            "delay_accuracy": {k: v1["aggregate"]["delay_accuracy"][k] for k in
                               ("graded_steps", "hits", "misses", "hit_fraction",
                                "mean_abs_error_d")},
            "false_positives_count": v1["aggregate"]["false_positives"]["count"],
            "baseline_k_hop_pooled": v1["aggregate"]["statistics"]["baseline_k_hop_pooled"],
            "engine_precision": v1["aggregate"]["statistics"]["engine_precision"],
        }}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "scorecard_rev2.json").write_text(
        json.dumps(card, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out_dir / "scorecard_rev2.md").write_text(render_md(card), encoding="utf-8")
    return card


def _fr(x):
    return "—" if x is None else (f"{x * 100:.1f}%")


def render_md(card: dict) -> str:
    body = render_markdown(card).split("\n")
    # replace the v1 title; keep every table and definition v1 renders
    body[0] = "# " + LABEL + " — SupermAP Scorecard, chain engine rev 2"
    v1 = card["v1_reference"]["aggregate_headline"]
    a = card["aggregate"]
    hdr = [
        "", "**" + LABEL + "** — engine under test: " + ENGINE_LABEL
        + " (plug-in registry `" + card["plugin_registry_hash"][:12] + "`). "
        "Protocol, grading rules and metric definitions are engine v1's, unchanged; "
        "each replay episode ran once; the result is published as it came out.",
        "",
        "Engine v1's `data/replay/scorecard.json` / `scorecard.md` are byte-untouched "
        "(sha256 `" + card["v1_reference"]["sha256"][:16] + "...`).",
        "", "## Headline: rev 2 against rev 1 (same three episodes, same grading)", "",
        "| Metric | Engine v1 | ENGINE REV 2 |", "|---|---|---|",
        "| Node coverage | %d / %d (%s) | %d / %d (%s) |" % (
            v1["coverage"]["covered_count"], v1["coverage"]["distinct_non_seed_nodes"],
            _fr(v1["coverage"]["fraction"]), a["coverage"]["covered_count"],
            a["coverage"]["distinct_non_seed_nodes"], _fr(a["coverage"]["fraction"])),
        "| Delay hits (window 0.5x-2x) | %d / %d | %d / %d |" % (
            v1["delay_accuracy"]["hits"], v1["delay_accuracy"]["graded_steps"],
            a["delay_accuracy"]["hits"], a["delay_accuracy"]["graded_steps"]),
        "| Mean abs delay error (d) | %s | %s |" % (
            v1["delay_accuracy"]["mean_abs_error_d"], a["delay_accuracy"]["mean_abs_error_d"]),
        "| False positives | %d | %d |" % (v1["false_positives_count"], a["false_positives"]["count"]),
        "| Engine precision | %s | %s |" % (_fr(v1["engine_precision"]),
                                           _fr(a["statistics"]["engine_precision"])),
        "| Naive k-hop baseline coverage (budget-matched) | %s | %s |" % (
            _fr(v1["baseline_k_hop_pooled"]["coverage_fraction"]),
            _fr(a["statistics"]["baseline_k_hop_pooled"]["coverage_fraction"])),
        "| Naive k-hop baseline precision | %s | %s |" % (
            _fr(v1["baseline_k_hop_pooled"]["precision"]),
            _fr(a["statistics"]["baseline_k_hop_pooled"]["precision"])),
        "",
    ]
    return "\n".join(body[:1] + hdr + body[1:])


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    out = None
    if argv[:1] == ["--out-dir"]:
        out = Path(argv[1])
    card = generate(out)
    print(json.dumps({"label": LABEL, "aggregate": card["aggregate"]["coverage"],
                      "delay": card["aggregate"]["delay_accuracy"]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
