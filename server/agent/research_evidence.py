"""Per-session evidence list for agent research tasks.

Every research tool result the agent receives is recorded here under a stable
id (E1, E2, ...). The id travels back to the model inside the tool result, so
the model can cite it; the research note (``research_note.py``) may state only
what an evidence entry holds, and the verifier (``research_verify.py``)
checks every number and claim against these entries.

An entry keeps, for audit and for the verifier:
  id            E<n>, assigned in order of first record, never reused
  tool          the research tool that produced it
  kind          catalog | episodes | assemble | analysis kind | scene_scan
  args, args_sha256   the validated arguments and a hash of their canonical JSON
  analysis_id   the API's id (assembly_id or analysis id), when it has one
  n             sample size the API reported (days, events, scenes ...)
  label         the API's own label (descriptive statistics, not a forecast)
  caveat_chips  the API's caveat chips, verbatim
  numbers       every numeric value the model was shown, by dotted path
  strings       short strings the model was shown (dates, names, ids)
  result_sha256 hash of the digest the model was shown
  figure        the API's chart_spec, kept for the export, never shown to the model
  meta          tool-side facts used to validate later calls (series keys ...)

The same call (same tool and arguments) returns the same entry, so a repeated
call never grows the list. Entries are data: nothing here is executed.
"""
from __future__ import annotations

import hashlib
import json
import math
import threading

MAX_EVIDENCE = 60
MAX_NUMBERS = 400
MAX_STRINGS = 300
MAX_STR_LEN = 200
MAX_LIST_WALK = 40
SKIP_KEYS = frozenset({"chart_spec"})


class EvidenceError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str,
                      ensure_ascii=False)


def sha256_hex(obj) -> str:
    return hashlib.sha256(canonical(obj).encode("utf-8")).hexdigest()


def numeric_leaves(obj, prefix: str = "", out: dict | None = None, depth: int = 0) -> dict:
    """{dotted path: number} for every finite number below ``obj`` (bools are
    not numbers). Lists are walked up to MAX_LIST_WALK items."""
    out = {} if out is None else out
    if depth > 8 or len(out) >= MAX_NUMBERS:
        return out
    if isinstance(obj, bool):
        return out
    if isinstance(obj, (int, float)):
        if isinstance(obj, float) and not math.isfinite(obj):
            return out
        out[prefix or "value"] = obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if k in SKIP_KEYS:
                continue
            numeric_leaves(v, f"{prefix}.{k}" if prefix else str(k), out, depth + 1)
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(list(obj)[:MAX_LIST_WALK]):
            numeric_leaves(v, f"{prefix}[{i}]", out, depth + 1)
    return out


def string_leaves(obj, prefix: str = "", out: dict | None = None, depth: int = 0) -> dict:
    """{dotted path: short string} for the strings below ``obj``."""
    out = {} if out is None else out
    if depth > 8 or len(out) >= MAX_STRINGS:
        return out
    if isinstance(obj, str):
        if obj.strip():
            out[prefix or "value"] = obj[:MAX_STR_LEN]
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if k in SKIP_KEYS:
                continue
            string_leaves(v, f"{prefix}.{k}" if prefix else str(k), out, depth + 1)
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(list(obj)[:MAX_LIST_WALK]):
            string_leaves(v, f"{prefix}[{i}]", out, depth + 1)
    return out


class EvidenceLog:
    """Ordered, de-duplicated evidence for one agent session. Thread-safe."""

    def __init__(self):
        self.items: list[dict] = []
        self._by_key: dict[str, str] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------- write
    def add(self, *, tool: str, kind: str, args: dict, digest: dict,
            analysis_id: str | None = None, n=None, label: str | None = None,
            caveat_chips: list | None = None, figure: dict | None = None,
            meta: dict | None = None, summary: str = "") -> dict:
        """Record (or find) the entry for this call. Returns the entry."""
        args_sha = sha256_hex(args)[:16]
        key = f"{tool}:{args_sha}"
        with self._lock:
            known = self._by_key.get(key)
            if known:
                return next(e for e in self.items if e["id"] == known)
            if len(self.items) >= MAX_EVIDENCE:
                raise EvidenceError("evidence_cap",
                                    f"the evidence list is full ({MAX_EVIDENCE} entries)")
            eid = f"E{len(self.items) + 1}"
            entry = {
                "id": eid, "tool": tool, "kind": kind, "args": args,
                "args_sha256": args_sha, "analysis_id": analysis_id,
                "n": n if isinstance(n, (int, float)) and not isinstance(n, bool) else None,
                "label": label, "caveat_chips": list(caveat_chips or [])[:12],
                "summary": summary[:200],
                "numbers": numeric_leaves(digest),
                "strings": string_leaves(digest),
                "result_sha256": sha256_hex(digest),
                "figure": figure, "meta": meta or {},
            }
            self.items.append(entry)
            self._by_key[key] = eid
            return entry

    # -------------------------------------------------------------- read
    def get(self, eid: str) -> dict | None:
        with self._lock:
            return next((e for e in self.items if e["id"] == eid), None)

    def ids(self) -> list[str]:
        with self._lock:
            return [e["id"] for e in self.items]

    def of_kind(self, *kinds: str) -> list[dict]:
        with self._lock:
            return [e for e in self.items if e["kind"] in kinds]

    def assembly(self, assembly_id: str) -> dict | None:
        """The assemble entry that produced ``assembly_id`` in this session."""
        with self._lock:
            return next((e for e in self.items
                         if e["kind"] == "assemble" and e["analysis_id"] == assembly_id), None)

    def public(self) -> list[dict]:
        """The compact list the UI shows (no numbers, no figure)."""
        with self._lock:
            return [{"id": e["id"], "tool": e["tool"], "kind": e["kind"],
                     "args_sha256": e["args_sha256"], "analysis_id": e["analysis_id"],
                     "n": e["n"], "label": e["label"], "summary": e["summary"],
                     "caveat_chips": e["caveat_chips"], "has_figure": bool(e["figure"]),
                     "result_sha256": e["result_sha256"][:16]}
                    for e in self.items]

    def dump(self) -> list[dict]:
        with self._lock:
            return [dict(e) for e in self.items]

    def load(self, items: list[dict]) -> None:
        with self._lock:
            self.items = [dict(e) for e in items if isinstance(e, dict) and "id" in e]
            self._by_key = {f"{e['tool']}:{e['args_sha256']}": e["id"] for e in self.items}

    def payload_view(self, max_values: int = 60) -> list[dict]:
        """What the note writer is shown: id, tool, kind, n, label, chips and
        the numbers/strings of each entry (capped). The figure is never sent."""
        with self._lock:
            out = []
            for e in self.items:
                nums = list(e["numbers"].items())[:max_values]
                out.append({"id": e["id"], "tool": e["tool"], "kind": e["kind"],
                            "analysis_id": e["analysis_id"],
                            "arguments": e["args"], "n": e["n"], "label": e["label"],
                            "caveat_chips": e["caveat_chips"],
                            "values": [{"name": k, "value": v} for k, v in nums],
                            "text": [{"name": k, "value": v} for k, v in
                                     list(e["strings"].items())[:max_values]]})
            return out


class ResearchState:
    """Everything research-related one agent session carries."""

    def __init__(self):
        self.evidence = EvidenceLog()
        self.notes: list[dict] = []
        self.question: str = ""
        self.provider = None           # set by the manager; not persisted
        self.note_state: str = "none"  # none | writing | ready | failed
        self.note_error: str | None = None
        self.lock = threading.RLock()

    def latest_note(self) -> dict | None:
        with self.lock:
            return self.notes[-1] if self.notes else None

    def note(self, note_id: str) -> dict | None:
        with self.lock:
            return next((n for n in self.notes if n["id"] == note_id), None)

    def summary(self) -> dict:
        n = self.latest_note()
        return {"evidence": self.evidence.public(), "note_state": self.note_state,
                "note_error": self.note_error,
                "note": ({"id": n["id"], "title": n["title"], "label": n["label"],
                          "verification": n["verification"]} if n else None)}

    def dump(self) -> dict:
        with self.lock:
            return {"evidence": self.evidence.dump(), "notes": list(self.notes),
                    "question": self.question}

    def load(self, doc: dict) -> None:
        if not isinstance(doc, dict):
            return
        self.evidence.load(doc.get("evidence") or [])
        self.notes = [n for n in (doc.get("notes") or []) if isinstance(n, dict) and "id" in n]
        self.question = str(doc.get("question") or "")
        self.note_state = "ready" if self.notes else "none"
