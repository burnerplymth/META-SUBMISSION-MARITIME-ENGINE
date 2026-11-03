"""HTTP surface of the research note (mounted from ``routes.register_agent_routes``).

  GET  /agent/sessions/{id}/evidence          the evidence list (ids, tool, arguments hash,
                                              analysis id, n, label, caveat chips)
  POST /agent/sessions/{id}/research-report   write the note from the evidence (202). The click
                                              is the confirmation on a live model; a recorded
                                              session writes a RECORDED note at no cost.
  GET  /agent/sessions/{id}/research-report   {state, error, note}; state none | writing | ready |
                                              failed; the note carries its verification chips
  GET  /agent/sessions/{id}/research-report/export?format=html|pdf|json&note_id=
                                              download the note (HTML self-contained; PDF through
                                              the reports pipeline, 501 when no renderer)

Errors use the house shape {"error": {"code", "message"}}.
"""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from . import research_export, research_note
from .session import AgentError

FORMATS = {"html": "text/html; charset=utf-8", "pdf": "application/pdf",
           "json": "application/json"}


def _report_doc(s) -> dict:
    n = s.research.latest_note()
    return {"state": s.research.note_state, "error": s.research.note_error,
            "note": research_note.public_note(n, s.research) if n else None}


def register_research_routes(app, manager, guard) -> None:
    def _session(sid: str):
        s = manager.get(sid)
        if s is None:
            raise AgentError("unknown_session", "no such session", 404)
        return s

    @app.get("/agent/sessions/{sid}/evidence")
    @guard
    async def get_evidence(sid: str):
        s = _session(sid)
        return {"session_id": s.id, "transport": s.transport,
                "evidence": s.research.evidence.public()}

    @app.post("/agent/sessions/{sid}/research-report", status_code=202)
    @guard
    async def post_report(sid: str, req: Request):
        raw = await req.body()
        if raw.strip():
            raise AgentError("invalid_request", "this request takes no body", 422)
        s = manager.request_note(sid)
        return {"session_id": s.id, "transport": s.transport, **_report_doc(s)}

    @app.get("/agent/sessions/{sid}/research-report")
    @guard
    async def get_report(sid: str):
        s = _session(sid)
        return {"session_id": s.id, "transport": s.transport, **_report_doc(s)}

    @app.get("/agent/sessions/{sid}/research-report/export")
    @guard
    async def export_report(sid: str, req: Request):
        s = _session(sid)
        fmt = req.query_params.get("format", "html")
        if fmt not in FORMATS:
            raise AgentError("invalid_request", f"format must be one of {sorted(FORMATS)}", 422)
        note_id = req.query_params.get("note_id")
        note = s.research.note(note_id) if note_id else s.research.latest_note()
        if note is None:
            raise AgentError("no_note", "no research note has been written in this session", 404)
        evidence = s.research.evidence.dump()
        pub = {k: v for k, v in research_note.public_note(note, s.research).items()
               if k != "evidence"}
        if fmt == "html":
            data = research_export.render_html(pub, evidence).encode("utf-8")
        elif fmt == "json":
            data = research_export.render_json(pub, evidence).encode("utf-8")
        else:
            try:
                data = research_export.render_pdf_bytes(pub, evidence)
            except research_export.PdfError as exc:
                if getattr(exc, "busy", False):       # the render gate is full: try again, not "no renderer"
                    return JSONResponse(status_code=429, headers={"Retry-After": "5"}, content={"error": {
                        "code": "rate_limited", "message": "the PDF renderer is busy; try again shortly"}})
                return JSONResponse(status_code=501, content={"error": {
                    "code": "renderer_unavailable",
                    "message": "no PDF renderer on this server; use the HTML export and print to PDF"}})
        return Response(content=data, media_type=FORMATS[fmt], headers={
            "Content-Disposition": f'attachment; filename="{research_export.filename(note, fmt)}"',
            "X-Note-Sha256": research_export.content_hash(data)})
