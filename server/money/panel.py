"""Money panel data — pure functions, quarantined by construction.

Shape per node:
  {node_id,
   exposed: [{ticker, company, exposure_kind, filing_url,
              last_price, price_date, prev_close, price_change_pct,
              sparkline, price_provenance}],
   simulated_cost_pressure: {value, label: "SIMULATED", rule, provenance}
                            | omitted}

Prices are real adapter output (Stooq) with {source, retrieved_at, mode}
provenance; an exposed company with no quote is listed with null price
fields — absence beats fabrication.

MODELLED IMPACT — the only figure this layer emits, and why:
  Rule: when a run's event stream contains cost-channel events at a node,
  simulated_cost_pressure.value = the maximum `magnitude` among those
  events. The cost channel's magnitude is a
  *normalised expedite-pressure indicator, never currency* — so the
  panel re-publishes that indicator, labelled SIMULATED, with the engine
  run as its provenance. NO equity-price impact percentage is computed:
  no defensible mechanical rule maps a normalised pressure indicator to
  a share-price move, so that figure is omitted everywhere.

This module imports nothing from the engine; run events arrive as plain
Event dicts (read-only consumption via stored-run shapes, as the contract allows).
"""
from __future__ import annotations

SIMULATED_LABEL = "SIMULATED"
COST_PRESSURE_RULE = (
    "max magnitude among the run's cost-channel events at this node "
    "(normalised expedite-pressure indicator, never "
    "currency, never a price forecast)")


def simulated_cost_pressure(events: list[dict], node_id: str,
                            run_id: str | None) -> dict | None:
    mags = [ev["magnitude"] for ev in events
            if ev.get("node_id") == node_id and ev.get("channel") == "cost"
            and isinstance(ev.get("magnitude"), (int, float))]
    if not mags:
        return None
    return {
        "value": max(mags),
        "label": SIMULATED_LABEL,
        "rule": COST_PRESSURE_RULE,
        "provenance": {"source": f"SupermAP engine run {run_id}",
                       "mode": "MODELED"},
    }


def build_money_panel(node_ids: list[str], exposures_doc: dict,
                      quotes_by_ticker: dict[str, dict | None],
                      run_events: list[dict] | None = None,
                      run_id: str | None = None) -> dict:
    """Assemble the per-node money panel. Pure: same inputs, same output.

    quotes_by_ticker: ticker -> stooq.latest_quote() output (or None when
    no quote could be fetched — the company is still listed, unpriced).
    """
    by_node: dict[str, list[dict]] = {}
    for m in exposures_doc.get("mappings", []):
        by_node.setdefault(m["node_id"], []).append(m)
    panel: dict[str, dict] = {}
    for node_id in node_ids:
        exposed = []
        for m in by_node.get(node_id, []):
            q = quotes_by_ticker.get(m["ticker"])
            exposed.append({
                "ticker": m["ticker"],
                "company": m["company"],
                "exposure_kind": m["exposure_kind"],
                "filing_url": m["filing"]["url"],
                "last_price": q["last_price"] if q else None,
                "price_date": q["price_date"] if q else None,
                "prev_close": q["prev_close"] if q else None,
                "price_change_pct": q["price_change_pct"] if q else None,
                "sparkline": q["sparkline"] if q else [],
                "price_provenance": q["provenance"] if q else None,
            })
        entry: dict = {"node_id": node_id, "exposed": exposed}
        if run_events is not None:
            pressure = simulated_cost_pressure(run_events, node_id, run_id)
            if pressure is not None:
                entry["simulated_cost_pressure"] = pressure
        panel[node_id] = entry
    return panel
