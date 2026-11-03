"""Ensemble statistics service: job management for the
engine's ensemble-statistics layer (server/engine/ensemble_stats.py).

A job runs up to 10,000 deterministic causal variants of one
disruption on a background thread, aggregates them incrementally,
and persists ONE record per job in its own store directory. Jobs
never write to the run store, the Ledger, or the scorecard —
the separation is structural (this package imports none of them).
"""
