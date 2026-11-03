"""Replay window routes (additive, read-only over stored files).

  GET  /replay/episodes                  the curated list; route-restriction episodes only with
                                         ?include_route_restrictions=1
  GET  /replay/episodes/{id}             one episode document with provenance and the sha256 of every source file
  POST /replay/export/xlsx               a workbook from a table the client already shows (no engine, no file write)

Nothing here runs an engine or writes to disk. Registered from ``create_app`` through one defensive hook.
"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi import Request
from fastapi.responses import JSONResponse, Response

try:
    from . import replay_episodes as R
except ImportError:  # pragma: no cover - imported as a top-level module
    import replay_episodes as R  # type: ignore

XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAX_SHEETS, MAX_ROWS, MAX_COLS, MAX_CELL = 6, 2000, 30, 2000
_ID = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
_STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def _cell(v):
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v
    return str(v)[:MAX_CELL]


def workbook_from_body(body: dict) -> bytes:
    """Validate the posted tables and write the workbook. Raises ValueError with a short reason."""
    try:
        from reports.xlsx import table_sheet, text_sheet, write_workbook
    except ImportError:  # pragma: no cover
        from server.reports.xlsx import table_sheet, text_sheet, write_workbook  # type: ignore
    title = str(body.get("title") or "Replay comparison")[:120]
    sheets_in = body.get("sheets")
    if not isinstance(sheets_in, list) or not 1 <= len(sheets_in) <= MAX_SHEETS:
        raise ValueError("sheets must be a list of 1 to 6 tables")
    sheets = []
    for s in sheets_in:
        if not isinstance(s, dict) or not isinstance(s.get("columns"), list) or not isinstance(s.get("rows"), list):
            raise ValueError("each sheet needs columns and rows")
        cols = [str(c)[:80] for c in s["columns"]][:MAX_COLS]
        if not cols:
            raise ValueError("a sheet needs at least one column")
        if len(s["rows"]) > MAX_ROWS:
            raise ValueError("too many rows")
        rows = []
        for r in s["rows"]:
            if not isinstance(r, list):
                raise ValueError("rows must be lists")
            line = [_cell(v) for v in r[:len(cols)]]
            rows.append(line + [None] * (len(cols) - len(line)))
        sheets.append(table_sheet(str(s.get("name") or "Sheet")[:31], cols, rows))
    meta = body.get("meta")
    if isinstance(meta, list) and meta:
        rows = [[(title, "title")]] + [[(str(k)[:60], "bold"), (str(v)[:400], None)] for k, v in
                                       (p for p in meta[:40] if isinstance(p, list) and len(p) == 2)]
        sheets.insert(0, text_sheet("Provenance", rows, [28, 90]))
    created = body.get("created")
    if not isinstance(created, str) or not _STAMP.match(created):
        created = "2026-10-29T00:00:00Z"   # no clock in the file unless the caller supplies one
    return write_workbook(sheets, created=created, title=title)


def register_replay_routes(app, ctx: dict) -> None:
    root = Path(ctx.get("root") or R.REPO_ROOT)

    @app.get("/replay/episodes")
    async def replay_episodes(include_route_restrictions: int = 0):
        return R.list_episodes(root, include_restricted=bool(include_route_restrictions))

    @app.get("/replay/episodes/{episode_id}")
    async def replay_episode(episode_id: str):
        if not _ID.match(episode_id):
            return _error(404, "unknown_episode", "unknown episode")
        doc = R.build_episode(episode_id, root)
        if doc is None:
            return _error(404, "unknown_episode", "unknown episode")
        return doc

    @app.post("/replay/export/xlsx")
    async def replay_export_xlsx(request: Request):
        try:
            body = await request.json()
        except Exception:
            return _error(400, "bad_json", "request body must be JSON")
        if not isinstance(body, dict):
            return _error(422, "invalid_request", "request body must be an object")
        try:
            data = workbook_from_body(body)
        except ValueError as exc:
            return _error(422, "invalid_request", str(exc))
        return Response(content=data, media_type=XLSX_TYPE,
                        headers={"Content-Disposition": 'attachment; filename="replay-comparison.xlsx"'})
