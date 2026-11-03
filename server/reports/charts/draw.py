"""Drawers: one function per chart family, each returning
(svg fragment, body height, summary sentence) for a body area `W` wide.
Pure functions of the normalised spec: no clock, no randomness."""
from __future__ import annotations

import math

from .axes import XAxis, YAxis, spread, zero_heuristic
from .fmt import coord as c
from .fmt import esc, num, text_w, trunc, wrap
from .theme import channel_slot, slot_class

PAD = 16
TICK = 11


def T(x, y, s, cls="t", size=11, anchor=None, weight=None, italic=False,
      extra=""):
    a = f' text-anchor="{anchor}"' if anchor else ""
    w = f' font-weight="{weight}"' if weight else ""
    i = ' font-style="italic"' if italic else ""
    return (f'<text x="{c(x)}" y="{c(y)}" class="{cls}" font-size="{size}"'
            f'{a}{w}{i}{extra}>{esc(s)}</text>')


def line(x1, y1, x2, y2, cls="gr", w=1, dash=None, extra=""):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return (f'<line x1="{c(x1)}" y1="{c(y1)}" x2="{c(x2)}" y2="{c(y2)}" '
            f'class="{cls}" stroke-width="{w}"{d}{extra}/>')


def rect(x, y, w, h, cls, rx=0, extra=""):
    r = f' rx="{rx}"' if rx else ""
    return (f'<rect x="{c(x)}" y="{c(y)}" width="{c(w)}" height="{c(h)}" '
            f'class="{cls}"{r}{extra}/>')


def sslot(series, i):
    """Categorical slot of series i: a series named after a chain channel
    keeps that channel's colour everywhere; others take their order."""
    return channel_slot(series[i]["name"]) or i + 1


def tip(s):
    return f"<title>{esc(s)}</title>"


def _series_summary(series, limit=6):
    bits = []
    for s in series[:limit]:
        ys = [p["y"] for p in s["points"] if p.get("y") is not None]
        if not ys:
            bits.append(f"{s['name']}: no values (NOT SERVED)")
            continue
        bits.append(f"{s['name']}: {len(ys)} values, lowest {num(min(ys))}, "
                    f"highest {num(max(ys))}, last {num(ys[-1])}")
    more = f"; and {len(series) - limit} more series" \
        if len(series) > limit else ""
    return "; ".join(bits) + more


def _unit_head(ax, series=None):
    lab, unit = ax.get("label"), ax.get("unit")
    if not lab and series:
        lab = None
    if lab and unit:
        return f"{lab} [{unit}]"
    return lab or (f"[{unit}]" if unit else "")


def _key_row(items, x, y, width):
    """Inline name key for multi-series charts that cannot label directly:
    [(slot, name)] -> (svg, height)."""
    out, cx, cy = [], x, y
    for slot, name in items:
        w = text_w(name, 11) + 24
        if cx + w > x + width and cx > x:
            cx, cy = x, cy + 16
        out.append(f'<circle cx="{c(cx + 4)}" cy="{c(cy - 4)}" r="4" '
                   f'class="f{slot_class(slot)}"/>')
        out.append(T(cx + 12, cy, name, "t2", 11))
        cx += w
    return "".join(out), (cy - y) + 16


# ------------------------------------------------------------------ line
def _marker_rows(annots, xa, plot_x0, plot_x1):
    """Stagger marker labels over up to 3 rows so they do not overlap."""
    rows_end = [-1e9, -1e9, -1e9]
    placed = []
    for a in annots:
        if a["kind"] != "marker":
            continue
        px = xa.pos(a["x"])
        if px is None:
            continue
        w = text_w(a["label"], 10.5) + 6
        anchor = "start"
        x0 = px + 4
        if x0 + w > plot_x1 + 40:
            anchor, x0 = "end", px - 4
        lo, hi = (x0, x0 + w) if anchor == "start" else (x0 - w, x0)
        row = next((r for r in range(3) if lo >= rows_end[r] + 4), 2)
        rows_end[row] = max(rows_end[row], hi)
        placed.append((px, a["label"], anchor, x0, row))
    return placed


def draw_xy(spec, W):
    kind = spec["type"]
    series = spec["series"]
    ax, ay = spec["axes"]["x"], spec["axes"]["y"]
    labels_on = spec["options"]["labels"]
    pts = [s["points"] for s in series]
    ys_all = [p["y"] for s in series for p in s["points"]]
    # direct end labels (line) need a right margin
    end_lab = []
    for si, s in enumerate(series):
        last = next((p for p in reversed(s["points"])
                     if p["y"] is not None), None)
        if last is not None:
            end_lab.append((si, last, f"{s['name']} {num(last['y'])}"))
    right = 16
    if kind == "line" and labels_on and end_lab:
        right = min(170, max(text_w(t, 11) for _, _, t in end_lab)) + 22
    # y axis
    hl = [a["y"] for a in spec["annotations"] if a["kind"] == "hline"]
    plot_h = 230
    zero_inc = kind == "line" and zero_heuristic(ys_all)
    ya = YAxis(ys_all, 24 + plot_h, 24, ay, include_zero=zero_inc, extra=hl)
    left = max([text_w(t, TICK) for _, t in ya.ticks()] or [10]) + PAD + 8
    xa = XAxis(ax["kind"], pts, left, W - right, ax)
    mrows = _marker_rows(spec["annotations"], xa, left, W - right)
    nrow = (max(r for *_, r in mrows) + 1) if mrows else 0
    top = 24 + 14 * nrow
    ya = YAxis(ys_all, top + plot_h, top, ay, include_zero=zero_inc,
               extra=hl)
    out = []
    head = _unit_head(ay)
    if head:
        out.append(T(PAD, 12, head, "tm", 11))
    # grid + y ticks
    for py, lab in ya.ticks():
        out.append(line(left, py, W - right, py, "gr"))
        out.append(T(left - 6, py + 4, lab, "tm", TICK, "end"))
    base = ya.pos(max(ya.d0, min(0.0, ya.d1)))
    out.append(line(left, base, W - right, base, "ax"))
    # bands first (under data)
    for a in spec["annotations"]:
        if a["kind"] == "band":
            p0, p1 = xa.pos(a["x0"]), xa.pos(a["x1"])
            if p0 is None or p1 is None:
                continue
            lo, hi = min(p0, p1), max(p0, p1)
            out.append(rect(lo, top, hi - lo, plot_h, "fo",
                            extra=' fill-opacity=".14"'))
            out.append(T(lo + 4, top + 12, a["label"], "t2", 10.5))
    # x ticks
    for px, lab in xa.ticks():
        if xa.kind == "category" and len(xa.cats) > 12:
            continue
        out.append(line(px, top + plot_h, px, top + plot_h + 4, "ax"))
        out.append(T(px, top + plot_h + 17,
                     trunc(lab, max(40, (xa.band if xa.kind == "category"
                                         else 80) - 2), TICK),
                     "tm", TICK, "middle"))
    xh = _unit_head(ax)
    if xh:
        out.append(T((left + W - right) / 2, top + plot_h + 34, xh, "tm",
                     11, "middle"))
    for a in spec["annotations"]:
        if a["kind"] == "hline":
            py = ya.pos(a["y"])
            out.append(line(left, py, W - right, py, "cn", 1, "4 3"))
            out.append(T(W - right - 2, py - 4, a["label"], "t2", 10.5,
                         "end"))
    for px, lab, anchor, tx, row in mrows:
        out.append(line(px, top - 14 * row - 14 + 4, px, top + plot_h, "cn",
                        1, "3 3"))
        out.append(T(tx, top - 14 * row - 5, lab, "t2", 10.5, anchor))
    # series
    ends = []
    for si, s in enumerate(series):
        k = slot_class(sslot(series, si))
        if kind == "line":
            segs, cur = [], []
            for p in s["points"]:
                px = xa.pos(p["x"])
                if p["y"] is None or px is None:
                    if cur:
                        segs.append(cur)
                    cur = []
                    continue
                cur.append((px, ya.pos(p["y"])))
            if cur:
                segs.append(cur)
            for seg in segs:
                d = "M" + " L".join(f"{c(x)} {c(y)}" for x, y in seg)
                out.append(f'<path d="{d}" class="ln k{k}" '
                           f'stroke-width="2"/>')
            if len(s["points"]) <= 40:
                for p in s["points"]:
                    px = xa.pos(p["x"])
                    if p["y"] is None or px is None:
                        continue
                    out.append(f'<circle cx="{c(px)}" cy="{c(ya.pos(p["y"]))}'
                               f'" r="2.5" class="f{k} rg" '
                               f'stroke-width="1">{tip(s["name"] + ": " + num(p["y"]))}</circle>')
            for seg in segs[-1:]:
                x, y = seg[-1]
                out.append(f'<circle cx="{c(x)}" cy="{c(y)}" r="4" '
                           f'class="f{k} rg" stroke-width="1.5"/>')
        else:
            for p in s["points"]:
                px = xa.pos(p["x"])
                if p["y"] is None or px is None:
                    continue
                out.append(
                    f'<circle cx="{c(px)}" cy="{c(ya.pos(p["y"]))}" r="4.5" '
                    f'class="f{k} rg" stroke-width="1.5">'
                    f'{tip((s["name"] + ": ") + num(p["y"]) + " at " + str(p["x"]))}'
                    f'</circle>')
    # direct labels
    if kind == "line" and labels_on:
        ypx = []
        for si, last, t in end_lab:
            ypx.append(ya.pos(last["y"]) + 4)
        sp = spread(ypx, 13, top + 8, top + plot_h + 4)
        for (si, last, t), y in zip(end_lab, sp):
            out.append(T(xa.pos(last["x"]) + 9, y,
                         trunc(t, 168, 11), "t", 11))
    elif kind == "scatter" and labels_on:
        taken = []
        n = 0
        for si, s in enumerate(series):
            for p in s["points"]:
                if p.get("label") is None or p["y"] is None or n >= 40:
                    continue
                px, py = xa.pos(p["x"]), ya.pos(p["y"])
                w = text_w(p["label"], 10.5)
                box = (px + 7, py - 7, px + 7 + w, py + 5)
                if any(not (box[2] < b[0] or box[0] > b[2] or box[3] < b[1]
                            or box[1] > b[3]) for b in taken):
                    continue
                if box[2] > W - 4:
                    continue
                taken.append(box)
                n += 1
                out.append(T(px + 7, py + 4, p["label"], "t2", 10.5))
    if kind == "scatter" and len(series) > 1:
        # scatter cannot label every point: name the series in a key row
        ksvg, kh = _key_row([(sslot(series, i), s["name"])
                             for i, s in enumerate(series)],
                            left, top + plot_h + 52, W - left - right)
        out.append(ksvg)
        extra_h = kh + 6
    else:
        extra_h = 0
    h = top + plot_h + 42 + extra_h
    return "".join(out), h, _series_summary(series)


# ------------------------------------------------------------------ bars
def _bar_items(spec):
    """-> (cats, values[s][c], suffix[c], slot[c]|None, shown, of)"""
    series = spec["series"]
    typ = spec["type"]
    if typ == "route_delay":
        pts = series[0]["points"]
        cats = [f"{p['from']} to {p['to']}" for p in pts]
        vals = [[p["value"] for p in pts]]
        sfx = [("" if p.get("channel") is None else p["channel"])
               for p in pts]
        use_slot = [((channel_slot(p["channel"]) or 0) if p.get("channel")
                     else 1) for p in pts]
    else:
        cats, seen = [], set()
        for s in series:
            for p in s["points"]:
                if p["x"] not in seen:
                    seen.add(p["x"])
                    cats.append(p["x"])
        look = [{p["x"]: p["y"] for p in s["points"]} for s in series]
        vals = [[look[si].get(cat) for cat in cats]
                for si in range(len(series))]
        sfx = [""] * len(cats)
        use_slot = [None] * len(cats)
    n = len(cats)
    order = list(range(n))
    srt = spec["options"]["sort"]
    if typ == "route_delay" and srt == "none":
        srt = "value_desc"
    if srt != "none":
        def key(i):
            if spec["type"] == "stacked_bar":
                v = sum(vals[s][i] or 0 for s in range(len(vals)))
                none = all(vals[s][i] is None for s in range(len(vals)))
            else:
                v = vals[0][i]
                none = v is None
            return (none, -(v or 0) if srt == "value_desc" else (v or 0), i)
        order.sort(key=key)
    cap = spec["options"]["max_rows"]
    shown = order[:cap]
    return ([cats[i] for i in shown],
            [[row[i] for i in shown] for row in vals],
            [sfx[i] for i in shown], [use_slot[i] for i in shown], len(shown),
            n)


def draw_bars(spec, W):
    typ = spec["type"]
    series = spec["series"]
    stacked = typ == "stacked_bar"
    cats, vals, sfx, uslot, shown, of = _bar_items(spec)
    ns = len(series)
    opt = spec["options"]["orientation"]
    longest = max([len(x) for x in cats] or [0])
    if typ == "route_delay":
        orient = "h"
    elif opt == "auto":
        orient = "h" if (len(cats) > 6 or longest > 12) else "v"
    else:
        orient = opt
    ay = spec["axes"]["y"]
    unit_head = _unit_head(ay)
    labels_on = spec["options"]["labels"]
    # stacked totals / extents
    if stacked:
        tops = [sum(vals[s][i] or 0 for s in range(ns))
                for i in range(len(cats))]
        allv = tops + [0]
    else:
        allv = [v for row in vals for v in row if v is not None] + [0]
    out = []
    ysum = _series_summary(series)
    need_key = ns > 1 and (stacked or orient == "v")
    key_h = 0
    if need_key:
        ksvg, key_h = _key_row([(sslot(series, i), s["name"])
                                for i, s in enumerate(series)],
                               PAD, 28 if unit_head else 14, W - 2 * PAD)
        out.append(ksvg)
    top = (22 if unit_head else 6) + key_h + (8 if key_h else 0)
    if unit_head:
        out.append(T(PAD, 12, unit_head, "tm", 11))

    def seg_cls(si, ci):
        return slot_class(uslot[ci] if uslot[ci] is not None else sslot(series, si))

    if orient == "h":
        grp = 14 if (stacked or ns == 1) else 12 * ns + 2 * (ns - 1)
        band = grp + 10
        lab_w = min(200, max([text_w(x, 11.5) for x in cats] or [20]))
        left = PAD + lab_w + 16
        val_lab = []
        for ci in range(len(cats)):
            if stacked:
                t = num(tops[ci]) if any(
                    vals[s][ci] is not None for s in range(ns)) else ""
            else:
                t = max([num(vals[s][ci]) for s in range(ns)
                         if vals[s][ci] is not None] or [""], key=len)
            if sfx[ci]:
                t += "  " + sfx[ci]
            val_lab.append(t)
        first_names = ns > 1 and not stacked
        rlab = max([text_w(t, 11) for t in val_lab] or [10]) + 12
        if first_names:
            rlab = max(rlab, max(text_w(s["name"], 10.5) for s in series)
                       + text_w("0000", 11) + 18)
        right = min(rlab, 220)
        ya = YAxis(allv, 0, 0, ay)          # reuse for ticks on x
        ticks = ya.ticks_v
        plot_w = W - left - right
        def X(v):
            return left + (v - ya.d0) / (ya.d1 - ya.d0) * plot_w \
                if ya.d1 != ya.d0 else left
        plot_h = band * max(1, len(cats))
        for t in ticks:
            px = X(t)
            out.append(line(px, top, px, top + plot_h, "gr"))
            from .fmt import tick_label
            out.append(T(px, top + plot_h + 16, tick_label(t, ya.step),
                         "tm", TICK, "middle"))
        zero = X(0)
        out.append(line(zero, top, zero, top + plot_h, "ax"))
        for ci, cat in enumerate(cats):
            y0 = top + ci * band + 5
            out.append(T(PAD, y0 + grp / 2 + 4, trunc(cat, lab_w, 11.5),
                         "t", 11.5))
            if stacked:
                acc = 0.0
                for si in range(ns):
                    v = vals[si][ci]
                    if v is None or v == 0:
                        continue
                    x0, x1 = X(acc), X(acc + v)
                    out.append(rect(x0, y0, max(0.5, x1 - x0), grp,
                                    f"f{slot_class(sslot(series, si))} rg",
                                    extra=' stroke-width="1.5"') .replace(
                        "/>", f">{tip(series[si]['name'] + ': ' + num(v) + ' (' + cat + ')')}</rect>"))
                    acc += v
                if labels_on and val_lab[ci]:
                    out.append(T(X(acc) + 6, y0 + grp / 2 + 4, val_lab[ci],
                                 "t", 11))
            else:
                for si in range(ns):
                    v = vals[si][ci]
                    by = y0 + si * 14
                    bh = 14 if ns == 1 else 12
                    if v is None:
                        out.append(T(zero + 6, by + bh / 2 + 4,
                                     "NOT SERVED", "tm", 10.5, italic=True))
                        continue
                    x0, x1 = sorted((zero, X(v)))
                    out.append(rect(x0, by, max(0.8, x1 - x0), bh,
                                    f"f{seg_cls(si, ci)}", 2).replace(
                        "/>", f">{tip(cat + ': ' + num(v))}</rect>"))
                    if labels_on:
                        t = num(v)
                        if si == 0 and sfx[ci]:
                            t += "  " + sfx[ci]
                        if first_names and ci == 0:
                            t += "  " + series[si]["name"]
                        out.append(T(max(x1, zero) + 6, by + bh / 2 + 4, t,
                                     "t", 11))
        body_h = top + plot_h + 24
    else:
        plot_h = 220
        ya = YAxis(allv, top + plot_h, top, ay, include_zero=True)
        left = max([text_w(t, TICK) for _, t in ya.ticks()] or [10]) \
            + PAD + 8
        right = 16
        n = max(1, len(cats))
        bandw = (W - left - right) / n
        for py, lab in ya.ticks():
            out.append(line(left, py, W - right, py, "gr"))
            out.append(T(left - 6, py + 4, lab, "tm", TICK, "end"))
        zero = ya.pos(0)
        out.append(line(left, zero, W - right, zero, "ax"))
        gw = bandw * 0.72
        for ci, cat in enumerate(cats):
            cx = left + (ci + 0.5) * bandw
            out.append(T(cx, top + plot_h + 16, trunc(cat, bandw - 2, TICK),
                         "tm", TICK, "middle"))
            if stacked:
                acc = 0.0
                for si in range(ns):
                    v = vals[si][ci]
                    if v is None or v == 0:
                        continue
                    y1, y0 = ya.pos(acc + v), ya.pos(acc)
                    out.append(rect(cx - gw / 2, y1, gw, max(0.5, y0 - y1),
                                    f"f{slot_class(sslot(series, si))} rg",
                                    extra=' stroke-width="1.5"').replace(
                        "/>", f">{tip(series[si]['name'] + ': ' + num(v) + ' (' + cat + ')')}</rect>"))
                    acc += v
                if labels_on and any(vals[s][ci] is not None
                                     for s in range(ns)):
                    out.append(T(cx, ya.pos(acc) - 5, num(acc), "t", 11,
                                 "middle"))
            else:
                bw = gw / ns
                for si in range(ns):
                    v = vals[si][ci]
                    bx = cx - gw / 2 + si * bw
                    if v is None:
                        ns_t = "NOT SERVED" if text_w("NOT SERVED", 9) \
                            <= bw else "n/s"
                        out.append(T(bx + bw / 2, zero - 6, ns_t, "tm",
                                     9, "middle", italic=True))
                        continue
                    y1, y0 = sorted((ya.pos(v), zero))
                    out.append(rect(bx + 1, y1, max(1, bw - 2),
                                    max(0.8, y0 - y1),
                                    f"f{seg_cls(si, ci)}", 2).replace(
                        "/>", f">{tip(cat + ': ' + num(v))}</rect>"))
                    if labels_on and text_w(num(v), 10.5) <= bw + 14:
                        out.append(T(bx + bw / 2, y1 - 4, num(v), "t", 10.5,
                                     "middle"))
        body_h = top + plot_h + 28
    if shown < of:
        out.append(T(PAD, body_h + 4,
                     f"Showing {shown} of {of} rows; the data file holds "
                     f"the rest.", "tm", 10.5, italic=True))
        body_h += 18
    return "".join(out), body_h, ysum


# --------------------------------------------------------------- cascade
def draw_cascade(spec, W):
    pts = spec["series"][0]["points"]
    cap = spec["options"]["max_rows"]
    rows = pts[:cap]
    n = len(rows)
    rh = 18
    lab_w = min(200, max([text_w(p["label"], 11) + min(p["depth"], 6) * 7
                          for p in rows] or [40]))
    ends = [f"{p['channel']} {num(p['duration'])} d" for p in rows]
    right = min(150, max([text_w(t, 10.5) for t in ends] or [30])) + 14
    left = PAD + lab_w + 10
    ends_v = [p["start"] + p["duration"] for p in rows]
    lo = min([p["start"] for p in rows] + [0.0])
    hi = max(ends_v + [lo + 1e-9])
    from .fmt import nice_ticks, tick_label
    ticks, step, d0, d1 = nice_ticks(lo, hi, max(3, int((W - left - right)
                                                         / 90)))
    plot_w = W - left - right

    def X(v):
        return left + (v - d0) / (d1 - d0) * plot_w if d1 != d0 else left
    top = 22
    ph = rh * max(1, n)
    out = []
    xh = spec["axes"]["x"].get("label") or "Days after the disruption starts"
    out.append(T(PAD, 12, xh + " (arrival dot, bar = delay added at the "
                 "place)", "tm", 11))
    for t in ticks:
        out.append(line(X(t), top, X(t), top + ph, "gr"))
        out.append(T(X(t), top + ph + 16, tick_label(t, step), "tm", TICK,
                     "middle"))
    ycentre = [top + i * rh + rh / 2 for i in range(n)]
    for i, p in enumerate(rows):          # connectors under the bars
        par = p.get("parent")
        if par is not None and par < n:
            px = X(rows[par]["start"])
            out.append(f'<path d="M{c(px)} {c(ycentre[par] + 3)} '
                       f'V{c(ycentre[i])} H{c(X(p["start"]))}" class="cn" '
                       f'stroke-width=".8" stroke-opacity=".7"/>')
    for i, p in enumerate(rows):
        slot = channel_slot(p["channel"])
        k = slot_class(slot)
        y = ycentre[i]
        ind = min(p["depth"], 6) * 7
        out.append(T(PAD + ind, y + 4, trunc(p["label"], lab_w - ind, 11),
                     "t", 11))
        x0, x1 = X(p["start"]), X(p["start"] + p["duration"])
        if p["duration"] > 0:
            out.append(rect(x0, y - 4.5, max(2, x1 - x0), 9, f"f{k}", 2)
                       .replace("/>", f">{tip(p['label'] + ': ' + p['channel'] + ', arrives day ' + num(p['start']) + ', delay ' + num(p['duration']) + ' d')}</rect>"))
        out.append(f'<circle cx="{c(x0)}" cy="{c(y)}" r="3.2" '
                   f'class="f{k} rg" stroke-width="1.5"/>')
        out.append(T(max(x1, x0) + 7, y + 4, ends[i], "t2", 10.5))
    body_h = top + ph + 24
    if n < len(pts):
        out.append(T(PAD, body_h + 4, f"Showing the first {n} of {len(pts)} "
                     f"events in stream order; the data file holds all.",
                     "tm", 10.5, italic=True))
        body_h += 18
    chans = sorted({p["channel"] for p in rows})
    summ = (f"{n} events in stream order, arrival days {num(lo)} to "
            f"{num(max(ends_v) if ends_v else 0)}; channels "
            f"{', '.join(chans)}.")
    return "".join(out), body_h, summ


# ----------------------------------------------------------- small mult.
def draw_small_multiples(spec, W):
    series = spec["series"]
    n = len(series)
    cols = min(spec["options"]["columns"], n)
    nrows = math.ceil(n / cols)
    gap = 20
    pw = (W - 2 * PAD - (cols - 1) * gap) / cols
    ph = 118
    ax = spec["axes"]["x"]
    ptype = spec["options"]["panel_type"]
    ylab = 34
    xa = XAxis(ax["kind"], [s["points"] for s in series], 0, pw - ylab - 8,
               ax, target=3)
    out = []
    head = _unit_head(spec["axes"]["y"])
    out.append(T(PAD, 12, (head + "; " if head else "")
                 + "each panel has its own vertical scale", "tm", 11))
    top = 22
    xt = xa.ticks()
    if xa.kind == "category" and len(xt) > 4:
        xt = [xt[0], xt[len(xt) // 2], xt[-1]]
    for i, s in enumerate(series):
        r, col = divmod(i, cols)
        ox = PAD + col * (pw + gap)
        oy = top + r * (ph - 6)
        ys = [p["y"] for p in s["points"]]
        ya = YAxis(ys, oy + 18 + 66, oy + 18, spec["axes"]["y"],
                   include_zero=(ptype == "bar" or zero_heuristic(ys)),
                   target=3)
        px0 = ox + ylab
        unit = s.get("unit")
        out.append(T(ox, oy + 10, trunc(s["name"] + (f" [{unit}]"
                                                       if unit else ""),
                                          pw, 11.5), "t", 11.5,
                     weight="600"))
        for py, lab in ya.ticks():
            out.append(line(px0, py, ox + pw, py, "gr"))
            out.append(T(px0 - 4, py + 3.5, lab, "tm", 10, "end"))
        base = ya.pos(max(ya.d0, min(0.0, ya.d1)))
        out.append(line(px0, base, ox + pw, base, "ax"))
        if ptype == "line":
            cur, segs = [], []
            for p in s["points"]:
                x = xa.pos(p["x"])
                if p["y"] is None or x is None:
                    if cur:
                        segs.append(cur)
                    cur = []
                    continue
                cur.append((px0 + x, ya.pos(p["y"])))
            if cur:
                segs.append(cur)
            for seg in segs:
                d = "M" + " L".join(f"{c(a)} {c(b)}" for a, b in seg)
                out.append(f'<path d="{d}" class="ln k1" stroke-width="1.8"/>')
            if segs:
                a, b = segs[-1][-1]
                out.append(f'<circle cx="{c(a)}" cy="{c(b)}" r="3" '
                           f'class="f1 rg" stroke-width="1"/>')
        else:
            m = max(1, len(s["points"]))
            bw = (pw - ylab - 8) / m
            for j, p in enumerate(s["points"]):
                if p["y"] is None:
                    continue
                y1, y0 = sorted((ya.pos(p["y"]), base))
                out.append(rect(px0 + j * bw + bw * .12, y1, bw * .76,
                                max(.8, y0 - y1), "f1", 1))
        if xt:
            ends = [xt[0], xt[-1]] if len(xt) > 1 else [xt[0]]
            for j, (tx, lab) in enumerate(ends):
                anc = "start" if j == 0 and len(ends) > 1 else (
                    "end" if len(ends) > 1 else "middle")
                out.append(T(px0 + tx, oy + 18 + 66 + 14, lab, "tm", 10,
                             anc))
        lastv = next((p["y"] for p in reversed(s["points"])
                      if p["y"] is not None), None)
        out.append(T(ox + pw, oy + 10, "" if lastv is None
                     else "last " + num(lastv), "t2", 10.5, "end"))
    body_h = top + nrows * (ph - 6) + 4
    return "".join(out), body_h, _series_summary(series, 4)


# ------------------------------------------------------------- sparkline
def draw_sparkline(spec, W):
    s = spec["series"][0]
    pts = [p for p in s["points"]]
    xa = XAxis(spec["axes"]["x"]["kind"], [pts], 4, W - 70, spec["axes"]["x"])
    ys = [p["y"] for p in pts]
    ya = YAxis(ys, 34, 6, spec["axes"]["y"], include_zero=False)
    out, cur, segs = [], [], []
    for p in pts:
        x = xa.pos(p["x"])
        if p["y"] is None or x is None:
            if cur:
                segs.append(cur)
            cur = []
            continue
        cur.append((x, ya.pos(p["y"])))
    if cur:
        segs.append(cur)
    for seg in segs:
        d = "M" + " L".join(f"{c(a)} {c(b)}" for a, b in seg)
        out.append(f'<path d="{d}" class="ln k1" stroke-width="1.8"/>')
    lastv = None
    if segs:
        a, b = segs[-1][-1]
        out.append(f'<circle cx="{c(a)}" cy="{c(b)}" r="3" class="f1 rg" '
                   f'stroke-width="1"/>')
        lastv = next(p["y"] for p in reversed(pts) if p["y"] is not None)
        out.append(T(W - 62, b + 4, num(lastv), "t", 11))
    return "".join(out), 40, _series_summary([s])
