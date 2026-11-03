"""Prediction Ledger: lock + deterministic auto-grader.

Store: data/ledger.jsonl, APPEND-ONLY at the file level. lock() appends one
entry line; grade_entry() appends one grade-record line
({"record_type": "grade", "entry_id": ..., "grade": {...}}). Lines are
never rewritten or deleted; the merged read view attaches a grade record to
its entry as entry["grade"] — that attachment is the single grade write the
ledger contract permits, at most once per entry (a second grade attempt is
refused). Corrections happen only via a superseding appended entry whose
"supersedes" field references the original entry_id.

Grading rule v1 (fixed) — implemented exactly:
  A predicted step is a HIT when outcome evidence shows an effect at the
  predicted node_id within the window [0.5 x predicted delay_d,
  2 x predicted delay_d] (measured from the disruption start on the same
  clock as event `at`), backed by queue/capacity evidence. Otherwise MISSED.
  grade.delay_error_d = mean over hit steps of |observed - predicted|.
  Every grade carries evidence[] provenance {source, retrieved_at, mode};
  a grade without evidence does not publish. The grader is deterministic;
  no LLM anywhere in this path.

Grading rule v2 amendments:
  * The root step (seq 0 — "capacity loss at source", the disruption
    itself) is NEVER graded: it is excluded from hit/missed/ungradeable.
    Only seq >= 1 steps are scored.
  * Observations carry `observed_at` (when the outcome was observed).
    The lock-before-knowable gate is locked_at < observed_at <=
    window_end, where window_end is the entry's grading-window close:
    disruption_start + 2 x max(delay_d over the locked chain), with
    disruption_start derived from the locked chain itself
    (chain[0].at - chain[0].delay_d). `retrieved_at` is recorded
    in provenance but does not gate anything.
  * Per node, the observation that decides a step is the EARLIEST
    in-window observation, tie-broken by record id
    (id / record_id / dedupe_key) — grading is order-independent
    (the v1 first-in-list rule is retired).
  * Observation schema is validated up front: a missing/invalid
    node_id, observed_delay_d or observed_at raises LedgerError
    (HTTP 422) — never a bare KeyError/500.

Grading rule v2 amendments:
  * lock() refuses a chain with fewer than 3 downstream predictions
    (predicted_chain length < 4) with LedgerError — since seq 0 is
    never graded, shorter chains carry almost nothing gradable.
  * lock() stores the grading window it computes at lock time on the
    entry as `window_end`; grading uses the stored window when the
    entry carries one and falls back to the derived window for
    entries that predate the field (semantics unchanged).

Hash chain: every appended
record carries `prev_hash` + `hash`, where hash is SHA-256 over the
record's canonical JSON (sorted keys, whitespace-free separators — the
codebase convention of server/engine/canonical.py) including prev_hash.
Appends happen under an fcntl flock so the check-and-write sequence is
atomic across processes and threads; the writer enforces one lock per
disruption_id (a re-lock must supersede the existing entry) and one
grade per entry_id. verify() recomputes the whole chain and raises
LedgerChainError on any break (edited byte, deleted/reordered line,
second grade). Read paths tolerate a torn FINAL line (dropped, flagged
via store.torn_tail_detected / accuracy()["torn_tail_dropped"]); a torn
line anywhere else is a verify() failure and a loud read error.

Migration: a pre-chain (legacy) ledger file is migrated on the first
write or verify() under lock by appending ONE marker record
({"record_type": "migration", ...}) that anchors the chain over the
legacy lines exactly as they stand (the marker carries the legacy line
count + a SHA-256 of the legacy bytes). Legacy lines are NEVER
rewritten or reordered.

Evidence semantics: the "evidence-less steps are ungradeable" rule is also
implemented in server/engine/backtest.py (has_evidence). The check is
REIMPLEMENTED here (has_evidence below) rather than imported, so this module
stays independent of the engine. Semantics mirrored: an observation
whose record carries no evidence source is UNGRADEABLE — never a hit,
never a miss, never silently dropped; it is listed in
grade.ungradeable_steps and excluded from delay_error_d. A predicted node
with no observation at all is MISSED. If NO observation is evidence-backed, grading refuses to
publish a grade at all.

Lock-before-knowable (hard rule): validate_lock_before_knowable()
returns a violation for every graded entry whose locked_at is not strictly
before the observed_at (legacy grades: retrieved_at) of EVERY grading
evidence record. As a module it also verifies the hash chain and exits
non-zero on any failure:  python -m llm.ledger [ledger_path]
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LEDGER_PATH = REPO_ROOT / "data" / "ledger.jsonl"

GENESIS_PREV = "GENESIS"
MIGRATION_RECORD_TYPE = "migration"

GRADING_RULE_V1 = (
    "A predicted step is a HIT when outcome evidence shows an effect at the "
    "predicted node_id within the window [0.5 x predicted delay_d, "
    "2 x predicted delay_d] (measured from the disruption start on the same "
    "clock as event `at`), backed by queue/capacity evidence (e.g. PortWatch "
    "activity/queue anomaly, CBP wait spike, AIS density/queue counts, "
    "satellite anchorage counts). Otherwise the step is MISSED. "
    "grade.delay_error_d = mean over hit steps of |observed delay - "
    "predicted delay_d|. Every grade carries evidence[] with {source, "
    "retrieved_at, mode} provenance; a grade without evidence does not "
    "publish. Lock-before-knowable is a hard rule: for every graded "
    "entry, locked_at < retrieved_at of every grading evidence record. "
    "The auto-grader is deterministic, no LLM in the grading path; "
    "re-running it on the same evidence reproduces the same grade. Entries "
    "are never edited/deleted; a grade write is the only mutation, at most "
    "once; corrections only via a superseding appended entry referencing "
    "the original entry_id."
)

GRADING_RULE_V2_AMENDMENTS = (
    "Amendment: the root step (seq 0) is never graded; "
    "observations carry observed_at and the lock-before-knowable gate is "
    "locked_at < observed_at <= window_end, window_end = disruption start "
    "+ 2 x the largest predicted delay_d in the locked chain; per node "
    "the earliest in-window observation decides (tie-break by record "
    "id), so grading is independent of observation order; observation "
    "schema (node_id, observed_delay_d, observed_at) is validated up "
    "front and violations raise LedgerError, never a bare KeyError."
)

LED05_AMENDMENTS = (
    "Amendment: lock() refuses a predicted_chain with fewer "
    "than 3 downstream predictions (length < 4) — grading excludes "
    "seq 0, so anything shorter carries at most 2 gradable steps and "
    "a length-1 chain carries none; shipped entries are untouched "
    "(the refusal is at lock time only). lock() also computes the "
    "grading window at lock time (the _window_end logic) and stores "
    "it on the entry as window_end; grading uses the STORED window "
    "when present and falls back to the derived window for entries "
    "that predate the field, with the grading semantics unchanged."
)

# Minimum predicted_chain length at lock time — the root step
# (seq 0) plus at least 3 downstream (gradable) predictions.
MIN_CHAIN_LENGTH = 4


class LedgerError(RuntimeError):
    pass


class LedgerChainError(LedgerError):
    """Hash-chain / file-integrity failure. Fails loudly."""


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ts(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc)


def _fmt_ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _canonical(obj) -> str:
    """Canonical JSON for hashing: sorted keys, whitespace-free — the
    codebase convention (server/engine/canonical.py)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def _record_hash(record: dict) -> str:
    """SHA-256 over the record's canonical JSON, `hash` field excluded
    (prev_hash IS included — it is the chain link being sealed)."""
    body = {k: v for k, v in record.items() if k != "hash"}
    return hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()


def _legacy_digest(lines: list[str]) -> str:
    """SHA-256 over the legacy lines exactly as they stand (each line
    newline-terminated, as the pre-chain writer always wrote them)."""
    blob = "".join(line + "\n" for line in lines).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def has_evidence(obs: dict) -> bool:
    """Same semantics as the engine backtest's evidence check (kept local to
    avoid an import). True when the record carries a non-empty
    evidence source via `source`, `evidence_source`, `evidence_url`, or an
    `evidence` string/dict/list carrying a source."""
    if not isinstance(obs, dict):
        return False
    for field in ("source", "evidence_source", "evidence_url"):
        v = obs.get(field)
        if isinstance(v, str) and v.strip():
            return True
    ev = obs.get("evidence")
    if isinstance(ev, str):
        return bool(ev.strip())
    if isinstance(ev, dict):
        src = ev.get("source")
        return isinstance(src, str) and bool(src.strip())
    if isinstance(ev, list):
        for item in ev:
            if isinstance(item, str) and item.strip():
                return True
            if isinstance(item, dict):
                src = item.get("source")
                if isinstance(src, str) and src.strip():
                    return True
    return False


def _provenance_of(obs: dict) -> dict | None:
    """Normalise an evidence-backed observation to {source, retrieved_at,
    observed_at, mode} provenance, or None if it cannot produce a full
    chip. observed_at is mandatory (schema validation guarantees
    it); retrieved_at rides along when the record carries one."""
    source = obs.get("source") or obs.get("evidence_source") \
        or obs.get("evidence_url")
    retrieved_at = obs.get("retrieved_at")
    mode = obs.get("mode")
    ev = obs.get("evidence")
    if isinstance(ev, dict):
        source = source or ev.get("source")
        retrieved_at = retrieved_at or ev.get("retrieved_at")
        mode = mode or ev.get("mode")
    observed_at = obs.get("observed_at")
    if source and observed_at and mode in ("LIVE", "SNAPSHOT", "MODELED"):
        prov = {"source": source, "observed_at": observed_at, "mode": mode}
        if retrieved_at:
            prov["retrieved_at"] = retrieved_at
        return prov
    return None


def _validate_observation(obs, idx: int) -> None:
    """Up-front schema validation. Any defect raises LedgerError
    (HTTP 422) — never a bare KeyError from deep in the grader."""
    if not isinstance(obs, dict):
        raise LedgerError(f"observation {idx} is not a JSON object")
    node = obs.get("node_id")
    if not isinstance(node, str) or not node:
        raise LedgerError(
            f"observation {idx} lacks a valid node_id (observation schema)")
    delay = obs.get("observed_delay_d")
    if isinstance(delay, bool) or not isinstance(delay, (int, float)):
        raise LedgerError(
            f"observation for node {node} lacks a numeric "
            "observed_delay_d (observation schema)")
    observed_at = obs.get("observed_at")
    if not isinstance(observed_at, str) or not observed_at:
        raise LedgerError(
            f"observation for node {node} lacks observed_at "
            "(observations carry observed_at)")
    try:
        _ts(observed_at)
    except (ValueError, TypeError):
        raise LedgerError(
            f"observation for node {node} has an unparseable "
            f"observed_at: {observed_at!r}")
    retrieved_at = obs.get("retrieved_at")
    if retrieved_at is not None:
        try:
            _ts(retrieved_at)
        except (ValueError, TypeError):
            raise LedgerError(
                f"observation for node {node} has an unparseable "
                f"retrieved_at: {retrieved_at!r}")


def _disruption_start(predicted_chain: list[dict]) -> datetime | None:
    """Disruption start on the event clock, derived from the locked chain
    itself: chain[0].at - chain[0].delay_d (the engine sets the root
    event's `at` to started_at + its delay). None when underivable."""
    if not predicted_chain:
        return None
    root = min(predicted_chain, key=lambda e: e.get("seq", 0))
    try:
        return _ts(root["at"]) - timedelta(days=float(root["delay_d"]))
    except Exception:
        return None


def _window_end(predicted_chain: list[dict]) -> datetime | None:
    """The entry's grading-window close: disruption start +
    2 x the largest predicted delay_d in the chain — the moment the
    widest step's rule-v1 window [0.5x, 2x] closes. None when the chain
    carries no derivable start."""
    start = _disruption_start(predicted_chain)
    if start is None or not predicted_chain:
        return None
    try:
        widest = max(float(e["delay_d"]) for e in predicted_chain)
    except Exception:
        return None
    return start + timedelta(days=2.0 * widest)


def observation_from_event(entry: dict, event: dict) -> dict:
    """Build a grading observation from a server-held event record
    (grade-by-reference): every grading field comes from the
    resolved server record, never from the caller. node_id and
    provenance (source / retrieved_at / mode) are the event's own;
    observed_at is the event's started_at (fallback detected_at);
    observed_delay_d is derived deterministically on the event clock as
    observed_at - disruption_start, the same start the grader derives
    from the locked chain. Fields that cannot be derived are left
    None — grade_chain's schema validation then refuses the grade
    with a LedgerError (422), never a silent default."""
    start = _disruption_start(entry.get("predicted_chain") or [])
    observed_at = event.get("started_at") or event.get("detected_at")
    delay = None
    if start is not None and isinstance(observed_at, str):
        try:
            delay = (_ts(observed_at) - start).total_seconds() / 86400.0
        except (ValueError, TypeError):
            delay = None
    prov = event.get("provenance")
    if not isinstance(prov, dict):
        prov = {}
    return {
        "id": event.get("id"),
        "node_id": event.get("node_id"),
        "observed_delay_d": delay,
        "observed_at": observed_at,
        "source": event.get("source") or prov.get("source"),
        "retrieved_at": event.get("retrieved_at")
        or prov.get("retrieved_at"),
        "mode": event.get("mode") or prov.get("mode"),
    }


def _obs_sort_key(obs: dict) -> tuple:
    """Deterministic observation ordering: earliest observed_at
    first, tie-broken by the record's id so grading never depends on
    the order observations were supplied in."""
    rid = obs.get("id") or obs.get("record_id") or obs.get("dedupe_key") or ""
    return (_ts(obs["observed_at"]), str(rid))


def grade_chain(predicted_chain: list[dict],
                observations: list[dict],
                *, locked_at: str | None = None,
                window_end: str | None = None) -> dict:
    """Pure deterministic grader core (rule v1 window + v2
    amendments). Returns {hit_steps, missed_steps, ungradeable_steps,
    delay_error_d, evidence, window_end, lbk_blocked}.

    Per predicted step with seq >= 1 (the root, seq 0, is never
    graded): among the observations for its node, only those inside
    the gate locked_at < observed_at <= window_end are eligible (the
    locked_at bound applies when `locked_at` is given); the EARLIEST
    eligible observation (tie-break: record id) decides. Evidence-
    backed observation -> hit iff observed delay in window, else
    missed. Eligible observation present but evidence-less ->
    ungradeable. No eligible observation -> missed when the node was
    never observed at all, ungradeable when observations exist but
    every one fell outside the gate. `lbk_blocked` is True when an
    evidence-backed observation was excluded solely because its
    observed_at is not after locked_at.
    """
    for idx, obs in enumerate(observations):
        _validate_observation(obs, idx)
    # A stored window_end (written by lock()) governs when the
    # caller supplies one; otherwise derive it (legacy entries).
    if window_end is not None:
        try:
            window_end_dt = _ts(window_end)
        except (ValueError, TypeError):
            raise LedgerError(
                f"stored window_end is unparseable: {window_end!r}")
    else:
        window_end_dt = _window_end(predicted_chain)
    locked_dt = _ts(locked_at) if locked_at else None
    by_node: dict[str, list[dict]] = {}
    for obs in observations:
        by_node.setdefault(obs["node_id"], []).append(obs)
    hits: list[int] = []
    missed: list[int] = []
    ungradeable: list[int] = []
    errors: list[float] = []
    evidence: list[dict] = []
    seen_prov: set[tuple] = set()
    lbk_blocked = False
    for ev in sorted(predicted_chain, key=lambda e: e["seq"]):
        seq = ev["seq"]
        if seq == 0:
            continue  # The root step is never graded
        pred = float(ev["delay_d"])
        cands = by_node.get(ev["node_id"], [])
        if not cands:
            missed.append(seq)
            continue
        eligible: list[dict] = []
        for obs in cands:
            odt = _ts(obs["observed_at"])
            if locked_dt is not None and not odt > locked_dt:
                if has_evidence(obs):
                    lbk_blocked = True
                continue
            if window_end_dt is not None and odt > window_end_dt:
                continue
            eligible.append(obs)
        if not eligible:
            ungradeable.append(seq)
            continue
        obs = min(eligible, key=_obs_sort_key)
        if not has_evidence(obs):
            ungradeable.append(seq)
            continue
        prov = _provenance_of(obs)
        if prov is not None:
            key = (prov["source"], prov.get("retrieved_at"),
                   prov["observed_at"], prov["mode"])
            if key not in seen_prov:
                seen_prov.add(key)
                evidence.append(prov)
        observed = float(obs["observed_delay_d"])
        if 0.5 * pred <= observed <= 2.0 * pred:
            hits.append(seq)
            errors.append(abs(observed - pred))
        else:
            missed.append(seq)
    return {
        "hit_steps": hits,
        "missed_steps": missed,
        "ungradeable_steps": ungradeable,
        "delay_error_d": round(sum(errors) / len(errors), 6) if errors else None,
        "evidence": evidence,
        "window_end": _fmt_ts(window_end_dt) if window_end_dt else None,
        "lbk_blocked": lbk_blocked,
    }


class LedgerStore:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or DEFAULT_LEDGER_PATH)
        # Read-path integrity flags, refreshed by every read:
        # torn_tail_detected — a torn FINAL line was dropped on read;
        # duplicate_grade_detected — a second grade line for one entry
        # exists in the file (the first grade wins the merged view;
        # verify() fails loudly on it).
        self.torn_tail_detected = False
        self.duplicate_grade_detected = False

    # ----- raw store ------------------------------------------------------
    def _parse_lines(self, text: str) -> tuple[list[dict], list[str], bool]:
        """Parse ledger text -> (records, raw_lines, torn_tail).

        A torn FINAL line (truncated JSON, e.g. a crashed writer) is
        dropped and reported via torn_tail; an unparseable line
        anywhere else raises LedgerChainError — reads fail loudly
        rather than silently skipping sealed history."""
        raw = [ln for ln in text.splitlines() if ln.strip()]
        records: list[dict] = []
        torn = False
        for i, line in enumerate(raw):
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                if i == len(raw) - 1:
                    torn = True
                    break
                raise LedgerChainError(
                    f"ledger line {i + 1} is not valid JSON ({exc}); "
                    "only a torn FINAL line is tolerated on read — "
                    "run verify() for a full integrity check")
        self.torn_tail_detected = torn
        return records, raw, torn

    def _read_records(self) -> list[dict]:
        if not self.path.exists():
            self.torn_tail_detected = False
            return []
        records, _raw, _torn = self._parse_lines(
            self.path.read_text(encoding="utf-8"))
        return records

    @contextlib.contextmanager
    def _locked_file(self):
        """Open the ledger and hold an exclusive flock for the whole
        check-and-write sequence (appends are atomic across
        processes and threads)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            fh.seek(0)
            yield fh
        finally:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            finally:
                fh.close()

    def _records_in(self, fh) -> tuple[list[dict], list[str]]:
        fh.seek(0)
        records, raw, torn = self._parse_lines(fh.read())
        if torn:
            raise LedgerChainError(
                "ledger has a torn final line; writes are refused "
                "until the tail is repaired (reads tolerate it, "
                "verify() reports it)")
        return records, raw

    @staticmethod
    def _is_chained(records: list[dict]) -> bool:
        return any("hash" in rec or
                   rec.get("record_type") == MIGRATION_RECORD_TYPE
                   for rec in records)

    def _migrate_locked(self, fh) -> list[dict]:
        """Migration, called with the flock held: if the file
        holds pre-chain (legacy) records, append ONE marker record
        anchoring the chain over the legacy lines exactly as they
        stand. Never rewrites or reorders a legacy line. Returns the
        record list (post-migration)."""
        records, raw = self._records_in(fh)
        if not records or self._is_chained(records):
            return records
        legacy_lines = raw[:len(records)]
        digest = _legacy_digest(legacy_lines)
        marker = {
            "record_type": MIGRATION_RECORD_TYPE,
            "marker": "ledger-hash-chain-genesis",
            "migrated_at": _utcnow(),
            "legacy_line_count": len(records),
            "legacy_sha256": digest,
            "prev_hash": digest,
        }
        marker["hash"] = _record_hash(marker)
        self._write_line(fh, marker)
        return records + [marker]

    @staticmethod
    def _write_line(fh, record: dict) -> None:
        fh.seek(0, os.SEEK_END)
        fh.write(json.dumps(record, sort_keys=True, ensure_ascii=False)
                 + "\n")
        fh.flush()
        os.fsync(fh.fileno())

    def _append_locked(self, fh, records: list[dict], record: dict) -> dict:
        """Append `record` with its chain links. Caller holds the flock
        and has already migrated. `records` is the current content."""
        prev = GENESIS_PREV
        for rec in reversed(records):
            if "hash" in rec:
                prev = rec["hash"]
                break
        record = dict(record)
        record["prev_hash"] = prev
        record["hash"] = _record_hash(record)
        self._write_line(fh, record)
        return record

    def _append(self, record: dict) -> dict:
        """Locked single append (migration + chain). Kept as the
        store's one write primitive; lock()/grade_entry() do their
        invariant checks inside the same flock via _locked_file."""
        with self._locked_file() as fh:
            records = self._migrate_locked(fh)
            return self._append_locked(fh, records, record)

    # ----- read view (grades merged onto entries) --------------------------
    @staticmethod
    def _merged(records: list[dict]) -> tuple[list[dict], bool]:
        entries: dict[str, dict] = {}
        order: list[str] = []
        grades: dict[str, dict] = {}
        dup_grade = False
        for rec in records:
            if rec.get("record_type") == "grade":
                if rec["entry_id"] in grades:
                    # A second grade line never silently wins;
                    # the first grade stands and verify() fails loudly.
                    dup_grade = True
                    continue
                grades[rec["entry_id"]] = rec["grade"]
            elif "entry_id" in rec:
                entries[rec["entry_id"]] = rec
                order.append(rec["entry_id"])
        merged = []
        for eid in order:
            entry = dict(entries[eid])
            if eid in grades:
                entry["grade"] = grades[eid]
            merged.append(entry)
        return merged, dup_grade

    def entries(self) -> list[dict]:
        merged, dup = self._merged(self._read_records())
        self.duplicate_grade_detected = dup
        return merged

    def get(self, entry_id: str) -> dict | None:
        for e in self.entries():
            if e["entry_id"] == entry_id:
                return e
        return None

    # ----- integrity ----------------------------------------------
    def verify(self) -> bool:
        """Recompute the whole hash chain. Migrates a legacy file
        first (under lock, marker append only). Returns True when the
        chain is intact; raises LedgerChainError on ANY break — edited
        byte, deleted or reordered line, torn line, duplicate grade,
        broken link."""
        with self._locked_file() as fh:
            records = self._migrate_locked(fh)
            fh.seek(0)
            _records2, raw, torn = self._parse_lines(fh.read())
        if torn:
            raise LedgerChainError(
                "ledger has a torn final line (truncated write); "
                "the chain cannot be verified past it")
        problems = self._verify_records(records, raw)
        if problems:
            raise LedgerChainError(
                "ledger hash-chain verification failed: "
                + "; ".join(problems))
        return True

    @staticmethod
    def _verify_records(records: list[dict], raw: list[str]) -> list[str]:
        problems: list[str] = []
        if not records:
            return problems
        marker_idx = next(
            (i for i, r in enumerate(records)
             if r.get("record_type") == MIGRATION_RECORD_TYPE), None)
        chained_from = 0
        prev = GENESIS_PREV
        if marker_idx is not None:
            marker = records[marker_idx]
            legacy = raw[:marker_idx]
            if marker.get("legacy_line_count") != marker_idx:
                problems.append(
                    f"migration marker claims {marker.get('legacy_line_count')} "
                    f"legacy lines but {marker_idx} precede it")
            digest = _legacy_digest(legacy)
            if marker.get("legacy_sha256") != digest:
                problems.append(
                    "legacy segment hash mismatch — pre-chain lines "
                    "were edited, deleted or reordered")
            if marker.get("prev_hash") != digest:
                problems.append("migration marker is not anchored to "
                                "the legacy segment hash")
            chained_from = marker_idx
            # The marker's own prev_hash IS the legacy anchor (checked
            # just above), not a link to a previous record's hash.
            prev = marker.get("prev_hash")
        elif not any("hash" in r for r in records):
            problems.append("ledger is unchained and carries no "
                            "migration marker (unmigrated legacy file)")
            return problems
        elif any("hash" not in r for r in records):
            problems.append("chained records follow unchained lines "
                            "with no migration marker between them")
            chained_from = next(i for i, r in enumerate(records)
                                if "hash" in r)
            prev = records[chained_from].get("prev_hash", GENESIS_PREV)
        for i in range(chained_from, len(records)):
            rec = records[i]
            if rec.get("prev_hash") != prev:
                problems.append(
                    f"record {i + 1}: prev_hash does not match the "
                    "previous record's hash (line deleted, reordered "
                    "or edited)")
            if rec.get("hash") != _record_hash(rec):
                problems.append(
                    f"record {i + 1}: hash mismatch — record content "
                    "was edited after append")
            prev = rec.get("hash")
        grade_counts: dict[str, int] = {}
        for rec in records:
            if rec.get("record_type") == "grade":
                grade_counts[rec["entry_id"]] = \
                    grade_counts.get(rec["entry_id"], 0) + 1
        for eid, n in sorted(grade_counts.items()):
            if n > 1:
                problems.append(
                    f"entry {eid} has {n} grade records — grades are "
                    "written at most once")
        return problems

    # ----- lock --------------------------------------------------------
    def lock(self, disruption: dict, run: dict, *,
             locked_at: str | None = None,
             entry_id: str | None = None,
             supersedes: str | None = None,
             node_tiers: dict | None = None,
             known_event_ids: set | None = None) -> dict:
        """Append a locked prediction. `disruption` is an event (needs
        `id`); `run` is a completed run in chain-payload shape ({run_id,
        events}) or a run record carrying `events`. The predicted_chain is
        a VERBATIM deep copy of the run's event array.

        Tier guard (core/shell tiers): when `node_tiers`
        (node_id -> tier) is supplied, a lock is REFUSED if the
        disruption's node or any chain event's node is SHELL tier —
        shell nodes are routing-grade and never originate Ledger locks.
        Callers on the live paths (autolock, the HTTP router) always
        pass the map; the parameter is optional only so offline fixture
        tooling keeps working on untiered data.

        Referential integrity: when `known_event_ids` (the id
        set of the server's event store) is supplied, a disruption_id
        that does not resolve in it is REFUSED.

        One lock per disruption: enforced under the append
        flock — a second lock for the same disruption_id is refused
        unless it supersedes the existing entry for that disruption."""
        disruption_id = disruption.get("id") or disruption.get("disruption_id")
        if not disruption_id:
            raise LedgerError("disruption carries no id (disruption id required)")
        if known_event_ids is not None \
                and disruption_id not in known_event_ids:
            raise LedgerError(
                f"disruption_id {disruption_id} does not resolve in "
                "the server event store (referential integrity)")
        if node_tiers is not None:
            origin = disruption.get("node_id")
            if origin is not None and node_tiers.get(origin) == "shell":
                raise LedgerError(
                    f"tier guard: disruption node {origin} is SHELL tier — "
                    "shell nodes never originate Ledger locks")
            chain_peek = run.get("events") or run.get("predicted_chain") or []
            shell_hits = sorted({e.get("node_id") for e in chain_peek
                                 if node_tiers.get(e.get("node_id")) == "shell"})
            if shell_hits:
                raise LedgerError(
                    "tier guard: predicted chain touches SHELL node(s) "
                    f"{shell_hits} — shell nodes never feed Ledger locks")
        run_id = run.get("run_id")
        chain = run.get("events") or run.get("predicted_chain")
        if not run_id or not chain:
            raise LedgerError("run must carry run_id and a non-empty chain")
        chain = json.loads(json.dumps(chain))  # verbatim deep copy
        seqs = [e.get("seq") for e in chain]
        if seqs != list(range(len(chain))):
            raise LedgerError("predicted_chain seq must be gapless 0..n-1")
        if any(e.get("run_id") != run_id for e in chain):
            raise LedgerError("every chain event must carry the run_id")
        if len(chain) < MIN_CHAIN_LENGTH:
            raise LedgerError(
                f"predicted_chain has {len(chain)} step(s) — fewer than "
                "3 downstream predictions beyond the root step "
                "(grading excludes seq 0, so a chain shorter "
                f"than {MIN_CHAIN_LENGTH} carries almost nothing "
                "gradable); lock refused")
        if entry_id is None:
            digest = hashlib.sha256(
                json.dumps(chain, sort_keys=True).encode()).hexdigest()[:8]
            entry_id = f"led_{digest}_{uuid.uuid4().hex[:6]}"
        entry = {
            "entry_id": entry_id,
            "locked_at": locked_at or _utcnow(),
            "disruption_id": disruption_id,
            "run_id": run_id,
            "predicted_chain": chain,
        }
        # State the grading window on the entry at lock time;
        # grading prefers this stored value over re-deriving it.
        window_end_dt = _window_end(chain)
        if window_end_dt is not None:
            entry["window_end"] = _fmt_ts(window_end_dt)
        if supersedes:
            entry["supersedes"] = supersedes
        with self._locked_file() as fh:
            records = self._migrate_locked(fh)
            existing, _dup = self._merged(records)
            if any(e["entry_id"] == entry_id for e in existing):
                raise LedgerError(f"entry_id already exists: {entry_id}")
            prior = [e for e in existing
                     if e["disruption_id"] == disruption_id]
            if prior and supersedes not in {e["entry_id"] for e in prior}:
                raise LedgerError(
                    f"disruption {disruption_id} is already locked by "
                    f"{prior[0]['entry_id']}; a second lock for one "
                    "disruption requires supersedes=<that entry_id> "
                    "(one lock per disruption)")
            return self._append_locked(fh, records, entry)

    # ----- grade --------------------------------------------------------
    def grade_entry(self, entry_id: str, observations: list[dict], *,
                    graded_at: str | None = None) -> dict:
        with self._locked_file() as fh:
            records = self._migrate_locked(fh)
            entries, _dup = self._merged(records)
            entry = next((e for e in entries
                          if e["entry_id"] == entry_id), None)
            if entry is None:
                raise LedgerError(f"unknown entry_id: {entry_id}")
            if "grade" in entry:
                raise LedgerError(
                    f"entry {entry_id} is already graded; grades are "
                    "written at most once — append a superseding entry "
                    "instead")
            result = grade_chain(entry["predicted_chain"], observations,
                                 locked_at=entry["locked_at"],
                                 window_end=entry.get("window_end"))
            if not result["evidence"]:
                if result["lbk_blocked"]:
                    raise LedgerError(
                        "LOCK-BEFORE-KNOWABLE violated: every "
                        "evidence-backed observation for entry "
                        f"{entry_id} was observed at or before its "
                        f"locked_at {entry['locked_at']} (gate: "
                        "locked_at < observed_at)")
                raise LedgerError(
                    "grade without evidence does not publish: "
                    "no evidence-backed observation with full "
                    "provenance was given")
            grade = {
                "hit_steps": result["hit_steps"],
                "missed_steps": result["missed_steps"],
                "ungradeable_steps": result["ungradeable_steps"],
                "delay_error_d": result["delay_error_d"],
                "graded_at": graded_at or _utcnow(),
                "evidence": result["evidence"],
            }
            if result["window_end"] is not None:
                grade["grading_window_end"] = result["window_end"]
            # Hard gate enforced at write time too, not only at validation time
            # (on observed_at, bounded by the window close).
            for prov in grade["evidence"]:
                if not _ts(entry["locked_at"]) < _ts(prov["observed_at"]):
                    raise LedgerError(
                        "LOCK-BEFORE-KNOWABLE violated: locked_at "
                        f"{entry['locked_at']} is not before evidence "
                        f"observed_at {prov['observed_at']}")
                if result["window_end"] is not None and \
                        _ts(prov["observed_at"]) > _ts(result["window_end"]):
                    raise LedgerError(
                        "grading window violated: evidence observed_at "
                        f"{prov['observed_at']} is after the entry's "
                        f"window close {result['window_end']}")
            self._append_locked(fh, records,
                                {"record_type": "grade",
                                 "entry_id": entry_id, "grade": grade})
            return grade

    # ----- validator -------------------------------------------------
    def validate_lock_before_knowable(self) -> list[str]:
        """Hard-fail list: one string per violation (empty = pass).
        The comparison uses each evidence record's observed_at;
        grade records without observed_at fall back to
        their retrieved_at."""
        violations: list[str] = []
        for entry in self.entries():
            grade = entry.get("grade")
            if not grade:
                continue
            if not grade.get("evidence"):
                violations.append(
                    f"{entry['entry_id']}: grade carries no evidence")
                continue
            for prov in grade["evidence"]:
                when = prov.get("observed_at") or prov.get("retrieved_at")
                try:
                    ok = _ts(entry["locked_at"]) < _ts(when)
                except Exception:
                    ok = False
                if not ok:
                    violations.append(
                        f"{entry['entry_id']}: LOCK-BEFORE-KNOWABLE "
                        f"violated — locked_at {entry['locked_at']} !< "
                        f"evidence observed_at {when} "
                        f"({prov.get('source')})")
        return violations

    def accuracy(self) -> dict:
        """Ledger-only KPI (never conflated with BACKTEST figures).

        Latest-only — an entry that another entry
        supersedes is excluded entirely (its grade, hit and missed
        steps count for nothing); a correction chain contributes only
        its newest entry. The merged history (entries()) is unchanged:
        superseded entries still list, they just do not score. The
        exclusion lives here, not in _merged, because _merged is the
        shared read view for the public page and API, which must keep
        showing the full history."""
        entries = self.entries()
        superseded = {e["supersedes"] for e in entries
                      if e.get("supersedes")}
        hits = missed = graded = 0
        for e in entries:
            if e["entry_id"] in superseded:
                continue
            g = e.get("grade")
            if g:
                graded += 1
                hits += len(g.get("hit_steps", []))
                missed += len(g.get("missed_steps", []))
        denom = hits + missed
        out = {"graded_entries": graded, "hit_steps": hits,
               "missed_steps": missed,
               "accuracy": round(hits / denom, 4) if denom else None,
               "source": "LEDGER (graded entries only)"}
        if self.torn_tail_detected:
            # A torn final line was dropped on read — surface
            # it in the returned data instead of failing the read.
            out["torn_tail_dropped"] = True
        return out


def validate_store(path: Path | None = None) -> list[str]:
    return LedgerStore(path).validate_lock_before_knowable()


def main(argv: list[str] | None = None) -> int:
    """CI entry: python -m llm.ledger [ledger_path] — exit 1 on any
    hash-chain break or lock-before-knowable violation."""
    import sys
    argv = list(sys.argv[1:] if argv is None else argv)
    path = Path(argv[0]) if argv else None
    store = LedgerStore(path)
    try:
        store.verify()
    except LedgerError as exc:
        print(f"FAIL: {exc}")
        return 1
    violations = validate_store(path)
    if violations:
        for v in violations:
            print(f"FAIL: {v}")
        return 1
    print("OK: hash chain intact; lock-before-knowable holds for "
          "every graded entry")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
