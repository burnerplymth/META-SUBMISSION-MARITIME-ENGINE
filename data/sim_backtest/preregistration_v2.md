# SIM Historical Backtest — Pre-registration (extended run)

Registered **before any extended run and before any behaviour change**. This
document, the two held-out event records (`panama/`, `la_lb/`), and
`run_configs_v2.json` are registered together, before the extended
implementation. Rules carried over from the baseline run unchanged: day ticks, seed 42,
weather OFF, twin-paired extraction, bands — ratio metrics HIT iff
sim/observed in [0.5, 2]; observed range [a, b] HIT iff sim in
[a/2, 2b]; share/drop metrics HIT iff |sim − observed| ≤ 20 pp; an
actual that could not be sourced is UNGRADED, never estimated.

## 1. What the extended run is, and what it is not

The baseline run (pre-registration `preregistration.md`, scorecard in
`scorecard.md` / `scorecard.json`) graded 8 metrics: 3 HIT, 4 MISS,
1 UNGRADED. The extended run adds four SIM mechanisms —
wait-vs-divert, stepped shocks with a recovery ramp,
call reassignment and exposed-subpopulation grading — and audits
the lane-census through-share.

- **Baseline grades are never recomputed or overwritten.** `scorecard.md`,
  `scorecard.json`, `preregistration.md`, `run_configs.json`,
  `build_scorecard.py`, `run_sim_backtest.py` and `results/` stay
  unchanged. The extended run publishes as NEW files beside
  them (`scorecard_v2.*`, `results_v2/`).
- **The three original events are graded on their ORIGINAL baseline
  metrics, definitions and bands** (§3), extracted by the same
  definitions as the baseline run (reused from `run_sim_backtest.py`).
  The extended run changes the SIM's behaviour and the event CONFIGS
  (§2), never the yardsticks.
- **One metric is added** — RS-3 (§4), the exposed-subpopulation flow
  metric, labelled an extended-run addition wherever
  it prints. It does not enter the baseline-comparable aggregate; the
  extended run prints
  two aggregates: the original-8 aggregate (comparable to the baseline run) and
  the held-out aggregate (RS-3 listed separately from it).
- **Two held-out validation events** (§5) — the Panama Canal drought
  restrictions (2023–24) and the LA/LB congestion (2021) — are
  registered together with their event records, BEFORE any
  extended mechanism was implemented. They were not used to
  design or tune any mechanism (the mechanism specification
  predates these records and never mentions either event)
  and are run **once**, at the end, on the final code.
- **No second tuning round.** If a mechanism does not improve its
  target metric, or degrades another, the scorecard prints it as
  found.

## 2. Extended-run event configs (documented facts only)

Full machine-readable configs: `run_configs_v2.json`. Every field
below traces to an event record or a cited documented fact; no field is
fitted to a graded comparator.

### Ever Given — config UNCHANGED from the baseline run

Full closure of `chk_suez`, 2021-03-23T05:40Z, 6.31 d (SCA via
Reuters; record `ever_given/`). The change for this event is the
wait-vs-divert mechanism itself, not the config: **no `expected_duration_d`
override** — the fleet's expectation defaults to the declared window,
and the documented fact is that the closure was short and publicly
bounded (refloated 29 Mar; a salvage operation with a days-scale
horizon, not an open-ended suspension). Run window and ticks as the baseline run
(start 2021-03-20T00:00Z, 30 ticks).

### Red Sea — one documented config field added

Closure of `chk_babelmandeb` from 2023-12-15T00:00Z, 76 d, exactly
as the baseline run, plus **`expected_duration_d: null`** (open-ended). Documented
basis: the major carriers suspended Red Sea transits "until further
notice" / until security conditions changed (record `red_sea/`:
the Maersk, MSC, CMA CGM and Hapag-Lloyd suspensions of 15–16 Dec
2023 were open-ended; the window's end remains an observation
boundary, not an announced reopening). Under wait-vs-divert, an open-ended
expectation means the wait is treated as unbounded, so a qualifying
bypass is taken — the baseline behaviour for this event, now reached
through the rule rather than by default. Run window as the baseline run (start
2023-12-01T00:00Z, 100 ticks).

### Baltimore — expressed in the stepped-shock form

The baseline registration recorded that the single hard closure was
a vocabulary limitation ("the stepped middle is real; the flat form
overstates the closure's hermeticity"). The extended run expresses the SAME event
as adjacent steps at `sea_baltimore` from 2024-03-26T05:30Z, with
loss levels derived from the documented channel-capability record in
`baltimore/actuals.json` (USACE / Unified Command / USCG
statements) — **not** from the graded PortWatch call series:

| Step | Span (UTC) | Loss | Documented basis |
|---|---|---|---|
| 1 | Mar 26 05:30 → Apr 1 00:00 | 1.00 | Vessel traffic suspended (MPA, Mar 26); no alternate channel yet existed. |
| 2 | Apr 1 → Apr 19 | 0.85 | Shallow alternate channels only (Sollers Point 11 ft, Apr 1/2; Hawkins Point 14 ft, Apr 2) — capability below the ~15% anchor documented for the next phase; the step adopts that anchor as its ceiling. |
| 3 | Apr 19 → Apr 25 | 0.85 | Fort Carroll channel (20 ft) opens Apr 19; Unified Command estimate: ~15% of pre-collapse commercial activity → loss 0.85. |
| 4 | Apr 25 → Apr 29 06:00 | 0.25 | Fort McHenry Limited Access Channel (300 ft × 35 ft); USCG estimate ~75% of typical vessel types could use it → loss 0.25. |
| 5 | Apr 29 06:00 → May 20 | 0.85 | Limited Access Channel closed again Apr 29 to focus on *Dali* removal; shallow channels remain (≈ step 2/3 capability). |
| 6 | May 20 → Jun 10 | 0.00 | Federal Channel cleared to 400 ft × 50 ft on May 20, "permitting all pre-collapse, deep-draft commercial vessels to transit" (USACE). Full 700 ft restoration Jun 10 ends the event. Mechanically inert; it fixes the event span (BA-1's window, BA-3's origin, reassignment's span) to the documented full-reopening date. |

Judgements declared: step 2's level adopts step 3's documented
anchor as a ceiling (the record quantifies no shallower phase);
step 6's 0.00 follows the USACE statement verbatim (the residual
width constraint is not quantified in any source found). Tradepoint
Atlantic's throughout-operation is documented qualitatively only;
no step level is adjusted for it — the residual shows in BA-1's
decomposition, not in the config.

**Recovery ramp: NOT applied to Baltimore.** The ramp mechanism
is part of the extended run, but no non-circular documented basis for its
length exists in the record: the observed 13-day recovery is itself
BA-3's graded comparator, and the record documents no independent
restoration schedule. BA-3 in the extended run therefore tests the stepped form
alone, and the scorecard prints this reasoning with the grade.

**Call reassignment** is configured on the same event:
substitutes with shares from the documented Project44 container
split (via AJOT, 22 Apr 2024; `baltimore/actuals.json`):
`sea_virginia` 0.43, `sea_newyork` 0.26, `sea_wilmingtonde` 0.13 —
total reassigned share 0.82 of the closed place's lanes. The
Project44 "Newark-Elizabeth 10%" line is NOT configured separately:
the record itself notes that Newark-Elizabeth is part of the
NY/NJ complex (`sea_newyork`), so counting it would double-count.
The shares are container shares applied to lanes — the sim has no
container/vessel distinction; the mismatch is declared here and in
this document, and reassignment's accounting reports calls AND
modelled tonnage (lane capacity share × the closed place's daily
capacity per reassigned arrival) so the tonnage basis prints beside
the call basis. Reassignment is active for the event's full span
(first step start → last step end), per the call-reassignment rule: carriers
re-pointed services for the event's duration, not only while the
channel was fully closed.

Run window as the baseline run (start 2024-03-24T00:00Z, 110 ticks).

## 3. The original metrics (verbatim from the baseline run — unchanged)

EG-1, EG-2, EG-3, BA-1, BA-2, BA-3, RS-1, RS-2 with the exact
extraction definitions and bands of `preregistration.md` §§2–4.
Observed comparators are recomputed from the same stored raw
PortWatch series as the baseline run (they cannot move; they equal the
baseline run's printed values).

## 4. RS-3 — the exposed-subpopulation addition (extended run only)

**RS-3 — Exposed-subpopulation Suez flow change.** Sim: completed
non-diverted arrivals during the baseline metric window
(2024-01-01T00:00Z ≤ t < 2024-03-01T00:00Z) on lanes transiting BOTH
`chk_babelmandeb` AND `chk_suez` (the exposed sub-population of the
measured node's traffic), disrupted vs twin, as a % change — the
RS-1 extraction restricted to the both-node lane set. Observed: the
exposed strait's own transit change over the same windows —
PortWatch Bab el-Mandeb `n_total` mean, Jan–Feb 2024 vs Nov 2023 =
**−51.83%** (from the stored `red_sea/portwatch_suez_redsea.json`,
as printed in the baseline decomposition). Band: |diff| ≤ 20 pp, as RS-1.
**Registered caveat:** the census audit (`census_audit.json`) establishes whether
the both-node lane set is non-empty in the modelled census; if it is
empty, RS-3 prints UNDEFINED — NO EXPOSED LANES IN CENSUS with the
audit cross-reference, and is excluded from the aggregates. That
outcome is a result, not an error. A companion decomposition
(completions change on Bem-transiting lanes alone and on
Suez-transiting lanes alone) prints ungraded either way.

## 5. Held-out validation events

Registered here in full from the event records `panama/actuals.json` and
`la_lb/actuals.json`. Neither
event informed any mechanism decision in the extended run; each registered
run executes **once**, on the final code, after the three original
events.

### 5.1 Panama Canal drought restrictions (2023–24)

Shock translation (arithmetic printed here):
`capacity_loss = 1 − slots_restricted / slots_normal`,
`slots_normal = 36/day` (ACP's normal-operations figure). The
announced-but-never-implemented 20/day and 18/day levels (Advisory
A-48-2023, replaced by A-54-2023 before taking effect) are excluded
— the implemented floor was **22 slots/day** → loss **0.389**. The
May 2024 maintenance dip (phase P7) is not drought and is
not configured. Draft restrictions are outside the sim's vocabulary
and are not folded into the loss (stated plainly in the record).

**Registered runs (two, each once):**

- **Primary** — start 2023-11-28T00:00Z, 110 day ticks; single flat
  window at `chk_panama`: kind `capacity_cut`, loss 0.389, from
  2023-12-01T00:00Z, duration 46 d (the implemented slot floor,
  through 2024-01-15).
- **August phase run** — start 2023-07-27T00:00Z, 45 day ticks;
  single flat window at `chk_panama`: kind `capacity_cut`, loss
  0.111 (phase P1, 32 slots), from 2023-07-30T00:00Z, duration 45 d.
  Registered because PA-2's observed queue peak occurred under this
  phase (caveat in the record): grading the December run's queue
  against an August observation would bake a phase mismatch into a
  held-out grade. The record proposed this run as optional; this
  registration adopts it as PA-2's graded run and prints the
  primary run's queue figure ungraded beside it.

**Metrics:**

- **PA-1 — Transit drop %, tightest phase (share band).** Sim:
  completed non-diverted arrivals on `chk_panama`-transiting lanes
  with arrival time in [2023-12-01, 2024-01-16), disrupted vs twin →
  % change (the RS-1 construction at this node). Observed: PortWatch
  Panama Canal `n_total` mean 21.11/day (2023-12-01 → 2024-01-15)
  vs baseline 32.11/day (2023-01-01 → 2023-07-29) = **−34.26%**
  (recomputed from the stored rows). Band: |diff| ≤ 20 pp.
- **PA-2 — Excess peak queue, days of flow (ratio band; August
  run).** Sim: (max disrupted queue − max twin queue at
  `chk_panama` over the August run) ÷ the same run's twin corridor
  departures/day on Panama-transiting lanes. Observed: excess peak
  queue 163 − 90 = **73 vessels** (FreightWaves from ACP data: the
  9 Aug 2023 queue high minus the normal-period average queue) ÷
  32.11 baseline transits/day = **2.27 days of flow**. Band: ratio
  in [0.5, 2]. The primary run's same-construction figure prints
  ungraded.
- **PA-3 — Mean added delay at `chk_panama` (range band; primary
  run).** Sim: the `added_delay_by_node` statistic at `chk_panama`
  (hold arrivals counted separately, never averaged in). Observed:
  the published waiting-time forecast range **[5, 15] days**
  (Waterfront Maritime Services, 7 Nov 2023). Band: sim
  in [2.5, 30]. Basis printed in advance: the
  comparator is a dated published forecast range for arriving
  vessels, not a measured all-vessel mean (none exists); the baseline RS-2 graded against published ranges the same way.
  The measured non-booked average peaks (11.4 d SB / 15.2 d NB) sit
  at/inside the range's top; booked vessels waited far less.

Printed ungraded (primary run): raw queue peaks, divert segments,
hold arrivals at `chk_panama`, clearance days after 2024-01-16 (no
observed comparator proposed — the real canal stepped UP to a
still-restricted 24/day rather than reopening).

### 5.2 LA/LB congestion (2021)

The ports never closed; calls and tonnage barely moved (PortWatch:
total calls **+7.68%**, container calls +3.66%, all-class import
tonnage −1.19%, peak window vs baseline). The event
lives in the MXSOC queue. Shock: at
**`sea_losangeles` only** — that node carries the combined
"Los Angeles-Long Beach" complex (PortWatch port664) in the graph's
data model, and every observed comparator is a combined-complex
quantity; `sea_longbeach` is NOT shocked (no observed basis exists
for an LB-specific level, and shocking both would apply one
complex's deficit twice — the two-node limitation is stated here
in advance). Kind `congestion`, single flat window, loss
**0.20**, from 2021-10-03T00:00Z, duration 98 d (ends at the
documented peak day, 2022-01-09). The 0.20 is a *window-compressed
equivalent*, derived in the record (Derivation A): the sourced sustained
net deficit is ≈ **0.08** of flow (Derivation A); the balance is the
pre-October queue build (≈12.07 days of container flow inherited by
October) compressed into the 98-day window so the run can reach the
peak's 19.94 days of flow from a zero start. It is not a claim that
real capacity fell 20%, and the scorecard prints both numbers. Run:
start 2021-10-01T00:00Z, 110 day ticks (ends 2022-01-19).

**Metrics:**

- **LB-1 — Excess peak queue, days of flow (ratio band).** Sim:
  (max disrupted queue − max twin queue at `sea_losangeles`) ÷ twin
  berth-call rate at `sea_losangeles` (berth segments/day, same
  run). Observed: MXSOC peak backup **109** container ships
  (2022-01-09) ÷ PortWatch baseline container call rate **5.467/day**
  (port664, 2021-01-01 → 2021-03-31) = **19.94 days of flow**. Band:
  ratio in [0.5, 2]. Registered caveats: the sim queue
  counts all modelled vessel classes at the node, the MXSOC backup
  counts container ships only — the ratio form is what absorbs this;
  the baseline quarter is itself early-congestion (no uncongested
  quarter exists inside the stored window). The MXSOC counting rule
  changed mid-series on 2021-11-16 (SAQA/CTA) — the record's note
  on this is inherited by this metric.
- **LB-2 — Port-call change, peak window (share band).** Sim: berth
  calls at `sea_losangeles` on run ticks in the peak window
  (2021-10-01 → 2022-01-31, truncated at the run end 2022-01-19),
  disrupted mean vs twin mean → % change. Observed: PortWatch
  port664 total port calls, peak window 10.984/day vs baseline
  10.200/day = **+7.68%**. Band: |diff| ≤ 20 pp. Registered
  expectation (stated in advance): the honest observed
  comparator is a RISE; a berth-scaling loss mechanically reduces
  sim calls — a sim change below −12.32% publishes as a MISS, the
  quantitative form of this event's central lesson.
- **LB-3 — Clearance days. UNGRADED** (registered, per the standing
  rule). The observed emptying has no capacity-restoration date
  (demand-side decline over most of 2022), so no
  like-for-like observed comparator exists for the sim's
  window-end-plus-clearance construction. The sim clearance value
  prints ungraded; the observed milestones from the peak
  (232 d to ≤10% of peak, 283 d to 4 ships, 317 d to the declared
  end) are documented in the record for a future long-window
  registration.

Printed ungraded: raw queue peaks at both complex nodes, sim mean
added delay at `sea_losangeles`, berth-call change at
`sea_longbeach` (≈ 0 by construction — printed so the unshocked-node
artefact is on the record).

## 6. Execution and checking (extended run)

`run_sim_backtest_v2.py`
runs the registered events twin-paired on the final code and stores
raw extractions under `results_v2/`, including per-event behaviour
counts (divert / hold segments) and Baltimore's reassignment
accounting. `build_scorecard_v2.py` builds `scorecard_v2.json` /
`scorecard_v2.md` from the stored raws + stored series, so every
extended-run grade is recomputable from those files; the shared observed
comparators equal the baseline run's printed values. The scorecard prints,
per metric: baseline sim → extended sim, observed, baseline grade → extended grade; held-out
grades at the same prominence; and the census-audit verdict.
