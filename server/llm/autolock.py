"""Prediction Ledger AUTO-LOCK policy (autolock-v1).

The ledger (ledger.py) can be written by hand; this module is the
deterministic policy
that locks REAL predictions at the end of an ingestion cycle
(server/adapters/run_once.py calls run_autolock() behind the
SUPERMAP_AUTOLOCK switch). Every decision — locked or skipped, with the
reason — is returned in the summary AND emitted through the caller's
logger: silent locking is not acceptable.

POLICY autolock-v1 (deterministic; the only randomness anywhere is the
ledger entry_id suffix minted by LedgerStore.lock, as for manual locks).
An event from the normalized store is evaluated in store order and
checked in this exact order (first failing check is the recorded reason):

  1. already_locked      — dedupe STRICTLY by disruption_id against the
                           ledger's merged entries. Re-running a cycle
                           never double-locks. (Checked first so an
                           already-locked event always reports as such.)
  2. unmapped_node       — event.node_id is not a node of the graph.
                           (minting already grounds events; this is
                           the belt-and-braces check at lock time.)
  3. status_not_active   — status is not "active"/"developing": a
                           resolved event's outcome may already be known.
  4. low_severity        — severity < MIN_SEVERITY (3). Justification:
                           Severity 1–2 are Minor/Unknown-level
                           advisories (NWS maps Minor -> 2); they dominate
                           live feed volume and, under the derivation
                           table below, model capacity loss < 0.50 — below
                           the loss of the one genuine manual lock (0.50).
                           3 (Moderate) is the first material level.
  5. stale_detection     — FRESHNESS: detected_at is more than
                           FRESHNESS_WINDOW_H (48h) before the lock moment.
                           Auto-lock never backfills old events. 48h spans
                           multiple ingestion cycles while staying well
                           inside the earliest grading window of any
                           qualifying scenario (see check 9).
  6. low_confidence      — the classifier (Classifier.classify) is
                           run on text built from the event (see
                           classifier_text); the candidate must be
                           mintable at the FROZEN 0.70 threshold
                           (MINT_THRESHOLD). The classifier is the source
                           of kind/severity for scenario derivation.
  7. classifier_node_mismatch — the classifier's grounded node_id must
                           agree with the event's adapter-grounded
                           node_id. (Consistency check, NOT independent
                           grounding: adapter grounding is geometric /
                           name-based at mint time, and the classifier
                           text carries the node's name as location
                           context, mirroring the spot-check texts.)
  8. cap_reached         — MAX_LOCKS_PER_CYCLE (3) locks already minted
                           this cycle. Justification: the ledger is a
                           public proof surface; a small cap bounds the
                           damage of a fragmented feed (one NWS advisory
                           split across area segments minted 3 distinct
                           disruption_ids in the real store) while sitting
                           comfortably above the observed qualifying rate
                           (0–1 per cycle). Capped events stay eligible
                           next cycle while still fresh — they are
                           skipped, not queued.
  9. outcome_window_passed — KNOWABILITY GUARD: after deriving the
                           scenario and running the engine in memory, the
                           lock moment must precede the close of the root
                           step's rule-v1 grading window
                           (started_at + 2 x root delay_d). Past that
                           close, the headline outcome is already
                           knowable and locking would be backfill.
                           Together with check 5 this makes
                           lock-before-knowable hold BY CONSTRUCTION for
                           auto-locks: grading evidence can only be
                           retrieved after outcomes occur, i.e. after the
                           windows the lock precedes.
 10. chain_too_short     — the engine chain has
                           fewer than 3 downstream predictions (length
                           < 4). LedgerStore.lock refuses such chains
                           (grading excludes seq 0, so they carry almost
                           nothing gradable); autolock records the skip
                           instead of locking — and a LedgerError from
                           lock() is likewise recorded as a skip, never
                           raised out of the cycle.

SCENARIO DERIVATION (a MODELLING CHOICE, labelled as such in every run
journal and cycle log line — derived parameters are NEVER observed data):
  kind          = the classifier's kind (check 6).
  capacity_loss = the event's own capacity_loss when the feed stated one
                  (events require it for closure/capacity_cut); otherwise
                  CAPACITY_LOSS_BY_SEVERITY[<classifier severity>]:
                  {1: 0.10, 2: 0.25, 3: 0.50, 4: 0.75, 5: 0.90}
                  (monotone in severity; severity 3 -> 0.50 matches the
                  loss of the genuine manual lock's API live-path run).
                  The engine still forces 1.0 for kind=closure.
  duration_d    = event.expected_duration_d (feed-stated) when present,
                  else the classifier's expected_duration_d when present,
                  else DEFAULT_DURATION_D = 7.0 (the API live-path default;
                  the genuine manual lock's run used 7.0).
  shape         = "step" (the API live-path shape).
  started_at    = event.started_at or event.detected_at.

LOCK: the engine run uses the API's deterministic run_id derivation
(api.store.derive_run_id over the same journal core the API's live
branch builds, plus live_disruption_id), so an auto-lock run_id is the
run_id the API itself would mint for the identical journal. The run
record is saved to the runs store with the full derivation recorded
under journal.autolock; the ledger entry is then locked with
locked_at = the lock moment and the run's chain verbatim.

SWITCH: SUPERMAP_AUTOLOCK — default OFF (a lock made on the
classifier's own confidence alone, with no human review, is opt-in);
"=1" (also true/yes/on) turns it on. The switch is read by the run_once
hook, not here, so this module can be exercised directly
regardless of env.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .classifier import MINT_THRESHOLD, Classifier
from .ledger import LedgerError, LedgerStore
from .provider import LLMProvider

try:  # server.llm.autolock (repo-root identity)
    from ..api.store import RunStore, derive_run_id
    from ..engine.engine import Engine, validate_disruption, validate_params
    from ..engine.graph import Graph
except ImportError:  # llm.autolock (server/ on sys.path identity)
    from api.store import RunStore, derive_run_id  # type: ignore
    from engine.engine import Engine, validate_disruption, validate_params  # type: ignore
    from engine.graph import Graph  # type: ignore

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GRAPH_PATH = REPO_ROOT / "data" / "graph" / "graph.json"
DEFAULT_RUNS_DIR = REPO_ROOT / "data" / "runs"

POLICY_ID = "autolock-v1"
MIN_SEVERITY = 3
FRESHNESS_WINDOW_H = 48.0
MAX_LOCKS_PER_CYCLE = 3
DEFAULT_DURATION_D = 7.0
CAPACITY_LOSS_BY_SEVERITY = {1: 0.10, 2: 0.25, 3: 0.50, 4: 0.75, 5: 0.90}
LOCKABLE_STATUSES = ("active", "developing")

_DERIVATION_NOTE = (
    "DERIVED by the autolock-v1 deterministic mapping (server/llm/"
    "autolock.py) — a modelling choice, NOT observed feed data.")


def _max_classify() -> int:
    try:
        return max(0, int(os.environ.get("SUPERMAP_AUTOLOCK_MAX_CLASSIFY", "12")))
    except ValueError:
        return 12


def autolock_enabled(environ: dict | None = None) -> bool:
    """SUPERMAP_AUTOLOCK switch: default OFF; only 1/true/yes/on enables.
    Locking on model confidence alone is an operator opt-in."""
    env = os.environ if environ is None else environ
    raw = str(env.get("SUPERMAP_AUTOLOCK", "0")).strip().lower()
    return raw in ("1", "true", "yes", "on")


def _parse_ts(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
    except ValueError:
        return None


def _fmt_ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def classifier_text(event: dict, node_name: str | None) -> str:
    """Deterministic classifier input for a stored event.

    Mirrors the spot-check construction (headline + area for NWS alerts;
    name/type/country for GDACS) and appends the adapter-grounded node's
    name as location context — the adapter grounded the event
    geometrically or by name at mint time, and the classifier is asked
    for kind/severity/confidence GIVEN that location (see policy check
    7 in the module docstring). Generic fallback for other sources uses
    only the event's own structured fields."""
    nws = event.get("nws")
    if isinstance(nws, dict) and nws.get("headline"):
        text = f"{nws.get('event') or 'Weather alert'}: {nws['headline']}"
        if nws.get("area"):
            text += f". {nws['area']}"
    else:
        gdacs = event.get("gdacs")
        if isinstance(gdacs, dict) and (gdacs.get("name") or gdacs.get("eventtype")):
            text = (f"{gdacs.get('name') or gdacs.get('eventtype')} "
                    f"({gdacs.get('eventtype') or 'event'}) in "
                    f"{gdacs.get('country') or 'an affected area'}")
        else:
            text = (f"{event.get('kind', 'other')} disruption reported by "
                    f"{event.get('source', 'feed')} (severity "
                    f"{event.get('severity')})")
    if node_name:
        text += f". Location: {node_name}."
    return text


def derive_scenario(event: dict, candidate: dict) -> tuple[dict, dict]:
    """The documented deterministic derivation (see module docstring).

    Returns (scenario spec for engine validation, derivation record).
    Every value in the record carries its source label; table/default
    values are labelled MODELED."""
    kind = candidate.get("kind") or event.get("kind") or "other"
    kind_source = "classifier" if candidate.get("kind") else "event.kind (fallback)"

    if event.get("capacity_loss") is not None:
        capacity_loss = float(event["capacity_loss"])
        capacity_source = "event.capacity_loss (feed-stated)"
    else:
        sev = candidate.get("severity")
        sev = sev if isinstance(sev, int) and sev in CAPACITY_LOSS_BY_SEVERITY \
            else event.get("severity")
        capacity_loss = CAPACITY_LOSS_BY_SEVERITY.get(sev, 0.50)
        capacity_source = (f"CAPACITY_LOSS_BY_SEVERITY[{sev}] "
                           "(MODELED severity table)")

    if event.get("expected_duration_d"):
        duration_d = float(event["expected_duration_d"])
        duration_source = "event.expected_duration_d (feed-stated)"
    elif candidate.get("expected_duration_d"):
        duration_d = float(candidate["expected_duration_d"])
        duration_source = "classifier expected_duration_d"
    else:
        duration_d = DEFAULT_DURATION_D
        duration_source = f"default {DEFAULT_DURATION_D}d (MODELED; API live-path precedent)"

    spec = {
        "node_id": event.get("node_id"),
        "kind": kind,
        "capacity_loss": capacity_loss,
        "duration_d": duration_d,
        "shape": "step",
        "started_at": event.get("started_at") or event.get("detected_at"),
    }
    derivation = {
        "policy": POLICY_ID,
        "modelled": True,
        "note": _DERIVATION_NOTE,
        "kind": {"value": kind, "source": kind_source},
        "capacity_loss": {"value": capacity_loss, "source": capacity_source},
        "duration_d": {"value": duration_d, "source": duration_source},
        "shape": {"value": "step", "source": "API live-path precedent"},
        "classifier": {
            "node_id": candidate.get("node_id"),
            "kind": candidate.get("kind"),
            "severity": candidate.get("severity"),
            "confidence": candidate.get("confidence"),
            "mintable": candidate.get("mintable"),
            "threshold": candidate.get("threshold", MINT_THRESHOLD),
            "transport": candidate.get("transport"),
            "model": candidate.get("model"),
        },
        "c3_severity": event.get("severity"),
    }
    return spec, derivation


def run_autolock(events: list[dict], *,
                 ledger_path: Path | None = None,
                 runs_dir: Path | None = None,
                 graph_path: Path | None = None,
                 page_path: Path | None = None,
                 classifier=None,
                 now: datetime | None = None,
                 cap: int = MAX_LOCKS_PER_CYCLE,
                 max_classify: int | None = None,
                 memo: dict | None = None,
                 logger=None) -> dict:
    """Evaluate `events` (event dicts, store order) under autolock-v1 and
    lock the qualifying ones. Returns the cycle summary dict; every
    decision is also passed to `logger` (a callable taking one string)
    when one is given.

    CREDIT BOUNDS: a cycle makes at most
    ``max_classify`` classifier calls (default SUPERMAP_AUTOLOCK_MAX_CLASSIFY,
    12; the rest are recorded as ``classifier_budget`` and wait for the next
    cycle), and ``memo`` -- a dict the caller keeps between cycles -- stops
    an event whose text has not changed from being classified (and paid
    for) again in every later cycle."""
    if max_classify is None:
        max_classify = _max_classify()
    classify_calls = 0
    now_dt = now or datetime.now(timezone.utc)
    locked_at = _fmt_ts(now_dt)
    ledger_path = Path(ledger_path) if ledger_path is not None else None
    ledger = LedgerStore(ledger_path) if ledger_path is not None else LedgerStore()
    runs_dir = Path(runs_dir) if runs_dir is not None else DEFAULT_RUNS_DIR
    graph_path = Path(graph_path or os.environ.get(
        "SUPERMAP_GRAPH", DEFAULT_GRAPH_PATH))
    graph = Graph(json.loads(graph_path.read_text(encoding="utf-8")))
    node_ids = set(graph.nodes)
    node_names = {nid: n.get("name") or nid for nid, n in graph.nodes.items()}
    node_tiers = {nid: n.get("tier", "core")
                  for nid, n in graph.nodes.items()}
    if classifier is None:
        classifier = Classifier(LLMProvider.for_background(), graph_path=graph_path)
    engine = Engine(graph)
    run_store = RunStore(runs_dir)

    already_locked = {e["disruption_id"] for e in ledger.entries()}
    decisions: list[dict] = []
    locked_entries: list[dict] = []

    def decide(disruption_id, decision, reason, **extra):
        rec = {"disruption_id": disruption_id, "decision": decision,
               "reason": reason, **extra}
        decisions.append(rec)
        if logger is not None:
            if decision == "locked":
                logger(f"[autolock] LOCKED {disruption_id} -> entry "
                       f"{extra.get('entry_id')} run {extra.get('run_id')} "
                       f"(derived scenario, MODELED: "
                       f"kind={extra.get('derived_kind')} "
                       f"capacity_loss={extra.get('derived_capacity_loss')} "
                       f"duration_d={extra.get('derived_duration_d')})")
            else:
                logger(f"[autolock] skipped {disruption_id}: {reason}"
                       + (f" ({extra['detail']})" if extra.get("detail") else ""))
        return rec

    for event in events:
        if not isinstance(event, dict) or not event.get("id"):
            decide(str(event.get("id")) if isinstance(event, dict) else None,
                   "skipped", "malformed_event")
            continue
        did = event["id"]
        # 1. dedupe, strictly by disruption_id
        if did in already_locked:
            decide(did, "skipped", "already_locked")
            continue
        # 2. graph mapping
        node_id = event.get("node_id")
        if node_id not in node_ids:
            decide(did, "skipped", "unmapped_node",
                   detail=f"node_id {node_id!r} not in graph")
            continue
        # 2b. tier guard (core/shell tiers): an event at a
        # SHELL node is routing-grade signal only — it never originates
        # a Ledger lock. Refused here, before any classifier spend; the
        # LedgerStore.lock tier guard is the second seam.
        if node_tiers.get(node_id) == "shell":
            decide(did, "skipped", "shell_node",
                   detail=f"node {node_id} is SHELL tier (routing-grade); "
                          "shell nodes never originate Ledger locks")
            continue
        # 3. status
        if event.get("status") not in LOCKABLE_STATUSES:
            decide(did, "skipped", "status_not_active",
                   detail=f"status={event.get('status')!r}")
            continue
        # 4. severity bar
        sev = event.get("severity")
        if not isinstance(sev, int) or isinstance(sev, bool) \
                or sev < MIN_SEVERITY:
            decide(did, "skipped", "low_severity",
                   detail=f"severity {sev!r} < bar {MIN_SEVERITY}")
            continue
        # 5. freshness of detection
        detected_dt = _parse_ts(event.get("detected_at"))
        if detected_dt is None:
            decide(did, "skipped", "stale_detection",
                   detail="detected_at missing/unparseable")
            continue
        age_h = (now_dt - detected_dt).total_seconds() / 3600.0
        if age_h > FRESHNESS_WINDOW_H:
            decide(did, "skipped", "stale_detection",
                   detail=f"detected {age_h:.1f}h before lock moment "
                          f"> freshness window {FRESHNESS_WINDOW_H}h")
            continue
        # 8 (cap, checked before spending a classifier call): an event
        # that passed every cheap check but arrives after the cap is
        # recorded cap_reached.
        if len(locked_entries) >= cap:
            decide(did, "skipped", "cap_reached",
                   detail=f"per-cycle cap {cap} already reached")
            continue
        # 6. classifier gate (fixed 0.70 mint threshold)
        text = classifier_text(event, node_names.get(node_id))
        memo_key = (did, hashlib.sha256(text.encode("utf-8")).hexdigest()[:16])
        if memo is not None and memo_key in memo:
            decide(did, "skipped", "already_classified",
                   detail=f"judged {memo[memo_key]} in an earlier cycle; "
                          "its text has not changed")
            continue
        if classify_calls >= max_classify:
            decide(did, "skipped", "classifier_budget",
                   detail=f"per-cycle classifier budget {max_classify} used")
            continue
        classify_calls += 1
        try:
            candidate = classifier.classify(
                text, str(event.get("source") or ""),
                str(event.get("retrieved_at") or locked_at))
        except Exception as exc:  # a classifier failure never locks, never crashes the cycle
            decide(did, "skipped", "classifier_error", detail=str(exc))
            continue
        conf = candidate.get("confidence")
        if memo is not None:
            memo[memo_key] = "low_confidence" if (
                not candidate.get("mintable") or conf is None
                or float(conf) < MINT_THRESHOLD) else "classified"
            if len(memo) > 5000:
                memo.pop(next(iter(memo)))
        if not candidate.get("mintable") or conf is None \
                or float(conf) < MINT_THRESHOLD:
            decide(did, "skipped", "low_confidence",
                   detail=f"classifier confidence {conf} / mintable "
                          f"{candidate.get('mintable')} < "
                          f"{MINT_THRESHOLD} (kind={candidate.get('kind')}, "
                          f"severity={candidate.get('severity')}, "
                          f"node={candidate.get('node_id')}, "
                          f"transport={candidate.get('transport')})")
            continue
        # 7. classifier / adapter node agreement
        if candidate.get("node_id") != node_id:
            decide(did, "skipped", "classifier_node_mismatch",
                   detail=f"classifier node {candidate.get('node_id')!r} "
                          f"!= event node {node_id!r}")
            continue
        # Derive + run (deterministic run_id, exactly as the API does).
        spec_raw, derivation = derive_scenario(event, candidate)
        try:
            spec = validate_disruption(engine.graph, spec_raw)
            params = validate_params(None)
        except Exception as exc:
            decide(did, "skipped", "invalid_scenario", detail=str(exc))
            continue
        journal_core = {"kind": "run", "graph_hash": graph.hash,
                        "disruption": spec, "params": params,
                        "live_disruption_id": did}
        run_id = derive_run_id(journal_core)
        state = engine.run(run_id, spec, params)
        chain = state.events
        if not chain:
            decide(did, "skipped", "empty_chain")
            continue
        # 9. knowability guard: root step's grading window not yet closed
        started_dt = _parse_ts(spec.get("started_at")) or detected_dt
        window_close = started_dt + timedelta(days=2.0 * float(chain[0]["delay_d"]))
        if now_dt >= window_close:
            decide(did, "skipped", "outcome_window_passed",
                   detail=f"root grading window closed { _fmt_ts(window_close) }")
            continue
        # 10. a short live chain is skipped, never locked —
        # LedgerStore.lock would refuse it (fewer than 3 downstream
        # predictions), and that refusal must not kill the cycle.
        if len(chain) < 4:
            decide(did, "skipped", "chain_too_short",
                   detail=f"predicted chain length {len(chain)} < 4 "
                          "(fewer than 3 downstream predictions)")
            continue
        # Persist the run (idempotent: deterministic run_id) + lock.
        if not run_store.exists(run_id):
            journal = {**journal_core, "graph_version": graph.version,
                       "autolock": derivation}
            run_store.save(run_id, journal, state)
        try:
            entry = ledger.lock(
                event, {"run_id": run_id, "events": chain},
                locked_at=locked_at, node_tiers=node_tiers,
                # The events evaluated here ARE the server's
                # event store records for this cycle — pass their ids so
                # the store enforces referential integrity on this path.
                known_event_ids={e["id"] for e in events
                                 if isinstance(e, dict) and e.get("id")})
        except LedgerError as exc:
            # A store refusal (length, tier guard, duplicate)
            # skips this event; it never kills the cycle.
            decide(did, "skipped", "lock_refused", detail=str(exc))
            continue
        already_locked.add(did)
        locked_entries.append(entry)
        decide(did, "locked", "qualifying",
               entry_id=entry["entry_id"], run_id=run_id,
               derived_kind=spec["kind"],
               derived_capacity_loss=spec["capacity_loss"],
               derived_duration_d=spec["duration_d"],
               classifier_confidence=conf,
               classifier_transport=candidate.get("transport"))

    if locked_entries:
        # Refresh the public render from the same store (best effort: a
        # render failure never undoes a lock; the lock line above stands).
        try:
            from .ledger_page import generate_page
            target = Path(page_path) if page_path is not None \
                else ledger.path.parent / "ledger-public.html"
            generate_page(ledger.path, target)
        except Exception as exc:
            if logger is not None:
                logger(f"[autolock] public page regeneration failed: {exc}")

    return {
        "policy": POLICY_ID,
        "enabled": True,
        "evaluated": len(events),
        "locked_count": len(locked_entries),
        "locked_entry_ids": [e["entry_id"] for e in locked_entries],
        "decisions": decisions,
    }
