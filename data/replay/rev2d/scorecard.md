# BACKTEST ENGINE REV 2 · PROFILE d - SupermAP Scorecard, chain engine rev 2 profile d + services routes

**BACKTEST ENGINE REV 2 · PROFILE d** - engine under test: ENGINE REV 2 · PROFILE d (profile rev2d, plug-in registry `3dd94e96c024`, profile digest `4b0b0e79ab6a`; sea-lane geometry `762a12555a7f`: 3084 of 3642 sea legs with real geometry, 558 on the graph's own transit) with the routes table from the graph services layer (`routes: 'services'`, eras pre_red_sea_crisis; table `951f3dd3b33c`; 14 graph legs bound; rotations: 129 services). Protocol, grading rules and metric definitions are engine v1's, unchanged; each replay episode (panama_2023_24, red_sea_2024: VAL-01 TRAIN; la_2021, a TEST episode, is not run) ran once per configuration; the result is published as it came out. Configuration pre-registered in `data/replay/rev2d/preregistration.md` (sha256 `154988617a0d`).

Engine v1's, rev 2's, rev 2 + services' and rev 2 calibrated's published three-episode scorecards are byte-untouched (sha256 in the JSON); their columns here are those engines re-run in memory on the same two episodes, so every column grades the same steps.

## Headline: engines on the same two episodes, same grading

| Metric | Engine v1 | ENGINE REV 2 | REV 2 + SERVICES | REV 2 CALIBRATED + SERVICES | **PROFILE d + SERVICES (primary)** | profile d, no chains | profile d, witnessed alternatives off |
|---|---|---|---|---|---|---|---|
| Node coverage | 4 / 7 (57.1%) | 1 / 7 (14.3%) | 3 / 7 (42.9%) | 3 / 7 (42.9%) | 4 / 7 (57.1%) | 1 / 7 (14.3%) | 3 / 7 (42.9%) |
| Delay hits (window 0.5x-2x) | 2 / 2 | 1 / 1 | 1 / 2 | 1 / 2 | 2 / 3 | 1 / 1 | 1 / 2 |
| Mean abs delay error (d) | 1.25 | 5.0 | 86.382493 | 22.662731 | 3.876763 | 4.510463 | 5.374625 |
| False positives | 193 | 2 | 3 | 3 | 3 | 2 | 3 |
| Engine precision | 2.0% | 33.3% | 50.0% | 50.0% | 57.1% | 33.3% | 50.0% |
| **Naive k-hop baseline coverage (budget-matched)** | 85.7% | 42.9% | 42.9% | 42.9% | 57.1% | 42.9% | 57.1% |
| **Naive k-hop baseline precision** | 3.0% | 30.0% | 30.0% | 30.0% | 25.0% | 30.0% | 25.0% |

The naive baseline is budget-matched to each engine's own predicted node count, so its figures differ by column. The two replays are the engines' in-sample set.


Every number below carries its denominator definition. The full methodology is in the Methodology section and is embedded in scorecard.json alongside these numbers.

## Headline

| Replay | Observed steps | Input steps (seed, excluded) | Ungradeable steps (no evidence) | Node coverage (covered / distinct non-seed nodes) | Delay grading (hits / graded) | Mean \|observed − predicted\| (graded steps) | False positives (predicted, never observed) | Genuine misses |
|---|---|---|---|---|---|---|---|---|
| panama_2023_24 | 12 | 9 | 0 | 1 / 3 (33.3%) | 1 / 1 | 0.881039 d | 1 | 1 |
| red_sea_2024 | 11 | 3 | 0 | 3 / 4 (75.0%) | 1 / 2 | 5.374626 d | 2 | 1 |
| **Aggregate (pooled)** | 23 | 12 | 0 | 4 / 7 (57.1%) | 2 / 3 | 3.876763 d | 3 | 2 |

Denominators: node coverage is over distinct non-seed nodes with evidence-backed observed effects; delay grading is over non-seed steps with observed_delay_d > 0 at predicted nodes only, one graded step per node (earliest observed step; later quantified steps at the same node are duplicates, listed and in no denominator); the mean error is over those graded steps only. The predicted delay is the node's FIRST-HIT delay (first event in stream order, delay-0 cost leaves excluded); a node whose only events are delay-0 cost leaves counts as NOT predicted. Input steps, unquantified steps, ungradeable steps (no evidence source — never a hit, never a miss), duplicate steps, and quantified steps at unpredicted nodes are excluded from both rates and are listed per replay below.

## Statistical honesty

- Coverage 4/7 (57.1%), exact Clopper-Pearson 95% CI: [18.4%, 90.1%].
- Delay hit rate 2/3 (66.7%), exact Clopper-Pearson 95% CI: [9.4%, 99.2%].
- Small sample, stated plainly: 23 observed steps across 2 replays yield 3 graded steps over 7 distinct non-seed nodes. One graded step is worth 33.333333 points of hit rate; one node is worth 14.285714 points of coverage. Treat every rate here as a small-sample estimate, not a measurement.

### Naive baseline — directed k-hop, same events, same node budget

Baseline method: directed BFS from the seed over the graph's directed edges, whole hop levels, until the non-seed node count first reaches the engine's predicted non-seed count for the same replay. Precision = covered / predicted.

| Replay | Engine coverage | Baseline coverage (k hops, budget) | Engine precision | Baseline precision |
|---|---|---|---|---|
| panama_2023_24 | 1 / 3 (33.3%) | 1 / 3 (33.3%) (k=1, budget 2, predicted 6) | 50.0% | 16.7% |
| red_sea_2024 | 3 / 4 (75.0%) | 3 / 4 (75.0%) (k=3, budget 5, predicted 10) | 60.0% | 30.0% |
| **Pooled** | 4 / 7 (57.1%) | 4 / 7 (57.1%) | 57.1% | 25.0% |

- On coverage, the engine (57.1%) ties the naive baseline (57.1%).
- On precision, the engine (57.1%) beats the naive baseline (25.0%).

## Comparison with legacy grading (labelled, not comparable)

| Grading | Engine | Coverage | Delay hits | Mean |error| (d) | False positives |
|---|---|---|---|---|---|
| Headline (first-hit, node dedupe, cost leaves excluded) | current | 4 / 7 | 2 / 3 | 3.876763 | 3 |
| Legacy rules on the SAME runs (max-over-events, no dedupe, cost leaves counted) | current | 6 / 7 | 3 / 5 | 5.172539 | 29 |
| As published for the original engine (original grading: max-over-events estimator, no node dedupe, delay-0 cost leaves counted as predictions) | original | 9 / 10 | 3 / 5 | 7.68 | 52 |

The three rows answer three different questions (different engine and/or different grading rules); no row may be quoted as a like-for-like improvement or regression against another.

## Replay: panama_2023_24 — Panama Canal drought, 2023-24

- Seed (disruption) node: `chk_panama` — 9 input step(s) at this node excluded from grading.
- Duration used: 215.0 d (dataset expected_duration_d mapped to engine duration_d).
- Capacity loss used: 0.5 (basis kind: derived). Basis: Derived by arithmetic only on the two sourced ACP slot counts: normal schedule ~36 transits/day, crisis minimum 18/day from 1 Feb 2024; capacity_loss = 1 - 18/36 = 0.5. No conversion factor involved.
- Capacity loss source: Panama Canal Authority (ACP) Customer Advisory 30 Oct 2023, via DB Group: https://www.dbgroup.net/en/news/panama-canal-daily-transit-limited-due-drought
- Dataset notes: capacity_loss 0.5 = ACP slot schedule 36/day normal to 18/day minimum (Feb 2024). expected_duration_d 215 = 2023-10-30 (ACP advisory) to 2024-06-01 (slots restored to 32/day, ~89% of normal). Queue/draft figures rest partly on the Ballast Markets secondary compilation of ACP/press reporting - re-verification against ACP advisories and IMF PortWatch is an open item.
- Disclosure — duration_d: mapped (value 215.0). replay specs carry the field expected_duration_d; the engine reads duration_d — mapped explicitly so the engine's silent 7.0 d default cannot fire
- Disclosure — shape: default_substituted (value step). dataset states no shape; engine default passed explicitly
- Run: run_id `scorecard_panama_2023_24`, stream hash `cb8a65da8de4e5184ecfd495ed65746507c39fcb725e6c7e3dc181b1b458bc2d`, 14 events; dataset file `panama_2023_24.json`, dataset SHA-256 `9798efd698c2ed1afd40b5fe89b480f28ef1cda3432526f5a58e2cd482666a6a`.

### Node coverage — 1 / 3 distinct non-seed nodes (33.3%)

- Covered nodes: `chk_suez`
- Uncovered nodes: `sea_losangeles`, `sea_newyork`

### Delay accuracy — 1 hit / 0 miss across 1 graded step(s); mean |observed − predicted| = 0.881039 d

Predicted delay per node = first-hit delay_d (first event in stream order, delay-0 cost leaves excluded). HIT = observed within [0.5×, 2×] of predicted. One graded step per node: the earliest observed step grades.

| Node | Observed (d) | Predicted — first-hit (d) | Window | Result | \|Error\| (d) |
|---|---|---|---|---|---|
| `chk_suez` | 8.5 | 7.618961 | [3.80948, 15.237922] | hit | 0.881039 |

Quantified steps at unpredicted nodes (genuine misses, excluded from the mean above):

- `sea_newyork`: observed 8.5 d, no prediction. Asia to US East Coast container services rerouted via Suez (+7-10 days) during the peak-crisis window. observed_delay_d 8.5 is the midpoint of the observed +7-10 d range.

### Input steps (seed node, excluded from grading) — 9

- `chk_panama`: observed 0.0 d (2023-10-25) — ~135 ships waiting to transit (~50% above the normal 80-85).
- `chk_panama`: observed 0.0 d (2023-10-30) — ACP advisory sets the progressive slot-reduction schedule: 24/day (7-30 Nov 2023), 22/day (Dec 2023), 20/day (Jan 2024), 18/day (from 1 Feb 2024), vs a normal ~36/day.
- `chk_panama`: observed 0.0 d (2023-11-01) — Slot-auction premiums average ~$2.5M per slot (range $1.5-4M) for 2-4 auctioned slots/day (Nov-Feb peak window; cost channel).
- `chk_panama`: observed 0.0 d (2023-11-30) — Neopanamax maximum draft limited to 44 ft (from a normal ~50 ft), forcing light-loading of the largest container ships and LNG carriers.
- `chk_panama`: observed 0.0 d (2024-01-01) — Gatun Lake at 79.5 ft - lowest January level on record, ~6 ft below normal.
- `chk_panama`: observed 0.0 d (2024-02-01) — Daily transit slots reach the crisis minimum: 18/day (a ~50% capacity cut).
- `chk_panama`: observed 17.5 d (2024-02-29) — Queues of 135-150 ships; average waits 15-20 days, some vessels 25+ days (Nov 2023-Feb 2024 peak window). observed_delay_d 17.5 is the midpoint of the observed 15-20 d wait range.
- `chk_panama`: observed 0.0 d (2024-03-01) — Recovery begins: slots raised to 22/day (Mar), then 24 (1 Apr), 27 (1 May).
- `chk_panama`: observed 6.0 d (2024-06-01) — Slots restored to 32/day (~89% of normal); Gatun Lake back to ~85.2 ft by 15 Jun; queues near normal (~88 ships in May, waits 5-7 days). observed_delay_d 6.0 is the midpoint of the observed 5-7 d wait range.

### Unquantified steps (observed_delay_d = 0; in coverage, not delay-graded) — 1

- `sea_losangeles`: not predicted — US West Coast + rail land bridge used as a substitute for Asia-US East Coast flows during the peak-crisis window (substitution observed; no added-delay figure stated by the source). Some bulk carriers rerouted via Cape Horn (+18-21 days) - no graph node represents that leg.

### Ungradeable steps (no evidence source; never a hit, never a miss) — 0

- none

### Duplicate steps (node already graded; in no denominator) — 0

- none

### Cost-only predicted nodes (only delay-0 cost leaves; count as NOT predicted) — 9

`sea_antwerp`, `sea_balboa`, `sea_cartagena`, `sea_houston`, `sea_newyork`, `sea_portsaid`, `sea_rotterdam`, `sea_savannah`, `sea_shanghai`

### False positives — 1 predicted node(s) with no observed effect

`sea_colon`

## Replay: red_sea_2024 — Red Sea / Bab el-Mandeb crisis, 2023-2025

- Seed (disruption) node: `chk_babelmandeb` — 3 input step(s) at this node excluded from grading.
- Duration used: 559.0 d (dataset expected_duration_d mapped to engine duration_d).
- Capacity loss used: 0.7 (basis kind: sourced). Basis: The loss figure is stated by the source: gross tonnage of ships entering the Gulf of Aden fell >70% between the 1st half of Dec 2023 and the 1st half of Feb 2024; taken as 0.7 (the stated floor). Corroborated in the same timeline by Suez transits -70% by Jun 2024 (UNCTAD Review of Maritime Transport 2024). No arithmetic conversion applied. Container-ship capacity fell further (-92% by Jun 2024); 0.7 is the conservative all-shipping figure.
- Capacity loss source: UNCTAD, via Africa Ports feature (Apr 2024): https://africaports.co.za/2024/04/24/feature-red-sea-crisis-and-implications-for-trade-facilitation-in-africa/
- Dataset notes: capacity_loss 0.7 = observed gross-tonnage fall through the Gulf of Aden (>70% by mid-Feb 2024, UNCTAD); container-ship capacity fell further (-92% by Jun 2024). expected_duration_d 559 = days from 2023-11-19 to the last timeline observation (2025-05-31, Suez tonnage still ~70% below 2023) - an observed lower bound, not a forecast; the disruption had not resolved in the source timeline.
- Disclosure — duration_d: mapped (value 559.0). replay specs carry the field expected_duration_d; the engine reads duration_d — mapped explicitly so the engine's silent 7.0 d default cannot fire
- Disclosure — shape: default_substituted (value step). dataset states no shape; engine default passed explicitly
- Run: run_id `scorecard_red_sea_2024`, stream hash `a315aa62d6d4f7b8ea8e546d833009dd5f8d76b760446e0a81c48760fead5037`, 30 events; dataset file `red_sea_2024.json`, dataset SHA-256 `79e86cb5945d4b9b9871c6064a12799fe2a9a8ba998f533e8f459328b50d2def`.

### Node coverage — 3 / 4 distinct non-seed nodes (75.0%)

- Covered nodes: `chk_capeofgoodhope`, `chk_suez`, `sea_rotterdam`
- Uncovered nodes: `sea_shanghai`

### Delay accuracy — 1 hit / 1 miss across 2 graded step(s); mean |observed − predicted| = 5.374626 d

Predicted delay per node = first-hit delay_d (first event in stream order, delay-0 cost leaves excluded). HIT = observed within [0.5×, 2×] of predicted. One graded step per node: the earliest observed step grades.

| Node | Observed (d) | Predicted — first-hit (d) | Window | Result | \|Error\| (d) |
|---|---|---|---|---|---|
| `sea_rotterdam` | 10.0 | 4.983154 | [2.491577, 9.966308] | miss | 5.016846 |
| `chk_capeofgoodhope` | 10.0 | 15.732405 | [7.866202, 31.46481] | hit | 5.732405 |

### Input steps (seed node, excluded from grading) — 3

- `chk_babelmandeb`: observed 0.0 d (2023-11-19) — Incident involving the car carrier Galaxy Leader in the Red Sea - first of a series of incidents that led carriers to avoid the corridor (crew held until 22 Jan 2025).
- `chk_babelmandeb`: observed 0.0 d (2024-02-15) — Gross tonnage of ships entering the Gulf of Aden fell >70% (1st half Dec 2023 to 1st half Feb 2024 window).
- `chk_babelmandeb`: observed 0.0 d (2024-06-30) — Container-ship capacity entering the Gulf of Aden -92% (by Jun 2024). Rerouting raised overall vessel demand by ~3% and container-ship demand by ~12% (UNCTAD via Splash247).

### Unquantified steps (observed_delay_d = 0; in coverage, not delay-graded) — 5

- `chk_suez`: predicted delay (first-hit) 248.430396 d — Onset of sustained carrier diversions away from the Red Sea corridor (UNCTAD reference timeline).
- `chk_suez`: predicted delay (first-hit) 248.430396 d — By end of December 2023, traffic through the Suez Canal was down by about half vs pre-crisis levels.
- `chk_suez`: predicted delay (first-hit) 248.430396 d — Suez transits -70% vs mid-Dec 2023 (by Jun 2024). Canal revenue fell $9.4B (FY2022/23) to $7.2B (FY2023/24); transits 25,911 to 20,148.
- `sea_shanghai`: not predicted — Shanghai Containerized Freight Index (SCFI) averaged 2,496 points in 2024, +149% year on year (rate channel; node is the index's home port).
- `chk_suez`: predicted delay (first-hit) 248.430396 d — Suez tonnage still approximately 70% below its 2023 level - no full recovery 17 months in.

### Ungradeable steps (no evidence source; never a hit, never a miss) — 0

- none

### Duplicate steps (node already graded; in no denominator) — 1

- `chk_capeofgoodhope`: observed 10.0 d — duplicate of the earliest graded step at this node (one graded step per node per replay)

### Cost-only predicted nodes (only delay-0 cost leaves; count as NOT predicted) — 19

`gnd_duisburg`, `sea_antwerp`, `sea_beirut`, `sea_buenosaires`, `sea_busan`, `sea_capetown`, `sea_damietta`, `sea_dublin`, `sea_felixstowe`, `sea_hamburg`, `sea_marsaxlokk`, `sea_montreal`, `sea_newyork`, `sea_ningbo`, `sea_piraeus`, `sea_qingdao`, `sea_santos`, `sea_shanghai`, `sea_singapore`

### False positives — 2 predicted node(s) with no observed effect

`sea_jeddah`, `sea_portsaid`

## Genuine misses — all replays (published, not hidden)

| Replay | Node | Observed (d) | Predicted — first-hit (d) | Why it is a miss |
|---|---|---|---|---|
| panama_2023_24 | `sea_newyork` | 8.5 | — | node not predicted |
| red_sea_2024 | `sea_rotterdam` | 10.0 | 4.983154 | outside window [2.491577, 9.966308] |

## Ungradeable steps — all replays (no evidence source; listed, not dropped)

None.

## Methodology

1. **Evidence rule:** Evidence rule, enforced in this grading path: an observed step with no evidence source (no evidence_source / evidence_url / evidence provenance) is UNGRADEABLE — never a hit, never a miss, and never silently dropped. Ungradeable steps are counted and listed as their own line per replay and in the aggregate, and are excluded from node coverage, delay grading, and the false-positive comparison (an evidence-less observation is not an observed effect), so every denominator covers evidence-backed steps only.

2. **Seed exclusion:** Observed steps at the replay disruption's own node are the scenario input, not predictions. They are excluded from all grading and are counted and listed separately as input steps.

3. **Node coverage:** Node coverage = (distinct non-seed nodes with at least one observed effect that the engine predicted) / (distinct non-seed nodes with at least one observed effect). 'Predicted' means at least one event at the node that is not a delay-0 cost-channel leaf (see cost_leaf_exclusion). Covered and uncovered nodes are listed.

4. **Delay accuracy:** Delay accuracy is graded ONLY on non-seed steps where observed_delay_d > 0 (quantified) AND the node was predicted: HIT if the observed delay is within [0.5x, 2x] of the predicted delay for that node, else MISS. The predicted delay for a node is the FIRST-HIT delay: the delay_d of the node's first event in stream order, excluding delay-0 cost-channel leaves (the estimator the harness backtest.py uses; an legacy grading used the maximum delay_d over all events at the node, so its figures are not comparable — a legacy-grading comparison computed on the same runs is published alongside the headline). Grading is deduplicated BY NODE: one graded step per node per replay, the earliest observed step for the node (first in dataset order); later quantified steps at an already-graded node are duplicates — counted, listed, and in no denominator. mean_abs_error_d is the mean of |observed - predicted| over the graded steps only. Quantified non-seed steps at unpredicted nodes have no predicted delay to grade against: they are genuine misses, listed individually, and excluded from the mean. Steps with observed_delay_d = 0 are 'effect observed, delay unquantified': counted in node coverage, excluded from delay grading, and listed.

5. **False positives:** False positives = nodes the engine predicted (cost-only nodes excluded — see cost_leaf_exclusion) with no observed effect at all; an observed step at the seed node counts as an observed effect for this purpose. Listed by node; they are not graded as misses of observed steps.

6. **Cost leaf exclusion:** Cost-leaf exclusion: an event on the 'cost' channel with delay_d = 0 is a cost-pressure leaf, not a delay prediction. A node whose only predicted events are delay-0 cost leaves counts as NOT predicted: it earns no coverage credit and is not a false positive. Such nodes are listed per replay as cost-only nodes. Cost leaves never supply a node's first-hit delay.

7. **Miss classification:** Every observed step carries exactly one class: 'ungradeable' (no evidence source — see evidence_rule; checked first, before any other class), 'seed-input' (at the disruption node), 'unquantified' (non-seed, observed_delay_d = 0), 'duplicate' (non-seed, quantified, at a node already graded in this replay — see the dedupe rule in delay_accuracy), or 'genuine' (non-seed, quantified, first step at its node). Genuine misses — graded steps outside the [0.5x, 2x] window plus quantified steps at unpredicted nodes — are listed individually with observed vs predicted values.

8. **Statistics:** Confidence intervals are exact Clopper-Pearson 95% intervals for a binomial proportion, computed on the pooled counts (coverage: covered / distinct non-seed observed nodes; delay hit rate: hits / graded steps). Baseline: a naive directed k-hop neighbourhood of the seed node over the graph's directed edges, expanded whole hop levels at a time until its non-seed node count first reaches the engine's predicted non-seed node count for the same replay (same events, same node budget); baseline coverage and precision (covered / predicted) are printed beside the engine's, per replay and pooled, and the scorecard states plainly when the engine does not beat the baseline on a metric.

9. **Effective spec:** Each replay is run on an effective disruption built from the dataset's stated spec: the stated capacity_loss is passed explicitly and the dataset's expected_duration_d (the disruption spec field) is mapped to the engine's duration_d. Every mapping or default substitution is recorded in the replay's disclosures and printed alongside its numbers; the duration used and the capacity_loss basis are stated for every replay. No silent defaults.

10. **Determinism:** This artifact embeds no timestamp (deliberate). Generating the scorecard twice from the same datasets and graph yields byte-identical scorecard.json and scorecard.md. Anchors: graph hash, per-dataset SHA-256 of the raw file bytes, per-replay run_id and event stream hash.

## Honesty caveats

- Edge shares are MODELED allocations pending re-derivation from PortWatch/Comtrade/CPPI/LSCI: the graph's edge capacity_share and transit values were allocated by the dataset builders, not extracted from those sources, so every predicted delay inherits that modeling choice.
- Predicted delays scale with the effective disruption duration: the duration used for each replay is the dataset's stated expected_duration_d, mapped explicitly (see each replay's section). For Red Sea 2024 that stated duration (559 d) is an observed lower bound — days from onset to the last timeline observation, with the disruption still unresolved — not a forecast.
- LA 2021 capacity_loss is a MODELED assumption, not a sourced figure (see the LA section, which prints the full basis alongside the LA numbers).
- This decomposed presentation refines the raw window-rule rate; it does not change the grading rule v1 used for live Ledger entries.
- LA 2021 is doubly in-sample: its capacity_loss input (0.78) is derived from the same MESocal 10 Nov 2021 snapshot that supplies the LA observed steps' labels, so the LA run is partly graded against its own input source.
- DEFERRED: the full 25-event protocol v2 is a roadmap item and has NOT been run. This scorecard grades the 3 shipped replay episodes only; no 25-event held-out protocol stands behind any figure printed here.
- Engine model (ENGINE REV 2, server/engine2): a stock-and-flow world model. Places hold a queue stock and a cover stock and serve at mu_max x (1 - loss); legs are FIFO delay lines of base_transit_d; wait = backlog / service rate (Little) evaluated on the cumulative curves; nothing above queue_cap is dropped (spillback holds it at the sender); blocked flow is re-routed onto the authored bypass legs up to their headroom; cover days against the shortfall rate give stockout times; sea flows are in vessel-calls/day (ports: TEU/day / teu_per_call), removing engine v1's mixed-unit sum. Every non-identity number is a registered plug-in (server/engine2/plugins.py); mu_max = 1.3 x baseline load, the 7-day cover and the cross-layer equivalence are TEMPORARY placeholders.
- Routes table (opt-in, graph services layer): graph sea legs that the selected service rotations serve through chokepoints are replaced by chains through those chokepoints (same attributed flow), and Suez / Bab el-Mandeb failures can reroute along the Cape with added days = route length difference. Rotations are SOURCED announcements (carrier releases and trade press, 23 pre-crisis rotations on the default selection), not proof of sailed routes; chokepoint placement beyond a printed marker and the Cape alternative's days are MODELLED. Only graph legs that a rotation serves through a chokepoint are bound; origin-destination pairs that are not graph legs carry no volume and are not modelled.
- Calibration profile rev2c (server/engine2/calibration.py, registry in server/engine2/plugins_cal.py): (1) peak service rate mu_max per place = P99 / median of the 7-day mean of IMF PortWatch daily counts over 2022-07-01..2023-10-29, replacing the 1.3 x baseline guess for every sea place with a series (ground and air hubs keep 1.3, TEMPORARY); a daily call or transit count is a demonstrated rate, not a design capacity; (2) the Los Angeles / Long Beach terminal cover is 4.0 d (Port of Los Angeles dwell-time release), every other place keeps the graph's placeholder cover (TEMPORARY); (3) an inventory event's delay_d is the peak cumulative arrival deficit in days (it was the length of the shortfall). The Cape detour days, layer equivalence, mode-shift fractions, no-restock, route foresight and the cost reference remain TEMPORARY. There is no Panama alternative route in the model: a long Panama cut keeps queueing.
- Profile d (server/engine2/profile_d.py, registry in server/engine2/plugins_d.py) on top of the rev2c calibration: (1) every sea leg of the graph sails at lane distance / service speed (MARNET network via searoute-py; BIMCO / DVZ speeds) where the leg has real geometry, the rest keep the graph's transit days; (2) gates: every place is a FIFO server with a bounded anchorage that also refuses exogenous through-traffic above queue_cap; Panama has fixed slots with a booked / unbooked split (ACP advisory A-04-2024), Suez has convoy windows (Kuehne+Nagel); (3) route choice is graded (logistic in the time advantage, scale TEMPORARY) and uses the lane that avoids a gate (Suez and Bab el-Mandeb unconditionally, as in rev 2; any other gate only where a selected service witnesses it, i.e. Panama -> Suez for Asia -> US East Coast) and port substitution along service rotations; (4) rotation recovery: a late arrival is carried to the vessel's next calls less a TEMPORARY absorbed fraction; (5) SHELL places route and propagate but mint no event. The logistic scale, the absorbed fraction, the speed-class boundary, the feeder limit and the ground / air mu_max ratios remain TEMPORARY. The Panama -> Suez alternative is witnessed by two Suez-sailed Asia -> US East Coast rotations; that they serve the pairs the routes table puts on Panama is MODELLED.
- The replay episodes are in-sample for this engine revision: it was configured with sight of them, and one definition was adopted after viewing a run on them. No plug-in value was set or changed to move a replay number; plug-in defaults come from the published sources cited in each plug-in's registry entry or from pinned graph/PortWatch files. The adopted definition: an inventory (stockout) event reports the length of the supply gap as delay_d, because it reported 0.0 d and could never be window-graded. This is therefore an in-sample check on the same three episodes, not a held-out evaluation.
- IN-SAMPLE: the two episodes (panama_2023_24, red_sea_2024) are the in-sample set every engine was checked against, and both are TRAIN in VAL-01. la_2021 is a TEST episode of VAL-01 and was not run for this card. Pre-registered configuration: see preregistration.md; the sealed TEST split was not touched by this card.

## Reproducibility

- Graph: hash `49b90bcc9efd844c6a90c92ed20718220c3239eb22b4a773d944be326bce53c3`, version `1.0`, 200 nodes, 536 edges.
- panama_2023_24: dataset `panama_2023_24.json` SHA-256 `9798efd698c2ed1afd40b5fe89b480f28ef1cda3432526f5a58e2cd482666a6a`; run_id `scorecard_panama_2023_24`; stream hash `cb8a65da8de4e5184ecfd495ed65746507c39fcb725e6c7e3dc181b1b458bc2d`.
- red_sea_2024: dataset `red_sea_2024.json` SHA-256 `79e86cb5945d4b9b9871c6064a12799fe2a9a8ba998f533e8f459328b50d2def`; run_id `scorecard_red_sea_2024`; stream hash `a315aa62d6d4f7b8ea8e546d833009dd5f8d76b760446e0a81c48760fead5037`.
- No timestamp is embedded in scorecard.json or this file (deliberate): regenerating from the same datasets + graph yields byte-identical files.
