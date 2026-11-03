"""Report centre + export centre routes.

  GET  /reports/templates          templates, section registry, size estimate
  POST /reports/build              {run_id, template, sections[], format}
  POST /reports/pdf                same body; PDF via headless Chromium
  GET  /exports/datasets           catalogue: rows + HAS DATA state
  GET  /exports/dataset/{id}.csv   one dataset as CSV (units in headers)
  GET  /exports/dataset/{id}.xlsx  one dataset as an XLSX workbook (cover sheet)
  POST /exports/pack               {run_id, datasets[], rows_cap} -> zip
                                   (datasets, figures, workbook, report)
  POST /exports/bundle             {items[]} -> zip of the listed datasets,
                                   figures and reports with manifest.json
  GET  /exports/charts             catalogue of figures with their ChartSpec
  GET  /exports/chart/{id}.svg|.json|.csv|.png   one figure and its data
  POST /exports/chart              ad-hoc ChartSpec / dataset columns

Registered from api.app.create_app via register_report_routes(app).
Errors use the house shape {"error": {"code", "message"}}.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

import source_credits as _credits
from fastapi import Request
from fastapi.responses import JSONResponse, Response

from ports.feeds import iso, parse_ts
from ports.routes import error
from ports.sources import ctx_from_app

from . import bundle, centre, exports, pdf
from .charts import adhoc, png as chartpng, render as chartrender
from .charts import catalogue as chartcat
from .charts.spec import SpecError, normalise
from .charts.theme import THEMES
from .data import ReportError, RunBundle


def _err(exc: ReportError) -> JSONResponse:
    return error(exc.status, exc.code, exc.message)


async def _json_body(request: Request):
    try:
        body = await request.json()
    except Exception:
        raise ReportError("bad_json", "request body must be JSON", 400)
    if not isinstance(body, dict):
        raise ReportError("invalid_request", "body must be a JSON object")
    return body


# A run id ends up in a Content-Disposition header: only a plain token may.
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


def _safe_id(value, name):
    if value is not None and not (isinstance(value, str) and ID_RE.match(value)):
        raise ReportError("invalid_request",
                          f"{name} must be 1-128 characters of letters, digits, _ . : -")
    return value


def _run_id(body, required=True):
    rid = body.get("run_id")
    if rid is None and not required:
        return None
    if not isinstance(rid, str) or not rid:
        raise ReportError("invalid_request", "run_id (string) is required")
    return _safe_id(rid, "run_id")


def _opt_str(body, key):
    v = body.get(key)
    if v is not None and not isinstance(v, str):
        raise ReportError("invalid_request", f"{key} must be a string")
    return v or None


def _opt_dt(raw, name):
    if raw is None or raw == "":
        return None
    ts = parse_ts(raw)
    if ts is None:
        raise ReportError("invalid_request",
                          f"{name} must be an ISO-8601 date or timestamp")
    return ts


def _csv_list(raw):
    if raw is None:
        return None
    vals = [v.strip() for v in raw.split(",") if v.strip()]
    return tuple(vals) or None


def _params_from_query(q) -> exports.Params:
    board = {}
    for k in ("mode", "kind", "tier", "region", "status"):
        v = _csv_list(q.get(k))
        if v:
            board[k] = list(v)
    return exports.Params(
        from_=_opt_dt(q.get("from"), "from"), to=_opt_dt(q.get("to"), "to"),
        channel=_csv_list(q.get("channel")), place=_csv_list(q.get("place")),
        board=board or None)


def _int_arg(raw, name, default, lo, hi):
    if raw is None:
        return default
    try:
        v = int(raw)
    except (TypeError, ValueError):
        raise ReportError("invalid_request", f"{name} must be an integer")
    if isinstance(raw, str) and str(v) != raw.strip():
        raise ReportError("invalid_request", f"{name} must be an integer")
    if not lo <= v <= hi:
        raise ReportError("invalid_request",
                          f"{name} must be in {lo}..{hi}")
    return v


def _make_env(app, q) -> exports.Env:
    """Env from a query mapping or a body dict (run_id, sim_run_id, tick,
    as_of); 404 early on an unknown run."""
    tick = q.get("tick")
    if tick is not None:
        tick = _int_arg(tick, "tick", None, 0, 10 ** 9)
    ctx = ctx_from_app(app)
    as_of = _opt_dt(q.get("as_of"), "as_of")
    if as_of is not None:
        from ports.sources import Ctx
        ctx = Ctx(**{**ctx.__dict__, "now_fn": (lambda: as_of)})
    env = exports.Env(ctx, q.get("run_id") or None,
                      q.get("sim_run_id") or None, tick)
    if env.run_id:
        env.bundle  # 404 early on an unknown run
    return env


def _chart_error(exc) -> JSONResponse:
    return error(exc.status, exc.code, exc.message)


def _chart_response(spec, ext, theme, scale, cid):
    """spec (normalised) -> Response in svg / json / csv / png."""
    chip = spec["chip"]
    if ext == "json":
        body = {"schema": "chart/1", "chart_id": cid, "spec": spec,
                "caption": chartrender.caption(spec),
                "files": {k: f"/exports/chart/{cid}.{k}"
                          for k in ("svg", "csv", "png")}}
        data = (json.dumps(body, sort_keys=True, indent=1,
                           ensure_ascii=False) + "\n").encode("utf-8")
        mt = "application/json"
    elif ext == "csv":
        data, mt = chartrender.chart_csv(spec), "text/csv; charset=utf-8"
    else:
        svg = chartrender.render_svg(spec, theme=theme)
        if ext == "svg":
            data, mt = svg.encode("utf-8"), "image/svg+xml; charset=utf-8"
        else:
            ok, why = chartpng.available()
            if not ok:
                return error(501, "renderer_unavailable",
                             f"no PNG renderer on this server ({why}); the "
                             f"figure is served as .svg")
            try:
                data = chartpng.render_pngs({cid: svg}, scale)[cid]
            except chartpng.PngError as exc:
                if exc.busy:
                    return error(503, "renderer_busy", str(exc))
                return error(502, "png_failed", str(exc))
            mt = "image/png"
    return Response(
        content=data, media_type=mt,
        headers={"Content-Disposition": f'inline; filename="{cid}.{ext}"',
                 "X-Provenance": chip,
                 "X-Source": _credits.header_safe(
                     chartrender.source_line(spec)),
                 "X-Chart-Sha256": hashlib.sha256(data).hexdigest()})


def _theme_scale(q):
    theme = q.get("theme") or "light"
    if theme not in THEMES:
        raise ReportError("invalid_request",
                          f"theme must be one of {list(THEMES)}")
    scale = _int_arg(q.get("scale"), "scale", 2, 1, 3)
    return theme, scale


def _bool_arg(body, key, default):
    v = body.get(key, default)
    if not isinstance(v, bool):
        raise ReportError("invalid_request", f"{key} must be true or false")
    return v


def register_report_routes(app) -> None:
    @app.get("/reports/templates")
    def reports_templates(request: Request):
        try:
            ctx = ctx_from_app(app)
            rid = request.query_params.get("run_id")
            bundle = RunBundle(ctx, rid) if rid else None
            body = centre.templates_payload(bundle)
            ok, why = pdf.available()
            body["pdf"] = {
                "available": ok, "renderer": why if ok else None,
                "reason": None if ok else why,
                "print_view": "format pdf-print-view always available "
                              "(print stylesheet; Save as PDF from any "
                              "browser)"}
            body["run_id"] = rid
            return body
        except ReportError as exc:
            return _err(exc)

    @app.post("/reports/build")
    async def reports_build(request: Request):
        try:
            body = await _json_body(request)
            ctx = ctx_from_app(app)
            bundle = RunBundle(ctx, _run_id(body))
            rep = centre.build_report(
                bundle, body.get("template", "full"), body.get("sections"),
                body.get("format", "html"))
        except ReportError as exc:
            return _err(exc)
        if body.get("as") == "raw":
            return Response(
                content=rep.content, media_type=rep.media_type,
                headers={"X-Report-Sha256": rep.meta["sha256"],
                         "X-Report-Bytes": str(rep.meta["bytes"]),
                         "X-Report-Sections": ",".join(rep.meta["sections"]),
                         "Content-Disposition":
                         f'inline; filename="{rep.filename}"'})
        return {"schema": "reports/1",
                "generated_at": iso(datetime.now(timezone.utc)),
                "meta": rep.meta, "filename": rep.filename,
                "media_type": rep.media_type,
                "content": rep.content.decode("utf-8")}

    @app.post("/reports/pdf")
    async def reports_pdf(request: Request):
        try:
            body = await _json_body(request)
            ctx = ctx_from_app(app)
            bundle = RunBundle(ctx, _run_id(body))
            rep = centre.build_report(
                bundle, body.get("template", "full"), body.get("sections"),
                "pdf-print-view")
        except ReportError as exc:
            return _err(exc)
        ok, why = pdf.available()
        if not ok:
            return error(501, "renderer_unavailable",
                         f"no PDF renderer on this server ({why}); use "
                         f"format 'pdf-print-view' and print to PDF from a "
                         f"browser")
        try:
            data = pdf.render_pdf(rep.content)
        except pdf.PdfError as exc:
            if exc.busy:
                return error(503, "renderer_busy", str(exc))
            return error(502, "pdf_failed", str(exc))
        return Response(
            content=data, media_type="application/pdf",
            headers={"Content-Disposition":
                     f'attachment; filename="{bundle.run_id}-'
                     f'{body.get("template", "full")}.pdf"',
                     "X-Report-Source-Sha256": rep.meta["sha256"]})

    @app.get("/exports/datasets")
    def exports_datasets(request: Request):
        try:
            q = request.query_params
            tick = _int_arg(q.get("tick"), "tick", None, 0, 10 ** 9) \
                if q.get("tick") is not None else None
            ctx = ctx_from_app(app)
            as_of = _opt_dt(q.get("as_of"), "as_of")
            if as_of is not None:
                from ports.sources import Ctx
                ctx = Ctx(**{**ctx.__dict__, "now_fn": (lambda: as_of)})
            return exports.catalogue(ctx, q.get("run_id") or None,
                                     q.get("sim_run_id") or None, tick)
        except ReportError as exc:
            return _err(exc)

    @app.get("/exports/dataset/{name}")
    def exports_dataset(name: str, request: Request):
        try:
            ext = name.rpartition(".")[2]
            if ext not in ("csv", "xlsx"):
                raise ReportError(
                    "not_found", "datasets are served as <id>.csv or "
                    "<id>.xlsx", 404)
            did = name[:-(len(ext) + 1)]
            if did not in exports.DATASETS:
                raise ReportError("dataset_not_found",
                                  f"unknown dataset {did!r}", 404)
            q = request.query_params
            env = _make_env(app, q)
            cap = _int_arg(q.get("rows_cap"), "rows_cap",
                           exports.DEFAULT_ROWS_CAP, 1, exports.MAX_ROWS_CAP)
            params = _params_from_query(q)
            if ext == "xlsx":
                as_of_dt = _opt_dt(q.get("as_of"), "as_of") or \
                    env.ctx.now.replace(second=0, microsecond=0)
                wb, written, avail = exports.dataset_workbook(
                    env, did, params, cap, as_of_dt)
            else:
                rows, avail = exports.collect(env, did, params, cap)
                written = len(rows)
        except ReportError as exc:
            return _err(exc)
        spec = exports.DATASETS[did]
        if ext == "xlsx":
            data, mt = wb, ("application/vnd.openxmlformats-officedocument."
                            "spreadsheetml.sheet")
        else:
            data = exports.to_csv_bytes(spec["columns"], rows)
            mt = "text/csv; charset=utf-8"
        return Response(
            content=data, media_type=mt,
            headers={"Content-Disposition":
                     f'attachment; filename="{did}.{ext}"',
                     "X-Rows-Written": str(written),
                     "X-Rows-Available": str(avail),
                     "X-Rows-Cut": str(avail - written),
                     "X-Provenance": spec["provenance"],
                     "X-Source": _credits.header_safe(_credits.source_line(
                         spec.get("sources"), spec["provenance"]))})

    @app.post("/exports/pack")
    async def exports_pack(request: Request):
        try:
            body = await _json_body(request)
            ctx = ctx_from_app(app)
            rid = _run_id(body, required=False)
            sid = _opt_str(body, "sim_run_id")
            _safe_id(sid, "sim_run_id")
            if rid is None and sid is None and not body.get("datasets"):
                raise ReportError(
                    "invalid_request",
                    "give run_id and/or sim_run_id, or name datasets that "
                    "need neither")
            if rid:
                RunBundle(ctx, rid)  # 404 on unknown run
            tick = body.get("tick")
            if tick is not None:
                tick = _int_arg(tick, "tick", None, 0, 10 ** 9)
            filt = body.get("filters")
            if filt is None:
                filt = {}
            if not isinstance(filt, dict):
                raise ReportError("invalid_request",
                                  "filters must be an object")
            params = _params_from_query({k: (",".join(v)
                                             if isinstance(v, list) else v)
                                         for k, v in filt.items()})
            data, manifest = exports.build_pack(
                ctx, run_id=rid, sim_run_id=sid, tick=tick,
                datasets=body.get("datasets"),
                rows_cap=body.get("rows_cap", exports.DEFAULT_ROWS_CAP),
                as_of=_opt_dt(body.get("as_of"), "as_of"),
                include_report=body.get("include_report", True) is not False,
                template=body.get("template", "full"),
                sections=body.get("sections"), params=params,
                include_charts=_bool_arg(body, "include_charts", True),
                charts=body.get("charts"),
                include_xlsx=_bool_arg(body, "include_xlsx", True),
                include_png=_bool_arg(body, "include_png", False))
        except ReportError as exc:
            return _err(exc)
        return Response(
            content=data, media_type="application/zip",
            headers={"Content-Disposition":
                     f'attachment; filename="supermap-pack-{rid or sid}.zip"',
                     "X-Pack-Files": str(len(manifest["files"]) + 1),
                     "X-Pack-Generated-At": manifest["generated_at"],
                     "X-Pack-As-Of": manifest["as_of"]})

    # ------------------------------------------------------------ bundle
    @app.post("/exports/bundle")
    async def exports_bundle(request: Request):
        try:
            body = bundle.check_body(await _json_body(request))
            env = _make_env(app, body)
            rid = env.run_id
            cap = body.get("rows_cap", exports.DEFAULT_ROWS_CAP)
            if not isinstance(cap, int) or isinstance(cap, bool) \
                    or not 1 <= cap <= exports.MAX_ROWS_CAP:
                raise ReportError(
                    "invalid_request",
                    f"rows_cap must be an integer in 1..{exports.MAX_ROWS_CAP}")
            theme = body.get("theme") or "light"
            as_of_dt = _opt_dt(body.get("as_of"), "as_of") or \
                env.ctx.now.replace(second=0, microsecond=0)
            tick = body.get("tick")
            if tick is not None:
                _int_arg(tick, "tick", None, 0, 10 ** 9)
            items = body["items"]
            names = bundle.member_names(items, rid)
        except ReportError as exc:
            return _err(exc)

        files: dict[str, bytes] = {}
        meta: list[dict] = []
        errors: list[dict] = []
        pngs: dict[str, tuple[str, str, int]] = {}  # name -> (svg, id, scale)
        total = 0

        def fail(it, name, code, message):
            errors.append({"kind": it["kind"], "id": it.get("id")
                           or it.get("template", "full"),
                           "format": it["format"], "code": code,
                           "message": message})

        def keep(it, name, data, chip, source):
            nonlocal total
            total += len(data)
            if total > bundle.MAX_BYTES:
                raise ReportError("too_large", "the bundle would exceed "
                                  f"{bundle.MAX_BYTES // (1024 * 1024)} MB",
                                  413)
            files[name] = data
            meta.append({"kind": it["kind"], "id": it.get("id")
                         or it.get("template", "full"), "format":
                         it["format"], "file": name, "provenance": chip,
                         "source": source})

        try:
            for it, name in zip(items, names):
                try:
                    if it["kind"] == "dataset":
                        did = it["id"]
                        if did not in exports.DATASETS:
                            fail(it, name, "dataset_not_found",
                                 f"unknown dataset {did!r}")
                            continue
                        dspec = exports.DATASETS[did]
                        params = _params_from_query({
                            k: (",".join(v) if isinstance(v, list) else v)
                            for k, v in (it.get("filters") or {}).items()})
                        if it["format"] == "xlsx":
                            data, _w, _a = exports.dataset_workbook(
                                env, did, params, cap, as_of_dt)
                        else:
                            rows, _a = exports.collect(env, did, params, cap)
                            data = exports.to_csv_bytes(dspec["columns"],
                                                        rows)
                        keep(it, name, data, dspec["provenance"],
                             f"export dataset {did}")
                    elif it["kind"] == "figure":
                        cid = it["id"]
                        fspec = chartcat.build_spec(env, cid)
                        if not any(s["points"] for s in fspec["series"]):
                            fail(it, name, "no_data",
                                 f"figure {cid!r} has no rows to draw")
                            continue
                        th = it.get("theme") or theme
                        if th not in THEMES:
                            fail(it, name, "invalid_request",
                                 f"theme must be one of {list(THEMES)}")
                            continue
                        fmt = it["format"]
                        if fmt == "csv":
                            data = chartrender.chart_csv(fspec)
                        elif fmt == "json":
                            data = (json.dumps(
                                {"schema": "chart/1", "chart_id": cid,
                                 "spec": fspec,
                                 "caption": chartrender.caption(fspec)},
                                sort_keys=True, indent=1,
                                ensure_ascii=False) + "\n").encode("utf-8")
                        else:
                            svg = chartrender.render_svg(fspec, theme=th)
                            if fmt == "svg":
                                data = svg.encode("utf-8")
                            else:
                                sc = _int_arg(it.get("scale"), "scale",
                                              2, 1, 3)
                                pngs[name] = (svg, cid, sc)
                                meta.append({"kind": "figure", "id": cid,
                                             "format": "png", "file": name,
                                             "provenance": fspec["chip"],
                                             "source": "export figure"})
                                continue
                        keep(it, name, data, fspec["chip"],
                             f"export figure {cid}")
                    else:
                        if not rid:
                            fail(it, name, "invalid_request",
                                 "a report needs run_id")
                            continue
                        tpl = it.get("template", "full")
                        if it["format"] == "pdf":
                            rep = centre.build_report(
                                RunBundle(ctx_from_app(app), rid), tpl,
                                it.get("sections"), "pdf-print-view")
                            ok, why = pdf.available()
                            if not ok:
                                fail(it, name, "renderer_unavailable",
                                     "no PDF renderer on this server")
                                continue
                            try:
                                data = pdf.render_pdf(rep.content)
                            except pdf.PdfError as exc:
                                fail(it, name, "pdf_failed", str(exc))
                                continue
                        else:
                            rep = centre.build_report(
                                RunBundle(ctx_from_app(app), rid), tpl,
                                it.get("sections"), it["format"])
                            data = rep.content
                        keep(it, name, data, "MODELLED",
                             f"report {tpl}")
                except ReportError as exc:
                    if exc.code == "too_large":
                        raise
                    fail(it, name, exc.code, exc.message)
                except chartcat.ChartError as exc:
                    fail(it, name, exc.code, exc.message)
            if pngs:
                ok, why = chartpng.available()
                if not ok:
                    for name in pngs:
                        meta[:] = [m for m in meta if m["file"] != name]
                        errors.append({"kind": "figure", "id": pngs[name][1],
                                       "format": "png",
                                       "code": "renderer_unavailable",
                                       "message": "no PNG renderer on this "
                                                  "server"})
                else:
                    by_scale: dict[int, dict[str, str]] = {}
                    for name, (svg, cid, sc) in pngs.items():
                        by_scale.setdefault(sc, {})[name] = svg
                    try:
                        for sc, group in by_scale.items():
                            for name, data in chartpng.render_pngs(
                                    group, sc).items():
                                total += len(data)
                                if total > bundle.MAX_BYTES:
                                    raise ReportError(
                                        "too_large", "the bundle would "
                                        "exceed the size limit", 413)
                                files[name] = data
                    except chartpng.PngError as exc:
                        for name in pngs:
                            if name not in files:
                                meta[:] = [m for m in meta
                                           if m["file"] != name]
                                errors.append({"kind": "figure",
                                               "id": pngs[name][1],
                                               "format": "png",
                                               "code": "png_failed",
                                               "message": str(exc)})
        except ReportError as exc:
            return _err(exc)

        if not files:
            first = errors[0] if errors else {"code": "invalid_request",
                                              "message": "nothing was built"}
            return error(422, first["code"], first["message"])
        generated = iso(datetime.now(timezone.utc))
        context = {"run_id": rid, "sim_run_id": env.sim_run_id,
                   "as_of": iso(as_of_dt), "rows_cap": cap}
        manifest = bundle.build_manifest(files, meta, errors, context,
                                         generated)
        data = bundle.zip_with_manifest(files, manifest)
        return Response(
            content=data, media_type="application/zip",
            headers={"Content-Disposition":
                     f'attachment; filename="supermap-bundle-'
                     f'{rid or env.sim_run_id or "export"}.zip"',
                     "X-Bundle-Files": str(len(files) + 1),
                     "X-Bundle-Errors": str(len(errors)),
                     "X-Bundle-Generated-At": generated})

    # ------------------------------------------------------------ figures
    @app.get("/exports/charts")
    def exports_charts(request: Request):
        try:
            q = request.query_params
            env = _make_env(app, q)
            ds = q.get("dataset") or None
            if ds is not None and ds not in exports.DATASETS:
                raise ReportError("dataset_not_found",
                                  f"unknown dataset {ds!r}", 404)
            rows = chartcat.list_charts(
                env, with_spec=q.get("spec") != "false", dataset=ds)
        except ReportError as exc:
            return _err(exc)
        ok, why = chartpng.available()
        return {"schema": "charts/1", "run_id": env.run_id,
                "sim_run_id": env.sim_run_id, "dataset": ds,
                "themes": list(THEMES), "png": {"available": ok,
                                                "reason": None if ok else why},
                "limits": {"max_series": 12, "max_points_total": 10000,
                           "max_points_per_series": 2000,
                           "max_rows_drawn": 400},
                "counter": f"{sum(1 for r in rows if r['state'] == 'HAS DATA')}"
                           f" of {len(rows)} figures can be drawn",
                "charts": rows}

    @app.get("/exports/chart/{name}")
    def exports_chart(name: str, request: Request):
        try:
            cid, dot, ext = name.rpartition(".")
            if not dot or ext not in ("svg", "json", "csv", "png"):
                raise ReportError(
                    "not_found", "figures are served as <id>.svg, .json, "
                    ".csv or .png", 404)
            q = request.query_params
            env = _make_env(app, q)
            theme, scale = _theme_scale(q)
            spec = chartcat.build_spec(env, cid)
            if not any(s["points"] for s in spec["series"]):
                raise ReportError("no_data", f"figure {cid!r} has no rows "
                                  f"to draw", 404)
        except ReportError as exc:
            return _err(exc)
        except chartcat.ChartError as exc:
            return _chart_error(exc)
        return _chart_response(spec, ext, theme, scale, cid)

    @app.post("/exports/chart")
    async def exports_chart_post(request: Request):
        try:
            body = await _json_body(request)
            adhoc.check_body(body)
            fmt = body.get("format", "svg")
            if fmt not in ("svg", "png", "json", "csv"):
                raise ReportError("invalid_request",
                                  "format must be svg, png, json or csv")
            theme, scale = _theme_scale(body)
            if "spec" in body:
                raw = adhoc.spec_from_user_spec(body["spec"])
                cid = "adhoc"
            else:
                did = body["dataset"]
                if not isinstance(did, str) or did not in exports.DATASETS:
                    raise ReportError("dataset_not_found",
                                      f"unknown dataset {did!r}", 404)
                env = _make_env(app, body)
                spec_ds = exports.DATASETS[did]
                gate = exports.needs_state(spec_ds, env)
                if gate:
                    raise ReportError(
                        "invalid_request", f"dataset {did!r} needs "
                        f"{'run_id' if gate == 'NEEDS RUN' else 'sim_run_id'}")
                filt = body.get("filters") or {}
                if not isinstance(filt, dict):
                    raise ReportError("invalid_request",
                                      "filters must be an object")
                params = _params_from_query({
                    k: (",".join(v) if isinstance(v, list) else v)
                    for k, v in filt.items()})
                cap = adhoc.rows_cap_of(body)
                rows, avail = exports.collect(env, did, params, cap)
                raw = adhoc.spec_from_dataset(
                    did, spec_ds, spec_ds["columns"], rows, avail, body,
                    env.run_id)
                cid = f"{did}_{body.get('type')}"
            spec = normalise(raw)
        except ReportError as exc:
            return _err(exc)
        except SpecError as exc:
            return error(422, "invalid_request", str(exc))
        return _chart_response(spec, fmt, theme, scale, cid)
