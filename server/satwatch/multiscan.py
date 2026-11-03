"""Multi-source products for the satellite desk.

Two products beyond the Sentinel-2 scan pipeline:

* ``scan_s1``    Sentinel-1 GRD VV crop -> CFAR bright targets (MODELED).
* ``crop_optical``  NAIP (US aerial) or Landsat crop, imagery only.

Each writes one record and one PNG under ``<satwatch_dir>/multi/`` (a
subdirectory, so the Sentinel-2 scan listing and replay pack, which glob
the top level, never see them). The PNG is served by
``GET /satwatch/multi/crops/{file}.png``.

Provenance, as ``{source, retrieved_at, mode}``:

* the crop image is a stored scene            -> ``SNAPSHOT``
* detector output (points, counts, scores)    -> ``MODELED``

Nothing here is ever ``SOURCED``, and a count is always a number of bright candidate targets (clutter such as quays included), never a vessel count:
``count_basis`` rides on the record. These records are NOT scan records
(``kind`` is ``satwatch_multi``): the signal minting path does not read
them, so a SAR target count cannot mint an event.
"""
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import multistac as ms
from . import overlay as overlay_mod
from . import sar as sar_mod
from . import sources as src_mod
from .detected import stamp
from .scan import _atomic_write_bytes, _atomic_write_text
from .stac import HttpFn, StacError, coverage_fraction, default_http

KIND = "satwatch_multi"
CONTRACT = "satwatch-multi/1"
MULTI_DIR = "multi"
CROP_URL_PREFIX = "/satwatch/multi/crops/"
PLACE_RE = re.compile(r"^[a-z0-9_]{1,64}$")
OPTICAL_SOURCES = ("naip", "landsat")
NAIP_WINDOW_M = ms.NAIP_WINDOW_M
OPTICAL_MAX_PX = 1200
MIN_NAIP_COVER = 0.15
MAX_SCENE_TRIES = 3

COUNT_BASIS = "BRIGHT_TARGETS"
DETECTOR_STATUS_NOT_RUN = "NOT RUN"


class MultiError(RuntimeError):
    """A multi-source product could not be made. ``code`` is the served
    error code; nothing is faked when it is raised."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def record_path(satwatch_dir: Path, source_id: str, place: str) -> Path:
    if not PLACE_RE.match(place):
        raise ValueError("bad place id")
    src_mod.get_source(source_id)
    return Path(satwatch_dir) / MULTI_DIR / f"{place}__{source_id}.json"


def load_record(satwatch_dir: Path, source_id: str, place: str) -> Optional[dict]:
    try:
        path = record_path(satwatch_dir, source_id, place)
    except (ValueError, KeyError):
        return None
    if not path.is_file():
        return None
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not (isinstance(rec, dict) and rec.get("kind") == KIND):
        return None
    for k, v in stamp(rec.get("counts")).items():       # records stored before the stamp existed
        rec.setdefault(k, v)
    return rec


def summarise(rec: Optional[dict]) -> Optional[dict]:
    """What the chip row needs from a stored record."""
    if not rec:
        return None
    return {"date": (rec.get("scene") or {}).get("date"),
            "retrieved_at": rec.get("retrieved_at"),
            "targets_floor": (rec.get("counts") or {}).get("targets_floor"),
            "verification": rec.get("verification"), "human_reviewed": rec.get("human_reviewed", False),
            "detector_status": (rec.get("detector") or {}).get("status")}


# ---------------------------------------------------------------------------
# Rendering (a normal display of the raw values, not an enhancement)
# ---------------------------------------------------------------------------

def render_amplitude_png(width: int, height: int, values) -> tuple[bytes, dict]:
    """Log (dB) amplitude stretch between the 5th and 99.8th percentile of
    the valid pixels. Returns (png_bytes, render_info)."""
    import io
    from PIL import Image  # type: ignore
    sample = sorted(int(v) for v in values[::7] if v > 0)
    if not sample:
        raise MultiError("empty_crop", "crop holds no valid pixels")
    lo_v = max(1, sample[int(len(sample) * 0.05)])
    hi_v = max(lo_v + 1, sample[min(len(sample) - 1, int(len(sample) * 0.998))])
    lo, hi = 20.0 * math.log10(lo_v), 20.0 * math.log10(hi_v)
    span = max(hi - lo, 1e-6)
    lut = bytearray(65536)
    for v in range(1, 65536):
        lut[v] = max(0, min(255, int(255.0 * (20.0 * math.log10(v) - lo) / span)))
    data = bytes(lut[min(65535, int(v))] for v in values)
    buf = io.BytesIO()
    Image.frombytes("L", (width, height), data).save(buf, "PNG", optimize=True)
    return buf.getvalue(), {
        "stretch": "log amplitude (dB), p5 to p99.8 of valid pixels",
        "p5_dn": lo_v, "p998_dn": hi_v}


def _ensure_dirs(satwatch_dir: Path) -> None:
    (Path(satwatch_dir) / MULTI_DIR).mkdir(parents=True, exist_ok=True)


def _crop_name(prefix: str, place: str, date: str) -> str:
    return f"{prefix}_{place}_{date}.png"


def _drop_old_crop(satwatch_dir: Path, rec: Optional[dict], keep: str) -> None:
    old = ((rec or {}).get("crop") or {}).get("file")
    if old and old != keep and old == Path(old).name and old.endswith(".png"):
        try:
            (Path(satwatch_dir) / MULTI_DIR / old).unlink()
        except OSError:
            pass


def _provenance(src: dict, mode: str, retrieved_at: str) -> dict:
    return {"source": f"{src['label']} via Earth Search STAC and Planetary Computer titiler; "
                      f"{src['attribution']}",
            "retrieved_at": retrieved_at, "mode": mode}


def _scene_block(scene: dict, source_id: str) -> dict:
    out = {"id": scene["id"], "date": scene["date"], "datetime": scene["datetime"],
           "platform": scene.get("platform")}
    if scene.get("cloud_pct_tile") is not None:
        out["cloud_pct_tile"] = scene["cloud_pct_tile"]
    if source_id == "sentinel-1":
        out["orbit_state"] = scene.get("orbit_state")
        out["instrument_mode"] = scene.get("instrument_mode")
    if source_id == "naip":
        out["year"] = scene.get("year")
    return out


# ---------------------------------------------------------------------------
# Sentinel-1
# ---------------------------------------------------------------------------

def build_s1_items(place: str, detections: list[dict], width: int, height: int,
                   crop_file: str, scene: dict) -> list[dict]:
    """Detector hits as satwatch-overlay/1 point items (norm1000)."""
    raw = []
    for d in detections:
        raw.append({
            "label": "bright target (SAR)",
            "zone": None,
            "score": d["score"],
            "point": [overlay_mod.px_to_norm(d["x"] + 0.5, width),
                      overlay_mod.px_to_norm(d["y"] + 0.5, height)],
            "note": f"contrast {d['contrast_db']} dB",
        })
    items = overlay_mod.build_items(
        raw, node_id=place, role="now", crop_id=crop_file,
        scene={"date": scene["date"], "cloud_pct_aoi": None, "cloud_pct_tile": None},
        detector_id=sar_mod.ALGORITHM_ID, crop_px=(width, height))
    for it, d in zip(items, detections):
        it["visibility_basis"] = {"cloud_pct_aoi": None, "cloud_pct_tile": None,
                                  "method": "SAR: not cloud-limited", "note": None}
        it["size_px"] = d["size_px"]
        it["contrast_db"] = d["contrast_db"]
    return items


def scan_s1(place: str, aoi: dict, satwatch_dir: Path, *,
            http: HttpFn = default_http, days: Optional[int] = None,
            params: Optional[sar_mod.CfarParams] = None) -> dict:
    """Fetch the newest covering Sentinel-1 GRD crop for ``place``, run the
    CFAR detector, write the record and PNG, return the record."""
    src = src_mod.get_source("sentinel-1")
    bbox = list(aoi["core_bbox"])
    try:
        scenes = ms.usable_s1(ms.covering(
            ms.search("sentinel-1", bbox, days=days, http=http), bbox))
    except StacError as exc:
        raise MultiError("feed_down", str(exc)) from exc
    if not scenes:
        raise MultiError("no_scene", "no Sentinel-1 IW scene covers this place in the window")
    w, h, eff = ms.crop_size_px(bbox, float(src["pixel_spacing_m"]))
    # The catalogue lists a scene before the renderer has it (the newest one
    # can 404 for a few hours): fall back to the next covering scene, never
    # to an invented one.
    last_err: Optional[StacError] = None
    for scene in scenes[:MAX_SCENE_TRIES]:
        try:
            raw = ms.fetch_crop_bytes("sentinel-1", bbox, scene["id"], w, h, http=http)
            cw, ch, values = ms.decode_gray(raw)
            break
        except StacError as exc:
            last_err = exc
    else:
        raise MultiError("crop_not_served", str(last_err)) from last_err
    if (cw, ch) != (w, h):       # the renderer returned another size: say so
        eff = round(eff * w / cw, 2)
    result = sar_mod.detect_ships(cw, ch, values, pixel_spacing_m=eff, params=params)
    png, render = render_amplitude_png(cw, ch, values)

    retrieved = _now()
    crop_file = _crop_name("s1", place, scene["date"])
    prev = load_record(satwatch_dir, "sentinel-1", place)
    sd = Path(satwatch_dir)
    _ensure_dirs(sd)
    _atomic_write_bytes(sd / MULTI_DIR / crop_file, png)
    detections = result["detections"] if result["status"] == sar_mod.STATUS_OK else []
    items = build_s1_items(place, detections, cw, ch, crop_file, scene)
    served = result["status"] == sar_mod.STATUS_OK
    rec = {
        "kind": KIND, "contract": CONTRACT, "source": "sentinel-1", "place": place,
        "name": aoi.get("name"), "retrieved_at": retrieved,
        "imagery_mode": "SNAPSHOT", "mode": "MODELED",
        "provenance": _provenance(src, "MODELED", retrieved),
        "imagery_provenance": _provenance(src, "SNAPSHOT", retrieved),
        "scene": _scene_block(scene, "sentinel-1"),
        "bbox": bbox,
        "crop": {"file": crop_file, "url": CROP_URL_PREFIX + crop_file,
                 "px": [cw, ch], "pixel_spacing_m": eff, "render": render},
        "detector": {
            "id": sar_mod.ALGORITHM_ID, "label": sar_mod.ALGORITHM_LABEL,
            "status": result["status"], "params": result["params"],
            "stats": result["stats"], "rejected": result["rejected"]},
        "counts": ({"targets_floor": len(items)} if served else None),
        **stamp({"targets_floor": len(items)} if served else None),
        "count_basis": COUNT_BASIS,
        "detections": items,
        "ship_detection_grade": src["ship_detection_grade"],
        "attribution": {"chip": src["attribution_chip"], "text": src["attribution"],
                        "licence": src["licence"]},
    }
    _atomic_write_text(record_path(sd, "sentinel-1", place),
                       json.dumps(rec, indent=1, sort_keys=True) + "\n")
    _drop_old_crop(sd, prev, crop_file)
    return rec


# ---------------------------------------------------------------------------
# Optical crops (NAIP, Landsat): imagery only
# ---------------------------------------------------------------------------

def _area(b: list) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _intersect(a: list, b: list) -> list:
    return [max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])]


def pick_naip(scenes: list[dict], bbox: list,
              window_m: float = NAIP_WINDOW_M) -> Optional[tuple[dict, float, list]]:
    """Choose the NAIP tile and the crop box for a place.

    NAIP ships as ~7 km quarter-quad tiles, so a place is covered by
    several; the crop is a ``window_m`` window about the place centre.
    Tiles covering at least half as much of the window as the best tile
    are comparable; among them one containing the centre wins, then the
    newest acquisition. Returns ``(scene,
    cover_fraction, crop_bbox)`` where ``cover_fraction`` is the share of
    the window the tile actually covers, or None when that is below
    MIN_NAIP_COVER (nothing honest to show).
    """
    centre = ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)
    window = ms.centre_window(bbox, window_m)
    rows = []
    for s in scenes:
        fb = s.get("footprint_bbox")
        if not fb:
            continue
        inter = _intersect(fb, window)
        overlap = _area(inter) / _area(window) if _area(window) else 0.0
        if overlap <= 0:
            continue
        has_centre = fb[0] <= centre[0] <= fb[2] and fb[1] <= centre[1] <= fb[3]
        rows.append((has_centre, overlap, s.get("datetime") or "", s, inter))
    if not rows:
        return None
    # Tiles that cover at least half as much as the best one are comparable;
    # among those the newest acquisition wins (flights differ in tile extent).
    top = max(r[1] for r in rows)
    comparable = [r for r in rows if r[1] >= 0.5 * top]
    best = max(comparable, key=lambda r: (r[0], r[2], r[1]))
    if best[1] < MIN_NAIP_COVER:
        return None
    return best[3], best[1], best[4]


def crop_optical(source_id: str, place: str, aoi: dict, satwatch_dir: Path, *,
                 http: HttpFn = default_http, days: Optional[int] = None) -> dict:
    """NAIP or Landsat crop for ``place``: a stored scene image, nothing
    detected. NAIP is US-only and answers ``outside_coverage`` elsewhere."""
    if source_id not in OPTICAL_SOURCES:
        raise MultiError("bad_source", f"{source_id!r} has no crop product")
    src = src_mod.get_source(source_id)
    bbox = list(aoi["core_bbox"])
    if not src_mod.covered_by_region(source_id, bbox):
        raise MultiError("outside_coverage", f"{src['label']} covers the US only")
    try:
        if source_id == "naip":
            scenes = ms.search("naip", ms.centre_window(bbox, NAIP_WINDOW_M),
                               days=None, http=http)
            picked = pick_naip(scenes, bbox)
            if picked is None:
                raise MultiError("no_scene", "no NAIP tile covers this place")
            scene, cover, crop_bbox = picked
            target_m = 1.0
        else:
            scenes = ms.covering(ms.search("landsat", bbox, days=days, http=http), bbox)
            if not scenes:
                raise MultiError("no_scene", "no Landsat scene covers this place in the window")
            scene, cover = scenes[0], 1.0
            crop_bbox = bbox
            target_m = float(src["resolution_m"])
        w, h, eff = ms.crop_size_px(crop_bbox, target_m, max_px=OPTICAL_MAX_PX)
        png = ms.fetch_crop_bytes(source_id, crop_bbox, scene["id"], w, h, http=http)
    except StacError as exc:
        code = "feed_down" if "search" in str(exc) else "crop_not_served"
        raise MultiError(code, str(exc)) from exc

    retrieved = _now()
    prefix = "naip" if source_id == "naip" else "landsat"
    crop_file = _crop_name(prefix, place, scene["date"])
    sd = Path(satwatch_dir)
    prev = load_record(sd, source_id, place)
    _ensure_dirs(sd)
    _atomic_write_bytes(sd / MULTI_DIR / crop_file, png)
    rec = {
        "kind": KIND, "contract": CONTRACT, "source": source_id, "place": place,
        "name": aoi.get("name"), "retrieved_at": retrieved,
        "imagery_mode": "SNAPSHOT", "mode": "SNAPSHOT",
        "provenance": _provenance(src, "SNAPSHOT", retrieved),
        "scene": _scene_block(scene, source_id),
        "bbox": [round(v, 6) for v in crop_bbox],
        "place_bbox": bbox,
        "cover_pct": round(100.0 * cover, 1),
        "crop": {"file": crop_file, "url": CROP_URL_PREFIX + crop_file,
                 "px": [w, h], "pixel_spacing_m": eff,
                 "window_m": NAIP_WINDOW_M if source_id == "naip" else None},
        "detector": {"id": None, "label": None, "status": DETECTOR_STATUS_NOT_RUN},
        "counts": None, "count_basis": None, "detections": [],
        "ship_detection_grade": src["ship_detection_grade"],
        "attribution": {"chip": src["attribution_chip"], "text": src["attribution"],
                        "licence": src["licence"]},
    }
    _atomic_write_text(record_path(sd, source_id, place),
                       json.dumps(rec, indent=1, sort_keys=True) + "\n")
    _drop_old_crop(sd, prev, crop_file)
    return rec
