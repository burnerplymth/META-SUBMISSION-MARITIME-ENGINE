# Pre-registration: BACKTEST ENGINE REV 2 + SERVICES ROUTES

Written BEFORE the scorecard in this directory was generated. Scope: the services
layer, with pre-registration discipline.

## What is run

* Engine under test: ENGINE REV 2 (`server/engine2`, plug-in registry
  unchanged) with the ROUTES TABLE built from the graph services sidecar
  (`data/graph/services.json`, sha256 recorded in the scorecard) -
  `routes: 'services'` in the journal.
* Services selection (fixed here, not tuned): eras `pre_red_sea_crisis`;
  `include_unverified: false` (the 21 entries flagged
  `webfetch_not_machine_verified` are excluded); `include_unstated: false`
  (entries with no positive routing statement are excluded). The same
  selection is used for all three episodes: the episodes are disruptions that
  start at or after the crisis (Red Sea 2023-11-19) or before it (Panama
  2023-10-30, Los Angeles 2021), and the network a disruption interrupts is the
  one that was operating before the disruption, i.e. the pre-crisis rotations.
  (The automatic era rule would choose the diversion set for the Red Sea
  episode, which would start the episode from a network already rerouted
  around the Cape; that is the wrong baseline and is therefore overridden here,
  before any run.)
* Datasets: the three replay episodes `data/replay/red_sea_2024.json`,
  `panama_2023_24.json`, `la_2021.json`, byte-untouched.

## What is NOT changed

* Protocol, grading rules and every metric definition are engine v1's
  (`engine.scorecard.build_scorecard` is imported, not copied): coverage,
  first-hit delay window [0.5x, 2x], false positives, budget-matched naive
  k-hop baseline, legacy grading, Clopper-Pearson intervals.
* The naive k-hop baseline is computed on the ORIGINAL core graph, not on the
  routes-augmented graph, so it is the same baseline as in the v1 and rev-2
  scorecards.
* `data/replay/scorecard.{json,md}` (v1) and `data/replay/rev2/*` are not
  touched; their sha256 are recorded in the new scorecard.
* No plug-in value, no routes-table rule and no services selection is changed
  after seeing any replay number. Each episode is run ONCE. The result is
  published whatever it says, including if it is worse than rev 2 or than the
  naive baseline.

## Disclosure of what was looked at before this file was written

* Before this file was written the flagship scenarios (Suez 3/7/14/30/90 d, Bab
  el-Mandeb 14/30 d, Hormuz 14 d, Panama 14 d) were run with and without the
  routes table and their event counts were read. Three routes-table rules were
  adopted after reading those outputs and before any replay was run with a
  routes table: (1) cargo between two calls takes the shortest segment of the
  rotation that joins them, never the whole loop; (2) a segment that passes
  the same chokepoint twice, or crosses Suez / the Cape between two places on
  the same side of it, is not an origin-destination route; (3) route lengths
  are kilometres along the graph's legs with the bound leg's own days-per-km
  as the time basis.
* No replay episode was run with a routes table before this file was
  recorded. The replay episodes remain IN-SAMPLE for the rev-2 engine
  (inherited caveat) and the three episodes are the only ones.
* Expected, stated in advance so that it can be checked: the routes table
  binds only graph legs that a selected service serves through a chokepoint
  (14 core legs on the pre-crisis selection). It therefore can change the
  outcome only where those legs matter (the Red Sea episode: Rotterdam,
  Antwerp, Hamburg, Port Said, the Cape). It cannot change the Panama or Los
  Angeles episodes except through the 3 Panama-routed legs. Nothing here
  predicts the direction of the result.
