"""REALISM TESTER: a stored simulated outcome vs the observed history.

Frozen, published rule ``realism-v1`` - mirrors the style of the existing
backtest scorecards (data/replay/scorecard.md for the chain engine,
data/sim_backtest/scorecard.md for the sim): pre-declared bands, every
rate printed with its denominator, ungradeable things NAMED (never a hit,
never a miss, never estimated), a naive baseline on the SAME card, exact
Clopper-Pearson intervals, and the small-sample caveat in the text.

What is compared (per place, over the same dates):
  observed  IMF PortWatch daily counts (transits at a chokepoint, port
            calls at a port) - SNAPSHOT history from the cache;
  simulated the run's own prediction: for a SIM run the daily berth
            calls at the node (the sim's 'berth' segments - the extraction
            the sim backtest registered as BA-1), for a CHAIN run the chain event
            (which nodes get hit, how late, how hard at the seed).

Rule realism-v1 (nothing below is tuned after the fact):
  baseline   observed: mean of the 28 days before the event start (>= 14
             days present). sim: mean of the sim's own lead-in days before
             the event start (>= 7 days present). The evaluation period is
             [max(from, event start), min(to, last day of the stated duration)]:
             the disruption's own stated duration, so a 6-day closure is
             not diluted by a long quiet tail (and a window past the sim
             horizon is clipped to it).
  change %   100 * (evaluation mean - baseline) / baseline.
  direction  down if change <= -10%, up if >= +10%, else flat.
  R1 DIRECTION   predicted direction == observed direction. Chain: a place
                 the event stream hits (delay_d > 0, cost leaves excluded) is
                 predicted down; every other place flat.
  R2 MAGNITUDE   HIT iff |predicted change - observed change| <= 20
                 percentage points (the sim backtest's registered band).
                 Sim: every place. Chain: the seed place only, predicted
                 change = -100 * capacity_loss (the chain engine predicts
                 delays, not throughput, elsewhere -> UNGRADED there).
  R3 ONSET       onset = first day of 3 consecutive days >= 20% below
                 baseline. HIT iff |predicted lag - observed lag| <= 7
                 days. Graded over places with an OBSERVED onset; a
                 prediction of no onset there is a miss. A predicted onset
                 with no observed onset is a false alarm (printed, in no
                 denominator). Chain lag = first-hit event `at` minus the
                 event start; the seed's lag is 0 by construction and is
                 graded like any other place.
  R4 RANK        Spearman correlation between predicted and observed
                 severity (size of the drop; chain: max delay_d) across
                 the places with both; UNGRADED under 4 places.
Baselines on the same card:
  persistence    nothing changes (flat, 0 pp, no onset, constant rank).
  k-hop          directed BFS from the seed over the graph; the smallest k
                 whose hop set covers as many compared places as the model
                 predicted down (whole hop levels); predicts down inside,
                 flat outside; direction + rank (nearer = more severe)
                 only - no magnitude, no onset.
No LLM at any step: the grade is arithmetic over stored numbers.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

from .series_math import (baseline as series_baseline, direction, mean,
                          onset_day, pct_change, spearman, window_values)
from .util import (HistoryError, add_days, canonical, iso_day, parse_day,
                   sha256_hex, utcnow_iso)

try:
    from engine.scorecard import clopper_pearson
except Exception:  # pragma: no cover
    try:
        from server.engine.scorecard import clopper_pearson  # type: ignore
    except Exception:
        clopper_pearson = None

RULE_ID = "realism-v1"
LABEL = ("BACKTEST — historical outcome vs a SIMULATED outcome. Not the live "
         "Ledger; these numbers never mix with Ledger grades.")
RULE = {
    "id": RULE_ID,
    "baseline_days": 28, "baseline_min_days": 14, "sim_baseline_min_days": 7,
    "evaluation_period": ("[max(from, event start), min(to, event start + "
                          "ceil(duration_d) - 1)] - the disruption's own stated "
                          "duration, clipped to the window / sim horizon"),
    "flat_band_pct": 10.0,
    "magnitude_band_pp": 20.0,
    "onset": {"threshold_pct": -20.0, "sustain_days": 3, "hit_within_days": 7},
    "rank": {"statistic": "Spearman", "min_places": 4},
    "denominators": {
        "direction": "compared places with an observed and a predicted direction",
        "magnitude": "compared places with an observed and a predicted change",
        "onset": "compared places with an observed onset",
        "rank": "compared places with an observed and a predicted severity"},
    "ungraded_is": "never a hit, never a miss, never estimated",
    "grader": "deterministic arithmetic; no LLM at any step",
    "observed_series": "IMF PortWatch daily counts (SNAPSHOT history)",
}
CAVEAT = ("A few events prove the machinery, not the forecasting power: "
          "every rate here rests on a handful of places for one or two "
          "events. One graded place moves a hit rate by tens of points. "
          "Read these as a worked, auditable comparison, not a skill "
          "estimate. The simulator's traffic is modelled from lane "
          "structure, not the historical fleet.")


# ----------------------------------------------------------------- digests
def _parse_ts(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def digest_chain(record: dict) -> dict:
    """Prediction digest of a stored chain run."""
    j = record["journal"]
    spec = j["disruption"]
    start = spec.get("started_at")
    places: dict = {}
    for ev in record["events"]:
        if ev.get("channel") == "cost" and float(ev["delay_d"]) == 0.0:
            continue          # a cost leaf is not a delay prediction
        n = ev["node_id"]
        p = places.get(n)
        if p is None:
            lag = None
            if start and ev.get("at"):
                lag = round((_parse_ts(ev["at"]) - _parse_ts(start)
                             ).total_seconds() / 86400.0, 6)
            places[n] = {"first_seq": ev["seq"], "first_at": ev.get("at"),
                         "first_lag_d": lag, "first_delay_d": float(ev["delay_d"]),
                         "max_delay_d": float(ev["delay_d"]),
                         "max_magnitude": float(ev.get("magnitude") or 0.0),
                         "channels": [ev["channel"]]}
        else:
            p["max_delay_d"] = max(p["max_delay_d"], float(ev["delay_d"]))
            p["max_magnitude"] = max(p["max_magnitude"],
                                     float(ev.get("magnitude") or 0.0))
            if ev["channel"] not in p["channels"]:
                p["channels"].append(ev["channel"])
    return {"kind": "chain", "run_id": record["run_id"],
            "stream_hash": record.get("stream_hash"),
            "event": {k: spec.get(k) for k in
                      ("node_id", "kind", "capacity_loss", "duration_d",
                       "shape", "started_at")},
            "params": j.get("params"), "n_events": len(record["events"]),
            "places": {k: places[k] for k in sorted(places)}}


def digest_sim(record: dict, places: list[str]) -> dict:
    """Prediction digest of a stored sim run: BERTH calls per day at each
    place (the sim's served-call count - the same extraction the sim
    backtest registered for port calls, BA-1). Counted from the record's
    ``berth`` segments; the run's own lead-in days are its baseline."""
    j = record["journal"]
    out = {"kind": "sim", "run_id": record.get("sim_run_id"),
           "metric": "berth calls per day (sim 'berth' segments at the node)",
           "tick": j.get("tick"), "ticks": j.get("ticks"),
           "start_at": j.get("start_at"), "seed": j.get("seed"),
           "weather": "pinned" if j.get("weather_snapshot_hash") else "off",
           "disruptions": [{k: d.get(k) for k in
                            ("node_id", "kind", "capacity_loss", "duration_d",
                             "shape", "started_at")}
                           for d in j.get("disruptions", [])],
           "series": {}, "unavailable": {}}
    out["event"] = out["disruptions"][0] if out["disruptions"] else None
    if j.get("tick") != "day":
        out["unavailable"] = {p: "sim tick is not 'day'" for p in places}
        return out
    start_d = parse_day(j["start_at"])
    start_epoch = datetime.fromisoformat(
        j["start_at"].replace("Z", "+00:00")).timestamp()
    ticks = int(j["ticks"])
    known = set((record.get("node_series") or {}))
    counts: dict[str, list[int]] = {}
    for p in places:
        if p in known:
            counts[p] = [0] * ticks
        else:
            out["unavailable"][p] = "place not in the sim's node_series"
    wanted = set(counts)
    if wanted:
        for evs in (record.get("segments") or {}).values():
            for seg in evs:
                if seg.get("kind") != "berth" or seg.get("node") not in wanted:
                    continue
                k = int((seg["t"] - start_epoch) // 86400)
                if 0 <= k < ticks:
                    counts[seg["node"]][k] += 1
    for p, arr in counts.items():
        out["series"][p] = {iso_day(add_days(start_d, k)): float(v)
                            for k, v in enumerate(arr)}
    return out


# ------------------------------------------------------------------ grading
def _rate(x: int, n: int) -> dict:
    out = {"hits": x, "graded": n,
           "rate": round(x / n, 6) if n else None}
    if clopper_pearson is not None and n:
        out["ci95_clopper_pearson"] = clopper_pearson(x, n)
    return out


def _band(change) -> str | None:
    return direction(change, RULE["flat_band_pct"])


def _observed_row(series: dict, event_start: date, eval_start: date, to: date):
    base, nb = series_baseline(series, event_start, RULE["baseline_days"],
                               RULE["baseline_min_days"])
    vals = window_values(series, eval_start, to)
    row = {"baseline_per_day": None if base is None else round(base, 4),
           "baseline_days_present": nb, "eval_days_present": len(vals),
           "eval_mean": None, "change_pct": None, "direction": None,
           "onset_date": None, "onset_lag_d": None}
    if base is None or not vals:
        return row
    m = sum(vals) / len(vals)
    ch = pct_change(m, base)
    row["eval_mean"] = round(m, 4)
    row["change_pct"] = None if ch is None else round(ch, 3)
    row["direction"] = _band(ch)
    od = onset_day(series, eval_start, to, base,
                   RULE["onset"]["threshold_pct"] / 100.0,
                   RULE["onset"]["sustain_days"])
    if od is not None:
        row["onset_date"] = iso_day(od)
        row["onset_lag_d"] = (od - event_start).days
    return row


def _sim_row(series: dict, event_start: date, eval_start: date, to: date):
    lead = window_values(series, event_start - timedelta(days=RULE["baseline_days"]),
                         event_start - timedelta(days=1))
    row = {"baseline_per_day": None, "baseline_days_present": len(lead),
           "eval_mean": None, "change_pct": None, "direction": None,
           "onset_date": None, "onset_lag_d": None}
    if len(lead) < RULE["sim_baseline_min_days"]:
        return row
    base = sum(lead) / len(lead)
    row["baseline_per_day"] = round(base, 4)
    vals = window_values(series, eval_start, to)
    if not vals or base <= 0:
        return row
    m = sum(vals) / len(vals)
    ch = pct_change(m, base)
    row["eval_mean"] = round(m, 4)
    row["change_pct"] = None if ch is None else round(ch, 3)
    row["direction"] = _band(ch)
    od = onset_day(series, eval_start, to, base,
                   RULE["onset"]["threshold_pct"] / 100.0,
                   RULE["onset"]["sustain_days"])
    if od is not None:
        row["onset_date"] = iso_day(od)
        row["onset_lag_d"] = (od - event_start).days
    return row


def _score(rows: list[dict], who: str) -> dict:
    """Aggregate one predictor's per-place entries into the four metrics.
    ``who`` is a key in each row ('model' | 'persistence' | 'k_hop')."""
    d_hit = d_n = m_hit = m_n = o_hit = o_n = 0
    errs: list[float] = []
    false_alarms = 0
    ungraded: list[dict] = []
    pred_sev, obs_sev = [], []
    for r in rows:
        o, p = r["observed"], r[who]
        if o["direction"] is not None and p["direction"] is not None:
            d_n += 1
            d_hit += int(o["direction"] == p["direction"])
        elif o["direction"] is not None:
            ungraded.append({"place": r["place"], "metric": "direction",
                             "reason": p.get("why_no_direction", "no prediction")})
        if o["change_pct"] is not None and p.get("change_pct") is not None:
            m_n += 1
            m_hit += int(abs(p["change_pct"] - o["change_pct"])
                         <= RULE["magnitude_band_pp"])
        if who != "k_hop" and o["onset_lag_d"] is not None:
            o_n += 1
            if p.get("onset_lag_d") is None:
                pass            # predicted no onset: a miss, no error value
            else:
                err = abs(p["onset_lag_d"] - o["onset_lag_d"])
                errs.append(err)
                o_hit += int(err <= RULE["onset"]["hit_within_days"])
        elif who != "k_hop" and o["onset_lag_d"] is None \
                and p.get("onset_lag_d") is not None:
            false_alarms += 1
        if o["change_pct"] is not None and p.get("severity") is not None:
            obs_sev.append(max(0.0, -o["change_pct"]))
            pred_sev.append(p["severity"])
    rho = spearman(pred_sev, obs_sev) if len(pred_sev) >= RULE["rank"]["min_places"] else None
    return {
        "direction": _rate(d_hit, d_n),
        "magnitude": (_rate(m_hit, m_n) if who != "k_hop" else
                      {"graded": 0, "note": "UNGRADED - direction-only baseline"}),
        "onset": ({**_rate(o_hit, o_n),
                   "mean_abs_error_d": (round(sum(errs) / len(errs), 6)
                                        if errs else None),
                   "n_with_both": len(errs), "false_alarms": false_alarms}
                  if who != "k_hop" else
                  {"graded": 0, "note": "UNGRADED - direction-only baseline"}),
        "rank_correlation": {"spearman": rho, "n": len(pred_sev),
                             "graded": rho is not None,
                             "note": (None if rho is not None else
                                      "UNGRADED - fewer than 4 places with "
                                      "variation on both sides")},
        "ungraded": ungraded,
    }


def _compare(model: dict, base: dict, key: str, label: str = "baseline"):
    """Descriptive counts only, side by side (no verdict, no ranking)."""
    a, b = model.get(key, {}), base.get(key, {})
    if a.get("rate") is None or b.get("rate") is None:
        return "not comparable (one side ungraded)"
    return (f"model {a['hits']}/{a['graded']} vs {label} "
            f"{b['hits']}/{b['graded']}")


def _comparisons(model_s: dict, persist_s: dict, khop_s: dict) -> dict:
    return {
        "direction_vs_persistence": _compare(model_s, persist_s, "direction",
                                             "persistence"),
        "direction_vs_k_hop": _compare(model_s, khop_s, "direction", "k_hop"),
        "magnitude_vs_persistence": _compare(model_s, persist_s, "magnitude",
                                             "persistence"),
        "onset_vs_persistence": _compare(model_s, persist_s, "onset",
                                         "persistence"),
    }


def scrub_card(card: dict) -> dict:
    """A stored card with a ``verdicts`` block (win/lose wording) is served
    with the same descriptive ``comparisons`` instead, derived
    from the card's own stored counts. Cards already in the new shape pass
    through unchanged."""
    if not isinstance(card, dict) or "verdicts" not in card:
        return card
    out = {k: v for k, v in card.items() if k != "verdicts"}
    try:
        base = card.get("baselines") or {}
        comps = _comparisons(card["metrics"], base["persistence"],
                             base["k_hop"])
    except (KeyError, TypeError):
        comps = {}
    out["comparisons"] = comps
    head = out.get("headline")
    if isinstance(head, dict):
        out["headline"] = {**{k: v for k, v in head.items()
                              if k != "verdicts"}, "comparisons": comps}
    return out


def evaluation_end(event_start: date, window_to: date,
                   duration_d: float | None) -> date:
    """Last day of the evaluation period (rule realism-v1)."""
    if duration_d is None or duration_d <= 0:
        return window_to
    return min(window_to, event_start + timedelta(days=int(math.ceil(duration_d)) - 1))


def grade(pred: dict, observed: dict, *, event_start: date, window_from: date,
          window_to: date, places: list[dict], hops: dict | None,
          seed_place: str, capacity_loss: float | None,
          duration_d: float | None = None) -> dict:
    """The pure grader.

    pred       a digest from digest_chain / digest_sim;
    observed   {place: {"served": bool, "series": {date: value}, ...}};
    places     [{"place", "role": seed|watch|control}];
    hops       directed hop counts from the seed (for the k-hop baseline).
    """
    eval_start = max(window_from, event_start)
    window_to = evaluation_end(event_start, window_to, duration_d)
    rows = []
    not_compared = []
    for pl in places:
        p = pl["place"]
        obs = observed.get(p) or {}
        if not obs.get("served"):
            not_compared.append({"place": p, "reason": obs.get("reason", "no observed series"),
                                 "detail": obs.get("detail", "")})
            continue
        orow = _observed_row(obs["series"], event_start, eval_start, window_to)
        if orow["direction"] is None:
            not_compared.append({
                "place": p, "reason": "insufficient_observed_data",
                "detail": (f"baseline days present {orow['baseline_days_present']} "
                           f"(need {RULE['baseline_min_days']}), evaluation days "
                           f"present {orow['eval_days_present']}")})
            continue
        row = {"place": p, "role": pl["role"], "observed": orow}
        # ---- model
        if pred["kind"] == "sim":
            ser = pred["series"].get(p)
            if ser is None:
                row["model"] = {"direction": None, "change_pct": None,
                                "onset_lag_d": None, "severity": None,
                                "why_no_direction": pred["unavailable"].get(
                                    p, "no sim series")}
            else:
                s = _sim_row(ser, event_start, eval_start, window_to)
                row["model"] = {
                    "direction": s["direction"], "change_pct": s["change_pct"],
                    "onset_lag_d": s["onset_lag_d"],
                    "severity": (None if s["change_pct"] is None
                                 else max(0.0, -s["change_pct"])),
                    "sim_baseline_per_day": s["baseline_per_day"],
                    "sim_baseline_days_present": s["baseline_days_present"],
                    "sim_eval_mean": s["eval_mean"],
                    "onset_date": s["onset_date"],
                    "why_no_direction": ("sim lead-in shorter than "
                                         f"{RULE['sim_baseline_min_days']} days "
                                         "before the event (fork with "
                                         "lead_in_days >= 7)")
                    if s["direction"] is None else None}
        else:
            hit = pred["places"].get(p)
            if hit:
                lag = hit["first_lag_d"]
                row["model"] = {
                    "direction": "down",
                    "change_pct": (round(-100.0 * capacity_loss, 3)
                                   if p == seed_place and capacity_loss is not None
                                   else None),
                    "onset_lag_d": lag, "severity": hit["max_delay_d"],
                    "first_hit_delay_d": hit["first_delay_d"],
                    "channels": hit["channels"]}
            else:
                row["model"] = {"direction": "flat", "change_pct": None,
                                "onset_lag_d": None, "severity": 0.0}
        # ---- persistence
        row["persistence"] = {"direction": "flat", "change_pct": 0.0,
                              "onset_lag_d": None, "severity": 0.0}
        rows.append(row)

    # ---- k-hop baseline (needs the model's predicted-down count)
    n_pred_down = sum(1 for r in rows if r["model"]["direction"] == "down")
    k_hop = {"k": None, "budget_places": None}
    if hops is not None and rows:
        budget = max(1, n_pred_down)
        ks = sorted({hops[r["place"]] for r in rows if r["place"] in hops})
        chosen = None
        for k in ks:
            if sum(1 for r in rows if hops.get(r["place"], 10 ** 6) <= k) >= budget:
                chosen = k
                break
        if chosen is None and ks:
            chosen = ks[-1]
        k_hop = {"k": chosen, "budget_places": budget}
        for r in rows:
            h = hops.get(r["place"])
            inside = h is not None and chosen is not None and h <= chosen
            r["k_hop"] = {"direction": "down" if inside else "flat",
                          "hops_from_seed": h, "change_pct": None,
                          "onset_lag_d": None,
                          "severity": (None if h is None else -float(h))}
    else:
        for r in rows:
            r["k_hop"] = {"direction": None, "hops_from_seed": None,
                          "change_pct": None, "onset_lag_d": None,
                          "severity": None,
                          "why_no_direction": "no hop distances available"}

    model_s = _score(rows, "model")
    persist_s = _score(rows, "persistence")
    khop_s = _score(rows, "k_hop")
    comparisons = _comparisons(model_s, persist_s, khop_s)
    return {
        "rows": rows, "not_compared": not_compared,
        "model": model_s,
        "baselines": {
            "persistence": {"description": "nothing changes: flat, 0 pp, no onset",
                            **persist_s},
            "k_hop": {"description": ("directed k-hop from the seed, whole "
                                      "hop levels, same place budget as the "
                                      "model's predicted-down count; "
                                      "direction + rank only"),
                      **k_hop, **khop_s},
        },
        "comparisons": comparisons,
        "n_compared": len(rows),
    }


# --------------------------------------------------------------- the store
class RealismStore:
    """Stored result cards: one JSON file per card under
    ``<data>/history_realism/`` (tracked fixtures for the seeded cases)."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def _path(self, card_id: str) -> Path:
        if (not isinstance(card_id, str) or not card_id
                or "/" in card_id or "\\" in card_id or ".." in card_id
                or "\x00" in card_id):
            raise ValueError("bad card id")
        return self.root / f"{card_id}.json"

    def save(self, card: dict) -> dict:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(card["card_id"])
        text = json.dumps(card, sort_keys=True, indent=1, ensure_ascii=False) + "\n"
        fd, tmp = tempfile.mkstemp(dir=str(self.root), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
        return card

    def load(self, card_id: str) -> dict | None:
        try:
            p = self._path(card_id)
            return scrub_card(json.loads(p.read_text(encoding="utf-8")))
        except (ValueError, OSError):
            return None

    def cards(self) -> list[dict]:
        out = []
        if not self.root.is_dir():
            return out
        for p in sorted(self.root.glob("*.json")):
            try:
                doc = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(doc, dict) and doc.get("schema") == CARD_SCHEMA:
                out.append(scrub_card(doc))
        return out

    def index(self) -> list[dict]:
        rows = []
        for c in self.cards():
            rows.append({
                "card_id": c["card_id"], "title": c.get("title"),
                "run_id": c["run"]["run_id"], "run_kind": c["run"]["kind"],
                "event_id": (c.get("event") or {}).get("event_id"),
                "window": c["window"], "label": c["label"],
                "headline": c["headline"],
                "stored_at": c.get("stored_at")})
        rows.sort(key=lambda r: (r["title"] or "", r["card_id"]))
        return rows

    def find_by_run(self, run_id: str) -> list[dict]:
        return [c for c in self.cards() if c["run"]["run_id"] == run_id]


CARD_SCHEMA = "supermap.realism_card.v1"


def _fmt_rate(r: dict) -> str:
    if not r or r.get("rate") is None:
        return "ungraded"
    return f"{r['hits']}/{r['graded']}"


def headline(model: dict, persistence: dict, k_hop: dict, comparisons: dict) -> dict:
    return {
        "direction": {"model": _fmt_rate(model["direction"]),
                      "persistence": _fmt_rate(persistence["direction"]),
                      "k_hop": _fmt_rate(k_hop["direction"])},
        "magnitude": {"model": _fmt_rate(model["magnitude"]),
                      "persistence": _fmt_rate(persistence["magnitude"])},
        "onset": {"model": _fmt_rate(model["onset"]),
                  "model_mean_abs_error_d": model["onset"].get("mean_abs_error_d"),
                  "persistence": _fmt_rate(persistence["onset"])},
        "rank_correlation": {"model": model["rank_correlation"]["spearman"],
                             "k_hop": k_hop["rank_correlation"]["spearman"]},
        "comparisons": comparisons,
    }


def build_card(*, pred: dict, observed: dict, event_start: date,
               window_from: date, window_to: date, places: list[dict],
               hops: dict | None, seed_place: str, capacity_loss,
               event: dict | None, title: str | None, notes: list[str],
               duration_d: float | None = None,
               forked_from: dict | None = None,
               include_series: bool = True) -> dict:
    g = grade(pred, observed, event_start=event_start, window_from=window_from,
              window_to=window_to, places=places, hops=hops,
              seed_place=seed_place, capacity_loss=capacity_loss,
              duration_d=duration_d)
    eval_to = evaluation_end(event_start, window_to, duration_d)
    obs_prov = []
    series_out = {}
    for pl in places:
        o = observed.get(pl["place"]) or {}
        if o.get("served"):
            obs_prov.append({"place": pl["place"], "metric": o.get("metric"),
                             "unit": o.get("unit"),
                             "source": o["provenance"]["source"],
                             "retrieved_at": o["provenance"]["retrieved_at"],
                             "mode": o["provenance"]["mode"],
                             "payload_sha256": (o.get("cache") or {}).get("payload_sha256")})
            if include_series:
                lo = iso_day(add_days(event_start, -RULE["baseline_days"]))
                series_out[pl["place"]] = {
                    "observed": [[d, v] for d, v in sorted(o["series"].items())
                                 if d >= lo],
                    "simulated": ([[d, v] for d, v in sorted(
                        pred["series"].get(pl["place"], {}).items())]
                        if pred["kind"] == "sim" else None)}
    ident = {"rule": RULE_ID, "run": pred.get("run_id"),
             "stream_hash": pred.get("stream_hash"),
             "event_start": iso_day(event_start), "from": iso_day(window_from),
             "to": iso_day(window_to), "eval_to": iso_day(eval_to),
             "places": sorted(p["place"] for p in places),
             "obs": sorted((x["place"], x["payload_sha256"]) for x in obs_prov),
             "pred": sha256_hex(canonical(pred))}
    card_id = "real_" + sha256_hex(canonical(ident))[:16]
    card = {
        "schema": CARD_SCHEMA, "card_id": card_id, "label": LABEL,
        "title": title, "rule": RULE,
        "run": {"run_id": pred.get("run_id"), "kind": pred["kind"],
                "stream_hash": pred.get("stream_hash"),
                "prediction_digest": pred if pred["kind"] == "chain" else
                {k: v for k, v in pred.items() if k != "series"}},
        "event": event, "forked_from": forked_from,
        "window": {"from": iso_day(window_from), "to": iso_day(window_to),
                   "event_start": iso_day(event_start),
                   "evaluation_from": iso_day(max(window_from, event_start)),
                   "evaluation_to": iso_day(eval_to),
                   "baseline": {"from": iso_day(add_days(event_start, -RULE["baseline_days"])),
                                "to": iso_day(add_days(event_start, -1))}},
        "places": g["rows"], "not_compared": g["not_compared"],
        "metrics": g["model"], "baselines": g["baselines"],
        "comparisons": g["comparisons"],
        "headline": headline(g["model"], g["baselines"]["persistence"],
                             g["baselines"]["k_hop"], g["comparisons"]),
        "series": series_out,
        "observed_provenance": obs_prov,
        "notes": notes,
        "caveats": [CAVEAT],
        "grader": RULE["grader"],
    }
    return card
