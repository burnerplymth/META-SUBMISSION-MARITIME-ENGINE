"""AlertsStore — JSONL persistence under one directory.

Layout (default ``data/alerts/``):
  * ``rules.jsonl``      — upsert lines (a full rule object) and
    tombstone lines (``{"id": ..., "deleted": true}``); the merged read
    view applies lines in order, last write wins.
  * ``watchlists.jsonl`` — same upsert/tombstone discipline for
    watchlists (``{"id", "name", "node_ids", "created_at",
    "updated_at"}``).
  * ``fired.jsonl``      — append-only fired-alert records.
  * ``state.json``       — evaluator state (per-rule adapter-condition
    memory + processed fact id sets), replaced atomically
    (tmp + rename, the RunStore discipline).

Every read is corrupt-line tolerant (one malformed
line in an append-written file must never be fatal): bad lines are
skipped and counted on ``self.skipped_lines``.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import ownership as _ownership
from .validate import RuleValidationError


def _unique_tmp(path: Path) -> Path:
    """Per-writer unique temp path for atomic replace:
    the deterministic ``<name>.tmp`` shared by concurrent writers let
    one writer's replace consume another's temp file."""
    return path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")


def now_iso() -> str:
    """ISO-8601 UTC, seconds, Z — the house timestamp format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class AlertsStore:
    def __init__(self, base_dir: str | Path):
        self.dir = Path(base_dir)
        self.rules_path = self.dir / "rules.jsonl"
        self.watchlists_path = self.dir / "watchlists.jsonl"
        self.fired_path = self.dir / "fired.jsonl"
        self.state_path = self.dir / "state.json"
        self._lock = threading.Lock()
        self.skipped_lines = 0

    # -- low-level tolerant IO -------------------------------------------
    def _read_jsonl(self, path: Path) -> list[dict]:
        if not path.exists():
            return []
        rows: list[dict] = []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        for line in lines:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                self.skipped_lines += 1
                continue
            if isinstance(row, dict):
                rows.append(row)
            else:
                self.skipped_lines += 1
        return rows

    def _append_jsonl(self, path: Path, row: dict) -> None:
        with self._lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    @staticmethod
    def _merged(rows: list[dict]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for row in rows:
            rid = row.get("id")
            if not isinstance(rid, str) or not rid:
                continue
            if row.get("deleted") is True:
                out.pop(rid, None)
            else:
                out[rid] = row
        return out

    # -- cross-process serialisation ----------------------------
    @contextlib.contextmanager
    def locked(self, name: str = "evaluate.lock"):
        """Hold an exclusive fcntl flock on a lock file in the store
        directory for a whole read-modify-write cycle. The evaluator's
        load->fire->save spans two store INSTANCES in production (the
        API's store and the adapters/run_once.py cycle's separate
        store on the same directory), so the per-instance threading
        lock cannot serialise it — the flock can (the llm/ledger.py
        pattern). Separate opens contend even within one process."""
        self.dir.mkdir(parents=True, exist_ok=True)
        fh = (self.dir / name).open("a+", encoding="utf-8")
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            finally:
                fh.close()

    # -- owner-token migration -----------------------------------
    def migrate_owner_tokens(self) -> int:
        """Rewrite every raw ``own_...`` owner token in rules.jsonl /
        watchlists.jsonl (history lines included) to its one-way key.
        Line-for-line: order, tombstones and every other field are
        preserved byte-for-byte; unparseable lines are kept verbatim.
        Atomic (unique tmp + replace) under the store flock; a no-op
        (file untouched) when nothing holds a raw token. Returns the
        number of lines rewritten."""
        changed = 0
        for path in (self.rules_path, self.watchlists_path):
            if not path.exists():
                continue
            with self.locked("migrate.lock"), self._lock:
                try:
                    lines = path.read_text(encoding="utf-8").splitlines()
                except OSError:
                    continue
                out: list[str] = []
                n = 0
                for line in lines:
                    new = line
                    if "own_" in line:
                        try:
                            row = json.loads(line)
                        except (ValueError, UnicodeDecodeError):
                            row = None
                        if isinstance(row, dict) \
                                and isinstance(row.get("owner"), str) \
                                and row["owner"] \
                                and not _ownership.is_owner_key(row["owner"]):
                            row["owner"] = _ownership.owner_key(row["owner"])
                            new = json.dumps(row, ensure_ascii=False,
                                             sort_keys=True)
                            n += 1
                    out.append(new)
                if n:
                    tmp = _unique_tmp(path)
                    tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
                    tmp.replace(path)
                    changed += n
        return changed

    # -- rules -------------------------------------------------------------
    def list_rules(self) -> list[dict]:
        merged = self._merged(self._read_jsonl(self.rules_path))
        return sorted(merged.values(),
                      key=lambda r: (str(r.get("created_at", "")), str(r.get("id", ""))))

    def get_rule(self, rule_id: str) -> dict | None:
        return self._merged(self._read_jsonl(self.rules_path)).get(rule_id)

    def upsert_rule(self, rule: dict, owner: str | None = None) -> dict:
        """Stamp the browser owner (when the caller supplies
        one) and enforce the per-owner rule cap — a NEW rule beyond
        MAX_RULES_PER_OWNER for its owner is a clean 422
        (RuleValidationError), never silently accepted. Re-writing an
        existing rule of the same owner is not counted against the cap."""
        if owner:
            rule = {**rule, "owner": _ownership.owner_key(owner)}
        elif rule.get("owner") and not _ownership.is_owner_key(rule["owner"]):
            rule = {**rule, "owner": _ownership.owner_key(rule["owner"])}
        effective_owner = rule.get("owner")
        live = self.list_rules()
        if rule.get("id") not in {r.get("id") for r in live} \
                and len(live) >= _ownership.MAX_RULES_TOTAL:
            raise RuleValidationError(
                f"rule cap reached: this service holds at most "
                f"{_ownership.MAX_RULES_TOTAL} rules")
        if effective_owner:
            owned = {r["id"] for r in live
                     if r.get("owner") == effective_owner}
            if rule.get("id") not in owned \
                    and len(owned) >= _ownership.MAX_RULES_PER_OWNER:
                raise RuleValidationError(
                    f"rule cap reached: at most "
                    f"{_ownership.MAX_RULES_PER_OWNER} rules per owner")
        self._append_jsonl(self.rules_path, rule)
        return rule

    def delete_rule(self, rule_id: str, owner: str | None = None,
                    admin_token: str | None = None) -> bool:
        existing = self.get_rule(rule_id)
        if existing is None:
            return False
        _ownership.authorize_delete(existing, owner=owner,
                                    admin_token=admin_token)
        self._append_jsonl(self.rules_path, {"id": rule_id, "deleted": True})
        return True

    # -- watchlists --------------------------------------------------------
    def list_watchlists(self) -> list[dict]:
        merged = self._merged(self._read_jsonl(self.watchlists_path))
        return sorted(merged.values(),
                      key=lambda w: (str(w.get("created_at", "")), str(w.get("id", ""))))

    def get_watchlist(self, watchlist_id: str) -> dict | None:
        return self._merged(self._read_jsonl(self.watchlists_path)).get(watchlist_id)

    def upsert_watchlist(self, watchlist: dict,
                         owner: str | None = None) -> dict:
        """Stamp the browser owner (when supplied) and enforce
        the per-watchlist node cap on EVERY upsert — past
        MAX_WATCHLIST_NODES node ids is a clean 422."""
        if owner:
            watchlist = {**watchlist, "owner": _ownership.owner_key(owner)}
        elif watchlist.get("owner") \
                and not _ownership.is_owner_key(watchlist["owner"]):
            watchlist = {**watchlist, "owner": _ownership.owner_key(
                watchlist["owner"])}
        live = self.list_watchlists()
        if watchlist.get("id") not in {w.get("id") for w in live} \
                and len(live) >= _ownership.MAX_WATCHLISTS_TOTAL:
            raise RuleValidationError(
                f"watchlist cap reached: this service holds at most "
                f"{_ownership.MAX_WATCHLISTS_TOTAL} watchlists")
        node_ids = watchlist.get("node_ids") or []
        if len(node_ids) > _ownership.MAX_WATCHLIST_NODES:
            raise RuleValidationError(
                f"watchlist holds at most "
                f"{_ownership.MAX_WATCHLIST_NODES} node ids")
        self._append_jsonl(self.watchlists_path, watchlist)
        return watchlist

    def delete_watchlist(self, watchlist_id: str, owner: str | None = None,
                         admin_token: str | None = None) -> bool:
        existing = self.get_watchlist(watchlist_id)
        if existing is None:
            return False
        _ownership.authorize_delete(existing, owner=owner,
                                    admin_token=admin_token)
        self._append_jsonl(self.watchlists_path,
                           {"id": watchlist_id, "deleted": True})
        return True

    # -- fired alerts ------------------------------------------------------
    def append_fired(self, fired: dict) -> dict:
        self._append_jsonl(self.fired_path, fired)
        return fired

    def read_fired(self, limit: int = 100, rule_id: str | None = None) -> list[dict]:
        rows = self._read_jsonl(self.fired_path)
        if rule_id is not None:
            rows = [r for r in rows if r.get("rule_id") == rule_id]
        rows.reverse()  # newest first
        return rows[:limit]

    # -- evaluator state ---------------------------------------------------
    def load_state(self) -> dict:
        if not self.state_path.exists():
            return {}
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (ValueError, OSError, UnicodeDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def save_state(self, state: dict) -> None:
        with self._lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = _unique_tmp(self.state_path)
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(state, fh, sort_keys=True)
            tmp.replace(self.state_path)
