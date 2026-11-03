# SIM BACKTEST SCORECARD V2

Grades the **SIM layer** (traffic, queues, diversions) against documented historical blockages, in the extended run (wait-vs-divert, stepped shocks, call reassignment and exposed-subpopulation grading, defined in `preregistration_v2.md`). Separate from the causal-chain scorecard; the two are never merged. **The baseline scorecard (`scorecard.md` / `scorecard.json`) is unchanged and its grades are never recomputed** — baseline sim values below are quoted from it.

Pre-registration: `preregistration_v2.md`, recorded **before any extended run and before any behaviour change**, together with the two held-out event records and `run_configs_v2.json`. Held-out events (Panama, LA/LB) were never used to design or tune any mechanism; each registered run executed **once**, on the final code. Graph SHA-256: `9743a288fb5d670d8f814f4f6240bab395fd72b7f63ee50475793539e564a062`. Bands as registered (ratio ∈ [0.5, 2]; range [a, b] → sim ∈ [a/2, 2b]; share/drop |diff| ≤ 20 pp; unsourced actual → UNGRADED).

## Aggregates

| Set | Graded | Hits | Misses | Ungraded / undefined |
|---|---|---|---|---|
| Original 8 metrics — baseline run | 7 | 3 | 4 | 1 |
| Original 8 metrics — extended run | 7 | 2 | 5 | 1 |
| Held-out (Panama + LA/LB) — extended run | 5 | 2 | 3 | 1 |

Extended-run additions graded outside both aggregates: **BA-2v** (calls+tonnage yardstick for call reassignment) and **RS-3** (exposed-subpopulation metric — resolved UNDEFINED, see the census verdict below).

## Ever Given / Suez Canal — baseline config unchanged

| ID | Metric | Sim (baseline → extended) | Observed | Band | Grade (baseline → extended) |
|---|---|---|---|---|---|
| EG-1 | Excess peak queue at chk_suez, in days of normal corridor flow | 0.55 → **2.18** (excess peak 36 vessels ÷ 16.5 corridor departures/day) | **7.42** (422 vessels (SCA via Reuters) ÷ PortWatch baseline 56.91 transits/day) | ratio in [0.5, 2] | MISS → **MISS** |
| EG-2 | Mean added delay at chk_suez (twin statistic) | 2.33 → **1.92** (hold arrivals at chk_suez: 38) | **—** (average delay per queued vessel could not be sourced (only forecasts and anecdotes exist); never estimated) | — | UNGRADED → **UNGRADED** |
| EG-3 | Clearance days after reopening (queue back to twin level, sustained 3 ticks) | 9.45 → **—** (clearance NOT reached within the run (disrupted queue still above twin at the horizon) — the held queue's tail outlasts the 30-tick run) | **5.00** (backlog fully cleared 2021-04-03 (Reuters/SCA); reopening 2021-03-29) | ratio in [0.5, 2] | HIT → **MISS** |

Behaviour: divert segments 9, hold arrivals at chk_suez 38 (baseline run: 52 diverts, 9 holds — wait-vs-divert now holds vessels whose bypass costs more time than the expected wait).

## Baltimore / Key Bridge — stepped shock + call reassignment

| ID | Metric | Sim (baseline → extended) | Observed | Band | Grade (baseline → extended) |
|---|---|---|---|---|---|
| BA-1 | Port-call drop at sea_baltimore during closure window (berth calls, disrupted vs twin) | -100.00 → **-51.72** (span days [2, 78], berth means 0.18 vs twin 0.38/day) | **-60.06** | |diff| <= 20 pp | MISS → **HIT** |
| BA-2 | Diversion uplift at sea_virginia during closure window (berth calls % change vs twin) — baseline calls-only yardstick, kept for comparability | -14.95 → **-7.54** | **2.08** | |diff| <= 20 pp | HIT → **HIT** |
| BA-2v | Virginia diversion effect, calls AND tonnage combined (extended-run addition; call-reassignment yardstick) | **-7.54** (calls -7.54% × reassigned tonnage +0.00% (0 reassigned calls, 0 capacity-units over 76 d ÷ twin Virginia tonnage/day 1,761)) | **31.23** (calls +2.08% × AIS-estimated import tonnage +28.56% (PortWatch port826, same windows)) | |diff| <= 20 pp | **MISS** |
| BA-3 | Recovery days after full reopening (berth calls >= 90% of twin, sustained 3 d) | 0 → **2** (recovery measured from the last step's end (2024-06-10, full channel restoration)) | **13** (first 7-day mean >= 90% of baseline on 2024-06-23) | ratio in [0.5, 2] | MISS → **MISS** |

Reassignment accounting: 10 of 12 Baltimore-serving lanes re-pointed during the span (sea_newyork: 0 departures, 0 calls, 0 capacity-units; sea_virginia: 0 departures, 0 calls, 0 capacity-units; sea_wilmingtonde: 0 departures, 0 calls, 0 capacity-units). **Zero reassigned departures fired**: all 14 Baltimore-bound departures inside the span were Virginia-origin legs of the Baltimore↔Virginia shuttle — that lane is assigned to Virginia by the share order, and a departure already AT its substitute cannot re-point (the declared rule); the port's other lanes carried no in-span Baltimore-bound departures (the census gives Baltimore 14 inbound departures in 76 days). BA-2v's tonnage component is therefore 0 and BA-2v grades the calls-only reality. Printed as found; no re-run.

## Red Sea window — open-ended expectation

| ID | Metric | Sim (baseline → extended) | Observed | Band | Grade (baseline → extended) |
|---|---|---|---|---|---|
| RS-1 | Suez transit drop, Jan-Feb 2024 (completed non-diverted arrivals on Suez-transiting lanes, disrupted vs twin) | 0.00 → **0.00** (completions 1075 vs twin 1075) | **-40.84** | |diff| <= 20 pp | MISS → **MISS** |
| RS-2 | Mean lateness of arrivals on Bab-el-Mandeb-transiting lanes, Jan-Feb 2024 (occurrence-matched vs twin) | 7.66 → **—** (over 0 matched pairs — ZERO pairs: under the open-ended expectation the exposed local-hop traffic has no certified bypass and is HELD at the strait past the window, so no in-window arrival exists to be late; the statistic is undefined and prints MISS (the conservative label), not a hit) | **[7, 12]** | sim in [3.5, 24] (observed range [7, 12] widened by [0.5x, 2x]) | HIT → **MISS** |
| RS-3 | Exposed-subpopulation Suez flow change (extended-run addition): RS-1 extraction restricted to lanes transiting BOTH chk_babelmandeb AND chk_suez | **—** (the modelled census contains 0 lanes transiting both straits (census audit, census_audit.json)) | **-51.83** (PortWatch Bab el-Mandeb n_total, Jan-Feb 2024 vs Nov 2023) | |diff| <= 20 pp | **UNDEFINED — NO EXPOSED LANES IN CENSUS** |

Ungraded decomposition: completions change on Bab-el-Mandeb-only lanes -100.0%, on Suez-only lanes 0.0%.

## Held-out: Panama Canal drought (2023–24)

| ID | Metric | Sim | Observed | Band | Grade |
|---|---|---|---|---|---|
| PA-1 | Transit drop %, tightest phase (completed non-diverted arrivals on chk_panama-transiting lanes, 2023-12-01 → 2024-01-16, disrupted vs twin) | **-6.71** (completions 320 vs twin 343) | **-34.27** (PortWatch Panama n_total 21.11/day (2023-12-01 → 2024-01-15) vs baseline 32.11/day (2023-01-01 → 2023-07-29)) | |diff| <= 20 pp | **MISS** |
| PA-2 | Excess peak queue, days of flow (August phase run) | **0.55** (excess peak 6 vessels ÷ 10.96 twin corridor departures/day (August run)) | **2.27** (excess queue 163 − 90 = 73 vessels (FreightWaves from ACP data) ÷ 32.11 baseline transits/day) | ratio in [0.5, 2] | **MISS** |
| PA-3 | Mean added delay at chk_panama (twin statistic, primary run) | **13.88** (hold arrivals at chk_panama: 0 (counted separately, never averaged in)) | **[5, 15]** (published waiting-time forecast range [5, 15] d (Waterfront Maritime Services, 2023-11-07) — a dated forecast range, per the registration's stated basis) | sim in [2.5, 30] | **HIT** |

Ungraded (primary run): queue peaks disrupted/twin 44/45; excess peak in days of flow -0.11 d (the PA-2 construction on the December phase — printed ungraded per the registration); divert segments 0; hold arrivals at chk_panama 0; clearance after 2024-01-16: 5.0 d (no comparator — the real canal stepped UP to a still-restricted level).

## Held-out: LA/LB congestion (2021)

| ID | Metric | Sim | Observed | Band | Grade |
|---|---|---|---|---|---|
| LB-1 | Excess peak queue, days of flow | **0.00** (excess peak 0 vessels ÷ twin berth-call rate 6.24/day at sea_losangeles) | **19.94** (MXSOC peak backup 109 container ships (2022-01-09) ÷ PortWatch baseline container call rate 5.467/day) | ratio in [0.5, 2] | **MISS** |
| LB-2 | Port-call change, peak window (berth calls at sea_losangeles, disrupted vs twin) | **-7.73** (berth means 5.75 vs twin 6.24/day over the run window) | **7.68** (PortWatch port664 total calls 10.984/day (2021-10-01 → 2022-01-31) vs baseline 10.200/day (Q1 2021) — a RISE, as registered) | |diff| <= 20 pp | **HIT** |
| LB-3 | Clearance days after the window | **1.00** | **—** (no capacity-restoration date exists (demand-side emptying over 2022); observed milestones 232 d / 283 d / 317 d documented in the actuals file — no like-for-like comparator, per the registration) | — | **UNGRADED** |

Ungraded: queue peaks at sea_losangeles disrupted/twin 0/0; at the unshocked sea_longbeach 0/0; mean added delay at sea_losangeles 3.48 d. The registered 0.20 loss is a window-compressed equivalent: the sourced sustained deficit is ≈0.08, the balance being the pre-October queue build compressed into the window — not a claim that real capacity fell 20%.

## Census-audit verdict

census weights FAITHFUL; through share 0.0 by construction of the leg census + rendered-path transit identification; no census data changed (census_audit.json) — full audit: `census_audit.json`. Consequence: RS-3 resolves UNDEFINED as registered; the Red Sea miss (RS-1) is a census-structure result, not a weights error, and no in-boundary fix exists.

## Reproducibility

Every extended-run grade and observed comparator is recomputed by `build_scorecard_v2.py` from the stored raws and series; the shared comparators equal the baseline run's published values.
