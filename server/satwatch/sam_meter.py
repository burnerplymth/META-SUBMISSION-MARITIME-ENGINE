"""SAM call meter: count every Meta SAM request, price it, cap it per day.

SAM calls go to a different endpoint from the LLM and are NOT in the LLM
spend meter (``GET /llm/status``). This module is their ledger:

    <SUPERMAP_DATA_DIR or <repo>/data>/satwatch/sam_ledger.jsonl
    (override: SATWATCH_SAM_LEDGER_PATH)

Rules (fail closed, like the LLM cap):

* A call is RESERVED in the ledger under an exclusive file lock BEFORE the
  request is sent; the reservation is refused when today's calls already
  equal the cap, so parallel scans cannot overshoot it.
* The cap is ``SATWATCH_SAM_DAILY_CAP`` calls per UTC day (images; one
  call = one image). Unset or unparseable -> ``DEFAULT_DAILY_CAP``. ``0``
  refuses every call. A ledger that cannot be read or written refuses the
  call. ``SATWATCH_SAM_DISABLED=1`` (or the project-wide ``SUPERMAP_LLM_DISABLED=1``) refuses every call.
* A call counts once the request has been sent. It is released (not
  counted) only when the transport failed before any HTTP answer. Any HTTP
  answer, including an error status, stays counted: whether Meta bills a
  4xx is not published, so the meter assumes it does.
* Cost is ESTIMATED: ``images x $2.50 / 1000`` (the published price). The
  reply reports ``usage.images_processed`` and no price; when present it
  replaces the one-image estimate for that call.
"""
from __future__ import annotations

import fcntl
import json
import os
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

try:
    import envflag as _flags
except ImportError:  # imported as server.* from the repo root
    from server import envflag as _flags  # type: ignore

PRICE_PER_1000_IMAGES_USD = 2.50
PRICE_PER_IMAGE_USD = PRICE_PER_1000_IMAGES_USD / 1000.0
DEFAULT_DAILY_CAP = 100          # calls per UTC day = $0.25
ENV_CAP = "SATWATCH_SAM_DAILY_CAP"
ENV_DISABLED = "SATWATCH_SAM_DISABLED"
ENV_LEDGER = "SATWATCH_SAM_LEDGER_PATH"
REPO_ROOT = Path(__file__).resolve().parents[2]


class SamCapError(RuntimeError):
    """A SAM call was refused before it was sent (cap, kill switch or
    unusable ledger). Nothing was spent."""


def default_ledger_path() -> Path:
    override = os.environ.get(ENV_LEDGER)
    if override:
        return Path(override)
    root = Path(os.environ.get("SUPERMAP_DATA_DIR") or REPO_ROOT / "data")
    return root / "satwatch" / "sam_ledger.jsonl"


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _env_truthy(name: str) -> bool:
    return _flags.env_truthy(name)


def disabled() -> bool:
    """SATWATCH_SAM_DISABLED, or the project-wide Meta kill switch SUPERMAP_LLM_DISABLED."""
    return _env_truthy(ENV_DISABLED) or _env_truthy("SUPERMAP_LLM_DISABLED")


def resolve_cap() -> tuple[int, str]:
    """(calls per day, source). Bad input falls back to the default cap
    (never to 'unlimited')."""
    raw = (os.environ.get(ENV_CAP) or "").strip()
    if not raw:
        return DEFAULT_DAILY_CAP, "default"
    try:
        cap = int(raw)
        if cap < 0:
            raise ValueError
        return cap, "env"
    except ValueError:
        return DEFAULT_DAILY_CAP, "default (invalid env value ignored)"


class SamMeter:
    _thread_lock = threading.Lock()

    def __init__(self, path: Optional[Path] = None,
                 clock: Callable[[], str] = _utcnow) -> None:
        self.path = Path(path) if path else default_ledger_path()
        self._clock = clock

    # ----- ledger plumbing --------------------------------------------------
    @contextmanager
    def _locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_name(self.path.name + ".lock")
        with self._thread_lock, open(lock_path, "a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _read(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        with open(self.path, "r", encoding="utf-8") as fh:
            for n, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError as exc:
                    raise SamCapError(f"SAM ledger line {n} is unreadable: refusing "
                                      "calls until it is fixed") from exc
                if isinstance(rec, dict):
                    out.append(rec)
        return out

    def _append(self, rec: dict) -> None:
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def _day(self) -> str:
        return self._clock()[:10]

    @staticmethod
    def _totals(records: list[dict], day: str) -> dict:
        released = {r.get("ref") for r in records if r.get("type") == "release"}
        settled = {r.get("ref"): r for r in records if r.get("type") == "settle"}
        calls = images = 0
        http_errors = 0
        for r in records:
            if r.get("type") != "reserve" or not str(r.get("ts", "")).startswith(day):
                continue
            if r.get("id") in released:
                continue
            calls += 1
            s = settled.get(r.get("id"))
            n = s.get("images_processed") if s else None
            images += n if isinstance(n, int) and not isinstance(n, bool) and n >= 0 else 1
            if s and s.get("http_status") not in (None, 200):
                http_errors += 1
        return {"calls": calls, "images": images, "http_errors": http_errors}

    # ----- call lifecycle ---------------------------------------------------
    def reserve(self, *, prompt: str, purpose: str = "satwatch") -> str:
        """Count one call BEFORE it is sent; SamCapError when refused."""
        if disabled():
            raise SamCapError(f"SAM calls are disabled ({ENV_DISABLED} or SUPERMAP_LLM_DISABLED is set)")
        cap, _ = resolve_cap()
        ref = uuid.uuid4().hex
        try:
            with self._locked():
                totals = self._totals(self._read(), self._day())
                if totals["calls"] >= cap:
                    raise SamCapError(
                        f"daily SAM call cap reached: {totals['calls']} calls today >= "
                        f"cap {cap} ({ENV_CAP}); refusing the call")
                self._append({"type": "reserve", "id": ref, "ts": self._clock(),
                              "prompt": prompt, "purpose": purpose,
                              "model": "sam-3.1", "est_images": 1})
        except SamCapError:
            raise
        except OSError as exc:
            raise SamCapError(f"SAM ledger unavailable ({type(exc).__name__}): "
                              "refusing the call (fail closed)") from exc
        return ref

    def settle(self, ref: str, *, images_processed: Optional[int],
               http_status: Optional[int], response_id: Optional[str] = None) -> None:
        try:
            with self._locked():
                self._append({"type": "settle", "ref": ref, "ts": self._clock(),
                              "images_processed": images_processed,
                              "http_status": http_status, "response_id": response_id})
        except OSError:
            pass   # the reservation already counts the call; never mask the result

    def release(self, ref: str, reason: str) -> None:
        try:
            with self._locked():
                self._append({"type": "release", "ref": ref, "ts": self._clock(),
                              "reason": reason[:120]})
        except OSError:
            pass

    # ----- read side --------------------------------------------------------
    def status(self) -> dict:
        cap, source = resolve_cap()
        try:
            if self.path.exists():
                with self._locked():
                    totals = self._totals(self._read(), self._day())
            else:
                totals = {"calls": 0, "images": 0, "http_errors": 0}
            ledger_ok, ledger_err = True, None
        except (OSError, SamCapError) as exc:
            totals = {"calls": 0, "images": 0, "http_errors": 0}
            ledger_ok, ledger_err = False, type(exc).__name__
        calls = totals["calls"]
        return {
            "model": "sam-3.1",
            "calls_today": calls,
            "images_today": totals["images"],
            "http_errors_today": totals["http_errors"],
            "cost_usd_today": round(totals["images"] * PRICE_PER_IMAGE_USD, 6),
            "cost_basis": "ESTIMATED: images x $2.50 per 1,000 (published price); "
                          "the reply reports images_processed, not a price",
            "price_per_1000_images_usd": PRICE_PER_1000_IMAGES_USD,
            "cap_calls_per_day": cap,
            "cap_source": source,
            "remaining_calls_today": max(0, cap - calls),
            "cap_reached": calls >= cap,
            "disabled": disabled(),
            "ledger_ok": ledger_ok,
            "ledger_error": ledger_err,
            "separate_from_llm_meter": True,
        }
