# VAL-01 lite: a held-out benchmark across ports, kinds and regions

Label: Backtest. These figures are never merged with the live Ledger numbers. Rule version `val01-v1`.

This page is the pre-registration of the benchmark. The inputs and the rule it pins are data files, identified here by hash:

| File | sha256 |
|---|---|
| `data/eval/val01/episodes.json` (episode inputs and sources, no outcomes) | `d1e714ff652a669496ca1602b205f97e3e0b6b3fe28bba524fe90f6d89c201c3` |
| `data/eval/val01/rule_val01-v1.json` (the metric rule) | `a5ddceb3ce8a43b56185656f8cd20ad3fd14629b2241b32576947252e788508e` |

Caveat. `la_2021` is both a VAL-01 TEST episode and one of the three in-sample replay episodes (`data/replay/la_2021.json`) that the engine configurations (v1, rev 2, rev 2 + services, rev 2 calibrated) were configured and checked against, so its TEST result is not blind for any configuration. The other four TEST episodes (beirut_2020, ila_2024, meishan_2021, rotterdam_2025) are not replay episodes. "Sealed" means the TEST outcome files were hashed before any VAL-01 run, not that the episode was never seen when the engines were configured.

## 1. Purpose

A three-episode, in-sample backtest dominated by two canals cannot support an accuracy claim. This benchmark is mostly port-level, spans regions and place kinds, reports every number stratified so the model's behaviour across ports is visible, and holds a TEST set in a sealed directory that is scored once per frozen model configuration.

## 2. Episodes (14; 9 TRAIN, 5 TEST; 11 port-level, 3 chokepoint-level)

Split rule: `sha256("val01-split-v1:" + id)`, first 8 hex digits as an integer, mod 100; below 60 is TRAIN. The seed string was fixed before any outcome was collected.

| Id | Level, seed place | Region | Kind, loss (basis) | Start, duration | Metric | Split |
|---|---|---|---|---|---|---|
| `shanghai_2022` | port, sea_shanghai | Asia-Pacific | capacity_cut 0.25 (SOURCED, Drewry via Seatrade) | 2022-04-01, 30 d | container calls | TRAIN |
| `nhavasheva_2021` | port, sea_nhavasheva | Asia-Pacific | closure 1.0 (STATED; planned restart) | 2021-05-15, 2.1 d | calls | TRAIN |
| `meishan_2021` | port, sea_ningbo | Asia-Pacific | capacity_cut 0.25 (DERIVED, a quarter of container throughput) | 2021-08-11, 14 d | container calls | TEST |
| `rotterdam_2025` | port, sea_rotterdam | Europe | closure 1.0 (STATED) | 2025-10-08, 4.7 d | container calls | TEST |
| `baltimore_2024` | port, sea_baltimore (shell tier) | North America | closure 1.0 (STATED) | 2024-03-26, 76 d | calls | TRAIN |
| `dorian_2019` | port, sea_charleston | North America | closure 1.0 (STATED) | 2019-09-04, 2.4 d | calls | TRAIN |
| `montreal_2024` | port, sea_montreal | North America | closure 1.0 (STATED) | 2024-11-04, 5.3 d | calls | TRAIN |
| `la_2021` | port, sea_losangeles | North America | capacity_cut 0.78 (MODELED) | 2021-09-19, 58 d | calls | TEST |
| `ila_2024` | port, sea_newyork | North America | closure 1.0 (STATED) | 2024-10-01, 3 d | calls | TEST |
| `beirut_2020` | port, sea_beirut | Middle East & N. Africa | capacity_cut 0.55 (DERIVED, 45 percent operating) | 2020-08-04, 23 d (lower bound) | calls | TEST |
| `santos_2025` | port, sea_santos | Latin America | closure 1.0 (STATED, 29 h) | 2025-07-29, 1.2 d | calls | TRAIN |
| `redsea_2024` | chokepoint, chk_babelmandeb | Middle East & N. Africa | capacity_cut 0.70 (SOURCED, UNCTAD) | 2023-11-19, 559 d | transits | TRAIN |
| `evergiven_2021` | chokepoint, chk_suez | Middle East & N. Africa | closure 1.0 (STATED) | 2021-03-23, 6.3 d | transits | TRAIN |
| `panama_2023_24` | chokepoint, chk_panama | Latin America | capacity_cut 0.50 (DERIVED, ACP 36 to 18/day) | 2023-10-30, 215 d | transits | TRAIN |

Each record in `episodes.json` carries the disruption spec, the basis class of its capacity loss (SOURCED, DERIVED, STATED_CLOSURE, MODELED) with the arithmetic, the a-priori co-affected places and two to four URLs with a retrieval date and a verification label: `fetched` (the page was read), `search_result` (the fact appears in a search-result excerpt for that URL; the page was not available) or `prior_curated_record` (taken from a curated record of the same event in this repo). The weaker labels stay visible on purpose.

Coverage gaps: Sub-Saharan Africa has no port episode and Europe has one, because no candidate had a capacity figure and dates in a fetched source. Candidates and reasons are in `episodes.json` under `excluded`. Air hubs and ground nodes have no PortWatch daily series (apart from three ground crossings), so they cannot be graded here; the report says NOT SERVED rather than leaving them out.

What the set cannot do: every capacity loss is an input given to the engine, so the benchmark never asks whether the engine can predict that a disruption happens or how big it is. It asks where the effect shows up, when, and how the model compares with doing nothing.

## 3. Lockbox

Collection writes the observed outcome of each episode (PortWatch daily series for the seed, the universe and the controls, with source, `retrieved_at`, mode and payload hash) and its placebo window. TRAIN episodes go to `data/eval/val01/train/`; TEST episodes go to `data/eval/val01/lockbox/`, each with a sealed sha256 manifest. The collector prints no TEST value.

Scoring TEST (`run_test` in `server/eval/val01.py`) is refused unless the lockbox manifest verifies, a frozen config hash is supplied and equals the hash recomputed from the model files and the benchmark inputs, and no TEST result for that config hash exists. The config hash and the manifest hash are written into the result. One TEST run per config hash, published whatever it says; a changed model has a new hash and is a new config.

## 4. Metrics (rule `val01-v1`)

Observed series: IMF PortWatch daily counts. Baseline B is the mean of the 28 days before the start day; a place needs 14 days present and B of at least 5 per day, else it is not compared (the reason is recorded). Definitions are in `rule_val01-v1.json`; in short:

* (a) onset day error at the seed: the first day in the first 30 days with s consecutive days at least 20 percent below B, where s = max(1, min(3, floor(duration_d))); the model's onset is its first non-cost chain event at the seed. Hit within 7 days.
* (b) magnitude: peak trough of the w-day trailing mean against B; relative error of the model's loss; hit within 20 points.
* (c) recovery: first day the trailing mean is back within 10 percent of B for 7 days; the model says duration plus the seed wait. Not recovered inside the stored window is censored, not graded.
* (d) downstream places: direction agreement (down, flat, up), recall and precision of "down", and rank correlation between predicted severity (max delay) and the observed drop, against persistence and the directed k-hop baseline.
* (e) control places (a pool of 32): false-positive rate, a place predicted down that is observed not down.
* Placebo windows: the same places read over a window in which the episode did not happen. They give the chance rate of an observed "down", the chance precision of the model's predicted-down places, and the false-onset rate at the seed.

Every rate is printed with its denominator and an exact Clopper-Pearson interval. Ungraded is never a hit and never a miss. Delay days are not graded: PortWatch counts vessels and does not observe waiting time.

## 5. Stratification and per-place results

The result files report every metric three ways:

1. Pooled: one number per metric, with its interval.
2. Stratified by place kind (sea port, chokepoint, ground; air hub NOT SERVED) and by region (North America, Latin America & Caribbean, Europe, Middle East & North Africa, Sub-Saharan Africa, Asia-Pacific). The seed metrics (a, b, c) are stratified by the seed's kind and region; the place-level metrics (d, e, placebo) by each place's kind and region. Each cell shows numerator, denominator and exact interval and is flagged "indicative" under 5 observations. An empty cell is printed as empty, not dropped.
3. Per place: one flat table of every compared place of every episode (observed change, observed and predicted direction, severity, placebo direction), sorted by disagreement, so outliers are visible.

## 6. Configurations

`v1` (default engine), `rev2`, `rev2_services`, `rev2_calibrated` (rev2c plus the same services routes). Engines are called as library code, unmodified. Shell-tier seeds (Baltimore) are refused by every engine; the refusal is recorded and the episode is excluded from the model's denominators, not scored as a miss. No engine configuration carries an accuracy claim: the TEST runs found none distinguishable from trivial baselines.

## 7. Rules the benchmark holds itself to

1. The rule and episodes above do not change after the outcomes are collected. A later correction is made in code and TRAIN and TEST are re-run; the rule text is not changed to fit.
2. No engine is tuned on TEST. TRAIN is the only split a baseline table is run on before the single TEST run per config. `la_2021` is also an in-sample replay episode (see the caveat above), so this holds for VAL-01 itself and for four of the five TEST episodes.
3. TEST results are published whatever they say.

Results: `data/eval/val01/results_*.json`.
