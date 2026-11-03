"""BACKTEST ENGINE REV 2 · PROFILE d: the scorecard protocol re-run on engine2 with profile d
(real sea-lane geometry, gate disciplines, graded route choice with real alternatives, rotation
recovery, tier wall) on top of the rev2c calibration.

Pre-registered in data/replay/rev2d/preregistration.md. EPISODES: the two replay episodes that are
NOT in the sealed TEST split of the VAL-01 benchmark (panama_2023_24 and red_sea_2024, both TRAIN);
data/replay/la_2021.json is a TEST episode of that benchmark and is NOT run by this module (a TEST
episode is run only by the VAL-01 runner). The comparison columns (v1, rev 2, rev 2 + services, rev 2 calibrated +
services) are re-run here, in memory, on the SAME two episodes, because the published cards pool
three. The protocol, grading rules and every metric definition are engine v1's
(server/engine/scorecard.py is imported, not copied). PRIMARY configuration: profile d + the
services routes table (same selection as data/replay/rev2_services/ and rev2_calibrated/).
SECONDARY configurations, all pre-registered, each run ONCE: profile d without chokepoint chains
(``no_chains``); profile d + chains with the conditional (witnessed) alternatives switched off
(``no_conditional_alternatives``, isolates the Panama -> Suez lane). The naive k-hop baseline runs
on the ORIGINAL core graph. Each replay episode is run ONCE per configuration; the result is
published whatever it says. Nothing under data/replay/ except the new directory is written.

CLI: python -m engine2.scorecard_rev2d [--out-dir D]
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
from .journal import get_profile_engine2, get_routed_engine2
from .plugins_d import D_HASH, PROFILE_LABEL
from .scorecard_rev2 import REV2_INSAMPLE_CAVEAT, REV2_MODEL_CAVEAT
from .scorecard_rev2_calibrated import CAL_CAVEAT, _headline_ref
from .scorecard_rev2_services import ERAS, ROUTES_CAVEAT, _BaselineFacade

REPO_ROOT = Path(__file__).resolve().parents[2]
LABEL = "BACKTEST ENGINE REV 2 · PROFILE d"

D_CAVEAT = (
    "Profile d (server/engine2/profile_d.py, registry in server/engine2/plugins_d.py) on top of "
    "the rev2c calibration: (1) every sea leg of the graph sails at lane distance / service speed "
    "(MARNET network via searoute-py; BIMCO / DVZ speeds) where the leg has real geometry, the rest "
    "keep the graph's transit days; (2) gates: every place is a FIFO server with a bounded anchorage "
    "that also refuses exogenous through-traffic above queue_cap; Panama has fixed slots with a "
    "booked / unbooked split (ACP advisory A-04-2024), Suez has convoy windows (Kuehne+Nagel); "
    "(3) route choice is graded (logistic in the time advantage, scale TEMPORARY) and uses the lane "
    "that avoids a gate (Suez and Bab el-Mandeb unconditionally, as in rev 2; any other gate only "
    "where a selected service witnesses it, i.e. Panama -> Suez for Asia -> US East Coast) and port "
    "substitution along service rotations; (4) rotation recovery: a late arrival is carried to the "
    "vessel's next calls less a TEMPORARY absorbed fraction; (5) SHELL places route and propagate but "
    "mint no event. The logistic scale, the absorbed fraction, the speed-class boundary, the feeder "
    "limit and the ground / air mu_max ratios remain TEMPORARY. The Panama -> Suez alternative is "
    "witnessed by two Suez-sailed Asia -> US East Coast rotations; that they serve the pairs the routes "
    "table puts on Panama is MODELLED.")


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


EPISODES = ("panama_2023_24", "red_sea_2024")       # VAL-01 TRAIN; la_2021 is TEST and is not run here

CONFIGS = (("primary", "profile d + services chains (headline)", ()),
           ("no_chains", "profile d, no chokepoint chains", ()),
           ("no_conditional_alternatives", "profile d + chains, witnessed alternatives off",
            ("alternatives_conditional",)))


def generate(out_dir: Path | None = None) -> dict:
    out_dir = Path(out_dir) if out_dir else REPO_ROOT / "data" / "replay" / "rev2d"
    graph = load_graph(REPO_ROOT / "data" / "graph" / "graph.json")
    replay_dir = REPO_ROOT / "data" / "replay"
    paths = [replay_dir / f"{name}.json" for name in EPISODES]
    entries = load_entries(paths)
    try:
        from graph_services.loader import load_services
    except Exception:  # pragma: no cover
        from server.graph_services.loader import load_services  # type: ignore
    svc = load_services(graph.raw, graph.hash)
    selection = {"file_sha256": svc.file_sha256, "eras": ERAS,
                 "include_unverified": False, "include_unstated": False}
    routed = get_profile_engine2(graph, selection)
    card = build_scorecard(_BaselineFacade(routed), entries)           # PRIMARY, run once
    plain = get_profile_engine2(graph, None)
    card_plain = build_scorecard(_BaselineFacade(plain), entries)       # SECONDARY, run once
    noalt = get_profile_engine2(graph, selection, "d", ablate=("alternatives_conditional",))
    card_noalt = build_scorecard(_BaselineFacade(noalt), entries)       # SECONDARY, run once

    # comparison columns: the other engine configurations re-run on the SAME two episodes (in memory, nothing written)
    from engine.engine import Engine
    cmp_cards = {
        "v1": build_scorecard(Engine(graph), entries),
        "rev2": build_scorecard(Engine2(graph), entries),
        "rev2_services": build_scorecard(_BaselineFacade(get_routed_engine2(graph, selection)), entries),
        "rev2_calibrated": build_scorecard(
            _BaselineFacade(get_routed_engine2(graph, selection, calibration=default_calibration())), entries)}
    refs = {}
    for key, rel in (("v1", "scorecard.json"), ("rev2", "rev2/scorecard_rev2.json"),
                     ("rev2_services", "rev2_services/scorecard.json"),
                     ("rev2_calibrated", "rev2_calibrated/scorecard.json")):
        p = replay_dir / rel
        refs[key] = {"file": f"data/replay/{rel}", "sha256": _sha(p),
                     "note": "published three-episode card, byte-untouched",
                     "aggregate_headline_published_3_episodes": _headline_ref(
                         json.loads(p.read_text(encoding="utf-8"))),
                     "aggregate_headline": _headline_ref(cmp_cards[key]),
                     "aggregate_headline_basis": "re-run in memory on the same two episodes as this card"}

    card["artifact"] = "supermap-scorecard-engine-rev2-profile-d"
    card["label"] = LABEL
    card["engine_rev"] = 2
    card["engine_label"] = PROFILE_LABEL
    card["profile"] = "rev2d"
    card["plugin_registry_hash"] = D_HASH
    card["profile_digest"] = routed.prof.digest
    card["calibration"] = routed.cal.describe()
    card["sea_routes"] = {**routed.prof.sea.digest(), "coverage": routed.prof.sea.coverage(),
                          "file": "data/graph/sea_routes.json"}
    table = routed.routes
    card["routes"] = {
        "source": "services", **selection, "table_sha256": table.digest,
        "legs_bound": len(table.entries), "legs_skipped": len(table.skipped),
        "bound_legs": [e["leg"] for e in table.entries],
        "alternatives": {e["leg"]: {k: e["alt"][k] for k in ("via", "gate", "added_days", "basis", "services")}
                         for e in table.entries if e["alt"]},
        "port_substitution_triples": table.audit["port_substitution_triples"],
        "gates": table.audit["gate_through_flow_vs_baseline"],
        "preregistration": "data/replay/rev2d/preregistration.md",
        "preregistration_sha256": _sha(out_dir / "preregistration.md"),
        "services_file": "data/graph/services.json",
        "rotation_services": routed.profile_services_n,
    }
    caveats = [c for c in HONESTY_CAVEATS if not c.startswith("Engine model (revision 2")]
    caveats = [c for c in caveats if not c.startswith("IN-SAMPLE")] + [
        REV2_MODEL_CAVEAT, ROUTES_CAVEAT, CAL_CAVEAT, D_CAVEAT, REV2_INSAMPLE_CAVEAT,
        ("IN-SAMPLE: the two episodes (panama_2023_24, red_sea_2024) are the in-sample set every "
         "engine was checked against, and both are TRAIN in VAL-01. la_2021 is a TEST episode of "
         "VAL-01 and was not run for this card. Pre-registered configuration: see preregistration.md; "
         "the sealed TEST split was not touched by this card.")]
    card["honesty_caveats"] = caveats
    for key, ref in refs.items():
        card[f"{key}_reference"] = ref
    card["variants"] = {
        "no_chains": {"label": "profile d without chokepoint chains (secondary, pre-registered)",
                      "aggregate_headline": _headline_ref(card_plain),
                      "replays": [{"id": r["id"], "stream_hash": r["stream_hash"],
                                   "event_count": r["event_count"]} for r in card_plain["replays"]]},
        "no_conditional_alternatives": {
            "label": "profile d + chains with witnessed alternatives off (secondary, pre-registered)",
            "aggregate_headline": _headline_ref(card_noalt),
            "replays": [{"id": r["id"], "stream_hash": r["stream_hash"],
                         "event_count": r["event_count"]} for r in card_noalt["replays"]]}}
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
    body[0] = "# " + LABEL + " - SupermAP Scorecard, chain engine rev 2 profile d + services routes"
    cols = [("Engine v1", card["v1_reference"]["aggregate_headline"]),
            ("ENGINE REV 2", card["rev2_reference"]["aggregate_headline"]),
            ("REV 2 + SERVICES", card["rev2_services_reference"]["aggregate_headline"]),
            ("REV 2 CALIBRATED + SERVICES", card["rev2_calibrated_reference"]["aggregate_headline"]),
            ("**PROFILE d + SERVICES (primary)**", headline_of(card)),
            ("profile d, no chains", card["variants"]["no_chains"]["aggregate_headline"]),
            ("profile d, witnessed alternatives off",
             card["variants"]["no_conditional_alternatives"]["aggregate_headline"])]
    rt = card["routes"]
    cov = card["sea_routes"]["coverage"]["total"]

    def row(name, f):
        return "| " + name + " | " + " | ".join(f(h) for _, h in cols) + " |"

    hdr = [
        "", "**" + LABEL + "** - engine under test: " + card["engine_label"]
        + " (profile rev2d, plug-in registry `" + card["plugin_registry_hash"][:12] + "`, profile digest `"
        + card["profile_digest"][:12] + "`; sea-lane geometry `"
        + card["sea_routes"]["content_sha256"][:12] + "`: " + str(cov["network"]) + " of " + str(cov["legs"])
        + " sea legs with real geometry, " + str(cov["fallback"]) + " on the graph's own transit) "
        "with the routes table from the graph services layer (`routes: 'services'`, eras "
        + ", ".join(rt["eras"]) + "; table `" + rt["table_sha256"][:12] + "`; " + str(rt["legs_bound"])
        + " graph legs bound; rotations: " + str(rt["rotation_services"]) + " services). "
        "Protocol, grading rules and metric definitions are engine v1's, unchanged; each replay "
        "episode (panama_2023_24, red_sea_2024: VAL-01 TRAIN; la_2021, a TEST episode, is not run) ran once "
        "per configuration; the result is published as it came out. Configuration "
        "pre-registered in `data/replay/rev2d/preregistration.md` (sha256 `"
        + rt["preregistration_sha256"][:12] + "`).",
        "",
        "Engine v1's, rev 2's, rev 2 + services' and rev 2 calibrated's published three-episode scorecards "
        "are byte-untouched (sha256 in the JSON); their columns here are those engines re-run in memory on "
        "the same two episodes, so every column grades the same steps.",
        "", "## Headline: engines on the same two episodes, same grading", "",
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
        "The naive baseline is budget-matched to each engine's own predicted node count, so its "
        "figures differ by column. The two replays are the engines' in-sample set.",
        "",
    ]
    return "\n".join(body[:1] + hdr + body[1:])


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    out = Path(argv[1]) if argv[:1] == ["--out-dir"] else None
    card = generate(out)
    print(json.dumps({"label": LABEL, "coverage": card["aggregate"]["coverage"],
                      "delay": card["aggregate"]["delay_accuracy"],
                      "variants": {k: v["aggregate_headline"] for k, v in card["variants"].items()}},
                     indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
