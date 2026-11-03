"""Resolve a journaled extra selection into what a run consumes.

The journal core of an opted-in run carries
    sim:      "extra": {"file_sha256", "view": "sim"}
    engine 2: "extra": {"file_sha256", "view": "engine2", "lane_families": [...],
                        "table_sha256", "merged_graph_sha256"}
and nothing else about the layer (the sidecar is content-addressed by its file
hash). Replaying or building a run re-resolves the selection against the file on
disk and REFUSES when its hash differs: a run is never silently re-derived
against other data. A body without ``extra`` never reaches this module.
"""
from __future__ import annotations

from .loader import Extra, ExtraError, load_extra
from .merge import engine2_view, sim_view

_SIM_MEMO: list = []      # [(key, graph_data object, result)] newest last


def parse_selection(value, view: str) -> tuple[dict | None, str | None]:
    """Normalise a body's ``extra`` value; (None, None) when absent / false.
    Returns (partial selection, error). The file hash and the family names are
    checked against the loaded sidecar by selection_for_run."""
    if value is None or value is False:
        return None, None
    if value is True:
        return {"view": view, "lane_families": None}, None
    if view == "sim":
        return None, "extra must be a boolean on /sim/run"
    if not isinstance(value, dict):
        return None, "extra must be a boolean or an object"
    bad = sorted(set(value) - {"lane_families"})
    if bad:
        return None, f"unknown extra keys: {bad}"
    fam = value.get("lane_families")
    if fam is not None and (not isinstance(fam, list) or any(not isinstance(x, str) for x in fam)):
        return None, "extra.lane_families must be a list of strings"
    return {"view": view, "lane_families": None if fam is None else sorted(set(fam))}, None


def selection_for_run(graph_data: dict, graph_hash: str, value, view: str,
                      path=None) -> tuple[dict | None, Extra | None, str | None]:
    """(journal selection, loaded Extra, error message). (None, None, None)
    when the body does not opt in."""
    sel, err = parse_selection(value, view)
    if err:
        return None, None, err
    if sel is None:
        return None, None, None
    try:
        ex = load_extra(graph_data, graph_hash, path)
    except (OSError, ExtraError) as exc:
        return None, None, f"extra layer unavailable: {exc}"
    out = {"file_sha256": ex.file_sha256, "view": view}
    if view == "engine2":
        fams = sel["lane_families"]
        if fams is None:
            fams = list(ex.families)
        for f in fams:
            if f not in ex.families:
                off = sorted({ln["family"] for ln in ex.disabled})
                why = "is shipped disabled" if f in off else "is not a lane family of this sidecar"
                return None, None, f"extra.lane_families: {f!r} {why} (enabled: {list(ex.families)})"
        out["lane_families"] = sorted(fams)
    return out, ex, None


def resolve(graph_data: dict, graph_hash: str, selection: dict, path=None) -> Extra:
    """The Extra for a journaled selection; raises ExtraError when the sidecar
    changed since the run was journaled."""
    ex = load_extra(graph_data, graph_hash, path)
    if ex.file_sha256 != selection["file_sha256"]:
        raise ExtraError("extra sidecar differs from the one this run was journaled against")
    return ex


def sim_graph(graph_data: dict, graph_hash: str, selection: dict, path=None) -> tuple[dict, dict]:
    """(merged raw graph for the sim, meta). Memoized per (graph object, file
    hash) so the sim's derive cache sees one stable object."""
    key = (id(graph_data), selection["file_sha256"])
    for k, obj, res in _SIM_MEMO:
        if k == key and obj is graph_data:
            return res
    ex = resolve(graph_data, graph_hash, selection, path)
    raw, meta = sim_view(graph_data, ex)
    meta = {"file_sha256": ex.file_sha256, "content_sha256": ex.doc.get("content_sha256"),
            "view": "sim", **meta}
    res = (raw, meta)
    _SIM_MEMO.append((key, graph_data, res))
    del _SIM_MEMO[:-3]
    return res


def engine2_inputs(graph_data: dict, graph_hash: str, selection: dict, path=None) -> tuple[dict, list, dict]:
    """(merged raw graph, lane specs, meta) for a journaled rev-2 selection."""
    ex = resolve(graph_data, graph_hash, selection, path)
    raw, specs, meta = engine2_view(graph_data, ex, selection.get("lane_families"))
    meta = {"file_sha256": ex.file_sha256, "content_sha256": ex.doc.get("content_sha256"),
            "view": "engine2", **meta}
    return raw, specs, meta
