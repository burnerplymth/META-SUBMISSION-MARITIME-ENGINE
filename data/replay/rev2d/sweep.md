# ENGINE REV 2 · PROFILE d - all-places sweep and ablation

One run per CORE place with the CRIT standard disruption (closure, 30 d, loss 1.0), profile `4b0b0e79ab6a`, profile d + services chains. Invariants asserted per run: gapless seq, DAG, flow conservation, held-traffic conservation, no negative stock, finite delays, SHELL never in the stream, byte-identical replay, runtime budget.

Places 200; events mean 6.075; places hit mean 5.185; total delay mean 38.4251 d; median of per-run max delay 29.942308 d; never propagate 3; runs with a violation **0**; total 604.64 s.

## By place kind

| Kind | Places | Events min / median / p90 / max | Places hit (median) | Max delay d: median / p90 / max | Never propagate | Slowest run s |
|---|---|---|---|---|---|---|
| air_hub | 43 | 2 / 5.0 / 11.8 / 25 | 5.0 | 29.942308 / 29.942308 / 29.942308 | 0 | 3.078 |
| chokepoint | 12 | 5 / 12.5 / 26.6 / 31 | 10.0 | 29.97084 / 29.981167 / 30.0 | 0 | 8.028 |
| ground_node | 45 | 1 / 3.0 / 8.6 / 20 | 3.0 | 29.942308 / 29.942308 / 29.942308 | 2 | 2.968 |
| port | 100 | 1 / 4.0 / 10.1 / 48 | 4.0 | 29.944801 / 29.963769 / 29.978671 | 1 | 4.899 |

## Flagged outliers

| Place | Kind | Why |
|---|---|---|
| `gnd_citydeep` | ground_node | never propagates (root event only) |
| `gnd_riyadh` | ground_node | never propagates (root event only) |
| `sea_beirut` | port | never propagates (root event only) |
| `sea_shanghai` | port | events 48 > 10 x kind median 4 |

## Leave-one-out ablation, aggregated over ALL CORE places

Each row switches ONE mechanism of the profile off and repeats the sweep. Delta = ablated minus full.

| Mechanism off | Events mean | Places hit mean | Total delay mean (d) | Delta events | Delta places hit | Delta delay (d) | Places with a different stream | Violations |
|---|---|---|---|---|---|---|---|---|
| (none: full profile) | 6.075 | 5.185 | 38.4251 | - | - | - | - | 0 |
| retime | 6.07 | 5.18 | 38.5321 | -0.005 | -0.005 | 0.107 | 178 | 0 |
| sourced_gates | 6.065 | 5.18 | 38.4085 | -0.01 | -0.005 | -0.0166 | 6 | 0 |
| spill | 6.075 | 5.185 | 38.4232 | 0.0 | 0.0 | -0.0019 | 9 | 0 |
| graded_choice | 6.115 | 5.225 | 38.537 | 0.04 | 0.04 | 0.1119 | 31 | 0 |
| rotation | 5.92 | 5.03 | 38.3752 | -0.155 | -0.155 | -0.0499 | 133 | 0 |
| alternatives | 6.05 | 5.145 | 38.4271 | -0.025 | -0.04 | 0.002 | 80 | 0 |
| alternatives_conditional | 6.05 | 5.165 | 38.3528 | -0.025 | -0.02 | -0.0723 | 54 | 0 |
| substitution | 5.92 | 5.03 | 38.4298 | -0.155 | -0.155 | 0.0047 | 136 | 0 |
