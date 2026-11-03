# SIM BACKTEST SCORECARD

Grades the **SIM layer** (traffic, queues, diversions) against documented
historical blockages. This is a separate artifact from the causal-chain
scorecard (`data/replay/scorecard.*`): the two grade different engines and
are never merged, pooled, or averaged.

Pre-registration: `preregistration.md` in this directory, recorded
**before any sim run for these events was executed**. Every run used the
shipped sim exactly as registered. Graph SHA-256:
`9743a288fb5d670d8f814f4f6240bab395fd72b7f63ee50475793539e564a062`
(992 places / 4,550 routes, current graph — the sim's traffic is modelled
from lane structure, not the historical fleet; graded metrics are
shape/ratio-first and absolute sim counts carry that caveat).

Bands (registered in advance, mirroring the chain scorecard's window convention): ratio
metrics HIT iff sim/observed ∈ [0.5, 2]; observed range [a, b] HIT iff sim
∈ [a/2, 2b]; share/drop metrics HIT iff |sim − observed| ≤ 20 percentage
points. An actual that could not be sourced makes its metric UNGRADED —
never a hit, never a miss, never replaced by an estimate.

## Aggregate

| Graded metrics | Hits | Misses | Ungraded (no sourced actual) |
|---|---|---|---|
| 7 | 3 | 4 | 1 |

| Event | Metrics | Hits | Misses | Ungraded |
|---|---|---|---|---|
| Ever Given / Suez, Mar 2021 | EG-1…EG-3 | 1 | 1 | 1 |
| Baltimore / Key Bridge, Mar–Jun 2024 | BA-1…BA-3 | 1 | 2 | 0 |
| Red Sea window, Dec 2023–Feb 2024 | RS-1…RS-2 | 1 | 1 | 0 |

## Ever Given / Suez Canal — closure at `chk_suez`, 2021-03-23T05:40Z, 6.31 d

Shock from documented facts only: grounded 2021-03-23 05:40 UTC (BBC via
Sentinel Vision EVT-839), refloated 2021-03-29 13:05 UTC (Boskalis press
release). Actuals: `ever_given/actuals.json`; PortWatch series:
`ever_given/portwatch_suez_2021.json`.

| ID | Metric | Sim | Observed | Band | Grade |
|---|---|---|---|---|---|
| EG-1 | Excess peak queue at Suez, in days of normal corridor flow | **0.55 d** (excess peak 9 vessels ÷ 16.5 corridor departures/day) | **7.42 d** (422 vessels, SCA via Reuters, ÷ PortWatch baseline 56.91 transits/day) | ratio ∈ [0.5, 2] (ratio 0.07) | **MISS** |
| EG-2 | Mean added delay at `chk_suez` (twin statistic) | 2.33 d | — | — | **UNGRADED** (average delay per queued vessel could not be sourced; only forecasts and anecdotes exist — never estimated) |
| EG-3 | Clearance days after reopening (queue back to twin level, sustained 3 ticks) | **9.45 d** | **5.0 d** (backlog fully cleared 2021-04-03, Reuters/SCA; reopening 2021-03-29) | ratio ∈ [0.5, 2] (ratio 1.89) | **HIT** |

Printed, ungraded: raw queue peaks — disrupted 24, twin 15 (scale caveat
above applies; the sim's absolute queue is not the real fleet's). Divert
segments during the run: 52 (all avoiding `chk_suez`). Hold arrivals at
`chk_suez`: 9. Reality, for contrast: fewer than 20 ships diverted in total
(Linerlytica retrospective) and 422 waited — the real fleet overwhelmingly
waited; the sim's traffic predominantly diverted at planning time or was
never in-window.

## Baltimore / Key Bridge — closure at `sea_baltimore`, 2024-03-26T05:30Z, 75.77 d

Phases are NOT modelled (registered choice): the shipped vocabulary
applies one capacity-loss level per step window, so the run is a full
closure to the documented full-reopening date, 2024-06-10 (USACE). The
real closure leaked — Tradepoint Atlantic stayed reachable outside the
bridge and the phased channels carried real traffic from late April — so
the observed call drop is −60%, not ~−100%; that asymmetry is in the
registered design, not discovered after the fact. Actuals:
`baltimore/actuals.json` (+ `baltimore/supplement/`, a second set of
sources, consistent on every graded figure); PortWatch series:
`baltimore/portwatch_baltimore_2024.json` (port103) and
`baltimore/portwatch_virginia_2024.json` (port826 "Norfolk").

| ID | Metric | Sim | Observed | Band | Grade |
|---|---|---|---|---|---|
| BA-1 | Port-call drop during closure (berth calls, disrupted vs twin) | **−100.0%** (0.00 vs 0.39 calls/day) | **−60.06%** (5.375 → 2.147 calls/day, PortWatch) | \|diff\| ≤ 20 pp (diff 39.9 pp) | **MISS** |
| BA-2 | Diversion uplift at Virginia (berth calls % change vs twin) | **−14.9%** (2.20 vs 2.59 calls/day) | **+2.07%** (3.775 → 3.853 calls/day, PortWatch) | \|diff\| ≤ 20 pp (diff 17.0 pp) | **HIT** |
| BA-3 | Recovery days after full reopening (berth calls ≥ 90% of twin, sustained 3 d) | **0 d** (full service the day the window ends) | **13 d** (recovery date 2024-06-23, PortWatch 7-day trailing mean ≥ 90% of baseline) | ratio ∈ [0.5, 2] (ratio 0.0) | **MISS** |

Printed, ungraded: queue peak at `sea_baltimore` — 0 in both runs. The
registered expectation (arriving vessels queue at the closed port) did
not occur: no queue ever formed at the port itself. The delay appears
instead as a mean added delay of **+28.35 d** on the port's 32 matched
arrivals, realized upstream and in schedule slip, and zero divert
segments — the shipped model has no pre-departure diversion for a
destination closure, and no mechanism that moves calls to Virginia
(BA-2's in-band grade is magnitude luck: the sim's Virginia calls *fell*
on knock-on effects; the observed call uplift was near zero because the
real diversion showed up in cargo tonnage, +28.6% imports, not calls).

## Red Sea crisis window — closure at `chk_babelmandeb`, 2023-12-15, 76 d

Carrier suspensions of Red Sea transits began 2023-12-15/16 (Maersk,
Hapag-Lloyd, MSC, CMA CGM — Reuters). In the sim's reaction vocabulary a
suspension is a behavioural closure (a partial capacity loss only slows
approaches in the shipped model and can never divert), so the shock is
registered as a full closure — deliberately diverging from the chain
replay's capacity_cut 0.7 in the chain engine's capacity vocabulary; the
cross-check below compares the two as found. Metric window:
2024-01-01 → 2024-02-29. Actuals:
`red_sea/actuals.json`; PortWatch series: `red_sea/portwatch_suez_redsea.json`,
`red_sea/portwatch_babelmandeb_redsea.json`.

| ID | Metric | Sim | Observed | Band | Grade |
|---|---|---|---|---|---|
| RS-1 | Suez transit drop (completed non-diverted arrivals on Suez-transiting lanes, disrupted vs twin) | **0.0%** (1,075 vs 1,075 completions) | **−40.84%** (76.57 → 45.30 transits/day, PortWatch) | \|diff\| ≤ 20 pp (diff 40.8 pp) | **MISS** |
| RS-2 | Mean lateness of arrivals on Bab-el-Mandeb-transiting lanes (occurrence-matched vs twin) | **7.66 d** (22 matched pairs) | **7–12 d** published Cape added time (World Bank 7–10; UNCTAD 12; IMF "10 or more") | sim ∈ [3.5, 24] | **HIT** |

Printed, ungraded (decomposition of RS-1): the exposed population is
tiny in the modelled lane census — 25 corridor legs departed on
Bab-el-Mandeb-transiting lanes inside the window, of which 26 divert
segments (avoided `chk_babelmandeb`) and 2 hold arrivals account for
essentially all of it: the exposed traffic *did* divert, almost
completely, exactly as a closure implies. But those legs feed only
~2% of the 1,075 Suez-lane completions (the rest are regional lanes
that transit Suez without Bab el-Mandeb), so the Suez-level completion
count does not move. Observed counterpart for the exposed strait
itself: Bab el-Mandeb transits fell **−51.83%** (77.33 → 37.25/day,
PortWatch). Mean added delay at `chk_babelmandeb`: +16.74 d (55 matched
arrivals); at `chk_suez`: 0.0 d (497 matched arrivals) — diverted
traffic does not arrive late at Suez; it arrives late at its
destinations (RS-2).

## Cross-check vs the chain backtest (same shock family, not graded)

The chain replay `red_sea_2024` (capacity_cut 0.7 at `chk_babelmandeb` from
2023-11-19, chain-engine vocabulary) published: node coverage 4/4, delay
grading 2 hits / 3 graded, mean |error| 8.33 d, 20 false positives, 1
genuine miss. Its graded delay steps put the observed Cape-reroute figure
of 10.0 d against predictions at the nodes where its chain says delay
accumulates: `chk_capeofgoodhope` 7.5 d predicted (hit, twice) and
`sea_rotterdam` 30.0 d predicted (miss — a queue backlog propagated from
Colombo; the chain scorecard itself flags the channel mismatch).

The SIM's delay picture on the same shock family: pooled lateness of
arrivals on Bab-el-Mandeb-transiting lanes is 7.66 d — inside the
published 7–12 d Cape band (RS-2, hit) and close to the chain engine's Cape-node
figure (7.5 d predicted vs 10.0 d observed). **Agreement**: both engines,
independently, place the reroute's delay magnitude at roughly 7–17 d
against an observed ~10 d (the SIM adds +16.74 d mean added delay on
endpoint arrivals at the blocked strait itself). **Divergence**: the
engines disagree about where delay lives. The chain engine propagates delay into
destination-port backlogs — including a 63.4 d predicted delay at
`chk_suez` on unquantified steps and the 30 d Rotterdam over-prediction.
The SIM shows 0.0 d added delay at `chk_suez` and at `sea_rotterdam`:
in the traffic model, delay lives in transit on the diverted legs and at
the blocked node, not in destination queues. On flow, the two are not
comparable — the chain engine does not measure flow at all — and the SIM's flow
response at Suez (0.0% vs observed −40.84%, RS-1) is this backtest's
own miss, for the lane-census reason decomposed above.

## Methodology (as registered)

Runs: shipped constants only; seed 42; day ticks; weather off (no
historical weather snapshot exists in this system for these windows);
every run paired with its undisrupted twin (same derivation, empty
disruption set). Extraction definitions are those of
`preregistration.md`, implemented verbatim by `run_sim_backtest.py`;
raw extractions in `results/`. Grades and every observed comparator
recompute from the stored raw results and raw PortWatch series
(`build_scorecard.py` computes them from those files).
No sim constant, threshold, or config was adjusted after the
pre-registration.
