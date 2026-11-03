"""Server-side tick simulation.

A separate, additive simulation layer over the same graph and
Event-shaped disruption specs the causal engine consumes. It never reads
or writes event streams, the scorecard, or the Ledger: sim runs live under
their own ``sim_…`` ids in ``data/sim_runs/`` and every figure they
produce is a SIMULATED estimate.

Modules:
  geometry  — great-circle (slerp) sampling, antimeridian splitting,
              great-circle distance. Pure functions.
  derive    — lane derivation from the pinned graph (headway / speed
              classes from in-mode quartiles), land-crossing fractions
              vs the bundled Natural Earth polygons + ``rendered_via``
              disposition, entity roster + schedules from ONE seeded
              RNG in a canonical draw order.
  weather   — Open-Meteo snapshot client (batched, pinnable, honest
              failure: ``(None, reason)``, never fabricated).
  simulate  — event-segment simulator (ping-pong routing, FIFO berth
              queues, disruption windows, weather factors).
  state     — pure ``state(tick)`` over a stored run record.
  store     — SimStore, mirroring the RunStore discipline.
"""
