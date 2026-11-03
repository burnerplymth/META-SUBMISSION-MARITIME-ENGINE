"""Vessel detection behind a provider registry (``SATWATCH_DETECTOR``).

ONE interface, one registry, the transport chosen by environment:

    detector.detect(image_path, aoi) -> DetectionResult      # ONE crop

``scan.py`` calls it for BOTH the baseline and the now crop (same
detector, same prompts, same thresholds) and composes the delta; a
detector never sees the other crop and never has to know about pairs.

Providers (``SATWATCH_DETECTOR`` value -> class):

=====================  ===============================================
``none`` (default)     NoDetector: AWAITING_META_KEY, counts null.
                       Alias ``fixture``.
``reference-counts``   ReferenceCountsDetector: OFFLINE replay of reference
                       counts recorded from the baked crops (offline
                       replay; not a live detection). Labelled
                       REFERENCE COUNTS - OFFLINE; never Meta, never
                       live. Its counts may NOT mint production signals.
                       Alias ``reference-counts-offline``.
``meta-sam``           MetaSamDetector: SAM 3.1 (``sam-3.1``) on the Meta
                       Model API, POST /v1/responses (see ``sam31.py``
                       for the verified wire contract). Live only when
                       SATWATCH_DETECTOR=meta-sam AND a key is present
                       (META_API_KEY / META_MODEL_API_KEY /
                       SUPERMAP_META_API_KEY); every call is metered and
                       capped per day (``sam_meter.py``). Alias ``meta``.
``open-weights-sam``   OpenWeightsSamDetector: client for OUR OWN
                       ``satwatch-sam-worker/1`` HTTP contract, i.e. a
                       self-hosted ``facebook/sam3.1`` behind Modal /
                       Baseten / any GPU host. The worker is not built
                       here; with no endpoint configured it reports
                       AWAITING. Alias ``sam-open``.
=====================  ===============================================

Doctrine: counts are never invented. A provider that has not run
returns ``counts=None`` and ``status=AWAITING_META_KEY``; every consumer
(signals, brief, overlay) refuses to proceed on null counts.

Detections: each provider returns RAW detections in norm1000 space
(see ``overlay.py``); the pipeline adds crop/scene/visibility envelope
and validates against the overlay contract.
"""
from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Protocol

from . import overlay as overlay_mod
from . import sam31 as sam31_mod
from . import sam_meter as sam_meter_mod

# Concept prompts for the detector. The countable
# class is large vessels only (>= ~150 m LOA at 10 m/px Sentinel-2).
CONCEPT_PROMPTS = ["container ship", "cargo vessel", "container stack", "gantry crane"]
COUNTABLE_CLASS_NOTE = "large vessels >= ~150 m LOA only (10 m/px Sentinel-2)"
# 150 m at 10 m/px = 15 px; a SAM box whose long side is below this is not
# a countable large vessel (same floor the reference counts applied).
MIN_COUNTABLE_LONG_SIDE_PX = 15

STATUS_AWAITING_KEY = "AWAITING_META_KEY"
STATUS_OK = "OK"
STATUS_PARTIAL = "PARTIAL"

ENV_SELECTOR = "SATWATCH_DETECTOR"


class DetectorConfigError(ValueError):
    """SATWATCH_DETECTOR names no registered provider."""


@dataclass
class DetectionResult:
    status: str                        # AWAITING_META_KEY | OK | PARTIAL
    provider: str                      # registry id
    counts: Optional[dict] = None      # {"vessels": int, ...} - None until a real detector ran
    detections: Optional[list] = None  # RAW per-object geometry (norm1000) or None
    prompts: list = field(default_factory=lambda: list(CONCEPT_PROMPTS))
    note: Optional[str] = None
    visibility: Optional[str] = None   # detector-stated visibility basis (free text)
    provider_label: Optional[str] = None
    per_crop: Optional[dict] = None    # set by scan.compose_detection (both crops)
    provenance: Optional[dict] = None  # source / model / mode / SAM call + cost accounting

    def to_dict(self) -> dict:
        out = {
            "overlay_contract": overlay_mod.CONTRACT_ID,
            "per_crop": self.per_crop,
            "status": self.status,
            "provider": self.provider,
            "provider_label": self.provider_label,
            "counts": self.counts,
            "detections": self.detections,
            "prompts": self.prompts,
            "countable_class": COUNTABLE_CLASS_NOTE,
            "note": self.note,
        }
        if self.provenance:      # only providers with a source/model/cost story
            out["provenance"] = self.provenance
        return out


class Detector(Protocol):
    name: str

    def detect(self, image_path: str, aoi: dict) -> DetectionResult:
        ...


def _awaiting(provider: str, label: str, note: str) -> DetectionResult:
    return DetectionResult(status=STATUS_AWAITING_KEY, provider=provider,
                           counts=None, detections=None, note=note,
                           provider_label=label)


# ---------------------------------------------------------------------------
# none
# ---------------------------------------------------------------------------

class FixtureDetector:
    """Keyless default: honestly reports that detection has not run.

    Returns NO detections and counts=None on every call.
    """

    name = "none"
    label = "NO DETECTOR - AWAITING META KEY"

    def detect(self, image_path: str, aoi: dict) -> DetectionResult:
        return _awaiting(
            self.name, self.label,
            "NOT RUN - detection awaits Meta API key")


NoDetector = FixtureDetector


# ---------------------------------------------------------------------------
# meta-sam (SAM 3.1 on the Meta Model API)
# ---------------------------------------------------------------------------

# PNGs whose name says they are not the stored native crop never go to
# detection (ENHANCED VIEW is browser-side only; this is a second wall).
SYNTHETIC_NAME_MARKERS = ("enhanc", "synthetic", "upscal", "sharpen", "denois",
                          "super_res", "superres")


def _box_iou(a: tuple, b: tuple) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


class MetaSamDetector:
    """SAM 3.1 (``sam-3.1``) on the Meta Model API: the real detector.

    Contract and wire format: ``sam31.py`` (verified against the docs and
    live replies). One call = ONE concept phrase on ONE image, so each
    crop costs ``len(prompts)`` calls (default 2: "container ship",
    "cargo vessel"; $2.50 per 1,000 images). The port-context phrases in
    CONCEPT_PROMPTS ("container stack", "gantry crane") are not vessels
    and are never counted; they are not called by default.

    Honesty rules (all enforced here):

    * NATIVE pixels only: the stored crop file is sent as is. A file whose
      name marks it enhanced / upscaled / synthetic is refused, and the
      browser-side ENHANCED VIEW never reaches the server.
    * Counts are DETECTIONS (mode MODELED), not ground truth, and carry
      source + model + the unvalidated-recall flag in ``provenance``.
    * 10 m Sentinel-2 is at the edge of what SAM can see: the live service
      returned zero matches on three native crops with visible ships and
      only returns objects it scores above ~0.5. A zero is "SAM found
      none", never "there are none".
    * Objects are kept only if their long side is >= MIN_COUNTABLE_LONG_SIDE_PX.
    * Anything unreadable raises (``sam31.SamError``) and nothing is
      counted; a refused call (daily cap, kill switch, unusable ledger)
      raises ``sam_meter.SamCapError`` before a byte is sent.
    * A mask that cannot be decoded keeps its valid box and loses only its
      outline (``localization`` falls back to bbox, noted per object).
    """

    name = "meta-sam"
    label = "META SAM 3.1"
    IMPLEMENTED = True
    VESSEL_PROMPTS = ("container ship", "cargo vessel")
    DEDUPE_IOU = 0.5
    MASK_EPS_PX = 0.7

    def __init__(self, api_key: Optional[str] = None, *, http=None, meter=None,
                 prompts=None, timeout: int = 120, outlines: Optional[bool] = None) -> None:
        # Polygon outlines are optional: counts and boxes never depend on the
        # mask codec. SATWATCH_SAM_OUTLINES=0 switches the codec off entirely.
        self.outlines = (os.environ.get("SATWATCH_SAM_OUTLINES", "1").strip().lower()
                         not in ("0", "off", "false", "no")
                         if outlines is None else bool(outlines))
        self.api_key = api_key if api_key is not None else sam31_mod.resolve_key()
        self._http = http
        self.meter = meter if meter is not None else sam_meter_mod.SamMeter()
        chosen = tuple(prompts) if prompts else self.VESSEL_PROMPTS
        bad = [p for p in chosen if p not in CONCEPT_PROMPTS]
        if bad:
            raise ValueError(f"prompts must come from CONCEPT_PROMPTS: {bad}")
        self.prompts = chosen
        self.timeout = timeout

    # ----- input ---------------------------------------------------------
    @staticmethod
    def _read_native(image_path: str) -> tuple[bytes, int, int]:
        p = Path(image_path)
        lowered = p.name.lower()
        if any(m in lowered for m in SYNTHETIC_NAME_MARKERS):
            raise sam31_mod.SamError(
                f"{p.name}: enhanced or synthetic imagery never feeds detection "
                "(native crop pixels only)")
        if not p.is_file():
            raise sam31_mod.SamError(f"crop not found: {p.name}")
        raw = p.read_bytes()
        if len(raw) > sam31_mod.MAX_IMAGE_BYTES:
            raise sam31_mod.SamError(f"{p.name}: larger than {sam31_mod.MAX_IMAGE_BYTES} bytes")
        size = overlay_mod.png_size(p)
        if size is None:
            raise sam31_mod.SamError(f"{p.name}: not a readable PNG")
        w, h = size
        if max(w, h) < MIN_COUNTABLE_LONG_SIDE_PX:
            raise sam31_mod.SamError(f"{p.name}: crop smaller than the countable size floor")
        return raw, w, h

    # ----- one metered call ---------------------------------------------
    def _call(self, prompt: str, png: bytes, mime: str = "image/png") -> "sam31_mod.SamReply":
        body = json.dumps(sam31_mod.build_request(prompt, png, mime=mime)).encode("utf-8")
        ref = self.meter.reserve(prompt=prompt)          # SamCapError: nothing sent
        http = self._http or sam31_mod.default_http
        try:
            status, text = http(sam31_mod.ENDPOINT, body,
                                sam31_mod.request_headers(self.api_key), self.timeout)
        except Exception as exc:                          # no HTTP answer at all
            self.meter.release(ref, f"transport: {type(exc).__name__}")
            raise sam31_mod.SamCallError(
                f"SAM request failed before an answer: {type(exc).__name__}") from exc
        if status != 200:
            self.meter.settle(ref, images_processed=None, http_status=status)
            raise sam31_mod.SamCallError(f"SAM HTTP {status}: {self._error_text(text)}")
        try:
            reply = sam31_mod.parse_reply(text)
        except sam31_mod.SamError:
            self.meter.settle(ref, images_processed=None, http_status=200)
            raise
        self.meter.settle(ref, images_processed=reply.images_processed,
                          http_status=200, response_id=reply.response_id)
        return reply

    @staticmethod
    def _error_text(text: str) -> str:
        try:
            doc = json.loads(text)
            err = doc.get("error") if isinstance(doc, dict) else None
            msg = (err.get("message") if isinstance(err, dict) else None) or text
        except ValueError:
            msg = text
        return " ".join(str(msg).split())[:200]

    # ----- mapping -------------------------------------------------------
    def _to_raw(self, rec: "sam31_mod.SamRecord", prompt: str) -> dict:
        w, h = rec.w, rec.h
        bbox = overlay_mod.bbox_from_xyxy_px(
            (rec.x1, rec.y1, rec.x2 + 1, rec.y2 + 1), w, h)
        conf = rec.confidence
        note = (f"{self.label} detection for '{prompt}'"
                + (f", confidence {conf:.2f}" if conf is not None else "")
                + "; model output, not ground truth")
        item = {"label": prompt, "score": conf, "bbox": bbox, "note": note}
        mask = rec.mask
        why = None
        if not self.outlines:
            why = "outline off (SATWATCH_SAM_OUTLINES=0)"
        elif mask is None:
            why = "no mask in the reply"
        elif mask.encoding != "one_bit":
            why = f"{mask.encoding} mask not decoded"
        elif (mask.height, mask.width) != (rec.box_h, rec.box_w):
            why = "mask raster does not match its box"
        else:
            try:
                raster = sam31_mod.decode_mask(mask)
                if not any(raster):
                    why = "mask is empty"
                else:
                    rings = sam31_mod.raster_to_rings(
                        raster, mask.height, mask.width, eps=self.MASK_EPS_PX)
                    norm = [overlay_mod.ring_from_px(
                        [(min(max(x + rec.x1, 0), w), min(max(y + rec.y1, 0), h))
                         for x, y in ring], w, h) for ring in rings]
                    if norm:
                        item["mask"] = {"format": "polygon", "rings": norm}
                    else:
                        why = "mask outline could not be traced"
            except sam31_mod.SamMaskError as exc:
                why = f"mask not decoded ({exc})"
        if why:
            item["note"] += f"; outline dropped: {why}"
        return item

    def _merge(self, per_prompt: list) -> tuple[list, int]:
        """Greedy IoU de-duplication across prompts (highest score wins)."""
        flat = [(r, rec) for r, rec in per_prompt]
        flat.sort(key=lambda t: -(t[0].get("score") or 0.0))
        kept: list = []
        merged = 0
        for raw, rec in flat:
            box = (rec.x1, rec.y1, rec.x2 + 1, rec.y2 + 1)
            twin = next((k for k in kept if _box_iou(k[1], box) >= self.DEDUPE_IOU), None)
            if twin is not None:
                merged += 1
                twin[0].setdefault("also_matched", []).append(raw["label"])
                continue
            kept.append((raw, box))
        return [k[0] for k in kept], merged

    # ----- the seam ------------------------------------------------------
    def detect(self, image_path: str, aoi: dict) -> DetectionResult:
        if not self.api_key:
            return _awaiting(self.name, self.label,
                             "no Meta API key (META_API_KEY / META_MODEL_API_KEY / "
                             "SUPERMAP_META_API_KEY) - detection awaits Meta API key")
        png, w, h = self._read_native(image_path)
        per_prompt: list = []
        by_prompt: dict = {}
        images = calls = 0
        response_ids: list = []
        for prompt in self.prompts:
            reply = self._call(prompt, png)
            calls += 1
            images += reply.images_processed if reply.images_processed is not None else 1
            if reply.response_id:
                response_ids.append(reply.response_id)
            matches = 0
            for rec in reply.records:
                if rec.frame != 0 or (rec.w, rec.h) != (w, h):
                    raise sam31_mod.SamResponseError(
                        f"reply frame does not match the {w}x{h} crop that was sent")
                per_prompt.append((self._to_raw(rec, prompt), rec))
                matches += 1
            by_prompt[prompt] = matches
        raw, merged = self._merge(per_prompt)
        kept, dropped = filter_countable(raw, w, h)
        cost = round(images * sam_meter_mod.PRICE_PER_IMAGE_USD, 6)
        counts = {"vessels": len(kept), "frame_vessels": len(kept),
                  "below_size_floor_excluded": dropped,
                  "duplicates_merged": merged, "count_basis": "frame"}
        provenance = {
            "source": sam31_mod.SOURCE_LABEL, "model": sam31_mod.MODEL,
            "mode": "MODELED", "counts_are_detections": True, "ground_truth": False,
            "image_basis": "native crop pixels (no enhancement, no upscaling)",
            "mask_encoding": "one_bit", "prompts": list(self.prompts),
            "matches_by_prompt": by_prompt, "sam_calls": calls,
            "images_processed": images, "est_cost_usd": cost,
            "cost_basis": "ESTIMATED: images x $2.50 per 1,000 (published price)",
            "recall_validated": False, "response_ids": response_ids,
        }
        return DetectionResult(
            status=STATUS_OK, provider=self.name, counts=counts, detections=kept,
            prompts=list(self.prompts), provider_label=self.label,
            provenance=provenance,
            note=(f"{self.label} ({sam31_mod.MODEL}, Meta Model API): {len(kept)} detections "
                  f"with long side >= {MIN_COUNTABLE_LONG_SIDE_PX} px on the native crop "
                  f"({dropped} below the floor excluded, {merged} duplicates merged); "
                  "MODELED detections, not ground truth; recall at 10 m not validated; "
                  f"{calls} SAM calls, est. ${cost:.4f}"))


# ---------------------------------------------------------------------------
# open-weights-sam (our own worker contract; worker infra NOT built here)
# ---------------------------------------------------------------------------

WORKER_CONTRACT = "satwatch-sam-worker/1"


def filter_countable(raw: list[dict], w: int, h: int) -> tuple[list[dict], int]:
    """Drop detections below the large-vessel size floor.

    ``raw`` boxes/masks are norm1000; the floor is in crop PIXELS. A raw
    detection with no bbox cannot be sized and is dropped from the count
    (never counted on faith). Returns (kept, dropped_count).
    """
    kept, dropped = [], 0
    for r in raw:
        box = r.get("bbox")
        if box is None and r.get("mask") and r["mask"].get("format") == "polygon":
            box = overlay_mod.bbox_of_rings(r["mask"]["rings"])
        if box is None:
            dropped += 1
            continue
        long_px = max((box[2] - box[0]) / 1000.0 * w, (box[3] - box[1]) / 1000.0 * h)
        # norm1000 is rounded to 0.001: allow that rounding, nothing more
        if long_px >= MIN_COUNTABLE_LONG_SIDE_PX - 0.01:
            kept.append(r)
        else:
            dropped += 1
    return kept, dropped


class OpenWeightsSamDetector:
    """Client for a self-hosted SAM 3.x worker (``satwatch-sam-worker/1``).

    How it plugs in (Modal / Baseten / any GPU host): wrap
    ``facebook/sam3.1`` (SAM licence - check terms) in one HTTPS POST
    endpoint that implements:

        request  {"contract": "satwatch-sam-worker/1",
                  "image_png_b64": "<base64 PNG>",
                  "prompts": ["container ship", ...]}
        response {"contract": "satwatch-sam-worker/1",
                  "model": "facebook/sam3.1", "model_revision": "...",
                  "image_size": [w, h],
                  "detections": [{"label": str, "score": float,
                                   "bbox_xyxy_px": [x0,y0,x1,y1],
                                   "polygon_px": [[x,y],...] | null}]}

    Set ``SATWATCH_SAM_WORKER_URL`` (+ optional
    ``SATWATCH_SAM_WORKER_TOKEN``) and ``SATWATCH_DETECTOR=open-weights-sam``.
    This is OUR contract, not a third party's: nothing about the Meta
    API is assumed. Unset URL -> AWAITING (counts null).
    """

    name = "open-weights-sam"
    label = "OPEN-WEIGHTS SAM (self-hosted worker)"

    def __init__(self, url: Optional[str] = None, token: Optional[str] = None,
                 http: Optional[Callable[[str, dict, dict], dict]] = None,
                 timeout: int = 120) -> None:
        self.url = url if url is not None else os.environ.get("SATWATCH_SAM_WORKER_URL", "")
        self.token = token if token is not None else os.environ.get("SATWATCH_SAM_WORKER_TOKEN", "")
        self._http = http or self._default_http
        self.timeout = timeout

    def _default_http(self, url: str, body: dict, headers: dict) -> dict:
        req = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json", **headers})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def detect(self, image_path: str, aoi: dict) -> DetectionResult:
        if not self.url:
            return _awaiting(self.name, self.label,
                             "SATWATCH_SAM_WORKER_URL not set - no open-weights worker configured")
        body = {"contract": WORKER_CONTRACT,
                "image_png_b64": base64.b64encode(Path(image_path).read_bytes()).decode("ascii"),
                "prompts": list(CONCEPT_PROMPTS)}
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            resp = self._http(self.url, body, headers)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return _awaiting(self.name, self.label,
                             f"open-weights worker unreachable: {type(exc).__name__}")
        try:
            if resp.get("contract") != WORKER_CONTRACT:
                raise ValueError("worker contract mismatch")
            w, h = resp["image_size"]
            raw = []
            for d in resp["detections"]:
                x0, y0, x1, y1 = d["bbox_xyxy_px"]
                item = {"label": d["label"], "score": float(d["score"]),
                        "bbox": overlay_mod.bbox_from_xyxy_px((x0, y0, x1, y1), w, h)}
                if d.get("polygon_px"):
                    item["mask"] = {"format": "polygon", "rings": [
                        overlay_mod.ring_from_px(d["polygon_px"], w, h)]}
                raw.append(item)
        except (KeyError, TypeError, ValueError):
            return _awaiting(self.name, self.label,
                             "open-weights worker returned an unreadable response - nothing counted")
        kept, dropped = filter_countable(raw, w, h)
        counts = {"vessels": len(kept), "frame_vessels": len(kept),
                  "below_size_floor_excluded": dropped,
                  "count_basis": "frame"}
        return DetectionResult(
            status=STATUS_OK, provider=self.name, counts=counts, detections=kept,
            provider_label=self.label,
            note=(f"{self.label}: model {resp.get('model')} rev {resp.get('model_revision')}; "
                  f"countable = long side >= {MIN_COUNTABLE_LONG_SIDE_PX} px; "
                  f"{dropped} below the floor excluded"))


# ---------------------------------------------------------------------------
# reference-counts (OFFLINE replay)
# ---------------------------------------------------------------------------

REFERENCE_FIXTURES = Path(__file__).resolve().parent / "fixtures" \
    / "reference_detections.json"
REFERENCE_LABEL = "REFERENCE COUNTS (offline)"
REFERENCE_CHIP = "REFERENCE COUNTS - OFFLINE"

_CROP_RE = re.compile(
    r"^(?P<node>.+)_(?P<role>now|baseline)_(?P<date>\d{4}-\d{2}-\d{2})\.png$")


class ReferenceCountsDetector:
    """Offline transport: replays reference counts recorded offline from
    the baked crops (offline replay; not a live detection;
    fixtures/reference_detections.json).

    Called once per crop, like every provider: a NOW crop returns the
    now-side counts, a BASELINE crop the baseline-side counts, each only
    when the crop's date matches the recorded scene date. Localisations
    are approximate POINTS (the recorded pass produced no masks and no
    measured boxes; none are fabricated, and baseline points were never
    recorded so a baseline crop returns counts only). An unrecorded
    scene returns counts=None.
    """

    name = "reference-counts"
    label = REFERENCE_CHIP

    def __init__(self, fixtures_path: Path | str = REFERENCE_FIXTURES) -> None:
        self.fixtures_path = Path(fixtures_path)
        self._fixtures: Optional[dict] = None

    def _load(self) -> dict:
        if self._fixtures is None:
            try:
                doc = json.loads(self.fixtures_path.read_text(encoding="utf-8"))
                self._fixtures = doc.get("sites", {})
            except Exception:
                self._fixtures = {}
        return self._fixtures

    def detect(self, image_path: str, aoi: dict) -> DetectionResult:
        node_id = (aoi or {}).get("node_id")
        m = _CROP_RE.match(Path(image_path).name)
        site = self._load().get(node_id or "") if node_id else None
        role = m.group("role") if m else None
        if site is None or m is None \
                or site.get(f"{role}_scene_date") != m.group("date"):
            return _awaiting(
                self.name, self.label,
                f"{REFERENCE_LABEL}: no recorded reference count "
                "for this scene - nothing counted, nothing invented")
        side = site[role]
        # vessels = the ANCHORAGE zone count (what brief.py/signals.py read
        # as the queue); whole-frame totals ride under frame_vessels.
        counts = {
            "vessels": side["anchorage"],
            "frame_vessels": side["total"],
            "anchorage": side["anchorage"],
            "berth": side["berth"],
            "transit": side["transit"],
            "yard": site["yard"][role],
            "ambiguous_excluded": side.get("ambiguous_excluded"),
            "count_basis": "anchorage_zone",
        }
        raw = None
        if role == "now":
            raw = [
                {"label": d["class"], "zone": d["zone"], "point": [d["x"], d["y"]],
                 "note": "approximate point; no mask, no measured bbox"}
                for d in side.get("detections", [])]
        return DetectionResult(
            status=STATUS_OK, provider=self.name, counts=counts, detections=raw,
            visibility=side.get("visibility"), provider_label=self.label,
            note=(f"{REFERENCE_LABEL}: reference counts recorded offline "
                  "over the baked Sentinel-2 crops - "
                  "NOT Meta SAM, NOT live. " + site["counting_basis"]))


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProviderSpec:
    id: str
    factory: Callable[[], Detector]
    label: str
    aliases: tuple = ()
    counts_are_real: bool = False   # may counts from this provider mint production signals?
    # Has this detector's recall at the crop resolution been measured against
    # ground truth? No provider is validated: SAM on 10 m Sentinel-2 returned
    # zero matches on every recorded ship crop. Unvalidated counts need a
    # human review before they can become an event (signals.evaluate_signal).
    recall_validated: bool = False
    offline_test: bool = False
    requires: tuple = ()            # env vars that must be present
    requires_any: bool = False      # True: ANY one of `requires` suffices


REGISTRY: dict[str, ProviderSpec] = {
    "none": ProviderSpec("none", FixtureDetector, FixtureDetector.label,
                         aliases=("fixture",)),
    "reference-counts": ProviderSpec(
        "reference-counts", ReferenceCountsDetector, ReferenceCountsDetector.label,
        aliases=("reference-counts-offline",), offline_test=True),
    "meta-sam": ProviderSpec(
        "meta-sam", MetaSamDetector, MetaSamDetector.label, aliases=("meta",),
        counts_are_real=True, requires=sam31_mod.KEY_ENVS, requires_any=True),
    "open-weights-sam": ProviderSpec(
        "open-weights-sam", OpenWeightsSamDetector, OpenWeightsSamDetector.label,
        aliases=("sam-open",), counts_are_real=True,
        requires=("SATWATCH_SAM_WORKER_URL",)),
}


def resolve_provider_id(choice: Optional[str] = None) -> str:
    """Normalise a selector value to a registry id; DetectorConfigError
    on a name that is registered nowhere (a typo must not silently
    become 'no detector' at run time)."""
    raw = (choice if choice is not None
           else os.environ.get(ENV_SELECTOR, "none")).strip().lower() or "none"
    for spec in REGISTRY.values():
        if raw == spec.id or raw in spec.aliases:
            return spec.id
    raise DetectorConfigError(
        f"{ENV_SELECTOR}={raw!r} is not a registered provider "
        f"(known: {', '.join(sorted(REGISTRY))})")


def get_detector(choice: Optional[str] = None) -> Detector:
    return REGISTRY[resolve_provider_id(choice)].factory()


def _provider_state(spec: ProviderSpec) -> tuple[str, str]:
    if spec.id == "none":
        return "NOT_SERVED", "no detector configured: counts stay null"
    if spec.offline_test:
        return "OFFLINE_TEST", "recorded reference-count replay; never live, never mints signals"
    if spec.id == "meta-sam":
        if not sam31_mod.resolve_key():
            return "AWAITING_META_KEY", ("set META_API_KEY (or META_MODEL_API_KEY / "
                                         "SUPERMAP_META_API_KEY)")
        status = sam_meter_mod.SamMeter().status()
        if status["disabled"]:
            return "NOT_SERVED", f"SAM calls disabled ({sam_meter_mod.ENV_DISABLED})"
        if status["cap_reached"]:
            return "NOT_SERVED", (f"daily SAM call cap reached "
                                  f"({status['calls_today']}/{status['cap_calls_per_day']})")
        return "READY", ("key present; SAM 3.1 transport implemented; live only when "
                         "selected with SATWATCH_DETECTOR=meta-sam")
    present = all(os.environ.get(v) for v in spec.requires)
    if not present:
        return "NOT_CONFIGURED", "set SATWATCH_SAM_WORKER_URL to a satwatch-sam-worker/1 endpoint"
    return "READY", "worker URL configured"


def describe_detectors() -> dict:
    """Registry status for GET /satwatch/detectors. Never prints a key."""
    try:
        selected = resolve_provider_id()
        error = None
    except DetectorConfigError as exc:
        selected, error = None, str(exc)
    rows = []
    for spec in REGISTRY.values():
        state, note = _provider_state(spec)
        row = {
            "id": spec.id, "label": spec.label, "aliases": list(spec.aliases),
            "selected": spec.id == selected, "state": state, "note": note,
            "counts_are_real": spec.counts_are_real, "offline_test": spec.offline_test,
            "requires_env": list(spec.requires),
        }
        if spec.id == "meta-sam":
            row["requires_any_of_env"] = True
            row["available"] = state == "READY"
            row["model"] = sam31_mod.MODEL
            row["usage"] = sam_meter_mod.SamMeter().status()
        rows.append(row)
    return {"selector_env": ENV_SELECTOR, "selected": selected,
            "config_error": error, "providers": rows}
