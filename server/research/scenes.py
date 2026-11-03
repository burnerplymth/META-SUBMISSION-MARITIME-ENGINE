"""POST /research/scenes/scan: per-scene detector counts at historical Sentinel acquisitions.

RECORDED (default): replays a count that was recorded from a real SAM call on exactly this scene and
crop (data/research/sam_recorded.json). LIVE: needs the admin bearer or the demo token, a cost
confirmation equal to the estimate, a Meta key, and passes through the SAM meter (daily cap) and the
single-image price. A scene with high cloud or no-data is never counted; a scene with no recorded
reply is NOT counted either ("no_recorded_reply"), never replaced by a stand-in.

Results are MODELLED and display-only: "SAM found N", never "there are N". Nothing here writes an
event or feeds a signal.
"""
from __future__ import annotations

from datetime import date

from .util import (ID_RE, LABEL_MODELLED, VERIFICATION, MAX_SCENES, SCENE_CLOUD_MAX, SCENE_NODATA_MAX, ResearchError,
                   check_depth, only_keys, parse_day, str_list)

PRICE_PER_IMAGE_USD = 0.0025
MAX_LIVE_SCENES = 3
PROMPTS = ("container ship", "cargo vessel")
DETECTORS = ("sam", "s1_cfar")
_KEYS = {"place", "scene_ids", "window", "detector", "mode", "confirm_cost_usd", "prompt"}


def usable_reason(scene: dict) -> str | None:
    c, n = scene.get("cloud_pct"), scene.get("nodata_pct")
    if c is not None and c > SCENE_CLOUD_MAX:
        return f"cloud_pct {c:g} > {SCENE_CLOUD_MAX:g}"
    if n is not None and n > SCENE_NODATA_MAX:
        return f"nodata_pct {n:g} > {SCENE_NODATA_MAX:g}"
    return None


class SceneScanner:
    def __init__(self, bundle, place_exists):
        self.bundle = bundle
        self.place_exists = place_exists            # callable(place) -> bool

    # ------------------------------------------------------------ validation
    def parse(self, body) -> dict:
        if not isinstance(body, dict):
            raise ResearchError("invalid_request", "body must be a JSON object")
        check_depth(body)
        only_keys(body, _KEYS, "request")
        place = body.get("place")
        if not isinstance(place, str) or not ID_RE.match(place):
            raise ResearchError("invalid_request", "place must be a place id")
        if not self.place_exists(place):
            raise ResearchError("unknown_place", f"{place!r} is not a known place", 404)
        det = body.get("detector", "sam")
        if det not in DETECTORS:
            raise ResearchError("invalid_request", "detector must be 'sam' or 's1_cfar'")
        mode = body.get("mode", "recorded")
        if mode not in ("recorded", "live"):
            raise ResearchError("invalid_request", "mode must be 'recorded' or 'live'")
        ids = body.get("scene_ids")
        win = body.get("window")
        if (ids is None) == (win is None):
            raise ResearchError("invalid_request", "give exactly one of scene_ids or window")
        if ids is not None:
            ids = str_list(ids, "scene_ids", max_len=MAX_SCENES, pattern=_SCENE_ID)
            if not ids:
                raise ResearchError("invalid_request", "scene_ids is empty")
        else:
            if not isinstance(win, dict):
                raise ResearchError("invalid_request", "window must be {from, to}")
            only_keys(win, {"from", "to"}, "window")
            a, b = parse_day(win.get("from"), "window.from"), parse_day(win.get("to"), "window.to")
            if b < a or (b - a).days > 366:
                raise ResearchError("invalid_request", "window must be 0..366 days")
            win = (a, b)
        prompt = body.get("prompt", PROMPTS[0])
        if prompt not in PROMPTS:
            raise ResearchError("invalid_request", f"prompt must be one of {list(PROMPTS)}")
        confirm = body.get("confirm_cost_usd")
        if confirm is not None and (isinstance(confirm, bool) or not isinstance(confirm, (int, float))):
            raise ResearchError("invalid_request", "confirm_cost_usd must be a number")
        return {"place": place, "detector": det, "mode": mode, "scene_ids": ids, "window": win,
                "prompt": prompt, "confirm": confirm}

    # -------------------------------------------------------------- selection
    def select(self, req: dict) -> list[dict]:
        sensor = "sentinel-2" if req["detector"] == "sam" else "sentinel-1"
        if req["scene_ids"] is not None:
            out = []
            for sid in req["scene_ids"]:
                s = self.bundle.scene_by_id(sid)
                if s is None or s["place"] != req["place"]:
                    raise ResearchError("scene_not_found", f"scene {sid!r} is not listed for {req['place']}", 404)
                out.append(s)
            return out
        a, b = req["window"]
        return self.bundle.scenes(req["place"], sensor, a, b)[:MAX_SCENES]

    # ------------------------------------------------------------------ scan
    def scan(self, req: dict, *, live_runner=None, live_ok: bool = False) -> dict:
        scenes = self.select(req)
        recorded = {}
        for e in self.bundle.sam_recorded():
            if e.get("detector", "sam") == req["detector"]:
                recorded[(e["scene_id"], e.get("prompt"))] = e
        results, not_counted = [], []
        live_list = []
        for s in scenes:
            why = usable_reason(s)
            if why:
                not_counted.append({"scene_id": s["scene_id"], "t": s["date"], "reason": why})
                continue
            if req["detector"] == "s1_cfar" and req["mode"] == "live":
                not_counted.append({"scene_id": s["scene_id"], "t": s["date"],
                                    "reason": "s1_cfar_live_not_offered"})
                continue
            if req["mode"] == "live":
                live_list.append(s)
                continue
            rec = recorded.get((s["scene_id"], req["prompt"] if req["detector"] == "sam" else None)) or \
                recorded.get((s["scene_id"], None))
            if rec is None:
                not_counted.append({"scene_id": s["scene_id"], "t": s["date"], "reason": "no_recorded_reply"})
                continue
            results.append(self._row(s, req, rec, recorded=True))
        cost = {"mode": req["mode"], "calls": 0, "est_usd": 0.0, "price_per_image_usd": PRICE_PER_IMAGE_USD}
        if req["mode"] == "live":
            if not live_ok:
                raise ResearchError("unauthorized", "a live scan requires the admin bearer token", 401)
            if len(live_list) > MAX_LIVE_SCENES:
                raise ResearchError("too_many_live_scenes", f"a live scan runs at most {MAX_LIVE_SCENES} scenes", 422)
            est = round(len(live_list) * PRICE_PER_IMAGE_USD, 6)
            cost.update(calls=len(live_list), est_usd=est)
            if not live_list:
                pass
            elif req["confirm"] is None or abs(float(req["confirm"]) - est) > 1e-6:
                raise ResearchError("cost_not_confirmed", f"confirm_cost_usd must equal the estimate ({est})", 422)
            elif live_runner is None:
                raise ResearchError("awaiting_meta_key", "live scans are not configured", 409)
            else:
                for s in live_list:
                    rec = live_runner(s, req["prompt"])
                    results.append(self._row(s, req, rec, recorded=False))
        return {"place": req["place"], "detector": req["detector"],
                "mode": "LIVE" if req["mode"] == "live" else "RECORDED", "label": LABEL_MODELLED,
                "verification": VERIFICATION, "human_reviewed": False,
                "results": results, "not_counted": not_counted, "cost": cost,
                "criteria": {"max_cloud_pct": SCENE_CLOUD_MAX, "max_nodata_pct": SCENE_NODATA_MAX}}

    @staticmethod
    def _row(s: dict, req: dict, rec: dict, *, recorded: bool) -> dict:
        n = rec["count"]
        name = "SAM" if req["detector"] == "sam" else "CFAR"
        chips = ["modelled_count"] + (["recorded_not_live"] if recorded else [])
        return {"scene_id": s["scene_id"], "t": s["date"], "source": s["source"],
                "cloud_pct": s.get("cloud_pct"), "nodata_pct": s.get("nodata_pct"), "usable": True,
                "found": n, "prompt": rec.get("prompt"), "text": f"{name} found {n}" if n else f"{name} found none",
                "recorded": recorded, "note": rec.get("note"), "below_floor": rec.get("below_floor"),
                "verification": VERIFICATION, "human_reviewed": False, "chips": chips}


import re  # noqa: E402
_SCENE_ID = re.compile(r"^[A-Za-z0-9_.\-]{1,100}$")
