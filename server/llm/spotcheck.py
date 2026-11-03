"""Classifier check on a small labelled sample set.

Runs the classifier over a small labelled set of feed-style texts
(spotcheck_data.json — includes the 3 real NOAA/NWS events from
data/events.jsonl, identified by their event ids, plus sample
headlines) and reports severity agreement.

With the fixture transport (no Meta key), agreement measures the
fixture transport's deterministic heuristic against the labels: it
exercises the classifier path and does not measure live accuracy. With
a key the same check runs unchanged (transport flips via env only).
"""
from __future__ import annotations

import json
from pathlib import Path

from .classifier import Classifier
from .provider import LLMProvider

DATA_PATH = Path(__file__).resolve().parent / "spotcheck_data.json"


def load_cases() -> list[dict]:
    return json.loads(DATA_PATH.read_text(encoding="utf-8"))["cases"]


def run_spotcheck(classifier: Classifier | None = None) -> dict:
    classifier = classifier or Classifier(LLMProvider.from_env())
    cases = load_cases()
    results = []
    severity_hits = 0
    kind_hits = 0
    node_hits = 0
    for case in cases:
        got = classifier.classify(case["text"], case["source"],
                                  case.get("retrieved_at",
                                           "2026-01-01T00:00:00Z"))
        sev_ok = got["severity"] == case["expected_severity"]
        kind_ok = got["kind"] == case["expected_kind"]
        node_ok = got["node_id"] == case["expected_node_id"]
        severity_hits += sev_ok
        kind_hits += kind_ok
        node_hits += node_ok
        results.append({"id": case["id"], "severity_ok": sev_ok,
                        "kind_ok": kind_ok, "node_ok": node_ok,
                        "expected_severity": case["expected_severity"],
                        "got_severity": got["severity"],
                        "expected_kind": case["expected_kind"],
                        "got_kind": got["kind"],
                        "expected_node_id": case["expected_node_id"],
                        "got_node_id": got["node_id"],
                        "mintable": got["mintable"]})
    n = len(cases)
    return {
        "cases": n,
        "severity_agreement": round(severity_hits / n, 4) if n else None,
        "kind_agreement": round(kind_hits / n, 4) if n else None,
        "node_agreement": round(node_hits / n, 4) if n else None,
        "target_severity_agreement": 0.80,
        "transport": classifier.provider.transport_name,
        "honesty": ("FIXTURE transport: agreement measures the recorded-"
                    "fixture heuristic against the labels, NOT live Spark. "
                    "Re-run unchanged when the Meta key lands for the live "
                    "agreement figure."
                    if classifier.provider.transport_name == "fixture"
                    else "Live transport agreement."),
        "results": results,
    }


def save_summary(report: dict, model: str, path: Path | None = None) -> Path:
    """Persist the agreement figures (no per-case rows) so GET /llm/status
    can serve the LAST figure to the status panel. Written atomically."""
    import os
    from datetime import datetime, timezone
    from .paths import default_spotcheck_path
    target = Path(path or default_spotcheck_path())
    target.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cases": report["cases"],
        "severity_agreement": report["severity_agreement"],
        "kind_agreement": report["kind_agreement"],
        "node_agreement": report["node_agreement"],
        "target_severity_agreement": report["target_severity_agreement"],
        "transport_label": "LIVE" if report["transport"] == "http" else "RECORDED",
        "model": model,
    }
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(doc, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(target)
    return target


def main(argv: list[str] | None = None) -> int:
    import sys
    args = list(sys.argv[1:] if argv is None else argv)
    classifier = Classifier(LLMProvider.from_env())
    report = run_spotcheck(classifier)
    print(json.dumps(report, indent=2))
    if "--save" in args:
        where = save_summary(report, classifier.provider.model)
        print(f"saved summary -> {where}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
