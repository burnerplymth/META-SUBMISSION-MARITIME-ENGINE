"""Figures for the brief, drawn by the existing chart writers (reports.charts)
as SVG: the longest modelled delays by place (always), the lane planner's
low / median / high as range bars (when a lane plan is served) and the
Monte Carlo band (when a job is served). Every figure carries its chip."""
from __future__ import annotations

from . import fmt

try:
    from reports.charts import normalise, render_svg
    from reports.charts import catalogue as _cat
except ImportError:  # imported as server.* from the repo root
    from server.reports.charts import normalise, render_svg  # type: ignore
    from server.reports.charts import catalogue as _cat  # type: ignore


def _draw(spec: dict, fid: str) -> dict | None:
    try:
        norm = normalise(spec)
        svg = render_svg(norm, embed=True, uid=f"brief-{fid}")
    except Exception:
        return None
    return {"id": fid, "title": norm["title"], "chip": norm["chip"], "svg": svg}


def chain_delays(bundle) -> dict | None:
    try:
        spec = _cat.delay_by_place(_cat.BundleSource(bundle), caps={"delay_rows": 8})
    except Exception:
        return None
    if not any(s["points"] for s in spec["series"]):
        return None
    spec["title"] = "Longest modelled delay by place"
    return _draw(spec, "chain_delays")


def lane_ranges(routes: list[dict], plan_label: str) -> dict | None:
    if not routes:
        return None
    series = []
    for name, key in (("Low", "low"), ("Median", "median"), ("High", "high")):
        series.append({"name": name, "source": f"Lane planner, {plan_label}, total at arrival",
                       "unit": "days",
                       "points": [{"x": r["label"], "y": r["total"][key]} for r in routes]})
    spec = {"type": "bar", "chip": "SIMULATED",
            "title": "Total days at arrival by route: low, median, high",
            "subtitle": f"{plan_label}; named speed and queue variants",
            "series": series,
            "axes": {"y": {"label": "Days from departure", "unit": "days", "min": 0}},
            "options": {"orientation": "v" if len(routes) <= 4 else "h"},
            "note": "SIMULATED: spread of named variants; not a forecast."}
    return _draw(spec, "lane_ranges")


def mc_band(series: dict | None, job_id: str) -> dict | None:
    if not isinstance(series, dict):
        return None
    t = series.get("t_days")
    places = [p for p in (series.get("places") or []) if isinstance(p, dict)]
    if not t or not places:
        return None
    p0 = places[0]
    name = fmt.clean_text(p0.get("name") or p0.get("place"), 60)
    out = []
    for label, key in (("p10", "p10"), ("p50 (median)", "p50"), ("p90", "p90")):
        vals = p0.get(key)
        if not isinstance(vals, list) or len(vals) != len(t):
            return None
        pts = [{"x": float(x), "y": float(y)} for x, y in zip(t, vals)
               if isinstance(x, (int, float)) and isinstance(y, (int, float))]
        out.append({"name": label, "source": f"Monte Carlo {job_id}", "unit": "days", "points": pts})
    spec = {"type": "line", "chip": "SIMULATED",
            "title": f"Simulated delay at {name}: p10, median and p90",
            "subtitle": "Delay the chain has reached at the place, by day, over all simulations",
            "series": out,
            "axes": {"x": {"label": "Days after the disruption starts", "unit": "days", "kind": "number"},
                     "y": {"label": "Delay at the place", "unit": "days", "min": 0}},
            "note": "SIMULATED: seeded input-uncertainty simulation; not a forecast."}
    return _draw(spec, "mc_band")
