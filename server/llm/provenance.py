"""The provenance block every model output carries.

    {"model": "...", "transport": "LIVE"|"RECORDED"|"NONE",
     "transport_raw": "http"|"fixture"|"none", "live": bool,
     "prompt_id": "classifier@v2", "prompt_version": "2",
     "cost_usd": 0.0, "cached": false}

The UI renders a META chip from this block alone: model id, LIVE or
RECORDED, cached. Rules:

* ``LIVE``     - the http transport answered (a real model call, cost_usd
                 is the settled charge).
* ``RECORDED`` - the fixture transport answered: a deterministic
                 recorded-fixture/heuristic response, NOT a live model.
                 ``model`` then reads "fixture-transport (no live model)"
                 and the chip must say RECORDED, never the vendor name.
* ``NONE``     - no model was called (e.g. an out-of-graph refusal, or a
                 validation fallback that never reached a model).

``cached`` is true when the answer came from the explain cache (no call
made, cost 0); a cached answer keeps the transport/model it was
produced with. The block never contains a key or endpoint.
"""
from __future__ import annotations

from typing import Optional

LABELS = {"http": "LIVE", "fixture": "RECORDED"}


def transport_label(transport_raw: Optional[str]) -> str:
    return LABELS.get(transport_raw or "", "NONE")


def make(*, model: Optional[str], transport_raw: Optional[str],
         prompt_name: Optional[str], prompt_version: Optional[str],
         cost_usd: float = 0.0, cached: bool = False,
         estimated_cost_usd: Optional[float] = None) -> dict:
    label = transport_label(transport_raw)
    version = str(prompt_version) if prompt_version not in (None, "") else None
    block = {
        "model": model,
        "transport": label,
        "transport_raw": transport_raw or "none",
        "live": label == "LIVE",
        "prompt_id": (f"{prompt_name}@v{version}" if prompt_name and version
                      else prompt_name),
        "prompt_version": version,
        "cost_usd": float(cost_usd or 0.0),
        "cached": bool(cached),
    }
    if estimated_cost_usd is not None:
        block["estimated_cost_usd"] = float(estimated_cost_usd)
    return block


def from_result(res: dict, prompt_name: str, *, cached: bool = False) -> dict:
    """Provenance from an ``LLMProvider.complete`` result dict."""
    return make(model=res.get("model"), transport_raw=res.get("transport"),
                prompt_name=prompt_name, prompt_version=res.get("prompt_version"),
                cost_usd=res.get("cost_usd", 0.0), cached=cached,
                estimated_cost_usd=res.get("estimated_cost_usd"))


def none(reason: str = "no model call") -> dict:
    block = make(model=None, transport_raw=None, prompt_name=None,
                 prompt_version=None, cost_usd=0.0, cached=False)
    block["note"] = reason
    return block
