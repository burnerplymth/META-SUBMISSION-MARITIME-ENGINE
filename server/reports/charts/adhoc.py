"""Ad-hoc charts: POST /exports/chart.

Two closed body shapes:
  {"spec": <ChartSpec>}                      draw a spec the caller supplies
                                             (data and provenance are the
                                             caller's assertion; the figure
                                             says so in its note)
  {"dataset": id, "type", "x", "y", ...}     draw columns of a SERVED dataset

Dataset charts never aggregate: if two rows would land on the same
(series, category) the request is refused rather than summed, and a value
column whose unit varies per row ("see unit_class") must be narrowed to one
class with `where` first (no cross-class figure, ever)."""
from __future__ import annotations

from .fmt import parse_iso
from .spec import CHIPS, MAX_POINTS_TOTAL, MAX_SERIES, SpecError, is_num

DATASET_TYPES = ("line", "scatter", "bar", "stacked_bar",
                 "small_multiples", "sparkline")
BODY_KEYS = {"spec", "dataset", "run_id", "sim_run_id", "tick", "as_of",
             "filters", "type", "x", "y", "series_by", "label", "title",
             "subtitle", "rows_cap", "options", "where", "stack_class",
             "format", "theme", "scale"}
DEFAULT_ROWS_CAP = 2000
MAX_ROWS_CAP = 5000
ADHOC_NOTE = ("Ad-hoc figure: the data and provenance chip are asserted by "
              "the caller, not served by this product.")


def check_body(body):
    if not isinstance(body, dict):
        raise SpecError("body must be a JSON object")
    extra = sorted(set(body) - BODY_KEYS)
    if extra:
        raise SpecError(f"body: unknown key {extra[0]!r}; allowed: "
                        f"{sorted(BODY_KEYS)}")
    if ("spec" in body) == ("dataset" in body):
        raise SpecError("give exactly one of 'spec' (a ChartSpec) or "
                        "'dataset' (a served dataset id)")


def spec_from_user_spec(spec):
    if not isinstance(spec, dict):
        raise SpecError("spec must be an object")
    out = dict(spec)
    note = out.get("note")
    out["note"] = (f"{note} " if isinstance(note, str) and note else "") \
        + ADHOC_NOTE
    return out


def _col_index(columns, name, what):
    names = [n for n, _ in columns]
    if not isinstance(name, str) or name not in names:
        raise SpecError(f"{what} must be a column of the dataset: {names}")
    return names.index(name)


def _coerce_y(v):
    if v == "" or v is None:
        return None
    if is_num(v):
        return v
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise SpecError(f"value {v!r} in the y column is not a number")
    if f != f or f in (float("inf"), float("-inf")):
        raise SpecError("the y column holds a non-finite number")
    return f


def spec_from_dataset(did, spec_ds, columns, rows, avail, body, run_id):
    """-> raw ChartSpec dict. `rows` are the dataset rows (already capped
    and filtered by the dataset's own filters)."""
    typ = body.get("type")
    if typ not in DATASET_TYPES:
        raise SpecError(f"type must be one of {list(DATASET_TYPES)} for a "
                        f"dataset chart")
    xi = _col_index(columns, body.get("x"), "x")
    yi = _col_index(columns, body.get("y"), "y")
    si = None
    if body.get("series_by") is not None:
        si = _col_index(columns, body["series_by"], "series_by")
    li = None
    if body.get("label") is not None:
        li = _col_index(columns, body["label"], "label")
    where = body.get("where") or {}
    if not isinstance(where, dict):
        raise SpecError("where must be an object {column: value}")
    sel = rows
    for col, val in where.items():
        ci = _col_index(columns, col, "where column")
        sel = [r for r in sel if str(r[ci]) == str(val)]
    # a value column whose unit varies per row must be narrowed to one class
    yname, yunit = columns[yi]
    unit = yunit
    if yunit and yunit.startswith("see "):
        ref = yunit[4:].split()[0]
        names = [n for n, _ in columns]
        if ref in names:
            vals = sorted({str(r[names.index(ref)]) for r in sel})
            if len(vals) > 1:
                raise SpecError(
                    f"column {yname!r} is in a different unit per row "
                    f"(see {ref}); the rows span {len(vals)} classes "
                    f"{vals[:4]}. Narrow to one with where: "
                    f"{{\"{ref}\": \"<value>\"}} (no figure mixes unit "
                    f"classes)")
            unit = vals[0] if vals else None
        else:
            unit = None
    # provenance chip
    prov = spec_ds["provenance"]
    chip = prov if prov in CHIPS else None
    if chip is None:
        names = [n for n, _ in columns]
        cc = f"{yname}_chip"
        if cc in names:
            chips = {str(r[names.index(cc)]) for r in sel if r[yi] != ""}
            chips.discard("")
            if len(chips) == 1 and next(iter(chips)) in CHIPS:
                chip = next(iter(chips))
            elif len(chips) > 1:
                raise SpecError(f"column {yname!r} mixes provenance chips "
                                f"{sorted(chips)}; narrow the rows to one")
        if chip is None:
            chip = "SNAPSHOT"
    # group
    groups: dict = {}
    order = []
    for r in sel:
        g = "" if si is None else str(r[si])
        if g not in groups:
            groups[g] = []
            order.append(g)
        groups[g].append(r)
    if len(order) > MAX_SERIES[typ]:
        raise SpecError(f"series_by gives {len(order)} series; a {typ} chart "
                        f"takes at most {MAX_SERIES[typ]}. Narrow with "
                        f"where.")
    cat_type = typ in ("bar", "stacked_bar")
    xs_all = [r[xi] for r in sel]
    iso_time = bool(xs_all) and all(isinstance(x, str) and parse_iso(x)
                                    for x in xs_all)
    numeric_x = bool(xs_all) and all(is_num(x) for x in xs_all)
    series = []
    total = 0
    for g in order:
        pts, seen = [], set()
        for r in groups[g]:
            y = _coerce_y(r[yi])
            x = r[xi]
            if cat_type or not (numeric_x or iso_time):
                x = str(x)
            if cat_type:
                if x in seen:
                    raise SpecError(
                        f"two rows share the category {x!r}"
                        + (f" in series {g!r}" if g else "")
                        + "; a chart never sums rows. Add series_by or "
                          "narrow with where.")
                seen.add(x)
            p = {"x": x, "y": y}
            if li is not None and r[li] != "":
                p["label"] = str(r[li])
            pts.append(p)
        total += len(pts)
        if total > MAX_POINTS_TOTAL:
            raise SpecError(f"more than {MAX_POINTS_TOTAL} points")
        s = {"name": g or str(body.get("y")), "points": pts,
             "source": f"export dataset {did}" + (f", run {run_id}"
                                                  if run_id else "")}
        if unit:
            s["unit"] = unit
        series.append(s)
    cut = avail - len(rows)
    xcol = columns[xi]
    spec = {"type": typ, "chip": chip,
            "title": body.get("title") or f"{spec_ds['label']}: "
                                          f"{body.get('y')} by {body.get('x')}",
            "series": series,
            "axes": {"x": {"label": xcol[0], **({"unit": xcol[1]}
                                                  if xcol[1] else {})},
                     "y": {"label": yname, **({"unit": unit} if unit else {})}},
            "options": body.get("options") or {}}
    if body.get("subtitle"):
        spec["subtitle"] = body["subtitle"]
    if body.get("stack_class"):
        spec["stack_class"] = body["stack_class"]
    if cut > 0:
        spec["note"] = (f"The dataset was cut to {len(rows)} of {avail} "
                        f"rows by rows_cap.")
    return spec


def rows_cap_of(body):
    cap = body.get("rows_cap", DEFAULT_ROWS_CAP)
    if not isinstance(cap, int) or isinstance(cap, bool) \
            or not 1 <= cap <= MAX_ROWS_CAP:
        raise SpecError(f"rows_cap must be a whole number in "
                        f"1..{MAX_ROWS_CAP}")
    return cap
