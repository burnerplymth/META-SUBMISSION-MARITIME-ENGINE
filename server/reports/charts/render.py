"""Figure frame: title, accessible description, body, provenance footer.

    render_svg(spec, theme="light", embed=False, uid=None) -> str

`spec` may be a raw ChartSpec (it is normalised first) or an already
normalised one. The output is a pure function of (spec, theme, embed,
uid): same inputs, same bytes. Stand-alone files carry their own
stylesheet and background; `embed=True` omits both so an HTML page can
theme the figure (see theme.page_css)."""
from __future__ import annotations

import csv
import hashlib
import io

from ..csv_guard import neutralise
import source_credits as _credits
from . import draw
from .fmt import coord as c
from .fmt import esc, text_w, wrap
from .spec import SpecError, normalise
from .theme import CHIP_CLASS, THEMES, page_css, standalone_css  # noqa: F401

DRAWERS = {"line": draw.draw_xy, "scatter": draw.draw_xy,
           "bar": draw.draw_bars, "stacked_bar": draw.draw_bars,
           "route_delay": draw.draw_bars, "cascade": draw.draw_cascade,
           "small_multiples": draw.draw_small_multiples,
           "sparkline": draw.draw_sparkline}
TYPE_NAMES = {"line": "line chart", "scatter": "scatter chart",
              "bar": "bar chart", "stacked_bar": "stacked bar chart",
              "route_delay": "bar chart of delay per leg",
              "cascade": "cascade timeline of the causal chain",
              "small_multiples": "small multiples",
              "sparkline": "sparkline"}


def ensure_norm(spec) -> dict:
    """Normalise (idempotent: a normalised spec normalises to itself)."""
    return normalise(spec)


def source_line(spec) -> str:
    """'Source: ...' text; series that differ in source or chip are named."""
    pairs = []
    for s in spec["series"]:
        p = (s["chip"], s["source"])
        if p not in [q[1:] for q in pairs]:
            pairs.append((s["name"],) + p)
    if len(pairs) == 1:
        return _credits.source_line(pairs[0][2])
    return "Sources: " + "; ".join(
        f"{n} ({chip}) {_credits.display(src)}" for n, chip, src in pairs)


def caption(spec) -> str:
    """Figure caption: what is drawn, its provenance chip and sources."""
    spec = ensure_norm(spec)
    return (f"{spec['title']} [{spec['chip']}]. {source_line(spec)}."
            + (f" {spec['note']}" if spec.get("note") else ""))


def uid_of(spec) -> str:
    h = hashlib.sha1(
        (spec["title"] + "|" + spec["type"]).encode("utf-8")).hexdigest()
    return "sm" + h[:8]


def render_svg(spec, theme: str = "light", embed: bool = False,
               uid: str | None = None) -> str:
    spec = ensure_norm(spec)
    if theme not in THEMES:
        raise SpecError(f"theme must be one of {list(THEMES)}")
    uid = uid or uid_of(spec)
    typ = spec["type"]
    W = spec["options"]["width"]
    if typ == "sparkline":
        W = min(W, 360)
    pad = draw.PAD
    out = []
    y = 0.0
    spark = typ == "sparkline"
    if spark:
        title_lines = wrap(spec["title"], W - 2 * pad, 11, 1)
        out.append(draw.T(pad, 14, title_lines[0], "t2", 11))
        y = 20
        body, bh, summary = DRAWERS[typ](spec, W)
        out.append(f'<g transform="translate(0 {c(y)})">{body}</g>')
        y += bh + 6
    else:
        tl = wrap(spec["title"], W - 2 * pad, 15, 2)
        y = 22
        for i, t in enumerate(tl):
            out.append(draw.T(pad, y, t, "t", 15, weight="600"))
            y += 19
        if spec.get("subtitle"):
            for t in wrap(spec["subtitle"], W - 2 * pad, 12, 2):
                out.append(draw.T(pad, y + 2, t, "t2", 12))
                y += 16
        y += 6
        body, bh, summary = DRAWERS[typ](spec, W)
        out.append(f'<g transform="translate(0 {c(y)})">{body}</g>')
        y += bh + 8
    # footer: chip + sources + note
    chip = spec["chip"]
    cc = CHIP_CLASS.get(chip, "notserved")
    chip_w = text_w(chip, 10) * 1.2 + 16
    fs = 10 if spark else 10.5
    y += 4
    out.append(f'<rect x="{c(pad)}" y="{c(y - 11)}" width="{c(chip_w)}" '
               f'height="15" rx="3" class="chip-{cc}" stroke-width="1"/>')
    out.append(draw.T(pad + 8, y, chip, f"chipt-{cc}", 10, weight="700",
                      extra=' letter-spacing=".4"'))
    lines = wrap(source_line(spec), W - 2 * pad - chip_w - 10, fs,
                 1 if spark else 3)
    ly = y
    for i, t in enumerate(lines):
        out.append(draw.T(pad + chip_w + 10, ly, t, "tm", fs))
        ly += 13
    y = max(y, ly - 13)
    if spec.get("note"):
        y += 2
        for t in wrap(spec["note"], W - 2 * pad, fs, 3):
            y += 13
            out.append(draw.T(pad, y, t, "tm", fs, italic=True))
    H = y + 12
    desc = (f"{TYPE_NAMES[typ].capitalize()}. {summary} Provenance "
            f"{chip}. {source_line(spec)}.")
    root = (f'<svg xmlns="http://www.w3.org/2000/svg" class="sm-chart" '
            f'viewBox="0 0 {c(W)} {c(H)}" width="{c(W)}" height="{c(H)}" '
            f'role="img" aria-labelledby="{uid}-t {uid}-d"'
            + ("" if embed else f' data-theme="{theme}"')
            + ' style="max-width:100%;height:auto">')
    head = f'<title id="{uid}-t">{esc(spec["title"])} ({chip})</title>' \
           f'<desc id="{uid}-d">{esc(desc)}</desc>'
    style = "" if embed else f"<style>{standalone_css(theme)}</style>"
    bg = "" if embed else f'<rect class="bg" width="{c(W)}" height="{c(H)}"/>'
    return root + head + style + bg + "".join(out) + "</svg>\n"


# -------------------------------------------------------------------- csv
_neutral = neutralise   # the one shared guard (reports/csv_guard.py)


def chart_table(spec):
    """(header, rows): the exact data behind the figure, long form."""
    spec = ensure_norm(spec)
    typ = spec["type"]
    ay, ax = spec["axes"]["y"], spec["axes"]["x"]
    rows = []
    if typ == "cascade":
        head = ["step", "place", "arrival_day [days after start]",
                "delay_d [days]", "channel", "parent_step", "depth",
                "magnitude [share of capacity]", "provenance", "source"]
        s = spec["series"][0]
        for i, p in enumerate(s["points"]):
            rows.append([i + 1, p["label"], p["start"], p["duration"],
                         p["channel"], "" if p["parent"] is None
                         else p["parent"] + 1, p["depth"],
                         p.get("magnitude", ""), s["chip"], s["source"]])
    elif typ == "route_delay":
        head = ["from_place", "to_place", "delay [days]", "channel",
                "provenance", "source"]
        s = spec["series"][0]
        for p in s["points"]:
            rows.append([p["from"], p["to"],
                         "" if p["value"] is None else p["value"],
                         p.get("channel", ""), s["chip"], s["source"]])
    elif typ in ("bar", "stacked_bar"):
        u = ay.get("unit") or next((s.get("unit") for s in spec["series"]
                                    if s.get("unit")), None)
        head = ["series", "category", f"value [{u}]" if u else "value",
                "provenance", "source"]
        for s in spec["series"]:
            for p in s["points"]:
                rows.append([s["name"], p["x"],
                             "" if p["y"] is None else p["y"], s["chip"],
                             s["source"]])
    else:
        u = ay.get("unit")
        xu = ax.get("unit")
        head = ["series", f"x [{xu}]" if xu else "x",
                f"y [{u}]" if u else "y", "label", "provenance", "source"]
        for s in spec["series"]:
            for p in s["points"]:
                rows.append([s["name"], p["x"],
                             "" if p["y"] is None else p["y"],
                             p.get("label", ""), s["chip"], s["source"]])
    return head, rows


def chart_csv(spec) -> bytes:
    head, rows = chart_table(spec)
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(head)
    for r in rows:
        w.writerow([_neutral(v) for v in r])
    return buf.getvalue().encode("utf-8")
