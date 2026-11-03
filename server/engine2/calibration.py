"""ENGINE REV 2 CALIBRATION PROFILE ``rev2c``: data-derived plug-in values.

Replaces two TEMPORARY plug-ins of the default rev-2 registry by values derived
from cached, cited data (every parameter of the derivation is a registry entry in
plugins_cal.py, none is written here):

  * mu_max ratio per place from the IMF PortWatch daily window cached in
    data/calibration/portwatch_daily_window.json
    (ratio = quantile of the trailing 7-day mean / its median);
  * inventory cover of the Los Angeles / Long Beach terminals from the Port of
    Los Angeles dwell-time figure.

Pure functions of the cached file; no network, no RNG. CLI:
    PYTHONPATH=server python -m engine2.calibration --write
regenerates data/calibration/mu_max_ratios.json.
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

from engine.canonical import canonical_json, sha256_hex

from .plugins_cal import CAL_HASH, cal_plug

REPO_ROOT = Path(__file__).resolve().parents[2]
WINDOW_PATH = REPO_ROOT / "data" / "calibration" / "portwatch_daily_window.json"
RATIOS_PATH = REPO_ROOT / "data" / "calibration" / "mu_max_ratios.json"


def trailing_means(counts: list, width: int) -> list:
    """Means of every full run of ``width`` consecutive days without a gap."""
    out = []
    for i in range(width - 1, len(counts)):
        w = counts[i - width + 1:i + 1]
        if any(x is None for x in w):
            continue
        out.append(sum(w) / width)
    return out


def quantile(values: list, q: float) -> float:
    """Linear-interpolated quantile (q in [0, 1])."""
    s = sorted(values)
    k = (len(s) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def _series_for(place: dict, prm: dict):
    if place.get("layer") == "chokepoint":
        return place["counts"], prm["metric_chokepoint"].split("|")[0]
    cont = [x for x in place["container"] if x is not None]
    if cont and statistics.mean(cont) >= prm["container_min_calls_per_day"]:
        return place["container"], "portcalls_container"
    return place["all"], "portcalls"


def derive_ratios(window_doc: dict | None = None, prm: dict | None = None) -> dict:
    """{node_id: {ratio, raw_ratio, metric, n_points, median, peak, layer}} for
    every place whose series has enough valid trailing means; the rest are
    listed under ``unusable`` with a reason. Class fallbacks under ``fallback``."""
    prm = prm or cal_plug("mu_max_portwatch")
    bounds = cal_plug_bounds()
    doc = window_doc if window_doc is not None else json.loads(WINDOW_PATH.read_text(encoding="utf-8"))
    per, unusable = {}, {}
    for node, place in sorted(doc["places"].items()):
        if "error" in place:
            unusable[node] = place["error"]
            continue
        series, metric = _series_for(place, prm)
        tm = trailing_means(series, prm["mean_window_d"])
        if len(tm) < prm["min_points"]:
            unusable[node] = f"only {len(tm)} valid trailing means"
            continue
        med = statistics.median(tm)
        if med <= 0:
            unusable[node] = "median is zero"
            continue
        peak = quantile(tm, prm["quantile"])
        raw = peak / med
        per[node] = {"ratio": round(min(max(raw, bounds[0]), bounds[1]), 6),
                     "raw_ratio": round(raw, 6), "metric": metric, "n_points": len(tm),
                     "median": round(med, 6), "peak": round(peak, 6),
                     "layer": place["layer"], "portid": place.get("portid")}
    fallback = {}
    for layer in ("chokepoint", "port"):
        rs = [v["ratio"] for v in per.values() if v["layer"] == layer]
        if rs:
            fallback[layer] = round(statistics.median(rs), 6)
    return {"places": per, "unusable": unusable, "fallback": fallback,
            "window": doc["window"], "params": prm,
            "source_payload_sha256": doc.get("payload_sha256"),
            "retrieved_at": doc.get("retrieved_at")}


def cal_plug_bounds() -> list:
    from .plugins_cal import CAL_BY_NAME
    return CAL_BY_NAME["mu_max_portwatch"]["bounds"]


class Calibration:
    """What Engine2 consults under the rev2c profile."""

    profile = "rev2c"

    def __init__(self, derived: dict | None = None):
        self.derived = derived if derived is not None else derive_ratios()
        self.cover = dict(cal_plug("cover_days_sourced"))
        self.hash = CAL_HASH
        self.digest = sha256_hex(canonical_json([self.derived, self.cover]))

    def mu_ratio_for(self, node_id: str, node: dict, default: float) -> float:
        hit = self.derived["places"].get(node_id)
        if hit is not None:
            return float(hit["ratio"])
        if node.get("mode") == "sea":
            layer = "chokepoint" if node.get("kind") == "chokepoint" else "port"
            fb = self.derived["fallback"].get(layer)
            if fb is not None:
                return float(fb)
        return float(default)

    def cover_days_for(self, node_id: str, node: dict) -> float:
        if node_id in self.cover:
            return float(self.cover[node_id])
        return float(node["inventory_days"])

    def describe(self) -> dict:
        return {"profile": self.profile, "registry_hash": self.hash,
                "places_with_series": len(self.derived["places"]),
                "fallback": self.derived["fallback"],
                "source_payload_sha256": self.derived["source_payload_sha256"],
                "derived_digest": self.digest}


_DEFAULT: dict = {}


def default_calibration() -> Calibration:
    if "c" not in _DEFAULT:
        _DEFAULT["c"] = Calibration()
    return _DEFAULT["c"]


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    d = derive_ratios()
    text = json.dumps(d, indent=1, sort_keys=True) + "\n"
    if "--write" in argv:
        RATIOS_PATH.write_text(text, encoding="utf-8")
    print(json.dumps({"places": len(d["places"]), "unusable": d["unusable"],
                      "fallback": d["fallback"]}, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
