"""BACKTEST ENGINE REV 2 CALIBRATED: the scorecard protocol re-run on engine2
with the calibration profile ``rev2c`` (data-derived mu_max per place, sourced
LA/LB cover, deficit-days delay definition).

Pre-registered in data/replay/rev2_calibrated/preregistration.md. The protocol, grading rules and every metric
definition are engine v1's (server/engine/scorecard.py is imported, not copied).
PRIMARY configuration: rev2c + the services routes table (same selection as
data/replay/rev2_services/). SECONDARY: rev2c without routes. The naive k-hop
baseline runs on the ORIGINAL core graph. Each replay episode is run ONCE per
configuration; the result is published whatever it says. Nothing under
data/replay/ except the new directory is written.

CLI: python -m engine2.scorecard_rev2_calibrated [--out-dir D]
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from engine.graph import load_graph
from engine.scorecard import (HONESTY_CAVEATS, build_scorecard, load_entries,
                              render_markdown)

from .calibration import default_calibration
from .engine import Engine2
from .journal import get_routed_engine2
from .plugins_cal import CAL_HASH, PROFILE_LABEL
from .scorecard_rev2 import REV2_INSAMPLE_CAVEAT, REV2_MODEL_CAVEAT
from .scorecard_rev2_services import ERAS, ROUTES_CAVEAT, _BaselineFacade

REPO_ROOT = Path(__file__).resolve().parents[2]
LABEL = "BACKTEST ENGINE REV 2 CALIBRATED"

CAL_CAVEAT = (
    "Calibration profile rev2c (server/engine2/calibration.py, registry "
    "in server/engine2/plugins_cal.py): (1) peak service rate "
    "mu_max per place = P99 / median of the 7-day mean of IMF PortWatch daily "
    "counts over 2022-07-01..2023-10-29, replacing the 1.3 x baseline guess for "
    "every sea place with a series (ground and air hubs keep 1.3, TEMPORARY); a "
    "daily call or transit count is a demonstrated rate, not a design capacity; "
    "(2) the Los Angeles / Long Beach terminal cover is 4.0 d (Port of Los Angeles "
    "dwell-time release), every other place keeps the graph's placeholder cover "
    "(TEMPORARY); (3) an inventory event's delay_d is the peak cumulative "
    "arrival deficit in days (it was the length of the shortfall). The Cape "
    "detour days, layer equivalence, mode-shift fractions, no-restock, route "
    "foresight and the cost reference remain TEMPORARY. There is no Panama "
    "alternative route in the model: a long Panama cut keeps queueing.")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fr(x):
    return "n/a" if x is None else f"{x * 100:.1f}%"


def headline_of(card: dict) -> dict:
    return {"coverage": card["aggregate"]["coverage"],
            "delay_accuracy": card["aggregate"]["delay_accuracy"],
            "false_positives_count": card["aggregate"]["false_positives"]["count"],
            "baseline_k_hop_pooled": card["aggregate"]["statistics"]["baseline_k_hop_pooled"],
            "engine_precision": card["aggregate"]["statistics"]["engine_precision"]}


def _headline_ref(c: dict) -> dict:
    return {"coverage": c["aggregate"]["coverage"],
            "delay_accuracy": {k: c["aggregate"]["delay_accuracy"][k] for k in
                               ("graded_steps", "hits", "misses", "hit_fraction",
                                "mean_abs_error_d")},
            "false_positives_count": c["aggregate"]["false_positives"]["count"],
            "baseline_k_hop_pooled": c["aggregate"]["statistics"]["baseline_k_hop_pooled"],
            "engine_precision": c["aggregate"]["statistics"]["engine_precision"]}


def generate(out_dir: Path | None = None) -> dict:
    out_dir = Path(out_dir) if out_dir else REPO_ROOT / "data" / "replay" / "rev2_calibrated"
    graph = load_graph(REPO_ROOT / "data" / "graph" / "graph.json")
    replay_dir = REPO_ROOT / "data" / "replay"
    paths = sorted(p for p in replay_dir.glob("*.json") if p.name != "scorecard.json")
    entries = load_entries(paths)
    try:
        from graph_services.loader import load_services
    except Exception:  # pragma: no cover
        from server.graph_services.loader import load_services  # type: ignore
    svc = load_services(graph.raw, graph.hash)
    selection = {"file_sha256": svc.file_sha256, "eras": ERAS,
                 "include_unverified": False, "include_unstated": False}
    cal = default_calibration()
    routed = get_routed_engine2(graph, selection, calibration=cal)
    card = build_scorecard(_BaselineFacade(routed), entries)           # PRIMARY, run once
    plain = Engine2(graph, calibration=cal)
    card_plain = build_scorecard(plain, entries)                         # SECONDARY, run once

    v1_json = replay_dir / "scorecard.json"
    r2_json = replay_dir / "rev2" / "scorecard_rev2.json"
    sv_json = replay_dir / "rev2_services" / "scorecard.json"
    v1 = json.loads(v1_json.read_text(encoding="utf-8"))
    r2 = json.loads(r2_json.read_text(encoding="utf-8"))
    sv = json.loads(sv_json.read_text(encoding="utf-8"))

    card["artifact"] = "supermap-scorecard-engine-rev2-calibrated"
    card["label"] = LABEL
    card["engine_rev"] = 2
    card["engine_label"] = PROFILE_LABEL
    card["profile"] = "rev2c"
    card["plugin_registry_hash"] = CAL_HASH
    card["calibration"] = cal.describe()
    table = routed.routes
    card["routes"] = {
        "source": "services", **selection, "table_sha256": table.digest,
        "legs_bound": len(table.entries), "legs_skipped": len(table.skipped),
        "bound_legs": [e["leg"] for e in table.entries],
        "gates": table.audit["gate_through_flow_vs_baseline"],
        "preregistration": "data/replay/rev2_calibrated/preregistration.md",
        "preregistration_sha256": _sha(out_dir / "preregistration.md"),
        "services_file": "data/graph/services.json",
    }
    caveats = [c for c in HONESTY_CAVEATS if not c.startswith("Engine model (revision 2")]
    caveats = [c for c in caveats if not c.startswith("IN-SAMPLE")] + [
        REV2_MODEL_CAVEAT, ROUTES_CAVEAT, CAL_CAVEAT, REV2_INSAMPLE_CAVEAT,
        ("IN-SAMPLE (inherited): the three episodes are the same set every engine "
         "is checked against; the 25-event protocol v2 is still deferred and has "
         "NOT been run. Pre-registered configuration: see preregistration.md.")]
    card["honesty_caveats"] = caveats
    card["v1_reference"] = {"file": "data/replay/scorecard.json", "sha256": _sha(v1_json),
                            "note": "engine v1 scorecard, byte-untouched",
                            "aggregate_headline": _headline_ref(v1)}
    card["rev2_reference"] = {"file": "data/replay/rev2/scorecard_rev2.json",
                              "sha256": _sha(r2_json),
                              "note": "engine rev 2 scorecard, byte-untouched",
                              "aggregate_headline": _headline_ref(r2)}
    card["rev2_services_reference"] = {
        "file": "data/replay/rev2_services/scorecard.json", "sha256": _sha(sv_json),
        "note": "engine rev 2 + services routes scorecard, byte-untouched",
        "aggregate_headline": _headline_ref(sv)}
    card["variant_rev2c_no_routes"] = {
        "label": "BACKTEST ENGINE REV 2 CALIBRATED, no routes table (secondary, pre-registered)",
        "aggregate_headline": _headline_ref(card_plain),
        "replays": [{"id": r["id"], "stream_hash": r["stream_hash"],
                     "event_count": r["event_count"]} for r in card_plain["replays"]]}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "scorecard.json").write_text(
        json.dumps(card, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out_dir / "scorecard.md").write_text(render_md(card), encoding="utf-8")
    return card


def _cov(h: dict) -> str:
    c = h["coverage"]
    return "%d / %d (%s)" % (c["covered_count"], c["distinct_non_seed_nodes"], _fr(c["fraction"]))


def _hits(h: dict) -> str:
    return "%d / %d" % (h["delay_accuracy"]["hits"], h["delay_accuracy"]["graded_steps"])


def render_md(card: dict) -> str:
    body = render_markdown(card).split("\n")
    body[0] = "# " + LABEL + " - SupermAP Scorecard, chain engine rev 2 calibrated + services routes"
    cols = [("Engine v1", card["v1_reference"]["aggregate_headline"]),
            ("ENGINE REV 2", card["rev2_reference"]["aggregate_headline"]),
            ("REV 2 + SERVICES", card["rev2_services_reference"]["aggregate_headline"]),
            ("**REV 2 CALIBRATED + SERVICES (primary)**", headline_of(card)),
            ("REV 2 CALIBRATED, no routes", card["variant_rev2c_no_routes"]["aggregate_headline"])]
    rt = card["routes"]

    def row(name, f):
        return "| " + name + " | " + " | ".join(f(h) for _, h in cols) + " |"

    hdr = [
        "", "**" + LABEL + "** - engine under test: " + card["engine_label"]
        + " (profile rev2c, plug-in registry `" + card["plugin_registry_hash"][:12] + "`; "
        "PortWatch window payload `" + str(card["calibration"]["source_payload_sha256"])[:12] + "`) "
        "with the routes table from the graph services layer (`routes: 'services'`, eras "
        + ", ".join(rt["eras"]) + "; table `" + rt["table_sha256"][:12] + "`; services file `"
        + rt["file_sha256"][:12] + "`; " + str(rt["legs_bound"]) + " graph legs bound). "
        "Protocol, grading rules and metric definitions are engine v1's, unchanged; each "
        "replay episode ran once per configuration; the result is published as it came out. "
        "Configuration pre-registered in `data/replay/rev2_calibrated/preregistration.md` "
        "(sha256 `" + rt["preregistration_sha256"][:12] + "`).",
        "",
        "Engine v1's `data/replay/scorecard.json` (sha256 `" + card["v1_reference"]["sha256"][:12]
        + "`), rev 2's `data/replay/rev2/scorecard_rev2.json` (`"
        + card["rev2_reference"]["sha256"][:12] + "`) and rev 2 + services' "
        "`data/replay/rev2_services/scorecard.json` (`"
        + card["rev2_services_reference"]["sha256"][:12] + "`) are byte-untouched.",
        "", "## Headline: engines on the same three episodes, same grading", "",
        "| Metric | " + " | ".join(n for n, _ in cols) + " |",
        "|---|" + "---|" * len(cols),
        row("Node coverage", lambda h: _cov(h)),
        row("Delay hits (window 0.5x-2x)", lambda h: _hits(h)),
        row("Mean abs delay error (d)", lambda h: str(h["delay_accuracy"]["mean_abs_error_d"])),
        row("False positives", lambda h: str(h["false_positives_count"])),
        row("Engine precision", lambda h: _fr(h["engine_precision"])),
        row("**Naive k-hop baseline coverage (budget-matched)**",
            lambda h: _fr(h["baseline_k_hop_pooled"]["coverage_fraction"])),
        row("**Naive k-hop baseline precision**",
            lambda h: _fr(h["baseline_k_hop_pooled"]["precision"])),
        "",
        "The naive baseline is budget-matched to each engine's own predicted node count, so "
        "its figures differ by column. The three replays are the engines' in-sample set.",
        "",
    ]
    return "\n".join(body[:1] + hdr + body[1:])


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    out = Path(argv[1]) if argv[:1] == ["--out-dir"] else None
    card = generate(out)
    print(json.dumps({"label": LABEL, "coverage": card["aggregate"]["coverage"],
                      "delay": card["aggregate"]["delay_accuracy"],
                      "no_routes": card["variant_rev2c_no_routes"]["aggregate_headline"]},
                     indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
