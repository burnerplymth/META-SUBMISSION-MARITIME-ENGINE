"""Replay an existing scan pack through a detector transport.

The live pipeline (scan.run_scan) fetches fresh scenes from STAC. This
module answers a different question - "what would the desk show if the
detector seam had a counted transport?" - by rebuilding each stored scan
record from its OWN stored scene pair and crop files, running the
env-selected detector (detect.get_detector) over the stored now crop,
and re-validating through scan.build_record. No STAC and no new imagery:
scene ids, dates, cloud figures, crops and retrieved_at are carried over byte-for-byte; ONLY the detection block changes.

Used by the offline detector (SATWATCH_DETECTOR=reference-counts, reference
counts recorded from the baked crops) to
exercise the detector seam end to end. A detector that makes paid Meta calls (meta-sam)
is refused unless --live is passed. The source scan
directory is never written to; records + crops land in --out.

CLI:
    SATWATCH_DETECTOR=reference-counts \
      python3 -m server.satwatch.replay --out /tmp/scans
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Optional

try:  # package import (repo root on sys.path)
    from . import scan as scan_mod
    from .detect import (REGISTRY, DetectionResult, get_detector,
                         resolve_provider_id)
except ImportError:  # run as a loose script inside server/satwatch/
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import scan as scan_mod  # type: ignore
    from detect import (REGISTRY, DetectionResult, get_detector,  # type: ignore
                        resolve_provider_id)

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
DEFAULT_SRC_DIR = REPO_ROOT / "data" / "snapshots" / "satwatch"


def _pair_from_record(record: dict) -> dict:
    """Rebuild the select_pair() result shape from a stored record."""
    pair: dict = {"evaluated": record.get("screened", []),
                  "usable_pair": record.get("usable_pair", False),
                  "notes": list(record.get("notes", []))}
    for label in ("now", "baseline"):
        block = record.get(f"scene_{label}")
        if block is None:
            pair[label] = None
            continue
        scene = {k: block[k] for k in
                 ("id", "date", "datetime", "cloud_pct_tile", "mgrs_tile",
                  "platform") if k in block}
        pair[label] = {"scene": scene,
                       "cloud_pct_aoi": block.get("cloud_pct_aoi")}
    return pair


def replay_record(record: dict, src_dir: Path, detector) -> dict:
    """One stored record -> the same record with detection replayed."""
    aoi = {"node_id": record["node_id"], "name": record["name"],
           "bbox": record["bbox"], "core_bbox": record["core_bbox"]}
    pair = _pair_from_record(record)
    crops = dict(record.get("crops") or {"now": None, "baseline": None})
    # BOTH stored crops go through the same detector (baseline + now).
    detection = scan_mod.detect_pair(detector, aoi, pair, crops, src_dir)
    rebuilt = scan_mod.build_record(
        aoi, pair, detection, crops,
        (record.get("catalog") or {}).get("search_url", ""),
        (record.get("catalog") or {}).get("http_status", 0),
        retrieved_at=record["retrieved_at"])
    return rebuilt


def replay_pack(src_dir: Path, out_dir: Path, detector=None) -> list[dict]:
    """Replay every *.json scan record in src_dir into out_dir (+ crops)."""
    detector = detector if detector is not None else get_detector()
    src_dir, out_dir = Path(src_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    aois = scan_mod.load_aois()
    out: list[dict] = []
    for path in sorted(src_dir.glob("*.json"), key=lambda p: p.name):
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("kind") != scan_mod.SCAN_KIND:
            continue
        if record["node_id"] in aois:  # canonical AOI wins over the copy
            record = {**record, "bbox": aois[record["node_id"]]["bbox"],
                      "core_bbox": aois[record["node_id"]]["core_bbox"],
                      "name": aois[record["node_id"]]["name"]}
        rebuilt = replay_record(record, src_dir, detector)
        scan_mod.validate_scan_record(rebuilt)
        (out_dir / path.name).write_text(
            json.dumps(rebuilt, indent=1, sort_keys=True) + "\n",
            encoding="utf-8")
        for fname in (rebuilt.get("crops") or {}).values():
            if fname and (src_dir / fname).is_file():
                shutil.copyfile(src_dir / fname, out_dir / fname)
        out.append(rebuilt)
    return out


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Replay stored satwatch scans through a detector transport")
    ap.add_argument("--src", default=str(DEFAULT_SRC_DIR))
    ap.add_argument("--out", required=True)
    ap.add_argument("--live", action="store_true",
                    help="allow a detector that makes paid Meta calls "
                         "(meta-sam); without it only offline detectors run")
    args = ap.parse_args(argv)
    det_spec = REGISTRY[resolve_provider_id()]
    if det_spec.counts_are_real and not args.live:
        print("[replay] refused: the selected detector makes live paid "
              "calls; pass --live to allow it", file=sys.stderr)
        return 2
    records = replay_pack(Path(args.src), Path(args.out))
    for rec in records:
        det = rec["detection"]
        counts = det.get("counts") or {}
        print(f"[replay] {rec['node_id']}: provider={det['provider']} "
              f"status={det['status']} "
              f"vessels_now={counts.get('vessels_now')} "
              f"vessels_baseline={counts.get('vessels_baseline')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
