"""Data-pack export.

Given a run_id, build a pack (directory, or zip with --zip) containing:
  chain.json    — the GET /chain payload for the run: the event stream
                  VERBATIM from the run-store record plus edges
                  materialised by the edge rule (derived
                  mechanically from parent_id) via the ONE shared
                  implementation in server/chain_edges.py.
  scorecard.json— the stored scorecard artifact, copied byte-for-byte
                  (only when present; its absence is stated in summary.md).
  ledger.json   — this run's ledger entries from data/ledger.jsonl
                  (empty list + explicit note when the file is absent).
  summary.md    — run inputs, headline results, scorecard headline WITH
                  its honesty caveats (house vocabulary), ledger state.
  manifest.json — per-file provenance {source, generated_at, sha256}.

DETERMINISM (acceptance): regenerating the pack for the same run from
the same inputs yields byte-identical files EXCEPT manifest.json,
whose only varying field is generated_at. summary.md deliberately
contains no timestamp. The zip form uses a fixed 1980-01-01 entry date
and sorted entry names, so the same exception (manifest generated_at)
is the only source of byte difference there too.

Market quarantine: a pack contains NO money-layer data. Modelled
figures appear only via the scorecard's own labelled content.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUNS_DIR = REPO_ROOT / "data" / "runs"
DEFAULT_SCORECARD = REPO_ROOT / "data" / "replay" / "scorecard.json"
DEFAULT_LEDGER = REPO_ROOT / "data" / "ledger.jsonl"

if str(REPO_ROOT / "server") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "server"))
from money.common import utcnow_iso  # noqa: E402
from chain_edges import materialise_edges  # noqa: E402
from reports.vocab import plain  # noqa: E402


def _display_path(path: Path, logical: str) -> str:
    """The path a pack records for an input — relative to the
    repo root when the input lives under it, otherwise the stable
    logical name for that input. A pack built anywhere must never
    embed the build machine's absolute (home) path."""
    try:
        return Path(path).resolve().relative_to(
            REPO_ROOT.resolve()).as_posix()
    except (ValueError, OSError):
        return logical


def canonical_bytes(obj) -> bytes:
    return (json.dumps(obj, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False) + "\n").encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _check_run_id(run_id: str) -> None:
    if (not isinstance(run_id, str) or not run_id or "/" in run_id
            or "\\" in run_id or ".." in run_id or "\x00" in run_id):
        raise ValueError("bad run_id")


def load_run_record(run_id: str, runs_dir: Path) -> dict:
    _check_run_id(run_id)
    path = runs_dir / f"{run_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"unknown run_id: {run_id} (no {path})")
    return json.loads(path.read_text(encoding="utf-8"))




def chain_payload(record: dict) -> dict:
    events = record["events"]
    return {
        "run_id": record["run_id"],
        "status": record["status"],
        "event_count": record["event_count"],
        "stream_hash": record["stream_hash"],
        "events": events,
        "edges": materialise_edges(
            events, record["journal"]["disruption"]["node_id"]),
    }


def load_ledger_entries(ledger_path: Path, run_id: str) -> tuple[list[dict], bool]:
    """(entries for run_id, file_present). A malformed line is skipped
    and counted by the caller via the returned entries only — the pack
    states the file's presence, never repairs it."""
    if not ledger_path.exists():
        return [], False
    entries = []
    for line in ledger_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict) and entry.get("run_id") == run_id:
            entries.append(entry)
    return entries, True


def build_summary(record: dict, scorecard: dict | None,
                  ledger_entries: list[dict], ledger_present: bool) -> str:
    journal = record["journal"]
    disruption = journal["disruption"]
    events = record["events"]
    nodes = sorted({ev["node_id"] for ev in events})
    channels: dict[str, int] = {}
    for ev in events:
        channels[ev["channel"]] = channels.get(ev["channel"], 0) + 1
    max_delay = max((ev["delay_d"] for ev in events), default=0.0)
    lines = [
        f"# SupermAP data pack — {record['run_id']}",
        "",
        "## Run inputs (from the run journal)",
        "",
        f"- Journal kind: `{journal['kind']}`",
        f"- Map: version {journal.get('graph_version')}, "
        f"hash `{journal.get('graph_hash')}`",
        f"- Disruption: place `{disruption['node_id']}`, "
        f"kind `{disruption['kind']}`, "
        f"capacity_loss {disruption.get('capacity_loss')}, "
        f"duration_d {disruption.get('duration_d')}, "
        f"shape `{disruption.get('shape')}`, "
        f"started_at {disruption.get('started_at')}",
        f"- Params: `{json.dumps(journal.get('params') or {}, sort_keys=True)}`",
    ]
    if journal["kind"] == "fork":
        lines.append(f"- Fork of: `{journal.get('parent_run_id')}` "
                     f"at seq {journal.get('at_seq')}, "
                     f"overrides `{json.dumps(journal.get('overrides') or {}, sort_keys=True)}`")
    lines += [
        "",
        "## Headline results",
        "",
        f"- Status: {record['status']}",
        f"- Events: {record['event_count']} across {len(nodes)} places",
        f"- Stream hash (SHA-256 over canonical event array): `{record['stream_hash']}`",
        f"- Max delay_d: {max_delay}",
        "- Channels: " + ", ".join(f"{k} ×{v}" for k, v in sorted(channels.items())),
        f"- Places hit: {', '.join(f'`{n}`' for n in nodes)}",
        "",
        "## Backtest scorecard (BACKTEST: past replays, descriptive only)",
        "",
    ]
    if scorecard is None:
        lines.append("No scorecard artifact was present at pack build time "
                     "(expected at data/replay/scorecard.json).")
    else:
        agg = scorecard.get("aggregate") or {}
        cov = agg.get("coverage") or {}
        delay = agg.get("delay_accuracy") or {}
        fps = agg.get("false_positives") or {}
        counts = agg.get("counts") or {}
        lines += [
            f"- Replays graded: {agg.get('replays')}",
            f"- Places matched: {cov.get('covered_count')} of "
            f"{cov.get('distinct_non_seed_nodes')} observed — pooled "
            "per-replay denominators, see scorecard.json for the definition",
            f"- Delay difference: {delay.get('hits')} of "
            f"{delay.get('graded_steps')} graded steps matched, mean "
            f"absolute difference {delay.get('mean_abs_error_d')} d "
            "(graded steps only; observed-not-modelled and unquantified "
            "steps are excluded from this mean by the stated methodology)",
            f"- Modelled, not observed: {fps.get('count')}; observed, not "
            f"modelled: {counts.get('genuine_misses')}; observed steps: "
            f"{counts.get('observed_steps')}",
            "",
            "### Scorecard caveats",
            "",
        ]
        for caveat in scorecard.get("honesty_caveats") or []:
            lines.append(f"- {plain(str(caveat))}")
    lines += [
        "",
        "## Ledger state",
        "",
    ]
    if not ledger_present:
        lines.append("No ledger file was present at data/ledger.jsonl at "
                     "pack build time; this run has no locked ledger "
                     "entries in the pack (ledger.json records this).")
    else:
        lines.append(f"Ledger file present; entries locked for this run: "
                     f"{len(ledger_entries)}.")
        for entry in ledger_entries:
            grade = entry.get("grade")
            grade_txt = ("ungraded" if grade is None else
                         f"graded ({len(grade.get('hit_steps') or [])} steps matched)")
            lines.append(f"- `{entry.get('entry_id')}` — locked_at "
                         f"{entry.get('locked_at')}, {grade_txt}")
    lines += [
        "",
        "## Provenance & quarantine",
        "",
        "- chain.json is the run's event stream verbatim (via the run API chain "
        "shape); see manifest.json for per-file SHA-256 provenance.",
        "- Source: SupermAP stored run record, replay scorecard and chain "
        "ledger. No external feed is copied into this pack.",
        "- This pack contains no money-layer/market data (market "
        "quarantine). Any modelled "
        "figure in the scorecard is labelled there as MODELED/BACKTEST.",
        "",
    ]
    return "\n".join(lines)


def build_pack(run_id: str, out_dir: str | Path,
               runs_dir: str | Path = DEFAULT_RUNS_DIR,
               scorecard_path: str | Path = DEFAULT_SCORECARD,
               ledger_path: str | Path = DEFAULT_LEDGER) -> dict:
    """Build the pack directory; returns the manifest dict."""
    runs_dir, scorecard_path, ledger_path = (Path(runs_dir),
                                             Path(scorecard_path),
                                             Path(ledger_path))
    out_dir = Path(out_dir)
    record = load_run_record(run_id, runs_dir)
    generated_at = utcnow_iso()

    files: dict[str, bytes] = {}
    sources: dict[str, str] = {}

    files["chain.json"] = canonical_bytes(chain_payload(record))
    sources["chain.json"] = (
        f"run store record "
        f"{_display_path(runs_dir / (run_id + '.json'), f'data/runs/{run_id}.json')} "
        "rendered in the run API GET /chain shape (events verbatim)")

    scorecard = None
    if scorecard_path.exists():
        raw = scorecard_path.read_bytes()
        files["scorecard.json"] = raw
        sources["scorecard.json"] = (
            f"stored artifact "
            f"{_display_path(scorecard_path, 'data/replay/scorecard.json')} "
            "(byte copy)")
        scorecard = json.loads(raw.decode("utf-8"))

    ledger_entries, ledger_present = load_ledger_entries(ledger_path, run_id)
    ledger_display = _display_path(ledger_path, "data/ledger.jsonl")
    files["ledger.json"] = canonical_bytes({
        "run_id": run_id,
        "ledger_file": ledger_display,
        "ledger_file_present": ledger_present,
        "entries": ledger_entries,
        **({} if ledger_present else
           {"note": "no ledger file present at pack build time; "
                    "zero entries is a fact about the store, not a grade"}),
    })
    sources["ledger.json"] = (f"ledger {ledger_display} filtered to run_id "
                              f"{run_id}" if ledger_present else
                              f"ledger {ledger_display} absent at build time")

    summary = build_summary(record, scorecard, ledger_entries, ledger_present)
    files["summary.md"] = summary.encode("utf-8")
    sources["summary.md"] = "generated by server/reports/pack.py from the run record, scorecard and ledger"

    manifest = {
        "pack": "supermap-data-pack",
        "run_id": run_id,
        "generated_at": generated_at,
        "determinism": "all files byte-identical across regenerations "
                       "except this manifest's generated_at",
        "files": {
            name: {"source": sources[name], "generated_at": generated_at,
                   "sha256": sha256_hex(data), "bytes": len(data)}
            for name, data in sorted(files.items())
        },
    }
    files["manifest.json"] = (json.dumps(manifest, indent=1, sort_keys=True)
                              + "\n").encode("utf-8")

    out_dir.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (out_dir / name).write_bytes(data)
    return manifest


def build_pack_zip(run_id: str, out_zip: str | Path, **kw) -> dict:
    """Same pack as a zip: fixed entry timestamps (1980-01-01) and
    sorted names; the manifest generated_at exception still applies."""
    import io
    buf_dir = Path(str(out_zip) + ".dir")
    manifest = build_pack(run_id, buf_dir, **kw)
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(buf_dir.iterdir(), key=lambda p: p.name):
            info = zipfile.ZipInfo(path.name, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16
            zf.writestr(info, path.read_bytes())
    for path in buf_dir.iterdir():
        path.unlink()
    buf_dir.rmdir()
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build a SupermAP data pack")
    ap.add_argument("run_id")
    ap.add_argument("--out", required=True, help="output directory (or .zip path with --zip)")
    ap.add_argument("--zip", action="store_true")
    ap.add_argument("--runs-dir", default=str(DEFAULT_RUNS_DIR))
    ap.add_argument("--scorecard", default=str(DEFAULT_SCORECARD))
    ap.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    args = ap.parse_args(argv)
    kw = {"runs_dir": args.runs_dir, "scorecard_path": args.scorecard,
          "ledger_path": args.ledger}
    if args.zip:
        manifest = build_pack_zip(args.run_id, args.out, **kw)
    else:
        manifest = build_pack(args.run_id, args.out, **kw)
    print(json.dumps({"run_id": args.run_id, "out": args.out,
                      "files": sorted(manifest["files"])}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
