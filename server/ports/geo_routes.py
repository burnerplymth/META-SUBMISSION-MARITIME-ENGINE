"""Port Twin read-only routes, mounted from ports/routes.py:

  GET /ports/geo/index                    which ports have geometry / imagery / profile
  GET /ports/{place_id}/geo               layers GeoJSON + attribution + bbox + hash
  GET /ports/{place_id}/geo/imagery       underlay metadata (bounds, scene, date)
  GET /ports/{place_id}/geo/imagery.png   the Sentinel-2 true-colour PNG
  GET /ports/{place_id}/profile           port-profile/1 (the simulation contract)

Files come from <data_root>/ports_geo (data_root = the app's data dir:
SUPERMAP_DATA_DIR, else the repository's data/).  Absent data is a 404
NOT SERVED in the house error shape, never an empty 200.  gzip is applied
when the client accepts it (pre-compressed once per file version).
"""
from __future__ import annotations

import gzip
import hashlib
import json
import re
from pathlib import Path

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from . import geo_catalog

REPO_ROOT = Path(__file__).resolve().parents[2]
ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
ATTRIBUTION = "(c) OpenStreetMap contributors"
LICENSE = "ODbL 1.0 - https://opendatacommons.org/licenses/odbl/1-0/"

_CACHE: dict = {}


def _err(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status,
                        content={"error": {"code": code, "message": message[:300]}})


def _geo_dir(app) -> Path:
    """<data_root>/ports_geo. data_root is SUPERMAP_DATA_DIR or the repo's
    data/ by default, so an alternate root never reaches the real files."""
    root = getattr(app.state, "data_root", None)
    return (Path(root) if root is not None else REPO_ROOT / "data") / "ports_geo"


def _read(path: Path):
    """-> (bytes, gz_bytes, sha256) cached by (path, mtime, size) or None."""
    try:
        st = path.stat()
    except OSError:
        return None
    key = (str(path), st.st_mtime_ns, st.st_size)
    hit = _CACHE.get(str(path))
    if hit and hit[0] == key:
        return hit[1]
    raw = path.read_bytes()
    val = (raw, None, hashlib.sha256(raw).hexdigest())
    _CACHE[str(path)] = (key, val)
    return val


def _send(request: Request, body: bytes, media: str, etag: str,
          cache_key: str, extra: dict | None = None) -> Response:
    headers = {"ETag": f'"{etag[:32]}"', "Vary": "Accept-Encoding",
               "Cache-Control": "public, max-age=300", **(extra or {})}
    if request.headers.get("if-none-match") == headers["ETag"]:
        return Response(status_code=304, headers=headers)
    if "gzip" in request.headers.get("accept-encoding", "") and len(body) > 1024 \
            and media != "image/png":
        gz = _CACHE.get("gz:" + cache_key)
        if gz is None or gz[0] != etag:
            gz = (etag, gzip.compress(body, compresslevel=6, mtime=0))
            _CACHE["gz:" + cache_key] = gz
        headers["Content-Encoding"] = "gzip"
        return Response(content=gz[1], media_type=media, headers=headers)
    return Response(content=body, media_type=media, headers=headers)


def register_geo_routes(app) -> None:
    def _paths(place_id: str):
        d = _geo_dir(app)
        return (d / f"{place_id}.geojson", d / "profiles" / f"{place_id}.json",
                d / "imagery" / f"{place_id}.png", d / "imagery" / f"{place_id}.json")

    def _bad(place_id: str):
        if not ID_RE.match(place_id):
            return _err(400, "invalid_request", "bad place_id")
        return None

    @app.get("/ports/geo/index")
    def geo_index(request: Request):
        d = _geo_dir(app)
        man_path = d / "manifest.json"
        man = _read(man_path)
        ports = {}
        if man is not None:
            try:
                ports = json.loads(man[0]).get("ports", {})
            except ValueError:
                ports = {}
        out = []
        for pid in sorted(ports):
            geo_p, prof_p, png_p, imeta_p = _paths(pid)
            if not geo_p.exists():
                continue                      # hide when absent
            m = ports[pid]
            imagery = None
            im = _read(imeta_p)
            if im is not None and png_p.exists():
                try:
                    j = json.loads(im[0])
                    imagery = {"scene_date": j.get("scene_date"),
                               "scene_id": j.get("scene_id"),
                               "bytes": png_p.stat().st_size,
                               "resolution_m": j.get("resolution_m"),
                               "basis": "SOURCED"}
                except ValueError:
                    imagery = None
            out.append({
                "place_id": pid,
                "name": geo_catalog.PORTS.get(pid, {}).get("name", pid),
                "geometry": {"bytes": m.get("bytes"), "sha256": m.get("sha256"),
                             "bbox": m.get("bbox"), "counts": m.get("counts"),
                             "osm_retrieved": m.get("osm_retrieved"),
                             "basis": "SOURCED"},
                "imagery": imagery,
                "profile": ({"bytes": prof_p.stat().st_size,
                             "basis": "MODELLED"} if prof_p.exists() else None),
            })
        body = json.dumps({"schema": "port-geo-index/1",
                           "attribution": ATTRIBUTION, "license": LICENSE,
                           "count": len(out), "ports": out},
                          sort_keys=True, separators=(",", ":")).encode()
        etag = hashlib.sha256(body).hexdigest()
        return _send(request, body, "application/json", etag, "index")

    @app.get("/ports/{place_id}/geo")
    def port_geo(place_id: str, request: Request):
        bad = _bad(place_id)
        if bad:
            return bad
        geo_p = _paths(place_id)[0]
        got = _read(geo_p)
        if got is None:
            return _err(404, "not_served",
                        f"NOT SERVED: no port geometry for {place_id!r}")
        raw, _gz, sha = got
        layers_q = request.query_params.get("layers")
        if layers_q:
            want = {x.strip() for x in layers_q.split(",") if x.strip()}
            col = json.loads(raw)
            # The gzip cache is keyed by the request, so the key must
            # be the CANONICAL selection (the layers this port really has),
            # not the raw query string: "quay,x1", "quay,x2", ... "x" + any
            # junk made one new 100 KB cache entry per request, for ever.
            known = set(col["properties"]["counts"])
            layers_q = ",".join(sorted(want & known))
            want = set(layers_q.split(",")) if layers_q else set()
            col["features"] = [f for f in col["features"]
                               if f["properties"]["layer"] in want]
            col["properties"]["counts"] = {
                k: v for k, v in col["properties"]["counts"].items() if k in want}
            inner = json.dumps(col, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=False).encode()
            filtered = True
        else:
            inner, filtered = raw, False
        head = json.dumps({
            "schema": "port-geo/1", "place_id": place_id, "basis": "SOURCED",
            "geometry_hash": sha, "filtered": filtered,
            "attribution": ATTRIBUTION, "license": LICENSE,
        }, sort_keys=True, separators=(",", ":")).encode()
        # splice the stored FeatureCollection in without re-serialising it
        body = head[:-1] + b',"geojson":' + inner + b"}"
        return _send(request, body, "application/json",
                     # ETag is cut to 32 hex chars: a filtered view needs its
                     # own digest or it would share the whole file's ETag
                     (hashlib.sha256(f"{sha}f{layers_q}".encode()).hexdigest()
                      if filtered else sha),
                     f"geo:{place_id}:{'f' + layers_q if filtered else ''}",
                     {"X-Geometry-Hash": sha})

    @app.get("/ports/{place_id}/profile")
    def port_profile(place_id: str, request: Request):
        bad = _bad(place_id)
        if bad:
            return bad
        got = _read(_paths(place_id)[1])
        if got is None:
            return _err(404, "not_served",
                        f"NOT SERVED: no port profile for {place_id!r}")
        return _send(request, got[0], "application/json", got[2],
                     f"profile:{place_id}")

    @app.get("/ports/{place_id}/geo/imagery")
    def port_imagery(place_id: str, request: Request):
        bad = _bad(place_id)
        if bad:
            return bad
        _g, _p, png_p, meta_p = _paths(place_id)
        got = _read(meta_p)
        if got is None or not png_p.exists():
            return _err(404, "not_served",
                        f"NOT SERVED: no clear Sentinel-2 underlay for {place_id!r}")
        meta = json.loads(got[0])
        meta["image_url"] = f"/ports/{place_id}/geo/imagery.png"
        body = json.dumps(meta, sort_keys=True, separators=(",", ":")).encode()
        return _send(request, body, "application/json", got[2], f"imeta:{place_id}")

    @app.get("/ports/{place_id}/geo/imagery.png")
    def port_imagery_png(place_id: str, request: Request):
        bad = _bad(place_id)
        if bad:
            return bad
        got = _read(_paths(place_id)[2])
        if got is None:
            return _err(404, "not_served",
                        f"NOT SERVED: no clear Sentinel-2 underlay for {place_id!r}")
        return _send(request, got[0], "image/png", got[2], f"png:{place_id}")
