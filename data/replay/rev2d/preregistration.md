# Pre-registration: BACKTEST ENGINE REV 2 · PROFILE d

Written BEFORE the scorecard in this directory was generated; `engine2.scorecard_rev2d` was never run
on a replay episode before it. Design requirement: the simulator is a causal cause-and-effect world model with
formulaic plug-ins only where real data is not available, each labelled; the redesign works for ALL places
(992: 200 CORE + 792 SHELL), as data-driven machinery with no place-specific code.

## What is run (frozen)

* Engine under test: ENGINE REV 2 · PROFILE d (opt-in: `POST /run {engine_rev: 2, profile: "d"}`). It is the
  rev2c calibration (PortWatch `mu_max` per place, sourced LA/LB cover, deficit-days delay definition) PLUS
  five mechanisms, each a registry entry with its identity-or-plug-in classification:
  1. real sea-lane geometry for every sea leg of the graph (`data/graph/sea_routes.json`, MARNET network via
     searoute-py; legs more than 3x their straight line, and legs off the network, keep the graph's own
     transit and are counted);
  2. gates: any place is a gate whose discipline is a data spec (`gate_specs`): default FIFO with a bounded
     anchorage that also refuses exogenous through-traffic; Panama = fixed slots with a booked / unbooked
     split (ACP advisory A-04-2024); Suez = convoy windows (Kuehne+Nagel);
  3. route choice: graded (logistic) diversion wherever a real alternative exists: the lane that avoids a gate
     (Suez / Bab el-Mandeb -> the Cape unconditionally, as in rev 2; any other gate only where a selected
     service witnesses it, i.e. Panama -> Suez for Asia -> US East Coast) and port substitution along service
     rotations;
  4. rotation recovery for every service of `services.json`;
  5. tier wall: SHELL places route, queue and propagate but mint no event.
* Frozen identifiers (recomputed from the code and data files; a change of any of them is a different configuration):

| Item | Value |
|---|---|
| plug-in registry hash (`D_HASH`) | `3dd94e96c02485aa894cf24a8e23ed19de707a0a4792030e9583ff9035dc2090` |
| profile digest, PRIMARY (profile d + services chains) | `4b0b0e79ab6a7d508ee6c9010aafefc9972432cdef8688bc894d8130073ee373` |
| profile digest, no chains | `4c3e60af6dbdfa7bb7ac2e9e7b3765627cbf840f6701ba1bdc727a4feb286c1e` |
| profile digest, witnessed alternatives off | `1518f57ef5ed7860b88f6d1f5b530ef755c2e5de14abd7710607cdeb46d0d8ab` |
| sea-lane sidecar content sha256 | `762a12555a7f564d1c871c958208290aaf65258661e1937601c7310a6b5623d8` |
| services sidecar file sha256 | `dfb6796f9c7df4a8ea04773a7b40b6f9654f5f37ad94f5e3e9f76017e1c572a4` |
| graph.json sha256 prefix | `9743a288fb5d670d` |
| VAL-01 config hash (`rev2d`) | `a00fd8ab78f880885375c3270d5def6b88450f5bd71fb1a8dd88e74ea4459dce` |
| code | this configuration (the `server/engine2` tree that the config hash fingerprints) |

* PRIMARY configuration (the headline): profile d + the routes table from the graph services sidecar with the
  SAME selection as `data/replay/rev2_services/` and `rev2_calibrated/` (eras `pre_red_sea_crisis`,
  `include_unverified: false`, `include_unstated: false`). SECONDARY configurations, same protocol, each run
  once: `no_chains` (profile d without chokepoint chains) and `no_conditional_alternatives` (profile d + chains
  with the witnessed alternatives switched off, which isolates the Panama -> Suez lane). All three are
  published whatever they say, including if worse than rev 2 calibrated, rev 2 + services, v1 or the naive
  baseline.
* Episodes: `panama_2023_24` and `red_sea_2024` only. **`la_2021` is NOT run.** The VAL-01 benchmark (`data/eval/val01/`)
  split rule puts `la_2021` in the sealed TEST set, which is run only once per config hash and not for this
  card. The other two replay episodes are TRAIN. The lockbox files were not opened; only `episodes.json` (the
  inputs and the split rule) was read.
* Protocol, grading rules and every metric: engine v1's (`engine.scorecard.build_scorecard` imported, not
  copied). The naive k-hop baseline runs on the ORIGINAL core graph. Comparison columns (v1, rev 2, rev 2 +
  services, rev 2 calibrated + services) are those engines re-run in memory on the same two episodes (the
  published three-episode cards are byte-untouched and referenced by sha256).
* Beside the scorecard: the VAL-01 TRAIN result of config `rev2d` (free to repeat), the all-places sweep (every
  CORE place closed in turn, CRIT standard disruption; invariants asserted; distributions by place kind;
  outliers) and the leave-one-out ablation aggregated over all CORE places. None of them is tuned against.

## Nothing is tuned after a number is seen

Every parameter is a registry entry; the TEMPORARY ones (stated here so they cannot be read as results):
logistic scale 2 d; absorbed fraction 0.3 per call; speed-class boundary 3,000 nm; land-separation ratio 3;
port-substitution feeder limit 400 nm; ground and air `mu_max` ratio 1.3; class-median `mu_max` for the
sea places without a PortWatch series; Panama booked share 0.9459 (upper reading of an ambiguous table,
disclosed). If the scorecard is worse than rev 2 calibrated, it is published as worse.

## Disclosure of what was looked at before this file was written

* The rev 2 calibrated diagnosis and the published rev 2 / rev 2 + services / rev 2 calibrated cards were read, including
  the Panama replay's observed New York figure (8.5 d, +7 to 10 d Suez substitute). It was known. The Panama
  -> Suez alternative was added because the services layer holds two selected pre-crisis Asia -> US East
  Coast rotations that sail through Suez (a sourced existence at trade level), not because of that figure;
  its zone-level application to the Shanghai / Ningbo / Busan -> New York pairs is MODELLED and says so.
* Before this file was written profile d was run on: hand-built synthetic graphs (mechanism checks); the flagship
  scenarios Suez 14 d, Bab el-Mandeb 14 d, Panama 30 d, Los Angeles 30 d and Rotterdam 30 d closures (place
  level standard disruptions, NOT the LA 2021 episode); the CRIT standard closure of all 200 CORE places
  (checked for termination of the substitution lanes at Virginia and for non-negative unbooked waits at the
  first step of a slot cut); the invariants checks (conservation, DAG, determinism, stream hash; no score), which ran profile d on
  `panama_2023_24`, `red_sea_2024` and `la_2021`. No replay output
  was inspected.
* The engine mechanisms and the registry were fixed by the design requirement above, before
  any replay number existed. No parameter was changed after the sweep.
* Expected, stated in advance so it can be checked: Cape detour days fall from 14 to 17 d to about 9 d
  (lane distance / 15.6 kn; Port of Rotterdam says 8 to 12 d); Panama's queue stays near its anchorage and
  the rest waits upstream, not in a 2,000 vessel-day queue; the New York delay falls from 45.9 d towards the
  4 to 10 d range if the diverted Asia -> US East Coast flow reaches New York through Suez, and a hit
  (predicted within 0.5x to 2x of 8.5 d) is possible but NOT claimed; coverage may move either way because
  SHELL places now carry flow and substitution lanes spread congestion; false positives may rise.
* Both episodes remain IN-SAMPLE for the engine (its in-sample set).
