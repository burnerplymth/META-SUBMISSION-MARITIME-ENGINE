"""SupermAP history: historical backplay, fork-to-simulator, realism tester.

Read-only against the engine, the sim and the live
adapters; everything here is additive and lives under ``server/history``.

Vocabulary, in order:
  1. BACKPLAY - pull dated data from the capable APIs (``providers``),
     cached on disk so a repeat call is offline and deterministic
     (``cache``), normalised to DisruptionEvents and per-place daily
     observation series (``normalise``), every record carrying the
     provenance envelope {source, retrieved_at, mode}. Historical records
     are SNAPSHOT (a recorded fetch) or MODELED (inferred); never LIVE.
  2. FORK - ``fork`` turns a historical event into the exact request
     bodies for POST /run (chain) and POST /sim/run (sim) plus a
     ``forked_from`` lineage block. It never runs a simulation itself.
  3. REALISM - ``realism`` grades a stored simulated outcome against the
     observed series under a frozen published rule (BACKTEST label), with
     naive baselines on the same card. Never graded by an LLM.

A source that cannot be reached or has no history is reported
``NOT SERVED`` with the reason; simulated data never backfills a gap.
"""
