"""MASK OVERLAY data contract ``satwatch-overlay/1``.

One shape for "what a detector found on a crop", whatever produced it
(Meta SAM, an open-weights SAM worker, or the offline reference-counts
detector). The map/UI draws from THIS shape and nothing else.

Coordinate space (``coord_space: "norm1000"``): x/y are floats in
[0, 1000], origin top-left of the crop PNG, x right, y down, the same
space the reference-count fixtures already use. Scale to pixels with
``px = v / 1000 * crop_px``. Crops are served square (1200 x 1200) but
``crop_px`` rides on every item so nothing depends on that.

A detection item::

    {
      "id":            "sea_singapore:now:0001",
      "label":         "cargo vessel",          # detector's class/prompt
      "zone":          "anchorage"|"berth"|"transit"|null,
      "score":         0.0..1.0 | null,          # null = detector gives none
      "localization":  "mask"|"bbox"|"point",    # best geometry present
      "bbox":          [x0, y0, x1, y1] | null,  # norm1000, x0<x1, y0<y1
      "point":         [x, y] | null,            # norm1000
      "mask":          null
                       | {"format": "polygon", "rings": [[[x, y], ...], ...]}
                       | {"format": "rle_coco", "size": [h, w], "counts": "..."},
      "coord_space":   "norm1000",
      "crop_px":       [w, h] | null,
      "detector_id":   "reference-counts",
      "crop_id":       "sea_singapore_now_2026-01-01.png",   # /satwatch/crops/<crop_id>
      "role":          "now"|"baseline",
      "scene_date":    "2026-01-01",
      "visibility_basis": {"cloud_pct_aoi": 17.9|null,
                           "cloud_pct_tile": 45.0|null,
                           "method": "...", "note": "..."|null},
      "note":          "..."|null
    }

``polygon`` rings are in norm1000. ``rle_coco`` is a COCO-style
run-length mask in PIXEL space of the crop (``size`` = [height, width])
- decode with pycocotools or any COCO RLE decoder; it is NOT norm1000.
Every geometry field is nullable: a detector that produced only a point
says so (``localization: "point"``, ``mask: null``, ``bbox: null``) and
nothing is invented to fill the gap.
"""
from __future__ import annotations

import math
import struct
from pathlib import Path
from typing import Iterable, Optional

from .detected import stamp

CONTRACT_ID = "satwatch-overlay/1"
COORD_SPACE = "norm1000"
ROLES = ("now", "baseline")
LOCALIZATIONS = ("mask", "bbox", "point")
MASK_FORMATS = ("polygon", "rle_coco")
VISIBILITY_METHOD_SCL = "SCL crop pixel count over core_bbox (classes 8/9/10 = cloud)"

_REQUIRED = ("id", "label", "localization", "coord_space", "detector_id",
             "crop_id", "role", "scene_date", "visibility_basis")


class OverlayError(ValueError):
    """A detection item violates the overlay contract."""


# ---------------------------------------------------------------------------
# Geometry helpers (pure conversions - nothing here estimates a detection)
# ---------------------------------------------------------------------------

def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) \
        and math.isfinite(v)


def _in_space(v) -> bool:
    return _num(v) and 0.0 <= v <= 1000.0


def px_to_norm(v: float, size_px: float) -> float:
    return round(float(v) / float(size_px) * 1000.0, 3)


def bbox_from_xyxy_px(box: Iterable[float], w: int, h: int) -> list[float]:
    x0, y0, x1, y1 = box
    return [px_to_norm(x0, w), px_to_norm(y0, h),
            px_to_norm(x1, w), px_to_norm(y1, h)]


def bbox_from_xywh_px(box: Iterable[float], w: int, h: int) -> list[float]:
    x, y, bw, bh = box
    return bbox_from_xyxy_px((x, y, x + bw, y + bh), w, h)


def ring_from_px(ring: Iterable[Iterable[float]], w: int, h: int) -> list[list[float]]:
    return [[px_to_norm(x, w), px_to_norm(y, h)] for x, y in ring]


def bbox_of_rings(rings: list) -> list[float]:
    """Tight bbox of polygon rings (derived from a REAL mask, not a guess)."""
    xs = [p[0] for r in rings for p in r]
    ys = [p[1] for r in rings for p in r]
    return [min(xs), min(ys), max(xs), max(ys)]


def png_size(path: Path) -> Optional[tuple[int, int]]:
    """(width, height) from a PNG's IHDR, or None when unreadable."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(24)
        if head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
            return None
        w, h = struct.unpack(">II", head[16:24])
        return int(w), int(h)
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate_mask(mask) -> None:
    if not isinstance(mask, dict) or mask.get("format") not in MASK_FORMATS:
        raise OverlayError(f"mask.format must be one of {MASK_FORMATS}")
    if mask["format"] == "polygon":
        rings = mask.get("rings")
        if not isinstance(rings, list) or not rings:
            raise OverlayError("polygon mask needs a non-empty rings list")
        for ring in rings:
            if not isinstance(ring, list) or len(ring) < 3:
                raise OverlayError("each polygon ring needs >= 3 points")
            for pt in ring:
                if not (isinstance(pt, (list, tuple)) and len(pt) == 2
                        and _in_space(pt[0]) and _in_space(pt[1])):
                    raise OverlayError("polygon points must be [x, y] in 0..1000")
    else:
        size, counts = mask.get("size"), mask.get("counts")
        if not (isinstance(size, list) and len(size) == 2
                and all(isinstance(s, int) and not isinstance(s, bool) and s > 0
                        for s in size)):
            raise OverlayError("rle_coco mask.size must be [height, width] ints")
        if not isinstance(counts, str) or not counts:
            raise OverlayError("rle_coco mask.counts must be a non-empty string")


def validate_detection(d: dict) -> dict:
    """Raise OverlayError unless ``d`` is a well-formed overlay item."""
    if not isinstance(d, dict):
        raise OverlayError("detection is not an object")
    for f in _REQUIRED:
        if d.get(f) in (None, ""):
            raise OverlayError(f"detection missing {f!r}")
    if d["coord_space"] != COORD_SPACE:
        raise OverlayError(f"coord_space must be {COORD_SPACE!r}")
    if d["role"] not in ROLES:
        raise OverlayError(f"role must be one of {ROLES}")
    if d["localization"] not in LOCALIZATIONS:
        raise OverlayError(f"localization must be one of {LOCALIZATIONS}")
    crop = d["crop_id"]
    if not isinstance(crop, str) or crop != Path(crop).name or "/" in crop \
            or "\\" in crop or Path(crop).suffix != ".png":
        raise OverlayError("crop_id must be a bare .png filename")
    score = d.get("score")
    if score is not None and not (_num(score) and 0.0 <= score <= 1.0):
        raise OverlayError("score must be null or within 0..1")
    bbox, point, mask = d.get("bbox"), d.get("point"), d.get("mask")
    if bbox is not None:
        if not (isinstance(bbox, (list, tuple)) and len(bbox) == 4
                and all(_in_space(v) for v in bbox)
                and bbox[0] < bbox[2] and bbox[1] < bbox[3]):
            raise OverlayError("bbox must be [x0, y0, x1, y1] in 0..1000, x0<x1, y0<y1")
    if point is not None:
        if not (isinstance(point, (list, tuple)) and len(point) == 2
                and all(_in_space(v) for v in point)):
            raise OverlayError("point must be [x, y] in 0..1000")
    if mask is not None:
        _validate_mask(mask)
    # localization must name geometry that is actually present
    present = {"mask": mask is not None, "bbox": bbox is not None,
               "point": point is not None}
    if not present[d["localization"]]:
        raise OverlayError(
            f"localization={d['localization']!r} but that geometry is null")
    best = next(k for k in LOCALIZATIONS if present[k])
    if best != d["localization"]:
        raise OverlayError(
            f"localization must name the best geometry present ({best!r})")
    vb = d["visibility_basis"]
    if not isinstance(vb, dict) or "cloud_pct_aoi" not in vb or "method" not in vb:
        raise OverlayError("visibility_basis needs cloud_pct_aoi and method")
    return d


def validate_detections(items) -> list:
    if not isinstance(items, list):
        raise OverlayError("detections must be a list")
    seen = set()
    for d in items:
        validate_detection(d)
        if d["id"] in seen:
            raise OverlayError(f"duplicate detection id {d['id']!r}")
        seen.add(d["id"])
    return items


# ---------------------------------------------------------------------------
# Building items from a detector's RAW detections
# ---------------------------------------------------------------------------
# A detector returns raw dicts: {label, zone?, score?, bbox?, point?, mask?,
# note?} in norm1000. build_items() adds the envelope (ids, crop, scene,
# visibility basis) that only the pipeline knows.

def visibility_basis(scene: Optional[dict], note: Optional[str] = None) -> dict:
    scene = scene or {}
    return {
        "cloud_pct_aoi": scene.get("cloud_pct_aoi"),
        "cloud_pct_tile": scene.get("cloud_pct_tile"),
        "method": VISIBILITY_METHOD_SCL if scene.get("cloud_pct_aoi") is not None
        else "AOI cloud screening unavailable for this scene (tile cloud only)",
        "note": note,
    }


def build_items(raw: Optional[list], *, node_id: str, role: str, crop_id: str,
                scene: Optional[dict], detector_id: str,
                crop_px: Optional[tuple[int, int]] = None,
                visibility_note: Optional[str] = None) -> list[dict]:
    items: list[dict] = []
    for i, r in enumerate(raw or [], start=1):
        bbox, point, mask = r.get("bbox"), r.get("point"), r.get("mask")
        localization = ("mask" if mask is not None
                        else "bbox" if bbox is not None else "point")
        if localization == "point" and point is None:
            raise OverlayError("raw detection carries no geometry at all")
        item = {
            "id": f"{node_id}:{role}:{i:04d}",
            "label": r.get("label") or "vessel",
            "zone": r.get("zone"),
            "score": r.get("score"),
            "localization": localization,
            "bbox": list(bbox) if bbox is not None else None,
            "point": list(point) if point is not None else None,
            "mask": mask,
            "coord_space": COORD_SPACE,
            "crop_px": list(crop_px) if crop_px else None,
            "detector_id": detector_id,
            "crop_id": crop_id,
            "role": role,
            "scene_date": (scene or {}).get("date"),
            "visibility_basis": visibility_basis(scene, visibility_note),
            "note": r.get("note"),
        }
        items.append(validate_detection(item))
    return items


# ---------------------------------------------------------------------------
# Serving envelope
# ---------------------------------------------------------------------------

def legacy_point_to_raw(d: dict) -> dict:
    """Pre-contract stored detections ({point, zone, class, localization})
    -> raw detection. Pure re-labelling; geometry stays a point."""
    return {"label": d.get("class") or d.get("label") or "vessel",
            "zone": d.get("zone"), "point": d.get("point"),
            "note": d.get("localization")}


def contract_items_for(scan: dict) -> list[dict]:
    """The scan's detections as contract items. Records written before
    the contract carry bare point dicts; those are re-labelled (not
    re-detected, geometry stays a point) on the way out."""
    det = scan.get("detection") or {}
    crops = scan.get("crops") or {}
    node_id = scan.get("node_id")
    out: list[dict] = []
    for i, d in enumerate(det.get("detections") or [], start=1):
        if isinstance(d, dict) and d.get("coord_space") == COORD_SPACE:
            out.append(d)
        elif isinstance(d, dict) and d.get("point") is not None:
            built = build_items(
                [legacy_point_to_raw(d)], node_id=node_id, role="now",
                crop_id=crops.get("now") or "legacy.png",
                scene=scan.get("scene_now"),
                detector_id=det.get("provider") or "unknown")
            built[0]["id"] = f"{node_id}:now:{i:04d}"
            out.append(built[0])
    return out


def normalise_scan(scan: dict) -> dict:
    """A copy of ``scan`` whose detection.detections are contract items
    (served by GET /satwatch/scans). Records with no detections pass
    through unchanged."""
    det = scan.get("detection")
    if not isinstance(det, dict) or not isinstance(det.get("detections"), list):
        return scan
    try:
        items = contract_items_for(scan)
    except OverlayError:
        return scan
    return {**scan, "detection": {**det, "detections": items,
                                  "overlay_contract": CONTRACT_ID}}


def overlay_for_scan(scan: dict, crop_url_prefix: str = "/satwatch/crops/") -> dict:
    """The overlay envelope for one stored scan record.

    ``served`` is False (with a reason) whenever no detector counted this
    scan - the UI hides the mask layer then; it never gets an empty list
    dressed up as "zero vessels".
    """
    det = scan.get("detection") or {}
    crops = scan.get("crops") or {}
    node_id = scan.get("node_id")

    def crop_block(role: str) -> Optional[dict]:
        fname = crops.get(role)
        scene = scan.get(f"scene_{role}")
        if not fname:
            return None
        return {"file": fname, "url": f"{crop_url_prefix}{fname}",
                "scene_date": (scene or {}).get("date"),
                "scene_id": (scene or {}).get("id"),
                "cloud_pct_aoi": (scene or {}).get("cloud_pct_aoi"),
                "cloud_pct_tile": (scene or {}).get("cloud_pct_tile")}

    status = det.get("status") or "AWAITING_META_KEY"
    served = isinstance(det.get("detections"), list) and det.get("counts") is not None
    reason = None
    if not served:
        reason = (det.get("note") or "no detector has counted this scan")
    contract_items = contract_items_for(scan) if served else []
    counts = det.get("counts")
    delta = None
    if isinstance(counts, dict) and counts.get("vessels_now") is not None \
            and counts.get("vessels_baseline") is not None:
        delta = counts["vessels_now"] - counts["vessels_baseline"]
    return {
        "contract": CONTRACT_ID,
        "coord_space": COORD_SPACE,
        "node_id": node_id,
        "name": scan.get("name"),
        "served": bool(served),
        "not_served_reason": reason,
        "status": status,
        "provider": det.get("provider"),
        "provider_label": det.get("provider_label"),
        "counts": counts,
        "delta": delta,
        **stamp(counts),
        "imagery_dates": scan.get("imagery_dates"),
        "usable_pair": scan.get("usable_pair"),
        "crops": {"now": crop_block("now"), "baseline": crop_block("baseline")},
        "detections": contract_items if served else [],
        "per_crop": det.get("per_crop"),
        "countable_class": det.get("countable_class"),
        "note": det.get("note"),
    }
