# SIM HISTORICAL BACKTEST — PRE-REGISTRATION

Registered **before any simulation run for these events was executed**. The
run artifacts under `results/` and the scorecard (`scorecard.json` /
`scorecard.md`) are recorded after this document.
Run configurations are pinned in `run_configs.json` (this directory).

This scorecard grades the **SIM layer** (traffic, queues, diversions).
It is a separate artifact from the causal-chain scorecard
(`data/replay/scorecard.*`), which grades the chain engine. The two are
never merged, pooled, or averaged.

## Standing rules (fixed in advance)

- **Engine**: the shipped SIM. No sim constant, threshold,
  or configuration may be adjusted after this registration to improve a
  grade. If a metric misses, it publishes as a miss with the delta printed.
- **Run discipline**: seed 42, day ticks, weather OFF (`None`) — the shipped
  demo-run discipline; no historical weather snapshot exists in this
  system for 2021/2024 windows. Every run is paired with its undisrupted
  **twin** (same graph, same derivation, empty disruption set).
- **Scale caveat (printed on every queue/flow figure)**: the sim's traffic
  is modelled from lane structure on the current (2026) graph, not the
  real historical fleet. Graded metrics are therefore shape/ratio-first;
  absolute sim counts are printed for the record, never graded raw.
- **Grading bands** (mirroring the chain scorecard's window convention):
  - Ratio metric (days, counts-of-flow): **HIT iff sim/observed ∈ [0.5, 2]**.
  - Observed **range** [a, b]: **HIT iff sim ∈ [a/2, 2b]**.
  - Share/drop metric (percentage points): **HIT iff |sim − observed| ≤ 20 pp**.
  - An actual that could not be sourced makes its metric **UNGRADED** —
    never a hit, never a miss, never replaced by an estimate. Counted and
    listed, per the chain scorecard's evidence rule.
- **Extraction definitions** (the exact outputs, fixed here; the runner in
  `run_sim_backtest.py` implements these verbatim and nothing else):
  - *Queue series*: `node_series[node]["queue"]` per day tick.
  - *Excess peak queue*: max over the run of disrupted queue at the blocked
    node minus max twin queue at the same node over the same run.
  - *Corridor lanes* for node X: derivation lanes whose transit path
    (`derivation["paths"][lane]["transits"]`) includes X.
  - *Corridor departures per day (twin)*: `depart` segments on corridor
    lanes in the twin ÷ run days.
  - *Mean added delay at a node*: `added_delay_by_node(disrupted, twin)`
    (occurrence-matched leg arrivals; hold arrivals counted separately,
    never averaged in — the shipped rule).
  - *Berth calls at a port*: segments of kind `berth` at the node, per day
    (a vessel taking a berth = a port call served).
  - *Clearance*: first day-tick after the disruption window's end at which
    disrupted queue ≤ twin queue at the same tick, sustained for 3
    consecutive ticks; clearance days = that tick's date − window end date.
  - *Recovery (Baltimore)*: first day after the window end whose berth-call
    count at the port is ≥ 90% of the twin's same-day count, sustained
    3 consecutive days; recovery days = that date − window end date.
  - *Corridor arrival lateness (Red Sea M2)*: arrivals at destinations of
    Bab-el-Mandeb-transiting lanes, occurrence-matched vs twin (the
    `_suez_lane_stats` method of the shipped demo run, generalized):
    mean lateness over arrivals inside the metric window.

## Event 1 — Ever Given / Suez Canal

**Shock config** (`run_configs.json`): closure, `chk_suez`,
capacity_loss 1.0, started 2021-03-23T05:40:00Z (grounding, BBC via
Sentinel Vision EVT-839), duration 6.31 d (to refloat 2021-03-29T13:05Z,
Boskalis press release 29 Mar 2021). Run: start 2021-03-20T00:00Z, 30 day
ticks.

**Observed actuals** (`ever_given/actuals.json`; PortWatch series
`ever_given/portwatch_suez_2021.json`, IMF PortWatch Daily_Chokepoints_Data,
retrieved 2026-10-21):
- Peak queue: **422 vessels** waiting to transit (Suez Canal Authority, via
  Reuters, 3 Apr 2021). An alternate report of 425 (Business Daily/AFP) is
  recorded in the actuals; grading uses the SCA figure.
- PortWatch baseline transits: **56.91/day** (mean n_total, 2021-03-01→22,
  recomputed from the stored rows: sum 1252 over 22 days).
- Backlog fully cleared: **2021-04-03** (Reuters, reporting SCA).
- Average delay per queued vessel: **NOT SOURCED** (searched; only
  forecasts and per-vessel anecdotes exist) → metric EG-2 is UNGRADED.

**Metrics**:
- **EG-1 — Excess peak queue, in days of flow.** Sim: excess peak queue at
  `chk_suez` ÷ twin corridor departures/day (Suez-transiting lanes).
  Observed: 422 ÷ 56.91 = **7.42 days of flow**. Ratio band [0.5, 2].
- **EG-2 — Mean added delay at `chk_suez`** (sim twin statistic).
  **UNGRADED** — no sourced observed average exists.
- **EG-3 — Clearance days after reopening.** Sim: clearance (definition
  above) from window end 2021-03-29. Observed: cleared 2021-04-03 −
  reopening 2021-03-29 = **5.0 days** (date granularity, stated). Ratio
  band [0.5, 2].
- Printed ungraded: raw disrupted/twin queue peaks (scale caveat), divert
  segment count, hold arrivals at `chk_suez`, sim mean added delay
  (EG-2's sim value, printed without a grade).

## Event 2 — Baltimore / Key Bridge

**Shock config**: closure, `sea_baltimore`, capacity_loss 1.0, started
2024-03-26T05:30:00Z (collapse ~01:28 EDT), duration 75.77 d — full
closure to the start of the documented full-reopening day, 2024-06-10
(USACE: full Fort McHenry channel restored). **Phases are NOT modelled**:
the shipped vocabulary applies one loss level per step window; stacking
adjacent windows at one node to fake the phase curve would compose
berth-scaling in ways the shipped reaction model does not document. This
choice and its expected direction of error (the sim restores full service
on 10 Jun; the real port's *calls* recovered over the following weeks)
are registered here, before the run. Run: start 2024-03-24T00:00Z, 110 day
ticks.

**Observed actuals** (`baltimore/actuals.json`; PortWatch Daily_Ports_Data
series for Baltimore (port103) and Virginia (port826 "Norfolk"), retrieved
2026-10-21, **recomputed from the stored rows** — an earlier
~95% drop figure did not survive recomputation and is not used):
- Baltimore calls: baseline (2024-02-15→03-25) **5.375/day** → closure
  window (2024-03-27→06-09) **2.147/day** = **−60.06%**. The closure mean
  is not ~0: Tradepoint Atlantic stayed reachable outside the bridge and
  the phased channels carried real traffic from late April.
- Virginia calls: baseline **3.775/day** → closure **3.853/day** =
  **+2.07%** (call counts flat; the documented diversion uplift is in
  cargo volume, not calls — BA-2 grades the call-count figure,
  like-for-like with what the sim counts).
- Baltimore recovery date (first date after 2024-06-10 whose 7-day
  trailing mean ≥ 90% of baseline): **2024-06-23** → lag **13 days**.

**Metrics**:
- **BA-1 — Port-call drop during closure.** Sim: berth calls at
  `sea_baltimore`, closure-window mean vs twin same-window mean → drop %.
  Observed: PortWatch Baltimore drop %. Band ±20 pp.
- **BA-2 — Diversion uplift at Virginia.** Sim: berth calls at
  `sea_virginia`, closure-window disrupted vs twin → % change. Observed:
  PortWatch Virginia % change. Band ±20 pp.
- **BA-3 — Recovery days after full reopening.** Sim: recovery
  (definition above) from window end 2024-06-10. Observed: PortWatch
  recovery date − 2024-06-10, in days. Ratio band [0.5, 2].
- Printed ungraded: queue peak at `sea_baltimore` — with the structural
  note, registered in advance: in the shipped model a destination closure
  queues arriving vessels at the closed port, while the real fleet
  largely diverted before sailing; no pre-departure diversion mechanism
  for destination closures is shipped. The queue figure is printed for
  the record and the findings note, never graded.

## Event 3 — Red Sea crisis window

**Shock config**: closure, `chk_babelmandeb`, capacity_loss 1.0, started
2023-12-15T00:00:00Z, duration 76.0 d (to 2024-02-29). Basis: carrier
suspensions of Red Sea transits began 2023-12-15/16 (Maersk, Hapag-Lloyd,
MSC, CMA CGM — Reuters). In the sim's reaction vocabulary a suspension is
a behavioural closure — partial capacity_loss only slows approaches in
the shipped model and can never produce a diversion, while the documented
behaviour was diversion. **Deliberate divergence from the chain replay's
config** (capacity_cut 0.7 in the chain engine's capacity vocabulary,
sourced from the UNCTAD gross-tonnage fall): each engine is fed the shock
in its own vocabulary; the scorecard's cross-check paragraph compares
the two delay pictures as found. Run: start 2023-12-01T00:00Z, 100 day
ticks.

**Observed actuals** (`red_sea/actuals.json`; PortWatch
Daily_Chokepoints_Data series for Suez and Bab el-Mandeb, retrieved
2026-10-21, recomputed from the stored rows):
- Suez transits: Nov 2023 baseline **76.57/day** → Jan–Feb 2024
  **45.30/day** = **−40.84%**.
- Bab el-Mandeb transits: Nov 2023 **77.33/day** → Jan–Feb 2024
  **37.25/day** = **−51.83%**.
- Cape of Good Hope added transit time, published range: **7–12 days**
  (World Bank 7–10 d; UNCTAD 12 d; IMF "10 or more days").

**Metrics** (metric window 2024-01-01→2024-02-29):
- **RS-1 — Suez transit drop.** Sim: completed destination arrivals on
  Suez-transiting lanes inside the window, disrupted vs twin → drop %.
  An arrival segment flagged diverted with avoided ∈ {`chk_babelmandeb`,
  `chk_suez`} is excluded — that vessel demonstrably did not transit
  Suez; all other arrivals on those lanes count. Observed: **−40.84%**
  (PortWatch, above). Band ±20 pp.
- **RS-2 — Corridor arrival lateness.** Sim: mean lateness of arrivals at
  destinations of Bab-el-Mandeb-transiting lanes inside the window
  (occurrence-matched vs twin). Observed: published Cape added-time range
  **[7, 12] days**. Range band [3.5, 24].
- Printed ungraded (decomposition of RS-1): divert segments with
  avoided ∈ {`chk_babelmandeb`, `chk_suez`}, hold arrivals at
  `chk_babelmandeb`, corridor legs departed in window, sim mean added
  delay at `chk_suez` and `chk_babelmandeb`.

## Cross-check (written into the scorecard, not graded)

A short paragraph comparing the sim's Red Sea delay picture (RS-2 and the
printed delay statistics) with the chain backtest's published red_sea_2024
claims (node coverage 4/4; delay hits 2/3; mean |error| 8.33 d; the
per-node observed-vs-predicted table in
`data/replay/scorecard.md`) — agreement or divergence printed as
found, including the vocabulary divergence registered above.
