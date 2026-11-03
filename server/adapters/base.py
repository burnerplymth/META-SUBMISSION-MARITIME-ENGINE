"""Adapter framework — base interface, provenance envelope, health state.

Every adapter implements ``fetch() -> FetchResult`` where FetchResult carries
``records`` plus a provenance envelope ``{source, retrieved_at, mode}`` with
``mode in {LIVE, SNAPSHOT, MODELED}``. A degraded adapter reports its true
mode — never LIVE for stale data, never silent zeros.

HTTP stack: Python standard library ``urllib`` only (see http_util.py; no
requests/httpx/websocket-client dependency is needed).
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

MODES = ("LIVE", "SNAPSHOT", "MODELED")
HEALTH_STATES = ("LIVE", "SNAPSHOT", "DOWN")

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("SUPERMAP_DATA_DIR", REPO_ROOT / "data"))
SNAPSHOT_DIR = DATA_DIR / "snapshots"


def utcnow_iso() -> str:
    """ISO 8601 UTC, seconds precision, Z suffix."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def provenance(source: str, mode: str, retrieved_at: Optional[str] = None) -> dict:
    if mode not in MODES:
        raise ValueError(f"invalid provenance mode: {mode!r}")
    return {
        "source": source,
        "retrieved_at": retrieved_at or utcnow_iso(),
        "mode": mode,
    }


@dataclass
class FetchResult:
    records: list
    provenance: dict

    def to_dict(self) -> dict:
        return {"records": self.records, "provenance": self.provenance}


DEFAULT_HEALTH_MAX_AGE_S = 6 * 3600


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def health_freshness(last_success_at: Optional[str],
                     max_age_s: float, now: Optional[datetime] = None) -> tuple:
    """(age_s, fresh) computed AT READ TIME: age is seconds
    since ``last_success_at`` (None when never / unparseable); fresh
    means a success exists and is no older than the per-adapter
    ``max_age_s``."""
    last = _parse_iso(last_success_at)
    if last is None:
        return None, False
    age_s = max(0.0, ((now or datetime.now(timezone.utc)) - last).total_seconds())
    return age_s, age_s <= max_age_s


@dataclass
class AdapterHealth:
    adapter: str
    state: str = "DOWN"            # LIVE | SNAPSHOT | DOWN (enum)
    last_success_at: Optional[str] = None
    mode: Optional[str] = None     # last fetch mode: LIVE | SNAPSHOT | MODELED
    note: Optional[str] = None
    max_age_s: float = DEFAULT_HEALTH_MAX_AGE_S

    def to_dict(self, now: Optional[datetime] = None) -> dict:
        age_s, fresh = health_freshness(self.last_success_at,
                                        self.max_age_s, now)
        state = self.state
        if state == "LIVE" and age_s is not None and not fresh:
            # A LIVE claim older than this adapter's max age is stale:
            # it reads as SNAPSHOT, with age/fresh saying why.
            state = "SNAPSHOT"
        out = {
            "adapter": self.adapter,
            "state": state,
            "last_success_at": self.last_success_at,
            "mode": self.mode,
            "fresh": fresh,
            "age_s": round(age_s, 3) if age_s is not None else None,
            "max_age_s": self.max_age_s,
        }
        if self.note:
            out["note"] = self.note
        return out


class AdapterError(Exception):
    """A live fetch failed (network, HTTP status, unparseable payload)."""


class NeedsKeyError(AdapterError):
    """The adapter cannot run at all until a user-supplied key exists."""


class BaseAdapter:
    """One writer per source. Subclasses implement ``_fetch_live()``.

    The base class owns the snapshot discipline:
      * a successful live fetch is written to the last-good snapshot pack
        under ``data/snapshots/<name>.json``;
      * a failed live fetch falls back to the pack, with every record's
        provenance rewritten to ``mode=SNAPSHOT`` and ``retrieved_at`` kept
        at the pack's original fetch time (staleness is visible, not hidden);
      * with no pack, ``fetch()`` raises — the registry then reports DOWN.
        It never fabricates records.
    """

    name: str = "base"                 # registry / health id
    source: str = "base"               # provenance.source string
    key_env: Optional[str] = None      # env var that must hold an API key
    snapshot_by_design: bool = False   # e.g. PortWatch loader
    empty_is_expected: bool = False    # a successful 0-record fetch is
                                       # legitimate for this adapter
    health_max_age_s: float = DEFAULT_HEALTH_MAX_AGE_S

    def __init__(self) -> None:
        self.health = AdapterHealth(adapter=self.name,
                                    max_age_s=self.health_max_age_s)
        self._lock = threading.Lock()

    def snapshot_only(self) -> bool:
        """True when ``SUPERMAP_SNAPSHOT_ONLY_FEEDS`` (comma list of adapter names, or ``all``)
        names this feed: no network call is made, the stored pack is served as SNAPSHOT."""
        raw = os.environ.get("SUPERMAP_SNAPSHOT_ONLY_FEEDS", "")
        names = {x.strip().lower() for x in raw.split(",") if x.strip()}
        return bool(names) and ("all" in names or self.name.lower() in names)

    # -- key gate ---------------------------------------------------------
    def has_key(self) -> bool:
        return bool(self.key_env and os.environ.get(self.key_env))

    def needs_key(self) -> bool:
        return bool(self.key_env) and not self.has_key()

    # -- snapshot pack ----------------------------------------------------
    @property
    def snapshot_path(self) -> Path:
        return SNAPSHOT_DIR / f"{self.name}.json"

    def _save_snapshot(self, result: FetchResult) -> None:
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        pack = {
            "adapter": self.name,
            "saved_at": utcnow_iso(),
            "provenance": result.provenance,
            "records": result.records,
        }
        # Per-writer unique temp name (the discipline the alerts
        # store already follows). The old
        # deterministic "<name>.json.tmp" let two writers of the same
        # pack — an abandoned fetch from a deadline-bound cycle and
        # the next cycle's fetch — consume each other's temp file.
        import uuid as _uuid
        tmp = self.snapshot_path.with_name(
            f"{self.snapshot_path.name}.{os.getpid()}."
            f"{_uuid.uuid4().hex}.tmp")
        tmp.write_text(json.dumps(pack, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.snapshot_path)

    def write_empty_snapshot(self, reason: str) -> None:
        """Create an explicit EMPTY last-good pack (zero records, reason
        recorded) when no live fetch has ever succeeded. An empty pack is
        honest data; a fabricated record is not."""
        if self.snapshot_path.exists():
            return
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        pack = {
            "adapter": self.name,
            "saved_at": utcnow_iso(),
            "provenance": provenance(self.source, "SNAPSHOT"),
            "records": [],
            "note": reason,
        }
        self.snapshot_path.write_text(
            json.dumps(pack, ensure_ascii=False, indent=1), encoding="utf-8")

    def _load_snapshot(self, reason: str) -> Optional[FetchResult]:
        if not self.snapshot_path.exists():
            return None
        try:
            pack = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        pack_prov = pack.get("provenance") or {}
        snap_prov = provenance(
            self.source, "SNAPSHOT",
            retrieved_at=pack_prov.get("retrieved_at"),
        )
        records = []
        for rec in pack.get("records", []):
            rec = dict(rec)
            rec["provenance"] = dict(snap_prov)  # relabel honestly as SNAPSHOT
            records.append(rec)
        return FetchResult(records=records, provenance=snap_prov)

    def _previous_pack_count(self) -> Optional[int]:
        if not self.snapshot_path.exists():
            return None
        try:
            pack = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
            records = pack.get("records")
            return len(records) if isinstance(records, list) else None
        except (OSError, json.JSONDecodeError):
            return None

    # -- public API -------------------------------------------------------
    def fetch(self) -> FetchResult:
        """Entry point. Raises AdapterError when DOWN with no snapshot."""
        with self._lock:
            if self.needs_key():
                # Key-gated: never connect, never fabricate. Snapshot pack
                # (normally empty) is still served, labelled.
                self.write_empty_snapshot(
                    f"NEEDS-KEY: no live fetch attempted; set {self.key_env}")
                snap = self._load_snapshot("needs-key")
                self.health.state = "DOWN"
                self.health.mode = "SNAPSHOT" if snap else None
                self.health.note = (
                    f"NEEDS-KEY: set {self.key_env} to enable live fetch"
                )
                if snap is not None:
                    return snap
                return FetchResult(
                    records=[],
                    provenance=provenance(self.source, "SNAPSHOT"),
                )
            if self.snapshot_only():
                # Deployment policy (licence): this feed serves its stored pack only.
                snap = self._load_snapshot("snapshot only")
                if snap is None:
                    self.health.state = "DOWN"
                    self.health.mode = None
                    self.health.note = "snapshot only by deployment policy; no snapshot pack"
                    raise AdapterError("snapshot only by deployment policy; no snapshot pack")
                self.health.state = "SNAPSHOT"
                self.health.mode = "SNAPSHOT"
                self.health.note = "snapshot only by deployment policy"
                return snap
            try:
                result = self._fetch_live()
            except Exception as exc:  # AdapterError, NetworkBlocked, URLError,
                # JSON errors, TLS failures — all are "live fetch failed".
                if isinstance(exc, KeyboardInterrupt):
                    raise
                snap = self._load_snapshot(str(exc))
                if snap is not None:
                    self.health.state = "SNAPSHOT"
                    self.health.mode = "SNAPSHOT"
                    self.health.note = f"live fetch failed ({exc}); serving last-good snapshot"
                    return snap
                self.health.state = "DOWN"
                self.health.mode = None
                self.health.note = f"live fetch failed ({exc}); no snapshot pack"
                raise AdapterError(str(exc)) from exc
            # Snapshot guard (generalising NTAD's
            # self-protection): a *successful* fetch returning 0
            # records, or fewer than half the last-good pack, does NOT
            # overwrite that pack — the old pack is served labelled
            # SNAPSHOT instead. Adapters that legitimately return
            # empty declare ``empty_is_expected``.
            prev_count = self._previous_pack_count()
            if not self.empty_is_expected and prev_count:
                shrunk = len(result.records) == 0 or \
                    len(result.records) < prev_count / 2
                if shrunk:
                    snap = self._load_snapshot("snapshot guard")
                    if snap is not None:
                        self.health.state = "SNAPSHOT"
                        self.health.mode = "SNAPSHOT"
                        self.health.note = (
                            f"live fetch returned {len(result.records)} "
                            f"record(s) vs last-good pack of {prev_count}; "
                            "snapshot guard kept the last-good pack "
                            "(not overwritten)")
                        return snap
            # success
            for rec in result.records:
                rec.setdefault("provenance", dict(result.provenance))
            self._save_snapshot(result)
            self.health.last_success_at = result.provenance["retrieved_at"]
            if self.snapshot_by_design or result.provenance["mode"] == "SNAPSHOT":
                self.health.state = "SNAPSHOT"
                self.health.mode = "SNAPSHOT"
                self.health.note = self.health.note or "snapshot loader by design"
            else:
                self.health.state = "LIVE"
                self.health.mode = result.provenance["mode"]
                self.health.note = None
            return result

    def _fetch_live(self) -> FetchResult:  # pragma: no cover - overridden
        raise NotImplementedError


__all__ = [
    "AdapterError", "AdapterHealth", "BaseAdapter", "DATA_DIR", "FetchResult",
    "HEALTH_STATES", "MODES", "NeedsKeyError", "REPO_ROOT", "SNAPSHOT_DIR",
    "health_freshness", "provenance", "utcnow_iso",
]
