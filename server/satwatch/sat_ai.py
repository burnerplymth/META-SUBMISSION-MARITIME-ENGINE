"""Meta AI on one NATIVE satellite crop: stored-crop resolution and SAM 3.1
segmentation. Pure logic; the HTTP surface is ``sat_ai_routes.py``.
The Muse Spark vision read of the same crop lives in ``llm/satvision.py``
(this package imports no llm module; it shares ``resolve_crop`` below).

Honesty rules (each enforced here):

* NATIVE pixels only. The crop that is sent (or looked up) is the stored file
  of a stored scan record; the browser-side ENHANCED VIEW never reaches this
  module and a file whose name marks it enhanced is refused.
* Everything that comes out is MODELLED. A SAM result is "SAM found N" (what
  the detector returned for one concept phrase), never "there are N"; a zero
  is "SAM found none". Recall at 10 m is unvalidated (SAM returned zero matches
  on the three native Sentinel-2 ship crops tried live, see META-API-DOCS-NOTES). Results are display-only: nothing here
  writes a scan record, a count that feeds ``signals`` or an event.
* RECORDED means a stored real reply replayed for the exact crop (matched by
  the sha256 of its bytes) and the exact phrase it was recorded with. LIVE
  means a real call made now. A recorded reply is never labelled live and a
  crop with no recorded reply is NOT SERVED, never a stand-in.
* Spending needs three things: the admin bearer, a key, and the caller's
  ``confirm_cost_usd`` equal to the estimate the plan showed. The SAM meter
  (per-day cap, ledger) applies to a SAM call; it is separate from the LLM spend
  ledger, which applies only to the Muse Spark vision read.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional

from . import multiscan as multi_mod
from . import overlay as overlay_mod
from . import sam31 as sam31_mod
from . import sam_meter as sam_meter_mod
from . import sources as src_mod
from .detect import MIN_COUNTABLE_LONG_SIDE_PX, MetaSamDetector, SYNTHETIC_NAME_MARKERS, _provider_state, REGISTRY

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SAM_FIXTURES = FIXTURES / "sam"

SAM_CONTRACT = "satwatch-sam/1"
LABEL_MODELLED = "MODELLED"
# every SAM-derived count wears this until a human has reviewed that place (nothing sets human_reviewed yet)
VERIFICATION = "Detected, not verified"
IMAGE_BASIS = "native crop pixels (no enhancement, no upscaling)"
ROLES = ("now", "baseline")
VESSEL_PROMPTS = ("container ship", "cargo vessel")
# Phrases the desk may send, per source. NAIP is port-layout reference
# imagery: "building" is the one phrase with a recorded real reply there.
SAM_PROMPTS = {"sentinel-2": VESSEL_PROMPTS, "naip": ("building",)}
VISION_SOURCES = ("sentinel-2", "naip", "landsat")


class SatAiError(Exception):
    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code, self.status = code, status


# ---------------------------------------------------------------------------
# Crop resolution (from the STORED record; nothing the client names is opened)
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> Optional[dict]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return doc if isinstance(doc, dict) else None


def resolve_crop(satwatch_dir: Path, source: str, place: str, role: Optional[str]) -> dict:
    """The stored native crop for (source, place[, role]): path, sha256 and
    the context the vision prompt needs. SatAiError when there is none."""
    from .routes import NODE_RE
    sd = Path(satwatch_dir)
    if not isinstance(place, str) or not NODE_RE.match(place):
        raise SatAiError("invalid_request", "place must be a place id", 422)
    if source not in VISION_SOURCES:
        raise SatAiError("bad_source", f"source must be one of {list(VISION_SOURCES)}", 422)
    if source == "sentinel-2":
        if role not in ROLES:
            raise SatAiError("invalid_request", "role must be 'now' or 'baseline'", 422)
        rec = _read_json(sd / f"{place}.json")
        if not rec or rec.get("kind") != "satwatch_scan" or not (rec.get("crops") or {}).get(role):
            raise SatAiError("crop_not_found", f"no stored Sentinel-2 {role} crop for {place!r}", 404)
        file = rec["crops"][role]
        path = sd / file
        scene_date = (rec.get("imagery_dates") or {}).get(role)
        resolution = rec.get("resolution_m") or 10
        name = rec.get("name")
    else:
        rec = multi_mod.load_record(sd, source, place)
        crop = (rec or {}).get("crop") or {}
        if not rec or not crop.get("file"):
            raise SatAiError("crop_not_found", f"no stored {source} crop for {place!r}", 404)
        file = crop["file"]
        path = sd / multi_mod.MULTI_DIR / file
        scene_date = (rec.get("scene") or {}).get("date")
        resolution = crop.get("pixel_spacing_m") or src_mod.get_source(source)["resolution_m"]
        name = rec.get("name")
    if file != Path(file).name or Path(file).suffix != ".png" or not path.is_file():
        raise SatAiError("crop_not_found", f"crop file for {place!r} is not served", 404)
    lowered = file.lower()
    if any(m in lowered for m in SYNTHETIC_NAME_MARKERS):
        raise SatAiError("synthetic_crop", "enhanced or synthetic imagery never feeds Meta AI", 422)
    data = path.read_bytes()
    size = overlay_mod.png_size(path)
    if size is None:
        raise SatAiError("crop_not_found", "the stored crop is not a readable PNG", 404)
    return {
        "source": source, "place": place, "role": role if source == "sentinel-2" else None,
        "name": name, "file": file, "path": path, "bytes": data,
        "sha256": hashlib.sha256(data).hexdigest(), "px": [size[0], size[1]],
        "scene_date": scene_date, "resolution_m": resolution,
        "source_label": src_mod.get_source(source)["label"],
    }


def crop_view(c: dict) -> dict:
    url = (f"/satwatch/crops/{c['file']}" if c["source"] == "sentinel-2"
           else f"/satwatch/multi/crops/{c['file']}")
    return {"source": c["source"], "place": c["place"], "role": c["role"], "name": c["name"],
            "crop_id": c["file"], "crop_url": url, "crop_px": c["px"],
            "scene_date": c["scene_date"], "resolution_m": c["resolution_m"]}


def close_enough(a, b, tol=1e-6) -> bool:
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# SAM 3.1
# ---------------------------------------------------------------------------

def sam_manifest(directory: Optional[Path] = None) -> list[dict]:
    d = Path(directory or SAM_FIXTURES)
    doc = _read_json(d / "manifest.json") or {}
    return [e for e in doc.get("entries") or [] if isinstance(e, dict)]


def sam_recorded_entry(sha256: str, prompt: str, directory: Optional[Path] = None) -> Optional[dict]:
    for e in sam_manifest(directory):
        if e.get("crop_sha256") == sha256 and e.get("prompt") == prompt:
            return e
    return None


def _sam_live_state() -> dict:
    state, reason = _provider_state(REGISTRY["meta-sam"])
    return {"state": state, "reason": reason, "admin_required": True}


def sam_usage() -> dict:
    return sam_meter_mod.SamMeter().status()


def sam_plan(c: dict, prompt: Optional[str], fixtures: Optional[Path] = None) -> dict:
    allowed = SAM_PROMPTS.get(c["source"])
    if not allowed:
        raise SatAiError("sam_not_offered", f"SAM is not offered on {c['source']} crops", 422)
    prompt = prompt or allowed[0]
    if prompt not in allowed:
        raise SatAiError("invalid_request", f"prompt must be one of {list(allowed)}", 422)
    entry = sam_recorded_entry(c["sha256"], prompt, fixtures)
    usage = sam_usage()
    return {
        "contract": SAM_CONTRACT, **crop_view(c), "prompt": prompt, "prompts": list(allowed),
        "recorded": ({"available": True, "recorded_at": entry.get("recorded_at")} if entry
                     else {"available": False, "recorded_at": None}),
        "live": _sam_live_state(),
        "images": 1, "est_cost_usd": round(sam31_mod.PRICE_PER_IMAGE_USD, 6),
        "price_per_1000_images_usd": sam31_mod.PRICE_PER_1000_IMAGES_USD,
        "cost_basis": "ESTIMATED",
        "usage": {
            "calls_today": usage["calls_today"], "cap_calls_per_day": usage["cap_calls_per_day"],
            "remaining_calls_today": usage.get("remaining_calls_today"),
            "cost_usd_today": usage["cost_usd_today"], "cap_reached": usage["cap_reached"],
            "disabled": usage["disabled"], "ledger_ok": usage["ledger_ok"],
        },
        "label": LABEL_MODELLED,
    }


def _object(det: MetaSamDetector, rec, prompt: str, idx: int, role: Optional[str], vessel: bool, w: int, h: int) -> dict:
    raw = det._to_raw(rec, prompt)
    mask = raw.get("mask")
    long_px = max(rec.box_w, rec.box_h)
    return {
        "id": f"sam:{prompt}:{idx:03d}", "label": prompt, "score": raw.get("score"),
        "localization": "mask" if mask else "bbox", "bbox": raw["bbox"], "point": None,
        "mask": mask, "coord_space": "norm1000", "crop_px": [w, h], "role": role or "now",
        "long_side_px": long_px,
        "below_floor": (long_px < MIN_COUNTABLE_LONG_SIDE_PX) if vessel else None,
        "note": raw.get("note"),
    }


def _sam_result(c: dict, prompt: str, reply, *, mode: str, entry: Optional[dict],
                images: int, est_cost: float, response_id: Optional[str]) -> dict:
    w, h = c["px"]
    det = MetaSamDetector(api_key="", meter=sam_meter_mod.SamMeter())
    vessel = prompt in VESSEL_PROMPTS
    objs = []
    for i, rec in enumerate(sorted(reply.records, key=lambda r: -(r.confidence or 0.0)), 1):
        if rec.frame != 0 or (rec.w, rec.h) != (w, h):
            raise sam31_mod.SamResponseError(
                f"reply frame does not match the {w}x{h} crop that was sent")
        objs.append(_object(det, rec, prompt, i, c["role"], vessel, w, h))
    live = mode == "LIVE"
    prov = {
        "model": sam31_mod.MODEL if live else "fixture-transport (no live model)",
        "transport": "LIVE" if live else "RECORDED", "transport_raw": "http" if live else "fixture",
        "live": live, "prompt_id": f"sam-3.1@concept:{prompt}", "prompt_version": None,
        "cost_usd": est_cost if live else 0.0, "estimated_cost_usd": round(sam31_mod.PRICE_PER_IMAGE_USD, 6),
        "cached": False,
        "source": sam31_mod.SOURCE_LABEL, "mode": "MODELED", "ground_truth": False,
        "recall_validated": False, "image_basis": IMAGE_BASIS,
        "images_processed": images, "response_id": response_id,
        "cost_basis": "ESTIMATED: images x $2.50 per 1,000 (published price)",
    }
    if not live:
        prov["note"] = (f"recorded real {sam31_mod.MODEL} reply"
                        + (f" of {entry.get('recorded_at')}" if entry else "")
                        + "; replayed for this exact crop, not a call made now")
        prov["recorded_model"] = sam31_mod.MODEL
        prov["recorded_at"] = entry.get("recorded_at") if entry else None
    return {
        "contract": SAM_CONTRACT, **crop_view(c), "prompt": prompt,
        "mode": mode, "found": len(objs),
        "below_floor": (sum(1 for o in objs if o["below_floor"]) if vessel else None),
        "objects": objs, "coord_space": "norm1000", "label": LABEL_MODELLED,
        "image_basis": IMAGE_BASIS, "provenance": prov,
        "verification": VERIFICATION, "human_reviewed": False,
        "counts_feed_signals": False,
    }


def sam_run_recorded(c: dict, prompt: str, fixtures: Optional[Path] = None) -> dict:
    plan = sam_plan(c, prompt, fixtures)        # validates source and phrase
    prompt = plan["prompt"]
    entry = sam_recorded_entry(c["sha256"], prompt, fixtures)
    if entry is None:
        raise SatAiError("not_recorded", "no recorded SAM reply for this crop and phrase", 404)
    d = Path(fixtures or SAM_FIXTURES)
    try:
        reply = sam31_mod.parse_reply((d / entry["reply"]).read_text(encoding="utf-8"))
        return _sam_result(c, prompt, reply, mode="RECORDED", entry=entry,
                           images=reply.images_processed or 1, est_cost=0.0,
                           response_id=reply.response_id)
    except (OSError, KeyError, sam31_mod.SamError) as exc:
        raise SatAiError("recorded_unreadable", f"the recorded reply could not be read ({type(exc).__name__})", 502)


def sam_run_live(c: dict, prompt: str, confirm_cost_usd, *, http=None, meter=None) -> dict:
    plan = sam_plan(c, prompt)
    prompt = plan["prompt"]
    if not close_enough(confirm_cost_usd, plan["est_cost_usd"]):
        raise SatAiError("cost_not_confirmed",
                         f"confirm_cost_usd must equal the estimate ({plan['est_cost_usd']})", 422)
    key = sam31_mod.resolve_key()
    if not key:
        raise SatAiError("awaiting_meta_key", "no Meta API key is configured", 409)
    det = MetaSamDetector(api_key=key, http=http, meter=meter or sam_meter_mod.SamMeter())
    try:
        png, w, h = det._read_native(str(c["path"]))
        reply = det._call(prompt, png)
        images = reply.images_processed if reply.images_processed is not None else 1
        return _sam_result(c, prompt, reply, mode="LIVE", entry=None, images=images,
                           est_cost=round(images * sam_meter_mod.PRICE_PER_IMAGE_USD, 6),
                           response_id=reply.response_id)
    except sam_meter_mod.SamCapError as exc:
        raise SatAiError("sam_cap", str(exc), 429)
    except sam31_mod.SamCallError as exc:
        raise SatAiError("sam_call_failed", str(exc), 502)
    except sam31_mod.SamError as exc:
        raise SatAiError("sam_bad_reply", str(exc), 502)
