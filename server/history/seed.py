"""Seed the tracked history fixtures by REAL fetches (run once, online).

    cd server && ../.venv/bin/python -m history.seed [--only red_sea_2024] \\
        [--cache ../data/history_cache] [--data-dir <scratch data copy>]

Each seed pulls the observed daily series for the event's watch places,
the GDACS alert list for the window, and the EIA oil-price context, all
through the SAME providers the API uses, so the cache entries carry the
providers' own provenance headers. The request budget applies (it is
charged to ``--data-dir``'s counter, never to a tracked file). A source
that cannot be served is reported and skipped - nothing is invented.
Idempotent: a window already covered by a cache entry is not re-fetched.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from .providers.replay_catalogue import WATCH
from .service import HistoryService
from .util import parse_day

# id -> (observation window, gdacs window, places beyond WATCH, comtrade reporters)
SEEDS = {
    "red_sea_2024": {
        "obs": ("2023-08-01", "2024-06-30"), "gdacs": ("2023-08-01", "2024-06-30"),
        "places": ["chk_babelmandeb", "chk_suez", "chk_capeofgoodhope",
                   "chk_gibraltar", "chk_malacca", "chk_hormuz"],
        "eia": ("2023-08-01", "2024-06-30"), "comtrade": []},
    "panama_2023_24": {
        "obs": ("2023-08-01", "2024-06-30"), "gdacs": None,
        "places": ["chk_panama", "sea_balboa", "sea_colon", "chk_suez",
                   "chk_capeofgoodhope", "chk_malacca"],
        "eia": None, "comtrade": [("PAN", "trade_exports_usd"),
                                  ("EGY", "trade_exports_usd")]},
    "la_2021": {
        "obs": ("2021-08-01", "2022-01-31"), "gdacs": ("2021-08-01", "2022-01-31"),
        "places": ["sea_losangeles", "sea_oakland", "sea_nwsa", "sea_savannah",
                   "sea_newyork"],
        "eia": ("2021-08-01", "2022-01-31"), "comtrade": []},
    "ever_given": {
        "obs": ("2021-02-01", "2021-05-15"), "gdacs": ("2021-02-01", "2021-05-15"),
        "places": ["chk_suez", "chk_capeofgoodhope", "chk_malacca"],
        "eia": ("2021-02-01", "2021-05-15"), "comtrade": [],
        "weather": [("chk_suez", "wind_speed_10m_max", "2021-03-15", "2021-03-31")]},
}


def seed(hist: HistoryService, only: list[str] | None = None) -> dict:
    report: dict = {}
    for sid, cfg in SEEDS.items():
        if only and sid not in only:
            continue
        rep = {"observations": [], "gdacs": None, "eia": [], "comtrade": [],
               "weather": []}
        a, b = (parse_day(x) for x in cfg["obs"])
        for place in cfg["places"]:
            r = hist.portwatch.observations(place, "activity", a, b)
            rep["observations"].append({
                "place": place, "served": r.get("served"),
                "n": (r.get("coverage") or {}).get("n"),
                "missing": len((r.get("coverage") or {}).get("missing_dates", [])),
                "cache_hit": (r.get("cache") or {}).get("hit"),
                "reason": r.get("reason"), "detail": r.get("detail")})
        if cfg["gdacs"]:
            ga, gb = (parse_day(x) for x in cfg["gdacs"])
            r = hist.gdacs.events(ga, gb)
            rep["gdacs"] = {"served": r.get("served"),
                            "events": len(r.get("events", [])),
                            "unmapped": r.get("unmapped_count"),
                            "reason": r.get("reason"), "detail": r.get("detail")}
        if cfg["eia"]:
            ea, eb = (parse_day(x) for x in cfg["eia"])
            for sid_ in ("RBRTE", "RWTC"):
                r = hist.eia.observations("market", f"spot:{sid_}", ea, eb)
                rep["eia"].append({"series": sid_, "served": r.get("served"),
                                   "n": (r.get("coverage") or {}).get("n"),
                                   "reason": r.get("reason")})
        for iso, metric in cfg["comtrade"]:
            r = hist.comtrade.observations(iso, metric, date(2021, 1, 1),
                                           date(2024, 12, 31))
            rep["comtrade"].append({"reporter": iso, "served": r.get("served"),
                                    "n": (r.get("coverage") or {}).get("n"),
                                    "reason": r.get("reason"),
                                    "detail": r.get("detail")})
        for place, metric, wa, wb in cfg.get("weather", []):
            r = hist.openmeteo.observations(place, metric, parse_day(wa),
                                            parse_day(wb))
            rep["weather"].append({"place": place, "metric": metric,
                                   "served": r.get("served"),
                                   "n": (r.get("coverage") or {}).get("n"),
                                   "reason": r.get("reason"),
                                   "detail": r.get("detail")})
        report[sid] = rep
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", action="append")
    ap.add_argument("--cache", default=None, help="cache directory to fill")
    ap.add_argument("--data-dir", default=None,
                    help="data dir for the budget counter and the graph")
    args = ap.parse_args(argv)
    repo_data = Path(__file__).resolve().parents[2] / "data"
    data_dir = Path(args.data_dir) if args.data_dir else repo_data
    hist = HistoryService(
        data_dir=data_dir, cache_dir=args.cache or repo_data / "history_cache",
        graph_path=repo_data / "graph" / "graph.json", fetch_enabled=True,
        repo_data_dir=repo_data)
    print(json.dumps(seed(hist, args.only), indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
