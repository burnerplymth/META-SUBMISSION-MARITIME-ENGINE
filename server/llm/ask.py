"""Ask-the-map: {question, scope_node_ids} -> cited answer.

The subgraph is computed from graph data (data/graph/graph.json, read-only):
the caller's explicit scope nodes (ask-scope = caller's explicit
scope_node_ids), their 1-hop neighbours *within the scope*, and the edges
among scoped nodes. Run ids are collected from stored run records whose
journal disruption node is in scope (read-only scan of data/runs/).

Chain-grounded ask: an optional ``run_id`` grounds the
question in ONE stored causal chain (the loaded run). The chain's
events go to the ``ask_chain`` prompt next to the subgraph; the scope
must lie inside the chain's own nodes; the answer is POST-CHECKED with
the same check the explain narrative uses (every number a step record
holds, every node the chain contains, no other graph place named). A
failed check discards the model text and serves the deterministic
template (``answer_source: "template"``, ``post_check:
"TEMPLATE_FALLBACK"``). A model reply of ``{"refused": true}`` is served
as a refusal with reason ``not_in_chain``. The LLM never sits in the
engine: it restates the chain or refuses.

Out-of-graph handling (hard rule): if the scope is empty, names no graph
node, or the question explicitly asks about a graph node outside the scope,
the answer is the explicit refusal shape — no provider call, cost $0:
  {answer: "OUT_OF_GRAPH: ...", cited_node_ids: [], run_ids: [],
   cost_usd: 0.0, refused: true, reason: "out_of_graph"}
With a ``run_id`` a scope node that is not in the chain is refused the same
way with reason ``scope_outside_chain``.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import grounding
from . import provenance as prov
from .explain_check import check_narrative, render_template
from .provider import LLMProvider

REPO_ROOT = Path(__file__).resolve().parents[2]
REFUSAL_PREFIX = "OUT_OF_GRAPH"

# A free-scope ask (no run_id) sends the subgraph of the scoped places as
# input tokens, billed per call. With every place in scope that is the whole
# network. Above this many distinct places the question is refused before any
# model call. A chain-grounded ask (run_id) is bounded by the chain itself.
MAX_SCOPE_NODES = 25

# Chain event fields the model is shown (the chain-run record, nothing derived).
_CHAIN_FIELDS = ("seq", "node_id", "parent_id", "channel", "mechanism",
                 "delay_d", "magnitude", "at")


def _load_graph(graph_path: Path | None) -> dict:
    import os
    path = graph_path or Path(
        os.environ.get("SUPERMAP_GRAPH", REPO_ROOT / "data" / "graph" / "graph.json"))
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _ids(ids) -> str:
    """Caller-supplied ids echoed in a refusal text: at most 5, 40 chars each."""
    ids = [str(i)[:40] for i in ids]
    more = f" (+{len(ids) - 5} more)" if len(ids) > 5 else ""
    return str(ids[:5]) + more


def _refusal(reason: str, code: str = "out_of_graph") -> dict:
    return {
        "answer": f"{REFUSAL_PREFIX}: {reason} This question lies outside "
                  "the scoped places, so SupermAP will not improvise an answer.",
        "cited_node_ids": [],
        "run_ids": [],
        "cost_usd": 0.0,
        "refused": True,
        "reason": code,
        "transport": "none (no LLM call made)",
        "provenance": prov.none("out-of-graph refusal: no model was called"),
    }


class AskTheMap:
    def __init__(self, provider: LLMProvider | None = None,
                 graph_path: Path | None = None,
                 runs_dir: Path | None = None,
                 chain_fetcher=None):
        self.provider = provider or LLMProvider.from_env()
        self._chain_fetcher = chain_fetcher
        self.graph = _load_graph(graph_path)
        self.nodes = {n["id"]: n for n in self.graph.get("nodes", [])}
        self.edges = self.graph.get("edges", [])
        self.runs_dir = Path(runs_dir or (REPO_ROOT / "data" / "runs"))

    def subgraph(self, scope_node_ids: list[str]) -> dict:
        scope = [s for s in scope_node_ids if s in self.nodes]
        scope_set = set(scope)
        edges = [e for e in self.edges
                 if e.get("from") in scope_set and e.get("to") in scope_set]
        run_ids: list[str] = []
        if self.runs_dir.exists():
            for path in sorted(self.runs_dir.glob("*.json")):
                try:
                    rec = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                node = (rec.get("journal", {}).get("disruption", {})
                        .get("node_id"))
                if node in scope_set and rec.get("run_id"):
                    run_ids.append(rec["run_id"])
        return {
            "nodes": [{"id": n, "name": self.nodes[n].get("name"),
                       "mode": self.nodes[n].get("mode"),
                       "kind": self.nodes[n].get("kind")} for n in scope],
            "edges": edges,
            "run_ids": run_ids,
        }

    def _fetch_chain(self, run_id: str) -> dict:
        if self._chain_fetcher is None:
            from .explain import default_chain_fetcher
            self._chain_fetcher = default_chain_fetcher
        return self._chain_fetcher(run_id)

    def _guard(self, question: str, scope_node_ids: list[str],
               chain: dict | None) -> dict | None:
        """The refusal shape when the question may not be answered at all
        (no model call), else None."""
        if not scope_node_ids:
            return _refusal("no scope_node_ids were supplied.")
        if chain is None and len(set(scope_node_ids)) > MAX_SCOPE_NODES:
            return _refusal(
                f"more than {MAX_SCOPE_NODES} places were scoped; narrow the "
                "question to a few places.", "scope_too_large")
        unknown = [s for s in scope_node_ids if s not in self.nodes]
        scoped = [s for s in scope_node_ids if s in self.nodes]
        if not scoped:
            return _refusal(
                f"none of the scoped ids are known places: {_ids(unknown)}.")
        if chain is not None:
            in_chain = {ev.get("node_id") for ev in chain.get("events") or []}
            outside_chain = [s for s in scoped if s not in in_chain]
            if outside_chain:
                return _refusal(
                    f"scoped place(s) {_ids(outside_chain)} are not part of chain "
                    f"{chain.get('run_id')}.", "scope_outside_chain")
        # A question naming a graph node outside the explicit scope is
        # out-of-graph for this call (ask-scope rule).
        q = question.lower()
        words = q.split()

        def labels(nid: str) -> set[str]:
            name = (self.nodes[nid].get("name") or "").lower()
            suffix = nid.split("_", 1)[-1]
            return {name} | ({suffix} if len(suffix) >= 4 else set())

        # A label an in-scope node also carries (sea_jeddah and air_jeddah
        # both end in "jeddah") names the in-scope place, not an outsider.
        scoped_labels = set().union(*(labels(s) for s in scoped))
        outside = []
        for nid, n in self.nodes.items():
            if nid in scoped:
                continue
            name = (n.get("name") or "").lower()
            suffix = nid.split("_", 1)[-1]
            hits = set()
            if name and name in q:
                hits.add(name)
            if len(suffix) >= 4 and suffix in words:
                hits.add(suffix)
            if hits - scoped_labels:
                outside.append(nid)
        if outside:
            return _refusal(
                f"the question asks about place(s) outside the supplied "
                f"scope: {_ids(sorted(set(outside)))}.")
        return None

    def _payload(self, question: str, scope_node_ids: list[str],
                 chain: dict | None) -> tuple[str, dict]:
        sub = self.subgraph(scope_node_ids)
        if chain is None:
            return "ask", {"question": question, "subgraph": sub}
        return "ask_chain", {
            "question": question, "subgraph": sub,
            "chain": {"run_id": chain.get("run_id"),
                      "stream_hash": chain.get("stream_hash"),
                      "events": [{k: ev.get(k) for k in _CHAIN_FIELDS}
                                 for ev in chain.get("events") or []]}}

    def cost_preview(self, question: str, scope_node_ids: list[str],
                     run_id: str | None = None) -> dict:
        """The pre-call estimate, or the refusal shape when the guard would
        refuse the question (a refusal is free: no model is called)."""
        chain = self._fetch_chain(run_id) if run_id else None
        refusal = self._guard(question, scope_node_ids, chain)
        if refusal is not None:
            return refusal
        name, payload = self._payload(question, scope_node_ids, chain)
        return {**self.provider.cost_preview(name, payload), "refused": False}

    def _foreign_names(self, answer: str, chain: dict) -> list[str]:
        """Graph places NAMED in the answer that the chain does not contain."""
        in_chain = {ev.get("node_id") for ev in chain.get("events") or []}
        chain_names = [str(self.nodes[n].get("name") or "").lower()
                       for n in in_chain if n in self.nodes]
        text = answer.lower()
        out = []
        for nid, n in self.nodes.items():
            name = str(n.get("name") or "").lower()
            if nid in in_chain or len(name) < 5 or name not in text:
                continue
            if any(name in cn for cn in chain_names):
                continue
            out.append(nid)
        return out

    def ask(self, question: str, scope_node_ids: list[str],
            run_id: str | None = None) -> dict:
        chain = self._fetch_chain(run_id) if run_id else None
        refusal = self._guard(question, scope_node_ids, chain)
        if refusal is not None:
            return refusal
        scoped = [s for s in scope_node_ids if s in self.nodes]
        sub = self.subgraph(scope_node_ids)
        name, payload = self._payload(question, scope_node_ids, chain)
        res = self.provider.complete(name, payload)
        model_refused = False
        # Place ids a reply may cite: the chain's own nodes, else the scope.
        allowed_ids = ({ev.get("node_id") for ev in chain.get("events") or []}
                       if chain is not None else set(scoped))
        allowed_ids = {i for i in allowed_ids if i in self.nodes}
        known_runs = ({chain.get("run_id")} if chain is not None
                      else set(sub["run_ids"]))
        try:
            raw = json.loads(res["text"])
            if not isinstance(raw, dict):
                raise TypeError("reply is not an object")
            model_refused = raw.get("refused") is True and chain is not None
            answer = raw.get("answer", "")
            answer = answer if isinstance(answer, str) else ""
            cited, cited_rejected = grounding.ground_id_list(
                raw.get("cited_node_ids", []), allowed_ids)
            run_ids, runs_rejected = grounding.ground_id_list(
                raw.get("run_ids", []), known_runs)
        except (json.JSONDecodeError, TypeError, AttributeError):
            # Not the JSON shape: the text is kept as the answer, but nothing
            # is cited on the model's behalf.
            answer, cited, run_ids = res["text"], [], []
            cited_rejected = runs_rejected = 0
        ground = {"cited_rejected": cited_rejected,
                  "run_ids_rejected": runs_rejected,
                  "reason": (grounding.NOT_IN_GRAPH
                             if cited_rejected or runs_rejected else None)}
        base = {"cost_usd": res["cost_usd"], "transport": res["transport"],
                "model": res["model"]}
        if model_refused:
            # The model declined (and was billed for it): a refusal that
            # keeps the real provenance. The code is fixed, never the
            # model's own words.
            return {"answer": "NOT_IN_CHAIN: the loaded chain does not hold "
                              "the answer to this question.",
                    "cited_node_ids": [], "run_ids": [], "refused": True,
                    "grounding": ground,
                    "reason": "not_in_chain", **base,
                    "provenance": prov.from_result(res, name)}
        if chain is None:
            # Citations are the model's own, validated against the scope
            # above (ground_id_list); the server never fills them in.
            unknown = grounding.find_unknown_ids(answer, set(scoped))
            foreign = grounding.foreign_place_names(
                answer, set(scoped), self.graph.get("nodes", []))
            if unknown or foreign or not answer.strip():
                # The prose names a place id outside the scope (or one the
                # graph does not hold), or is empty: the model text is
                # withheld.
                ground["reason"] = (
                    "answer_named_place_id_outside_scope" if unknown
                    else "answer_named_place_outside_scope" if foreign
                    else "empty_answer")
                return {"answer": "WITHHELD: the model answer is not "
                                  "grounded in the scoped places and legs.",
                        "cited_node_ids": [], "run_ids": [],
                        "refused": False, "answer_source": "withheld",
                        "grounding": ground, **base,
                        "provenance": {**prov.from_result(res, name),
                                       "narrative_source": "withheld"}}
            return {"answer": answer, "cited_node_ids": cited,
                    "run_ids": run_ids, "refused": False,
                    "grounding": ground, **base,
                    "provenance": prov.from_result(res, name)}
        # Chain-grounded: edge-out discipline. The model may only restate
        # the chain; a number or place the records do not hold voids it.
        ok, reason = check_narrative(answer, chain)
        if ok and not answer.strip():
            ok, reason = False, "empty_answer"
        if ok and self._foreign_names(answer, chain):
            ok, reason = False, "node_not_in_chain"
        cited = cited or scoped
        source = "model" if ok else "template"
        post_check = "PASS" if ok else "TEMPLATE_FALLBACK"
        return {"answer": answer if ok else render_template(chain),
                "cited_node_ids": cited,
                "run_ids": [chain.get("run_id")], "refused": False,
                "answer_source": source, "post_check": post_check,
                "post_check_reason": None if ok else reason,
                "run_id": chain.get("run_id"),
                "stream_hash": chain.get("stream_hash"),
                "grounding": ground, **base,
                "provenance": {**prov.from_result(res, name),
                               "narrative_source": source,
                               "post_check": post_check}}
