"""GET /graph/lanes/geometry: read-only lane polylines (schema lanes.geometry/1), additive.

Selectors (exactly one):
  legs=<id>,<id>,...   up to 400 sea leg ids ('u->v:sea')
  from=<place>&to=<place>   the lane legs on the shortest lane path between two sea places
  scope=sea            every lane pair (the map's legs layer); one row per unordered pair
Other parameters: detail=full|coarse (coarse = world-zoom level of detail), flagged=1 (also return the
polylines of flagged legs, with their flags).

The body is a pure function of the query and of the two content-addressed sidecars named in it, so the
ETag is a hash of both plus the canonical query; If-None-Match gets a 304, gzip is applied when accepted.
Nothing here writes anything.
"""
from __future__ import annotations

import gzip
import hashlib
import json

from fastapi import Request
from fastapi.responses import Response

from .store import KM_PER_NM, LaneGeometryError, SCHEMA as _SIDECAR_SCHEMA, leg_ends, load_lane_geometry, pair_key, split_antimeridian

SCHEMA = "lanes.geometry/1"
MAX_LEGS = 400
_MEMO: dict = {}
_MEMO_MAX = 24


def _etag_matches(header, etag) -> bool:
    if not header:
        return False
    for part in header.split(","):
        t = part.strip()
        if t == "*":
            return True
        if t.startswith("W/"):
            t = t[2:].strip()
        if t == etag:
            return True
    return False


def _accepts_gzip(header) -> bool:
    if not header:
        return False
    star = None
    for part in header.split(","):
        token, _, params = part.strip().partition(";")
        token = token.strip().lower()
        q = 1.0
        for prm in params.split(";"):
            prm = prm.strip()
            if prm.startswith("q="):
                try:
                    q = float(prm[2:])
                except ValueError:
                    q = 0.0
        if token == "gzip":
            return q > 0
        if token == "*":
            star = q
    return bool(star)


def _sea_routes_digest() -> dict:
    try:
        from engine2.sea_routes import load_sea_routes
    except Exception:  # pragma: no cover
        from server.engine2.sea_routes import load_sea_routes  # type: ignore
    return load_sea_routes().digest()


def build_body(lg, graph_nodes, params: dict):
    """(dict, None) or (None, (code, status, message, extra))."""
    detail = params.get("detail") or "full"
    if detail not in ("full", "coarse"):
        return None, ("invalid_request", 422, "detail must be full or coarse", {})
    flagged = params.get("flagged") in ("1", "true", "yes")
    sel = [k for k in ("legs", "from", "scope") if params.get(k)]
    if params.get("to") and not params.get("from"):
        return None, ("invalid_request", 422, "to needs from", {})
    if len(sel) != 1:
        return None, ("invalid_request", 422, "give exactly one of legs, from and to, or scope=sea", {})
    head = {"schema": SCHEMA, "label": "MODELLED", "detail": detail,
            "sea_routes": _sea_routes_digest(), "lane_geometry": lg.digest(),
            "land": {"scale": "1:50,000,000", **{k: lg.doc["land"][k] for k in ("dataset", "file_sha256")}},
            "units": {"coordinates": "lon, lat degrees", "nm": "nautical miles"}}
    if "legs" in sel:
        ids = [x.strip() for x in params["legs"].split(",") if x.strip()]
        if not ids or len(ids) > MAX_LEGS:
            return None, ("invalid_request", 422, f"legs must list 1 to {MAX_LEGS} leg ids", {})
        rows = [lg.leg(k, detail, flagged) for k in ids]
        return {**head, "legs": rows}, None
    if "from" in sel:
        a, b = params["from"], params.get("to")
        if not b:
            return None, ("invalid_request", 422, "from needs to", {})
        for pid in (a, b):
            if graph_nodes is not None and pid not in graph_nodes:
                return None, ("unknown_place", 404, f"unknown place: {pid!r}", {"place_id": pid})
        if a == b:
            return None, ("same_place", 422, "from and to must differ", {"place_id": a})
        got = lg.route(a, b)
        if got is None:
            return None, ("no_lane_path", 404, "no path of lane legs between the two places", {"from": a, "to": b})
        path, nm = got
        rows = [lg.leg(f"{u}->{v}:sea", detail, flagged) for u, v in zip(path, path[1:])]
        merged: list = []
        for u, v in zip(path, path[1:]):
            pts = lg.polyline(u, v, detail)
            merged.extend(pts if not merged else pts[1:])
        return {**head, "from": a, "to": b, "path": path, "nm": round(nm, 1), "km": round(nm * KM_PER_NM, 1),
                "legs": rows, "parts": split_antimeridian(merged)}, None
    if params.get("scope") != "sea":
        return None, ("invalid_request", 422, "scope must be sea", {})
    rows = []
    for k in sorted(lg.pairs):
        r = lg.pairs[k]
        if r["status"] != "lane":
            continue
        pts = lg.polyline(r["a"], r["b"], detail)
        rows.append({"id": k, "a": r["a"], "b": r["b"], "nm": r["lane_nm"], "parts": split_antimeridian(pts)})
    st = lg.stats()
    return {**head, "pairs": rows, "stats": {"pairs_by_status": st["pairs_by_status"],
                                             "legs_with_clean_lane": st["legs_with_clean_lane"],
                                             "legs_with_geometry_in_sea_routes": st["legs_with_geometry_in_sea_routes"]}}, None


def register_lane_geometry_routes(app, ctx: dict) -> None:
    graph = ctx["graph"]
    err = ctx["err"]

    @app.get("/graph/lanes/geometry")
    def lanes_geometry(request: Request):
        params = {k: v for k, v in request.query_params.items()}
        try:
            lg = load_lane_geometry()
        except LaneGeometryError as exc:
            return err("lane_geometry_unreadable", str(exc))
        key = json.dumps(params, sort_keys=True, separators=(",", ":"))
        etag = '"' + hashlib.sha256((lg.content_sha256 + SCHEMA + key).encode()).hexdigest() + '"'
        headers = {"ETag": etag, "Cache-Control": "no-cache", "Vary": "Accept-Encoding"}
        if _etag_matches(request.headers.get("if-none-match"), etag):
            return Response(status_code=304, headers=headers)
        hit = _MEMO.get((lg.content_sha256, key))
        if hit is None:
            nodes = getattr(graph, "nodes", None)
            body, bad = build_body(lg, nodes, params)
            if bad is not None:
                code, status, message, extra = bad
                from fastapi.responses import JSONResponse
                content = {"error": {"code": code, "message": message}, "refusal": {"reason": code, **extra}}
                return JSONResponse(status_code=status, content=content)
            raw = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            hit = (raw, gzip.compress(raw, compresslevel=6, mtime=0))
            if len(_MEMO) >= _MEMO_MAX:
                _MEMO.clear()
            _MEMO[(lg.content_sha256, key)] = hit
        raw, packed = hit
        if _accepts_gzip(request.headers.get("accept-encoding")):
            return Response(content=packed, media_type="application/json",
                            headers={**headers, "Content-Encoding": "gzip"})
        return Response(content=raw, media_type="application/json", headers=headers)
