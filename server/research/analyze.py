"""POST /research/analyze: descriptive statistics over a stored assembly.

Seven kinds. Every result carries the
verbatim label, ``n``, ``caveat_chips`` and, where n is adequate, a seeded bootstrap interval. No
p-values, no causal or predictive wording.
"""
from __future__ import annotations

import json
import math
from datetime import date, timedelta
from pathlib import Path

from . import stats as S
from .util import (ID_RE, LABEL_ANALYSIS, LABEL_OVERLAY, MAX_ANALYSES, SERIES_KEY_RE, ResearchError,
                   check_depth, only_keys, parse_day)

KINDS = ("event_study", "before_after", "lagged_correlation", "change_points", "distribution",
         "seasonality", "model_overlay")
_ALLOWED = {
    "event_study": {"kind", "series", "event_ids", "category", "window_days"},
    "before_after": {"kind", "series", "split", "event_id", "window_days"},
    "lagged_correlation": {"kind", "x", "y", "max_lag_days", "method"},
    "change_points": {"kind", "series", "method", "min_size"},
    "distribution": {"kind", "series", "by"},
    "seasonality": {"kind", "series"},
    "model_overlay": {"kind", "run_or_scenario", "series"},
}


class _Err(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def _r4(x):
    return None if x is None else round(x, 4)


class Analyzer:
    def __init__(self, bundle, cards_dir_fn):
        self.bundle = bundle
        self.cards_dir_fn = cards_dir_fn       # callable -> list[Path] of dirs holding stored realism cards

    # ------------------------------------------------------------------ api
    def analyze(self, asm: dict, body) -> dict:
        if not isinstance(body, dict):
            raise ResearchError("invalid_request", "body must be a JSON object")
        check_depth(body)
        only_keys(body, {"assembly_id", "analyses"}, "request")
        specs = body.get("analyses")
        if not isinstance(specs, list) or not 1 <= len(specs) <= MAX_ANALYSES:
            raise ResearchError("invalid_request", f"analyses must hold 1 to {MAX_ANALYSES} items")
        results = []
        for i, spec in enumerate(specs):
            kind = spec.get("kind") if isinstance(spec, dict) else None
            rid = f"an_{asm['assembly_id'][4:12]}_{i}_{kind if kind in KINDS else 'unknown'}"
            try:
                if kind not in KINDS:
                    raise _Err("invalid_request", f"kind must be one of {list(KINDS)}")
                only = _ALLOWED[kind]
                extra = sorted(set(spec) - only)
                if extra:
                    raise _Err("invalid_request", f"unknown field(s) for {kind}: {extra[:5]}")
                res = getattr(self, "_" + kind)(asm, spec)
                res = {"id": rid, "kind": kind, "label": LABEL_ANALYSIS, **res}
                if kind == "model_overlay":
                    res["label"] = LABEL_ANALYSIS + " " + LABEL_OVERLAY
            except _Err as exc:
                res = {"id": rid, "kind": kind if isinstance(kind, str) else None, "label": LABEL_ANALYSIS,
                       "error": {"code": exc.code, "message": exc.message}}
            results.append(res)
        return {"assembly_id": asm["assembly_id"], "label": LABEL_ANALYSIS, "results": results}

    # -------------------------------------------------------------- helpers
    @staticmethod
    def _series(asm, key):
        if not isinstance(key, str) or not SERIES_KEY_RE.match(key):
            raise _Err("invalid_request", "series must be a key like 'portwatch_transits:chk_suez'")
        s = asm["timeline"]["series"].get(key)
        if s is None:
            raise _Err("series_not_found", f"{key!r} is not in this assembly")
        if not s["values"]:
            raise _Err("series_not_served", f"{key!r} is not served")
        return s

    @staticmethod
    def _window(spec, default):
        w = spec.get("window_days", default)
        if (not isinstance(w, list) or len(w) != 2 or any(isinstance(v, bool) or not isinstance(v, int) for v in w)
                or not -120 <= w[0] <= 365 or not -120 <= w[1] <= 365 or w[0] >= w[1]):
            raise _Err("invalid_request", "window_days must be [low, high] integers within -120..365, low < high")
        return w

    @staticmethod
    def _idx(asm, iso):
        t = asm["timeline"]["t"]
        try:
            return t.index(iso[:10])
        except ValueError:
            return None

    def _events(self, asm, spec):
        evs = asm.get("events", [])
        if spec.get("event_ids") is not None:
            ids = spec["event_ids"]
            if not isinstance(ids, list) or not ids or len(ids) > 32 or not all(
                    isinstance(x, str) and ID_RE.match(x) for x in ids):
                raise _Err("invalid_request", "event_ids must be a non-empty list of event ids")
            by = {e["id"]: e for e in evs}
            missing = [i for i in ids if i not in by]
            if missing:
                raise _Err("event_not_found", f"not in this assembly: {missing[:3]}")
            return [by[i] for i in ids]
        cat = spec.get("category")
        if cat is None:
            raise _Err("invalid_request", "give event_ids or category")
        sel = [e for e in evs if e["category"] == cat]
        if not sel:
            raise _Err("event_not_found", f"no events of category {cat!r} in this assembly")
        return sel

    @staticmethod
    def _gaps_chip(values, chips):
        if any(v is None for v in values):
            chips.append("gaps_in_series")

    # ---------------------------------------------------------- event study
    def _event_study(self, asm, spec):
        s = self._series(asm, spec.get("series"))
        lo, hi = self._window(spec, [-14, 28])
        if lo >= 0:
            raise _Err("invalid_request", "window_days[0] must be negative: the study needs pre-event days")
        events = self._events(asm, spec)
        vals = s["values"]
        n = len(vals)
        b_lo = max(lo, -14)
        per_event, skipped = [], []
        for e in events:
            i0 = self._idx(asm, e["t"])
            if i0 is None:
                skipped.append({"event_id": e["id"], "reason": "event_outside_window"})
                continue
            base = [vals[i0 + o] for o in range(b_lo, 0) if 0 <= i0 + o < n and vals[i0 + o] is not None]
            if len(base) < 5:
                skipped.append({"event_id": e["id"], "reason": "fewer_than_5_baseline_days"})
                continue
            bm = S.mean(base)
            if not bm or bm <= 0:
                skipped.append({"event_id": e["id"], "reason": "baseline_mean_not_positive"})
                continue
            path = [[o, vals[i0 + o] / bm] for o in range(lo, hi + 1)
                    if 0 <= i0 + o < n and vals[i0 + o] is not None]
            per_event.append({"event_id": e["id"], "t0": e["t"][:10], "n_obs": len(path),
                              "baseline_mean": S._r(bm), "baseline_range": [S._r(min(base) / bm), S._r(max(base) / bm)],
                              "path": [[o, _r4(v)] for o, v in path], "_raw": path})
        chips = ["no_control_series"]
        self._gaps_chip(vals, chips)
        used = len(per_event)
        if used == 0:
            raise _Err("insufficient_data", "no event had a usable baseline: " + json.dumps(skipped[:3]))
        offs = range(lo, hi + 1)
        mean_path = []
        for o in offs:
            xs = [dict(pe["_raw"]).get(o) for pe in per_event]
            xs = [x for x in xs if x is not None]
            if xs:
                mean_path.append([o, S.mean(xs), len(xs)])
        post = [(o, m) for o, m, _ in mean_path if o >= 0]
        trough = min(post, key=lambda p: (p[1], p[0])) if post else None
        below = sum(1 for o, m in post if m < 0.9)
        recovery = None
        if trough:
            mp = {o: m for o, m, _ in mean_path}
            for o in range(trough[0] + 1, hi - 1):
                w = [mp.get(o + k) for k in range(3)]
                if all(x is not None for x in w) and S.mean(w) >= 0.95:
                    recovery = o
                    break
        ci, bands = None, []
        lo_b = min(pe["baseline_range"][0] for pe in per_event)
        hi_b = max(pe["baseline_range"][1] for pe in per_event)
        bands.append({"id": "baseline_range", "label": "Pre-event range (min to max)",
                      "points": [[o, lo_b, hi_b] for o in (lo, hi)]})
        if used >= S.MIN_N_EVENTS_CI:
            r = S.rng(S.SEED, "event_study", used, lo, hi)
            draws = {o: [] for o, _, _ in mean_path}
            maps = [dict(pe["_raw"]) for pe in per_event]
            for _ in range(S.RESAMPLES):
                pick = [maps[r.randrange(used)] for _ in range(used)]
                for o in draws:
                    xs = [m[o] for m in pick if o in m]
                    if xs:
                        draws[o].append(S.mean(xs))
            lo_pts, hi_pts = [], []
            for o in draws:
                iv = S._percentile_interval(draws[o], S.LEVEL)
                if iv:
                    lo_pts.append([o, _r4(iv[0]), _r4(iv[1])])
            bands.append({"id": "mean_ci", "label": "95% bootstrap interval over events", "points": lo_pts})
            ci = {"level": S.LEVEL, "method": "bootstrap over events", "resamples": S.RESAMPLES,
                  "seed": S.SEED, "pointwise": lo_pts}
        else:
            chips.append("insufficient_n")
        chips.append("single_event" if used == 1 else "few_events" if used < S.MIN_N_EVENTS_CI else "multiple_events")
        for pe in per_event:
            pe.pop("_raw")
        return {
            "inputs": {"series": spec["series"], "window_days": [lo, hi],
                       "event_ids": [e["id"] for e in events], "baseline_window_days": [b_lo, -1]},
            "n": used, "n_unit": "events",
            "estimate": {"baseline_window_days": [b_lo, -1], "per_event": per_event, "skipped": skipped,
                         "mean_path": [[o, _r4(m)] for o, m, _ in mean_path],
                         "trough": ({"offset_day": trough[0], "index": _r4(trough[1])} if trough else None),
                         "days_below_90pct": below, "recovery_offset_day": recovery,
                         "index_definition": "value divided by the mean of the pre-event baseline days (1.0 = baseline)"},
            "ci": ci,
            "chart_spec": {"type": "line",
                           "x": {"field": "offset_day", "label": "Day relative to event", "unit": "days"},
                           "y": {"label": "Index (1.0 = pre-event mean)", "unit": "index"},
                           "series": [{"id": "mean", "label": "Mean across events" if used > 1 else "Observed",
                                       "kind": "observed", "points": [[o, _r4(m)] for o, m, _ in mean_path]}],
                           "bands": bands,
                           "markers": [{"x": 0, "label": "event", "kind": "event"}]},
            "caveat_chips": chips}

    # ---------------------------------------------------------- before / after
    def _before_after(self, asm, spec):
        s = self._series(asm, spec.get("series"))
        lo, hi = self._window(spec, [-28, 28])
        vals = s["values"]
        n = len(vals)
        split = spec.get("split")
        if split == "event":
            eid = spec.get("event_id")
            if not isinstance(eid, str) or not ID_RE.match(eid):
                raise _Err("invalid_request", "split 'event' needs event_id")
            ev = next((e for e in asm.get("events", []) if e["id"] == eid), None)
            if ev is None:
                raise _Err("event_not_found", f"{eid!r} is not in this assembly")
            split_iso = ev["t"][:10]
        elif isinstance(split, str):
            try:
                split_iso = parse_day(split, "split").isoformat()
            except Exception:
                raise _Err("invalid_request", "split must be 'event' or a YYYY-MM-DD date") from None
        else:
            raise _Err("invalid_request", "split must be 'event' or a YYYY-MM-DD date")
        i0 = self._idx(asm, split_iso)
        if i0 is None:
            raise _Err("split_outside_window", "the split date is outside the assembled window")
        if lo >= 0 or hi <= 0:
            raise _Err("invalid_request", "window_days must straddle the split (low < 0 < high)")
        before = [(o, vals[i0 + o]) for o in range(lo, 0) if 0 <= i0 + o < n and vals[i0 + o] is not None]
        after = [(o, vals[i0 + o]) for o in range(0, hi + 1) if 0 <= i0 + o < n and vals[i0 + o] is not None]
        b, a = [v for _, v in before], [v for _, v in after]
        if len(b) < 2 or len(a) < 2:
            raise _Err("insufficient_data", "fewer than 2 observations on one side of the split")
        est = S.before_after(b, a)
        chips = ["autocorrelated_daily_data", "no_control_series"]
        self._gaps_chip(vals, chips)
        ci = None
        if len(b) >= S.MIN_N_MEAN_CI and len(a) >= S.MIN_N_MEAN_CI:
            ci = S.ci_dict(S.diff_mean_block_ci(b, a), method="moving-block bootstrap (7-day blocks) of the difference in means",
                           extra={"statistic": "diff_mean"})
            chips.append("block_bootstrap")
        else:
            chips.append("insufficient_n")
        mb, ma = est["before"]["mean"], est["after"]["mean"]
        return {
            "inputs": {"series": spec["series"], "split": split_iso, "window_days": [lo, hi]},
            "n": len(b) + len(a), "n_unit": "days",
            "estimate": {"split": split_iso, "window_days": [lo, hi], **est,
                         "n_before": len(b), "n_after": len(a)},
            "ci": ci,
            "chart_spec": {"type": "line",
                           "x": {"field": "offset_day", "label": "Day relative to split", "unit": "days"},
                           "y": {"label": s["unit"], "unit": s["unit"]},
                           "series": [{"id": "observed", "label": "Observed", "kind": "observed",
                                       "points": [[o, v] for o, v in before + after]},
                                      {"id": "mean_before", "label": "Mean before", "kind": "baseline",
                                       "points": [[lo, mb], [-1, mb]]},
                                      {"id": "mean_after", "label": "Mean after", "kind": "baseline",
                                       "points": [[0, ma], [hi, ma]]}],
                           "markers": [{"x": 0, "label": "split", "kind": "split"}]},
            "caveat_chips": chips}

    # ------------------------------------------------------ lagged correlation
    def _lagged_correlation(self, asm, spec):
        sx, sy = self._series(asm, spec.get("x")), self._series(asm, spec.get("y"))
        ml = spec.get("max_lag_days", 14)
        if isinstance(ml, bool) or not isinstance(ml, int) or not 0 <= ml <= 30:
            raise _Err("invalid_request", "max_lag_days must be an integer 0..30")
        if spec.get("method", "spearman") != "spearman":
            raise _Err("invalid_request", "method must be 'spearman'")
        x, y = sx["values"], sy["values"]
        rows = S.lagged_spearman(x, y, ml)
        peak = S.peak_lag(rows)
        if peak is None:
            raise _Err("insufficient_data", "fewer than 10 overlapping days at every lag")
        n0 = next(r["n"] for r in rows if r["lag"] == 0)
        band = _r4(1.96 / math.sqrt(max(n0, 1)))
        chips = ["multiple_lags_examined", "data_dependent_selection", "autocorrelated_daily_data"]
        self._gaps_chip(x, chips)
        ci = None
        if peak["n"] >= S.MIN_N_CORR_CI:
            iv = S.lag_peak_block_ci(x, y, peak["lag"])
            ci = S.ci_dict(iv, method="moving-block bootstrap (7-day blocks) of Spearman rho at the peak lag",
                           extra={"statistic": "rho", "lag": peak["lag"]})
            chips.append("block_bootstrap")
        else:
            chips.append("insufficient_n")
        return {
            "inputs": {"x": spec["x"], "y": spec["y"], "max_lag_days": ml, "method": "spearman"},
            "n": peak["n"], "n_unit": "days",
            "estimate": {"method": "spearman", "by_lag": rows, "peak": peak,
                         "convention": "lag > 0 means y is shifted later than x (x leads y)",
                         "approx_white_noise_band": band,
                         "note": "association between two series at shifted days; not evidence that either one acts on the other"},
            "ci": ci,
            "chart_spec": {"type": "heatmap_lag",
                           "x": {"field": "lag_day", "label": "Lag (days, positive = y later than x)", "unit": "days"},
                           "y": {"label": "Spearman rho", "unit": "rho"},
                           "series": [{"id": "rho", "label": "Spearman rho", "kind": "observed",
                                       "points": [[r["lag"], r["rho"]] for r in rows]}],
                           "bands": [{"id": "white_noise", "label": "Approximate band for unrelated white noise",
                                      "points": [[-ml, -band, band], [ml, -band, band]]}],
                           "markers": [{"x": peak["lag"], "label": "largest |rho|", "kind": "peak"}]},
            "caveat_chips": chips}

    # ------------------------------------------------------------ change points
    def _change_points(self, asm, spec):
        s = self._series(asm, spec.get("series"))
        method = spec.get("method", "cusum")
        if method not in ("cusum", "pelt-lite"):
            raise _Err("invalid_request", "method must be 'cusum' or 'pelt-lite'")
        ms = spec.get("min_size", 7)
        if isinstance(ms, bool) or not isinstance(ms, int) or not 3 <= ms <= 120:
            raise _Err("invalid_request", "min_size must be an integer 3..120")
        t = asm["timeline"]["t"]
        pairs = [(i, v) for i, v in enumerate(s["values"]) if v is not None]
        xs = [float(v) for _, v in pairs]
        chips = ["change_points_are_segmentations", "data_dependent_selection"]
        self._gaps_chip(s["values"], chips)
        if len(xs) < 2 * ms:
            chips.append("insufficient_n")
            cps, param = [], None
        elif method == "cusum":
            cps, param = S.cusum_changes(xs, ms)
        else:
            cps, param = S.pelt_l2(xs, ms)
        bounds = [0] + cps + [len(xs)]
        segs = []
        for a, b in zip(bounds, bounds[1:]):
            seg = xs[a:b]
            segs.append({"from": t[pairs[a][0]], "to": t[pairs[b - 1][0]], "n": len(seg), "mean": S._r(S.mean(seg))})
        pts = []
        for k, c in enumerate(cps):
            pts.append({"index": c, "date": t[pairs[c][0]], "mean_before": segs[k]["mean"], "mean_after": segs[k + 1]["mean"]})
        return {
            "inputs": {"series": spec["series"], "method": method, "min_size": ms},
            "n": len(xs), "n_unit": "days",
            "estimate": {"method": method, "min_size": ms,
                         ("threshold" if method == "cusum" else "penalty"): S._r(param),
                         "rule": ("binary segmentation; a split is kept when its scaled CUSUM statistic exceeds the 95th "
                                  "percentile of the same statistic over 200 seeded permutations" if method == "cusum" else
                                  "PELT with a squared-error cost and penalty 2 sigma^2 ln(n), sigma from the MAD of first differences"),
                         "points": pts, "segments": segs},
            "ci": None,
            "chart_spec": {"type": "segments",
                           "x": {"field": "date", "label": "Date", "unit": "date"},
                           "y": {"label": s["unit"], "unit": s["unit"]},
                           "series": [{"id": "observed", "label": "Observed", "kind": "observed",
                                       "points": [[t[i], v] for i, v in pairs]}]
                                     + [{"id": f"segment_{k}", "label": f"Segment mean {k + 1}", "kind": "baseline",
                                         "points": [[g["from"], g["mean"]], [g["to"], g["mean"]]]}
                                        for k, g in enumerate(segs)],
                           "markers": [{"x": p["date"], "label": "change point", "kind": "change_point"} for p in pts]},
            "caveat_chips": chips}

    # ------------------------------------------------------------- distribution
    def _distribution(self, asm, spec):
        s = self._series(asm, spec.get("series"))
        by = spec.get("by", "none")
        if by not in ("month", "event", "none"):
            raise _Err("invalid_request", "by must be 'month', 'event' or 'none'")
        t, vals = asm["timeline"]["t"], s["values"]
        groups: dict[str, list[float]] = {}
        if by == "none":
            groups["all"] = [float(v) for v in vals if v is not None]
        elif by == "month":
            for d, v in zip(t, vals):
                if v is not None:
                    groups.setdefault(d[:7], []).append(float(v))
        else:
            inside = set()
            for e in asm.get("events", []):
                a = date.fromisoformat(e["t"][:10])
                b = date.fromisoformat((e["t_end"] or e["t"])[:10])
                if e["t_end"] is None:
                    b = a + timedelta(days=7)
                d = a
                while d <= b:
                    inside.add(d.isoformat())
                    d += timedelta(days=1)
            if not asm.get("events"):
                raise _Err("event_not_found", "this assembly has no events to split by")
            for d, v in zip(t, vals):
                if v is not None:
                    groups.setdefault("inside_event_window" if d in inside else "outside_event_windows", []).append(float(v))
        out = []
        chips = ["autocorrelated_daily_data"]
        self._gaps_chip(vals, chips)
        small = False
        for key in sorted(groups):
            xs = groups[key]
            g = {"key": key, **S.summary(xs), "histogram": S.histogram(xs), "ci": None}
            if len(xs) >= S.MIN_N_MEDIAN_CI:
                iv = S.bootstrap_ci(xs, S.median, S.rng(S.SEED, "dist", key, len(xs)))
                g["ci"] = S.ci_dict(iv, extra={"statistic": "median"})
            else:
                small = True
            out.append(g)
        if small:
            chips.append("insufficient_n")
        total = sum(len(v) for v in groups.values())
        if total == 0:
            raise _Err("insufficient_data", "the series has no observations")
        if by == "none":
            g = out[0]
            h = g["histogram"]
            mids = [[_r4((h["edges"][i] + h["edges"][i + 1]) / 2), c] for i, c in enumerate(h["counts"])] \
                if len(h["edges"]) > 2 else [[h["edges"][0], g["n"]]]
            chart = {"type": "histogram", "x": {"field": "value", "label": s["unit"], "unit": s["unit"]},
                     "y": {"label": "Days", "unit": "days"},
                     "series": [{"id": "days", "label": "Days", "kind": "observed", "points": mids}]}
        else:
            chart = {"type": "bar", "x": {"field": "group", "label": "Group", "unit": "group"},
                     "y": {"label": "Median " + s["unit"], "unit": s["unit"]},
                     "series": [{"id": "median", "label": "Median", "kind": "observed",
                                 "points": [[g["key"], g["median"]] for g in out]}],
                     "bands": [{"id": "iqr", "label": "Interquartile range",
                                "points": [[g["key"], g["q1"], g["q3"]] for g in out]}]}
        return {"inputs": {"series": spec["series"], "by": by}, "n": total, "n_unit": "days",
                "estimate": {"by": by, "groups": out}, "ci": None,
                "chart_spec": chart, "caveat_chips": chips}

    # -------------------------------------------------------------- seasonality
    def _seasonality(self, asm, spec):
        s = self._series(asm, spec.get("series"))
        t, vals = asm["timeline"]["t"], s["values"]
        pres = [(date.fromisoformat(d), float(v)) for d, v in zip(t, vals) if v is not None]
        if len(pres) < 14:
            raise _Err("insufficient_data", "fewer than 14 observations")
        overall = S.mean([v for _, v in pres])
        chips = ["autocorrelated_daily_data"]
        self._gaps_chip(vals, chips)
        wk = []
        for dow in range(7):
            xs = [v for d, v in pres if d.weekday() == dow]
            wk.append({"dow": dow, "n": len(xs), "mean": S._r(S.mean(xs)),
                       "index": _r4(S.mean(xs) / overall) if xs and overall else None})
        span = (pres[-1][0] - pres[0][0]).days
        month = None
        if span >= 730:
            month = []
            for m in range(1, 13):
                xs = [v for d, v in pres if d.month == m]
                month.append({"month": m, "n": len(xs), "mean": S._r(S.mean(xs)),
                              "index": _r4(S.mean(xs) / overall) if xs and overall else None})
        else:
            chips += ["insufficient_n", "weekday_pattern_only"]
        return {"inputs": {"series": spec["series"]}, "n": len(pres), "n_unit": "days",
                "estimate": {"weekday": wk, "month": month, "acf": S.acf(vals, 14),
                             "index_definition": "group mean divided by the overall mean (1.0 = no difference)",
                             "dow_convention": "0 = Monday"},
                "ci": None,
                "chart_spec": {"type": "bar", "x": {"field": "weekday", "label": "Day of week (0 = Monday)", "unit": "day"},
                               "y": {"label": "Index", "unit": "index"},
                               "series": [{"id": "weekday_index", "label": "Weekday index", "kind": "observed",
                                           "points": [[w["dow"], w["index"]] for w in wk]}]},
                "caveat_chips": chips}

    # ------------------------------------------------------------ model overlay
    def _cards(self):
        out = {}
        for d in self.cards_dir_fn():
            for p in sorted(Path(d).glob("real_*.json")):
                try:
                    c = json.loads(p.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if isinstance(c, dict) and c.get("card_id"):
                    out.setdefault(c["card_id"], c)
        return out

    def _model_overlay(self, asm, spec):
        key = spec.get("series")
        s = self._series(asm, key)
        place = s["place"]
        ref = spec.get("run_or_scenario")
        if not isinstance(ref, str) or not ID_RE.match(ref):
            raise _Err("invalid_request", "run_or_scenario must be a run id, a stored card id or an episode id")
        allowed: dict[str, str] = {}
        for ep in self.bundle.episodes():
            for cid in ep.get("sim_runs", []):
                allowed[cid] = ep["id"]
        cards = self._cards()
        card = None
        if ref in allowed and ref in cards:
            card = cards[ref]
        else:
            for cid, c in sorted(cards.items()):
                if cid in allowed and (c.get("run") or {}).get("run_id") == ref:
                    card = c
                    break
            if card is None:
                ep = next((e for e in self.bundle.episodes() if e["id"] == ref), None)
                if ep:
                    pref = sorted([cid for cid in ep.get("sim_runs", []) if cid in cards],
                                  key=lambda c: (cards[c]["run"].get("kind") != "sim", c))
                    card = cards[pref[0]] if pref else None
        if card is None:
            raise _Err("run_not_found", "no stored simulated run matches; only runs for library episodes are offered")
        ser = (card.get("series") or {}).get(place)
        if not ser or not ser.get("simulated"):
            raise _Err("no_overlay_series", f"the run has no simulated series for {place}")
        sim = {d: v for d, v in ser["simulated"]}
        t, vals = asm["timeline"]["t"], s["values"]
        obs_pts = [[d, v] for d, v in zip(t, vals) if v is not None]
        sim_pts = [[d, sim[d]] for d in t if d in sim and sim[d] is not None]
        both = [(v, sim[d]) for d, v in zip(t, vals) if v is not None and d in sim and sim[d] is not None]
        chips = ["simulated", "single_event"]
        m = {"n_overlap": len(both), "mean_abs_diff": None, "rank_correlation": None,
             "observed_mean": None, "simulated_mean": None}
        if both:
            o, sm = [a for a, _ in both], [b for _, b in both]
            m["observed_mean"], m["simulated_mean"] = S._r(S.mean(o)), S._r(S.mean(sm))
            m["mean_abs_diff"] = S._r(S.mean([abs(a - b) for a, b in both]))
        if len(both) >= 10:
            m["rank_correlation"] = S._r(S.spearman([a for a, _ in both], [b for _, b in both]))
        else:
            chips.append("insufficient_n")
        if not sim_pts:
            raise _Err("no_overlay_series", "the simulated series does not overlap the assembled window")
        metric = ((card.get("run") or {}).get("prediction_digest") or {}).get("metric")
        return {
            "inputs": {"series": key, "run_or_scenario": ref, "card_id": card["card_id"]},
            "n": len(both), "n_unit": "days",
            "estimate": {"run_id": (card.get("run") or {}).get("run_id"), "run_kind": (card.get("run") or {}).get("kind"),
                         "title": card.get("title"), "window": card.get("window"),
                         "simulated_metric": metric, "observed": obs_pts, "simulated": sim_pts,
                         "metrics": m, "label": LABEL_OVERLAY,
                         "note": "the simulated series may use a different unit than the observed one; compare shape, not level"},
            "ci": None,
            "chart_spec": {"type": "line", "x": {"field": "date", "label": "Date", "unit": "date"},
                           "y": {"label": s["unit"], "unit": s["unit"]},
                           "series": [{"id": "observed", "label": "Observed", "kind": "observed", "points": obs_pts},
                                      {"id": "simulated", "label": "SIMULATED", "kind": "simulated", "points": sim_pts}]},
            "caveat_chips": chips}
