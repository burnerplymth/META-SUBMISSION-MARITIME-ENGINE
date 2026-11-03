"""Post-check for chain explanations (the LLM "edge out").

A model sentence about a causal chain may only restate the chain; it may
not introduce a number or a node the chain does not contain. After every
live (or recorded) explanation ``check_narrative`` verifies, against the
step records themselves:

* every number in the text is one the chain supports - a step seq /
  parent seq, an event count, a delay or magnitude (as written to the
  precision the text shows, or as a percentage), or a digit group of a
  step's ``at`` timestamp;
* every id-like token (``chk_suez``, ``sea_singapore``) is a node that
  appears in the chain.

On any miss the model text is DISCARDED and a deterministic template of
the chain is served instead, flagged ``narrative_source: "template"`` so
the UI shows a TEMPLATE chip, never the model chip. The check is
deliberately conservative: a sum or an average the model computed is
not in the step records, so it fails the check; losing a nice sentence
is cheaper than serving a wrong number.
"""
from __future__ import annotations

import re

_NUM_RE = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?")
_ID_RE = re.compile(r"\b(?:sea|air|gnd|chk|rail|road)_[a-z0-9_]+\b")


def _decimals(token: str) -> int:
    return len(token.split(".", 1)[1]) if "." in token else 0


def _allowed_values(events: list[dict]) -> list[float]:
    vals: list[float] = [float(len(events))]
    for ev in events:
        for key in ("seq", "parent_id", "tick"):
            v = ev.get(key)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                vals.append(float(v))
        for key in ("delay_d", "magnitude"):
            v = ev.get(key)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                vals.append(float(v))
                if key == "magnitude":
                    vals.append(float(v) * 100.0)
        at = ev.get("at")
        if isinstance(at, str):
            vals += [float(g) for g in re.findall(r"\d+", at)]
    return vals


def check_narrative(text: str, chain: dict) -> tuple[bool, str | None]:
    """(ok, reason). ``reason`` is a short machine code, never model text."""
    events = chain.get("events") or []
    run_id = str(chain.get("run_id") or "")
    stream_hash = str(chain.get("stream_hash") or "")
    scrubbed = text
    for token in (run_id, stream_hash):
        if token:
            scrubbed = scrubbed.replace(token, " ")
    allowed = _allowed_values(events)
    for tok in _NUM_RE.findall(scrubbed):
        shown = float(tok)
        d = _decimals(tok)
        if not any(round(v, d) == round(shown, d) for v in allowed):
            return False, "number_not_in_chain"
    nodes = {str(ev.get("node_id")) for ev in events}
    for ident in set(_ID_RE.findall(scrubbed)):
        if ident not in nodes:
            return False, "node_not_in_chain"
    return True, None


def render_template(chain: dict) -> str:
    """Deterministic words for a chain, from the step records only."""
    events = chain.get("events") or []
    if not events:
        return f"Run {chain.get('run_id')}: no events in the chain."
    steps = [f"step {ev.get('seq')}: {ev.get('node_id')} via {ev.get('channel')} "
             f"({ev.get('mechanism')}), delay {ev.get('delay_d')} d"
             for ev in events]
    return f"TEMPLATE. Run {chain.get('run_id')}: " + "; ".join(steps) + "."
