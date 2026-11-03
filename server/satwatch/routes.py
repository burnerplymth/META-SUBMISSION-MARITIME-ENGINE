"""HTTP surface for Satellite Watch beyond the read-only routes.

Registered from ``create_app`` via ``register_satwatch_routes(app, ctx)``
(same defensive pattern as the desks routes). Existing routes in
app.py; ``GET /satwatch/scans`` passes
records through ``overlay.normalise_scan`` so detections are always
contract items.

    GET  /satwatch/detectors          provider registry + selected + state
                                      (meta-sam row carries today's SAM calls/cost/cap)
    GET  /satwatch/sam/usage          SAM call meter: calls, est. cost, daily cap
                                      (SAM calls are NOT in /llm/status)
    GET  /satwatch/places             every configured anchorage + last scan
    GET  /satwatch/overlay/{node_id}  mask-overlay envelope for one anchorage
    GET  /satwatch/brief/{node_id}    change-intel diff + template brief (no LLM)
    GET  /satwatch/signals            dry-run: which scans WOULD mint, and why not
    POST /satwatch/signals/mint       admin + explicit per-place review {reviewed:[{place,scene_now_id}]}:
                                      mint the eligible reviewed scans as MODELED events
    POST /satwatch/scan               admin: create a scan job (202), poll below (never mints)
    GET  /satwatch/scan/{job_id}      job status (per-place results)
    GET  /satwatch/scan               recent jobs
    ...  /satwatch/sources, /s1, /naip, /landsat   multi-source desk (multi_routes.py)

Write routes (scan, mint) need ``Authorization: Bearer $SUPERMAP_ADMIN_TOKEN``
(fail closed when unset) because they spend network and write the event
feed. All handlers are plain ``def`` (threadpooled) - none blocks the
event loop. Errors use the house shape ``{error: {code, message}}``.
"""
from __future__ import annotations

import hmac
import json
import os
import re
from pathlib import Path

from fastapi import Request
from fastapi.responses import JSONResponse

from . import brief as brief_mod
from . import overlay as overlay_mod
from . import signals as signals_mod
from . import sam_meter as sam_meter_mod
from .detect import DetectorConfigError, describe_detectors
from .jobs import JobBusy, MintNeedsReview, ScanJobManager, UnknownPlace

ADMIN_TOKEN_ENV = "SUPERMAP_ADMIN_TOKEN"
NODE_RE = re.compile(r"^[a-z0-9_]{1,64}$")
SCAN_BODY_KEYS = {"places", "days", "mint"}
MINT_BODY_KEYS = {"reviewed"}
REVIEW_ITEM_KEYS = {"place", "scene_now_id"}
MAX_REVIEWED_PER_MINT = 30


def _err(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse(status_code=status,
                        content={"error": {"code": code, "message": message}})


def _admin_ok(request: Request) -> bool:
    expected = os.environ.get(ADMIN_TOKEN_ENV) or ""
    header = request.headers.get("authorization", "")
    presented = header[len("Bearer "):] if header.startswith("Bearer ") else ""
    # bytes, not str: compare_digest raises TypeError on a non-ASCII str,
    # which a client can send in the header (latin-1 decoded) -> 500.
    return bool(expected and presented and hmac.compare_digest(
        presented.encode("utf-8"), expected.encode("utf-8")))


def _read_scan(satwatch_dir: Path, node_id: str):
    if not NODE_RE.match(node_id):
        return None
    path = satwatch_dir / f"{node_id}.json"
    if not path.is_file():
        return None
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return rec if isinstance(rec, dict) and rec.get("kind") == "satwatch_scan" else None


def _all_scans(satwatch_dir: Path) -> list[dict]:
    out = []
    if not satwatch_dir.is_dir():
        return out
    for path in sorted(satwatch_dir.glob("*.json"), key=lambda p: p.name):
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(rec, dict) and rec.get("kind") == "satwatch_scan":
            out.append(rec)
    return out


def _known_node_ids(graph_path: Path):
    try:
        doc = json.loads(Path(graph_path).read_text(encoding="utf-8"))
        return {n["id"] for n in doc.get("nodes", [])}
    except Exception:
        return None


def register_satwatch_routes(app, ctx: dict) -> None:
    satwatch_dir = Path(ctx["satwatch_dir"])
    events_path = ctx.get("events_path")
    graph_path = ctx.get("graph_path")

    def _event_store():
        try:
            from ..store.store import EventStore
        except ImportError:  # server/ on sys.path
            from store.store import EventStore  # type: ignore
        return EventStore(Path(events_path), _known_node_ids(graph_path)
                          if graph_path else None)

    # A scan job never mints (jobs.MintNeedsReview): minting is the separate,
    # reviewed POST /satwatch/signals/mint below.
    manager = ScanJobManager(satwatch_dir,
                             http=ctx.get("http"),
                             start_thread=ctx.get("start_thread", True))
    app.state.satwatch_jobs = manager

    @app.get("/satwatch/detectors")
    def get_detectors():
        return describe_detectors()

    @app.get("/satwatch/sam/usage")
    def get_sam_usage():
        return sam_meter_mod.SamMeter().status()

    @app.get("/satwatch/places")
    def get_places():
        return {"places": manager.places()}

    @app.get("/satwatch/overlay/{node_id}")
    def get_overlay(node_id: str):
        rec = _read_scan(satwatch_dir, node_id)
        if rec is None:
            return _err("scan_not_found", f"no stored scan for {node_id!r}", 404)
        return overlay_mod.overlay_for_scan(rec)

    @app.get("/satwatch/brief/{node_id}")
    def get_brief(node_id: str):
        rec = _read_scan(satwatch_dir, node_id)
        if rec is None:
            return _err("scan_not_found", f"no stored scan for {node_id!r}", 404)
        intel = brief_mod.build_change_intel(rec)
        return {"node_id": node_id, "intel": intel,
                "text": brief_mod.render_brief(rec, intel),
                "generated_by": "deterministic template (no LLM in the satellite path)"}

    @app.get("/satwatch/signals")
    def get_signals():
        return {"signals": [signals_mod.evaluate_signal(r) for r in _all_scans(satwatch_dir)]}

    @app.post("/satwatch/signals/mint")
    def post_mint(request: Request, body: dict | None = None):
        if not _admin_ok(request):
            return _err("unauthorized", "minting requires the admin bearer token", 401)
        if not events_path:
            return _err("mint_unavailable", "no event store is mounted", 409)
        body = body if isinstance(body, dict) else {}
        if set(body) - MINT_BODY_KEYS:
            return _err("invalid_request", f"unknown field(s): {sorted(set(body) - MINT_BODY_KEYS)}", 422)
        reviewed = body.get("reviewed")
        if not (isinstance(reviewed, list) and reviewed):
            return _err("review_required",
                        "minting needs an explicit review: reviewed = [{place, scene_now_id}, ...] "
                        "naming each place and the scene that was reviewed", 422)
        if len(reviewed) > MAX_REVIEWED_PER_MINT:
            return _err("invalid_request", f"at most {MAX_REVIEWED_PER_MINT} reviewed places per call", 422)
        seen = set()
        for item in reviewed:
            if not (isinstance(item, dict) and set(item) == REVIEW_ITEM_KEYS
                    and all(isinstance(item[k], str) and 0 < len(item[k]) <= 128 for k in REVIEW_ITEM_KEYS)):
                return _err("invalid_request", "each reviewed item must be exactly {place, scene_now_id} (strings)", 422)
            if item["place"] in seen:
                return _err("invalid_request", f"place {item['place']!r} is listed twice", 422)
            seen.add(item["place"])
        results = []
        for item in reviewed:
            rec = _read_scan(satwatch_dir, item["place"])
            if rec is None:
                results.append({"node_id": item["place"], "minted": False,
                                "reasons": ["no_stored_scan"], "event_id": None})
                continue
            if (rec.get("scene_now") or {}).get("id") != item["scene_now_id"]:
                # The scan changed since the reviewer looked: not what was reviewed.
                results.append({"node_id": item["place"], "minted": False,
                                "reasons": ["reviewed_scene_does_not_match_stored_scan"],
                                "event_id": None})
                continue
            res = signals_mod.mint_modeled_event(rec, _event_store(), reviewed=True)
            results.append({"node_id": rec["node_id"], "minted": res["minted"],
                            "reasons": res["reasons"],
                            "event_id": (res["event"] or {}).get("id")})
        return {"results": results,
                "minted": sum(1 for r in results if r["minted"]),
                "mode": "MODELED"}

    @app.post("/satwatch/scan", status_code=202)
    def post_scan(request: Request, body: dict | None = None):
        if not _admin_ok(request):
            return _err("unauthorized", "scanning requires the admin bearer token", 401)
        if not isinstance(body, dict):
            return _err("invalid_request", 'body must be {"places": [...]|"all"}', 422)
        extra = set(body) - SCAN_BODY_KEYS
        if extra:
            return _err("invalid_request", f"unknown field(s): {sorted(extra)}", 422)
        if "places" not in body:
            return _err("invalid_request", 'places is required: node ids or "all"', 422)
        try:
            job = manager.submit(body["places"], days=body.get("days", 90),
                                 mint=bool(body.get("mint", False)))
        except MintNeedsReview as exc:
            return _err("mint_requires_review", str(exc), 422)
        except UnknownPlace as exc:
            return _err("unknown_place", str(exc), 422)
        except DetectorConfigError as exc:
            return _err("detector_misconfigured", str(exc), 422)
        except JobBusy as exc:
            return _err("scan_busy", str(exc), 429)
        except ValueError as exc:
            return _err("invalid_request", str(exc), 422)
        return job

    @app.get("/satwatch/scan/{job_id}")
    def get_scan_job(job_id: str):
        job = manager.get(job_id)
        if job is None:
            return _err("job_not_found", f"unknown scan job {job_id!r}", 404)
        return job

    @app.get("/satwatch/scan")
    def list_scan_jobs():
        return {"jobs": manager.list_jobs()}

    # Meta AI on a stored native crop: SAM run + vision read.
    try:
        from .sat_ai_routes import register_sat_ai_routes
        register_sat_ai_routes(app, {"satwatch_dir": satwatch_dir})
    except Exception:
        app.state.satwatch_sat_ai_routes = False

    # Aerial (sub-metre) scenes and the SAM tile pipeline.
    try:
        from .aerial_routes import register_aerial_routes
        register_aerial_routes(app, {"satwatch_dir": satwatch_dir, "http": ctx.get("http")})
    except Exception:
        app.state.satwatch_aerial_routes = False

    # Multi-source desk: additive routes, defensive like the
    # seam in app.py - a failure here never takes the routes above down.
    try:
        from .multi_routes import register_multi_routes
        register_multi_routes(app, {"satwatch_dir": satwatch_dir,
                                    "http": ctx.get("http"),
                                    "aois_fn": manager.aois,
                                    "clock": ctx.get("clock")})
    except Exception:
        app.state.satwatch_multi_routes = False
