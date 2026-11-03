"""POST /voice/transcribe and GET /voice/status (additive).

The browser posts the RAW audio as the request body with its Content-Type
(``audio/wav`` from the web helper), optionally ``?keywords=Suez,Panama`` and
``?language=en``. No multipart upload on this side (the server stack has no
form parser); the multipart call to Meta is built inside ``service``.

Reply (200): {transcript, untrusted: true, audioDurationMs, model,
transport: "LIVE"|"RECORDED", recorded, transcript_source, cost_usd,
estimated_cost_usd, audio{...}, provenance{...}}. The transcript is text to
show the user and, only after the user confirms it, to feed Agent mode as a
normal typed message. Nothing here acts on it.

Errors use the standard {error: {code, message}} shape:
  415 unsupported_format   413 audio_too_long / audio_too_large
  422 audio_empty / audio_invalid / invalid_request / asr_rejected
  429 voice_cap_reached (daily seconds) / asr_rate_limited (Retry-After)
  502 asr_unavailable / asr_unreachable / asr_bad_response / asr_auth_failed
  503 voice_disabled / voice_not_configured / voice_ledger_unavailable
"""
from __future__ import annotations

from fastapi import Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

try:
    from llm import demo_gate
except ImportError:  # imported as server.voice.* from the repo root
    from server.llm import demo_gate  # type: ignore

from . import audio as _audio
from .service import VoiceError, VoiceService, default_ledger_path


def _error(code: str, message: str, status: int, retry_after_s: int | None = None) -> JSONResponse:
    body: dict = {"code": code, "message": message}
    headers = {}
    if retry_after_s is not None:
        body["retry_after_s"] = retry_after_s
        headers["Retry-After"] = str(retry_after_s)
    return JSONResponse(status_code=status, content={"error": body}, headers=headers)


def _keywords(req: Request) -> list[str]:
    out: list[str] = []
    for raw in req.query_params.getlist("keywords"):
        out.extend(p for p in raw.split(",") if p.strip())
    return out


def register_voice_routes(app, ctx: dict | None = None) -> None:
    ctx = ctx or {}
    service: VoiceService | None = ctx.get("service")
    if service is None:
        service = VoiceService.from_env()
    app.state.voice_service = service
    recorded: dict = {}

    def service_for(req: Request) -> VoiceService:
        """The service this request may use. Demo gate on and no token: a
        RECORDED service with its own ledger and a day-long cap, so recorded
        answers can never use up (or be refused by) the live audio budget."""
        if demo_gate.request_unlocked(req) or service.transport_name == "fixture":
            return service
        if "svc" not in recorded:
            base = default_ledger_path()
            recorded["svc"] = VoiceService(
                transport="fixture", cap_seconds=86400.0,
                ledger_path=base.with_name(base.stem + ".recorded" + base.suffix))
        return recorded["svc"]

    @app.post("/voice/transcribe")
    async def voice_transcribe(req: Request):
        data = await req.body()
        svc = service_for(req)
        try:
            result = await run_in_threadpool(
                svc.transcribe, data, req.headers.get("content-type"),
                keywords=_keywords(req), language=req.query_params.get("language"))
        except (_audio.AudioError, VoiceError) as exc:
            return _error(exc.code, exc.message, exc.status,
                          getattr(exc, "retry_after_s", None))
        return JSONResponse(result)

    @app.get("/voice/status")
    async def voice_status(req: Request):
        try:
            out = await run_in_threadpool(service_for(req).status)
            out["demo_gate"] = demo_gate.status_block(req)
            return JSONResponse(out)
        except VoiceError as exc:
            return _error(exc.code, exc.message, exc.status)
