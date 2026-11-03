"""Scan pipeline: AOI -> STAC pair selection -> titiler crops -> scan record.

Writes ``data/snapshots/satwatch/<node_id>.json`` plus date-labelled
crop PNGs next to it. Imagery is real and live (Planetary Computer);
detection runs through the ``detect`` provider seam and, until the Meta
key lands, records ``counts: null`` with status AWAITING_META_KEY.

CLI:
    python3 -m server.satwatch.scan --aoi sea_portsaid
    python3 scan.py --aoi sea_portsaid sea_singapore --days 90
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:  # package import (repo root on sys.path)
    from . import overlay as overlay_mod
    from . import stac
    from .detect import (DetectionResult, FixtureDetector, STATUS_AWAITING_KEY,
                         STATUS_OK, STATUS_PARTIAL, get_detector)
except ImportError:  # run as a loose script inside server/satwatch/
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import overlay as overlay_mod  # type: ignore
    import stac  # type: ignore
    from detect import (DetectionResult, FixtureDetector, STATUS_AWAITING_KEY,  # type: ignore
                        STATUS_OK, STATUS_PARTIAL, get_detector)

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
DEFAULT_OUT_DIR = REPO_ROOT / "data" / "snapshots" / "satwatch"
AOIS_PATH = HERE / "aois.json"
SCAN_KIND = "satwatch_scan"
SCAN_VERSION = 1


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Atomic write (per-writer unique tmp + replace), the same
    discipline as the other stores — a direct write_bytes/write_text
    can leave a torn crop/record if the process dies mid-write."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def _atomic_write_text(path: Path, text: str) -> None:
    _atomic_write_bytes(path, text.encode("utf-8"))


def load_aois(path: Path = AOIS_PATH) -> dict[str, dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {a["node_id"]: a for a in data["aois"]}


# ---------------------------------------------------------------------------
# Scan-record schema
# ---------------------------------------------------------------------------

class ScanValidationError(ValueError):
    pass


def _scene_block(entry: Optional[dict], label: str) -> Optional[dict]:
    if entry is None:
        return None
    scene = entry["scene"]
    return {
        "id": scene["id"],
        "date": scene["date"],
        "datetime": scene["datetime"],
        "cloud_pct_tile": scene["cloud_pct_tile"],
        "cloud_pct_aoi": entry.get("cloud_pct_aoi"),
        "mgrs_tile": scene["mgrs_tile"],
        "platform": scene["platform"],
    }


def build_record(aoi: dict, pair: dict, detection: DetectionResult,
                 crops: dict, catalog_url: str, stac_status: int,
                 retrieved_at: Optional[str] = None) -> dict:
    now_block = _scene_block(pair.get("now"), "now")
    base_block = _scene_block(pair.get("baseline"), "baseline")
    record = {
        "kind": SCAN_KIND,
        "version": SCAN_VERSION,
        "node_id": aoi["node_id"],
        "name": aoi["name"],
        "bbox": aoi["bbox"],
        "core_bbox": aoi["core_bbox"],
        "scene_now": now_block,
        "scene_baseline": base_block,
        "imagery_dates": {
            "now": now_block["date"] if now_block else None,
            "baseline": base_block["date"] if base_block else None,
        },
        "resolution_m": 10,
        "source": stac.SOURCE_LABEL,
        "retrieved_at": retrieved_at or utcnow_iso(),
        "usable_pair": bool(pair.get("usable_pair")),
        "crops": crops,
        "detection": detection.to_dict(),
        "catalog": {"search_url": catalog_url, "http_status": stac_status},
        "screened": pair.get("evaluated", []),
        "notes": list(pair.get("notes", [])),
    }
    validate_scan_record(record, allow_empty=record["scene_now"] is None)
    return record


def validate_scan_record(record: dict, allow_empty: bool = False) -> dict:
    """Raise ScanValidationError unless ``record`` is a well-formed scan.

    Frozen-contract honesty rules enforced here: provenance is complete,
    imagery dates are present or explicitly null, and detection
    counts are either null (fixture / not run) or a real detector's
    non-negative integer vessel count - never a placeholder number.
    With ``allow_empty=True`` a record whose STAC window produced no
    footprint-covering scene at all (scene_now null) is accepted - that
    is an honest degraded record, written instead of crashing, and it
    must carry an explanatory note.
    """
    if not isinstance(record, dict):
        raise ScanValidationError("scan record is not an object")
    for field_name in ("node_id", "name", "bbox", "core_bbox", "scene_now",
                       "imagery_dates", "resolution_m", "source",
                       "retrieved_at", "detection"):
        if field_name not in record:
            raise ScanValidationError(f"scan record missing {field_name!r}")
    if record["resolution_m"] != 10:
        raise ScanValidationError("resolution_m must be 10 (Sentinel-2 L2A)")
    if not record["source"]:
        raise ScanValidationError("source missing - no datum without a source renders")
    if record["scene_now"] is None and not allow_empty:
        raise ScanValidationError("scene_now is required (a scan with no scene is not a scan)")
    if record["scene_now"] is None and not record.get("notes"):
        raise ScanValidationError("an empty scan record must carry an explanatory note")
    for label in ("scene_now", "scene_baseline"):
        block = record.get(label)
        if block is None:
            continue
        for field_name in ("id", "date", "datetime"):
            if not block.get(field_name):
                raise ScanValidationError(f"{label}.{field_name} missing")
        aoi_cloud = block.get("cloud_pct_aoi")
        if aoi_cloud is not None and not (0.0 <= aoi_cloud <= 100.0):
            raise ScanValidationError(f"{label}.cloud_pct_aoi out of range: {aoi_cloud}")
    det = record["detection"]
    status = det.get("status")
    if det.get("counts") is not None:
        counts = det["counts"]
        vessels = counts.get("vessels") if isinstance(counts, dict) else None
        if not isinstance(vessels, int) or isinstance(vessels, bool) or vessels < 0:
            raise ScanValidationError("detection.counts.vessels must be a non-negative int")
        if status == STATUS_AWAITING_KEY:
            raise ScanValidationError("counts present while status=AWAITING_META_KEY")
        for key in ("vessels_now", "vessels_baseline"):
            val = counts.get(key)
            if val is not None and (not isinstance(val, int)
                                    or isinstance(val, bool) or val < 0):
                raise ScanValidationError(
                    f"detection.counts.{key} must be a non-negative int or null")
        if status == STATUS_OK and "vessels_now" in counts \
                and (counts.get("vessels_now") is None
                     or counts.get("vessels_baseline") is None):
            # a delta is only claimed when BOTH crops were counted
            raise ScanValidationError(
                "status OK requires both vessels_now and vessels_baseline "
                "(PARTIAL when only one crop was counted)")
    elif status in (STATUS_OK, STATUS_PARTIAL):
        raise ScanValidationError(f"status {status} requires counts")
    items = det.get("detections")
    if items is not None:
        try:
            overlay_mod.validate_detections(items)
        except overlay_mod.OverlayError as exc:
            raise ScanValidationError(f"detection overlay invalid: {exc}") from exc
    return record


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def _per_crop_block(res: Optional[DetectionResult], role: str, crop: Optional[str],
                    scene: Optional[dict], n_items: int) -> dict:
    return {
        "role": role,
        "crop_id": crop,
        "scene_date": (scene or {}).get("date"),
        "status": res.status if res else STATUS_AWAITING_KEY,
        "provider": res.provider if res else None,
        "counted": bool(res is not None and res.counts is not None),
        "n_localised": n_items,
        "visibility": res.visibility if res else None,
        # the counting basis rides once on detection.note; per crop only the reason for NOT counting
        "note": (None if res is not None and res.counts is not None else
                 (res.note if res else "no crop for this scene - nothing to detect")),
    }


_ADDITIVE_PROVENANCE = ("sam_calls", "images_processed", "est_cost_usd")


def merge_provenance(results: dict) -> Optional[dict]:
    """Fold per-crop provenance (source/model/cost accounting) into one
    block: shared labels from the first, additive call/cost figures
    summed, each crop's own block kept under ``per_crop``. None when no
    provider attached any (fixture providers)."""
    blocks = {r: res.provenance for r, res in results.items()
              if res is not None and res.provenance}
    if not blocks:
        return None
    first = dict(next(iter(blocks.values())))
    for key in _ADDITIVE_PROVENANCE:
        if any(key in b for b in blocks.values()):
            total = sum(b.get(key) or 0 for b in blocks.values())
            first[key] = round(total, 6) if key == "est_cost_usd" else total
    first.pop("matches_by_prompt", None)
    first.pop("response_ids", None)
    first["per_crop"] = {r: {k: b[k] for k in ("matches_by_prompt", "sam_calls", "images_processed",
                                              "est_cost_usd", "response_ids") if k in b}
                         for r, b in blocks.items()}
    return first


def compose_detection(aoi: dict, pair: dict, crops: dict,
                      results: dict, out_dir: Path,
                      fallback_provider: str = "none") -> DetectionResult:
    """Fold the per-crop results of ONE detector into the scan's
    ``detection`` block.

    ``results`` = {"now": DetectionResult|None, "baseline": ...}. The
    combined counts carry ``<k>_now`` / ``<k>_baseline`` for every
    per-crop count key (so ``vessels_now``/``vessels_baseline`` are
    exactly what the detector reported for each crop) plus
    ``vessels_delta`` ONLY when both crops were counted. Status is OK when
    both crops were counted, PARTIAL when one was, and the detector's
    own AWAITING state otherwise. Nothing is carried from one crop to
    the other.
    """
    now_res, base_res = results.get("now"), results.get("baseline")
    counted = {r: res for r, res in (("now", now_res), ("baseline", base_res))
               if res is not None and res.counts is not None}
    ref = now_res or base_res
    provider = ref.provider if ref else fallback_provider
    label = ref.provider_label if ref else None

    items: list[dict] = []
    n_items = {"now": 0, "baseline": 0}
    for role, res in (("now", now_res), ("baseline", base_res)):
        if res is None or res.detections is None or not crops.get(role):
            continue
        built = overlay_mod.build_items(
            res.detections, node_id=aoi["node_id"], role=role, crop_id=crops[role],
            scene=_scene_block(pair.get(role), role) or {}, detector_id=res.provider,
            crop_px=overlay_mod.png_size(Path(out_dir) / crops[role]),
            visibility_note=res.visibility)
        items += built
        n_items[role] = len(built)

    per_crop = {role: _per_crop_block(results.get(role), role, crops.get(role),
                                      _scene_block(pair.get(role), role), n_items[role])
                for role in ("now", "baseline")}
    if not counted:
        return DetectionResult(
            status=STATUS_AWAITING_KEY, provider=provider, counts=None, detections=None,
            note=(ref.note if ref and ref.note else "no crop was counted - nothing invented"),
            provider_label=label, per_crop=per_crop)

    counts: dict = {}
    for role, res in counted.items():
        for k, v in res.counts.items():
            if k == "count_basis":
                counts["count_basis"] = v
            else:
                counts[f"{k}_{role}"] = v
    counts.setdefault("vessels_now", None)
    counts.setdefault("vessels_baseline", None)
    counts["vessels"] = counts["vessels_now"] if counts["vessels_now"] is not None \
        else counts["vessels_baseline"]
    both = len(counted) == 2
    counts["vessels_delta"] = (counts["vessels_now"] - counts["vessels_baseline"]
                               if both else None)
    counts["counted_by"] = provider
    notes = list(dict.fromkeys(r.note for r in (now_res, base_res)
                               if r is not None and r.note))
    note = " | ".join(notes)
    if not both:
        missing = "baseline" if "now" in counted else "now"
        note = f"PARTIAL: {missing} crop was not counted - no delta. " + note
    return DetectionResult(
        status=STATUS_OK if both else STATUS_PARTIAL, provider=provider,
        counts=counts, detections=items, note=note, provider_label=label,
        per_crop=per_crop, provenance=merge_provenance(results))


def detect_pair(detector, aoi: dict, pair: dict, crops: dict,
                out_dir: Path) -> DetectionResult:
    """Run the SAME detector over the baseline AND the now crop and
    compose the result. This is the only place detectors are invoked."""
    results: dict = {}
    for role in ("now", "baseline"):
        if crops.get(role):
            results[role] = detector.detect(str(Path(out_dir) / crops[role]), aoi)
        else:
            results[role] = None
    return compose_detection(aoi, pair, crops, results, out_dir,
                             fallback_provider=getattr(detector, "name", "none"))


def run_scan(aoi: dict, out_dir: Path, days: int = 90,
             detector=None, http=None, write_crops: bool = True) -> dict:
    """Run one AOI scan end to end and write its record + crops."""
    http = http or stac.default_http
    detector = detector if detector is not None else get_detector()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    start, end = stac.window(days=days)
    fc, catalog_url, status = stac.query_stac(aoi["bbox"], start, end, http=http)
    scenes = stac.parse_items(fc)
    covering = stac.filter_covering(scenes, aoi["core_bbox"])
    by_date = stac.dedupe_by_date(covering)

    cloud_cache: dict[str, Optional[float]] = {}

    def cloud_fn(scene: dict) -> Optional[float]:
        if scene["id"] not in cloud_cache:
            try:
                screen = stac.screen_aoi_cloud(aoi["core_bbox"], scene["id"], http=http)
                cloud_cache[scene["id"]] = screen["cloud_pct_aoi"]
            except Exception:
                cloud_cache[scene["id"]] = None  # degrade honestly, never guess
        return cloud_cache[scene["id"]]

    pair = stac.select_pair(by_date, cloud_fn)
    if not covering:
        pair["notes"].append(
            f"STAC returned {len(scenes)} intersecting scenes but none fully "
            f"covers core_bbox {aoi['core_bbox']} (footprint filter ON)")
    elif not by_date:
        pair["notes"].append("covering scenes existed but none survived date dedupe")
    if pair.get("now") and pair["now"]["cloud_pct_aoi"] is None:
        pair["notes"].append("AOI SCL screening unavailable for the selected scene; "
                              "cloud_pct_aoi is null, so the pair is not usable")
    if not pair.get("usable_pair"):
        pair["notes"].append("no usable (now + baseline) pair under the AOI cloud gate "
                              f"({stac.AOI_CLOUD_USABLE_MAX}% SCL cloud) in this window")

    crops: dict[str, Optional[str]] = {"now": None, "baseline": None}
    # Earth Search failover ids are not PC item ids: the PC titiler cannot
    # crop them. Degrade honestly (imagery-less record) rather than fail later.
    crops_possible = write_crops and catalog_url != stac.EARTH_SEARCH_STAC
    if write_crops and not crops_possible:
        pair["notes"].append(
            "catalog failover (Earth Search) answered; its scene ids cannot be cropped "
            "through the Planetary Computer titiler - no crops, no detection this scan")
    for label in ("now", "baseline"):
        entry = pair.get(label)
        if not entry or not crops_possible:
            continue
        scene = entry["scene"]
        fname = f"{aoi['node_id']}_{label}_{scene['date']}.png"
        raw, _ = stac.fetch_crop(aoi["core_bbox"], scene["id"], http=http)
        _atomic_write_bytes(out_dir / fname, raw)
        crops[label] = fname
    detection = detect_pair(detector, aoi, pair, crops, out_dir)
    record = build_record(aoi, pair, detection, crops, catalog_url, status)
    _atomic_write_text(out_dir / f"{aoi['node_id']}.json",
                       json.dumps(record, indent=1, sort_keys=True) + "\n")
    return record


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="Satellite Watch scan pipeline")
    ap.add_argument("--aoi", nargs="*", default=None,
                    help="node_ids to scan (default: the pre-scan pack)")
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    ap.add_argument("--no-crops", action="store_true")
    args = ap.parse_args(argv)
    aois = load_aois()
    targets = args.aoi or ["sea_portsaid", "sea_singapore", "sea_rotterdam",
                            "sea_busan", "sea_jebelali"]
    failures = 0
    for node_id in targets:
        if node_id not in aois:
            print(f"[satwatch] UNKNOWN AOI {node_id}", file=sys.stderr)
            failures += 1
            continue
        try:
            rec = run_scan(aois[node_id], Path(args.out_dir), days=args.days,
                           write_crops=not args.no_crops)
            now = rec["scene_now"]
            base = rec["scene_baseline"]
            if now is None:
                print(f"[satwatch] {node_id}: NO COVERING SCENE in window "
                      f"(degraded record written) detection={rec['detection']['status']}")
                continue
            print(f"[satwatch] {node_id}: now={now['date']} "
                  f"(tile cloud {now['cloud_pct_tile']}%, AOI cloud {now['cloud_pct_aoi']}%) "
                  f"baseline={base['date'] if base else None} "
                  f"usable_pair={rec['usable_pair']} detection={rec['detection']['status']}")
        except Exception as exc:
            failures += 1
            print(f"[satwatch] {node_id}: FAILED {exc}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
