"""Muse Spark vision read of ONE stored NATIVE satellite crop.

Pure logic over the one provider seam (``LLMProvider.complete(..., image=png)``,
prompt ``prompts/satvision.txt``). Crop resolution is shared with the SAM
module (``satwatch/sat_ai.py``, which itself imports no llm module); the HTTP
surface is ``satvision_routes.py``. Honesty rules as in sat_ai: native pixels
only, everything MODELLED and display-only, RECORDED means a stored real reply
for the exact crop (sha256), LIVE only for a call made now, and spending needs
the admin bearer, a key, an explicit http transport and the caller's
confirmation of the shown estimate. A bare META_API_KEY never goes live.
"""
from __future__ import annotations

import json
import os
import re
from typing import Optional

try:                                     # repo root on sys.path (server.llm)
    from ..satwatch import sat_ai as ai
    from . import provenance as prov_mod
    from . import provider as llm_provider
    from . import satvision_fixture as fx_mod
except ImportError:                      # server/ on sys.path (llm)
    from satwatch import sat_ai as ai    # type: ignore
    from llm import provenance as prov_mod        # type: ignore
    from llm import provider as llm_provider      # type: ignore
    from llm import satvision_fixture as fx_mod   # type: ignore

VISION_CONTRACT = "satwatch-vision/1"

PROMPT = "satvision"
QUALITY = ("clear", "hazy", "cloud_affected", "unusable")
LEVELS = ("sparse", "moderate", "dense", "cannot_assess")
SERVER_LIMITS = ["model reading of one image, not a count and not ground truth"]
SERVER_CANNOT = ["vessel type, name, flag, cargo and loaded state"]
# A statement that gives a NUMBER of vessels (or of the dark shapes, objects and
# specks that stand for them) is removed. A quantity (digits, a number word, "a
# handful", "a couple", "a single", "dozens" ...) followed within three words by
# a vessel or shape noun, or a count label ("ships: 12", "count of 9"). Density
# words that give no number ("few", "many", "some") are left alone.
_NUM_WORDS = (r"zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|"
              r"fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|"
              r"eighty|ninety|hundred|thousand")
_VAGUE_QTY = (r"(?:a|the|only|just)\s+(?:single|lone|sole|couple|pair|handful|dozen|few|number|score|bunch)|"
              r"single|lone|sole|couple|pair|handful|dozens?|hundreds?|scores?|tens|several|numerous|"
              r"multiple|countless|a\s+few")
_NOUNS = (r"ships?|vessels?|boats?|hulls?|barges?|tankers?|carriers?|freighters?|bulkers?|craft|"
          r"container\s?ships?|cargo\s?ships?|shapes?|objects?|targets?|contacts?|silhouettes?|"
          r"blobs?|specks?|dots?|spots?|returns?|detections?|markers?|wakes?|hull\s?shapes?")
_QTY = rf"(?P<q>\d[\d,]*(?:\.\d+)?\+?|(?:{_NUM_WORDS})(?:[-\s](?:{_NUM_WORDS}))*|{_VAGUE_QTY})"
_COUNT_RE = re.compile(
    rf"\b{_QTY}(?:[\s-]+[A-Za-z]+){{0,3}}?[\s-]+(?:of\s+)?(?:{_NOUNS})\b", re.I)
_APPROX = r"(?:approximately|approx\.?|about|around|roughly|circa|c\.|~|some|nearly|almost|over|under)?\s*"
# "Ships: 12", "Ships: approximately 12", "Ships (12)", "Ships numbering 12", "Vessels x12", "Count: 12", "x12 vessels"
_LABEL_RE = re.compile(
    rf"\b(?:{_NOUNS})\s*(?:[:=]|\(|[x×]|(?:numbering|numbers?|totall?ing|totals?|amounting\s+to)\b)\s*{_APPROX}~?\d|"
    rf"\b(?:count|total|tally)\s*[:=]?\s*(?:of\s+)?(?:\w+\s+){{0,2}}?{_APPROX}\d|"
    rf"\b[x×]\s?\d+\s+(?:{_NOUNS})\b|"
    rf"\b(?:\d[\d,]*|(?:{_NUM_WORDS}))\s+of\s+(?:them|these|those)\b", re.I)
# an upper-case Roman numeral is a number too: "XII vessels"
_ROMAN_RE = re.compile(rf"\b[IVXLCDM]{{2,}}\b(?:[\s-]+[A-Za-z]+){{0,2}}?[\s-]+(?:{_NOUNS})\b")
_UNIT_AFTER = re.compile(r"^\s*(?:m|km|cm|mm|px|pixels?|metres?|meters?|nm|%|percent|deg|degrees?|x|"
                         r"days?|hours?|h|d|s|seconds?)\b", re.I)


def states_vessel_count(text: str) -> bool:
    """True when ``text`` gives a number of vessels or of shapes on the water."""
    if _LABEL_RE.search(text) or _ROMAN_RE.search(text):
        return True
    for m in _COUNT_RE.finditer(text):
        q = m.group("q")
        if q[:1].isdigit() and _UNIT_AFTER.match(text[m.end("q"):]):
            continue                      # "10 m per pixel ... ships": a measure, not a count
        return True
    return False


class VisionReplyError(ValueError):
    """The model reply broke the schema: nothing is shown."""


def _strs(v, field: str, max_items: int, max_len: int = 200) -> list[str]:
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise VisionReplyError(f"{field} is not a list of strings")
    return [" ".join(x.split())[:max_len] for x in v if x.strip()][:max_items]


def validate_read(text: str) -> dict:
    """Parse and re-validate the model's JSON (never trust constrained
    decoding alone). Strings that state a number of vessels (in the scene, the
    features, the cues, ``cannot_see`` and ``limits``) are removed and counted
    in ``post_check``; an empty cannot_see / limits is filled from the
    server's fixed statements and flagged."""
    try:
        doc = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise VisionReplyError("the reply is not JSON") from exc
    if not isinstance(doc, dict):
        raise VisionReplyError("the reply is not a JSON object")
    scene = doc.get("scene")
    if not isinstance(scene, str) or not scene.strip():
        raise VisionReplyError("scene missing")
    if doc.get("image_quality") not in QUALITY:
        raise VisionReplyError("image_quality outside the allowed words")
    if doc.get("congestion_level") not in LEVELS:
        raise VisionReplyError("congestion_level outside the allowed words")
    conf = doc.get("confidence")
    if isinstance(conf, bool) or not isinstance(conf, (int, float)) or conf != conf:
        raise VisionReplyError("confidence is not a number")
    feats = doc.get("visible_features")
    if not isinstance(feats, list):
        raise VisionReplyError("visible_features is not a list")
    features = []
    for f in feats[:8]:
        if not isinstance(f, dict) or not isinstance(f.get("label"), str) or not isinstance(f.get("detail"), str):
            raise VisionReplyError("a visible feature is malformed")
        features.append({"label": " ".join(f["label"].split())[:80], "detail": " ".join(f["detail"].split())[:200]})
    cues = _strs(doc.get("congestion_cues"), "congestion_cues", 5)
    cannot = _strs(doc.get("cannot_see"), "cannot_see", 8)
    limits = _strs(doc.get("limits"), "limits", 8)
    removed = 0

    def clean(items):
        nonlocal removed
        out = []
        for x in items:
            if states_vessel_count(x):
                removed += 1
            else:
                out.append(x)
        return out
    scene_text = " ".join(scene.split())[:300]
    if states_vessel_count(scene_text):
        removed += 1
        scene_text = "Crop read; a sentence stating a vessel number was removed."
    kept = []
    for f in features:
        if states_vessel_count(f["label"]) or states_vessel_count(f["detail"]):
            removed += 1
        else:
            kept.append(f)
    cues = clean(cues)
    cannot = clean(cannot)
    limits = clean(limits)
    post = []
    if removed:
        post.append(f"removed {removed} statement(s) giving a number of vessels")
    added_by_server = False
    if not cannot:
        cannot, added_by_server = list(SERVER_CANNOT), True
    if not limits:
        limits, added_by_server = list(SERVER_LIMITS), True
    if added_by_server:
        post.append("cannot_see / limits were empty and were filled by the server")
    return {
        "scene": scene_text, "image_quality": doc["image_quality"],
        "visible_features": kept, "congestion_cues": cues,
        "congestion_level": doc["congestion_level"],
        "confidence": max(0.0, min(1.0, float(conf))),
        "cannot_see": cannot, "limits": limits,
        "post_check": post,
    }


def _vision_payload(c: dict) -> dict:
    return {"source": c["source_label"], "place": c["name"] or c["place"],
            "scene_date": c["scene_date"], "resolution_m_per_pixel": c["resolution_m"],
            "crop_px": c["px"], "image_basis": ai.IMAGE_BASIS, "image_sha256": c["sha256"]}


def vision_recorded_entry(sha256: str) -> Optional[dict]:
    return fx_mod.recorded_entry(sha256)


def _llm_live_state(provider) -> dict:
    st = provider.status()
    if provider.transport_name != "http":
        has_key = bool(os.environ.get(llm_provider.META_KEY_ENV)
                       or os.environ.get("SUPERMAP_LLM_API_KEY"))
        if not has_key:
            return {"state": "AWAITING_META_KEY", "reason": "set META_API_KEY", "admin_required": True}
        return {"state": "NOT_SERVED", "reason": "live transport not enabled (SUPERMAP_LLM_TRANSPORT)",
                "admin_required": True}
    if st["disabled"]:
        return {"state": "NOT_SERVED", "reason": "LLM calls disabled", "admin_required": True}
    if st["cap_reached"]:
        return {"state": "NOT_SERVED", "reason": "daily LLM cap reached", "admin_required": True}
    return {"state": "READY", "reason": "live transport on", "admin_required": True}


def vision_plan(c: dict, *, provider=None) -> dict:
    provider = provider or llm_provider.LLMProvider.from_env()
    payload = _vision_payload(c)
    # The estimate is what a LIVE Muse Spark call would cost, at Meta's rates,
    # whatever transport is selected (the fixture transport has placeholder
    # rates). The estimator never sends anything.
    estimator = llm_provider.LLMProvider(
        transport="http", endpoint=llm_provider.META_ENDPOINT,
        api_key="estimate-only", model=llm_provider.META_MODEL,
        spend_path=provider.spend_path)
    prev = estimator.cost_preview(PROMPT, payload, c["bytes"])
    st = provider.status()
    entry = vision_recorded_entry(c["sha256"])
    cap = provider.cap_usd
    return {
        "contract": VISION_CONTRACT, **ai.crop_view(c),
        "recorded": ({"available": True, "recorded_at": entry.get("recorded_at"),
                      "model": entry.get("model")} if entry
                     else {"available": False, "recorded_at": None, "model": None}),
        "live": _llm_live_state(provider),
        "model": llm_provider.META_MODEL,
        "est_cost_usd": prev["estimated_cost_usd"], "cost_basis": "ESTIMATED",
        "estimated_tokens_in": prev["estimated_tokens_in"],
        "estimated_tokens_out": prev["estimated_tokens_out"],
        "usage": {"spent_usd": st["spent_usd"], "cap_usd": cap,
                  "remaining_usd": (round(max(0.0, cap - st["accrued_usd"]), 6) if cap is not None else None),
                  "calls_today": st["calls_today"], "cap_reached": st["cap_reached"],
                  "disabled": st["disabled"]},
        "label": ai.LABEL_MODELLED,
    }


def vision_run(c: dict, *, mode: str, confirm_cost_usd=None, provider=None) -> dict:
    payload = _vision_payload(c)
    if mode == "recorded":
        entry = vision_recorded_entry(c["sha256"])
        if entry is None:
            raise ai.SatAiError("not_recorded", "no recorded vision read for this crop", 404)
        try:
            # recorded-only provider: its own spend file and no cap, so a public
            # replay can never accrue against (or be refused by) the live cap
            res = llm_provider.LLMProvider.recorded().complete(
                PROMPT, payload, image=c["bytes"])
        except fx_mod.NoRecordedRead as exc:
            raise ai.SatAiError("not_recorded", str(exc), 404)
        live = False
    else:
        provider = provider or llm_provider.LLMProvider.from_env()
        if provider.transport_name != "http":
            raise ai.SatAiError("live_not_configured", "the live transport is not enabled", 409)
        plan = vision_plan(c, provider=provider)
        if not ai.close_enough(confirm_cost_usd, plan["est_cost_usd"]):
            raise ai.SatAiError("cost_not_confirmed",
                             f"confirm_cost_usd must equal the estimate ({plan['est_cost_usd']})", 422)
        try:
            res = provider.complete(PROMPT, payload, image=c["bytes"])
        except llm_provider.CapExceeded as exc:
            raise ai.SatAiError("llm_cap", str(exc), 429)
        except llm_provider.ProviderDisabled as exc:
            raise ai.SatAiError("llm_disabled", str(exc), 409)
        except Exception as exc:                       # transport / HTTP failure
            raise ai.SatAiError("vision_call_failed", f"the vision call failed ({type(exc).__name__})", 502)
        entry, live = None, True
    try:
        read = validate_read(res["text"])
    except VisionReplyError as exc:
        raise ai.SatAiError("vision_bad_reply", f"the model reply was refused: {exc}", 502)
    prov = prov_mod.from_result(res, PROMPT)
    prov.update({"mode": "MODELED", "ground_truth": False, "image_basis": ai.IMAGE_BASIS,
                 "structured_output": bool(res.get("structured_output"))})
    if not live:
        prov["note"] = (f"recorded real {entry.get('model')} reply"
                        + (f" of {entry.get('recorded_at')}" if entry.get("recorded_at") else "")
                        + "; replayed for this exact crop, not a call made now")
        prov["recorded_model"] = entry.get("model")
        prov["recorded_at"] = entry.get("recorded_at")
        if entry.get("prompt_id"):      # the prompt version the reply was recorded with, not today's
            prov["prompt_id"] = entry["prompt_id"]
            prov["prompt_version"] = entry["prompt_id"].rsplit("@v", 1)[-1]
    return {
        "contract": VISION_CONTRACT, **ai.crop_view(c),
        "mode": "LIVE" if live else "RECORDED", "read": read, "label": ai.LABEL_MODELLED,
        "image_basis": ai.IMAGE_BASIS, "provenance": prov,
        "counts_feed_signals": False,
    }
