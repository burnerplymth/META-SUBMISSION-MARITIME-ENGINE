"""SupermAP IMPACT layer.

Goods at risk, volume at risk, slowdown, economy exposure and price
CONTEXT on top of the deterministic chain engine. The honesty
rules below are law in this package:

  * market/trade data never enters propagation (this package only READS
    run records, graph data and the snapshot inputs);
  * every figure served is a Figure object {value, unit, class, source,
    as_of, derivation} — no naked numbers (figures.py enforces it);
  * no payload field can carry a headline trade-at-risk number: figures
    stay inside their unit class, are never unit-converted and never
    merged across classes (figures.FORBIDDEN_FIELD_PARTS is enforced by the payload schema guard);
  * PortWatch tonnage always carries the class label
    "SOURCED — PORTWATCH AIS ESTIMATE" (it is a draught-model estimate,
    never a measurement);
  * price effects are context only: dated observed-index citations,
    channel-matched historical analogues, concentration watchlists —
    never a forward claim.
"""
