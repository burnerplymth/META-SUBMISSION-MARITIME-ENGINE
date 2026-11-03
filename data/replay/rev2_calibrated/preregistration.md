# Pre-registration: BACKTEST ENGINE REV 2 CALIBRATED

Written BEFORE the scorecard in this directory was generated (no calibrated engine
was run on a replay episode before this file was written). Design requirement: the simulator is a causal cause-and-effect
world model with formulaic plug-ins only where real data is not available, each
labelled.

## What is run

* Engine under test: ENGINE REV 2 with the CALIBRATION PROFILE `rev2c` (new,
  opt-in; the default rev-2 engine, its plug-in registry hash and its
  scorecards are untouched).
* PRIMARY configuration (the headline): `rev2c` + the routes table from the graph
  services sidecar with the SAME selection as `data/replay/rev2_services/`
  (eras `pre_red_sea_crisis`, `include_unverified: false`,
  `include_unstated: false`). SECONDARY configuration (reported beside it, same
  protocol): `rev2c` without routes. Each replay episode is run ONCE per
  configuration; both are published whatever they say, including if worse than
  rev 2, rev 2 + services, or the naive baseline.
* Datasets: `red_sea_2024.json`, `panama_2023_24.json`, `la_2021.json`,
  byte-untouched. Protocol, grading rules and every metric are engine v1's
  (`engine.scorecard.build_scorecard` imported, not copied). The naive k-hop
  baseline runs on the ORIGINAL core graph (same as v1, rev 2, rev 2 + services).

## What the profile changes (fixed here; nothing is tuned after a replay number)

1. Correction, delay of an `inventory` event. In rev 2, `delay_d` = length of the run of
   steps in which onward supply stayed short by >= the hit threshold (+ queue
   wait). That is the persistence of a shortfall, not how late cargo is: any
   sustained cut returns about the cut length whatever its size (Panama, 215 d
   half cut: 173 d at New York, 192.5 d at Colon). In this profile: the peak of the cumulative
   deficit in days, `max_t D(t) / lambda0` with `D(t) = integral of (lambda0 -
   arrivals)`: the horizontal distance between the baseline and the actual
   cumulative inflow curves, the same kind of quantity as the FIFO wait used for
   queue events.
2. PLUG-IN REPLACED BY DATA, peak service rate `mu_max`. Old: 1.3 x lambda0 at
   every place (TEMPORARY). New: per place, `mu_max = ratio x lambda0` with
   `ratio = P99 / median` of the 7-day trailing mean of IMF PortWatch daily counts
   over the window 2022-07-01 .. 2023-10-29 (it ends the day before the earliest
   2023 episode starts; it starts after the 2022 Shanghai lockdown). Metric:
   `n_total` for chokepoints; for ports `portcalls_container` if the window mean is
   >= 2 calls/day, else `portcalls`. A place needs >= 300 valid trailing means;
   ratio clipped to the registry bounds [1.0, 3.0]. Places without a series use
   the median of the ratios of their class (port, chokepoint); ground nodes and
   air hubs have no PortWatch series and keep 1.3 (TEMPORARY, labelled).
   Raw counts are cached in `data/calibration/portwatch_daily_window.json`
   (retrieved 2026-10-25 by `scripts/fetch_portwatch_calibration.py`).
3. PLUG-IN REPLACED BY A SOURCED FIGURE, inventory cover of the Los Angeles and
   Long Beach terminals: 4.0 days (Port of Los Angeles news release 29 Nov 2021,
   https://portoflosangeles.org/references/2021-News-Releases/news_112921_DwellFeeHold,
   retrieved 2026-10-25: before mid-2020 local-delivery containers dwelled "under
   four days" on the terminals, rail containers "less than two days"; the upper
   bound is used). Every other place keeps the graph's 7 / 5 / 3 / 0 days
   (TEMPORARY): no public per-place figure was found for them. Candidate national
   figures (FRED/Census inventories-to-sales: total business 1.33 months,
   merchant wholesalers 1.18, retailers 1.36 months, 2026-08, retrieved
   2026-10-25) are recorded in the registry but NOT applied, because graph
   ground nodes are rail and road hubs and border crossings, not warehouses, and
   the graph carries no commodity class per place.
4. NOT changed, stated so it cannot be read as an omission: the Cape detour days
   stay the route-length difference over the leg's own days-per-km (the services
   sidecar carries rotations, not days; the published figures found are
   secondary trade-press claims of 10 to 14 days for Asia to North Europe and
   are used only as a consistency note); layer equivalence, mode-shift
   fractions, no-restock, route foresight, cost reference stay TEMPORARY. No
   Panama alternative route is added: the services data holds no Suez-sailed
   Asia to US East Coast pair among the graph's legs (checked: 0 of 798
   origin-destination pairs), so the model has no sourced alternative for Panama
   and a long Panama cut keeps queueing. That is reported as an open item, not
   patched with a modelled lane chosen after knowing the observed +7 to 10 days.

## Disclosure of what was looked at before this file was written

* The published scorecards (rev 2, rev 2 + services) and the old-engine internals
  of the Panama episode were read during the diagnosis (queue series at Panama
  and New York, dependence shares, the 173 d figure's arithmetic). The Panama
  replay dataset itself states the observed New York figure (8.5 d, +7 to 10 d
  Suez substitute); it was known. No calibrated run existed. The ratios of the
  PortWatch window were printed (calibration data, not backtest output).
* All three episodes remain IN-SAMPLE for the engine (inherited caveat); the 25
  event protocol v2 is still not run.
* Expected, stated in advance so it can be checked: the delay definition can
  only move the delay column (not coverage); mu_max ratios near 1.1 at chokepoints
  (below 1.3) give less headroom, longer queues and slower recovery than rev 2;
  a New York delay near 8.5 d is NOT expected without a Panama alternative.
  Nothing here predicts the direction of coverage.
