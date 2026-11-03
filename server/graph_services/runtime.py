"""Resolve a journaled services selection into what a run consumes.

The journal core of an opted-in run carries
    "services": {"file_sha256", "eras", "include_unverified", "include_unstated"}
and nothing else about the layer (the sidecar is content-addressed by its
file hash). Replaying or building a run re-resolves the selection against
the sidecar on disk and REFUSES when the file's hash differs: a run is
never silently re-derived against other data.
"""
from __future__ import annotations

from .legs import build_leg_table, od_routes
from .loader import Services, ServicesError, load_services, parse_selection


def selection_for_run(graph_data: dict, graph_hash: str, value, start_at: str,
                      path=None) -> tuple[dict | None, Services | None, str | None]:
    """(journal selection, loaded Services, error message). Used by the
    API at request time; (None, None, None) when the body does not opt in."""
    sel, err = parse_selection(value, start_at)
    if err:
        return None, None, err
    if sel is None:
        return None, None, None
    try:
        svc = load_services(graph_data, graph_hash, path)
    except (OSError, ServicesError) as exc:
        return None, None, f"services layer unavailable: {exc}"
    return {"file_sha256": svc.file_sha256, **sel}, svc, None


def resolve(graph_data: dict, graph_hash: str, selection: dict, path=None) -> tuple[Services, list, list]:
    """(Services, selected entries, excluded) for a journaled selection;
    raises ServicesError when the sidecar changed since the run was journaled."""
    svc = load_services(graph_data, graph_hash, path)
    if svc.file_sha256 != selection["file_sha256"]:
        raise ServicesError("services sidecar differs from the one this run was journaled against")
    sel, out = svc.select(eras=selection["eras"],
                          include_unverified=selection["include_unverified"],
                          include_unstated=selection["include_unstated"])
    return svc, sel, out


def sim_services(graph_data: dict, graph_hash: str, selection: dict, path=None) -> dict:
    """The leg table derive() consumes (+ a ``meta`` block recorded in the
    derivation)."""
    svc, sel, out = resolve(graph_data, graph_hash, selection, path)
    lt = build_leg_table(sel, graph_data)
    lt["meta"] = {
        "file_sha256": svc.file_sha256,
        "content_sha256": svc.doc.get("content_sha256"),
        "eras": list(selection["eras"]),
        "include_unverified": selection["include_unverified"],
        "include_unstated": selection["include_unstated"],
        "services_selected": len(sel),
        "services_excluded": len(out),
        "service_ids": [e["service_id"] for e in sel],
        "dropped_by_validation": len(svc.dropped),
        "stats": lt["stats"],
    }
    return lt


def route_services(graph_data: dict, graph_hash: str, selection: dict, path=None) -> dict:
    """Origin-destination routes for the engine-rev-2 routes table."""
    svc, sel, out = resolve(graph_data, graph_hash, selection, path)
    return {"od": od_routes(sel, graph_data),
            "meta": {"file_sha256": svc.file_sha256, "eras": list(selection["eras"]),
                     "include_unverified": selection["include_unverified"],
                     "include_unstated": selection["include_unstated"],
                     "services_selected": len(sel), "service_ids": [e["service_id"] for e in sel]}}
