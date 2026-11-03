"""HTTP routes of the Monte Carlo layer.

Registered from server/api/app.py with ``register_montecarlo_routes``. All
errors go through the app's own ``_err`` (house error shape). Order matters:
the fixed paths (/montecarlo/compare, /montecarlo/batch) are declared before
``/montecarlo/{job_id}`` so they are never read as a job id.
"""
from __future__ import annotations

import json
import math
import os
import re
import secrets
from pathlib import Path

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from . import LABEL
from . import export as export_mod
from .jobs import (JOB_ID_RE, JobForbidden, JobNotFound, JobNotReady, MonteCarloJobs)
from .sampling import EngineError
from .store import MonteCarloStore

try:  # dual-import convention
    from api.security import request_client
except Exception:  # imported as server.montecarlo.* from the repo root
    from server.api.security import request_client  # type: ignore

TOKEN_HEADER = "X-Montecarlo-Token"
_INT_RE = re.compile(r"^[0-9]{1,9}$")


def _build_jobs(ctx: dict) -> MonteCarloJobs:
    base = Path(ctx["data_root"])
    directory = os.environ.get("SUPERMAP_MONTECARLO_DIR", str(base / "montecarlo"))
    return MonteCarloJobs(ctx["graph"], ctx["core_graph"], MonteCarloStore(directory))


def register_montecarlo_routes(app, ctx: dict) -> None:
    err = ctx["err"]
    jobs = _build_jobs(ctx)
    app.state.montecarlo_jobs = jobs

    def _not_found(job_id):
        return err("montecarlo_not_found", f"unknown montecarlo job: {str(job_id)[:40]!r}")

    def _not_ready(exc: JobNotReady):
        rec = exc.record
        return err("montecarlo_not_ready",
                   f"montecarlo job {rec['id']} is {rec['status']}, not done "
                   f"({rec['progress']['done']} of {rec['progress']['n']} simulations)")

    def _download(text: str, media: str, filename: str):
        return Response(content=text, media_type=media, headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Montecarlo-Label": LABEL, "Cache-Control": "no-store"})

    async def _body(req: Request):
        try:
            return await req.json(), None
        except Exception:
            return None, err("bad_json", "request body must be JSON")

    @app.post("/montecarlo")
    async def post_montecarlo(req: Request):
        body, bad = await _body(req)
        if bad is not None:
            return bad
        try:
            head = jobs.create(body, request_client(req), secrets.token_urlsafe(18))
        except EngineError as exc:
            return err(exc.code, str(exc))
        return JSONResponse(status_code=202, content=head)

    @app.post("/montecarlo/batch")
    async def post_montecarlo_batch(req: Request):
        body, bad = await _body(req)
        if bad is not None:
            return bad
        try:
            out = jobs.create_batch(body, request_client(req), secrets.token_urlsafe(18))
        except EngineError as exc:
            return err(exc.code, str(exc))
        return JSONResponse(status_code=202, content=out)

    @app.get("/montecarlo")
    async def list_montecarlo():
        rows = jobs.list_jobs()
        return {"label": LABEL, "jobs": rows, "total": len(rows)}

    @app.get("/montecarlo/compare")
    async def get_montecarlo_compare(req: Request):
        a, b = req.query_params.get("a"), req.query_params.get("b")
        if not (isinstance(a, str) and JOB_ID_RE.match(a) and isinstance(b, str)
                and JOB_ID_RE.match(b)):
            return err("invalid_request", "a and b must be montecarlo job ids")
        raw = req.query_params.get("threshold")
        threshold = 0.0
        if raw is not None:
            try:
                threshold = float(raw)
            except ValueError:
                return err("invalid_request", "threshold must be a number")
            if not math.isfinite(threshold) or threshold < 0:
                return err("invalid_request", "threshold must be a finite number >= 0")
        fmt = req.query_params.get("format", "json")
        if fmt not in ("json", "csv"):
            return err("invalid_request", "format must be json or csv")
        try:
            out = jobs.compare(a, b, threshold)
            if fmt == "csv":
                return _download(export_mod.compare_csv(out), "text/csv; charset=utf-8",
                                 f"compare_{a}_{b}.csv")
            return out
        except JobNotFound as exc:
            return _not_found(exc)
        except JobNotReady as exc:
            return _not_ready(exc)

    @app.get("/montecarlo/{job_id}/samples")
    async def get_montecarlo_samples(job_id: str, req: Request):
        if not JOB_ID_RE.match(job_id):
            return _not_found(job_id)
        offset, limit = 0, 200
        raw_o, raw_l = req.query_params.get("offset"), req.query_params.get("limit")
        if raw_o is not None:
            if not _INT_RE.match(raw_o):
                return err("invalid_request", "offset must be a non-negative integer")
            offset = int(raw_o)
        if raw_l is not None:
            if not _INT_RE.match(raw_l) or not 1 <= int(raw_l) <= 1000:
                return err("invalid_request", "limit must be an integer in [1, 1000]")
            limit = int(raw_l)
        try:
            return jobs.samples(job_id, offset, limit)
        except JobNotFound:
            return _not_found(job_id)
        except JobNotReady as exc:
            return _not_ready(exc)

    @app.get("/montecarlo/{job_id}/export")
    async def get_montecarlo_export(job_id: str, req: Request):
        if not JOB_ID_RE.match(job_id):
            return _not_found(job_id)
        qp = req.query_params
        fmt, kind = qp.get("format", "csv"), qp.get("kind")
        if fmt not in ("csv", "json"):
            return err("invalid_request", "format must be csv or json")
        if kind is not None and kind not in ("samples", "bands"):
            return err("invalid_request", "kind must be samples or bands")
        if fmt == "json" and kind is not None:
            return err("invalid_request", "kind applies to csv only; json is the whole job")
        try:
            rec = jobs.get_done(job_id)
            if fmt == "json":
                body = json.dumps(export_mod.job_json(jobs.view(rec), rec), sort_keys=True,
                                  separators=(",", ":"), allow_nan=False)
                return _download(body, "application/json", f"{job_id}.json")
            if (kind or "samples") == "bands":
                return _download(export_mod.bands_csv(rec), "text/csv; charset=utf-8",
                                 f"{job_id}_bands.csv")
            return _download(export_mod.samples_csv(rec), "text/csv; charset=utf-8",
                             f"{job_id}_samples.csv")
        except JobNotFound:
            return _not_found(job_id)
        except JobNotReady as exc:
            return _not_ready(exc)

    @app.get("/montecarlo/{job_id}")
    async def get_montecarlo(job_id: str):
        if not JOB_ID_RE.match(job_id):
            return _not_found(job_id)
        rec = jobs.get(job_id)
        if rec is None:
            return _not_found(job_id)
        return jobs.view(rec)

    @app.delete("/montecarlo/{job_id}")
    async def delete_montecarlo(job_id: str, req: Request):
        if not JOB_ID_RE.match(job_id):
            return _not_found(job_id)
        token = req.headers.get(TOKEN_HEADER) or req.query_params.get("owner_token") or ""
        try:
            jobs.delete(job_id, token[:200])
        except JobNotFound:
            return _not_found(job_id)
        except JobForbidden:
            return err("forbidden", "deleting a job needs the owner token returned when it was "
                                    f"created ({TOKEN_HEADER} header or owner_token parameter)")
        return {"job_id": job_id, "status": "cancelled"}
