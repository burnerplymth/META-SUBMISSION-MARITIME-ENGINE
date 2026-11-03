# SupermAP Scorecard — decomposed backtest grading

Every number below carries its denominator definition. The full methodology is in the Methodology section and is embedded in scorecard.json alongside these numbers.

## Headline

| Replay | Observed steps | Input steps (seed, excluded) | Ungradeable steps (no evidence) | Node coverage (covered / distinct non-seed nodes) | Delay grading (hits / graded) | Mean \|observed − predicted\| (graded steps) | False positives (predicted, never observed) | Genuine misses |
|---|---|---|---|---|---|---|---|---|
| la_2021 | 12 | 6 | 0 | 3 / 3 (100.0%) | 1 / 1 | 4.9 d | 36 | 0 |
| panama_2023_24 | 12 | 9 | 0 | 0 / 3 (0.0%) | 0 / 0 | — d | 9 | 2 |
| red_sea_2024 | 11 | 3 | 0 | 4 / 4 (100.0%) | 2 / 2 | 1.25 d | 184 | 0 |
| **Aggregate (pooled)** | 35 | 18 | 0 | 7 / 10 (70.0%) | 3 / 3 | 2.466667 d | 229 | 2 |

Denominators: node coverage is over distinct non-seed nodes with evidence-backed observed effects; delay grading is over non-seed steps with observed_delay_d > 0 at predicted nodes only, one graded step per node (earliest observed step; later quantified steps at the same node are duplicates, listed and in no denominator); the mean error is over those graded steps only. The predicted delay is the node's FIRST-HIT delay (first event in stream order, delay-0 cost leaves excluded); a node whose only events are delay-0 cost leaves counts as NOT predicted. Input steps, unquantified steps, ungradeable steps (no evidence source — never a hit, never a miss), duplicate steps, and quantified steps at unpredicted nodes are excluded from both rates and are listed per replay below.

## Statistical honesty

- Coverage 7/10 (70.0%), exact Clopper-Pearson 95% CI: [34.8%, 93.3%].
- Delay hit rate 3/3 (100.0%), exact Clopper-Pearson 95% CI: [29.2%, 100.0%].
- Small sample, stated plainly: 35 observed steps across 3 replays yield 3 graded steps over 10 distinct non-seed nodes. One graded step is worth 33.333333 points of hit rate; one node is worth 10.0 points of coverage. Treat every rate here as a small-sample estimate, not a measurement.

### Naive baseline — directed k-hop, same events, same node budget

Baseline method: directed BFS from the seed over the graph's directed edges, whole hop levels, until the non-seed node count first reaches the engine's predicted non-seed count for the same replay. Precision = covered / predicted.

| Replay | Engine coverage | Baseline coverage (k hops, budget) | Engine precision | Baseline precision |
|---|---|---|---|---|
| la_2021 | 3 / 3 (100.0%) | 3 / 3 (100.0%) (k=3, budget 39, predicted 77) | 7.7% | 3.9% |
| panama_2023_24 | 0 / 3 (0.0%) | 2 / 3 (66.7%) (k=2, budget 9, predicted 26) | 0.0% | 7.7% |
| red_sea_2024 | 4 / 4 (100.0%) | 4 / 4 (100.0%) (k=10, budget 188, predicted 177) | 2.1% | 2.3% |
| **Pooled** | 7 / 10 (70.0%) | 9 / 10 (90.0%) | 3.0% | 3.2% |

- On coverage, the engine (70.0%) does NOT beat the naive baseline (90.0%).
- On precision, the engine (3.0%) does NOT beat the naive baseline (3.2%).

## Comparison with legacy grading (labelled, not comparable)

| Grading | Engine | Coverage | Delay hits | Mean |error| (d) | False positives |
|---|---|---|---|---|---|
| Headline (first-hit, node dedupe, cost leaves excluded) | current | 7 / 10 | 3 / 3 | 2.466667 | 229 |
| Legacy rules on the SAME runs (max-over-events, no dedupe, cost leaves counted) | current | 7 / 10 | 4 / 4 | 2.475 | 239 |
| As published for the original engine (original grading: max-over-events estimator, no node dedupe, delay-0 cost leaves counted as predictions) | original | 9 / 10 | 3 / 5 | 7.68 | 52 |

The three rows answer three different questions (different engine and/or different grading rules); no row may be quoted as a like-for-like improvement or regression against another.

## Replay: la_2021 — Los Angeles / Long Beach congestion, 2021

- Seed (disruption) node: `sea_losangeles` — 6 input step(s) at this node excluded from grading.
- Duration used: 58.0 d (dataset expected_duration_d mapped to engine duration_d).
- Capacity loss used: 0.78 (basis kind: modeled). Basis: MODELED assumption - not a sourced figure and never to be presented as one. No sourced capacity-loss fraction exists for LA 2021: the source record quantifies queues and waits, not capacity loss, and berth throughput did not collapse (the Port of Los Angeles was on track for a record ~10.7M TEU in 2021, Port of Los Angeles release, 20 Dec 2021), so no throughput-drop derivation is defensible. Model: at the 10 Nov 2021 MESocal snapshot the complex held 109 container ships waiting and 30 at berth; capacity_loss is set to the waiting share of container ships present, 109 / (109 + 30) = 0.78 (rounded to 2 dp) - a stock-share proxy for the share of container-ship flow the berth/landside system was not serving at the peak snapshot, NOT a measured throughput drop. The binding constraint was landside (dwell/driver channel), not berth capacity. Any scorecard printing LA results must print this modeled assumption alongside them. (An earlier version carried capacity_loss as null; the engine then silently substituted its 0.5 default for congestion-kind specs.)
- Capacity loss source: Marine Exchange of Southern California (MESocal) 10 Nov 2021 report, via Western Overseas: https://www.westernoverseas.com/port-congestion-update-11-12-21/ (same report already cited for the 2021-11-10 observed steps in this dataset)
- Dataset notes: capacity_loss 0.78 is a MODELED value (see capacity_loss_basis in the disruption spec for the full derivation): the source timeline quantifies queue counts and waits, not a capacity-loss fraction, and no sourced figure is claimed. The binding constraint was landside (dwell/driver channel), not berth capacity. expected_duration_d 58 = 2021-09-19 (first dated record in the timeline) to 2021-11-16 (offshore queuing system takes effect) - the window covered by the source timeline, not the full historical episode. Pre-pandemic baseline: the complex had never seen a backlog greater than 17 ships (MESocal).
- Disclosure — duration_d: mapped (value 58.0). replay specs carry the field expected_duration_d; the engine reads duration_d — mapped explicitly so the engine's silent 7.0 d default cannot fire
- Disclosure — shape: default_substituted (value step). dataset states no shape; engine default passed explicitly
- Run: run_id `scorecard_la_2021`, stream hash `9088727a0f33ad9b719e7547526b0757d2ee0618e23efaa2ff37136b17413e5c`, 83 events; dataset file `la_2021.json`, dataset SHA-256 `f21195ad5e6b1dcb6aed9add60079ec1af9e473b1c0cc84e801c47671e8ea4ab`.

### Node coverage — 3 / 3 distinct non-seed nodes (100.0%)

- Covered nodes: `gnd_chicago`, `gnd_inlandempire`, `sea_longbeach`
- Uncovered nodes: none

### Delay accuracy — 1 hit / 0 miss across 1 graded step(s); mean |observed − predicted| = 4.9 d

Predicted delay per node = first-hit delay_d (first event in stream order, delay-0 cost leaves excluded). HIT = observed within [0.5×, 2×] of predicted. One graded step per node: the earliest observed step grades.

| Node | Observed (d) | Predicted — first-hit (d) | Window | Result | \|Error\| (d) |
|---|---|---|---|---|---|
| `sea_longbeach` | 14.9 | 10.0 | [5.0, 20.0] | hit | 4.9 |

### Input steps (seed node, excluded from grading) — 6

- `sea_losangeles`: observed 0.0 d (2021-09-19) — Record 97 vessels of all types in port (within 40 miles) as the queue builds (joint LA/LB complex figure).
- `sea_losangeles`: observed 0.0 d (2021-10-13) — White House brokers shift to 24/7 gate operations at LA/LB to address the backlog.
- `sea_losangeles`: observed 0.0 d (2021-10-18) — 100 vessels at anchor (record), 157 vessels of all types in port, 97 of them container ships (joint LA/LB complex figures).
- `sea_losangeles`: observed 0.0 d (2021-11-09) — New all-time record: 111 container ships waiting off the complex (joint LA/LB figure).
- `sea_losangeles`: observed 14.9 d (2021-11-10) — 164 ships in the complex; 109 container ships (79 at anchorage/drift, 30 at berth); average container-ship wait 13.7-16.1 days; ~700,000 containers waiting on the water. observed_delay_d 14.9 is the midpoint of the observed wait range.
- `sea_losangeles`: observed 0.0 d (2021-11-16) — New offshore queuing system takes effect: arriving container ships queue ~150 miles offshore in sailing-time order instead of anchoring in San Pedro Bay (queue count methodology changes from this date).

### Unquantified steps (observed_delay_d = 0; in coverage, not delay-graded) — 5

- `sea_longbeach`: predicted delay (first-hit) 10.0 d — Record 29 container ships in holding (drift) areas - anchorages full; 70+ container ships at anchor by 21 Sep, with 30+ at berth (joint LA/LB complex figures).
- `sea_longbeach`: predicted delay (first-hit) 10.0 d — Record 108 container ships waiting to dock (joint LA/LB complex figure).
- `gnd_inlandempire`: predicted delay (first-hit) 3.333333 d — National truck-driver shortage quantified at 80,000 drivers (American Trucking Associations, Oct 2021) - the inland evacuation bottleneck behind terminal dwell; the source row names the dwell channel at gnd_inlandempire and gnd_chicago jointly.
- `gnd_chicago`: predicted delay (first-hit) 3.333333 d — Same ATA driver-shortage observation (80,000 drivers, Oct 2021) on the rail knock-on node named in the source row.
- `sea_longbeach`: predicted delay (first-hit) 10.0 d — Harbor commissions vote the container dwell fee: $100/container/day, escalating $100/day, for boxes dwelling 9+ days (truck) / 6+ days (rail); data collection from 1 Nov, charging from 15 Nov (joint LA/LB measure).

### Ungradeable steps (no evidence source; never a hit, never a miss) — 0

- none

### Duplicate steps (node already graded; in no denominator) — 0

- none

### Cost-only predicted nodes (only delay-0 cost leaves; count as NOT predicted) — 3

`air_incheon`, `air_losangeles`, `sea_colon`

### False positives — 36 predicted node(s) with no observed effect

`air_ontario`, `chk_panama`, `chk_taiwan`, `gnd_blaine`, `gnd_calexico`, `gnd_dallas`, `gnd_detroit`, `gnd_kansascity`, `gnd_memphis`, `gnd_saltlakecity`, `gnd_stlouis`, `gnd_stockton`, `gnd_sweetgrass`, `gnd_vancouver`, `sea_antwerp`, `sea_busan`, `sea_dongguan`, `sea_guangzhou`, `sea_haiphong`, `sea_hochiminh`, `sea_hongkong`, `sea_kaohsiung`, `sea_keelung`, `sea_ningbo`, `sea_nwsa`, `sea_oakland`, `sea_princerupert`, `sea_qingdao`, `sea_qinzhou`, `sea_shanghai`, `sea_shenzhen`, `sea_tianjin`, `sea_tokyo`, `sea_vancouver`, `sea_xiamen`, `sea_yingkou`

## Replay: panama_2023_24 — Panama Canal drought, 2023-24

- Seed (disruption) node: `chk_panama` — 9 input step(s) at this node excluded from grading.
- Duration used: 215.0 d (dataset expected_duration_d mapped to engine duration_d).
- Capacity loss used: 0.5 (basis kind: derived). Basis: Derived by arithmetic only on the two sourced ACP slot counts: normal schedule ~36 transits/day, crisis minimum 18/day from 1 Feb 2024; capacity_loss = 1 - 18/36 = 0.5. No conversion factor involved.
- Capacity loss source: Panama Canal Authority (ACP) Customer Advisory 30 Oct 2023, via DB Group: https://www.dbgroup.net/en/news/panama-canal-daily-transit-limited-due-drought
- Dataset notes: capacity_loss 0.5 = ACP slot schedule 36/day normal to 18/day minimum (Feb 2024). expected_duration_d 215 = 2023-10-30 (ACP advisory) to 2024-06-01 (slots restored to 32/day, ~89% of normal). Queue/draft figures rest partly on the Ballast Markets secondary compilation of ACP/press reporting - re-verification against ACP advisories and IMF PortWatch is an open item.
- Disclosure — duration_d: mapped (value 215.0). replay specs carry the field expected_duration_d; the engine reads duration_d — mapped explicitly so the engine's silent 7.0 d default cannot fire
- Disclosure — shape: default_substituted (value step). dataset states no shape; engine default passed explicitly
- Run: run_id `scorecard_panama_2023_24`, stream hash `e71974bbceaf4882ab2fe6adb590c5b1408e0291ae3b8c61f04effeb28939274`, 20 events; dataset file `panama_2023_24.json`, dataset SHA-256 `9798efd698c2ed1afd40b5fe89b480f28ef1cda3432526f5a58e2cd482666a6a`.

### Node coverage — 0 / 3 distinct non-seed nodes (0.0%)

- Covered nodes: none
- Uncovered nodes: `chk_suez`, `sea_losangeles`, `sea_newyork`

### Delay accuracy — 0 hit / 0 miss across 0 graded step(s); mean |observed − predicted| = — d

Predicted delay per node = first-hit delay_d (first event in stream order, delay-0 cost leaves excluded). HIT = observed within [0.5×, 2×] of predicted. One graded step per node: the earliest observed step grades.

No graded steps in this replay.

Quantified steps at unpredicted nodes (genuine misses, excluded from the mean above):

- `sea_newyork`: observed 8.5 d, no prediction. Asia to US East Coast container services rerouted via Suez (+7-10 days) during the peak-crisis window. observed_delay_d 8.5 is the midpoint of the observed +7-10 d range.
- `chk_suez`: observed 8.5 d, no prediction. Suez used as the substitute route for Asia-US East Coast services (+7-10 days) - while Suez itself was constrained by the Red Sea crisis from Dec 2023. observed_delay_d is the midpoint of the observed range.

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

### Cost-only predicted nodes (only delay-0 cost leaves; count as NOT predicted) — 0

none

### False positives — 9 predicted node(s) with no observed effect

`sea_balboa`, `sea_buenosaires`, `sea_callao`, `sea_cartagena`, `sea_colon`, `sea_guayaquil`, `sea_manzanillo`, `sea_sanantonio`, `sea_santos`

## Replay: red_sea_2024 — Red Sea / Bab el-Mandeb crisis, 2023-2025

- Seed (disruption) node: `chk_babelmandeb` — 3 input step(s) at this node excluded from grading.
- Duration used: 559.0 d (dataset expected_duration_d mapped to engine duration_d).
- Capacity loss used: 0.7 (basis kind: sourced). Basis: The loss figure is stated by the source: gross tonnage of ships entering the Gulf of Aden fell >70% between the 1st half of Dec 2023 and the 1st half of Feb 2024; taken as 0.7 (the stated floor). Corroborated in the same timeline by Suez transits -70% by Jun 2024 (UNCTAD Review of Maritime Transport 2024). No arithmetic conversion applied. Container-ship capacity fell further (-92% by Jun 2024); 0.7 is the conservative all-shipping figure.
- Capacity loss source: UNCTAD, via Africa Ports feature (Apr 2024): https://africaports.co.za/2024/04/24/feature-red-sea-crisis-and-implications-for-trade-facilitation-in-africa/
- Dataset notes: capacity_loss 0.7 = observed gross-tonnage fall through the Gulf of Aden (>70% by mid-Feb 2024, UNCTAD); container-ship capacity fell further (-92% by Jun 2024). expected_duration_d 559 = days from 2023-11-19 to the last timeline observation (2025-05-31, Suez tonnage still ~70% below 2023) - an observed lower bound, not a forecast; the disruption had not resolved in the source timeline.
- Disclosure — duration_d: mapped (value 559.0). replay specs carry the field expected_duration_d; the engine reads duration_d — mapped explicitly so the engine's silent 7.0 d default cannot fire
- Disclosure — shape: default_substituted (value step). dataset states no shape; engine default passed explicitly
- Run: run_id `scorecard_red_sea_2024`, stream hash `c4feb1de8c319bdffe8efdfe67fd28f3f2e3de7f7668609ff1dde1794a89ea90`, 385 events; dataset file `red_sea_2024.json`, dataset SHA-256 `79e86cb5945d4b9b9871c6064a12799fe2a9a8ba998f533e8f459328b50d2def`.

### Node coverage — 4 / 4 distinct non-seed nodes (100.0%)

- Covered nodes: `chk_capeofgoodhope`, `chk_suez`, `sea_rotterdam`, `sea_shanghai`
- Uncovered nodes: none

### Delay accuracy — 2 hit / 0 miss across 2 graded step(s); mean |observed − predicted| = 1.25 d

Predicted delay per node = first-hit delay_d (first event in stream order, delay-0 cost leaves excluded). HIT = observed within [0.5×, 2×] of predicted. One graded step per node: the earliest observed step grades.

| Node | Observed (d) | Predicted — first-hit (d) | Window | Result | \|Error\| (d) |
|---|---|---|---|---|---|
| `sea_rotterdam` | 10.0 | 10.0 | [5.0, 20.0] | hit | 0.0 |
| `chk_capeofgoodhope` | 10.0 | 7.5 | [3.75, 15.0] | hit | 2.5 |

### Input steps (seed node, excluded from grading) — 3

- `chk_babelmandeb`: observed 0.0 d (2023-11-19) — Incident involving the car carrier Galaxy Leader in the Red Sea - first of a series of incidents that led carriers to avoid the corridor (crew held until 22 Jan 2025).
- `chk_babelmandeb`: observed 0.0 d (2024-02-15) — Gross tonnage of ships entering the Gulf of Aden fell >70% (1st half Dec 2023 to 1st half Feb 2024 window).
- `chk_babelmandeb`: observed 0.0 d (2024-06-30) — Container-ship capacity entering the Gulf of Aden -92% (by Jun 2024). Rerouting raised overall vessel demand by ~3% and container-ship demand by ~12% (UNCTAD via Splash247).

### Unquantified steps (observed_delay_d = 0; in coverage, not delay-graded) — 5

- `chk_suez`: predicted delay (first-hit) 6.666667 d — Onset of sustained carrier diversions away from the Red Sea corridor (UNCTAD reference timeline).
- `chk_suez`: predicted delay (first-hit) 6.666667 d — By end of December 2023, traffic through the Suez Canal was down by about half vs pre-crisis levels.
- `chk_suez`: predicted delay (first-hit) 6.666667 d — Suez transits -70% vs mid-Dec 2023 (by Jun 2024). Canal revenue fell $9.4B (FY2022/23) to $7.2B (FY2023/24); transits 25,911 to 20,148.
- `sea_shanghai`: predicted delay (first-hit) 10.0 d — Shanghai Containerized Freight Index (SCFI) averaged 2,496 points in 2024, +149% year on year (rate channel; node is the index's home port).
- `chk_suez`: predicted delay (first-hit) 6.666667 d — Suez tonnage still approximately 70% below its 2023 level - no full recovery 17 months in.

### Ungradeable steps (no evidence source; never a hit, never a miss) — 0

- none

### Duplicate steps (node already graded; in no denominator) — 1

- `chk_capeofgoodhope`: observed 10.0 d — duplicate of the earliest graded step at this node (one graded step per node per replay)

### Cost-only predicted nodes (only delay-0 cost leaves; count as NOT predicted) — 7

`air_amsterdam`, `air_beijing`, `air_guangzhou`, `air_indianapolis`, `air_istanbul`, `air_miami`, `air_shenzhen`

### False positives — 184 predicted node(s) with no observed effect

`air_addisababa`, `air_anchorage`, `air_atlanta`, `air_bangkok`, `air_brussels`, `air_chicago`, `air_cincinnati`, `air_cologne`, `air_delhi`, `air_doha`, `air_dubai`, `air_dubaiworldcentral`, `air_ezhou`, `air_frankfurt`, `air_hongkong`, `air_houston`, `air_incheon`, `air_jakarta`, `air_leipzig`, `air_liege`, `air_london`, `air_losangeles`, `air_louisville`, `air_madrid`, `air_memphis`, `air_milan`, `air_narita`, `air_newyork`, `air_ontario`, `air_osaka`, `air_paris`, `air_shanghaipudong`, `air_singapore`, `air_taipei`, `air_toronto`, `air_zhengzhou`, `chk_danish`, `chk_dover`, `chk_gibraltar`, `chk_hormuz`, `chk_malacca`, `chk_mozambique`, `chk_panama`, `chk_taiwan`, `chk_turkish`, `gnd_alexandriabay`, `gnd_atlanta`, `gnd_bettembourg`, `gnd_blaine`, `gnd_budapest`, `gnd_buffalo`, `gnd_calexico`, `gnd_channeltunnel`, `gnd_chicago`, `gnd_citydeep`, `gnd_columbus`, `gnd_dadri`, `gnd_dallas`, `gnd_detroit`, `gnd_duisburg`, `gnd_eaglepass`, `gnd_elpaso`, `gnd_frontroyal`, `gnd_greer`, `gnd_halkali`, `gnd_hidalgo`, `gnd_inlandempire`, `gnd_kansascity`, `gnd_khorgos`, `gnd_laredo`, `gnd_madrid`, `gnd_malaszewicze`, `gnd_memphis`, `gnd_milan`, `gnd_montreal`, `gnd_nogales`, `gnd_otaymesa`, `gnd_paris`, `gnd_porthuron`, `gnd_riyadh`, `gnd_saltlakecity`, `gnd_stlouis`, `gnd_stockton`, `gnd_sweetgrass`, `gnd_toronto`, `gnd_vancouver`, `gnd_warsaw`, `sea_algeciras`, `sea_ambarli`, `sea_antwerp`, `sea_balboa`, `sea_barcelona`, `sea_beirut`, `sea_bremerhaven`, `sea_brisbane`, `sea_buenosaires`, `sea_busan`, `sea_caimep`, `sea_callao`, `sea_capetown`, `sea_cartagena`, `sea_charleston`, `sea_chattogram`, `sea_colombo`, `sea_colon`, `sea_constanta`, `sea_dalian`, `sea_damietta`, `sea_dammam`, `sea_dongguan`, `sea_dublin`, `sea_durban`, `sea_felixstowe`, `sea_fuzhou`, `sea_gdansk`, `sea_gdynia`, `sea_genoa`, `sea_gioiatauro`, `sea_guangzhou`, `sea_guayaquil`, `sea_gwangyang`, `sea_haiphong`, `sea_halifax`, `sea_hamad`, `sea_hamburg`, `sea_hochiminh`, `sea_hongkong`, `sea_houston`, `sea_incheon`, `sea_jebelali`, `sea_jeddah`, `sea_kaohsiung`, `sea_karachi`, `sea_keelung`, `sea_khalifa`, `sea_koper`, `sea_laemchabang`, `sea_lehavre`, `sea_lianyungang`, `sea_longbeach`, `sea_losangeles`, `sea_manila`, `sea_manzanillo`, `sea_marsaxlokk`, `sea_melbourne`, `sea_mersin`, `sea_mombasa`, `sea_montreal`, `sea_mundra`, `sea_nagoya`, `sea_newyork`, `sea_ningbo`, `sea_nwsa`, `sea_oakland`, `sea_piraeus`, `sea_portklang`, `sea_portsaid`, `sea_princerupert`, `sea_qingdao`, `sea_qinzhou`, `sea_rizhao`, `sea_salalah`, `sea_sanantonio`, `sea_santos`, `sea_savannah`, `sea_shenzhen`, `sea_sines`, `sea_singapore`, `sea_sydney`, `sea_taicang`, `sea_taichung`, `sea_tangermed`, `sea_tanjungpelepas`, `sea_tanjungperak`, `sea_tanjungpriok`, `sea_tianjin`, `sea_tokyo`, `sea_valencia`, `sea_vancouver`, `sea_virginia`, `sea_xiamen`, `sea_yantai`, `sea_yingkou`, `sea_yokohama`

## Genuine misses — all replays (published, not hidden)

| Replay | Node | Observed (d) | Predicted — first-hit (d) | Why it is a miss |
|---|---|---|---|---|
| panama_2023_24 | `sea_newyork` | 8.5 | — | node not predicted |
| panama_2023_24 | `chk_suez` | 8.5 | — | node not predicted |

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
- Engine model (revision 2): lost flow is aggregated in absolute terms over each node's inbound edges; delays are service-time waits from stored backlog — backlog = shortfall x inbound flow x duration, stored capped by the node's real queue_cap, cleared at the spare service rate capacity x (1 - utilisation); one continuous delay function serves queue and congestion; model constants live in the versioned registry. Utilisation is a global registry ASSUMPTION (0.85) — the graph carries no per-node utilisation field — so every wait inherits it. Per-node outcomes are in the tables above; no node-level narrative is reused from engine v1.
- IN-SAMPLE: the engine rules were configured with sight of these replay results — the backlog model was adjusted against this same three-episode replay set. This scorecard is therefore an in-sample fit check on three known episodes, not a held-out evaluation, and its figures must not be quoted as out-of-sample accuracy.
- LA 2021 is doubly in-sample: its capacity_loss input (0.78) is derived from the same MESocal 10 Nov 2021 snapshot that supplies the LA observed steps' labels, so the LA run is partly graded against its own input source.
- DEFERRED: the full 25-event protocol v2 is a roadmap item and has NOT been run. This scorecard grades the 3 shipped replay episodes only; no 25-event held-out protocol stands behind any figure printed here.

## Reproducibility

- Graph: hash `fa02c992727e26c62c943b7d5a8fe81581647ec647776c901c3f44fc0e7706bf`, version `1.0`, 200 nodes, 536 edges.
- la_2021: dataset `la_2021.json` SHA-256 `f21195ad5e6b1dcb6aed9add60079ec1af9e473b1c0cc84e801c47671e8ea4ab`; run_id `scorecard_la_2021`; stream hash `9088727a0f33ad9b719e7547526b0757d2ee0618e23efaa2ff37136b17413e5c`.
- panama_2023_24: dataset `panama_2023_24.json` SHA-256 `9798efd698c2ed1afd40b5fe89b480f28ef1cda3432526f5a58e2cd482666a6a`; run_id `scorecard_panama_2023_24`; stream hash `e71974bbceaf4882ab2fe6adb590c5b1408e0291ae3b8c61f04effeb28939274`.
- red_sea_2024: dataset `red_sea_2024.json` SHA-256 `79e86cb5945d4b9b9871c6064a12799fe2a9a8ba998f533e8f459328b50d2def`; run_id `scorecard_red_sea_2024`; stream hash `c4feb1de8c319bdffe8efdfe67fd28f3f2e3de7f7668609ff1dde1794a89ea90`.
- No timestamp is embedded in scorecard.json or this file (deliberate): regenerating from the same datasets + graph yields byte-identical files.
