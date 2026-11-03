#!/usr/bin/env python3
"""Seed the stored chain runs the app reads, deterministically.

What it does
------------
``data/runs/*`` (stored chain runs) is gitignored, so a fresh checkout has
no stored runs, while the desks and report windows read ``data/runs``. This
script seeds the stored runs for the canonical demo events, writing only
under ``<data-dir>/runs`` and never any tracked file:

  * for every ``disruption_id`` the ledger (``data/ledger.jsonl``)
    references, it runs the live-event chain through the same code path
    ``POST /run`` uses (``source=live``) and stores the record under
    ``<data>/runs/``;
  * it also stores the canonical Suez closure scenario (7 d).

It is idempotent: run ids are content hashes (graph hash + event spec +
params), so a second run re-derives the same ids and skips records that
already exist.

It also compares each run id the ledger locks against the id the current
graph and engine produce, and prints any that cannot be reproduced. The
ledger is hash-chained evidence and is left untouched.

Usage
-----
    .venv/bin/python scripts/seed_runtime.py            # seed data/runs
    .venv/bin/python scripts/seed_runtime.py --check    # report only
    .venv/bin/python scripts/seed_runtime.py --strict   # exit 1 if a ledger
                                                        # run id differs
    .venv/bin/python scripts/seed_runtime.py --data-dir /tmp/scratch-data

``--data-dir`` points the script at another data root (it must contain
``events.jsonl`` and ``graph/graph.json``; the tracked ledger is always
read from the repo).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "server"))

CANONICAL_SUEZ = {
    "source": "scenario",
    "scenario": {"disruption": {
        "node_id": "chk_suez", "kind": "closure", "capacity_loss": 1.0,
        "duration_d": 7, "shape": "step",
        "started_at": "2026-10-26T00:00:00Z"}},
}


def ledger_pairs(ledger_path: Path) -> list[tuple[str, str]]:
    """(disruption_id, run_id) for every lock row of the tracked ledger."""
    pairs = []
    if not ledger_path.exists():
        return pairs
    for line in ledger_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("disruption_id") and row.get("run_id"):
            pairs.append((row["disruption_id"], row["run_id"]))
    return pairs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data-dir", type=Path, default=REPO / "data")
    ap.add_argument("--check", action="store_true",
                    help="report what would be seeded; write nothing")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 when a ledger run id cannot be reproduced")
    args = ap.parse_args(argv)

    data_dir = args.data_dir.resolve()
    runs_dir = data_dir / "runs"
    os.environ["SUPERMAP_DATA_DIR"] = str(data_dir)
    os.environ.setdefault("SUPERMAP_RATE_LIMIT", "off")  # in-process only

    from fastapi.testclient import TestClient
    from api.app import create_app

    if args.check:
        app = create_app(runs_dir=runs_dir)
    else:
        runs_dir.mkdir(parents=True, exist_ok=True)
        app = create_app(runs_dir=runs_dir)
    client = TestClient(app)

    mismatches = 0
    print(f"data dir : {data_dir}")
    print(f"runs dir : {runs_dir}{'  (check only)' if args.check else ''}")
    pairs = ledger_pairs(REPO / "data" / "ledger.jsonl")
    print(f"ledger   : {len(pairs)} locked entr{'y' if len(pairs) == 1 else 'ies'}")

    for disruption_id, ledger_run in pairs:
        if args.check:
            known = client.app.state.store.exists(ledger_run)
            print(f"  {disruption_id}: ledger run {ledger_run} "
                  f"{'PRESENT' if known else 'absent'}")
            mismatches += 0 if known else 1
            continue
        resp = client.post("/run", json={
            "source": "live", "live": {"disruption_id": disruption_id}})
        if resp.status_code != 201:
            print(f"  {disruption_id}: cannot regenerate "
                  f"({resp.status_code} {resp.json().get('error', {})})")
            mismatches += 1
            continue
        got = resp.json()["run_id"]
        if got == ledger_run:
            print(f"  {disruption_id}: reproduced {got}")
        else:
            mismatches += 1
            print(f"  {disruption_id}: stored {got} "
                  f"(ledger pins {ledger_run}: not reproducible with the "
                  "current graph/engine)")

    if not args.check:
        resp = client.post("/run", json=CANONICAL_SUEZ)
        print(f"  canonical Suez closure 7d: {resp.json().get('run_id')} "
              f"({resp.json().get('event_count')} events)")

    stored = len(list(runs_dir.glob("run_*.json"))) if runs_dir.exists() else 0
    print(f"stored runs now: {stored}")
    if mismatches:
        print(f"{mismatches} ledger run id(s) not reproducible -- see the "
              "docstring.")
    return 1 if (args.strict and mismatches) else 0


if __name__ == "__main__":
    raise SystemExit(main())
