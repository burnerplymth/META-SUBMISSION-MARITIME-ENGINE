"""The sim's view of the lane geometry sidecar (opt-in ``lane_geometry`` on POST /sim/run).

``selection_for_run`` builds the journal entry (the content hashes of the two sidecars the run was derived
on); ``provider_for_journal`` loads the sidecars again and refuses a run whose journaled hashes no longer
match what is on disk, so replay, verify and fork rebuild on the same geometry or not at all.
"""
from __future__ import annotations

from .store import LaneGeometryError, load_lane_geometry


def _wrap(lon: float) -> float:
    x = ((lon + 180.0) % 360.0) - 180.0
    return 180.0 if x == -180.0 and lon > 0 else x


def selection_for_run() -> dict:
    """The journal entry of a run that opts in; raises LaneGeometryError when the sidecars are unusable."""
    lg = load_lane_geometry()
    try:
        from engine2.sea_routes import load_sea_routes
    except Exception:  # pragma: no cover - imported as server.* from the repo root
        from server.engine2.sea_routes import load_sea_routes  # type: ignore
    sea = load_sea_routes()
    return {"sea_routes_content_sha256": sea.content_sha256,
            "lane_geometry_content_sha256": lg.content_sha256}


class SimLaneGeometry:
    """Lane polylines for derive(): sea legs only, as (lat, lon) points with longitudes in (-180, 180]."""

    def __init__(self, lg, selection: dict):
        self.lg = lg
        self.selection = dict(selection)

    def sea_points(self, u: str, v: str):
        """[(lat, lon), ...] from u to v along the lane, or None when the pair has no clean lane."""
        pts = self.lg.polyline(u, v, "full")
        if pts is None or len(pts) < 2:
            return None
        return [(lat, _wrap(lon)) for lon, lat in pts]

    def lane_nm(self, u: str, v: str):
        r = self.lg.record(u, v)
        return None if r is None else r["lane_nm"]

    def digest(self) -> dict:
        return dict(self.selection)


def provider_for_journal(selection: dict) -> SimLaneGeometry:
    lg = load_lane_geometry()
    now = selection_for_run()
    if now != {k: selection.get(k) for k in now}:
        raise LaneGeometryError("the run's journaled lane geometry hashes differ from the sidecars on disk")
    return SimLaneGeometry(lg, selection)
