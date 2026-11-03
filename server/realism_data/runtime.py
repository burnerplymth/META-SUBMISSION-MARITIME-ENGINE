"""Journaled selection of the inventory-days sidecar, for a future engine hook to call.

NOTHING IN THIS REPOSITORY CALLS THIS MODULE YET. The option ``inventory_days_source``
is not accepted by /run or /sim/run: wiring it would
have to change what a run computes (or at least what its journal records), which
server/engine*, engine2 and sim must not do. This module
is the contract such a hook would use, mirroring graph_extra/runtime.py:

    sel, loaded, err = selection_for_run(graph_raw, graph_hash, body.get("inventory_days_source"), "inventory_days")
    # journal core gains   "inventory_days_source": sel        ONLY when sel is not None
    loaded = resolve(graph_raw, graph_hash, "inventory_days", journaled_sel)   # on replay; refuses a changed file

``sel`` is ``{"kind", "file_sha256", "content_sha256", "rows"}``. A body without the
option never reaches this module and its journal gains no key, so ids and bytes of
every old body stay as they are.
"""
from __future__ import annotations

from .common import SidecarError
from .inventory_days import load_inventory_days

KINDS = {"inventory_days": ("inventory_days_source", load_inventory_days)}


def journal_key(kind: str) -> str:
    return KINDS[kind][0]


def parse_selection(value) -> tuple[bool, str | None]:
    """(opted_in, error). None / False = absent; True = opt in; anything else is refused."""
    if value is None or value is False:
        return False, None
    if value is True:
        return True, None
    return False, "must be a boolean (true opts in; absent or false leaves the run untouched)"


def stamp(kind: str, loaded) -> dict:
    return {"kind": kind, "file_sha256": loaded.file_sha256,
            "content_sha256": loaded.doc.get("content_sha256"), "rows": len(loaded.rows)}


def selection_for_run(graph_data: dict, graph_hash: str, value, kind: str, path=None):
    """(journal selection, loaded sidecar, error message). (None, None, None) when the body does not opt in."""
    on, err = parse_selection(value)
    if err:
        return None, None, f"{journal_key(kind)} {err}"
    if not on:
        return None, None, None
    try:
        loaded = KINDS[kind][1](graph_data, graph_hash, path)
    except (OSError, SidecarError) as exc:
        return None, None, f"{kind} sidecar unavailable: {exc}"
    return stamp(kind, loaded), loaded, None


def resolve(graph_data: dict, graph_hash: str, kind: str, selection: dict, path=None):
    """The sidecar for a journaled selection; raises SidecarError when the file changed since the run."""
    loaded = KINDS[kind][1](graph_data, graph_hash, path)
    if loaded.file_sha256 != selection["file_sha256"]:
        raise SidecarError(f"{kind} sidecar differs from the one this run was journaled against")
    return loaded
