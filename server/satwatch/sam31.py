"""SAM 3.1 on the Meta Model API: wire format, mask codec, request builder.

Pure functions plus a small transport; no state, no metering (see
``sam_meter.py``) and no overlay mapping (see ``detect.MetaSamDetector``).

Contract, verified against the official docs
(https://dev.meta.ai/docs/sam/segmenting, /sam/reading-segmentation) AND
against recorded live replies (fixtures/sam/):

Request  ``POST https://api.meta.ai/v1/responses``, bearer auth::

    {"model": "sam-3.1", "stream": true,
     "input": [{"role": "user", "content": [
         {"type": "input_text",  "text": "<ONE short noun phrase>"},
         {"type": "input_image", "image_url": "data:image/png;base64,..."}]}],
     "metadata": {"include_confidence": "true", "mask_encoding": "one_bit"}}

One concept per request (a comma list returns zero matches). The reply is
an SSE stream (``event: response.*`` / ``data: {json}``); the geometry is
the TEXT of ``response.output_text.delta`` (one delta carries the one
line of a single image) and is repeated in ``response.completed``. The
final ``usage`` carries ``images_processed`` (no price, no tokens).
Zero matches is a normal reply: ``response.completed`` with empty text
and no delta event.

Text grammar (a single image is frame ``<0f>``)::

    <0f>ID<|box;x1=..;y1=..;x2=..;y2=..;w=W;h=H;c=C|><|mask;x=0;y=0;c=C;data=MH,MW,<enc>PAYLOAD|>,ID<|box...

* ``x1,y1,x2,y2``: source pixels, **inclusive** corners (a mask of an
  ``x1=17..x2=76`` box is 60 columns wide); ``w,h``: the source size.
* ``c``: confidence 0..1, only because ``include_confidence`` is set. The
  live service returned nothing below ~0.50.
* ``data=MH,MW,``: the mask raster, rows then columns, covering exactly
  the box (``MH = y2-y1+1``, ``MW = x2-x1+1``); ``x=0;y=0`` is a literal
  and the raster is anchored at the box's top-left corner.
* ``<enc>``: ``~`` lossless (grey coverage, default), ``!`` one_bit. The
  payload is NOT plain base85: a 5-char length prefix, then 4-bytes-to-5
  chars in an 85-symbol alphabet (printable ASCII 0x21..0x7E minus
  ``" \\ , ; < > |``), carrying an adaptive binary arithmetic (range)
  coder over a 12-pixel causal context for one_bit. Fields are parsed by
  key, never by position.

Mask encoding choice: ``one_bit``. We only need the silhouette (to draw an
optional polygon outline and to size the object); one_bit payloads are
several times smaller than lossless, the coder is a small deterministic piece
of code, and it decodes every recorded real reply bit for bit. A reply that carries a ``~`` mask is not decoded
here: that object keeps its (valid) box and loses its outline; nothing is
guessed. Counts and boxes never depend on the mask codec: with
``SATWATCH_SAM_OUTLINES=0`` it is not run at all.

Origin of the mask codec: it implements the wire encoding that Meta's
``meta-sam-parser`` (SAM License) also implements; it was written from the
behaviour of that package, and no source file of it is bundled in this
repository. See ``NOTICE.md`` and ``licenses/SAM-LICENSE.txt`` at the
repository root for the licence copy and attribution terms.
"""
from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.request
from array import array
from dataclasses import dataclass, field
from typing import Callable, Optional

ENDPOINT = "https://api.meta.ai/v1/responses"
MODEL = "sam-3.1"
SOURCE_LABEL = "Meta Model API (api.meta.ai) / sam-3.1"
PRICE_PER_1000_IMAGES_USD = 2.50
PRICE_PER_IMAGE_USD = PRICE_PER_1000_IMAGES_USD / 1000.0
KEY_ENVS = ("META_API_KEY", "META_MODEL_API_KEY", "SUPERMAP_META_API_KEY")
MAX_IMAGE_BYTES = 16 * 1024 * 1024
MAX_MASK_PIXELS = 262_144        # decode budget per mask; bigger -> box only
MAX_RECORDS = 500                # a sane ceiling for one frame
MAX_REPLY_CHARS = 8_000_000


class SamError(RuntimeError):
    """Base class: nothing was counted."""


class SamCallError(SamError):
    """The call failed (HTTP error, transport error, failed response)."""


class SamResponseError(SamError):
    """The reply was incomplete or malformed: never counted partially."""


class SamMaskError(ValueError):
    """One mask payload could not be decoded (the box may still be good)."""


def resolve_key(env: Optional[dict] = None) -> Optional[str]:
    env = os.environ if env is None else env
    for name in KEY_ENVS:
        val = (env.get(name) or "").strip()
        if val:
            return val
    return None


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------

def build_request(prompt: str, image_bytes: bytes, *, mime: str = "image/png",
                  mask_encoding: str = "one_bit") -> dict:
    if mask_encoding not in ("one_bit", "lossless"):
        raise ValueError("mask_encoding must be one_bit or lossless")
    if not isinstance(prompt, str) or not prompt.strip() \
            or any(ch in prompt for ch in ",;\n+<>|"):
        raise ValueError("prompt must be one short noun phrase (no lists)")
    data_url = f"data:{mime};base64," + base64.b64encode(image_bytes).decode("ascii")
    return {
        "model": MODEL,
        "stream": True,
        "input": [{"role": "user", "content": [
            {"type": "input_text", "text": prompt.strip()},
            {"type": "input_image", "image_url": data_url},
        ]}],
        "metadata": {"include_confidence": "true", "mask_encoding": mask_encoding},
    }


HttpFn = Callable[[str, bytes, dict, float], "tuple[int, str]"]


def default_http(url: str, body: bytes, headers: dict, timeout: float) -> "tuple[int, str]":
    """POST ``body``; return (status, text). HTTP error statuses are
    RETURNED (the caller maps them); transport failures raise.
    """
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def request_headers(api_key: str) -> dict:
    return {"Content-Type": "application/json", "Accept": "text/event-stream",
            "Authorization": f"Bearer {api_key}"}


# ---------------------------------------------------------------------------
# Reply parsing
# ---------------------------------------------------------------------------

@dataclass
class SamMask:
    height: int
    width: int
    encoding: str          # "one_bit" | "lossless"
    payload: str           # marker included
    confidence: Optional[float] = None


@dataclass
class SamRecord:
    object_id: int
    frame: int
    x1: int
    y1: int
    x2: int            # inclusive
    y2: int            # inclusive
    w: int
    h: int
    confidence: Optional[float]
    mask: Optional[SamMask]

    @property
    def box_w(self) -> int:
        return self.x2 - self.x1 + 1

    @property
    def box_h(self) -> int:
        return self.y2 - self.y1 + 1


@dataclass
class SamReply:
    text: str
    records: list = field(default_factory=list)
    usage: Optional[dict] = None
    response_id: Optional[str] = None
    stream_form: str = "text"       # sse | json | text

    @property
    def images_processed(self) -> Optional[int]:
        v = (self.usage or {}).get("images_processed")
        return v if isinstance(v, int) and not isinstance(v, bool) else None


_FRAME_RE = re.compile(r"<(\d+)f>")
_RECORD_RE = re.compile(r"(\d+)<\|box;([^|]*)\|><\|mask;([^|]*)\|>")
_INT_RE = re.compile(r"^-?\d+$")


def _fields(blob: str) -> dict:
    out = {}
    for part in blob.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v
    return out


def _int(fields: dict, key: str) -> int:
    v = fields.get(key)
    if v is None or not _INT_RE.match(v):
        raise SamResponseError(f"box field {key!r} missing or not an integer")
    return int(v)


def _conf(fields: dict) -> Optional[float]:
    v = fields.get("c")
    if v is None:
        return None
    try:
        c = float(v)
    except ValueError as exc:
        raise SamResponseError("confidence is not a number") from exc
    if not (0.0 <= c <= 1.0):
        raise SamResponseError("confidence outside 0..1")
    return c


def parse_sam_text(text: str) -> list[SamRecord]:
    """Parse the segmentation text into records. STRICT: any leftover
    text that is not a frame marker, a record or a separator raises
    SamResponseError, because a silently dropped record would under-count.
    Empty text is the valid zero-match reply and returns []."""
    if text is None:
        raise SamResponseError("no reply text")
    if len(text) > MAX_REPLY_CHARS:
        raise SamResponseError("reply text exceeds the size ceiling")
    if not text.strip():
        return []
    frames = list(_FRAME_RE.finditer(text))
    if not frames or text[:frames[0].start()].strip(" \r\n\t,"):
        raise SamResponseError("reply does not start with a frame marker")
    records: list[SamRecord] = []
    for i, fm in enumerate(frames):
        frame = int(fm.group(1))
        end = frames[i + 1].start() if i + 1 < len(frames) else len(text)
        body = text[fm.end():end]
        pos = 0
        for m in _RECORD_RE.finditer(body):
            gap = body[pos:m.start()]
            if gap.strip(" \r\n\t,"):
                raise SamResponseError("unparseable text between records")
            pos = m.end()
            records.append(_record(frame, m))
        if body[pos:].strip(" \r\n\t,"):
            raise SamResponseError("unparseable trailing text after the last record")
        if len(records) > MAX_RECORDS:
            raise SamResponseError("too many records in one reply")
    seen = set()
    for r in records:
        key = (r.frame, r.object_id)
        if key in seen:
            raise SamResponseError(f"duplicate object id {r.object_id} on frame {r.frame}")
        seen.add(key)
    return records


def _record(frame: int, m: "re.Match") -> SamRecord:
    oid = int(m.group(1))
    bf, mf = _fields(m.group(2)), _fields(m.group(3))
    x1, y1, x2, y2 = (_int(bf, k) for k in ("x1", "y1", "x2", "y2"))
    w, h = _int(bf, "w"), _int(bf, "h")
    if not (w > 0 and h > 0 and 0 <= x1 <= x2 < w and 0 <= y1 <= y2 < h):
        raise SamResponseError(f"object {oid}: box outside the {w}x{h} frame")
    conf = _conf(bf)
    mconf = _conf(mf)
    if conf is not None and mconf is not None and abs(conf - mconf) > 1e-6:
        raise SamResponseError(f"object {oid}: box and mask confidence disagree")
    mask = None
    data = mf.get("data")
    if data is None:
        raise SamResponseError(f"object {oid}: mask has no data field")
    dm = re.match(r"^(\d+),(\d+),(.+)$", data, re.S)
    if not dm:
        raise SamResponseError(f"object {oid}: mask data is not H,W,payload")
    mh, mw, rest = int(dm.group(1)), int(dm.group(2)), dm.group(3)
    enc = {"!": "one_bit", "~": "lossless"}.get(rest[:1])
    if enc is None:
        raise SamResponseError(f"object {oid}: unknown mask encoding marker {rest[:1]!r}")
    if mh <= 0 or mw <= 0:
        raise SamResponseError(f"object {oid}: empty mask raster")
    mask = SamMask(height=mh, width=mw, encoding=enc, payload=rest,
                   confidence=mconf if mconf is not None else conf)
    return SamRecord(object_id=oid, frame=frame, x1=x1, y1=y1, x2=x2, y2=y2,
                     w=w, h=h, confidence=conf if conf is not None else mconf,
                     mask=mask)


def _walk_json_text(doc: dict) -> str:
    if isinstance(doc.get("output_text"), str):
        return doc["output_text"]
    parts = []
    for item in doc.get("output") or []:
        if not isinstance(item, dict):
            continue
        for c in item.get("content") or []:
            if isinstance(c, dict) and c.get("type") in (None, "output_text") \
                    and isinstance(c.get("text"), str):
                parts.append(c["text"])
    return "".join(parts)


def _check_final_response(resp: dict) -> None:
    status = resp.get("status")
    if resp.get("error"):
        err = resp["error"]
        msg = err.get("message") if isinstance(err, dict) else str(err)
        raise SamCallError(f"response error: {str(msg)[:200]}")
    if status not in (None, "completed"):
        raise SamResponseError(f"response status {status!r}, not completed")


def parse_reply(body, *, content_type: Optional[str] = None) -> SamReply:
    """Parse a full reply body: the SSE stream, the non-streaming JSON
    response, or bare segmentation text. A stream that never reaches
    ``response.completed`` is an error (truncated: never counted)."""
    if isinstance(body, (bytes, bytearray)):
        body = bytes(body).decode("utf-8", errors="replace")
    if not isinstance(body, str):
        raise SamResponseError("reply body is not text")
    stripped = body.lstrip()
    if stripped.startswith("{"):
        try:
            doc = json.loads(stripped)
        except ValueError as exc:
            raise SamResponseError("reply looks like JSON but does not parse") from exc
        if isinstance(doc.get("error"), dict):
            raise SamCallError(f"api error: {str(doc['error'].get('message'))[:200]}")
        resp = doc.get("response") if isinstance(doc.get("response"), dict) else doc
        _check_final_response(resp)
        text = _walk_json_text(resp)
        usage = resp.get("usage") if isinstance(resp.get("usage"), dict) else None
        return SamReply(text=text, records=parse_sam_text(text), usage=usage,
                        response_id=resp.get("id"), stream_form="json")
    if stripped.startswith(("event:", "data:", ":")):
        return _parse_sse(stripped)
    return SamReply(text=body.strip(), records=parse_sam_text(body.strip()),
                    stream_form="text")


def _parse_sse(body: str) -> SamReply:
    deltas: list[str] = []
    completed: Optional[dict] = None
    for raw in body.splitlines():
        line = raw.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            ev = json.loads(data)
        except ValueError as exc:
            raise SamResponseError("a stream event is not valid JSON") from exc
        if not isinstance(ev, dict):
            continue
        etype = ev.get("type")
        if etype == "response.output_text.delta":
            d = ev.get("delta")
            if isinstance(d, str):
                deltas.append(d)
        elif etype in ("response.failed", "response.error", "error"):
            err = ev.get("error") or (ev.get("response") or {}).get("error") or {}
            msg = err.get("message") if isinstance(err, dict) else str(err)
            raise SamCallError(f"stream reported failure: {str(msg)[:200]}")
        elif etype == "response.incomplete":
            raise SamResponseError("stream reported an incomplete response")
        elif etype == "response.completed":
            completed = ev.get("response") if isinstance(ev.get("response"), dict) else {}
    if completed is None:
        raise SamResponseError("stream ended without response.completed (truncated)")
    _check_final_response(completed)
    text = "".join(deltas)
    final_text = _walk_json_text(completed)
    if not text:
        text = final_text
    elif final_text and final_text != text:
        raise SamResponseError("streamed text differs from the completed response text")
    usage = completed.get("usage") if isinstance(completed.get("usage"), dict) else None
    return SamReply(text=text, records=parse_sam_text(text), usage=usage,
                    response_id=completed.get("id"), stream_form="sse")


# ---------------------------------------------------------------------------
# Mask codec (one_bit)
# ---------------------------------------------------------------------------

_EXCLUDED = frozenset('"\\,;<>|')
_ALPHABET = "".join(c for c in map(chr, range(0x21, 0x7F)) if c not in _EXCLUDED)
_RADIX = 85
_VALUE = {c: i for i, c in enumerate(_ALPHABET[:_RADIX])}
_PREFIX = 5
_MASK32 = 0xFFFFFFFF
_TOP = 1 << 24
_BOTTOM = 1 << 16
_INCREMENT = 14
_COUNT_LIMIT = 4096
_PAD = 2


def _unpack(payload: str) -> bytes:
    if len(payload) < _PREFIX:
        raise SamMaskError("payload shorter than its length prefix")
    try:
        length = 0
        for ch in payload[:_PREFIX]:
            length = length * _RADIX + _VALUE[ch]
    except KeyError as exc:
        raise SamMaskError("payload has a character outside the alphabet") from exc
    rem = length % 4
    expected = _PREFIX + (length // 4) * 5 + (0 if rem == 0 else rem + 1)
    if len(payload) != expected or length > MAX_REPLY_CHARS:
        raise SamMaskError("payload length does not match its prefix")
    out = bytearray(length)
    src = _PREFIX
    dst = 0
    try:
        for _ in range(length // 4):
            v = 0
            for ch in payload[src:src + 5]:
                v = v * _RADIX + _VALUE[ch]
            if v > _MASK32:
                raise SamMaskError("payload group out of range")
            out[dst:dst + 4] = v.to_bytes(4, "big")
            src += 5
            dst += 4
        if rem:
            v = 0
            for i in range(5):
                v = v * _RADIX + (_VALUE[payload[src + i]] if i < rem + 1 else _RADIX - 1)
            if v > _MASK32:
                raise SamMaskError("payload tail out of range")
            out[dst:dst + rem] = v.to_bytes(4, "big")[:rem]
    except KeyError as exc:
        raise SamMaskError("payload has a character outside the alphabet") from exc
    return bytes(out)


class _RangeDecoder:
    def __init__(self, data: bytes) -> None:
        if len(data) < 4:
            raise SamMaskError("payload too short for the range coder")
        self.data, self.pos = data, 0
        self.low, self.range, self.code, self.scaled = 0, _MASK32, 0, 0
        for _ in range(4):
            self.code = ((self.code << 8) | self._read()) & _MASK32

    def _read(self) -> int:
        if self.pos >= len(self.data):
            raise SamMaskError("payload ended before the mask was complete")
        b = self.data[self.pos]
        self.pos += 1
        return b

    def freq(self, total: int) -> int:
        self.scaled = self.range // total
        if self.scaled == 0:
            raise SamMaskError("invalid coder state")
        v = ((self.code - self.low) & _MASK32) // self.scaled
        return total - 1 if v >= total else v

    def advance(self, cum: int, freq: int) -> None:
        self.low = (self.low + self.scaled * cum) & _MASK32
        self.range = self.scaled * freq
        while True:
            if ((self.low ^ ((self.low + self.range) & _MASK32)) & _MASK32) < _TOP:
                pass
            elif self.range < _BOTTOM:
                self.range = (-self.low & _MASK32) & (_BOTTOM - 1)
            else:
                break
            self.code = ((self.code << 8) | self._read()) & _MASK32
            self.low = (self.low << 8) & _MASK32
            self.range = (self.range << 8) & _MASK32


def decode_one_bit(payload: str, height: int, width: int) -> bytes:
    """Decode a ``!`` mask payload to a row-major 0/1 raster of exactly
    ``height * width`` bytes, or raise SamMaskError."""
    if not payload.startswith("!"):
        raise SamMaskError("not a one_bit payload")
    if height <= 0 or width <= 0 or height * width > MAX_MASK_PIXELS:
        raise SamMaskError("mask raster outside the decode budget")
    dec = _RangeDecoder(_unpack(payload[1:]))
    counts = array("H", [1]) * (4096 * 2)
    pw = width + 2 * _PAD
    padded = bytearray(pw * (height + _PAD))
    out = bytearray(width * height)
    for y in range(height):
        row = (y + _PAD) * pw + _PAD
        for x in range(width):
            p = row + x
            q = p - pw
            e = p - 2 * pw
            ctx = (padded[p - 1] | (padded[p - 2] << 1)
                   | (padded[q - 2] << 2) | (padded[q - 1] << 3) | (padded[q] << 4)
                   | (padded[q + 1] << 5) | (padded[q + 2] << 6)
                   | (padded[e - 2] << 7) | (padded[e - 1] << 8) | (padded[e] << 9)
                   | (padded[e + 1] << 10) | (padded[e + 2] << 11))
            off = ctx << 1
            zero, one = counts[off], counts[off + 1]
            bit = 0 if dec.freq(zero + one) < zero else 1
            if bit == 0:
                dec.advance(0, zero)
            else:
                dec.advance(zero, one)
            if bit:
                padded[p] = 1
                out[y * width + x] = 1
            counts[off + bit] = (counts[off + bit] + _INCREMENT) & 0xFFFF
            if counts[off] + counts[off + 1] >= _COUNT_LIMIT:
                counts[off] = (counts[off] >> 1) or 1
                counts[off + 1] = (counts[off + 1] >> 1) or 1
    return bytes(out)


def decode_mask(mask: SamMask) -> bytes:
    """Row-major 0/1 raster for ``mask`` (one_bit only)."""
    if mask.encoding != "one_bit":
        raise SamMaskError(f"{mask.encoding} masks are not decoded here")
    return decode_one_bit(mask.payload, mask.height, mask.width)


# ---------------------------------------------------------------------------
# Raster -> polygon rings (pixel space, y down)
# ---------------------------------------------------------------------------

def _simplify(ring: list, eps: float) -> list:
    """Drop collinear points, then Douglas-Peucker with ``eps`` pixels."""
    n = len(ring)
    pts = [p for i, p in enumerate(ring)
           if (ring[i - 1][0] - p[0]) * (ring[(i + 1) % n][1] - p[1])
           != (ring[i - 1][1] - p[1]) * (ring[(i + 1) % n][0] - p[0])]
    if len(pts) <= 4 or eps <= 0:
        return pts

    def dp(seq):
        if len(seq) < 3:
            return seq
        (ax, ay), (bx, by) = seq[0], seq[-1]
        dx, dy = bx - ax, by - ay
        norm = (dx * dx + dy * dy) ** 0.5 or 1.0
        best, idx = -1.0, 0
        for i in range(1, len(seq) - 1):
            d = abs(dy * (seq[i][0] - ax) - dx * (seq[i][1] - ay)) / norm
            if d > best:
                best, idx = d, i
        if best <= eps:
            return [seq[0], seq[-1]]
        return dp(seq[:idx + 1])[:-1] + dp(seq[idx:])

    # split the closed ring at its two farthest-apart vertices so the
    # simplification has stable anchors
    b = max(range(len(pts)),
            key=lambda i: (pts[i][0] - pts[0][0]) ** 2 + (pts[i][1] - pts[0][1]) ** 2)
    out = dp(pts[:b + 1])[:-1] + dp(pts[b:] + [pts[0]])[:-1]
    return out if len(out) >= 3 else pts


def raster_to_rings(raster: bytes, height: int, width: int, *,
                    eps: float = 0.7) -> list[list[tuple[int, int]]]:
    """Outer boundaries (clockwise on screen) of every connected
    component of the 0/1 ``raster``, as rings of lattice points
    (x right, y down, in raster pixels). Holes are dropped. Components
    are 4-connected: a diagonal touch is two objects' worth of ring, not
    one pinched ring."""
    if len(raster) != height * width:
        raise SamMaskError("raster size does not match its dimensions")

    def fg(x: int, y: int) -> bool:
        return 0 <= x < width and 0 <= y < height and raster[y * width + x] == 1

    nxt: dict = {}
    for y in range(height):
        for x in range(width):
            if raster[y * width + x] != 1:
                continue
            if not fg(x, y - 1):
                nxt.setdefault((x, y), []).append((x + 1, y))
            if not fg(x + 1, y):
                nxt.setdefault((x + 1, y), []).append((x + 1, y + 1))
            if not fg(x, y + 1):
                nxt.setdefault((x + 1, y + 1), []).append((x, y + 1))
            if not fg(x - 1, y):
                nxt.setdefault((x, y + 1), []).append((x, y))

    def turn_rank(d_in, d_out):
        # prefer the rightmost turn (clockwise walk, fg on the right)
        cross = d_in[0] * d_out[1] - d_in[1] * d_out[0]
        dot = d_in[0] * d_out[0] + d_in[1] * d_out[1]
        return (0 if cross > 0 else 1 if dot > 0 else 2 if cross == 0 else 3)

    rings = []
    budget = 4 * (len(nxt) + 8)
    for start in sorted(nxt):
        while nxt.get(start):
            ring = [start]
            prev, cur = start, nxt[start].pop(0)
            steps = 0
            while cur != start:
                ring.append(cur)
                outs = nxt.get(cur)
                steps += 1
                if not outs or steps > budget:
                    ring = []
                    break
                d_in = (cur[0] - prev[0], cur[1] - prev[1])
                if len(outs) > 1:
                    outs.sort(key=lambda o: turn_rank(d_in, (o[0] - cur[0], o[1] - cur[1])))
                prev, cur = cur, outs.pop(0)
            n = len(ring)
            if n < 4:
                continue
            area2 = sum(ring[i][0] * ring[(i + 1) % n][1]
                        - ring[(i + 1) % n][0] * ring[i][1] for i in range(n))
            if area2 > 0:                      # outer ring; holes are negative
                simp = _simplify(ring, eps)
                if len(simp) >= 3:
                    rings.append(simp)
    return rings


def foreground_pixels(raster: bytes) -> int:
    return sum(raster)
