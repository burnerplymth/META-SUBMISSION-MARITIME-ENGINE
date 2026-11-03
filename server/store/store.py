"""SupermAP store — JSON/JSONL persistence under ``data/``.

Enforces the record contracts at the write boundary:
  * a record missing its provenance envelope ``{source, retrieved_at,
    mode}`` is REJECTED (ProvenanceError) — never stored, never rendered.
  * DisruptionEvents validate against the event schema (kind/status
    enums, severity 1–5, capacity_loss rules, timestamp ordering) before
    they are appended to ``data/events.jsonl``.
  * Dedupe: one id per real-world occurrence — an event whose occurrence
    key (explicit ``dedupe_key`` or its ``id``) is already stored is not
    appended twice; ``append_event`` returns False instead.

Files:
  * ``data/events.jsonl``  — DisruptionEvents (append-only)
  * ``data/records.jsonl`` — other provenanced records (feed items, waits,
    port-activity snapshots), each line ``{record_type, provenance, record}``
"""
from __future__ import annotations

import contextlib
import copy
import fcntl
import json
import re
import threading
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Iterable, Optional

try:  # imported as server.store (namespace package from repo root)
    from ..adapters.base import DATA_DIR, MODES
except ImportError:  # imported as top-level `store` with server/ on sys.path
    from adapters.base import DATA_DIR, MODES  # type: ignore

TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

C3_KINDS = {
    "closure", "capacity_cut", "congestion", "strike", "weather",
    "grounding", "accident", "policy", "other",
}
C3_STATUSES = {"active", "developing", "resolved"}


class ProvenanceError(ValueError):
    """Record lacks a complete provenance envelope — store refuses it."""


class ValidationError(ValueError):
    """Record fails its schema — store refuses it."""


def check_provenance(prov) -> dict:
    if not isinstance(prov, dict):
        raise ProvenanceError("provenance envelope missing (not an object)")
    for field in ("source", "retrieved_at", "mode"):
        if not prov.get(field):
            raise ProvenanceError(f"provenance.{field} missing")
    if prov["mode"] not in MODES:
        raise ProvenanceError(f"provenance.mode {prov['mode']!r} not in {MODES}")
    if not TS_RE.match(str(prov["retrieved_at"])):
        raise ProvenanceError(
            f"provenance.retrieved_at {prov['retrieved_at']!r} is not ISO-8601 UTC seconds"
        )
    return prov


def _ts(value: str, field: str) -> datetime:
    if not isinstance(value, str) or not TS_RE.match(value):
        raise ValidationError(f"{field} {value!r} is not ISO-8601 UTC (seconds, Z)")
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")


def validate_c3_event(ev: dict, known_node_ids: Optional[set] = None) -> dict:
    """Validate one DisruptionEvent against the event contract."""
    if not isinstance(ev, dict):
        raise ValidationError("event is not an object")
    for field in ("id", "node_id", "kind", "severity", "detected_at",
                  "source", "retrieved_at", "evidence_url", "status"):
        if ev.get(field) in (None, ""):
            raise ValidationError(f"event field {field!r} missing")
    if not str(ev["id"]).startswith("evt_"):
        raise ValidationError(f"event id {ev['id']!r} must be evt_-prefixed")
    if ev["kind"] not in C3_KINDS:
        raise ValidationError(f"event kind {ev['kind']!r} not in the allowed kinds")
    if ev["status"] not in C3_STATUSES:
        raise ValidationError(f"event status {ev['status']!r} not in the allowed statuses")
    sev = ev["severity"]
    if not isinstance(sev, int) or isinstance(sev, bool) or not 1 <= sev <= 5:
        raise ValidationError(f"event severity {sev!r} must be an integer 1..5")
    if ev["kind"] in ("closure", "capacity_cut") and ev.get("capacity_loss") is None:
        raise ValidationError(f"capacity_loss is required for kind={ev['kind']}")
    if ev["kind"] == "closure" and ev.get("capacity_loss") != 1.0:
        raise ValidationError("closure implies capacity_loss == 1.0")
    cl = ev.get("capacity_loss")
    if cl is not None and not (isinstance(cl, (int, float)) and 0.0 <= cl <= 1.0):
        raise ValidationError(f"capacity_loss {cl!r} must be within 0..1")
    conf = ev.get("confidence")
    if conf is not None and not (isinstance(conf, (int, float)) and 0.0 <= conf <= 1.0):
        raise ValidationError(f"confidence {conf!r} must be within 0..1")
    if ev.get("mode") is not None and ev["mode"] not in MODES:
        raise ValidationError(f"event mode {ev['mode']!r} not in {MODES}")
    detected = _ts(ev["detected_at"], "detected_at")
    retrieved = _ts(ev["retrieved_at"], "retrieved_at")
    if ev.get("started_at"):
        started = _ts(ev["started_at"], "started_at")
        if started > detected:
            raise ValidationError("started_at must be <= detected_at")
    if detected > retrieved:
        raise ValidationError("detected_at must be <= retrieved_at")
    if known_node_ids is not None and ev["node_id"] not in known_node_ids:
        raise ValidationError(f"node_id {ev['node_id']!r} does not resolve to a graph node")
    return ev


_EXPIRY_FIELDS = ("expires_at", "ends_at", "expiry_at", "expires", "ends",
                  "todate", "end_at")
_VOLATILE_EVENT_FIELDS = ("retrieved_at", "provenance")


def _expiry_dt(ev: dict) -> datetime | None:
    """Expiry instant of an event, from its top-level expiry field or
    the source sub-record (``nws.expires``, ``gdacs.todate``). A
    date-only value means the event runs through that date (end of
    day UTC). Unparseable values are ignored, never guessed."""
    candidates = [ev.get(f) for f in _EXPIRY_FIELDS]
    for sub in ("nws", "gdacs"):
        rec = ev.get(sub)
        if isinstance(rec, dict):
            candidates += [rec.get(f) for f in _EXPIRY_FIELDS]
            candidates.append(rec.get("todate"))
    for value in candidates:
        if not value:
            continue
        text = str(value)
        try:
            if TS_RE.match(text):
                return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(
                    tzinfo=timezone.utc)
            if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
                day = datetime.strptime(text, "%Y-%m-%d").date()
                return datetime.combine(day, time(23, 59, 59),
                                        tzinfo=timezone.utc)
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return None


def _fold_events(rows: list[dict], now: datetime | None = None) -> list[dict]:
    """Fold append-only lifecycle lines into the current event state.

    Lines are keyed by occurrence identity (``dedupe_key`` or ``id``);
    a later line for the same occurrence REPLACES the earlier one
    (first-seen order is preserved). Status is then derived: an
    event whose expiry has passed reads as ``resolved`` even if no
    later line ever said so (shipped events would otherwise stay
    ``active`` forever). The file itself is never rewritten."""
    now = now or datetime.now(timezone.utc)
    folded: dict[str, dict] = {}
    order: list[str] = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        key = row.get("dedupe_key") or row["id"]
        if key not in folded:
            order.append(key)
        folded[key] = copy.deepcopy(row)
    out = []
    for key in order:
        ev = folded[key]
        expiry = _expiry_dt(ev)
        if expiry is not None and expiry < now \
                and ev.get("status") in ("active", "developing"):
            ev["status"] = "resolved"
        out.append(ev)
    return out


def _event_payload_key(ev: dict) -> str:
    """Identity of an event's substance, ignoring volatile fetch-time
    fields — a re-fetch that changes only ``retrieved_at`` /
    ``provenance`` is the same state, not a lifecycle update."""
    substance = {k: v for k, v in ev.items()
                 if k not in _VOLATILE_EVENT_FIELDS}
    return json.dumps(substance, ensure_ascii=False, sort_keys=True,
                      default=str)


class EventStore:
    """Append-only store over ``data/events.jsonl`` with dedupe."""

    def __init__(self, path: Path | None = None, known_node_ids: Optional[set] = None):
        self.path = Path(path) if path else DATA_DIR / "events.jsonl"
        self.known_node_ids = known_node_ids
        self._lock = threading.Lock()
        self._ids: set[str] = set()
        self._keys: set[str] = set()
        self._load_index()

    def _load_index(self) -> None:
        if not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:  # a torn line (crash mid-append) must not freeze ingestion
                ev = json.loads(line)
                self._ids.add(ev["id"])
                self._keys.add(ev.get("dedupe_key") or ev["id"])
            except (ValueError, KeyError, TypeError, AttributeError):
                continue

    def __len__(self) -> int:
        return len(self._ids)

    @contextlib.contextmanager
    def _file_lock(self):
        """Exclusive flock on the events file for the whole
        check-then-append. The per-instance ``threading.Lock`` cannot
        serialise two EventStore instances (the satwatch mint builds a
        fresh store per call; the adapter cycle runs in another process),
        so peers re-read the index, could all see the id absent and all
        append it. flock locks per open file, so it also orders threads of
        one process that hold different instances."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a+", encoding="utf-8") as lock_fh:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)

    def append_event(self, ev: dict) -> bool:
        """Validate + append. Returns False when this occurrence is a dupe.

        Per-id uniqueness guard (patch item 2): one record per event id,
        enforced at this write point in two parts —

        * Provenance-mode consistency: when the event carries the
          nested ``provenance`` envelope, it must AGREE with the flat
          fields (``provenance.mode == mode``, likewise source and
          retrieved_at); a disagreeing copy is rejected with
          ProvenanceError, never stored. This is what makes the
          snapshot-fallback copy of an event (flat ``mode`` still LIVE,
          nested provenance relabelled SNAPSHOT by the adapter snapshot
          loader) illegitimate next to its consistent LIVE copy: of a
          LIVE/SNAPSHOT pair, only the consistent LIVE copy can ever be
          stored, whichever of the two arrives first.
        * Fresh on-disk check: the dedupe index is re-read from the file
          under the append lock before deciding, so a peer EventStore
          instance constructed before this one wrote (overlapping
          ingestion cycles) cannot re-append an id already on disk.
        """
        check_provenance({
            "source": ev.get("source"),
            "retrieved_at": ev.get("retrieved_at"),
            "mode": ev.get("mode", "LIVE"),
        })
        prov = ev.get("provenance")
        if prov is not None:
            check_provenance(prov)
            # Compare against SCHEMA DEFAULTS for
            # flat fields the event lacks — an absent flat field is
            # not a free pass. The flat mode defaults to "LIVE" (the
            # same default used to build the check above), so a
            # mode-less event carrying provenance.mode="SNAPSHOT" is
            # envelope-inconsistent and rejected, exactly as if it had
            # spelled out mode="LIVE" next to the SNAPSHOT envelope.
            _FLAT_DEFAULTS = {"mode": "LIVE"}
            for field in ("source", "retrieved_at", "mode"):
                if field in ev or field in _FLAT_DEFAULTS:
                    flat = ev.get(field, _FLAT_DEFAULTS.get(field))
                    if prov.get(field) != flat:
                        raise ProvenanceError(
                            f"provenance.{field} {prov.get(field)!r} disagrees "
                            f"with flat {field} {flat!r} (nested "
                            "provenance envelope must agree with the flat "
                            "fields)")
        validate_c3_event(ev, self.known_node_ids)
        key = ev.get("dedupe_key") or ev["id"]
        with self._lock, self._file_lock():
            self._load_index()  # refresh: ids a peer instance wrote since
            # this instance was constructed are on disk now — re-checking
            # only the construction-time index let paired copies through.
            if ev["id"] in self._ids or key in self._keys:
                # Lifecycle: an occurrence that is already
                # stored can still be UPDATED — a later full record
                # for the same id/dedupe_key whose substance differs
                # (status, severity, expiry, ...) is appended as a
                # lifecycle line and folded over the old state on
                # read. A re-fetch differing only in volatile
                # fetch-time fields remains a dupe (returns False).
                existing = None
                for row in self._read_rows():
                    if row.get("id") == ev["id"] or \
                            (row.get("dedupe_key") or row.get("id")) == key:
                        existing = row  # last matching line wins
                if existing is not None and \
                        _event_payload_key(existing) == _event_payload_key(ev):
                    return False
                if existing is None:
                    return False
                self.path.parent.mkdir(parents=True, exist_ok=True)
                _ensure_trailing_newline(self.path)
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(ev, ensure_ascii=False,
                                        sort_keys=True) + "\n")
                self._ids.add(ev["id"])
                self._keys.add(key)
                return True
            self.path.parent.mkdir(parents=True, exist_ok=True)
            _ensure_trailing_newline(self.path)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(ev, ensure_ascii=False, sort_keys=True) + "\n")
            self._ids.add(ev["id"])
            self._keys.add(key)
            return True

    def _read_rows(self) -> list[dict]:
        if not self.path.exists():
            return []
        rows = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return rows

    def read_all(self) -> list[dict]:
        """Current event state: lifecycle lines folded (later line for
        an occurrence replaces its earlier state) and expiry-derived
        ``resolved`` status applied. See :func:`_fold_events`."""
        return _fold_events(self._read_rows())


def _ensure_trailing_newline(path: Path) -> None:
    """If the file's last byte is not a newline (a crash tore the last
    append), terminate that line so the next record starts on its own."""
    try:
        with path.open("rb+") as fh:
            fh.seek(0, 2)
            if fh.tell() == 0:
                return
            fh.seek(-1, 2)
            if fh.read(1) != b"\n":
                fh.write(b"\n")
    except OSError:
        pass


def _record_payload_key(rec: dict) -> str:
    substance = {k: v for k, v in rec.items() if k != "provenance"}
    return json.dumps(substance, ensure_ascii=False, sort_keys=True,
                      default=str)


class RecordStore:
    """Append-only store for non-event provenanced records (feed items, waits)."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else DATA_DIR / "records.jsonl"
        self._lock = threading.Lock()
        self._keys: set[str] = set()
        # PortWatch activity is a *current-value* record type:
        # its dedupe_key identifies the port, and a refreshed value for
        # the same key is an upsert, not a dupe. The substance (sans
        # provenance) of the last stored activity row per key is kept
        # so an identical refresh still dedupes.
        self._activity: dict[str, str] = {}
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:  # a torn line (crash mid-append) is skipped
                        row = json.loads(line)
                        row.get("dedupe_key")
                    except (ValueError, AttributeError):
                        continue
                    if row.get("dedupe_key"):
                        self._keys.add(row["dedupe_key"])
                    if row.get("record_type") == "port_activity" \
                            and row.get("dedupe_key"):
                        self._activity[row["dedupe_key"]] = \
                            _record_payload_key(row.get("record") or {})

    def read_all(self) -> list[dict]:
        """Stored rows with dedupe/upsert folding applied: for
        ``port_activity`` the LATEST row per dedupe_key wins (upsert
        semantics); for every other type the first row wins (the
        append-time dedupe invariant). First-seen order preserved."""
        if not self.path.exists():
            return []
        folded: dict[str, dict] = {}
        order: list[str] = []
        anon = 0
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                key = row.get("dedupe_key")
            except (ValueError, AttributeError):
                continue
            if not key:
                anon += 1
                key = f"\x00anon{anon}"
                folded[key] = row
                order.append(key)
            elif key not in folded:
                folded[key] = row
                order.append(key)
            elif row.get("record_type") == "port_activity":
                folded[key] = row
        return [folded[k] for k in order]

    def append_records(self, record_type: str, records: Iterable[dict],
                       envelope_provenance: dict) -> int:
        """Every record must carry provenance (its own, or the envelope's).
        Missing provenance anywhere -> ProvenanceError, nothing is written."""
        check_provenance(envelope_provenance)
        rows = []
        for rec in records:
            prov = rec.get("provenance") or envelope_provenance
            check_provenance(prov)
            rows.append({
                "record_type": record_type,
                "dedupe_key": rec.get("dedupe_key"),
                "provenance": prov,
                "record": rec,
            })
        written = 0
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            _ensure_trailing_newline(self.path)
            with self.path.open("a", encoding="utf-8") as fh:
                for row in rows:
                    key = row["dedupe_key"]
                    if key and key in self._keys:
                        # Upsert exception: a port_activity row
                        # whose substance changed supersedes the stored
                        # value (appended as a new line; read_all folds
                        # latest-wins). Identical activity still dedupes.
                        if record_type != "port_activity" or \
                                self._activity.get(key) == \
                                _record_payload_key(row["record"]):
                            continue
                    fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                    if key:
                        self._keys.add(key)
                        if record_type == "port_activity":
                            self._activity[key] = \
                                _record_payload_key(row["record"])
                    written += 1
        return written
