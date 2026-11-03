"""RunBundle: everything one report / data pack reads about a run,
loaded once, read-only. Absent inputs stay absent (None / False flags);
nothing is substituted.
"""
from __future__ import annotations

from impact.derive import ImpactService
from impact.store import ImpactStore
from impact.rows import place_rows
from ports.sources import (Ctx, load_graph, load_json, read_exposures,
                           read_ledger)

from chain_edges import materialise_edges


class ReportError(Exception):
    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _replay_record(run_id: str):
    """A recorded replay chain id (replay_<episode>_<engine>) resolves to its recorded chain, read-only."""
    if not isinstance(run_id, str) or not run_id.startswith("replay_"):
        return None
    try:
        from api.replay_episodes import recorded_run_record
    except ImportError:  # imported as server.* from the repo root
        from server.api.replay_episodes import recorded_run_record  # type: ignore
    return recorded_run_record(run_id)


class RunBundle:
    def __init__(self, ctx: Ctx, run_id: str):
        self.ctx = ctx
        self.run_id = run_id
        store = ctx.run_store
        rec = store.load(run_id) if store is not None else None
        if rec is None:
            rec = _replay_record(run_id)
        if rec is None:
            raise ReportError("run_not_found",
                              f"unknown run_id: {run_id!r}", 404)
        self.record = rec
        self.recorded_replay = bool((rec.get("journal") or {}).get("recorded_replay"))
        self.graph = load_graph(ctx.graph_path)
        self.nodes = self.graph.nodes if self.graph else {}
        self.impact_store = ImpactStore(
            snapshots_dir=ctx.snapshots_dir, impact_dir=ctx.impact_dir,
            graph_path=ctx.graph_path)
        try:
            self.impact = ImpactService(self.impact_store).run_payload(rec)
        except ValueError as exc:
            raise ReportError("impact_unavailable",
                              f"impact derivation failed: {exc}", 500)
        self.place_rows = place_rows(self.impact)
        self.scorecard = load_json(ctx.scorecard_path)
        if not isinstance(self.scorecard, dict):
            self.scorecard = None
        self.ledger_all, self.ledger_present = read_ledger(ctx.ledger_path)
        self.exposures = read_exposures(ctx.exposures_path)

    # -- chain ---------------------------------------------------------
    @property
    def events(self) -> list[dict]:
        return self.record["events"]

    @property
    def journal(self) -> dict:
        return self.record["journal"]

    @property
    def edges(self) -> list[dict]:
        return materialise_edges(
            self.events, self.journal["disruption"]["node_id"])

    def name(self, place_id: str) -> str:
        n = self.nodes.get(place_id) or {}
        return n.get("name") or place_id

    def tier(self, place_id: str):
        return (self.nodes.get(place_id) or {}).get("tier")

    def chain_place_ids(self) -> list[str]:
        return sorted({e["node_id"] for e in self.events})

    def ledger_entries(self) -> list[dict]:
        return [e for e in self.ledger_all if e.get("run_id") == self.run_id]
