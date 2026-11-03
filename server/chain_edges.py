"""Chain-edge materialisation — the ONE implementation.

ChainEdge[] is derived mechanically from parent_id. The rule
lives here, in a neutral leaf module
that imports nothing from any other SupermAP package, so every
consumer can import it without a cycle:

  * api.app imports the llm router, so the home could NOT be inside
    server/api (llm would have had to import the api package);
  * reports and llm both run with server/ on sys.path as top-level
    packages, and from the repo root as server.* — a leaf module at
    server/chain_edges.py is importable both ways (``chain_edges``
    and ``server.chain_edges``).
"""
from __future__ import annotations


def materialise_edges(events: list[dict], disruption_node: str) -> list[dict]:
    """ChainEdge[] derived mechanically from parent_id."""
    by_seq = {ev["seq"]: ev for ev in events}
    edges = []
    for ev in events:
        parent = ev["parent_id"]
        edges.append({
            "from_seq": parent,
            "to_seq": ev["seq"],
            "from_node": disruption_node if parent is None else by_seq[parent]["node_id"],
            "to_node": ev["node_id"],
            "channel": ev["channel"],
        })
    return edges
