"""Voice transcription service: Meta ASR over HTTP, or a labelled fixture.

The ONE place voice audio leaves this process. Configuration is env-only:

  SUPERMAP_VOICE_TRANSPORT       "fixture" | "http". Unset: follows
                                 SUPERMAP_LLM_TRANSPORT (http only when that is
                                 literally "http"), else fixture. A bare
                                 META_API_KEY NEVER switches anything to live.
  SUPERMAP_VOICE_API_KEY         key for the http transport; falls back to
                                 SUPERMAP_LLM_API_KEY, then META_API_KEY (the
                                 Meta key is read only once http was chosen
                                 explicitly).
  SUPERMAP_VOICE_ENDPOINT        default https://api.meta.ai/v1/asr/transcribe
  SUPERMAP_VOICE_MODEL           default muse-voice-transcribe-1.0
  SUPERMAP_VOICE_DAILY_CAP_SECONDS  audio seconds per UTC day. Fails closed:
                                 on the http transport an unset variable means
                                 the default of 900 s (about $0.045); the
                                 fixture transport has no cap unless set.
  SUPERMAP_VOICE_DISABLED        1/true/yes/on (or SUPERMAP_LLM_DISABLED) refuses
                                 every call.
  SUPERMAP_VOICE_LEDGER_PATH     usage ledger (default <data root>/voice/
                                 voice_ledger.jsonl). Holds seconds and cost
                                 only: never audio, never a transcript.

Guardrails, in order, all before any network call: content type, upload size,
container sniff, duration (<= 10 min), kill switch, daily seconds cap (a
reservation is written under a file lock, so parallel requests cannot
overshoot). The ledger failing to read or write refuses the call.

The transcript is UNTRUSTED text. This module cleans it (control characters,
length) and returns it; nothing here parses it as a command or acts on it.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import re
import time
import unicodedata
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

try:
    import envflag as _flags
    from redact import redact as _redact
    from llm import provenance as _prov
except ImportError:  # imported as server.voice.* from the repo root
    from server import envflag as _flags  # type: ignore
    from server.redact import redact as _redact  # type: ignore
    from server.llm import provenance as _prov  # type: ignore

from . import audio as _audio

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]

META_ENDPOINT = "https://api.meta.ai/v1/asr/transcribe"
META_HOST = "api.meta.ai"
META_MODEL = "muse-voice-transcribe-1.0"
META_KEY_ENV = "META_API_KEY"
PRICE_USD_PER_HOUR = 0.18           # published Muse Voice Transcribe rate
DEFAULT_HTTP_CAP_SECONDS = 900.0
FIXTURE_MODEL = "fixture-transport (no live model)"
HTTP_TIMEOUT_S = 120
RETRY_500_BACKOFF_S = 1.0
RETRY_AFTER_429_S = 2
MAX_KEYWORDS = 50
MAX_KEYWORD_LEN = 40
MAX_TRANSCRIPT_CHARS = 4000

# Biases recognition toward the names and terms this product hears. Place
# names and freight words only; the caller may add more.
DEFAULT_KEYWORDS = (
    "Suez", "Panama", "Rotterdam", "Shanghai", "Singapore", "Los Angeles",
    "Long Beach", "Baltimore", "Malacca", "Cape of Good Hope", "TEU",
    "reroute", "congestion", "chokepoint", "lane",
)

# Recorded phrases the fixture transport answers with. They are NOT a
# transcription of the audio: the response says so.
FIXTURE_PHRASES = (
    "Close the Suez Canal for fourteen days and show me the reroute around the Cape of Good Hope",
    "What happens to Rotterdam if Panama Canal transits are cut by half",
    "Compare Los Angeles port congestion with a one week strike at Long Beach",
    "Plan a lane from Shanghai to Rotterdam and show the range of days",
    "Fork the last run and add a two day delay at Singapore",
    "Show the Baltimore bridge collapse and which legs it touches",
)

_LANG_RE = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8}){0,3}$")


class VoiceError(Exception):
    """A refusal or failure with a stable code and the HTTP status served."""

    def __init__(self, code: str, message: str, status: int,
                 retry_after_s: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retry_after_s = retry_after_s
        self.billed = False      # True: the service answered, so the call was billed


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def data_root() -> Path:
    return Path(os.environ.get("SUPERMAP_DATA_DIR") or REPO_ROOT / "data")


def default_ledger_path() -> Path:
    return Path(os.environ.get("SUPERMAP_VOICE_LEDGER_PATH")
                or data_root() / "voice" / "voice_ledger.jsonl")


def cost_usd(duration_ms: int) -> float:
    return round(duration_ms / 3_600_000.0 * PRICE_USD_PER_HOUR, 6)


def clean_keywords(raw) -> list[str]:
    """Caller keywords merged after the defaults: trimmed, control
    characters dropped, deduplicated case-insensitively, bounded."""
    out: list[str] = []
    seen: set[str] = set()
    items = list(DEFAULT_KEYWORDS) + [str(k) for k in (raw or [])]
    for item in items:
        k = "".join(ch for ch in unicodedata.normalize("NFC", item)
                    if ch.isprintable()).strip()[:MAX_KEYWORD_LEN]
        if k and k.lower() not in seen:
            seen.add(k.lower())
            out.append(k)
        if len(out) >= MAX_KEYWORDS:
            break
    return out


def clean_transcript(text) -> str:
    """Untrusted text from the recogniser: NFC, control characters removed
    (newlines/tabs become spaces), whitespace collapsed, length bounded."""
    if not isinstance(text, str):
        return ""
    t = unicodedata.normalize("NFC", text)
    t = "".join(" " if ch in "\r\n\t" else ch for ch in t if ch.isprintable() or ch in "\r\n\t")
    return re.sub(r"\s+", " ", t).strip()[:MAX_TRANSCRIPT_CHARS]


def encode_multipart(request_obj: dict, wav: bytes, boundary: str) -> bytes:
    """The ``request`` JSON part plus the ``audio`` part, stdlib only."""
    crlf = b"\r\n"
    parts = [
        b"--" + boundary.encode(),
        b'Content-Disposition: form-data; name="request"',
        b"Content-Type: application/json",
        b"",
        json.dumps(request_obj, separators=(",", ":")).encode("utf-8"),
        b"--" + boundary.encode(),
        b'Content-Disposition: form-data; name="audio"; filename="audio.wav"',
        b"Content-Type: audio/wav",
        b"",
        wav,
        b"--" + boundary.encode() + b"--",
        b"",
    ]
    return crlf.join(parts)


def _meta_error_text(body: bytes) -> str:
    """A short, key-free hint from a Meta error body."""
    try:
        doc = json.loads(body.decode("utf-8", "replace"))
        msg = None
        if isinstance(doc, dict):
            err = doc.get("error")
            msg = (err.get("message") if isinstance(err, dict) else err) or doc.get("message")
        text = str(msg) if msg else body.decode("utf-8", "replace")
    except ValueError:
        text = body.decode("utf-8", "replace")
    return _redact(clean_transcript(text))[:200]


class VoiceService:
    def __init__(self, *, transport: str | None = None, api_key: str | None = None,
                 endpoint: str | None = None, model: str | None = None,
                 cap_seconds: float | None = None, ledger_path: Path | None = None,
                 disabled: bool | None = None, sleep=time.sleep, urlopen=None):
        env = os.environ
        explicit = transport or env.get("SUPERMAP_VOICE_TRANSPORT")
        if explicit is None and env.get("SUPERMAP_LLM_TRANSPORT") == "http":
            explicit = "http"
        transport = explicit or "fixture"
        if transport not in ("fixture", "http"):
            raise VoiceError("voice_not_configured",
                             f"unknown voice transport '{transport}'", 503)
        self.transport_name = transport
        self.api_key = api_key if api_key is not None else (
            (env.get("SUPERMAP_VOICE_API_KEY") or env.get("SUPERMAP_LLM_API_KEY")
             or (env.get(META_KEY_ENV) if transport == "http" else "") or ""))
        self.endpoint = endpoint or env.get("SUPERMAP_VOICE_ENDPOINT") or META_ENDPOINT
        self.model = (model or env.get("SUPERMAP_VOICE_MODEL") or META_MODEL) \
            if transport == "http" else FIXTURE_MODEL
        if cap_seconds is not None:
            self.cap_source: str | None = "constructor"
        else:
            raw = env.get("SUPERMAP_VOICE_DAILY_CAP_SECONDS", "")
            if raw:
                try:
                    cap_seconds = float(raw)
                except ValueError:
                    cap_seconds = 0.0          # unparseable: fail closed
                self.cap_source = "env"
            elif transport == "http":
                cap_seconds = DEFAULT_HTTP_CAP_SECONDS
                self.cap_source = "default"
            else:
                self.cap_source = None
        self.cap_seconds = cap_seconds
        self.ledger_path = Path(ledger_path or default_ledger_path())
        self._disabled_override = disabled
        self._sleep = sleep
        self._urlopen = urlopen or urllib.request.urlopen

    @classmethod
    def from_env(cls) -> "VoiceService":
        return cls()

    # ------------------------------------------------------------ kill switch
    def _is_disabled(self) -> bool:
        if self._disabled_override is not None:
            return self._disabled_override
        return _flags.any_truthy("SUPERMAP_VOICE_DISABLED", "SUPERMAP_LLM_DISABLED")

    # ------------------------------------------------------------- ledger
    # Append-only JSONL: reserve (before the call, bounds the spend),
    # settle (after), release (the call failed). Unreadable or corrupt
    # means voice calls are refused: fail closed, never reset to zero.

    @contextmanager
    def _lock(self):
        try:
            self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(self.ledger_path) + ".lock", os.O_RDWR | os.O_CREAT, 0o644)
        except OSError:
            raise VoiceError("voice_ledger_unavailable",
                             "voice usage ledger is unavailable; refusing voice calls", 503) from None
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _read(self) -> list[dict]:
        if not self.ledger_path.exists():
            return []
        try:
            text = self.ledger_path.read_text(encoding="utf-8")
        except OSError:
            raise VoiceError("voice_ledger_unavailable",
                             "voice usage ledger is unreadable; refusing voice calls", 503) from None
        recs = []
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                rec = None
            if not isinstance(rec, dict):
                raise VoiceError("voice_ledger_unavailable",
                                 "voice usage ledger is corrupt; refusing voice calls", 503)
            recs.append(rec)
        return recs

    def _append(self, rec: dict) -> None:
        try:
            with open(self.ledger_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, sort_keys=True) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        except OSError:
            raise VoiceError("voice_ledger_unavailable",
                             "voice usage ledger is not writable; refusing voice calls", 503) from None

    @staticmethod
    def _totals(records: list[dict], today: str) -> tuple[float, float, int]:
        """(seconds, cost_usd, calls) used today: settled calls at their
        recorded amounts, outstanding reservations at their reserved ones."""
        closed = {r.get("ref") for r in records if r.get("type") in ("settle", "release")}
        secs = cost = 0.0
        calls = 0
        for r in records:
            if not str(r.get("ts", "")).startswith(today):
                continue
            t = r.get("type")
            if t == "settle" or (t == "reserve" and r.get("id") not in closed):
                secs += float(r.get("seconds", 0.0) or 0.0)
                cost += float(r.get("cost_usd", 0.0) or 0.0)
                calls += 1
        return round(secs, 3), round(cost, 6), calls

    # ------------------------------------------------------------- status
    def status(self) -> dict:
        with self._lock():
            secs, cost, calls = self._totals(self._read(), _today())
        host = urlparse(self.endpoint).hostname if self.transport_name == "http" else None
        live = self.transport_name == "http"
        return {
            "transport": self.transport_name,
            "transport_label": "LIVE" if live else "RECORDED",
            "live": live,
            "model": self.model,
            "endpoint_host": host,
            "disabled": self._is_disabled(),
            "cap_seconds": self.cap_seconds,
            "cap_source": self.cap_source,
            "seconds_today": secs,
            "cost_today_usd": cost,
            "calls_today": calls,
            "cap_reached": self.cap_seconds is not None and secs >= self.cap_seconds,
            "price_usd_per_hour": PRICE_USD_PER_HOUR,
            "max_duration_ms": _audio.MAX_DURATION_MS,
            "max_upload_bytes": _audio.MAX_UPLOAD_BYTES,
            "accepted_rates_hz": list(_audio.ACCEPTED_RATES),
            "ffmpeg_available": _audio.ffmpeg_path() is not None,
            "note": ("FIXTURE transport: answers are recorded phrases, not a "
                     "transcription of the audio; cost is an estimate, charge is $0."
                     if not live else
                     "Live transport: audio is sent to the Meta speech-to-text API."),
        }

    # ------------------------------------------------------------ transcribe
    def transcribe(self, data: bytes, content_type: str | None, *,
                   keywords=None, language: str | None = None) -> dict:
        """Validate, convert, then transcribe. Raises VoiceError / AudioError
        (both carry .code, .message, .status)."""
        if language is not None and not _LANG_RE.match(language):
            raise VoiceError("invalid_request", "language must be a BCP-47 style tag such as en or en-US", 422)
        if self._is_disabled():
            raise VoiceError("voice_disabled", "voice input is disabled on this server", 503)
        prepared = _audio.prepare(data, content_type)       # size/duration/format, no network
        seconds = prepared.duration_ms / 1000.0
        est = cost_usd(prepared.duration_ms)
        rid = uuid.uuid4().hex
        reserve = {"type": "reserve", "id": rid, "ts": _utcnow(), "seconds": seconds,
                   "cost_usd": est, "transport": self.transport_name, "model": self.model}
        with self._lock():
            records = self._read()
            if self.cap_seconds is not None:
                used, _, _ = self._totals(records, _today())
                if used + seconds > self.cap_seconds:
                    raise VoiceError(
                        "voice_cap_reached",
                        f"daily voice limit reached: {used:.0f} s used of "
                        f"{self.cap_seconds:.0f} s (this clip is {seconds:.1f} s)", 429)
            self._append(reserve)
        try:
            if self.transport_name == "fixture":
                body = self._fixture(prepared)
                charge = 0.0
            else:
                body = self._call_meta(prepared, clean_keywords(keywords), language)
                charge = est
        except BaseException as exc:
            if getattr(exc, "billed", False):
                # Meta answered (HTTP 200) so the audio was processed and billed even
                # though the reply is unusable: settle at the estimate, do not release.
                try:
                    with self._lock():
                        self._append({"type": "settle", "ref": rid, "ts": _utcnow(),
                                      "seconds": seconds, "cost_usd": est,
                                      "transport": self.transport_name, "model": self.model})
                except Exception:
                    log.warning("voice settle failed for %s; reservation bounds the spend", rid, exc_info=True)
                raise
            try:
                with self._lock():
                    self._read()
                    self._append({"type": "release", "ref": rid, "ts": _utcnow(),
                                  "seconds": 0.0, "cost_usd": 0.0})
            except Exception:
                log.warning("voice release failed for %s", rid, exc_info=True)
            raise
        try:
            with self._lock():
                self._append({"type": "settle", "ref": rid, "ts": _utcnow(),
                              "seconds": seconds, "cost_usd": est,
                              "transport": self.transport_name, "model": self.model})
        except Exception:
            log.warning("voice settle failed for %s; reservation bounds the spend", rid, exc_info=True)
        return self._result(prepared, body, est, charge)

    def _result(self, prepared, body: dict, est: float, charge: float) -> dict:
        live = self.transport_name == "http"
        reported = body.get("audioDurationMs")
        duration = reported if isinstance(reported, int) and 0 < reported <= _audio.MAX_DURATION_MS * 2 \
            else prepared.duration_ms
        block = _prov.make(model=self.model, transport_raw=self.transport_name,
                           prompt_name=None, prompt_version=None,
                           cost_usd=charge, cached=False, estimated_cost_usd=est)
        block["surface"] = "voice"
        block["audio_seconds"] = round(prepared.duration_ms / 1000.0, 3)
        return {
            "transcript": clean_transcript(body.get("transcript")),
            "untrusted": True,
            "audioDurationMs": duration,
            "model": self.model,
            "transport": "LIVE" if live else "RECORDED",
            "recorded": not live,
            "transcript_source": "meta_asr" if live else "recorded_fixture",
            "session_id": body.get("sessionId") if isinstance(body.get("sessionId"), str) else None,
            "cost_usd": charge,
            "estimated_cost_usd": est,
            "price_usd_per_hour": PRICE_USD_PER_HOUR,
            "audio": {"source_format": prepared.source_format, "converted_via": prepared.via,
                      "sample_rate_hz": prepared.rate, "duration_ms": prepared.duration_ms},
            "provenance": block,
        }

    # ------------------------------------------------------------ transports
    def _fixture(self, prepared) -> dict:
        digest = hashlib.sha256(prepared.wav).digest()
        phrase = FIXTURE_PHRASES[int.from_bytes(digest[:4], "big") % len(FIXTURE_PHRASES)]
        return {"transcript": phrase, "audioDurationMs": prepared.duration_ms,
                "sessionId": "fixture-" + digest.hex()[:12]}

    def _call_meta(self, prepared, keywords: list[str], language: str | None) -> dict:
        if not self.api_key:
            raise VoiceError("voice_not_configured",
                             "voice transport is http but no API key is configured", 503)
        req_obj = {"model": self.model, "audioEncoding": "WAV", "mode": "PUSH_TO_TALK"}
        if keywords:
            req_obj["keywords"] = keywords
        if language:
            req_obj["languageBias"] = language
        boundary = "supermap" + uuid.uuid4().hex
        payload = encode_multipart(req_obj, prepared.wav, boundary)
        attempts = 2
        for attempt in range(attempts):
            request = urllib.request.Request(
                self.endpoint, data=payload, method="POST",
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                         "Authorization": f"Bearer {self.api_key}",
                         "Accept": "application/json"})
            try:
                with self._urlopen(request, timeout=HTTP_TIMEOUT_S) as resp:
                    raw = resp.read()
            except urllib.error.HTTPError as exc:
                try:
                    detail = _meta_error_text(exc.read()[:4096])
                except Exception:
                    detail = ""
                if detail:
                    log.info("voice upstream HTTP %s: %s", exc.code, detail)   # server log only
                status = exc.code
                if status >= 500 and attempt + 1 < attempts:
                    self._sleep(RETRY_500_BACKOFF_S)          # one retry on 5xx
                    continue
                raise self._map_http_error(status, detail, exc) from None
            except (urllib.error.URLError, TimeoutError, OSError):
                if attempt + 1 < attempts:
                    self._sleep(RETRY_500_BACKOFF_S)
                    continue
                raise VoiceError("asr_unreachable",
                                 "the speech-to-text service could not be reached", 502) from None
            try:
                doc = json.loads(raw.decode("utf-8"))
            except ValueError:
                err = VoiceError("asr_bad_response",
                                 "the speech-to-text service returned an unreadable reply", 502)
                err.billed = True
                raise err from None
            if not isinstance(doc, dict) or not isinstance(doc.get("transcript"), str):
                err = VoiceError("asr_bad_response",
                                 "the speech-to-text reply had no transcript", 502)
                err.billed = True
                raise err
            return doc
        raise VoiceError("asr_unavailable", "the speech-to-text service is unavailable", 502)  # pragma: no cover

    @staticmethod
    def _map_http_error(status: int, detail: str, exc) -> VoiceError:
        hint = ""        # the upstream body is never echoed to the client (it is logged by the caller)
        if status == 400:
            return VoiceError("asr_rejected", "the speech-to-text service rejected the "
                              "request or audio" + hint, 422)
        if status == 413:
            return VoiceError("asr_payload_too_large",
                              "the speech-to-text service says the audio is too large" + hint, 413)
        if status == 429:
            try:
                wait = int(exc.headers.get("Retry-After", "")) if exc.headers else RETRY_AFTER_429_S
            except (TypeError, ValueError):
                wait = RETRY_AFTER_429_S
            wait = max(1, min(wait, 120))
            return VoiceError(
                "asr_rate_limited",
                "the speech-to-text service is rate limiting; retry with exponential "
                f"backoff (start at {wait} s, then double)", 429, retry_after_s=wait)
        if status in (401, 403):
            return VoiceError("asr_auth_failed",
                              "the speech-to-text service rejected the server credentials", 502)
        if status >= 500:
            return VoiceError("asr_unavailable",
                              "the speech-to-text service failed; try again shortly" + hint, 502)
        return VoiceError("asr_error", f"unexpected speech-to-text status {status}" + hint, 502)
