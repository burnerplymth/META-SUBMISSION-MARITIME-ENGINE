"""Alerts & watchlists.

Server-side alert rules over served facts, plus named node watchlists.
Rules evaluate on the seams that already exist — the adapter cycle
(server/adapters/run_once.py, cycle-end hook next to autolock), run/fork
completion (server/api/app.py), and ledger appends observed through those
same passes. No new polling infrastructure.

Semantics:
  * adapter_state rules are LEVEL-triggered with re-arm: a rule fires
    when its condition becomes true and again only after the condition
    has cleared (been observed false) in between.
  * event / run_outcome / ledger rules are EDGE-triggered on facts the
    evaluator has not processed before. Creating a rule primes the
    evaluator baseline (see evaluator.prime), so a rule never fires on
    facts that predate it.

Persistence (server/alerts/store.py) follows the house style: JSONL
append-only logs under data/alerts/ (rules/watchlists as upsert +
tombstone lines, fired alerts append-only), evaluator state as one
atomically-replaced JSON file. Every read is corrupt-line tolerant: a malformed line is skipped and counted, never fatal.
"""
