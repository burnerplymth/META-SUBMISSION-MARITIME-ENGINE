"""Off-host snapshot of the critical stores.

WHY: one disk failure would lose the Chain ledger (the product's proof
artefact) permanently, and the runtime dirs are not tracked by git. This
is a code-owned, versioned copy of the critical stores OUTSIDE the app
data dir, taken automatically and on demand, with a verified restore.

THE CRITICAL SET (identified from the code, not guessed):

  ledger.jsonl          Chain ledger — server/llm/ledger.py's
                        store (app default: <data>/ledger.jsonl)
  ledger-public.html    its published public page
  events.jsonl          the event store (server/store/store.py
                        EventStore) — autolock, alerts and the Ledger
                        all reference these ids
  records.jsonl         the adapter RecordStore (feed/activity records)
  alerts/               user-authored state: rules.jsonl and
                        watchlists.jsonl (owner tokens live
                        here), fired.jsonl, evaluator state.json
  replay/scorecard.json + replay/scorecard.md
                        the published scorecard artefacts
  graph/graph.json      the world graph every surface derives from
  runs/                 run records — Ledger entries point at
                        run_ids; without these the chains behind a
                        lock cannot be re-served
  ensemble_stats/, ensemble_tree/
                        job artefacts (specs + results users cite)

DELIBERATELY EXCLUDED (named, not silent):
  snapshots/            upstream last-good feed packs — caches of
                        other people's data; re-fetchable, and the
                        adapters recreate the honest empty pack
  sim_runs/             ~1.4 GB of derived simulation records —
                        re-runnable from their stored specs; including
                        them would make per-cycle snapshots infeasible
  llm caches/spend files under server/llm — runtime caches, not stores

MECHANISM: a snapshot is a directory
``<backup_dir>/snapshot-<UTC ISO>/`` containing the critical files at
their data-relative paths plus ``manifest.json`` (per-file sha256 +
size, created_at, source data dir). It is built in a sibling temp
directory and renamed into place, so a crash mid-copy never leaves a
half snapshot under a final name. ``restore_snapshot`` verifies every
manifest hash BEFORE copying anything back, then copies the listed
files over the data dir (files not in the manifest are never touched
or deleted). Retention: the newest ``SUPERMAP_BACKUP_KEEP`` snapshots
(default 7) are kept; older snapshot-* dirs are pruned by the
automatic trigger only (a manual snapshot never silently deletes).

CONFIG (SUPERMAP_* family):
  SUPERMAP_BACKUP_DIR        backup location. Default:
                             <repo>/../supermap-backups — outside the
                             app data dir AND outside the repo, so a
                             data-dir wipe or a repo clean cannot take
                             the backups with it. Point it at a
                             mounted off-host volume for real DR.
  SUPERMAP_BACKUP_KEEP       snapshots retained (default 7)
  SUPERMAP_BACKUP_INTERVAL_S min age of the newest snapshot before the
                             ingestion-cycle trigger takes another
                             (default 3600 — the cycle ticks more often
                             than backups should)
  SUPERMAP_BACKUP=0          disables the automatic (cycle) trigger;
                             the manual CLI always works

TRIGGERS:
  * automatic — adapters/run_once calls apply_backup() at cycle end
    (the apply_alerts/apply_autolock pattern: env-gated, throttled,
    failure-isolated, reported in the cycle summary);
  * manual — ``python3 -m backup.snapshot snapshot|list|restore``
    (from server/, or ``python3 -m server.backup.snapshot`` from the
    repo root). Restore takes the target snapshot dir explicitly.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("SUPERMAP_DATA_DIR", REPO_ROOT / "data"))

# Files (data-relative) + directories (copied recursively, data-relative).
CRITICAL_FILES = (
    "ledger.jsonl",
    "ledger-public.html",
    "events.jsonl",
    "records.jsonl",
    "replay/scorecard.json",
    "replay/scorecard.md",
    "graph/graph.json",
)
CRITICAL_DIRS = (
    "alerts",
    "runs",
    "ensemble_stats",
    "ensemble_tree",
)
MANIFEST_NAME = "manifest.json"
SNAPSHOT_PREFIX = "snapshot-"


def backup_dir() -> Path:
    env = os.environ.get("SUPERMAP_BACKUP_DIR")
    if env:
        return Path(env)
    return REPO_ROOT.parent / "supermap-backups"


def _keep() -> int:
    try:
        return max(1, int(os.environ.get("SUPERMAP_BACKUP_KEEP", "7")))
    except ValueError:
        return 7


def _interval_s() -> float:
    try:
        return max(0.0, float(os.environ.get(
            "SUPERMAP_BACKUP_INTERVAL_S", "3600")))
    except ValueError:
        return 3600.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(now: datetime) -> str:
    return now.strftime("%Y%m%dT%H%M%SZ")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _critical_paths(data_dir: Path) -> list[Path]:
    """Existing critical files, data-relative, sorted."""
    found: list[Path] = []
    for rel in CRITICAL_FILES:
        if (data_dir / rel).is_file():
            found.append(Path(rel))
    for rel in CRITICAL_DIRS:
        root = data_dir / rel
        if root.is_dir():
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    found.append(path.relative_to(data_dir))
    return sorted(found)


def list_snapshots(dest: Path | None = None) -> list[Path]:
    dest = dest or backup_dir()
    if not dest.is_dir():
        return []
    return sorted(p for p in dest.iterdir()
                  if p.is_dir() and p.name.startswith(SNAPSHOT_PREFIX)
                  and (p / MANIFEST_NAME).is_file())


def create_snapshot(data_dir: Path | None = None,
                    dest: Path | None = None,
                    now: datetime | None = None,
                    prune: bool = False) -> dict:
    """Copy the critical set into a new versioned snapshot dir.

    Returns the manifest dict (also written as manifest.json inside
    the snapshot). Missing critical files are listed under
    ``missing`` — a store that does not exist yet is a fact about the
    deployment, not an error."""
    data_dir = Path(data_dir) if data_dir else DATA_DIR
    dest = Path(dest) if dest else backup_dir()
    now = now or _now()
    dest.mkdir(parents=True, exist_ok=True)
    final = dest / f"{SNAPSHOT_PREFIX}{_stamp(now)}"
    if final.exists():  # same-second re-run: disambiguate, never merge
        final = dest / f"{SNAPSHOT_PREFIX}{_stamp(now)}-{uuid.uuid4().hex[:8]}"
    tmp = dest / f".{final.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    tmp.mkdir(parents=True)
    files: list[dict] = []
    present: set[str] = set()
    try:
        for rel in _critical_paths(data_dir):
            target = tmp / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(data_dir / rel, target)
            files.append({"path": rel.as_posix(),
                          "sha256": _sha256(target),
                          "size": target.stat().st_size})
            present.add(rel.as_posix())
        manifest = {
            "kind": "supermap-critical-snapshot",
            "version": 1,
            "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source_data_dir": str(data_dir),
            "files": files,
            "missing": [rel for rel in CRITICAL_FILES
                        if rel not in present],
        }
        (tmp / MANIFEST_NAME).write_text(
            json.dumps(manifest, indent=1, sort_keys=True),
            encoding="utf-8")
        tmp.rename(final)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    manifest["snapshot_dir"] = str(final)
    if prune:
        manifest["pruned"] = prune_snapshots(dest)
    return manifest


def prune_snapshots(dest: Path | None = None, keep: int | None = None) -> list[str]:
    dest = Path(dest) if dest else backup_dir()
    keep = _keep() if keep is None else keep
    snaps = list_snapshots(dest)
    pruned: list[str] = []
    for old in snaps[:max(0, len(snaps) - keep)]:
        shutil.rmtree(old)
        pruned.append(old.name)
    return pruned


def restore_snapshot(snapshot_dir: Path, data_dir: Path | None = None) -> dict:
    """Restore the manifest-listed files from ``snapshot_dir`` into
    the data dir. Every file's sha256 is verified against the
    manifest BEFORE anything is copied — a corrupt or tampered
    snapshot restores nothing. Files outside the manifest are never
    touched. Returns {"restored": [...], "snapshot": name}."""
    snapshot_dir = Path(snapshot_dir)
    data_dir = Path(data_dir) if data_dir else DATA_DIR
    manifest = json.loads(
        (snapshot_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    entries = manifest.get("files") or []
    root = data_dir.resolve()
    for entry in entries:  # verify everything first
        rel = str(entry["path"])
        if Path(rel).is_absolute() or ".." in Path(rel).parts or \
                not (root / rel).resolve().is_relative_to(root):
            raise ValueError(
                f"snapshot manifest path escapes the data dir: {rel!r} "
                "— nothing restored")
        src = snapshot_dir / entry["path"]
        if not src.is_file() or _sha256(src) != entry["sha256"]:
            raise ValueError(
                f"snapshot verification failed for {entry['path']!r}: "
                "file missing or sha256 mismatch — nothing restored")
    restored: list[str] = []
    for entry in entries:
        target = data_dir / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(snapshot_dir / entry["path"], target)
        restored.append(entry["path"])
    return {"restored": restored, "snapshot": snapshot_dir.name,
            "created_at": manifest.get("created_at")}


def apply_backup(data_dir: Path | None = None) -> dict:
    """Ingestion-cycle trigger (run_once pattern): env-gated,
    throttled to SUPERMAP_BACKUP_INTERVAL_S, failure-isolated — the
    caller reports the returned dict in the cycle summary."""
    if os.environ.get("SUPERMAP_BACKUP", "1") == "0":
        return {"enabled": False, "snapshot": None}
    try:
        snaps = list_snapshots()
        if snaps:
            manifest = json.loads(
                (snaps[-1] / MANIFEST_NAME).read_text(encoding="utf-8"))
            created = datetime.strptime(
                manifest["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
                    tzinfo=timezone.utc)
            age = (_now() - created).total_seconds()
            if age < _interval_s():
                return {"enabled": True, "snapshot": None,
                        "skipped": f"newest snapshot is {age:.0f}s old "
                                   f"(< {_interval_s():.0f}s interval)"}
        manifest = create_snapshot(data_dir=data_dir, prune=True)
        return {"enabled": True,
                "snapshot": Path(manifest["snapshot_dir"]).name,
                "files": len(manifest["files"]),
                "pruned": manifest.get("pruned", [])}
    except Exception as exc:
        return {"enabled": True, "snapshot": None, "error": str(exc)}


def _main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] not in ("snapshot", "list", "restore"):
        print(__doc__.split("TRIGGERS:")[0].strip().splitlines()[0])
        print("usage: python3 -m backup.snapshot "
              "snapshot|list|restore <snapshot_dir> [data_dir]")
        return 2
    cmd = argv[1]
    if cmd == "snapshot":
        manifest = create_snapshot(prune=True)
        print(json.dumps({k: manifest[k] for k in
                          ("snapshot_dir", "created_at", "missing")},
                         indent=1))
        print(f"files: {len(manifest['files'])}")
        return 0
    if cmd == "list":
        for snap in list_snapshots():
            print(snap.name)
        return 0
    if len(argv) < 3:
        print("restore needs the snapshot directory")
        return 2
    result = restore_snapshot(
        Path(argv[2]), Path(argv[3]) if len(argv) > 3 else None)
    print(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
