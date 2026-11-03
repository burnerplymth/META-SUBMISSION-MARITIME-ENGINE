"""LIVE SAM on one historical Sentinel-2 scene: fetch the native crop, call SAM through the existing
meter, return a count record. Used by POST /research/scenes/scan (mode live, gated).

Native pixels only: the crop is the Planetary Computer titiler render of the scene's ``visual`` asset
at its native 10 m grid, no enhancement and no upscaling. Imports of satwatch are lazy so the research
package does not need them to serve recorded data.
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone

from .util import ResearchError


def run_live_sam(bundle, scene: dict, prompt: str, *, http=None, crop_http=None, meter=None) -> dict:
    from satwatch import multistac, sam31, sam_meter
    from satwatch.detect import MIN_COUNTABLE_LONG_SIDE_PX, MetaSamDetector

    if os.environ.get("SUPERMAP_BLOCK_NETWORK") == "1":     # the crop fetch below is a network call
        raise ResearchError("network_blocked", "live scans need network access and SUPERMAP_BLOCK_NETWORK=1 is set", 409)
    key = sam31.resolve_key()
    if not key:
        raise ResearchError("awaiting_meta_key", "no Meta API key is configured", 409)
    bbox = bundle.scene_bbox(scene["place"], scene["source"])
    if not bbox:
        raise ResearchError("scene_not_found", "no crop box is recorded for this scene", 404)
    w, h, eff = multistac.crop_size_px(bbox, 10.0)
    try:
        png = multistac.fetch_crop_bytes("sentinel-2", bbox, scene["scene_id"], w, h,
                                         http=crop_http or multistac.default_http)
    except Exception as exc:  # noqa: BLE001
        raise ResearchError("crop_failed", f"the scene crop could not be fetched ({type(exc).__name__})", 502)
    det = MetaSamDetector(api_key=key, http=http, meter=meter or sam_meter.SamMeter())
    fd, path = tempfile.mkstemp(prefix="research_scene_", suffix=".png")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(png)
        data, pw, ph = det._read_native(path)
        try:
            reply = det._call(prompt, data)
        except sam_meter.SamCapError as exc:
            raise ResearchError("sam_cap", str(exc), 429)
        except sam31.SamCallError as exc:
            raise ResearchError("sam_call_failed", str(exc), 502)
        except sam31.SamError as exc:
            raise ResearchError("sam_bad_reply", str(exc), 502)
    except sam31.SamError as exc:
        raise ResearchError("crop_unusable", str(exc), 422)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    for rec in reply.records:
        if rec.frame != 0 or (rec.w, rec.h) != (pw, ph):
            raise ResearchError("sam_bad_reply", "reply frame does not match the crop that was sent", 502)
    below = sum(1 for r in reply.records if max(r.box_w, r.box_h) < MIN_COUNTABLE_LONG_SIDE_PX)
    return {
        "scene_id": scene["scene_id"], "place": scene["place"], "source": scene["source"], "detector": "sam",
        "prompt": prompt, "count": len(reply.records), "below_floor": below,
        "note": ("SAM found %d on a native 10 m crop; recall at 10 m is unvalidated and a count is a floor, "
                 "not the number of vessels" % len(reply.records)),
        "recorded_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "model": sam31.MODEL, "crop_bbox": bbox, "crop_px": [pw, ph],
        "images_processed": reply.images_processed, "response_id": reply.response_id,
        "est_cost_usd": round(sam_meter.PRICE_PER_IMAGE_USD, 6),
    }
