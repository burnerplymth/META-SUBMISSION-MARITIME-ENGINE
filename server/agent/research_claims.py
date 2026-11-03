"""Closed sentence templates for research-note findings.

The note's "Verified" status rests on this module and nothing else. A
finding may carry it only if the model returned a STRUCTURED CLAIM

    {template_id, metric, entity, entity2, direction, unit, evidence, values:[{slot, value}]}

and the code, not the model, then

  * looks the template up in the closed table below (unknown id: rejected);
  * binds it to ONE cited evidence entry of the kind the template needs;
  * checks that the entity (series key, run id ...) is a string that entry
    holds, character for character;
  * reads every slot's figure from the slot's own path in that entry, so a
    level cannot stand for a change and a before figure cannot stand for an
    after figure; the claimed value must equal the evidence value rounded to
    the digits it is written with (counts exactly), keep two significant
    digits, and agree in sign;
  * checks the direction word against the sign of the cited change, and the
    unit against the unit the evidence names for the series;
  * writes the sentence itself from the template. The model's own wording of
    a claim is never shown and never counted.

Everything else the model writes is free text: ``research_verify`` screens it
and shows it under "Context (unchecked)"; it is never counted as verified.
Pure functions of (claim, evidence entries): no model, no clock, no I/O.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

DIRECTIONS = ("up", "down", "unchanged")
NO_DIRECTION = ("", "none", "n/a", None)
MAX_DECIMALS = 6
_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.:/+()\-]{0,89}$")
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SERIES_KEY = re.compile(r"^series\[(\d+)\]\.key$")


@dataclass(frozen=True)
class Slot:
    name: str
    tail: tuple          # alternative paths below the template prefix; the first present is used
    label: str
    unit: str = ""       # "" none | "@" the claim's unit (series unit or %) | fixed text such as "d"
    integer: bool = False
    required: bool = True
    change: bool = False  # a signed change: the value is read as a magnitude and `direction` carries the sign
    group: str = ""       # slots of one group are given all together or not at all


@dataclass(frozen=True)
class Template:
    id: str
    metric: str
    kinds: tuple
    slots: tuple
    entity: str = ""          # "" none | "series" | "key" | "xy" | "run" (how the entity is found)
    unit: str = "none"        # none | series | pct   (what claim.unit must be)
    strings: tuple = ()       # (name, path) strings the sentence quotes from the entry
    direction: bool = False
    note: str = ""


def S(name, tail, label, **kw):
    return Slot(name, (tail,) if isinstance(tail, str) else tuple(tail), label, **kw)


TEMPLATES: dict[str, Template] = {t.id: t for t in (
    Template("assembly_window", "window", ("assemble",), (
        S("days", "window.days", "days in window", unit="d", integer=True),
        S("series_count", "series_count", "series assembled", integer=True, required=False),
        S("event_count", "event_count", "events listed", integer=True, required=False)),
        strings=(("from", "window.from"), ("to", "window.to"))),
    Template("assembly_scenes", "scenes", ("assemble",), (
        S("listed", "scenes.listed", "scenes listed", integer=True),
        S("usable", "scenes.usable", "scenes usable", integer=True))),
    Template("series_summary", "series_mean", ("assemble",), (
        S("n_obs", "n_obs", "days observed", unit="d", integer=True),
        S("n_days", "n_days", "days in window", unit="d", integer=True),
        S("mean", "mean", "mean", unit="@"),
        S("min", "min", "minimum", unit="@", required=False, group="range"),
        S("max", "max", "maximum", unit="@", required=False, group="range")),
        entity="key", unit="series"),
    Template("before_after_means", "mean_before_after", ("before_after",), (
        S("before_mean", "estimate.before.mean", "mean before", unit="@"),
        S("before_n", "estimate.before.n", "days before", unit="d", integer=True),
        S("after_mean", "estimate.after.mean", "mean after", unit="@"),
        S("after_n", "estimate.after.n", "days after", unit="d", integer=True)),
        entity="series", unit="series", strings=(("split", "estimate.split"),)),
    Template("before_after_change", "change_in_mean", ("before_after",), (
        S("change", ("estimate.diff_mean", "estimate.delta"), "difference in means", unit="@", change=True),),
        entity="series", unit="series", direction=True, strings=(("split", "estimate.split"),)),
    Template("before_after_pct", "percent_change", ("before_after",), (
        S("pct", "estimate.pct_change", "percent change in the mean", unit="@", change=True),),
        entity="series", unit="pct", direction=True, strings=(("split", "estimate.split"),)),
    Template("event_study_trough", "event_index", ("event_study",), (
        S("trough_index", "estimate.trough.index", "index at trough"),
        S("trough_day", "estimate.trough.offset_day", "trough day", unit="d", integer=True),
        S("below_days", "estimate.days_below_90pct", "days below 90 percent of baseline", unit="d",
          integer=True, required=False),
        S("recovery_day", "estimate.recovery_offset_day", "recovery day", unit="d", integer=True, required=False),
        S("n", "n", "events", unit="events", integer=True, required=False)),
        entity="series"),
    Template("lagged_peak", "rank_correlation", ("lagged_correlation",), (
        S("lag", "estimate.peak.lag", "lag at peak", unit="d", integer=True),
        S("rho", "estimate.peak.rho", "rank correlation at peak"),
        S("n", "estimate.peak.n", "pairs at peak", unit="d", integer=True)),
        entity="xy"),
    Template("change_points", "change_points", ("change_points",), (
        S("count", "estimate.point_count", "change points", integer=True),
        S("mean_before", "estimate.points[0].mean_before", "mean before first change point", unit="@",
          required=False, group="first"),
        S("mean_after", "estimate.points[0].mean_after", "mean after first change point", unit="@",
          required=False, group="first")),
        entity="series", unit="series",
        strings=(("method", "estimate.method"), ("date", "estimate.points[0].date"))),
    Template("distribution_groups", "median_by_group", ("distribution",), (
        S("g0_median", "estimate.groups[0].median", "median, first group", unit="@"),
        S("g0_n", "estimate.groups[0].n", "n, first group", unit="d", integer=True),
        S("g1_median", "estimate.groups[1].median", "median, second group", unit="@", required=False, group="g1"),
        S("g1_n", "estimate.groups[1].n", "n, second group", unit="d", integer=True, required=False, group="g1")),
        entity="series", unit="series",
        strings=(("g0", "estimate.groups[0].key"), ("g1", "estimate.groups[1].key"))),
    Template("seasonality_profile", "weekday_index", ("seasonality",), (
        S("low_index", "estimate.weekday_highlights.index_low.index", "lowest weekday index"),
        S("low_dow", "estimate.weekday_highlights.index_low.dow", "day of week, lowest", integer=True),
        S("high_index", "estimate.weekday_highlights.index_high.index", "highest weekday index"),
        S("high_dow", "estimate.weekday_highlights.index_high.dow", "day of week, highest", integer=True)),
        entity="series"),
    Template("overlay_metrics", "overlap", ("model_overlay",), (
        S("n_overlap", "estimate.metrics.n_overlap", "overlapping days", unit="d", integer=True),
        S("rank_correlation", "estimate.metrics.rank_correlation", "rank correlation", required=False),
        S("mean_abs_diff", "estimate.metrics.mean_abs_diff", "mean absolute difference", required=False)),
        entity="run"),
    Template("scene_scan_found", "detector_output", ("scene_scan",), (
        S("scene_count", "scene_count", "scenes scanned", integer=True),
        S("found_total", "found_total", "objects found by the detector", integer=True),
        S("not_counted", "not_counted_count", "scenes not counted", integer=True, required=False)),
        strings=(("mode", "mode"),)),
)}


def catalogue() -> list[dict]:
    """What the model is told it may claim (goes into the note payload)."""
    out = []
    for t in TEMPLATES.values():
        out.append({"template_id": t.id, "metric": t.metric, "evidence_kind": list(t.kinds),
                    "entity": {"": None, "series": "inputs.series (or inputs.x and inputs.y)",
                               "key": "series[i].key", "xy": "inputs.x and inputs.y",
                               "run": "estimate.run_id"}[t.entity],
                    "unit": {"none": "", "series": "the series unit", "pct": "%"}[t.unit],
                    "direction": t.direction,
                    "slots": [{"slot": s.name, "path": s.tail[0], "required": s.required} for s in t.slots]})
    return out


# ------------------------------------------------------------ numbers
def _dec(x) -> Decimal:
    return Decimal(repr(x)) if isinstance(x, float) else Decimal(int(x))


def number_ok(claimed, ev, *, integer: bool = False, change: bool = False) -> bool:
    """The claimed figure is the evidence figure, written with the digits it
    shows (rounded half up), keeping two significant digits. ``change``: the
    magnitude is compared and a written sign must be the evidence sign."""
    if isinstance(claimed, bool) or not isinstance(claimed, (int, float)):
        return False
    if isinstance(ev, bool) or not isinstance(ev, (int, float)):
        return False
    if not (math.isfinite(claimed) and math.isfinite(ev)):
        return False
    c, e = _dec(claimed), _dec(ev)
    if change:
        # an unsigned figure is a magnitude; a written minus must be the evidence's sign
        if c < 0 and e >= 0:
            return False
        if c != 0 and e == 0:
            return False
        c, e = abs(c), abs(e)
    if integer:
        return c == e and e == e.to_integral_value()
    exp = c.as_tuple().exponent
    dec = max(0, -exp) if isinstance(exp, int) else 0
    if dec > MAX_DECIMALS:
        return False
    q = Decimal(1).scaleb(-dec)
    if e.quantize(q, rounding=ROUND_HALF_UP) != c.quantize(q, rounding=ROUND_HALF_UP):
        return False
    if e != 0 and abs(c - e) > abs(e) * Decimal("0.05"):
        return False
    return True


def fmt_number(claimed, *, integer: bool = False, change: bool = False) -> str:
    c = _dec(claimed)
    if change:
        c = abs(c)
    if integer:
        s = str(int(c))
    else:
        s = format(c, "f")
    return s


def _unit_norm(u) -> str:
    u = ("" if u is None else str(u)).strip().lower()
    return "%" if u in ("%", "percent", "pct", "per cent") else u


# ------------------------------------------------------------ evidence lookups
def _num(entry: dict, path: str):
    v = (entry.get("numbers") or {}).get(path)
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _str(entry: dict, path: str):
    v = (entry.get("strings") or {}).get(path)
    return v.strip() if isinstance(v, str) and _SAFE.match(v.strip()) else None


def _first(entry: dict, tails: tuple, prefix: str):
    for t in tails:
        v = _num(entry, prefix + t)
        if v is not None:
            return v
    return None


def series_unit(entity: str, entries) -> tuple[str, str | None]:
    """(unit, evidence id) the evidence names for a series key; ("", None) if none."""
    for e in entries:
        for p, v in (e.get("strings") or {}).items():
            m = _SERIES_KEY.match(p)
            if m and v == entity:
                u = (e.get("strings") or {}).get(f"series[{m.group(1)}].unit")
                if isinstance(u, str) and u.strip() and _SAFE.match(u.strip()):
                    return u.strip(), e["id"]
    return "", None


@dataclass
class Result:
    ok: bool
    code: str = ""
    text: str = ""
    numbers: list = field(default_factory=list)
    evidence: list = field(default_factory=list)
    entity: str = ""
    template_id: str = ""


def _bad(code: str) -> Result:
    return Result(False, code)


def _bind_entity(t: Template, entry: dict, claim: dict):
    """-> (prefix, entity, entity2) or an error code string."""
    ent = claim.get("entity")
    ent = ent.strip() if isinstance(ent, str) else ""
    ent2 = claim.get("entity2")
    ent2 = ent2.strip() if isinstance(ent2, str) else ""
    if t.entity == "":
        return ("", "", "") if not ent and not ent2 else "entity_not_expected"
    if not ent or not _SAFE.match(ent):
        return "entity_missing"
    if t.entity == "series":
        return ("", ent, "") if _str(entry, "inputs.series") == ent else "entity_not_in_evidence"
    if t.entity == "run":
        return ("", ent, "") if _str(entry, "estimate.run_id") == ent else "entity_not_in_evidence"
    if t.entity == "xy":
        if _str(entry, "inputs.x") == ent and _str(entry, "inputs.y") == ent2:
            return "", ent, ent2
        return "entity_not_in_evidence"
    if t.entity == "key":
        for p, v in (entry.get("strings") or {}).items():
            m = _SERIES_KEY.match(p)
            if m and v == ent:
                return f"series[{m.group(1)}].", ent, ""
        return "entity_not_in_evidence"
    return "entity_not_in_evidence"


def _sign(x) -> str:
    return "up" if x > 0 else ("down" if x < 0 else "unchanged")


def check_claim(claim, by_id: dict, entries: list[dict]) -> Result:
    """Validate one structured claim against the evidence and render it.
    ``by_id``: every evidence entry of the session by id; ``entries``: the
    same as a list (used only to look up a series unit)."""
    if not isinstance(claim, dict):
        return _bad("bad_shape")
    t = TEMPLATES.get(claim.get("template_id")) if isinstance(claim.get("template_id"), str) else None
    if t is None:
        return _bad("template_unknown")
    metric = claim.get("metric")
    if metric not in (None, "") and metric != t.metric:
        return _bad("metric_mismatch")
    ev_ids = claim.get("evidence")
    ev_ids = [ev_ids] if isinstance(ev_ids, str) else ev_ids
    ev_ids = [x.strip().upper() for x in ev_ids if isinstance(x, str)] if isinstance(ev_ids, list) else []
    ev_ids = list(dict.fromkeys(ev_ids))
    if not ev_ids:
        return _bad("no_evidence")
    if any(x not in by_id for x in ev_ids):
        return _bad("unknown_evidence")
    cands = [by_id[x] for x in ev_ids if by_id[x].get("kind") in t.kinds]
    if not cands:
        return _bad("evidence_kind_mismatch")
    entry, prefix, ent, ent2, err = None, "", "", "", "entity_not_in_evidence"
    for c in cands:
        b = _bind_entity(t, c, claim)
        if isinstance(b, tuple):
            entry, (prefix, ent, ent2) = c, b
            break
        err = b
    if entry is None:
        return _bad(err)
    # direction
    d = claim.get("direction")
    d = d.strip().lower() if isinstance(d, str) else d
    if t.direction:
        if d not in DIRECTIONS:
            return _bad("direction_missing")
    elif d not in NO_DIRECTION:
        return _bad("direction_not_expected")
    # unit
    if t.unit == "series":
        want, unit_src = series_unit(ent, entries)
    elif t.unit == "pct":
        want, unit_src = "%", None
    else:
        want, unit_src = "", None
    if _unit_norm(claim.get("unit")) != _unit_norm(want):
        return _bad("unit_mismatch")
    # values
    rows = claim.get("values")
    if not isinstance(rows, list) or len(rows) > 24:
        return _bad("bad_shape")
    given: dict[str, object] = {}
    for r in rows:
        if not isinstance(r, dict) or not isinstance(r.get("slot"), str):
            return _bad("bad_shape")
        if r["slot"] in given:
            return _bad("slot_duplicate")
        given[r["slot"]] = r.get("value")
    names = {s.name: s for s in t.slots}
    if any(k not in names for k in given):
        return _bad("slot_unknown")
    for s in t.slots:
        if s.required and s.name not in given:
            return _bad("slot_missing")
    groups: dict[str, list[bool]] = {}
    for s in t.slots:
        if s.group:
            groups.setdefault(s.group, []).append(s.name in given)
    if any(any(v) and not all(v) for v in groups.values()):
        return _bad("slot_missing")
    # every figure from its own path
    vals: dict[str, str] = {}
    signed_ev: dict[str, float] = {}
    nums_out = []
    for s in t.slots:
        if s.name not in given:
            continue
        evv = _first(entry, s.tail, prefix)
        if evv is None:
            return _bad("value_not_in_evidence")
        if not number_ok(given[s.name], evv, integer=s.integer, change=s.change):
            return _bad("value_mismatch")
        vals[s.name] = fmt_number(given[s.name], integer=s.integer, change=s.change)
        if s.change:
            signed_ev[s.name] = evv
            if t.direction and d != _sign(evv):
                return _bad("direction_mismatch")
        nums_out.append({"name": s.label, "value": given[s.name] if not s.change else abs(given[s.name]),
                         "unit": want if s.unit == "@" else s.unit, "evidence": entry["id"]})
    if t.direction and not signed_ev:
        return _bad("direction_mismatch")
    # quoted strings
    q: dict[str, str] = {}
    for name, path in t.strings:
        q[name] = _str(entry, path) or ""
    if t.id in ("before_after_means", "before_after_change", "before_after_pct", "assembly_window"):
        for k in (("split",) if t.id != "assembly_window" else ("from", "to")):
            if not _ISO.match(q.get(k) or ""):
                return _bad("string_not_in_evidence")
    text = _render(t, vals, ent, ent2, want, q, d, entry)
    if text is None:
        return _bad("string_not_in_evidence")
    ev_out = [entry["id"]] + ([unit_src] if unit_src and unit_src != entry["id"] else [])
    return Result(True, "", text, nums_out, ev_out, ent, t.id)


def _u(unit: str) -> str:
    return f" {unit}" if unit else ""


def _render(t: Template, v: dict, ent: str, ent2: str, unit: str, q: dict, d, entry: dict):
    i = t.id
    if i == "assembly_window":
        txt = f"Assembled window {q['from']} to {q['to']} ({v['days']} d)"
        extra = []
        if "series_count" in v:
            extra.append(f"{v['series_count']} series")
        if "event_count" in v:
            extra.append(f"{v['event_count']} dated events")
        return txt + (": " + ", ".join(extra) if extra else "") + "."
    if i == "assembly_scenes":
        return f"{v['listed']} scenes listed, of which {v['usable']} usable."
    if i == "series_summary":
        n = f"{ent}: {v['n_obs']} of {v['n_days']} days observed, mean {v['mean']}{_u(unit)}"
        if "min" in v:
            n += f", range {v['min']} to {v['max']}{_u(unit)}"
        return n + "."
    if i == "before_after_means":
        return (f"{ent}: mean {v['before_mean']}{_u(unit)} before {q['split']} (n = {v['before_n']} d) "
                f"and {v['after_mean']}{_u(unit)} after (n = {v['after_n']} d).")
    if i in ("before_after_change", "before_after_pct"):
        figure = f"{v['change']}{_u(unit)}" if i == "before_after_change" else f"{v['pct']}%"
        if d == "unchanged":
            return f"{ent}: the mean is unchanged between the before period and the after period (split {q['split']})."
        word = "fell" if d == "down" else "rose"
        return (f"{ent}: the mean {word} by {figure} from the before period to the after period "
                f"(split {q['split']}).")
    if i == "event_study_trough":
        unit_n = _str(entry, "n_unit") or "events"
        txt = f"{ent}, indexed to the pre-event mean: the index is lowest at {v['trough_index']} on day {v['trough_day']} relative to the event"
        if "below_days" in v:
            txt += f"; {v['below_days']} days are below 90% of baseline"
        if "recovery_day" in v:
            txt += f"; the series is back within range at day {v['recovery_day']}"
        if "n" in v:
            txt += f" (n = {v['n']} {unit_n})"
        return txt + "."
    if i == "lagged_peak":
        return (f"Rank correlation between {ent} and {ent2} is largest at lag {v['lag']} d "
                f"(rho {v['rho']}, n = {v['n']} d); every lag in the window was examined, "
                "so the peak is a selected value.")
    if i == "change_points":
        method = q.get("method") or "the method"
        cnt = v["count"]
        if cnt == "0":
            return f"{ent}: no change point under {method}."
        txt = f"{ent}: {cnt} change points under {method}"
        if "mean_before" in v:
            if not _ISO.match(q.get("date") or ""):
                return None
            txt += (f"; the first, on {q['date']}, separates a mean of {v['mean_before']}{_u(unit)} "
                    f"from {v['mean_after']}{_u(unit)}")
        return txt + "."
    if i == "distribution_groups":
        if not q.get("g0"):
            return None
        parts = [f"{q['g0']} median {v['g0_median']}{_u(unit)} (n = {v['g0_n']} d)"]
        if "g1_median" in v:
            if not q.get("g1"):
                return None
            parts.append(f"{q['g1']} median {v['g1_median']}{_u(unit)} (n = {v['g1_n']} d)")
        return f"{ent} by group: " + "; ".join(parts) + "."
    if i == "seasonality_profile":
        return (f"{ent}: the weekday profile runs from an index of {v['low_index']} "
                f"(day of week {v['low_dow']}) to {v['high_index']} (day of week {v['high_dow']}).")
    if i == "overlay_metrics":
        txt = f"Simulated series of {ent} beside the observed series over {v['n_overlap']} overlapping days"
        more = []
        if "rank_correlation" in v:
            more.append(f"rank correlation {v['rank_correlation']}")
        if "mean_abs_diff" in v:
            more.append(f"mean absolute difference {v['mean_abs_diff']}")
        return txt + (": " + ", ".join(more) if more else "") + ". Simulated values, shown beside observed values."
    if i == "scene_scan_found":
        mode = q.get("mode") or "recorded"
        txt = f"The detector found {v['found_total']} objects across {v['scene_count']} scenes ({mode})"
        if "not_counted" in v:
            txt += f"; {v['not_counted']} scenes were not counted"
        return txt + ". Modelled detector output, not a vessel count."
    return None


# ------------------------------------------------------------ suggesting claims
def series_units(entries: list[dict]) -> dict:
    """{series key: unit} from the assembly entries of a session."""
    out: dict = {}
    for e in entries:
        st = e.get("strings") or {}
        for p, v in st.items():
            m = _SERIES_KEY.match(p)
            if m and st.get(f"series[{m.group(1)}].unit"):
                out.setdefault(v, st[f"series[{m.group(1)}].unit"])
    return out


def suggest_claims(entry: dict, units: dict | None = None) -> list[dict]:
    """Claims that copy exact figures from one evidence entry (``id``, ``kind``,
    ``numbers``, ``strings``). Used by the recorded writer, which narrates and
    never computes. ``units``: {series key: unit} (see ``series_units``). Every
    claim returned passes ``check_claim`` against the same entries."""
    units = units or {}
    st = entry.get("strings") or {}
    kind, eid = entry.get("kind"), entry.get("id")
    out: list[dict] = []

    def build(tid, ent="", ent2="", prefix="", direction="", unit="", skip=()):
        t = TEMPLATES[tid]
        vals, have = [], set()
        for s in t.slots:
            if s.name in skip:
                continue
            v = _first(entry, s.tail, prefix)
            if v is None:
                if s.required:
                    return
                continue
            vals.append({"slot": s.name, "value": v})
            have.add(s.name)
        for g in {s.group for s in t.slots if s.group}:
            members = [s.name for s in t.slots if s.group == g]
            if not all(m in have for m in members):
                vals = [x for x in vals if x["slot"] not in members]
        out.append({"template_id": tid, "metric": t.metric, "entity": ent, "entity2": ent2,
                    "direction": direction or "none", "unit": unit, "evidence": [eid], "values": vals})

    if kind == "assemble":
        build("assembly_window")
        if (_num(entry, "scenes.listed") or 0) > 0:
            build("assembly_scenes")
        for p, v in st.items():
            m = _SERIES_KEY.match(p)
            if m:
                i = m.group(1)
                build("series_summary", ent=v, prefix=f"series[{i}].", unit=st.get(f"series[{i}].unit", ""))
    elif kind == "before_after":
        series = st.get("inputs.series", "")
        build("before_after_means", ent=series, unit=units.get(series, ""))
        ch = _first(entry, ("estimate.diff_mean", "estimate.delta"), "")
        if ch is not None:
            build("before_after_change", ent=series, direction=_sign(ch), unit=units.get(series, ""))
        pc = _num(entry, "estimate.pct_change")
        if pc is not None:
            build("before_after_pct", ent=series, direction=_sign(pc), unit="%")
    elif kind == "event_study":
        build("event_study_trough", ent=st.get("inputs.series", ""))
    elif kind == "change_points":
        series = st.get("inputs.series", "")
        none = _num(entry, "estimate.point_count") == 0
        build("change_points", ent=series, unit=units.get(series, ""),
              skip=("mean_before", "mean_after") if none else ())
    elif kind == "distribution":
        series = st.get("inputs.series", "")
        build("distribution_groups", ent=series, unit=units.get(series, ""))
    elif kind == "seasonality":
        build("seasonality_profile", ent=st.get("inputs.series", ""))
    elif kind == "lagged_correlation":
        build("lagged_peak", ent=st.get("inputs.x", ""), ent2=st.get("inputs.y", ""))
    elif kind == "model_overlay":
        build("overlay_metrics", ent=st.get("estimate.run_id", ""))
    elif kind == "scene_scan":
        build("scene_scan_found")
    return out


def suggest_for_all(entries: list[dict]) -> list[dict]:
    units = series_units(entries)
    out: list[dict] = []
    # analyses first (they carry the findings), then the assembly's window and series
    for e in sorted(entries, key=lambda x: x.get("kind") == "assemble"):
        out += suggest_claims(e, units)
    return out
