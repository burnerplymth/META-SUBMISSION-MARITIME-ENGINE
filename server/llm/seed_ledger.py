"""Seed the first REAL ledger lock from existing data — no fabrication.

Source chain (all pre-existing, produced by other code paths):
  * disruption: the first NOAA/NWS event in data/events.jsonl
    (evt_20261020_sea_savannah_weather_200da99e)
  * run: the stored run record whose journal.live_disruption_id equals that
    event's id (run_93f4e9b02a99299e) — an engine run over the
    real graph through the API/engine path.

If no stored run is journal-linked to a live event, this script refuses to
seed anything rather than inventing a pairing. Run:
    python -m llm.seed_ledger
Idempotent: re-running detects the existing lock for the same run and
does not append a duplicate.
"""
from __future__ import annotations

import json
from pathlib import Path

from .ledger import LedgerStore

REPO_ROOT = Path(__file__).resolve().parents[2]


def find_seed() -> tuple[dict, dict] | None:
    events_path = REPO_ROOT / "data" / "events.jsonl"
    if not events_path.exists():
        return None
    events = [json.loads(line) for line in
              events_path.read_text(encoding="utf-8").splitlines()
              if line.strip()]
    runs_dir = REPO_ROOT / "data" / "runs"
    runs = []
    if runs_dir.exists():
        for p in sorted(runs_dir.glob("*.json")):
            try:
                runs.append(json.loads(p.read_text(encoding="utf-8")))
            except Exception:
                continue
    for event in events:
        for run in runs:
            if run.get("journal", {}).get("live_disruption_id") == event["id"]:
                return event, run
    return None


def main() -> int:
    found = find_seed()
    if found is None:
        print("NO SEED: no stored run is journal-linked to a live event; "
              "refusing to fabricate a pairing.")
        return 1
    event, run = found
    store = LedgerStore()
    for existing in store.entries():
        if existing["run_id"] == run["run_id"] \
                and existing["disruption_id"] == event["id"]:
            print(f"ALREADY SEEDED: {existing['entry_id']} "
                  f"(run {run['run_id']}, disruption {event['id']})")
            return 0
    entry = store.lock(event, run)
    print(f"SEEDED: {entry['entry_id']} locked_at={entry['locked_at']} "
          f"run={entry['run_id']} disruption={entry['disruption_id']} "
          f"steps={len(entry['predicted_chain'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
