"""BACKTEST ENGINE REV 2 + SERVICES ROUTES: the scorecard protocol re-run
on engine2 with the routes table built from the graph services layer.

Pre-registered in data/replay/rev2_services/preregistration.md. The protocol,
grading rules and every metric definition are engine v1's
(server/engine/scorecard.py is imported, not copied). The naive k-hop baseline runs on the ORIGINAL core graph (the
engine under test is wrapped so that ``.graph`` is the unaugmented core
graph), so it is the same baseline as in the v1 and rev-2 scorecards. Each
replay episode is run ONCE; the result is published whatever it says.
Nothing under data/replay/ except the new directory is written.

CLI: python -m engine2.scorecard_rev2_services [--out-dir D]
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from engine.graph import load_graph
from engine.scorecard import (HONESTY_CAVEATS, build_scorecard, load_entries,
                              render_markdown)

from .journal import get_routed_engine2
from .plugins import ENGINE_LABEL, PLUGINS_HASH
from .scorecard_rev2 import REV2_INSAMPLE_CAVEAT, REV2_MODEL_CAVEAT

REPO_ROOT = Path(__file__).resolve().parents[2]
LABEL = "BACKTEST ENGINE REV 2 + SERVICES ROUTES"
ERAS = ["pre_red_sea_crisis"]       # pre-registered; see preregistration.md

ROUTES_CAVEAT = (
    "Routes table (opt-in, graph services layer): graph sea legs that the "
    "selected service rotations serve through chokepoints are replaced by "
    "chains through those chokepoints (same attributed flow), and Suez / Bab "
    "el-Mandeb failures can reroute along the Cape with added days = route "
    "length difference. Rotations are SOURCED announcements (carrier "
    "releases and trade press, 23 pre-crisis rotations on the default "
    "selection), not proof of sailed routes; chokepoint placement beyond a "
    "printed marker and the Cape alternative's days are MODELLED. Only graph "
    "legs that a rotation serves through a chokepoint are bound; origin-"
    "destination pairs that are not graph legs carry no volume and are not "
    "modelled.")


class _BaselineFacade:
    """The routed engine for ``run``; the ORIGINAL core graph for ``graph``
    (what the naive baseline reads)."""

    def __init__(self, routed):
        self._routed = routed
        self.graph = routed.base_core

    def __getattr__(self, name):
        return getattr(self._routed, name)

    def run(self, *a, **kw):
        return self._routed.run(*a, **kw)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fr(x):
    return "n/a" if x is None else f"{x * 100:.1f}%"


def generate(out_dir: Path | None = None) -> dict:
    out_dir = Path(out_dir) if out_dir else REPO_ROOT / "data" / "replay" / "rev2_services"
    graph = load_graph(REPO_ROOT / "data" / "graph" / "graph.json")
    replay_dir = REPO_ROOT / "data" / "replay"
    paths = sorted(p for p in replay_dir.glob("*.json") if p.name != "scorecard.json")
    try:
        from graph_services.loader import load_services
    except Exception:  # pragma: no cover
        from server.graph_services.loader import load_services  # type: ignore
    svc = load_services(graph.raw, graph.hash)
    selection = {"file_sha256": svc.file_sha256, "eras": ERAS,
                 "include_unverified": False, "include_unstated": False}
    routed = get_routed_engine2(graph, selection)
    card = build_scorecard(_BaselineFacade(routed), load_entries(paths))
    v1_json = replay_dir / "scorecard.json"
    r2_json = replay_dir / "rev2" / "scorecard_rev2.json"
    v1 = json.loads(v1_json.read_text(encoding="utf-8"))
    r2 = json.loads(r2_json.read_text(encoding="utf-8"))
    card["artifact"] = "supermap-scorecard-engine-rev2-services"
    card["label"] = LABEL
    card["engine_rev"] = 2
    card["engine_label"] = ENGINE_LABEL
    card["plugin_registry_hash"] = PLUGINS_HASH
    table = routed.routes
    card["routes"] = {
        "source": "services", **selection, "table_sha256": table.digest,
        "legs_bound": len(table.entries), "legs_skipped": len(table.skipped),
        "bound_legs": [e["leg"] for e in table.entries],
        "gates": table.audit["gate_through_flow_vs_baseline"],
        "preregistration": "data/replay/rev2_services/preregistration.md",
        "preregistration_sha256": _sha(out_dir / "preregistration.md"),
        "services_file": "data/graph/services.json",
    }
    caveats = [c for c in HONESTY_CAVEATS if not c.startswith("Engine model (revision 2")]
    caveats = [c for c in caveats if not c.startswith("IN-SAMPLE")] + [
        REV2_MODEL_CAVEAT, ROUTES_CAVEAT, REV2_INSAMPLE_CAVEAT,
        ("IN-SAMPLE (inherited): the three episodes are the same set every engine "
         "is checked against; the 25-event protocol v2 is still deferred and has "
         "NOT been run. Pre-registered configuration: see preregistration.md.")]
    card["honesty_caveats"] = caveats

    def headline(c):
        return {"coverage": c["aggregate"]["coverage"],
                "delay_accuracy": {k: c["aggregate"]["delay_accuracy"][k] for k in
                                   ("graded_steps", "hits", "misses", "hit_fraction",
                                    "mean_abs_error_d")},
                "false_positives_count": c["aggregate"]["false_positives"]["count"],
                "baseline_k_hop_pooled": c["aggregate"]["statistics"]["baseline_k_hop_pooled"],
                "engine_precision": c["aggregate"]["statistics"]["engine_precision"]}

    card["v1_reference"] = {"file": "data/replay/scorecard.json", "sha256": _sha(v1_json),
                            "note": "engine v1 scorecard, byte-untouched",
                            "aggregate_headline": headline(v1)}
    card["rev2_reference"] = {"file": "data/replay/rev2/scorecard_rev2.json",
                              "sha256": _sha(r2_json),
                              "note": "engine rev 2 scorecard, byte-untouched",
                              "aggregate_headline": headline(r2)}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "scorecard.json").write_text(
        json.dumps(card, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out_dir / "scorecard.md").write_text(render_md(card), encoding="utf-8")
    return card


def render_md(card: dict) -> str:
    body = render_markdown(card).split("\n")
    body[0] = "# " + LABEL + " - SupermAP Scorecard, chain engine rev 2 + services routes"
    v1 = card["v1_reference"]["aggregate_headline"]
    r2 = card["rev2_reference"]["aggregate_headline"]
    a = headline_of(card)
    rt = card["routes"]
    hdr = [
        "", "**" + LABEL + "** - engine under test: " + card["engine_label"]
        + " (plug-in registry `" + card["plugin_registry_hash"][:12] + "`) with the routes "
        "table from the graph services layer (`routes: 'services'`, eras "
        + ", ".join(rt["eras"]) + "; table `" + rt["table_sha256"][:12] + "`; services file `"
        + rt["file_sha256"][:12] + "`; " + str(rt["legs_bound"]) + " graph legs bound). "
        "Protocol, grading rules and metric definitions are engine v1's, unchanged; each "
        "replay episode ran once; the result is published as it came out. Configuration "
        "pre-registered in `data/replay/rev2_services/preregistration.md` (sha256 `"
        + rt["preregistration_sha256"][:12] + "`).",
        "",
        "Engine v1's `data/replay/scorecard.json` (sha256 `" + card["v1_reference"]["sha256"][:12]
        + "`) and rev 2's `data/replay/rev2/scorecard_rev2.json` (sha256 `"
        + card["rev2_reference"]["sha256"][:12] + "`) are byte-untouched.",
        "", "## Headline: three engines, same three episodes, same grading", "",
        "| Metric | Engine v1 | ENGINE REV 2 | REV 2 + SERVICES ROUTES |", "|---|---|---|---|",
        "| Node coverage | %s | %s | %s |" % (
            _cov(v1), _cov(r2), _cov(a)),
        "| Delay hits (window 0.5x-2x) | %d / %d | %d / %d | %d / %d |" % (
            v1["delay_accuracy"]["hits"], v1["delay_accuracy"]["graded_steps"],
            r2["delay_accuracy"]["hits"], r2["delay_accuracy"]["graded_steps"],
            a["delay_accuracy"]["hits"], a["delay_accuracy"]["graded_steps"]),
        "| Mean abs delay error (d) | %s | %s | %s |" % (
            v1["delay_accuracy"]["mean_abs_error_d"], r2["delay_accuracy"]["mean_abs_error_d"],
            a["delay_accuracy"]["mean_abs_error_d"]),
        "| False positives | %d | %d | %d |" % (
            v1["false_positives_count"], r2["false_positives_count"], a["false_positives_count"]),
        "| Engine precision | %s | %s | %s |" % (
            _fr(v1["engine_precision"]), _fr(r2["engine_precision"]), _fr(a["engine_precision"])),
        "| **Naive k-hop baseline coverage (budget-matched)** | %s | %s | %s |" % (
            _fr(v1["baseline_k_hop_pooled"]["coverage_fraction"]),
            _fr(r2["baseline_k_hop_pooled"]["coverage_fraction"]),
            _fr(a["baseline_k_hop_pooled"]["coverage_fraction"])),
        "| **Naive k-hop baseline precision** | %s | %s | %s |" % (
            _fr(v1["baseline_k_hop_pooled"]["precision"]),
            _fr(r2["baseline_k_hop_pooled"]["precision"]),
            _fr(a["baseline_k_hop_pooled"]["precision"])),
        "",
    ]
    return "\n".join(body[:1] + hdr + body[1:])


def headline_of(card: dict) -> dict:
    return {"coverage": card["aggregate"]["coverage"],
            "delay_accuracy": card["aggregate"]["delay_accuracy"],
            "false_positives_count": card["aggregate"]["false_positives"]["count"],
            "baseline_k_hop_pooled": card["aggregate"]["statistics"]["baseline_k_hop_pooled"],
            "engine_precision": card["aggregate"]["statistics"]["engine_precision"]}


def _cov(h: dict) -> str:
    c = h["coverage"]
    return "%d / %d (%s)" % (c["covered_count"], c["distinct_non_seed_nodes"], _fr(c["fraction"]))


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    out = Path(argv[1]) if argv[:1] == ["--out-dir"] else None
    card = generate(out)
    print(json.dumps({"label": LABEL, "coverage": card["aggregate"]["coverage"],
                      "delay": card["aggregate"]["delay_accuracy"]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
