"""Research workbench routes (additive, mounted from create_app).

    GET  /research/catalog
    GET  /research/episodes
    POST /research/assemble
    POST /research/analyze
    POST /research/scenes/scan
    GET  /research/export?assembly_id=&format=csv|xlsx|json

Error shape everywhere: {"error": {"code", "message"}}. Handlers are plain ``def`` so they run on
FastAPI's threadpool (statistics are pure Python and can take a moment on a 5-year window).
"""
from __future__ import annotations

import hmac
import os
from pathlib import Path

from fastapi import Body, Request, Response
from fastapi.responses import JSONResponse

from . import export as X
from .analyze import Analyzer
from .assemble import Assembler, AssemblyStore
from .bundle import Bundle
from .catalog import build_catalog, build_episodes
from .scenes import SceneScanner
from .util import ASSEMBLY_RE, ResearchError

REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLE_ENV = "SUPERMAP_RESEARCH_BUNDLE"
ADMIN_TOKEN_ENV = "SUPERMAP_ADMIN_TOKEN"
FORMATS = ("csv", "xlsx", "json")


def _json_err(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": str(message)[:600]}})


def _admin_ok(request: Request) -> bool:
    expected = os.environ.get(ADMIN_TOKEN_ENV) or ""
    header = request.headers.get("authorization", "")
    presented = header[len("Bearer "):] if header.startswith("Bearer ") else ""
    return bool(expected and presented and hmac.compare_digest(presented.encode("utf-8"),
                                                              expected.encode("utf-8")))


def live_allowed(request: Request) -> bool:
    """A live scene scan spends Meta SAM credit. Same policy as every other live SAM path (satwatch routes): the admin
    bearer only. The visitor demo token (which unlocks recorded-vs-live chat) does NOT open it."""
    return _admin_ok(request)


def check_live_gates() -> None:
    """The environment must also say SAM may run: SATWATCH_DETECTOR=meta-sam (the switch every other SAM path honours;
    an alias is fine, a typo is not) and SUPERMAP_BLOCK_NETWORK not set (the crop fetch is a network call)."""
    sel = None
    try:
        try:
            from satwatch.detect import resolve_provider_id
        except ImportError:
            from server.satwatch.detect import resolve_provider_id  # type: ignore
        sel = resolve_provider_id()
    except Exception:  # noqa: BLE001 - unknown selector or no satwatch: not enabled
        sel = None
    if sel != "meta-sam":
        raise ResearchError("detector_not_enabled",
                            "live SAM scans are off: set SATWATCH_DETECTOR=meta-sam on the server", 409)
    if os.environ.get("SUPERMAP_BLOCK_NETWORK") == "1":
        raise ResearchError("network_blocked", "live scans need network access and SUPERMAP_BLOCK_NETWORK=1 is set", 409)


def register_research_routes(app, ctx: dict) -> None:
    """ctx keys: data_root (writable runtime dir), book (callable -> PlaceBook), and optional
    bundle_root, history (HistoryService or callable), realism_dirs (callable -> [Path]),
    live_runner (callable(scene, prompt) -> record; an injection seam)."""
    data_root = Path(ctx["data_root"])
    bundle_root = Path(ctx.get("bundle_root") or os.environ.get(BUNDLE_ENV) or (REPO_ROOT / "data" / "research"))
    bundle = Bundle(bundle_root)
    store = AssemblyStore(data_root / "research_cache" / "assemblies")
    history = ctx.get("history")
    book_fn = ctx["book"]

    def _history():
        return history() if callable(history) else history

    class _LazyAssembler(Assembler):
        @property
        def history(self):          # type: ignore[override]
            return _history()

        @history.setter
        def history(self, v):
            pass

    assembler = _LazyAssembler(bundle, store, book=book_fn, history=None, online=ctx.get("online"))

    def realism_dirs():
        fn = ctx.get("realism_dirs")
        if fn:
            return fn()
        return [d for d in (data_root / "history_realism", REPO_ROOT / "data" / "history_realism") if d.is_dir()]

    analyzer = Analyzer(bundle, realism_dirs)

    def place_exists(p: str) -> bool:
        b = book_fn()
        return b is not None and p in b

    scanner = SceneScanner(bundle, place_exists)
    app.state.research_bundle = bundle

    def guard(fn):
        try:
            return fn()
        except ResearchError as exc:
            return _json_err(exc.status, exc.code, str(exc))

    # ------------------------------------------------------------- reads
    @app.get("/research/catalog", tags=["research"], summary="Research datasets, coverage, licences and state")
    def research_catalog():
        return guard(lambda: build_catalog(bundle, _history() is not None))

    @app.get("/research/episodes", tags=["research"], summary="The curated, sourced episode library")
    def research_episodes():
        return guard(lambda: build_episodes(bundle))

    # ----------------------------------------------------------- assemble
    @app.post("/research/assemble", tags=["research"], summary="Assemble aligned historical data for 1..5 places")
    def research_assemble(body: dict | None = Body(None)):
        return guard(lambda: assembler.assemble(body))

    # ------------------------------------------------------------ analyze
    @app.post("/research/analyze", tags=["research"], summary="Descriptive statistics over an assembly")
    def research_analyze(body: dict | None = Body(None)):
        def run():
            if not isinstance(body, dict):
                raise ResearchError("invalid_request", "body must be a JSON object")
            aid = body.get("assembly_id")
            if not isinstance(aid, str) or not ASSEMBLY_RE.match(aid):
                raise ResearchError("invalid_request", "assembly_id must look like asm_ followed by 16 hex digits")
            asm = store.get(aid)
            if asm is None:
                raise ResearchError("assembly_not_found", "no stored assembly with that id; assemble again", 404)
            return analyzer.analyze(asm, body)
        return guard(run)

    # -------------------------------------------------------- scene scan
    @app.post("/research/scenes/scan", tags=["research"],
              summary="Detector counts on archived scenes (recorded by default; live: admin bearer, SATWATCH_DETECTOR=meta-sam, network not blocked, capped)")
    def research_scenes_scan(request: Request, body: dict | None = Body(None)):
        def run():
            req = scanner.parse(body)
            live_ok = False
            runner = ctx.get("live_runner")
            if req["mode"] == "live":
                live_ok = live_allowed(request)
                if live_ok:
                    check_live_gates()
                if live_ok and runner is None:
                    def runner(scene, prompt):
                        from .scenes_live import run_live_sam
                        return run_live_sam(bundle, scene, prompt)
            return scanner.scan(req, live_runner=runner, live_ok=live_ok)
        return guard(run)

    # ------------------------------------------------------------- export
    @app.get("/research/export", tags=["research"], summary="Flat tables, manifest and label as csv, xlsx or json")
    def research_export(assembly_id: str = "", format: str = "csv"):
        def run():
            if not ASSEMBLY_RE.match(assembly_id or ""):
                raise ResearchError("invalid_request", "assembly_id must look like asm_ followed by 16 hex digits")
            if format not in FORMATS:
                raise ResearchError("invalid_request", "format must be csv, xlsx or json")
            asm = store.get(assembly_id)
            if asm is None:
                raise ResearchError("assembly_not_found", "no stored assembly with that id; assemble again", 404)
            if format == "csv":
                data, mt = X.to_csv(asm), "text/csv; charset=utf-8"
            elif format == "json":
                data, mt = X.to_json(asm), "application/json"
            else:
                data, mt = X.to_xlsx(asm), \
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            return Response(content=data, media_type=mt, headers={
                "Content-Disposition": f'attachment; filename="research_{assembly_id}.{format}"',
                "X-Content-Type-Options": "nosniff"})
        return guard(run)

    app.state.research_routes = True
