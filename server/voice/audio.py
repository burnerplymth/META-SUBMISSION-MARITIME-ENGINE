"""Audio intake for voice input: validate, then convert to what Meta needs.

Meta's speech-to-text takes WAV only: mono, 16-bit PCM, 16 or 24 kHz, at most
10 minutes and 32 MB. This module turns whatever the browser sent into exactly
that, or refuses with a named error BEFORE any network call is made.

Three paths, cheapest first:

  1. WAV that is already mono 16-bit PCM at 16 or 24 kHz: validated and
     re-emitted with a canonical header. No signal processing. This is the
     path the web helper (web/app/src/voice/wav.ts) is built to hit.
  2. Any other PCM or float WAV: decoded, downmixed to mono, resampled to
     16 kHz with pure Python (stdlib only). Bounded: see PURE_MAX_OUTPUT_S.
  3. A compressed container (WebM/Opus, Ogg, MP4/M4A, MP3) or a WAV too long
     for path 2: converted by the system ``ffmpeg`` if it is installed, else
     refused with ``unsupported_format``.

Nothing here writes to disk: audio lives in memory for the length of one
request, and ffmpeg is fed and read through pipes.
"""
from __future__ import annotations

import shutil
import struct
import subprocess
import sys
from array import array
from dataclasses import dataclass
from itertools import accumulate

MAX_DURATION_MS = 600_000              # Meta: 10 minutes
MAX_WAV_BYTES = 32 * 1024 * 1024       # Meta: 32 MB (the WAV we send)
# What we accept from the browser: 20 MiB. The web app's longest take is 10 minutes of 16 kHz mono 16-bit WAV
# (19,200,044 bytes); api/security.py caps the request body at this plus 4 KiB.
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
ACCEPTED_RATES = (16_000, 24_000)
TARGET_RATE = 16_000
PURE_MAX_OUTPUT_S = 180                # pure-Python conversion work bound
FFMPEG_TIMEOUT_S = 40

# Declared Content-Type values we accept (parameters such as ";codecs=opus"
# are ignored). The bytes are sniffed as well: the header alone decides nothing.
ALLOWED_CONTENT_TYPES = frozenset({
    "audio/wav", "audio/x-wav", "audio/wave", "audio/vnd.wave",
    "audio/webm", "video/webm",          # MediaRecorder labels audio-only WebM either way
    "audio/ogg", "application/ogg",
    "audio/mp4", "audio/x-m4a", "audio/m4a",
    "audio/mpeg", "audio/mp3",
})

# sniffed family -> ffmpeg demuxer name (passed with -f so ffmpeg never probes)
_FFMPEG_DEMUXER = {
    "wav": "wav",
    "webm": "matroska,webm",
    "ogg": "ogg",
    "mp4": "mov,mp4,m4a,3gp,3g2,mj2",
    "mp3": "mp3",
}


class AudioError(Exception):
    """A refusal with a stable code and the HTTP status the route serves."""

    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass(frozen=True)
class Prepared:
    wav: bytes              # canonical mono 16-bit PCM WAV, ready for Meta
    rate: int               # 16000 or 24000
    duration_ms: int
    source_format: str      # wav | webm | ogg | mp4 | mp3
    via: str                # passthrough | python | ffmpeg


# --------------------------------------------------------------------- sniff

def normalise_content_type(value: str | None) -> str:
    return (value or "").split(";", 1)[0].strip().lower()


def sniff(data: bytes) -> str | None:
    """The container family of ``data`` from its leading bytes, or None."""
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "wav"
    if data[:4] == b"\x1a\x45\xdf\xa3":
        return "webm"
    if data[:4] == b"OggS":
        return "ogg"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return "mp4"
    if data[:3] == b"ID3" or (len(data) > 2 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0):
        return "mp3"
    return None


def ffmpeg_path() -> str | None:
    return shutil.which("ffmpeg")


# ----------------------------------------------------------------------- WAV

@dataclass
class _Wav:
    tag: int
    channels: int
    rate: int
    bits: int
    data: memoryview        # the sample bytes, truncated to whole frames


def _parse_wav(blob: bytes) -> _Wav:
    """Read a RIFF/WAVE container. Tolerates streamed files whose data size
    is 0 or 0xFFFFFFFF (ffmpeg pipes, some recorders): the data chunk then
    runs to the end of the buffer."""
    if len(blob) < 12 or blob[:4] != b"RIFF" or blob[8:12] != b"WAVE":
        raise AudioError("audio_invalid", "not a RIFF/WAVE file")
    pos = 12
    fmt = None
    mv = memoryview(blob)
    while pos + 8 <= len(blob):
        cid = bytes(mv[pos:pos + 4])
        (size,) = struct.unpack_from("<I", blob, pos + 4)
        body = pos + 8
        if cid == b"fmt ":
            if size < 16 or body + 16 > len(blob):
                raise AudioError("audio_invalid", "WAV fmt chunk is truncated")
            tag, ch, rate, _brate, _align, bits = struct.unpack_from("<HHIIHH", blob, body)
            if tag == 0xFFFE and size >= 26 and body + 26 <= len(blob):
                (tag,) = struct.unpack_from("<H", blob, body + 24)  # extensible sub-format
            fmt = (tag, ch, rate, bits)
        elif cid == b"data":
            if fmt is None:
                raise AudioError("audio_invalid", "WAV data chunk precedes fmt chunk")
            end = body + size
            if size in (0, 0xFFFFFFFF) or end > len(blob):
                end = len(blob)
            tag, ch, rate, bits = fmt
            if ch < 1 or ch > 8 or rate < 1000 or rate > 192_000:
                raise AudioError("audio_invalid", "WAV header has an implausible channel count or sample rate")
            frame = ch * (bits // 8)
            if bits % 8 or frame == 0:
                raise AudioError("unsupported_format", f"unsupported WAV sample width ({bits} bits)")
            end = body + ((end - body) // frame) * frame
            return _Wav(tag, ch, rate, bits, mv[body:end])
        pos = body + size + (size & 1)
    raise AudioError("audio_invalid", "WAV has no data chunk")


def _to_i16(w: _Wav) -> array:
    """Interleaved samples as signed 16-bit (truncating deeper formats)."""
    raw = bytes(w.data)
    if w.tag == 1 and w.bits == 16:
        a = array("h")
        a.frombytes(raw)
    elif w.tag == 1 and w.bits == 8:
        a = array("h", [(b - 128) << 8 for b in raw])
    elif w.tag == 1 and w.bits == 24:
        out = bytearray(len(raw) // 3 * 2)
        out[0::2] = raw[1::3]
        out[1::2] = raw[2::3]
        a = array("h")
        a.frombytes(bytes(out))
    elif w.tag == 1 and w.bits == 32:
        out = bytearray(len(raw) // 4 * 2)
        out[0::2] = raw[2::4]
        out[1::2] = raw[3::4]
        a = array("h")
        a.frombytes(bytes(out))
    elif w.tag == 3 and w.bits == 32:
        f = array("f")
        f.frombytes(raw)
        if sys.byteorder == "big":
            f.byteswap()
        a = array("h", [max(-32768, min(32767, int(round(x * 32767.0)))) for x in f])
        return a
    else:
        raise AudioError("unsupported_format",
                         f"unsupported WAV encoding (format {w.tag}, {w.bits} bits); "
                         "send 16-bit PCM")
    if sys.byteorder == "big":
        a.byteswap()
    return a


def _downmix(a: array, channels: int) -> array:
    if channels == 1:
        return a
    chans = [a[c::channels] for c in range(channels)]
    if channels == 2:
        return array("h", [(x + y) // 2 for x, y in zip(*chans)])
    return array("h", [sum(t) // channels for t in zip(*chans)])


def resample(samples: array, src: int, dst: int) -> array:
    """Resample mono 16-bit audio. Downsampling area-averages over each
    output sample's span (a box low-pass, enough to keep speech clean for
    recognition); upsampling interpolates linearly. Pure Python."""
    n = len(samples)
    if src == dst or n == 0:
        return samples
    m = n * dst // src
    ratio = src / dst
    out = array("h")
    if src > dst:
        prefix = [0]
        prefix.extend(accumulate(samples))

        def cum(x: float) -> float:
            i = int(x)
            if i >= n:
                return float(prefix[n])
            return prefix[i] + (x - i) * samples[i]

        for j in range(m):
            a = j * ratio
            v = (cum(a + ratio) - cum(a)) / ratio
            out.append(max(-32768, min(32767, int(round(v)))))
    else:
        last = n - 1
        for j in range(m):
            pos = j * ratio
            i = int(pos)
            frac = pos - i
            s0 = samples[i] if i < n else samples[last]
            s1 = samples[i + 1] if i + 1 < n else samples[last]
            out.append(int(round(s0 + (s1 - s0) * frac)))
    return out


def encode_wav(samples: array, rate: int) -> bytes:
    """Canonical 44-byte-header mono 16-bit PCM WAV."""
    pcm = array("h", samples)
    if sys.byteorder == "big":
        pcm.byteswap()
    data = pcm.tobytes()
    header = (b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
              + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
              + b"data" + struct.pack("<I", len(data)))
    return header + data


# ------------------------------------------------------------------- ffmpeg

def _ffmpeg_to_wav(data: bytes, family: str) -> bytes:
    exe = ffmpeg_path()
    if exe is None:
        raise AudioError(
            "unsupported_format",
            f"cannot convert {family} audio on this server (no ffmpeg); "
            "send 16 kHz mono 16-bit WAV", 415)
    cmd = [exe, "-nostdin", "-hide_banner", "-loglevel", "error",
           "-protocol_whitelist", "pipe", "-f", _FFMPEG_DEMUXER[family], "-i", "pipe:0",
           "-vn", "-sn", "-dn", "-ac", "1", "-ar", str(TARGET_RATE),
           "-t", str(MAX_DURATION_MS // 1000 + 5),
           "-c:a", "pcm_s16le", "-f", "wav", "pipe:1"]
    try:
        proc = subprocess.run(cmd, input=data, capture_output=True,
                              timeout=FFMPEG_TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired:
        raise AudioError("audio_invalid", "audio conversion timed out", 422) from None
    except OSError:
        raise AudioError("unsupported_format",
                         "audio conversion is unavailable on this server", 415) from None
    if proc.returncode != 0 or not proc.stdout:
        raise AudioError("audio_invalid", f"the {family} audio could not be decoded", 422)
    return proc.stdout


# --------------------------------------------------------------------- main

def _check_duration(duration_ms: int) -> None:
    if duration_ms <= 0:
        raise AudioError("audio_empty", "the recording contains no audio", 422)
    if duration_ms > MAX_DURATION_MS:
        raise AudioError("audio_too_long",
                         f"recording is {duration_ms / 1000:.1f} s; the limit is "
                         f"{MAX_DURATION_MS // 1000} s", 413)


def prepare(data: bytes, content_type: str | None) -> Prepared:
    """Validate ``data`` and return Meta-ready WAV. Raises AudioError.

    Every size and duration check happens here, before any network call."""
    ctype = normalise_content_type(content_type)
    if ctype not in ALLOWED_CONTENT_TYPES:
        raise AudioError("unsupported_format",
                         f"content type '{ctype or 'missing'}' is not accepted; "
                         "send audio/wav (16 kHz mono 16-bit)", 415)
    if not data:
        raise AudioError("audio_empty", "the request body is empty", 422)
    if len(data) > MAX_UPLOAD_BYTES:
        raise AudioError("audio_too_large",
                         f"upload exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB", 413)
    family = sniff(data)
    if family is None:
        raise AudioError("unsupported_format",
                         "the bytes are not WAV, WebM, Ogg, MP4 or MP3 audio", 415)

    if family != "wav":
        wav = _ffmpeg_to_wav(data, family)
        w = _parse_wav(wav)
        pcm = _to_i16(w)
        _check_duration(len(pcm) * 1000 // w.rate)
        return _finish(pcm, w.rate, family, "ffmpeg")

    w = _parse_wav(data)
    frames = len(w.data) // (w.channels * (w.bits // 8))
    _check_duration(frames * 1000 // w.rate)         # header-only, before any decode

    if w.tag == 1 and w.bits == 16 and w.channels == 1 and w.rate in ACCEPTED_RATES:
        pcm = array("h")
        pcm.frombytes(bytes(w.data))
        if sys.byteorder == "big":
            pcm.byteswap()
        return _finish(pcm, w.rate, "wav", "passthrough")

    out_s = frames / w.rate
    if out_s > PURE_MAX_OUTPUT_S:
        # Too much per-sample work for pure Python: ffmpeg, or a clear refusal.
        wav = _ffmpeg_to_wav(data, "wav")
        w2 = _parse_wav(wav)
        pcm = _to_i16(w2)
        return _finish(pcm, w2.rate, "wav", "ffmpeg")
    pcm = _downmix(_to_i16(w), w.channels)
    if w.rate not in ACCEPTED_RATES:
        pcm = resample(pcm, w.rate, TARGET_RATE)
        rate = TARGET_RATE
    else:
        rate = w.rate
    return _finish(pcm, rate, "wav", "python")


def _finish(pcm: array, rate: int, family: str, via: str) -> Prepared:
    duration_ms = len(pcm) * 1000 // rate
    _check_duration(duration_ms)
    wav = encode_wav(pcm, rate)
    if len(wav) > MAX_WAV_BYTES:
        raise AudioError("audio_too_large", "converted audio exceeds 32 MB", 413)
    return Prepared(wav=wav, rate=rate, duration_ms=duration_ms,
                    source_format=family, via=via)
