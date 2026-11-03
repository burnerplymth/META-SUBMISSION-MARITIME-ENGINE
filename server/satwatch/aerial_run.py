"""Aerial scene store and SAM runs. Logic only; the HTTP
surface is ``aerial_routes.py``, the pure geometry is ``aerial.py``.

Two places hold scenes:

* ``<satwatch_dir>/aerial/``        scenes an admin fetched (``<id>.json``,
  the image exactly as served, and ``<id>.replies/`` real SAM replies a live
  run recorded);
* ``fixtures/aerial/<id>/``         the committed demo set: ``scene.json``, a
  reduced display ``preview.jpg`` and ``replies/`` real SAM replies. The
  native crop is NOT committed (size); an admin can fetch it again and the
  stored copy is compared by sha256 with the one the replies were recorded on.

RECORDED replays stored real replies through the same parser, merge and
geometry as a live run, so a recorded result and a live result have one
shape. LIVE needs a key, a cost confirmation equal to the estimate and the
SAM meter (per-day cap, ledger); the route adds the admin bearer.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable, Optional

from . import aerial as ae
from . import sam31 as sam31_mod
from . import sam_meter as sam_meter_mod
from .detect import MetaSamDetector
from .scan import _atomic_write_bytes, _atomic_write_text

_SLUG = re.compile(r"[^a-z0-9]+")


def slug(phrase: str) -> str:
    return _SLUG.sub("-", phrase.lower()).strip("-")


def reply_name(phrase: str, tile: int) -> str:
    return f"{slug(phrase)}_t{tile}.sse"


def _read_json(path: Path) -> Optional[dict]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return doc if isinstance(doc, dict) else None


def store_dir(satwatch_dir) -> Path:
    return Path(satwatch_dir) / ae.AERIAL_DIR


def fixture_dir(scene_id: str, fixtures: Optional[Path] = None) -> Path:
    return Path(fixtures or ae.FIXTURES) / scene_id


def _valid_id(scene_id) -> bool:
    return isinstance(scene_id, str) and bool(ae.SCENE_ID_RE.match(scene_id))


def load_scene(satwatch_dir, scene_id: str, fixtures: Optional[Path] = None) -> Optional[dict]:
    """The scene record (stored beats fixture), plus where its parts live."""
    if not _valid_id(scene_id):
        return None
    stored = _read_json(store_dir(satwatch_dir) / f"{scene_id}.json")
    fixed = _read_json(fixture_dir(scene_id, fixtures) / "scene.json")
    scene = stored or fixed
    if scene is None or scene.get("contract") != ae.SCENE_CONTRACT:
        return None
    scene = dict(scene)
    scene["_stored"] = bool(stored)
    scene["_has_fixture"] = bool(fixed)
    # replies recorded by the fixture stay usable only on the very pixels they were recorded on
    if stored and fixed and (stored.get("crop") or {}).get("sha256") == (fixed.get("crop") or {}).get("sha256"):
        have = {(r["phrase"], r["tile"]) for r in scene.get("recorded", [])}
        scene["recorded"] = list(scene.get("recorded", [])) + [r for r in fixed.get("recorded", []) if (r["phrase"], r["tile"]) not in have]
    return scene


def list_scenes(satwatch_dir, fixtures: Optional[Path] = None) -> list[dict]:
    ids = set(p["id"] for p in ae.AERIAL_PLACES)
    for root in (store_dir(satwatch_dir), Path(fixtures or ae.FIXTURES)):
        if root.is_dir():
            for c in root.iterdir():
                name = c.stem if c.suffix == ".json" else c.name
                if _valid_id(name) and (c.suffix == ".json" or c.is_dir()):
                    ids.add(name)
    out = []
    for sid in sorted(ids):
        sc = load_scene(satwatch_dir, sid, fixtures)
        place = ae.PLACE_BY_ID.get(sid)
        if sc is None and place is None:
            continue
        out.append(scene_summary(sc, place))
    return out


def scene_summary(scene: Optional[dict], place: Optional[dict] = None) -> dict:
    if scene is None:                                  # a curated place that was never fetched
        src = ae.src_mod.get_aerial_source(place["source"])
        return {"scene_id": place["id"], "name": place["name"], "source": src["id"], "state": "NOT FETCHED",
                "badge": ae.badge(src["year"], place.get("gsd_m") or src["gsd_m"]),
                "gsd_m": place.get("gsd_m") or src["gsd_m"], "recorded_phrases": [], "image": None,
                "desk_place": place.get("desk_place")}
    phrases = sorted({r["phrase"] for r in scene.get("recorded", [])})
    return {
        "scene_id": scene["scene_id"], "name": scene["name"], "source": scene["source"], "badge": scene["badge"],
        "acquisition_label": scene["acquisition_label"], "gsd_m": scene["gsd_m"], "size_px": scene["size_px"],
        "state": "SNAPSHOT", "year": scene.get("year"), "nodata_pct": scene.get("nodata_pct"),
        "recorded_phrases": phrases, "image": "native" if scene["_stored"] else "preview",
        "attribution": scene.get("attribution"), "retrieved_at": scene.get("retrieved_at"),
        "desk_place": (ae.PLACE_BY_ID.get(scene["scene_id"]) or {}).get("desk_place"),
    }


def image_path(satwatch_dir, scene: dict, fixtures: Optional[Path] = None) -> Optional[tuple[Path, str, str]]:
    """(path, content type, kind) of the picture to show: the stored native
    crop when there is one, otherwise the committed reduced preview."""
    if scene.get("_stored"):
        p = store_dir(satwatch_dir) / scene["crop"]["file"]
        if p.is_file():
            return p, scene["crop"]["content_type"], "native"
    pv = fixture_dir(scene["scene_id"], fixtures) / "preview.jpg"
    if pv.is_file():
        return pv, "image/jpeg", "preview"
    return None


def parse_phrases(phrases) -> tuple:
    if phrases is None:
        return ae.DEFAULT_PHRASES
    if not isinstance(phrases, (list, tuple)) or not phrases or not all(isinstance(p, str) for p in phrases):
        raise ae.AerialError("invalid_request", "phrases must be a non-empty list of strings", 422)
    out = tuple(dict.fromkeys(phrases))
    bad = [p for p in out if p not in ae.PHRASES]
    if bad:
        raise ae.AerialError("invalid_request", f"phrases must come from {sorted(ae.PHRASES)}", 422)
    if len(out) > ae.MAX_PHRASES:
        raise ae.AerialError("invalid_request", f"at most {ae.MAX_PHRASES} phrases per run", 422)
    return out


def _stored_raw(satwatch_dir, scene: dict) -> Optional[bytes]:
    if not scene.get("_stored"):
        return None
    p = store_dir(satwatch_dir) / scene["crop"]["file"]
    return p.read_bytes() if p.is_file() else None


def plan(satwatch_dir, scene: dict, phrases, fixtures: Optional[Path] = None, usage: Optional[dict] = None) -> dict:
    phrases = parse_phrases(phrases)
    w, h = scene["size_px"]
    tiles = ae.usable_tiles(_stored_raw(satwatch_dir, scene), ae.plan_tiles(w, h))
    live_tiles = [t for t in tiles if not t["skipped"]]
    rec = {(r["phrase"], r["tile"]) for r in scene.get("recorded", [])}
    recorded_phrases = [p for p in phrases if all((p, t["i"]) in rec for t in live_tiles)]
    calls = len(live_tiles) * len(phrases)
    est = ae.estimate_cost(len(live_tiles), len(phrases))
    return {
        "contract": ae.CONTRACT, "scene_id": scene["scene_id"], "name": scene["name"], "badge": scene["badge"],
        "gsd_m": scene["gsd_m"], "size_px": [w, h], "phrases": list(phrases), "tiles": tiles,
        "tile_px": ae.TILE_PX, "overlap_px": ae.OVERLAP_PX,
        "recorded": {"available": bool(recorded_phrases), "phrases": recorded_phrases,
                     "missing": [p for p in phrases if p not in recorded_phrases]},
        "live": {"crop_stored": bool(scene["_stored"]),
                 "state": "READY" if scene["_stored"] else "NOT FETCHED",
                 "calls": calls, "within_cap": calls <= ae.MAX_TILES * ae.MAX_PHRASES and len(live_tiles) <= ae.MAX_TILES,
                 "admin_required": True},
        "est_cost_usd": est, "cost_basis": "ESTIMATED", "price_per_1000_images_usd": sam31_mod.PRICE_PER_1000_IMAGES_USD,
        "usage": usage, "label": ae.LABEL_MODELLED, "framing": ae.FRAMING, "image_basis": ae.IMAGE_BASIS,
    }


def _merge_phrase(scene: dict, phrase: str, tiles: list[dict], replies: dict) -> list[dict]:
    w, h = scene["size_px"]
    objs: list[dict] = []
    for t in tiles:
        objs += ae.objects_from_reply(replies[(phrase, t["i"])], phrase, t, (w, h), scene["gsd_m"])
    return ae.merge_objects(objs)


def run_recorded(satwatch_dir, scene: dict, phrases, fixtures: Optional[Path] = None) -> dict:
    phrases = parse_phrases(phrases)
    w, h = scene["size_px"]
    tiles = [dict(t, nodata_pct=None, skipped=False) for t in ae.plan_tiles(w, h)]
    index = {(r["phrase"], r["tile"]): r for r in scene.get("recorded", [])}
    dirs = [store_dir(satwatch_dir) / f"{scene['scene_id']}.replies", fixture_dir(scene["scene_id"], fixtures) / "replies"]
    replies, ids, at = {}, [], None
    served = []
    for p in phrases:
        if not all((p, t["i"]) in index for t in tiles):
            continue
        try:
            for t in tiles:
                ent = index[(p, t["i"])]
                path = next((d / ent["reply"] for d in dirs if (d / ent["reply"]).is_file()), None)
                if path is None:
                    raise FileNotFoundError(ent["reply"])
                rep = sam31_mod.parse_reply(path.read_text(encoding="utf-8"))
                replies[(p, t["i"])] = rep
                if rep.response_id:
                    ids.append(rep.response_id)
                at = ent.get("recorded_at") or at
        except (OSError, sam31_mod.SamError) as exc:
            raise ae.AerialError("recorded_unreadable", f"the recorded reply could not be read ({type(exc).__name__})", 502)
        served.append(p)
    if not served:
        raise ae.AerialError("not_recorded", "no recorded SAM reply for this scene and phrase set", 404)
    try:
        by_phrase = {p: _merge_phrase(scene, p, tiles, replies) for p in served}
    except sam31_mod.SamError as exc:
        raise ae.AerialError("recorded_unreadable", f"recorded reply does not match the scene ({exc})", 502)
    res = ae.build_result(scene, by_phrase, mode="RECORDED", images=len(tiles) * len(served), cost_usd=0.0,
                          tiles=tiles, response_ids=ids, recorded_at=at)
    res["missing_phrases"] = [p for p in phrases if p not in served]
    return res


def run_live(satwatch_dir, scene: dict, phrases, confirm_cost_usd, *, http: Optional[Callable] = None, meter=None,
             key: Optional[str] = None) -> dict:
    phrases = parse_phrases(phrases)
    raw = _stored_raw(satwatch_dir, scene)
    if raw is None:
        raise ae.AerialError("crop_not_stored", "fetch this scene first (admin) so the native crop is stored", 409)
    w, h = scene["size_px"]
    tiles = ae.usable_tiles(raw, ae.plan_tiles(w, h))
    live_tiles = [t for t in tiles if not t["skipped"]]
    if not live_tiles:
        raise ae.AerialError("no_data", "every tile of this scene is no-data", 422)
    if len(live_tiles) > ae.MAX_TILES:
        raise ae.AerialError("too_many_tiles", f"at most {ae.MAX_TILES} tiles per run", 422)
    est = ae.estimate_cost(len(live_tiles), len(phrases))
    try:
        ok = abs(float(confirm_cost_usd) - est) <= 1e-6
    except (TypeError, ValueError):
        ok = False
    if not ok:
        raise ae.AerialError("cost_not_confirmed", f"confirm_cost_usd must equal the estimate ({est})", 422)
    key = key if key is not None else sam31_mod.resolve_key()
    if not key:
        raise ae.AerialError("awaiting_meta_key", "no Meta API key is configured", 409)
    last: dict = {}

    def traced(url, body, headers, timeout):
        status, text = (http or sam31_mod.default_http)(url, body, headers, timeout)
        last.update(status=status, text=text)
        return status, text
    det = MetaSamDetector(api_key=key, http=traced, meter=meter or sam_meter_mod.SamMeter())
    rdir = store_dir(satwatch_dir) / f"{scene['scene_id']}.replies"
    rdir.mkdir(parents=True, exist_ok=True)
    replies, ids, recorded = {}, [], list(scene.get("recorded", []))
    done = 0
    stamp = ae.time.strftime("%Y-%m-%d", ae.time.gmtime())
    try:
        for t in live_tiles:
            png, mime = ae.tile_bytes(raw, scene["crop"]["content_type"], tiles, t, (w, h))
            for p in phrases:
                last.clear()
                rep = det._call(p, png, mime=mime)
                done += 1
                if last.get("text") is not None:
                    name = reply_name(p, t["i"])
                    _atomic_write_text(rdir / name, last["text"])
                    recorded = [r for r in recorded if (r["phrase"], r["tile"]) != (p, t["i"])]
                    recorded.append({"phrase": p, "tile": t["i"], "reply": name, "recorded_at": stamp})
                replies[(p, t["i"])] = rep
                if rep.response_id:
                    ids.append(rep.response_id)
        by_phrase = {p: _merge_phrase(scene, p, live_tiles, replies) for p in phrases}
    except sam_meter_mod.SamCapError as exc:
        raise ae.AerialError("sam_cap", str(exc), 429)
    except sam31_mod.SamCallError as exc:
        raise ae.AerialError("sam_call_failed", str(exc), 502)
    except sam31_mod.SamError as exc:
        raise ae.AerialError("sam_bad_reply", str(exc), 502)
    finally:                                    # keep what was recorded even if a later call failed
        meta = {k: v for k, v in scene.items() if not k.startswith("_")}
        meta["recorded"] = recorded
        _atomic_write_text(store_dir(satwatch_dir) / f"{scene['scene_id']}.json", json.dumps(meta, indent=1, sort_keys=True) + "\n")
    return ae.build_result(scene, by_phrase, mode="LIVE", images=done, cost_usd=round(done * sam_meter_mod.PRICE_PER_IMAGE_USD, 6),
                           tiles=tiles, response_ids=ids)


def save_fetched(satwatch_dir, raw: bytes, meta: dict) -> dict:
    d = store_dir(satwatch_dir)
    d.mkdir(parents=True, exist_ok=True)
    old = _read_json(d / f"{meta['scene_id']}.json")
    if old and old.get("crop", {}).get("sha256") == meta["crop"]["sha256"]:
        meta["recorded"] = old.get("recorded", [])      # same pixels: the recorded replies still apply
    _atomic_write_bytes(d / meta["crop"]["file"], raw)
    _atomic_write_text(d / f"{meta['scene_id']}.json", json.dumps(meta, indent=1, sort_keys=True) + "\n")
    return meta
