"""VAL-01 lite: a held-out, observable benchmark (rule ``val01-v1``).

Caveat: la_2021 is both a TEST episode here and an in-sample replay episode (data/replay/la_2021.json) that the
engines were configured against, so its TEST result is not blind.

Inputs and rule are data: data/eval/val01/episodes.json and rule_val01-v1.json (hashed before
any TEST outcome was collected). This module is pure arithmetic over stored numbers
plus a collector that fetches IMF PortWatch daily series; no LLM at any step.

BACKTEST label: nothing here is a live Ledger number and nothing here is
merged with one.

Layout of data/eval/val01/:
  episodes.json, rule_val01-v1.json   pre-registered inputs
  train/<id>.json                     observed outcomes of TRAIN episodes
  lockbox/<id>.json, lockbox/MANIFEST.json   TEST outcomes + sealed manifest
  results_*.json                      published results
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

try:  # server/ on sys.path (house convention)
    from history.series_math import (baseline as series_baseline, mean,
                                     onset_day, pct_change, spearman,
                                     window_values)
    from history.util import add_days, iso_day, parse_day
    from engine.scorecard import clopper_pearson
except ImportError:  # pragma: no cover - repo-root identity
    from server.history.series_math import (  # type: ignore
        baseline as series_baseline, mean, onset_day, pct_change, spearman,
        window_values)
    from server.history.util import add_days, iso_day, parse_day  # type: ignore
    from server.engine.scorecard import clopper_pearson  # type: ignore

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data" / "eval" / "val01"
EPISODES_PATH = DATA_DIR / "episodes.json"
RULE_PATH = DATA_DIR / "rule_val01-v1.json"
GRAPH_PATH = REPO_ROOT / "data" / "graph" / "graph.json"

RULE_ID = "val01-v1"
LABEL = ("BACKTEST - observable PortWatch metrics on public disruption "
         "episodes. Not the live Ledger; these numbers never mix with "
         "Ledger grades.")
SPLIT_SEED = "val01-split-v1:"
TRAIN, TEST = "TRAIN", "TEST"
CONFIGS = ("v1", "rev2", "rev2_services", "rev2_calibrated", "rev2d")
SERVICES_ERAS = ["pre_red_sea_crisis"]       # same selection as the rev 2 scorecards

DIRECTIONS = ("down", "flat", "up")


# ------------------------------------------------------------------ plumbing
def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_rule(path: Path = RULE_PATH) -> dict:
    return load_json(path)


def load_episodes(path: Path = EPISODES_PATH) -> list[dict]:
    return load_json(path)["episodes"]


def split_of(episode_id: str) -> str:
    h = int(hashlib.sha256((SPLIT_SEED + episode_id).encode()).hexdigest()[:8], 16) % 100
    return TRAIN if h < 60 else TEST


def episode_dir(split: str, data_dir: Path = DATA_DIR) -> Path:
    return Path(data_dir) / ("lockbox" if split == TEST else "train")


def start_day(ep: dict) -> date:
    return parse_day(ep["started_at"])


def stored_window(ep: dict, rule: dict, fetch_from: str, fetch_to: str) -> tuple[date, date]:
    w = rule["windows"]
    lo = max(add_days(start_day(ep), -int(w["pre_days"])), parse_day(fetch_from))
    hi = min(add_days(start_day(ep), math.ceil(ep["duration_d"]) + int(w["post_days_after_duration"])),
             parse_day(fetch_to))
    return lo, hi


def smoothing_days(ep: dict) -> int:
    return int(min(7, max(3, math.ceil(ep["duration_d"]))))


def metric_for_layer(ep_metric: str, layer: str) -> str:
    """Episode metric ('transits' | 'port_calls' | 'port_calls_container')
    translated to the layer of the place being read."""
    container = ep_metric.endswith("_container")
    if layer == "chokepoint":
        return "transits_container" if container else "transits"
    return "port_calls_container" if container else "port_calls"


# ----------------------------------------------------------- kinds, regions
REGIONS = ("North America", "Latin America & Caribbean", "Europe",
           "Middle East & North Africa", "Sub-Saharan Africa", "Asia-Pacific")
PLACE_KINDS = ("sea_port", "chokepoint", "air_hub", "ground")
_KIND_OF = {"port": "sea_port", "chokepoint": "chokepoint", "air_hub": "air_hub",
            "ground_node": "ground"}
_CHOKE_REGION = {
    "chk_suez": REGIONS[3], "chk_babelmandeb": REGIONS[3], "chk_hormuz": REGIONS[3],
    "chk_panama": REGIONS[1], "chk_malacca": REGIONS[5], "chk_taiwan": REGIONS[5],
    "chk_turkish": REGIONS[2], "chk_gibraltar": REGIONS[2], "chk_dover": REGIONS[2],
    "chk_danish": REGIONS[2], "chk_capeofgoodhope": REGIONS[4], "chk_mozambique": REGIONS[4]}


def _names(text: str) -> set:
    return {s.strip() for s in text.split(";") if s.strip()}


_COUNTRY_REGION = {}
for _region, _countries in (
    (REGIONS[0], "United States; Canada; Bermuda"),
    (REGIONS[1], "Mexico; Argentina; Bolivia; Brazil; Chile; Colombia; Costa Rica; Ecuador; "
     "El Salvador; Guatemala; Honduras; Guyana; Panama; Peru; Suriname; Uruguay; Venezuela; "
     "Antigua and Barbuda; Aruba; Bahamas; The Bahamas; Barbados; Caribbean Netherlands; Curaçao; "
     "Dominican Republic; Guadeloupe; Haiti; Jamaica; Martinique; Puerto Rico; Sint Maarten; "
     "St.  Lucia; St.  Vincent and the Grenadines; Trinidad and Tobago; United States Virgin Islands"),
    (REGIONS[2], "Albania; Austria; Belarus; Belgium; Bosnia and Herzegovina; Bulgaria; Croatia; "
     "Cyprus; Denmark; Estonia; Finland; France; Germany; Greece; Hungary; Iceland; Ireland; Italy; "
     "Latvia; Lithuania; Luxembourg; Malta; Netherlands; The Netherlands; Norway; Poland; Portugal; "
     "Romania; Russia; Russian Federation; Slovenia; Spain; Sweden; Turkey; Türkiye; Ukraine; "
     "United Kingdom"),
    (REGIONS[3], "Algeria; Armenia; Azerbaijan; Bahrain; Djibouti; Egypt; Georgia; Iran; Iraq; "
     "Israel; Jordan; Kuwait; Lebanon; Libya; Morocco; Oman; Qatar; Saudi Arabia; Sudan; Tunisia; "
     "United Arab Emirates"),
    (REGIONS[4], "Angola; Benin; Botswana; Burkina Faso; Burundi; Cabo Verde; Cameroon; "
     "Côte d'Ivoire; Ethiopia; Gabon; Ghana; Guinea; Kenya; Mauritania; Mauritius; Mozambique; "
     "Namibia; Nigeria; Republic of Congo; Réunion; Senegal; Sierra Leone; South Africa; Tanzania; Togo"),
    (REGIONS[5], "Afghanistan; American Samoa; Australia; Bangladesh; Bhutan; Brunei; Brunei Darussalam; "
     "Cambodia; China; India; Indonesia; Japan; Kazakhstan; Korea; South Korea; Maldives; Malaysia; "
     "Mongolia; Myanmar; New Caledonia; New Zealand; Pakistan; Papua New Guinea; Philippines; "
     "Singapore; Sri Lanka; Taiwan; Taiwan Province of China; Thailand; Turkmenistan; Vietnam")):
    for _c in _names(_countries):
        _COUNTRY_REGION[_c] = _region


def kind_of(node: dict) -> str:
    return _KIND_OF.get((node or {}).get("kind"), "other")


def region_of(node: dict) -> str:
    """Region of a graph node: chokepoints by a fixed table, everything else
    by the node's country (first of 'A / B'). 'Unmapped' is a visible gap."""
    node = node or {}
    if node.get("id") in _CHOKE_REGION:
        return _CHOKE_REGION[node["id"]]
    country = str(node.get("country") or "").split(" / ")[0].strip()
    return _COUNTRY_REGION.get(country, "Unmapped")


def load_nodes(graph_path: Path = GRAPH_PATH) -> dict:
    return {n["id"]: n for n in load_json(graph_path)["nodes"]}


# ---------------------------------------------------------------- placebo
def episode_span(ep: dict) -> tuple[date, date]:
    """[start - 28 d, start + ceil(duration) + 60 d]: the window within which
    a registered episode can disturb a place (rule: placebo_windows.start)."""
    s = start_day(ep)
    return add_days(s, -28), add_days(s, math.ceil(ep["duration_d"]) + 60)


def placebo_start(ep: dict, all_eps: list, fetch_from: str, fetch_to: str) -> date | None:
    """The first of start-364, start+364, start-728, start+728 whose window
    [p-28, p+ceil(duration)+7] lies inside the fetch range (with the 35 days of
    context before it) and touches no registered episode's span. None when
    none qualifies."""
    s = start_day(ep)
    lo_bound, hi_bound = parse_day(fetch_from), parse_day(fetch_to)
    spans = [episode_span(e) for e in all_eps]
    n = math.ceil(ep["duration_d"])
    for off in (-364, 364, -728, 728):
        p = add_days(s, off)
        w_lo, w_hi = add_days(p, -28), add_days(p, n + 7)
        ctx_lo, ctx_hi = add_days(p, -35), add_days(p, max(31, n + 7))
        if ctx_lo < lo_bound or ctx_hi > hi_bound:
            continue
        if any(not (w_hi < a or w_lo > b) for a, b in spans):
            continue
        return p
    return None


def placebo_window(ep: dict, p: date) -> tuple[date, date]:
    n = math.ceil(ep["duration_d"])
    return add_days(p, -35), add_days(p, max(31, n + 7))


def shifted_episode(ep: dict, p: date) -> dict:
    return dict(ep, started_at=iso_day(p) + "T00:00:00Z")


# ------------------------------------------------------------- place choice
def select_places(book, ep: dict, rule: dict, all_eps: list | None = None) -> dict:
    """Universe U, controls and hop distances for one episode. Pure function
    of the graph and the pre-registered episodes: it never reads an outcome.
    ``all_eps``: every registered episode (a control may not be the seed
    or a co-affected place of ANY episode)."""
    seed = ep["seed"]
    blocked = set()
    for other in (all_eps if all_eps is not None else [ep]):
        blocked.add(other["seed"])
        blocked.update(other.get("co_affected", []))
    hops = book.hops_from(seed, 12)
    d = rule["metrics"]["d_downstream"]
    cand = sorted(((h, p) for p, h in hops.items()
                   if p != seed and book.portwatch_series(p)),
                  key=lambda x: (x[0], x[1]))
    hop_pick = [p for h, p in cand if h <= 2][:10]
    universe, skipped = [], []
    for p in ep.get("co_affected", []):
        if p == seed:
            continue
        if p not in book:
            skipped.append({"place": p, "reason": "not_a_graph_place"})
        elif not book.portwatch_series(p):
            skipped.append({"place": p, "reason": "no_portwatch_series"})
        elif p not in [u["place"] for u in universe]:
            universe.append({"place": p, "role": "co_affected", "hops": hops.get(p)})
    for p in hop_pick:
        if p not in [u["place"] for u in universe]:
            universe.append({"place": p, "role": "hop", "hops": hops.get(p)})
    uni_ids = {u["place"] for u in universe}
    near = {p for p, h in hops.items() if h <= 2}
    excl = set(ep.get("exclude_controls", []))
    controls = []
    for p in rule["metrics"]["e_controls"]["pool"]:
        if p in uni_ids or p in near or p in excl or p in blocked:
            continue
        if p in book and book.portwatch_series(p):
            controls.append({"place": p, "role": "control", "hops": hops.get(p)})
    return {"seed": {"place": seed, "role": "seed", "hops": 0},
            "universe": universe, "controls": controls,
            "skipped_co_affected": skipped}


# --------------------------------------------------------------- collection
def episode_doc(ep: dict, rule: dict, places: dict, observations: dict,
                *, fetch_from: str, fetch_to: str, collected_at: str,
                episodes_sha256: str, rule_sha256: str) -> dict:
    """Build the stored outcome file of one episode from provider results
    (``observations``: place -> HistoryService.observations() dict)."""
    lo, hi = stored_window(ep, rule, fetch_from, fetch_to)
    entries = _entries(places, observations, lo, hi)
    return {
        "schema": "supermap.val01.outcome.v1", "label": LABEL,
        "episode_id": ep["id"], "split": split_of(ep["id"]),
        "rule": RULE_ID, "rule_sha256": rule_sha256,
        "episodes_sha256": episodes_sha256,
        "collected_at": collected_at,
        "window": {"from": iso_day(lo), "to": iso_day(hi)},
        "fetch_range": {"from": fetch_from, "to": fetch_to},
        "skipped_co_affected": places["skipped_co_affected"],
        "places": entries,
    }


def placebo_doc(ep: dict, places: dict, observations: dict, p: date | None, *,
                fetch_from: str, fetch_to: str, collected_at: str,
                episodes_sha256: str, rule_sha256: str) -> dict:
    """Stored placebo (undisrupted window) of one episode; ``p`` None means no
    qualifying window exists (recorded, not hidden)."""
    doc = {"schema": "supermap.val01.placebo.v1", "label": LABEL,
           "episode_id": ep["id"], "split": split_of(ep["id"]), "rule": RULE_ID,
           "rule_sha256": rule_sha256, "episodes_sha256": episodes_sha256,
           "collected_at": collected_at,
           "fetch_range": {"from": fetch_from, "to": fetch_to}}
    if p is None:
        doc.update({"available": False,
                    "reason": "no shifted window (+-364 d, +-728 d) inside the fetch range "
                              "that avoids every registered episode window", "places": []})
        return doc
    lo, hi = placebo_window(ep, p)
    doc.update({"available": True, "placebo_start": iso_day(p),
                "window": {"from": iso_day(lo), "to": iso_day(hi)},
                "places": _entries(places, observations, lo, hi)})
    return doc


def _entries(places: dict, observations: dict, lo: date, hi: date) -> list:
    entries = []
    for role_list in ([places["seed"]], places["universe"], places["controls"]):
        for pl in role_list:
            obs = observations.get(pl["place"]) or {}
            e = {"place": pl["place"], "role": pl["role"], "hops": pl.get("hops")}
            if not obs.get("served"):
                e.update({"served": False, "reason": obs.get("reason", "no_observation"),
                          "detail": obs.get("detail", "")})
            else:
                rows = [[r["date"], r["value"]] for r in obs["series"]
                        if lo <= parse_day(r["date"]) <= hi]
                e.update({
                    "served": True, "metric": obs["metric"], "unit": obs["unit"],
                    "layer": obs.get("layer"), "portname": obs.get("portname"),
                    "series": rows,
                    "provenance": {"source": obs["provenance"]["source"],
                                   "retrieved_at": obs["provenance"]["retrieved_at"],
                                   "mode": obs["provenance"]["mode"]},
                    "cache": {"payload_sha256": (obs.get("cache") or {}).get("payload_sha256"),
                              "query": (obs.get("cache") or {}).get("query")},
                })
            entries.append(e)
    return entries


def write_doc(doc: dict, data_dir: Path = DATA_DIR) -> Path:
    d = episode_dir(doc["split"], data_dir)
    d.mkdir(parents=True, exist_ok=True)
    suffix = ".placebo.json" if doc.get("schema") == "supermap.val01.placebo.v1" else ".json"
    path = d / f"{doc['episode_id']}{suffix}"
    path.write_text(json.dumps(doc, sort_keys=True, indent=1, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    return path


def build_manifest(split: str, data_dir: Path = DATA_DIR) -> dict:
    d = episode_dir(split, data_dir)
    files = {p.name: sha256_file(p) for p in sorted(d.glob("*.json"))
             if p.name != "MANIFEST.json"}
    man = {"schema": "supermap.val01.manifest.v1", "split": split,
           "rule": RULE_ID,
           "rule_sha256": sha256_file(Path(data_dir) / RULE_PATH.name),
           "episodes_sha256": sha256_file(Path(data_dir) / EPISODES_PATH.name),
           "files": files}
    man["manifest_sha256"] = sha256_text(canonical(
        {k: v for k, v in man.items() if k != "manifest_sha256"}))
    return man


def write_manifest(split: str, data_dir: Path = DATA_DIR) -> dict:
    man = build_manifest(split, data_dir)
    path = episode_dir(split, data_dir) / "MANIFEST.json"
    path.write_text(json.dumps(man, sort_keys=True, indent=1) + "\n", encoding="utf-8")
    return man


def verify_manifest(split: str, data_dir: Path = DATA_DIR) -> dict:
    """Recompute and compare; raises ValueError on any mismatch."""
    path = episode_dir(split, data_dir) / "MANIFEST.json"
    if not path.exists():
        raise ValueError(f"{split} manifest missing: {path}")
    stored = load_json(path)
    now = build_manifest(split, data_dir)
    if stored != now:
        bad = sorted(set(stored.get("files", {})) ^ set(now["files"])
                     | {f for f in now["files"] if stored.get("files", {}).get(f) != now["files"][f]})
        raise ValueError(f"{split} manifest does not verify; differing: {bad or 'header'}")
    return stored


# ---------------------------------------------------------------- statistics
def rate(x: int, n: int) -> dict:
    return {"hits": x, "graded": n, "rate": round(x / n, 6) if n else None,
            "ci95_clopper_pearson": clopper_pearson(x, n) if n else None}


def _series(entry: dict) -> dict:
    return {d: v for d, v in entry.get("series", [])}


def _smooth(series: dict, w: int, first: date, last: date) -> dict:
    out = {}
    d = first
    while d <= last:
        vals = window_values(series, d - timedelta(days=w - 1), d)
        if len(vals) >= max(1, w - 2):
            out[iso_day(d)] = sum(vals) / len(vals)
        d += timedelta(days=1)
    return out


# ------------------------------------------------------------ observed rows
def observe_place(entry: dict, ep: dict, rule: dict, doc_window_to: date) -> dict:
    """Direction row (realism-v1 style) for one place; ``comparable`` False
    with a reason when the place cannot be graded."""
    if not entry.get("served"):
        return {"comparable": False, "reason": entry.get("reason", "not_served")}
    g = rule["gradeability"]
    start = start_day(ep)
    ser = _series(entry)
    b, n = series_baseline(ser, start, g["baseline_days"], g["baseline_min_days_present"])
    if b is None:
        return {"comparable": False, "reason": "insufficient_baseline",
                "detail": f"{n} baseline days present, need {g['baseline_min_days_present']}"}
    if b < g["baseline_min_level_per_day"]:
        return {"comparable": False, "reason": "low_count",
                "detail": f"baseline {b:.2f}/day < {g['baseline_min_level_per_day']}"}
    eval_to = min(add_days(start, math.ceil(ep["duration_d"]) - 1), doc_window_to)
    vals = window_values(ser, start, eval_to)
    if not vals:
        return {"comparable": False, "reason": "no_evaluation_data"}
    m = sum(vals) / len(vals)
    ch = pct_change(m, b)
    band = 10.0
    dirn = "down" if ch <= -band else "up" if ch >= band else "flat"
    return {"comparable": True, "baseline_per_day": round(b, 4), "eval_mean": round(m, 4),
            "eval_days_present": len(vals), "change_pct": round(ch, 3),
            "direction": dirn, "severity": max(0.0, -ch)}


def observe_seed(entry: dict, ep: dict, rule: dict, doc_window_to: date) -> dict:
    """(a) onset, (b) peak trough, (c) recovery at the seed."""
    base = observe_place(entry, ep, rule, doc_window_to)
    if not base["comparable"]:
        return {"comparable": False, **{k: base[k] for k in base if k != "comparable"}}
    start = start_day(ep)
    ser = _series(entry)
    b = base["baseline_per_day"]
    w = smoothing_days(ep)
    m = rule["metrics"]
    # (a) onset
    sustain = max(1, min(3, int(math.floor(ep["duration_d"]))))
    od = onset_day(ser, start, add_days(start, 30), b, -0.20, sustain)
    onset_lag = None if od is None else (od - start).days
    # (b) trough of the w-day trailing mean
    t_hi = min(add_days(start, math.ceil(ep["duration_d"]) + w - 1), doc_window_to)
    sm = _smooth(ser, w, start, doc_window_to)
    in_trough = {d: v for d, v in sm.items() if start <= parse_day(d) <= t_hi}
    if not in_trough:
        return {"comparable": False, "reason": "no_trough_data"}
    t_trough, s_min = min(in_trough.items(), key=lambda kv: (kv[1], kv[0]))
    d_obs = min(1.0, max(0.0, 1.0 - s_min / b))
    # (c) recovery: 7 consecutive days with S_w >= 0.9 B from the trough on
    rec_lag, censored = None, False
    if d_obs >= 0.10:
        d = parse_day(t_trough)
        while d <= doc_window_to:
            ok = True
            for k in range(7):
                v = sm.get(iso_day(d + timedelta(days=k)))
                if v is None or v < 0.9 * b:
                    ok = False
                    break
            if ok:
                rec_lag = (d - start).days
                break
            d += timedelta(days=1)
        censored = rec_lag is None
    return {"comparable": True, "baseline_per_day": b, "w_days": w,
            "onset_lag_d": onset_lag, "onset_date": None if od is None else iso_day(od),
            "d_obs": round(d_obs, 6), "trough_value": round(s_min, 4),
            "trough_date": t_trough, "recovery_lag_d": rec_lag,
            "recovery_censored": censored if d_obs >= 0.10 else None,
            "change_pct": base["change_pct"], "direction": base["direction"]}


# --------------------------------------------------------------- predictions
def disruption_spec(ep: dict) -> dict:
    return {"node_id": ep["seed"], "kind": ep["kind"],
            "capacity_loss": ep["capacity_loss"], "duration_d": ep["duration_d"],
            "shape": "step", "started_at": ep["started_at"]}


def _is_cost_leaf(ev: dict) -> bool:
    return ev.get("channel") == "cost" and float(ev.get("delay_d", 0)) == 0.0


def _ts(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def digest_events(events: list[dict], spec: dict) -> dict:
    """Per-place prediction digest of an event stream (as realism-v1 digest_chain)."""
    start = _ts(spec["started_at"])
    places: dict[str, dict] = {}
    for ev in events:
        if _is_cost_leaf(ev):
            continue
        n = ev["node_id"]
        lag = (_ts(ev["at"]) - start).total_seconds() / 86400.0 if ev.get("at") else None
        p = places.get(n)
        if p is None:
            places[n] = {"first_seq": ev["seq"], "first_lag_d": None if lag is None else round(lag, 6),
                         "first_delay_d": float(ev["delay_d"]),
                         "max_delay_d": float(ev["delay_d"])}
        else:
            p["max_delay_d"] = max(p["max_delay_d"], float(ev["delay_d"]))
    seed = spec["node_id"]
    seed_events = [e for e in events if e["node_id"] == seed]
    root = min(seed_events, key=lambda e: e["seq"]) if seed_events else None
    return {"n_events": len(events), "places": {k: places[k] for k in sorted(places)},
            "seed_wait_d": None if root is None else float(root["delay_d"]),
            "seed_first_lag_d": (places.get(seed) or {}).get("first_lag_d")}


# ------------------------------------------------------------------ grading
def _hit_onset(pred, obs) -> bool:
    return pred is not None and abs(pred - obs) <= 7.0


def _hit_recovery(pred, obs) -> bool:
    return pred is not None and abs(pred - obs) <= max(7.0, 0.25 * obs)


def grade_episode(ep: dict, doc: dict, rule: dict, digest: dict) -> dict:
    """Grade one episode's stored outcome against one prediction digest.
    Returns per-metric rows for model, persistence, echo, k_hop."""
    window_to = parse_day(doc["window"]["to"])
    by_place = {e["place"]: e for e in doc["places"]}
    seed_entry = by_place[ep["seed"]]
    seed = observe_seed(seed_entry, ep, rule, window_to)
    spec = disruption_spec(ep)
    d_pred = 1.0 if ep["kind"] == "closure" else float(ep["capacity_loss"])
    out: dict = {"episode_id": ep["id"], "split": doc["split"], "seed": ep["seed"],
                 "seed_observed": seed, "ungraded": []}

    # ---- (a) onset
    a = {"graded": False}
    if seed["comparable"]:
        if seed["onset_lag_d"] is None:
            a = {"graded": False, "reason": "no_observed_onset",
                 "model_false_alarm": digest["seed_first_lag_d"] is not None}
        else:
            o = seed["onset_lag_d"]
            preds = {"model": digest["seed_first_lag_d"], "echo": 0.0, "persistence": None}
            a = {"graded": True, "observed_lag_d": o,
                 "predicted": preds,
                 "hit": {k: _hit_onset(v, o) for k, v in preds.items()},
                 "abs_error_d": {k: (None if v is None else round(abs(v - o), 6))
                                 for k, v in preds.items()}}
    else:
        a = {"graded": False, "reason": seed.get("reason")}
    out["a_onset"] = a

    # ---- (b) magnitude
    b = {"graded": False}
    if seed["comparable"]:
        if seed["d_obs"] < 0.10:
            b = {"graded": False, "reason": "no_observed_drop", "d_obs": seed["d_obs"]}
        else:
            do = seed["d_obs"]
            preds = {"model": d_pred, "persistence": 0.0}
            b = {"graded": True, "d_obs": do, "predicted": preds,
                 "relative_error": {k: round(abs(v - do) / do, 6) for k, v in preds.items()},
                 "hit": {k: abs(v - do) <= 0.20 for k, v in preds.items()}}
    else:
        b = {"graded": False, "reason": seed.get("reason")}
    out["b_magnitude"] = b

    # ---- (c) recovery
    c = {"graded": False}
    if seed["comparable"] and seed["d_obs"] >= 0.10:
        if seed["recovery_lag_d"] is None:
            c = {"graded": False, "reason": "censored_not_recovered_in_window"}
        else:
            o = seed["recovery_lag_d"]
            wait = digest["seed_wait_d"]
            preds = {"model": None if wait is None else ep["duration_d"] + wait,
                     "echo": float(ep["duration_d"]), "persistence": 0.0}
            c = {"graded": True, "observed_lag_d": o, "predicted": preds,
                 "hit": {k: _hit_recovery(v, o) for k, v in preds.items()},
                 "abs_error_d": {k: (None if v is None else round(abs(v - o), 6))
                                 for k, v in preds.items()}}
    elif seed["comparable"]:
        c = {"graded": False, "reason": "no_observed_drop"}
    else:
        c = {"graded": False, "reason": seed.get("reason")}
    out["c_recovery"] = c

    # ---- (d) downstream
    rows = []
    not_compared = []
    for pl in (u for u in doc["places"] if u["role"] in ("co_affected", "hop")):
        o = observe_place(pl, ep, rule, window_to)
        if not o["comparable"]:
            not_compared.append({"place": pl["place"], "role": pl["role"],
                                 "reason": o.get("reason"), "detail": o.get("detail")})
            continue
        hit = digest["places"].get(pl["place"])
        rows.append({"place": pl["place"], "role": pl["role"], "hops": pl.get("hops"),
                     "observed": o,
                     "model": {"direction": "down" if hit else "flat",
                               "severity": hit["max_delay_d"] if hit else 0.0},
                     "persistence": {"direction": "flat", "severity": 0.0}})
    n_down = sum(1 for r in rows if r["model"]["direction"] == "down")
    budget = max(1, n_down)
    ks = sorted({r["hops"] for r in rows if r["hops"] is not None})
    chosen = None
    for k in ks:
        if sum(1 for r in rows if r["hops"] is not None and r["hops"] <= k) >= budget:
            chosen = k
            break
    if chosen is None and ks:
        chosen = ks[-1]
    for r in rows:
        h = r["hops"]
        inside = h is not None and chosen is not None and h <= chosen
        r["k_hop"] = {"direction": "down" if inside else "flat",
                      "severity": None if h is None else -float(h)}
    out["d_downstream"] = {"rows": rows, "not_compared": not_compared,
                           "k_hop": {"k": chosen, "budget_places": budget},
                           "agreement": {}, "down_confusion": {}, "rank": {}}
    for who in ("model", "persistence", "k_hop"):
        agree = sum(1 for r in rows if r[who]["direction"] == r["observed"]["direction"])
        out["d_downstream"]["agreement"][who] = {"agree": agree, "n": len(rows)}
        tp = sum(1 for r in rows if r[who]["direction"] == "down" and r["observed"]["direction"] == "down")
        pd = sum(1 for r in rows if r[who]["direction"] == "down")
        od = sum(1 for r in rows if r["observed"]["direction"] == "down")
        out["d_downstream"]["down_confusion"][who] = {"tp": tp, "predicted_down": pd,
                                                      "observed_down": od}
        ps = [r[who]["severity"] for r in rows if r[who]["severity"] is not None]
        os_ = [r["observed"]["severity"] for r in rows if r[who]["severity"] is not None]
        rho = spearman(ps, os_) if len(ps) >= 4 else None
        out["d_downstream"]["rank"][who] = {"spearman": rho, "n": len(ps), "graded": rho is not None}

    # ---- (e) controls
    crow = []
    cnc = []
    for pl in (u for u in doc["places"] if u["role"] == "control"):
        o = observe_place(pl, ep, rule, window_to)
        if not o["comparable"]:
            cnc.append({"place": pl["place"], "reason": o.get("reason"), "detail": o.get("detail")})
            continue
        h = pl.get("hops")
        inside = h is not None and chosen is not None and h <= chosen
        crow.append({"place": pl["place"], "observed_direction": o["direction"],
                     "change_pct": o["change_pct"],
                     "model_down": pl["place"] in digest["places"],
                     "k_hop_down": inside, "persistence_down": False})
    obs_nondown = [r for r in crow if r["observed_direction"] != "down"]
    out["e_controls"] = {
        "rows": crow, "not_compared": cnc,
        "observed_down": sum(1 for r in crow if r["observed_direction"] == "down"),
        "false_positives": {
            "model": sum(1 for r in obs_nondown if r["model_down"]),
            "k_hop": sum(1 for r in obs_nondown if r["k_hop_down"]),
            "persistence": 0},
        "denominator": len(obs_nondown)}
    return out


# --------------------------------------------------------------- aggregation
def aggregate(graded: list[dict]) -> dict:
    """Pool counts over a list of grade_episode() results (one split)."""
    agg: dict = {"episodes": len(graded), "episode_ids": [g["episode_id"] for g in graded]}

    def pooled(metric_key, who, hit_key="hit"):
        x = n = 0
        errs = []
        for g in graded:
            m = g[metric_key]
            if not m.get("graded"):
                continue
            n += 1
            x += int(bool(m[hit_key].get(who)))
            e = (m.get("abs_error_d") or {}).get(who)
            if e is not None:
                errs.append(e)
        r = rate(x, n)
        if errs:
            r["mean_abs_error_d"] = round(sum(errs) / len(errs), 6)
            r["n_with_error"] = len(errs)
        return r

    agg["a_onset"] = {w: pooled("a_onset", w) for w in ("model", "echo", "persistence")}
    agg["a_onset"]["ungraded"] = [{"episode": g["episode_id"], "reason": g["a_onset"].get("reason"),
                                   "model_false_alarm": g["a_onset"].get("model_false_alarm")}
                                  for g in graded if not g["a_onset"].get("graded")]
    # (b)
    b = {}
    for who in ("model", "persistence"):
        x = n = 0
        res = []
        for g in graded:
            m = g["b_magnitude"]
            if m.get("graded"):
                n += 1
                x += int(m["hit"][who])
                res.append(m["relative_error"][who])
        r = rate(x, n)
        if res:
            s = sorted(res)
            r["median_relative_error"] = round(s[len(s) // 2] if len(s) % 2 else
                                               (s[len(s) // 2 - 1] + s[len(s) // 2]) / 2, 6)
            r["mean_relative_error"] = round(sum(res) / len(res), 6)
        b[who] = r
    b["ungraded"] = [{"episode": g["episode_id"], "reason": g["b_magnitude"].get("reason")}
                     for g in graded if not g["b_magnitude"].get("graded")]
    agg["b_magnitude"] = b
    # (c)
    agg["c_recovery"] = {w: pooled("c_recovery", w) for w in ("model", "echo", "persistence")}
    agg["c_recovery"]["ungraded"] = [{"episode": g["episode_id"], "reason": g["c_recovery"].get("reason")}
                                     for g in graded if not g["c_recovery"].get("graded")]
    # (d)
    d: dict = {"direction_agreement": {}, "down_recall": {}, "down_precision": {}, "rank": {}}
    for who in ("model", "persistence", "k_hop"):
        x = sum(g["d_downstream"]["agreement"][who]["agree"] for g in graded)
        n = sum(g["d_downstream"]["agreement"][who]["n"] for g in graded)
        d["direction_agreement"][who] = rate(x, n)
        tp = sum(g["d_downstream"]["down_confusion"][who]["tp"] for g in graded)
        od = sum(g["d_downstream"]["down_confusion"][who]["observed_down"] for g in graded)
        pd = sum(g["d_downstream"]["down_confusion"][who]["predicted_down"] for g in graded)
        d["down_recall"][who] = rate(tp, od)
        d["down_precision"][who] = rate(tp, pd)
        rhos = [(g["episode_id"], g["d_downstream"]["rank"][who]["spearman"])
                for g in graded if g["d_downstream"]["rank"][who]["graded"]]
        d["rank"][who] = {"per_episode": rhos, "n_graded_episodes": len(rhos),
                          "mean_spearman": round(sum(r for _, r in rhos) / len(rhos), 6) if rhos else None}
    d["not_compared"] = {g["episode_id"]: g["d_downstream"]["not_compared"] for g in graded
                         if g["d_downstream"]["not_compared"]}
    agg["d_downstream"] = d
    # (e)
    e: dict = {"false_positive_rate": {}, "denominator": sum(g["e_controls"]["denominator"] for g in graded),
               "controls_observed_down": sum(g["e_controls"]["observed_down"] for g in graded)}
    for who in ("model", "k_hop", "persistence"):
        x = sum(g["e_controls"]["false_positives"][who] for g in graded)
        e["false_positive_rate"][who] = rate(x, e["denominator"])
    agg["e_controls"] = e
    return agg


# ----------------------------------------------- strata, placebo, per place
def placebo_rows(ep: dict, pdoc: dict | None, rule: dict) -> dict:
    """Observed direction of each place over the episode's undisrupted
    placebo window (same rule, same eval-window length)."""
    if not pdoc or not pdoc.get("available"):
        return {"available": False,
                "reason": (pdoc or {}).get("reason", "no placebo file")}
    p = parse_day(pdoc["placebo_start"])
    pep = shifted_episode(ep, p)
    wto = parse_day(pdoc["window"]["to"])
    out = {"available": True, "placebo_start": pdoc["placebo_start"], "places": {},
           "seed_comparable": False, "seed_onset_observed": None}
    for e in pdoc["places"]:
        if e["role"] == "seed":
            s = observe_seed(e, pep, rule, wto)
            out["seed_comparable"] = bool(s["comparable"])
            if s["comparable"]:
                out["seed_onset_observed"] = s["onset_lag_d"] is not None
            continue
        o = observe_place(e, pep, rule, wto)
        out["places"][e["place"]] = ({"direction": o["direction"], "change_pct": o["change_pct"]}
                                     if o["comparable"] else {"direction": None, "reason": o["reason"]})
    return out


def enrich(g: dict, ep: dict, nodes: dict, plc: dict) -> None:
    """Attach place kind / region and the placebo direction to a graded
    episode's rows (in place)."""
    seed_node = nodes.get(ep["seed"], {})
    g["seed_kind"], g["seed_region"] = kind_of(seed_node), region_of(seed_node)
    g["placebo"] = {k: v for k, v in plc.items() if k != "places"}
    for r in g["d_downstream"]["rows"] + g["e_controls"]["rows"]:
        node = nodes.get(r["place"], {})
        r["kind"], r["region"] = kind_of(node), region_of(node)
        pp = (plc.get("places") or {}).get(r["place"]) or {}
        r["placebo_direction"] = pp.get("direction")
        r["placebo_change_pct"] = pp.get("change_pct")


def place_records(graded: list[dict]) -> list[dict]:
    recs = []
    for g in graded:
        for r in g["d_downstream"]["rows"]:
            recs.append({
                "episode": g["episode_id"], "split": g["split"], "role": r["role"],
                "place": r["place"], "kind": r["kind"], "region": r["region"],
                "hops": r["hops"], "baseline_per_day": r["observed"]["baseline_per_day"],
                "obs_dir": r["observed"]["direction"], "obs_change": r["observed"]["change_pct"],
                "model_dir": r["model"]["direction"], "model_severity": r["model"]["severity"],
                "k_hop_dir": r["k_hop"]["direction"], "persistence_dir": "flat",
                "placebo_dir": r["placebo_direction"], "placebo_change": r["placebo_change_pct"]})
        for r in g["e_controls"]["rows"]:
            recs.append({
                "episode": g["episode_id"], "split": g["split"], "role": "control",
                "place": r["place"], "kind": r["kind"], "region": r["region"],
                "hops": None, "baseline_per_day": None,
                "obs_dir": r["observed_direction"], "obs_change": r["change_pct"],
                "model_dir": "down" if r["model_down"] else "flat", "model_severity": None,
                "k_hop_dir": "down" if r["k_hop_down"] else "flat", "persistence_dir": "flat",
                "placebo_dir": r["placebo_direction"], "placebo_change": r["placebo_change_pct"]})
    return recs


def _cell(x: int, n: int) -> dict:
    out = rate(x, n)
    out["indicative"] = n < 5
    return out


def place_level(records: list[dict], field: str | None, universe_keys: tuple = ()) -> dict:
    """Place-level metrics (d, e, placebo) for each value of ``field``
    (None -> one pooled cell). ``universe_keys`` are listed even when empty."""
    keys = [None] if field is None else sorted({r[field] for r in records} | set(universe_keys))
    out = {}
    for key in keys:
        sub = records if field is None else [r for r in records if r[field] == key]
        uni = [r for r in sub if r["role"] != "control"]
        ctl = [r for r in sub if r["role"] == "control"]
        cell: dict = {"n_places": len(sub), "n_universe": len(uni), "n_controls": len(ctl)}
        cell["direction_agreement"] = {w: _cell(sum(1 for r in uni if r[f"{w}_dir"] == r["obs_dir"]), len(uni))
                                       for w in ("model", "persistence", "k_hop")}
        cell["down_recall"] = {w: _cell(sum(1 for r in uni if r[f"{w}_dir"] == "down" and r["obs_dir"] == "down"),
                                        sum(1 for r in uni if r["obs_dir"] == "down"))
                               for w in ("model", "persistence", "k_hop")}
        cell["down_precision"] = {w: _cell(sum(1 for r in uni if r[f"{w}_dir"] == "down" and r["obs_dir"] == "down"),
                                           sum(1 for r in uni if r[f"{w}_dir"] == "down"))
                                  for w in ("model", "persistence", "k_hop")}
        nd = [r for r in ctl if r["obs_dir"] != "down"]
        cell["control_false_positive"] = {w: _cell(sum(1 for r in nd if r[f"{w}_dir"] == "down"), len(nd))
                                          for w in ("model", "persistence", "k_hop")}
        cell["controls_observed_down"] = sum(1 for r in ctl if r["obs_dir"] == "down")
        pl = [r for r in sub if r["placebo_dir"] is not None]
        cell["placebo_down_rate"] = _cell(sum(1 for r in pl if r["placebo_dir"] == "down"), len(pl))
        pm = [r for r in pl if r["model_dir"] == "down"]
        cell["placebo_model_precision"] = {
            "in_placebo_window": _cell(sum(1 for r in pm if r["placebo_dir"] == "down"), len(pm)),
            "in_real_window": _cell(sum(1 for r in pm if r["obs_dir"] == "down"), len(pm))}
        out["ALL" if key is None else key] = cell
    return out


def seed_strata(graded: list[dict], field: str, universe_keys: tuple) -> dict:
    out = {}
    keys = sorted({g[field] for g in graded} | set(universe_keys))
    for key in keys:
        sub = [g for g in graded if g[field] == key]
        if not sub:
            out[key] = {"episodes": 0, "episode_ids": [], "note": "no episode in this stratum"}
            continue
        a = aggregate(sub)
        pc = [g for g in sub if g["placebo"].get("seed_onset_observed") is not None]
        a["placebo_onset_rate"] = _cell(sum(1 for g in pc if g["placebo"]["seed_onset_observed"]), len(pc))
        a["indicative"] = len(sub) < 5
        out[key] = a
    return out


def per_place_table(records: list[dict]) -> list[dict]:
    def disagree(r):
        # A control is not something the model was asked about: only a
        # predicted-down control that is not observed down is a disagreement
        # (a false positive). Universe places: any direction mismatch.
        if r["role"] == "control":
            return int(r["model_dir"] == "down" and r["obs_dir"] != "down")
        return int(r["model_dir"] != r["obs_dir"])
    rows = sorted(records, key=lambda r: (-disagree(r), -abs(r["obs_change"] or 0.0), r["episode"], r["place"]))
    return [{**r, "model_disagrees": bool(disagree(r))} for r in rows]


def analyse(graded: list[dict]) -> dict:
    recs = place_records(graded)
    pc = [g for g in graded if g["placebo"].get("seed_onset_observed") is not None]
    return {
        "strata": {
            "by_seed_kind": seed_strata(graded, "seed_kind", PLACE_KINDS),
            "by_seed_region": seed_strata(graded, "seed_region", REGIONS),
            "by_place_kind": place_level(recs, "kind", PLACE_KINDS),
            "by_place_region": place_level(recs, "region", REGIONS),
            "pooled_places": place_level(recs, None),
            "not_served": {"air_hub": "no PortWatch daily series; not gradeable in this benchmark",
                           "ground": "only gnd_detroit, gnd_montreal, gnd_vancouver have a series"},
        },
        "placebo": {
            "episodes_with_placebo": sum(1 for g in graded if g["placebo"].get("available")),
            "episodes_without": [{"episode": g["episode_id"], "reason": g["placebo"].get("reason")}
                                 for g in graded if not g["placebo"].get("available")],
            "seed_false_onset_rate": _cell(sum(1 for g in pc if g["placebo"]["seed_onset_observed"]), len(pc)),
        },
        "per_place": per_place_table(recs),
    }


# ------------------------------------------------------------------ engines
def services_selection(graph) -> dict:
    try:
        from graph_services.loader import load_services
    except ImportError:  # pragma: no cover
        from server.graph_services.loader import load_services  # type: ignore
    svc = load_services(graph.raw, graph.hash)
    return {"file_sha256": svc.file_sha256, "eras": SERVICES_ERAS,
            "include_unverified": False, "include_unstated": False}


def build_engine(config: str, graph_path: Path = GRAPH_PATH):
    """(engine, graph) for one of CONFIGS. Engines are called unmodified."""
    if config not in CONFIGS:
        raise ValueError(f"unknown config {config!r}; one of {CONFIGS}")
    try:
        from engine.graph import load_graph
    except ImportError:  # pragma: no cover
        from server.engine.graph import load_graph  # type: ignore
    graph = load_graph(graph_path)
    if config == "v1":
        try:
            from engine.engine import Engine
        except ImportError:  # pragma: no cover
            from server.engine.engine import Engine  # type: ignore
        return Engine(graph), graph
    try:
        from engine2.engine import Engine2
        from engine2.journal import get_routed_engine2
    except ImportError:  # pragma: no cover
        from server.engine2.engine import Engine2  # type: ignore
        from server.engine2.journal import get_routed_engine2  # type: ignore
    if config == "rev2":
        return Engine2(graph), graph
    sel = services_selection(graph)
    if config == "rev2_services":
        return get_routed_engine2(graph, sel), graph
    if config == "rev2d":
        # ENGINE REV 2 profile d: the rev2c calibration + sea-lane geometry + gate disciplines
        # + graded route choice + rotation recovery + tier wall, with the same services selection
        try:
            from engine2.journal import get_profile_engine2
        except ImportError:  # pragma: no cover
            from server.engine2.journal import get_profile_engine2  # type: ignore
        return get_profile_engine2(graph, sel), graph
    try:
        from engine2.calibration import default_calibration
    except ImportError:  # pragma: no cover
        from server.engine2.calibration import default_calibration  # type: ignore
    return get_routed_engine2(graph, sel, calibration=default_calibration()), graph


def predict(engine, ep: dict) -> dict:
    """Run the engine once on the episode's disruption and digest the stream."""
    spec = disruption_spec(ep)
    state = engine.run(f"val01_{ep['id']}", spec, None)
    return digest_events(state.events, spec)


def _tree_fingerprint(root: Path, exclude=("__pycache__",)) -> str:
    items = []
    for p in sorted(Path(root).rglob("*")):
        if p.is_file() and p.suffix in (".py", ".json") \
                and not any(x in p.parts for x in exclude):
            items.append([str(p.relative_to(root)), sha256_file(p)])
    return sha256_text(canonical(items))


def model_fingerprint(config: str, repo_root: Path = REPO_ROOT) -> dict:
    """What 'the model' is: the engine source tree(s), the graph, and for the
    services / calibrated configs the sidecar and calibration data."""
    fp = {"config": config,
          "graph_sha256": sha256_file(repo_root / "data" / "graph" / "graph.json"),
          "engine_src": _tree_fingerprint(repo_root / "server" / "engine")}
    if config != "v1":
        fp["engine2_src"] = _tree_fingerprint(repo_root / "server" / "engine2")
    if config in ("rev2_services", "rev2_calibrated", "rev2d"):
        fp["services_sha256"] = sha256_file(repo_root / "data" / "graph" / "services.json")
        fp["services_selection"] = {"eras": SERVICES_ERAS, "include_unverified": False,
                                    "include_unstated": False}
    if config in ("rev2_calibrated", "rev2d"):
        cal = repo_root / "data" / "calibration"
        fp["calibration_src"] = _tree_fingerprint(cal) if cal.is_dir() else None
    if config == "rev2d":
        fp["sea_routes_sha256"] = sha256_file(repo_root / "data" / "graph" / "sea_routes.json")
    return fp


def config_hash(config: str, data_dir: Path = DATA_DIR, repo_root: Path = REPO_ROOT) -> str:
    """Frozen model-config hash: model fingerprint + the pre-registered rule
    and episodes. Changing the model or the benchmark inputs changes it."""
    body = {"model": model_fingerprint(config, repo_root),
            "rule_sha256": sha256_file(Path(data_dir) / RULE_PATH.name),
            "episodes_sha256": sha256_file(Path(data_dir) / EPISODES_PATH.name)}
    return sha256_text(canonical(body))


# --------------------------------------------------------------------- runs
def load_docs(split: str, data_dir: Path = DATA_DIR) -> dict[str, dict]:
    d = episode_dir(split, data_dir)
    return {p.stem: load_json(p) for p in sorted(d.glob("*.json"))
            if p.name != "MANIFEST.json" and not p.name.endswith(".placebo.json")}


def load_placebos(split: str, data_dir: Path = DATA_DIR) -> dict[str, dict]:
    d = episode_dir(split, data_dir)
    return {p.name[:-len(".placebo.json")]: load_json(p)
            for p in sorted(d.glob("*.placebo.json"))}


def run_split(config: str, split: str, *, data_dir: Path = DATA_DIR,
              engine=None) -> dict:
    """Score one config on one split. No gate here: the caller enforces the
    TEST lockbox rules before calling this."""
    rule = load_rule(Path(data_dir) / RULE_PATH.name)
    eps = {e["id"]: e for e in load_episodes(Path(data_dir) / EPISODES_PATH.name)}
    docs = load_docs(split, data_dir)
    placebos = load_placebos(split, data_dir)
    nodes = load_nodes()
    if engine is None:
        engine, _g = build_engine(config)
    graded = []
    refused = []
    for eid in sorted(docs):
        ep = eps[eid]
        try:
            dig = predict(engine, ep)
        except Exception as exc:   # e.g. a SHELL-tier seed: the engines accept core seeds only
            refused.append({"episode_id": eid, "split": split,
                            "engine_refused": f"{type(exc).__name__}: {exc}"})
            continue
        g = grade_episode(ep, docs[eid], rule, dig)
        enrich(g, ep, nodes, placebo_rows(ep, placebos.get(eid), rule))
        g["prediction_digest"] = dig
        graded.append(g)
    return {"schema": "supermap.val01.result.v1", "label": LABEL, "rule": RULE_ID,
            "split": split, "config": config, "episodes": graded,
            "engine_refused": refused,
            "aggregate": aggregate(graded), **analyse(graded)}


# ----------------------------------------------------------- lockbox gating
class LockboxError(RuntimeError):
    """A TEST run was refused (missing/wrong hash, broken seal, repeat)."""


def result_path(split: str, config: str, chash: str | None,
                data_dir: Path = DATA_DIR) -> Path:
    if split == TEST:
        return Path(data_dir) / f"results_test_{config}_{chash[:12]}.json"
    return Path(data_dir) / f"results_train_{config}.json"


def gate_test_run(config: str, supplied_hash: str | None, *,
                  data_dir: Path = DATA_DIR, repo_root: Path = REPO_ROOT) -> dict:
    """Every condition that must hold before a TEST episode is scored:
    1. a frozen config hash is supplied;
    2. it equals the hash recomputed from the model and benchmark files now;
    3. the lockbox manifest verifies (no file edited since sealing);
    4. no TEST result for this config hash exists (one run per config).
    Returns {config_hash, manifest_sha256}; raises LockboxError otherwise."""
    if config not in CONFIGS:
        raise LockboxError(f"unknown config {config!r}")
    if not supplied_hash:
        raise LockboxError("TEST refused: a frozen model-config hash is required "
                           "(--config-hash; print it with --print-config-hash)")
    now = config_hash(config, data_dir, repo_root)
    if supplied_hash != now:
        raise LockboxError("TEST refused: the supplied config hash does not equal "
                           f"the hash of the model as it is now ({now[:12]}...); a "
                           "changed model is a new config and needs its own hash")
    try:
        man = verify_manifest(TEST, data_dir)
    except ValueError as exc:
        raise LockboxError(f"TEST refused: lockbox seal broken: {exc}")
    if result_path(TEST, config, now, data_dir).exists():
        raise LockboxError(f"TEST refused: config {config} hash {now[:12]} already "
                           "has its one TEST run (published, never replaced)")
    return {"config_hash": now, "manifest_sha256": man["manifest_sha256"]}


def run_test(config: str, supplied_hash: str | None, *, data_dir: Path = DATA_DIR,
             repo_root: Path = REPO_ROOT, engine=None, now: str | None = None) -> dict:
    """Gate, score TEST once, write the result next to the data. The config
    hash and the lockbox manifest hash are recorded in the result."""
    g = gate_test_run(config, supplied_hash, data_dir=data_dir, repo_root=repo_root)
    res = run_split(config, TEST, data_dir=data_dir, engine=engine)
    res["config_hash"] = g["config_hash"]
    res["model_fingerprint"] = model_fingerprint(config, repo_root)
    res["lockbox_manifest_sha256"] = g["manifest_sha256"]
    res["note"] = ("One TEST run per frozen config hash, published whatever it says. "
                   "BACKTEST; never merged with the live Ledger. "
                   "la_2021 is also an in-sample replay episode, so its TEST result is not blind.")
    path = result_path(TEST, config, g["config_hash"], data_dir)
    path.write_text(json.dumps(res, sort_keys=True, indent=1, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    res["_path"] = str(path)
    return res


def run_train(config: str, *, data_dir: Path = DATA_DIR, repo_root: Path = REPO_ROOT,
              engine=None) -> dict:
    try:
        man = verify_manifest(TRAIN, data_dir)
    except ValueError as exc:
        raise LockboxError(f"TRAIN seal broken: {exc}")
    res = run_split(config, TRAIN, data_dir=data_dir, engine=engine)
    res["config_hash"] = config_hash(config, data_dir, repo_root)
    res["model_fingerprint"] = model_fingerprint(config, repo_root)
    res["train_manifest_sha256"] = man["manifest_sha256"]
    path = result_path(TRAIN, config, None, data_dir)
    path.write_text(json.dumps(res, sort_keys=True, indent=1, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    res["_path"] = str(path)
    return res
