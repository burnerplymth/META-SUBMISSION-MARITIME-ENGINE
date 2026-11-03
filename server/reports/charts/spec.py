"""ChartSpec: the small, closed JSON schema every figure is drawn from.

    {type, title, subtitle?, chip, note?, stack_class?,
     series:[{name, source, chip?, unit?, class?, points:[...]}],
     axes:{x?:{label,unit,kind,min,max}, y?:{...}},
     annotations:[{kind:marker|hline|band, ...label}],
     options:{orientation, sort, columns, panel_type, max_rows, width,
              labels}}

`normalise` validates (unknown keys are refused, row caps enforced,
doctrine checked) and returns the canonical form the renderer draws.
Doctrine enforced here, not left to callers:
  * a stacked bar names ONE class (`stack_class`) and its series may not
    belong to different goods or unit classes;
  * no series, category or annotation is labelled as a total / headline
    'trade at risk' / forward price claim;
  * every figure carries a chip and every series a source.
"""
from __future__ import annotations

import math
import re

from reports.vocab import plain

from .fmt import clean, parse_iso

TYPES = ("line", "bar", "stacked_bar", "scatter", "small_multiples",
         "sparkline", "cascade", "route_delay")
CHIPS = ("SOURCED", "SNAPSHOT", "MODELLED", "SIMULATED", "NOT SERVED",
         "BACKTEST", "LEDGER")
AXIS_KINDS = ("number", "time", "category")
ORIENTATIONS = ("auto", "h", "v")
SORTS = ("none", "value_desc", "value_asc")

MAX_SERIES = {"line": 8, "bar": 8, "stacked_bar": 8, "scatter": 3,
              "small_multiples": 12, "sparkline": 1, "cascade": 1,
              "route_delay": 1}
MAX_POINTS_SERIES = 2000
MAX_POINTS_TOTAL = 10000
MAX_ROWS = 400            # cascade / route_delay / bar categories drawn
MAX_ANNOTATIONS = 20
MAX_TEXT = 200

GOODS_CLASSES = {"container", "dry_bulk", "general_cargo", "roro", "tanker"}
UNIT_CLASSES = {"teu/day", "vessels/day", "trucks/day", "tonnes/day"}
TOTAL_WORDS = {"total", "grand total", "sum", "all classes", "overall",
               "all places", "all ports", "all", "combined"}
CLAIM_RE = re.compile(
    r"trade[\s_-]*at[\s_-]*risk|price[\s_-]*target|forward[\s_-]*price|"
    r"forecast(ed)?[\s_-]*price|projected[\s_-]*price", re.IGNORECASE)

TOP_KEYS = {"type", "title", "subtitle", "chip", "note", "stack_class",
            "series", "axes", "annotations", "options"}
SERIES_KEYS = {"name", "source", "chip", "unit", "class", "points"}
AXIS_KEYS = {"label", "unit", "kind", "min", "max"}
OPTION_KEYS = {"orientation", "sort", "columns", "panel_type", "max_rows",
               "width", "labels"}
ANNOT_KEYS = {"kind", "x", "y", "x0", "x1", "label"}


class SpecError(ValueError):
    """The ChartSpec is not valid; the message says why."""


def _closed(d, allowed, where):
    if not isinstance(d, dict):
        raise SpecError(f"{where} must be an object")
    extra = sorted(set(d) - allowed)
    if extra:
        raise SpecError(f"{where}: unknown key {extra[0]!r}; allowed: "
                        f"{sorted(allowed)}")


def _text(v, where, required=False, maxlen=MAX_TEXT):
    if v is None or v == "":
        if required:
            raise SpecError(f"{where} is required")
        return None
    if not isinstance(v, str):
        raise SpecError(f"{where} must be a string")
    s = clean(v)
    if len(s) > maxlen:
        raise SpecError(f"{where} is longer than {maxlen} characters")
    if required and not s:
        raise SpecError(f"{where} is required")
    if CLAIM_RE.search(s):
        raise SpecError(f"{where}: this product states no headline 'trade at "
                        f"risk' figure and makes no forward price claim")
    return plain(s)


def is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) \
        and math.isfinite(v)


def _number(v, where, allow_none=False):
    if v is None and allow_none:
        return None
    if not is_num(v):
        raise SpecError(f"{where} must be a finite number"
                        + (" or null" if allow_none else ""))
    return v


def _label(v, where, maxlen=80):
    s = _text(v, where, required=True, maxlen=maxlen)
    if s.lower() in TOTAL_WORDS:
        raise SpecError(
            f"{where}: {s!r} reads as a total; no total across places or "
            f"classes is drawn (give the parts)")
    return s


def _axis(a, where):
    if a is None:
        return {}
    _closed(a, AXIS_KEYS, where)
    out = {}
    for k in ("label", "unit"):
        t = _text(a.get(k), f"{where}.{k}", maxlen=80)
        if t:
            out[k] = t
    if a.get("kind") is not None:
        if a["kind"] not in AXIS_KINDS:
            raise SpecError(f"{where}.kind must be one of {list(AXIS_KINDS)}")
        out["kind"] = a["kind"]
    for k in ("min", "max"):
        if a.get(k) is not None:
            out[k] = _number(a[k], f"{where}.{k}")
    if "min" in out and "max" in out and out["min"] >= out["max"]:
        raise SpecError(f"{where}.min must be below {where}.max")
    return out


def _xy_points(pts, where, kind_hint):
    out = []
    for i, p in enumerate(pts):
        w = f"{where}[{i}]"
        _closed(p, {"x", "y", "label"}, w)
        if "x" not in p:
            raise SpecError(f"{w}.x is required")
        x = p["x"]
        if isinstance(x, str):
            x = clean(x)
            if not x:
                raise SpecError(f"{w}.x is empty")
        else:
            _number(x, f"{w}.x")
        q = {"x": x, "y": _number(p.get("y"), f"{w}.y", allow_none=True)}
        lab = _text(p.get("label"), f"{w}.label", maxlen=80)
        if lab:
            q["label"] = lab
        out.append(q)
    return out


def _infer_kind(series, declared, chart_type):
    if chart_type in ("bar", "stacked_bar"):
        return "category"
    xs = [p["x"] for s in series for p in s["points"]]
    if declared:
        kind = declared
    elif any(isinstance(x, str) for x in xs) and any(
            not isinstance(x, str) for x in xs):
        raise SpecError("x values are mixed numbers and text; set "
                        "axes.x.kind to 'category' or send one kind only")
    elif not xs:
        kind = "number"
    elif all(not isinstance(x, str) for x in xs):
        kind = "number"
    elif all(parse_iso(x) is not None for x in xs):
        kind = "time"
    else:
        kind = "category"
    if kind == "number" and any(isinstance(x, str) for x in xs):
        raise SpecError("x values are mixed numbers and text; set "
                        "axes.x.kind to 'category' or send numbers only")
    if kind == "category":
        for s in series:
            for p in s["points"]:
                if not isinstance(p["x"], str):
                    p["x"] = str(p["x"])
    if kind == "time" and any(parse_iso(x) is None for x in xs):
        raise SpecError("axes.x.kind is 'time' but an x is not an ISO-8601 "
                        "date or timestamp")
    return kind


def _cat_points(pts, where):
    out = []
    for i, p in enumerate(pts):
        w = f"{where}[{i}]"
        _closed(p, {"x", "y"}, w)
        x = p.get("x")
        if is_num(x):
            x = str(x)
        q = {"x": _label(x, f"{w}.x"),
             "y": _number(p.get("y"), f"{w}.y", allow_none=True)}
        out.append(q)
    return out


def _cascade_points(pts, where):
    out = []
    for i, p in enumerate(pts):
        w = f"{where}[{i}]"
        _closed(p, {"label", "start", "duration", "channel", "parent",
                    "depth", "magnitude"}, w)
        q = {"label": _text(p.get("label"), f"{w}.label", True, 80),
             "start": _number(p.get("start"), f"{w}.start"),
             "duration": _number(p.get("duration"), f"{w}.duration"),
             "channel": _text(p.get("channel"), f"{w}.channel", True, 40)}
        if q["duration"] < 0:
            raise SpecError(f"{w}.duration must be zero or more")
        par = p.get("parent")
        if par is not None:
            if not isinstance(par, int) or isinstance(par, bool) \
                    or not 0 <= par < i:
                raise SpecError(f"{w}.parent must be the index of an "
                                f"earlier point or null")
        q["parent"] = par
        d = p.get("depth", 0)
        if not isinstance(d, int) or isinstance(d, bool) or d < 0:
            raise SpecError(f"{w}.depth must be a whole number >= 0")
        q["depth"] = d
        if p.get("magnitude") is not None:
            q["magnitude"] = _number(p["magnitude"], f"{w}.magnitude")
        out.append(q)
    return out


def _route_points(pts, where):
    out = []
    for i, p in enumerate(pts):
        w = f"{where}[{i}]"
        _closed(p, {"from", "to", "value", "channel"}, w)
        q = {"from": _text(p.get("from"), f"{w}.from", True, 80),
             "to": _text(p.get("to"), f"{w}.to", True, 80),
             "value": _number(p.get("value"), f"{w}.value", True)}
        ch = _text(p.get("channel"), f"{w}.channel", maxlen=40)
        if ch:
            q["channel"] = ch
        out.append(q)
    return out


def _annotations(raw, kind):
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_ANNOTATIONS:
        raise SpecError(f"annotations must be a list of at most "
                        f"{MAX_ANNOTATIONS}")
    out = []
    for i, a in enumerate(raw):
        w = f"annotations[{i}]"
        _closed(a, ANNOT_KEYS, w)
        k = a.get("kind")
        if k not in ("marker", "hline", "band"):
            raise SpecError(f"{w}.kind must be marker, hline or band")
        q = {"kind": k, "label": _text(a.get("label"), f"{w}.label", True,
                                       60)}
        if k == "marker":
            if a.get("x") is None:
                raise SpecError(f"{w}.x is required for a marker")
            q["x"] = a["x"] if isinstance(a["x"], str) else _number(
                a["x"], f"{w}.x")
        elif k == "hline":
            q["y"] = _number(a.get("y"), f"{w}.y")
        else:
            for f in ("x0", "x1"):
                if a.get(f) is None:
                    raise SpecError(f"{w}.{f} is required for a band")
                q[f] = a[f] if isinstance(a[f], str) else _number(
                    a[f], f"{w}.{f}")
        out.append(q)
    return out


def _options(o):
    if o is None:
        o = {}
    _closed(o, OPTION_KEYS, "options")
    out = {"orientation": o.get("orientation", "auto"),
           "sort": o.get("sort", "none"),
           "columns": o.get("columns", 3),
           "panel_type": o.get("panel_type", "line"),
           "max_rows": o.get("max_rows", 60),
           "width": o.get("width", 720),
           "labels": o.get("labels", True)}
    if out["orientation"] not in ORIENTATIONS:
        raise SpecError(f"options.orientation must be one of "
                        f"{list(ORIENTATIONS)}")
    if out["sort"] not in SORTS:
        raise SpecError(f"options.sort must be one of {list(SORTS)}")
    if out["panel_type"] not in ("line", "bar"):
        raise SpecError("options.panel_type must be line or bar")
    for k, lo, hi in (("columns", 1, 4), ("max_rows", 1, MAX_ROWS),
                      ("width", 320, 1200)):
        v = out[k]
        if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
            raise SpecError(f"options.{k} must be a whole number in "
                            f"{lo}..{hi}")
    if not isinstance(out["labels"], bool):
        raise SpecError("options.labels must be true or false")
    return out


def normalise(spec) -> dict:
    """Validate a ChartSpec and return its canonical form (or raise
    SpecError). Pure: the same spec always yields the same result."""
    _closed(spec, TOP_KEYS, "spec")
    typ = spec.get("type")
    if typ not in TYPES:
        raise SpecError(f"type must be one of {list(TYPES)}")
    out = {"type": typ,
           "title": _text(spec.get("title"), "title", True, 140),
           "subtitle": _text(spec.get("subtitle"), "subtitle", False, 200),
           "note": _text(spec.get("note"), "note", False, 300)}
    chip = spec.get("chip")
    if chip not in CHIPS:
        raise SpecError(f"chip is required and must be one of {list(CHIPS)}")
    out["chip"] = chip
    stack = _text(spec.get("stack_class"), "stack_class", False, 60)
    raw = spec.get("series")
    if not isinstance(raw, list) or not raw:
        raise SpecError("series must be a non-empty list")
    if len(raw) > MAX_SERIES[typ]:
        raise SpecError(f"a {typ} chart takes at most {MAX_SERIES[typ]} "
                        f"series")
    total = 0
    series, names = [], set()
    for si, s in enumerate(raw):
        w = f"series[{si}]"
        _closed(s, SERIES_KEYS, w)
        name = _label(s.get("name"), f"{w}.name")
        if name in names:
            raise SpecError(f"{w}.name {name!r} is repeated")
        names.add(name)
        src = _text(s.get("source"), f"{w}.source", True, 200)
        schip = s.get("chip", chip)
        if schip not in CHIPS:
            raise SpecError(f"{w}.chip must be one of {list(CHIPS)}")
        pts = s.get("points")
        if not isinstance(pts, list):
            raise SpecError(f"{w}.points must be a list")
        cap = MAX_ROWS if typ in ("cascade", "route_delay") \
            else MAX_POINTS_SERIES
        if len(pts) > cap:
            raise SpecError(f"{w}.points holds {len(pts)} rows; the cap is "
                            f"{cap}")
        total += len(pts)
        if total > MAX_POINTS_TOTAL:
            raise SpecError(f"more than {MAX_POINTS_TOTAL} points in total")
        if typ in ("bar", "stacked_bar"):
            npts = _cat_points(pts, f"{w}.points")
        elif typ == "cascade":
            npts = _cascade_points(pts, f"{w}.points")
        elif typ == "route_delay":
            npts = _route_points(pts, f"{w}.points")
        else:
            npts = _xy_points(pts, f"{w}.points", None)
        q = {"name": name, "chip": schip, "source": src, "points": npts}
        u = _text(s.get("unit"), f"{w}.unit", False, 60)
        if u:
            q["unit"] = u
        c = _text(s.get("class"), f"{w}.class", False, 60)
        if c:
            q["class"] = c
        series.append(q)
    out["series"] = series

    if typ == "stacked_bar":
        if not stack:
            raise SpecError(
                "a stacked bar must name the one class its parts belong to "
                "(stack_class); stacking across goods or unit classes is "
                "never drawn")
        for s in series:
            if s.get("class") and s["class"] != stack:
                raise SpecError(f"series {s['name']!r} is class "
                                f"{s['class']!r}, not {stack!r}: no stack "
                                f"across classes")
            if any(p["y"] is not None and p["y"] < 0 for p in s["points"]):
                raise SpecError("a stacked bar takes no negative values")
        low = {s["name"].lower() for s in series}
        if len(low & GOODS_CLASSES) >= 2 or len(low & UNIT_CLASSES) >= 2:
            raise SpecError("series named after different goods or unit "
                            "classes cannot be stacked")
        units = {s.get("unit") for s in series if s.get("unit")}
        if len(units) > 1:
            raise SpecError("stacked series must share one unit")
        out["stack_class"] = stack
    elif stack:
        out["stack_class"] = stack

    axes = spec.get("axes") or {}
    _closed(axes, {"x", "y"}, "axes")
    ax, ay = _axis(axes.get("x"), "axes.x"), _axis(axes.get("y"), "axes.y")
    if typ in ("line", "scatter", "sparkline", "small_multiples"):
        ax["kind"] = _infer_kind(series, ax.get("kind"), typ)
    elif typ in ("bar", "stacked_bar"):
        ax["kind"] = "category"
    out["axes"] = {"x": ax, "y": ay}
    out["annotations"] = _annotations(spec.get("annotations"), ax.get("kind"))
    out["options"] = _options(spec.get("options"))
    return out


def points_total(spec) -> int:
    return sum(len(s["points"]) for s in spec["series"])
