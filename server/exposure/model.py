"""Request validation for the shipment exposure routes.

Closed schema, strict types, hard caps. Nothing here reads or writes disk. Strings that can reach a CSV
or a spreadsheet are cleaned by ``clean_text``: control characters removed, whitespace collapsed, and any
leading = + - @ TAB CR stripped (the formula-injection guard of the exports pipeline, ``neutralise``, is
applied again when a file is written).
"""
from __future__ import annotations

import math
import re
from datetime import date, datetime

MAX_SHIPMENTS = 500
MAX_LANES = 60
MAX_ID = 64
MAX_TEXT = 120
MAX_UNIT = 16
MAX_NUMBER = 1.0e12
MAX_DEPTH = 6
MODES = ("sea", "air", "ground")
SHIPMENT_KEYS = frozenset({"id", "origin", "destination", "mode", "etd", "eta", "value_usd", "quantity", "unit", "via"})
SCENARIO_KEYS = frozenset({"scenario_id", "disruption", "duration_d", "capacity_loss"})
DISRUPTION_KEYS = frozenset({"kind", "place", "duration_d", "capacity_loss"})
BODY_KEYS = frozenset({"shipments", "scenario", "mc_job_id", "format"})
KINDS = ("closure", "capacity_cut", "congestion", "strike", "weather", "grounding", "accident", "policy", "other")
MC_ID = re.compile(r"^mc_[0-9a-f]{8,64}$")

_CTRL = re.compile(r"[\x00-\x1f\x7f-\x9f  ﻿​-‏‪-‮⁦-⁩]")
_LEAD = "=+-@\t\r  "


class Invalid(Exception):
    def __init__(self, code: str, message: str, status: int = 422, **extra):
        super().__init__(message)
        self.code, self.message, self.status, self.extra = code, message, status, extra


def clean_text(value: str) -> str:
    """Control characters removed, whitespace collapsed, leading = + - @ TAB CR stripped."""
    t = _CTRL.sub(" ", str(value))
    t = " ".join(t.split())
    while t and t[0] in _LEAD:
        t = t[1:].lstrip()
    return t


def depth_of(obj, limit: int = MAX_DEPTH) -> int:
    """Nesting depth of parsed JSON; stops counting past ``limit`` + 1."""
    stack = [(obj, 1)]
    deepest = 1
    while stack:
        cur, d = stack.pop()
        deepest = max(deepest, d)
        if deepest > limit:
            return deepest
        if isinstance(cur, dict):
            stack.extend((v, d + 1) for v in cur.values() if isinstance(v, (dict, list)))
        elif isinstance(cur, list):
            stack.extend((v, d + 1) for v in cur if isinstance(v, (dict, list)))
    return deepest


def _text(v, where: str, maxlen: int, required: bool = True):
    if v is None:
        if required:
            raise Invalid("invalid_request", f"{where} is required")
        return None
    if not isinstance(v, str):
        raise Invalid("invalid_request", f"{where} must be a string")
    if len(v) > maxlen * 2:
        raise Invalid("invalid_request", f"{where} is longer than {maxlen} characters")
    t = clean_text(v)
    if len(t) > maxlen:
        raise Invalid("invalid_request", f"{where} is longer than {maxlen} characters")
    if not t:
        if required:
            raise Invalid("invalid_request", f"{where} is empty")
        return None
    return t


def _num(v, where: str, lo=0.0, hi=MAX_NUMBER, required: bool = False):
    if v is None:
        if required:
            raise Invalid("invalid_request", f"{where} is required")
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise Invalid("invalid_request", f"{where} must be a number")
    f = float(v)
    if not math.isfinite(f):
        raise Invalid("invalid_request", f"{where} must be a finite number")
    if f < lo or f > hi:
        raise Invalid("invalid_request", f"{where} must be between {lo:g} and {hi:g}")
    return f


def _date(v, where: str):
    if v is None:
        return None
    if not isinstance(v, str) or len(v) > 40:
        raise Invalid("invalid_request", f"{where} must be an ISO-8601 date string")
    s = v.strip()
    if not s:
        return None
    try:
        if len(s) == 10:
            date.fromisoformat(s)
        else:
            datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        raise Invalid("invalid_request", f"{where} is not a valid ISO-8601 date") from None
    return s


def parse_shipments(rows) -> list[dict]:
    if not isinstance(rows, list):
        raise Invalid("invalid_request", "shipments must be a list")
    if not rows:
        raise Invalid("invalid_request", "shipments is empty")
    if len(rows) > MAX_SHIPMENTS:
        raise Invalid("too_many_shipments", f"at most {MAX_SHIPMENTS} shipments per request", status=413)
    out, seen = [], set()
    for i, r in enumerate(rows):
        w = f"shipments[{i}]"
        if not isinstance(r, dict):
            raise Invalid("invalid_request", f"{w} must be an object")
        bad = sorted(set(r) - SHIPMENT_KEYS)
        if bad:
            raise Invalid("invalid_request", f"{w} has unknown fields: {', '.join(str(b)[:24] for b in bad[:5])}")
        sid = _text(r.get("id"), f"{w}.id", MAX_ID)
        if sid in seen:
            raise Invalid("duplicate_id", f"{w}.id repeats an earlier id")
        seen.add(sid)
        mode = r.get("mode", "sea")
        if mode not in MODES:
            raise Invalid("invalid_request", f"{w}.mode must be sea, air or ground")
        out.append({
            "id": sid,
            "origin": _text(r.get("origin"), f"{w}.origin", MAX_TEXT),
            "destination": _text(r.get("destination"), f"{w}.destination", MAX_TEXT),
            "mode": mode,
            "etd": _date(r.get("etd"), f"{w}.etd"),
            "eta": _date(r.get("eta"), f"{w}.eta"),
            "value_usd": _num(r.get("value_usd"), f"{w}.value_usd"),
            "quantity": _num(r.get("quantity"), f"{w}.quantity"),
            "unit": _text(r.get("unit"), f"{w}.unit", MAX_UNIT, required=False),
            "via": _text(r.get("via"), f"{w}.via", MAX_TEXT, required=False),
        })
    return out


def parse_scenario(sc) -> dict:
    """{'mode': 'none'} | {'mode': 'library', ...} | {'mode': 'explicit', ...}; values validated here, places later."""
    if sc is None or sc == {}:
        return {"mode": "none"}
    if not isinstance(sc, dict):
        raise Invalid("invalid_request", "scenario must be an object")
    bad = sorted(set(sc) - SCENARIO_KEYS)
    if bad:
        raise Invalid("invalid_request", f"scenario has unknown fields: {', '.join(str(b)[:24] for b in bad[:5])}")
    sid, dis = sc.get("scenario_id"), sc.get("disruption")
    if sid is not None and dis is not None:
        raise Invalid("invalid_request", "give scenario_id or disruption, not both")
    if sid is not None:
        if not isinstance(sid, str) or not re.fullmatch(r"[a-z0-9\-]{1,64}", sid):
            raise Invalid("invalid_request", "scenario_id must be a scenario id")
        out = {"mode": "library", "scenario_id": sid}
        for k, hi in (("duration_d", 3650.0), ("capacity_loss", 1.0)):
            v = _num(sc.get(k), f"scenario.{k}", lo=1e-9, hi=hi)
            if v is not None:
                out[k] = v
        return out
    if dis is None:
        if "duration_d" in sc or "capacity_loss" in sc:
            raise Invalid("invalid_request", "scenario.duration_d needs a scenario_id")
        return {"mode": "none"}
    if not isinstance(dis, dict):
        raise Invalid("invalid_request", "scenario.disruption must be an object")
    bad = sorted(set(dis) - DISRUPTION_KEYS)
    if bad:
        raise Invalid("invalid_request", f"scenario.disruption has unknown fields: {', '.join(str(b)[:24] for b in bad[:5])}")
    kind = dis.get("kind")
    if kind not in KINDS:
        raise Invalid("invalid_request", "scenario.disruption.kind is not a known kind")
    place = _text(dis.get("place"), "scenario.disruption.place", MAX_TEXT)
    dur = _num(dis.get("duration_d"), "scenario.disruption.duration_d", lo=1e-9, hi=3650.0, required=True)
    cap = _num(dis.get("capacity_loss"), "scenario.disruption.capacity_loss", lo=1e-9, hi=1.0)
    out = {"mode": "explicit", "kind": kind, "place": place, "duration_d": dur}
    if cap is not None:
        out["capacity_loss"] = cap
    return out


MAX_RESOLVE = 500            # was 1000: one fuzzy miss costs ~17 ms, so 1000 misses was a 17 s request
RESOLVE_POOLS = ("sea", "air", "ground", "place", "via")


def parse_resolve(body) -> list[dict]:
    """Request of POST /exposure/resolve: {'items': [{'text', 'mode'?}]} with mode one of sea, air, ground, via."""
    if not isinstance(body, dict):
        raise Invalid("invalid_request", "request body must be a JSON object")
    bad = sorted(set(body) - {"items"})
    if bad:
        raise Invalid("invalid_request", f"unknown fields: {', '.join(str(b)[:24] for b in bad[:5])}")
    rows = body.get("items")
    if not isinstance(rows, list) or not rows:
        raise Invalid("invalid_request", "items must be a non-empty list")
    if len(rows) > MAX_RESOLVE:
        raise Invalid("too_many_items", f"at most {MAX_RESOLVE} items per request", status=413)
    out = []
    for i, r in enumerate(rows):
        w = f"items[{i}]"
        if not isinstance(r, dict):
            raise Invalid("invalid_request", f"{w} must be an object")
        bad = sorted(set(r) - {"text", "mode"})
        if bad:
            raise Invalid("invalid_request", f"{w} has unknown fields: {', '.join(str(b)[:24] for b in bad[:5])}")
        mode = r.get("mode", "sea")
        if mode not in ("sea", "air", "ground", "via"):
            raise Invalid("invalid_request", f"{w}.mode must be sea, air, ground or via")
        out.append({"text": _text(r.get("text"), f"{w}.text", MAX_TEXT), "mode": mode})
    return out


def parse_body(body, allow_format: bool = False) -> dict:
    if not isinstance(body, dict):
        raise Invalid("invalid_request", "request body must be a JSON object")
    if depth_of(body) > MAX_DEPTH:
        raise Invalid("invalid_request", "request body is nested too deeply")
    bad = sorted(set(body) - BODY_KEYS)
    if bad:
        raise Invalid("invalid_request", f"unknown fields: {', '.join(str(b)[:24] for b in bad[:5])}")
    mc = body.get("mc_job_id")
    if mc is not None and (not isinstance(mc, str) or not MC_ID.match(mc)):
        raise Invalid("invalid_request", "mc_job_id must be a Monte Carlo job id")
    fmt = body.get("format")
    if allow_format:
        if fmt not in ("csv", "xlsx"):
            raise Invalid("invalid_request", "format must be csv or xlsx")
    elif fmt is not None:
        raise Invalid("invalid_request", "unknown fields: format")
    return {"shipments": parse_shipments(body.get("shipments")), "scenario": parse_scenario(body.get("scenario")),
            "mc_job_id": mc, "format": fmt}
