"""Replay episodes: a read-only assembly of curated past events, each shown as what was observed beside what the
causal-chain engines produced on the preregistered scenario.

Pure module (no web framework): ``list_episodes`` and ``build_episode`` read stored files only and return plain
dicts with the sha256 of every source file. Nothing here runs an engine, writes a file or reads the network; the
chains were recorded by ``scripts/build_replay_chains.py`` and are checked there against the published VAL-01
digests. The comparison is descriptive: each pair holds the observed side and the modelled side as stored; the
client classifies them. No accuracy, forecast or hit-rate value is computed here.

Sources, all under the repository ``data/`` tree:
  data/sim_backtest/<event>/actuals.json + PortWatch files   dated reports with publisher, date and link
  data/eval/val01/*                                          preregistered scenario, per-place observed and modelled
  data/replay/*                                              curated-record step scorecards of each engine configuration
  data/sim_backtest/scorecard_v2.json + results_v2/*         simulation-layer metrics and daily series
  data/replay_episodes/chains/*                              recorded chains (one per episode and engine)
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "supermap.replay.episode/1"
LIST_SCHEMA = "supermap.replay.episodes/1"
MODE = "RECORDED"

# ── engine configurations (as in the existing scorecards) ───────────────────────────────────────────────────────
ENGINES: list[dict[str, Any]] = [
    {"id": "v1", "label": "v1", "prereg": "data/eval/val01/preregistration.md",
     "scorecard": "data/replay/scorecard.json",
     "train": "results_train_v1.json", "test": "results_test_v1_60ea09db5731.json"},
    {"id": "rev2", "label": "Rev 2", "prereg": "data/eval/val01/preregistration.md",
     "scorecard": "data/replay/rev2/scorecard_rev2.json",
     "train": "results_train_rev2.json", "test": "results_test_rev2_631234ae39fd.json"},
    {"id": "rev2_services", "label": "Rev 2 services", "prereg": "data/replay/rev2_services/preregistration.md",
     "scorecard": "data/replay/rev2_services/scorecard.json",
     "train": "results_train_rev2_services.json", "test": "results_test_rev2_services_e7421a2feab2.json"},
    {"id": "rev2_calibrated", "label": "Rev 2 calibrated", "prereg": "data/replay/rev2_calibrated/preregistration.md",
     "scorecard": "data/replay/rev2_calibrated/scorecard.json",
     "train": "results_train_rev2_calibrated.json", "test": "results_test_rev2_calibrated_315df5adacbf.json"},
    {"id": "rev2d", "label": "Rev 2 d", "prereg": "data/replay/rev2d/preregistration.md",
     "scorecard": "data/replay/rev2d/scorecard.json",
     "train": "results_train_rev2d.json", "test": None},
]
ENGINE_IDS = tuple(e["id"] for e in ENGINES)
VAL01 = "data/eval/val01"
SIM_PREREG = "data/sim_backtest/preregistration_v2.md"
SIM_SCORECARD = "data/sim_backtest/scorecard_v2.json"
CHAINS = "data/replay_episodes/chains"

# ── the curated episodes ────────────────────────────────────────────────────────────────────────────────────────
# Timeline entries name a figure of the episode's actuals file; the link, retrieval date and wording of the value
# come from that file, the label and the short publisher are the curator's.
T = lambda figure, label, date, publisher, badge, detail=None, published=None: {  # noqa: E731
    "figure": figure, "label": label, "date": date, "publisher": publisher, "badge": badge, "detail": detail, "published": published}

EPISODES: dict[str, dict[str, Any]] = {
    "ever_given": {
        "title": "Ever Given blockage, Suez Canal", "start": "2021-03-23", "end": "2021-04-03", "restricted": False,
        "one_liner": "Ship aground in the canal for six days; queue cleared 3 April.",
        "place": "chk_suez", "val01": "evergiven_2021", "split": "TRAIN", "not_blind": False,
        "actuals": "data/sim_backtest/ever_given/actuals.json", "replay": None, "sim_event": "ever_given",
        "series": [{"id": "transits", "label": "Suez Canal transits", "unit": "per day", "place": "chk_suez",
                    "file": "data/sim_backtest/ever_given/portwatch_suez_2021.json", "field": "n_total", "source": "file"}],
        "timeline": [
            T("grounding_datetime_utc", "Grounding", "2021-03-23", "Sentinel Vision, quoting BBC News", "BBC News", "About 05:40 UTC"),
            T("queue_vessels_2021-03-28_sca_chairman", "Queue reported", "2021-03-28", "Reuters, reporting the Suez Canal Authority chairman", "Reuters",
              "At least 369 vessels", "2021-03-29"),
            T("refloat_datetime_utc", "Refloated", "2021-03-29", "Royal Boskalis Westminster, press release", "Boskalis", "13:05 UTC", "2021-03-29"),
            T("shipping_resumed_datetime_utc", "Traffic resumed", "2021-03-29", "Wikipedia, citing the Suez Canal Authority", "Wikipedia", "17:00 UTC"),
            T("peak_queue_vessels_sca_via_reuters", "Queue at refloat", "2021-03-29", "Reuters, reporting the Suez Canal Authority", "Reuters",
              "422 vessels", "2021-04-03"),
            T("backlog_fully_cleared_date", "Backlog cleared", "2021-04-03", "Reuters, reporting the Suez Canal Authority", "Reuters", None, "2021-04-03"),
        ],
        "reported": [],
    },
    "la_lb": {
        "title": "Los Angeles and Long Beach congestion", "start": "2020-10-15", "end": "2022-11-22", "restricted": False,
        "one_liner": "Ships waiting off the two ports peaked at 109 on 9 January 2022.",
        "place": "sea_losangeles", "val01": "la_2021", "split": "TEST", "not_blind": True,
        "actuals": "data/sim_backtest/la_lb/actuals.json", "replay": "la_2021", "sim_event": "la_lb",
        "series": [{"id": "portcalls", "label": "Port calls, Los Angeles and Long Beach", "unit": "per day", "place": "sea_losangeles",
                    "file": "data/sim_backtest/la_lb/portwatch_lalb_2021-2022.json", "field": "portcalls", "source": "file"}],
        "timeline": [
            T("queue_2021-02-08_point_count", "Queue count", "2021-02-08", "Marine Exchange of Southern California, via Maritime Magazine", "MXSOC",
              "108 vessels in port, 46 at anchor", "2021-02-09"),
            T("queue_2021-08-29_point_count", "Record in port", "2021-08-29", "Marine Exchange of Southern California, via Port Technology International",
              "MXSOC", "129 vessels in port", "2021-08-30"),
            T("queue_2021-11-10_point_count_and_wait", "Container ships waiting", "2021-11-10",
              "Marine Exchange of Southern California, via Western Overseas", "MXSOC", "79 at anchorage, 30 at berth"),
            T("new_queuing_system_start", "Offshore queue process begins", "2021-11-16",
              "Marine Exchange of Southern California, via Maritime Magazine", "MXSOC", None),
            T("peak_backup_2022-01-09", "Peak backup", "2022-01-09", "Marine Exchange of Southern California, via Maritime Executive", "MXSOC",
              "109 container ships waiting", "2022-11-22"),
            T("backup_declared_ended_2022-11-22", "Backup declared over", "2022-11-22",
              "Marine Exchange of Southern California, via Maritime Executive", "MXSOC", None, "2022-11-22"),
        ],
        "reported": [],
    },
    "baltimore": {
        "title": "Key Bridge collapse, Port of Baltimore", "start": "2024-03-26", "end": "2024-06-10", "restricted": False,
        "one_liner": "Port traffic suspended 26 March; full channel reopened 10 June.",
        "place": "sea_baltimore", "val01": "baltimore_2024", "split": "TRAIN", "not_blind": False,
        "actuals": "data/sim_backtest/baltimore/actuals.json", "replay": None, "sim_event": "baltimore",
        "series": [
            {"id": "baltimore", "label": "Port calls, Baltimore", "unit": "per day", "place": "sea_baltimore",
             "file": "data/sim_backtest/baltimore/portwatch_baltimore_2024.json", "field": "portcalls", "source": "figure:baltimore_portcalls_on_collapse_day"},
            {"id": "virginia", "label": "Port calls, Norfolk (Port of Virginia)", "unit": "per day", "place": "sea_virginia",
             "file": "data/sim_backtest/baltimore/portwatch_virginia_2024.json", "field": "portcalls", "source": "figure:baltimore_portcalls_on_collapse_day"},
        ],
        "timeline": [
            T("collapse_datetime", "Bridge collapse", "2024-03-26", "National Transportation Safety Board", "NTSB", "About 01:29 EDT"),
            T("phase_wreckage_removal_starts", "Wreckage removal begins", "2024-03-30", "U.S. Army Corps of Engineers, Baltimore District", "USACE"),
            T("phase_limited_access_channel_opens", "Limited access channel opens", "2024-04-25", "U.S. Army Corps of Engineers, Baltimore District", "USACE",
              "300 ft wide, 35 ft deep"),
            T("phase_dali_refloated_and_moved", "Vessel moved, channel widened", "2024-05-20", "U.S. Army Corps of Engineers, Baltimore District", "USACE",
              "400 ft wide, 50 ft deep"),
            T("phase_full_fort_mchenry_channel_reopened", "Full channel reopened", "2024-06-10", "U.S. Army Corps of Engineers, Baltimore District", "USACE",
              "700 ft wide, 50 ft deep"),
        ],
        "reported": [
            {"figure": "diversion_project44_port_of_virginia_share", "place": "sea_virginia", "label": "Port of Virginia", "published": "2024-04-22"},
            {"figure": "diversion_project44_new_york_jersey_share", "place": "sea_newyork", "label": "New York and New Jersey", "published": "2024-04-22"},
            {"figure": "diversion_project44_wilmington_delaware_share", "place": "sea_wilmingtonde", "label": "Wilmington, Delaware", "published": "2024-04-22"},
        ],
    },
    "panama": {
        "title": "Panama Canal drought limits", "start": "2023-05-24", "end": "2024-08-05", "restricted": True,
        "one_liner": "Low rainfall cut daily slots to 22; slots rose again through 2024.",
        "place": "chk_panama", "val01": "panama_2023_24", "split": "TRAIN", "not_blind": False,
        "actuals": "data/sim_backtest/panama/actuals.json", "replay": "panama_2023_24", "sim_event": "panama",
        "series": [{"id": "transits", "label": "Panama Canal transits", "unit": "per day", "place": "chk_panama",
                    "file": "data/sim_backtest/panama/portwatch_panama_2023-2024.json", "field": "n_total", "source": "file"}],
        "timeline": [
            T("draft_2023-05-24_ft", "Draft limit lowered", "2023-05-24", "Panama Canal Authority, via The Loadstar and gCaptain", "ACP", "44.5 ft"),
            T("queue_high_2023-08-09", "Queue high", "2023-08-09", "FreightWaves, from Panama Canal Authority data", "FreightWaves", "163 vessels"),
            T("slots_2023-11-03_to_2023-11-06", "Slots reduced", "2023-11-03", "Panama Canal Authority, via GAC Hot Port News", "ACP", "25 per day", "2023-10-30"),
            T("wait_time_highs_2023-11-22_argus", "Waits at record", "2023-11-22", "Argus Media", "Argus", "24 d"),
            T("slots_2023-12-01_to_2024-01-15", "Slots at low point", "2023-12-01", "Panama Canal Authority, via GAC Hot Port News", "ACP", "22 per day", "2023-10-30"),
            T("slots_2024-05-16_and_2024-06-01", "Slots raised", "2024-05-16", "Panama Canal Authority, via Hellenic Shipping News", "ACP", "31 per day", "2024-04-17"),
        ],
        "reported": [],
    },
    "red_sea": {
        "title": "Red Sea transit decline", "start": "2023-12-15", "end": "2024-03-15", "restricted": True,
        "one_liner": "Suez Canal transits fell about 41% from November 2023 to early 2024.",
        "place": "chk_babelmandeb", "val01": "redsea_2024", "split": "TRAIN", "not_blind": False,
        "actuals": "data/sim_backtest/red_sea/actuals.json", "replay": "red_sea_2024", "sim_event": "red_sea",
        "series": [
            {"id": "suez", "label": "Suez Canal transits", "unit": "per day", "place": "chk_suez",
             "file": "data/sim_backtest/red_sea/portwatch_suez_redsea.json", "field": "n_total", "source": "portwatch_service"},
            {"id": "babelmandeb", "label": "Bab el-Mandeb transits", "unit": "per day", "place": "chk_babelmandeb",
             "file": "data/sim_backtest/red_sea/portwatch_babelmandeb_redsea.json", "field": "n_total", "source": "portwatch_service"},
        ],
        "timeline": [
            T("maersk_pause_red_sea", "Carrier pause announced", "2023-12-15", "Reuters, via TM Duche reproduction", "Reuters", None, "2023-12-18"),
            T("hapag_lloyd_reroute_decision", "Carrier reroute announced", "2023-12-18", "Reuters, via TM Duche reproduction", "Reuters", None, "2023-12-18"),
            T("cma_cgm_full_cape_routing_2024_02_01", "Cape routing effective", "2024-02-01", "CMA CGM, customer advisory", "CMA CGM", None, "2024-02-07"),
        ],
        "reported": [],
    },
}
DEFAULT_ORDER = ["ever_given", "la_lb", "baltimore", "panama", "red_sea"]

# metric id -> (unit, is a timing metric in days)
METRIC_UNITS: dict[str, tuple[str, bool]] = {
    "EG-1": ("d of flow", False), "EG-2": ("d", False), "EG-3": ("d", True),
    "BA-1": ("%", False), "BA-2": ("%", False), "BA-2v": ("%", False), "BA-3": ("d", True),
    "LB-1": ("d of flow", False), "LB-2": ("%", False), "LB-3": ("d", True),
    "PA-1": ("%", False), "PA-2": ("d of flow", False), "PA-3": ("d", False),
    "RS-1": ("%", False), "RS-2": ("d", False), "RS-3": ("%", False),
}
# short subject labels of the simulation metrics (the registered wording is long)
METRIC_LABELS: dict[str, str] = {
    "EG-1": "Excess peak queue", "EG-2": "Mean added delay", "EG-3": "Clearance after reopening",
    "BA-1": "Port-call drop at the closed port", "BA-2": "Calls at the main substitute port", "BA-2v": "Calls and tonnage at the main substitute port",
    "BA-3": "Recovery after full reopening", "LB-1": "Excess peak queue", "LB-2": "Port-call change in the peak window", "LB-3": "Clearance after the window",
    "PA-1": "Transit drop, tightest phase", "PA-2": "Excess peak queue", "PA-3": "Mean added delay",
    "RS-1": "Suez transit drop", "RS-2": "Mean lateness of arrivals", "RS-3": "Flow change on lanes through both straits",
}
SIM_SERIES: dict[str, list[dict[str, str]]] = {
    "ever_given": [{"id": "queue", "label": "Queue at Suez", "unit": "vessels", "disrupted": "queue_disrupted", "twin": "queue_twin"}],
    "baltimore": [{"id": "baltimore", "label": "Berth calls, Baltimore", "unit": "per day", "disrupted": "berth_baltimore_disrupted", "twin": "berth_baltimore_twin"},
                  {"id": "virginia", "label": "Berth calls, Virginia", "unit": "per day", "disrupted": "berth_virginia_disrupted", "twin": "berth_virginia_twin"}],
    "la_lb": [{"id": "la", "label": "Berth calls, Los Angeles", "unit": "per day", "disrupted": "berth_la_disrupted", "twin": "berth_la_twin"}],
}
SIM_RAW = {"ever_given": "ever_given", "baltimore": "baltimore", "la_lb": "la_lb", "panama": "panama", "red_sea": "red_sea"}

# ── file access ─────────────────────────────────────────────────────────────────────────────────────────────────
_CACHE: dict[tuple, Any] = {}
_USED: list[str] = []


def _stat(p: Path) -> tuple[int, int]:
    s = p.stat()
    return s.st_mtime_ns, s.st_size


def _raw(root: Path, rel: str) -> bytes:
    p = root / rel
    key = ("raw", str(root), rel, _stat(p))
    if key not in _CACHE:
        _CACHE[key] = p.read_bytes()
    return _CACHE[key]


def sha(root: Path, rel: str) -> str:
    key = ("sha", str(root), rel, _stat(root / rel))
    if key not in _CACHE:
        _CACHE[key] = hashlib.sha256(_raw(root, rel)).hexdigest()
    return _CACHE[key]


def jload(root: Path, rel: str) -> Any:
    key = ("json", str(root), rel, _stat(root / rel))
    if key not in _CACHE:
        _CACHE[key] = json.loads(_raw(root, rel).decode("utf-8"))
    return _CACHE[key]


def _exists(root: Path, rel: str) -> bool:
    return (root / rel).is_file()


class _Files:
    """Collects the source files an episode document was assembled from."""

    def __init__(self, root: Path):
        self.root = root
        self.rows: dict[str, dict[str, str]] = {}

    def use(self, rel: str, role: str) -> dict[str, str]:
        if rel not in self.rows:
            self.rows[rel] = {"file": rel, "sha256": sha(self.root, rel), "role": role}
        return {"file": rel, "sha256": self.rows[rel]["sha256"]}

    def as_list(self) -> list[dict[str, str]]:
        return sorted(self.rows.values(), key=lambda r: r["file"])


# ── small helpers ───────────────────────────────────────────────────────────────────────────────────────────────
def _num(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v) if v == v and v not in (float("inf"), float("-inf")) else None


def _day(text: str) -> date:
    return date.fromisoformat(text[:10])


def _add_days(text: str, d: float) -> str:
    base = datetime.fromisoformat(text.replace("Z", "+00:00")) if "T" in text else datetime.fromisoformat(text + "T00:00:00+00:00")
    return (base + timedelta(days=d)).date().isoformat()


def _fig(actuals: dict, key: str) -> dict | None:
    for f in actuals.get("figures", []):
        if f.get("name") == key or f.get("id") == key:
            return f
    return None


_URL = re.compile(r"https?://[^\s)\]>,;]+")


def split_source(text: str) -> tuple[str, str | None]:
    """'MESocal via Seatrade Maritime: https://x' -> ('MESocal via Seatrade Maritime', 'https://x')."""
    m = _URL.search(text or "")
    url = m.group(0).rstrip(".") if m else None
    head = (text[:m.start()] if m else text or "").strip().rstrip(":").strip()
    return head, url


# ── observed side ───────────────────────────────────────────────────────────────────────────────────────────────
def _series_rows(doc: Any) -> list[dict]:
    return doc if isinstance(doc, list) else doc.get("rows", [])


def _observed(ep: dict, files: _Files, root: Path) -> dict:
    actuals = jload(root, ep["actuals"])
    files.use(ep["actuals"], "dated reports")
    events: list[dict] = []
    for i, t in enumerate(ep["timeline"]):
        f = _fig(actuals, t["figure"])
        if f is None:
            continue
        url = f.get("source_url")
        events.append({
            "id": f"{t['figure']}", "order": i, "date": t["date"], "label": t["label"], "detail": t["detail"],
            "publisher": t["publisher"], "badge": t["badge"], "published": t["published"] or t["date"],
            "url": url, "retrieved_on": f.get("retrieved_on"),
        })
    events.sort(key=lambda e: (e["date"], e["order"]))
    series = []
    for s in ep["series"]:
        doc = jload(root, s["file"])
        files.use(s["file"], "PortWatch series")
        if s["source"] == "file":
            src = {"url": doc.get("source_url"), "retrieved_on": doc.get("retrieved_on")}
        elif s["source"].startswith("figure:"):
            f = _fig(actuals, s["source"].split(":", 1)[1]) or {}
            src = {"url": f.get("source_url"), "retrieved_on": f.get("retrieved_on")}
        else:  # portwatch_service: the actuals file names the service
            pw = actuals.get("portwatch_service") or {}
            src = {"url": pw.get("url"), "retrieved_on": actuals.get("retrieved_on")}
        pts = []
        for r in _series_rows(doc):
            v = _num(r.get(s["field"]))
            if r.get("date") and v is not None:
                pts.append([str(r["date"])[:10], v])
        series.append({"id": s["id"], "label": s["label"], "unit": s["unit"], "place_id": s["place"], "metric": s["field"],
                       "publisher": "IMF PortWatch", **src, "points": pts})
    reported = []
    for r in ep.get("reported", []):
        f = _fig(actuals, r["figure"])
        if f is None:
            continue
        reported.append({"id": r["figure"], "place_id": r["place"], "label": r["label"], "value": _num(f.get("value")), "unit": f.get("unit"),
                         "publisher": split_source(f.get("source_name", ""))[0], "url": f.get("source_url"), "published": r["published"],
                         "retrieved_on": f.get("retrieved_on")})
    return {"events": events, "series": series, "reported": reported}


# ── VAL-01 side: the preregistered scenario, observed and modelled places ───────────────────────────────────────
def _val01_episode(root: Path, vid: str) -> dict | None:
    for e in jload(root, f"{VAL01}/episodes.json")["episodes"]:
        if e["id"] == vid:
            return e
    return None


def _disruption(root: Path, files: _Files, ep: dict) -> dict | None:
    v = _val01_episode(root, ep["val01"])
    if v is None:
        return None
    files.use(f"{VAL01}/episodes.json", "preregistered scenario")
    return {"place_id": v["seed"], "kind": v["kind"], "capacity_loss": v["capacity_loss"], "duration_d": v["duration_d"],
            "started_at": v["started_at"], "basis": v.get("capacity_loss_basis", {}).get("class")}


def _result_doc(root: Path, eng: dict, split: str | None) -> tuple[str, dict] | None:
    if split not in ("TRAIN", "TEST"):
        return None
    name = eng["train" if split == "TRAIN" else "test"]
    if not name:
        return None
    rel = f"{VAL01}/{name}"
    return (rel, jload(root, rel)) if _exists(root, rel) else None


def _chain(root: Path, rid: str, eid: str) -> dict | None:
    rel = f"{CHAINS}/{rid}__{eid}.json"
    return (jload(root, rel), rel)[0] if _exists(root, rel) else None


_RUN_ID = re.compile(r"^replay_[a-z0-9_]{1,60}$")
_ENGINE_LABEL = {"v1": "ENGINE REV 1"}


def recorded_run_record(run_id: str, root: Path | None = None) -> dict | None:
    """A run record (the shape the run store returns) for a recorded replay chain id such as
    ``replay_ever_given_v1``; None when no recorded chain carries that id. Read-only: nothing is stored."""
    if not isinstance(run_id, str) or not _RUN_ID.match(run_id):
        return None
    root = Path(root or REPO_ROOT)
    d = root / CHAINS
    if not d.is_dir():
        return None
    for f in sorted(d.glob("*__*.json")):
        stem = f.stem
        if f"replay_{stem.split('__')[0]}_{stem.split('__', 1)[1]}" != run_id:
            continue
        chain = jload(root, f"{CHAINS}/{f.name}")
        if not isinstance(chain, dict) or chain.get("run_id") != run_id or chain.get("status") != "complete":
            return None
        cfg = str(chain.get("config") or "")
        label = _ENGINE_LABEL.get(cfg) or ("ENGINE REV " + cfg.split("rev", 1)[1][:1] if cfg.startswith("rev") else "ENGINE REV 1")
        return {"run_id": run_id, "status": "complete", "event_count": chain.get("event_count"),
                "stream_hash": chain.get("stream_hash"),
                "journal": {"kind": "replay", "disruption": dict(chain["disruption"]), "engine_label": label,
                            "recorded_replay": True, "replay_episode": chain.get("episode"), "replay_config": cfg},
                "events": list(chain.get("events") or []), "impacts": {}, "not_hit": []}
    return None


def _timing_pairs(entry: dict, digest: dict, disr: dict | None) -> list[dict]:
    """Seed onset and recovery, in days after the stated start (VAL-01 rule bands)."""
    out = []
    seed = entry.get("seed")
    obs = entry.get("seed_observed") or {}
    a, c = entry.get("a_onset") or {}, entry.get("c_recovery") or {}
    start = disr["started_at"] if disr else None

    def pair(key: str, label: str, g: dict, obs_date: str | None, model_lag, band) -> dict:
        p = {"key": key, "basis": "portwatch", "kind": "timing", "subject": {"place_id": seed, "label": label}, "unit": "d"}
        if g.get("graded"):
            p["observed"] = {"lag_d": g["observed_lag_d"], "date": obs_date}
            p["model"] = {"lag_d": model_lag, "date": _add_days(start, model_lag) if (start and model_lag is not None) else None}
            p["band"] = band(g["observed_lag_d"])
        else:
            reason = g.get("reason") or "not graded"
            p["observed"] = None
            p["model"] = {"lag_d": model_lag}
            p["ungraded"] = reason
        return p

    out.append(pair("seed:onset", "Onset", a, obs.get("onset_date"), (a.get("predicted") or {}).get("model") if a.get("graded") else digest.get("seed_first_lag_d"),
                    lambda o: {"abs_d": 7.0}))
    out.append(pair("seed:recovery", "Recovery", c, None, (c.get("predicted") or {}).get("model") if c.get("graded") else None,
                    lambda o: {"abs_d": max(7.0, 0.25 * o)}))
    return out


def _portwatch_pairs(root: Path, files: _Files, ep: dict, eng: dict, disr: dict | None) -> tuple[list[dict], dict]:
    """(pairs, info) from the published VAL-01 result of this engine on this episode."""
    res = _result_doc(root, eng, ep["split"])
    if res is None:
        return [], {"state": "no_result"}
    rel, doc = res
    files.use(rel, f"VAL-01 {ep['split']} result, {eng['id']}")
    vid = ep["val01"]
    for r in doc.get("engine_refused", []):
        if r.get("episode_id") == vid:
            return [], {"state": "refused", "result": rel, "config_hash": doc.get("config_hash")}
    entry = next((e for e in doc.get("episodes", []) if e.get("episode_id") == vid), None)
    if entry is None:
        return [], {"state": "no_result", "result": rel}
    digest = entry.get("prediction_digest") or {}
    lags = {k: v.get("first_lag_d") for k, v in (digest.get("places") or {}).items()}
    pairs = _timing_pairs(entry, digest, disr)
    down = entry.get("d_downstream") or {}
    for row in down.get("rows", []):
        o, m = row.get("observed") or {}, row.get("model") or {}
        place = row["place"]
        pairs.append({
            "key": f"place:{place}", "basis": "portwatch", "kind": "place", "subject": {"place_id": place}, "role": row.get("role"), "hops": row.get("hops"),
            "observed": {"direction": o.get("direction"), "change_pct": o.get("change_pct"), "baseline_per_day": o.get("baseline_per_day")},
            "model": {"direction": m.get("direction"), "delay_d": m.get("severity"), "lag_d": lags.get(place)},
        })
    for nc in down.get("not_compared", []):
        pairs.append({"key": f"place:{nc['place']}", "basis": "portwatch", "kind": "place", "subject": {"place_id": nc["place"]}, "role": nc.get("role"),
                      "observed": None, "model": None, "not_compared": nc.get("reason")})
    return pairs, {"state": "ok", "result": rel, "config_hash": doc.get("config_hash")}


def _record_pairs(root: Path, files: _Files, ep: dict, eng: dict, chain: dict | None) -> tuple[list[dict], dict]:
    rid = ep.get("replay")
    if not rid or not _exists(root, eng["scorecard"]):
        return [], {"state": "no_scorecard"}
    sc = jload(root, eng["scorecard"])
    rep = next((r for r in sc.get("replays", []) if r.get("id") == rid), None)
    if rep is None:
        return [], {"state": "no_scorecard"}
    files.use(eng["scorecard"], f"curated-record scorecard, {eng['id']}")
    digest = (chain or {}).get("digest", {}).get("places", {})
    start = (rep.get("effective_disruption") or {}).get("started_at")
    pairs = []
    for s in rep.get("steps", []):
        if s.get("result") == "input":
            continue
        pub, url = split_source(s.get("evidence_source", ""))
        node = s["node_id"]
        dg = digest.get(node)
        flagged = s.get("predicted_delay_d") is not None
        od = _num(s.get("observed_delay_d")) or 0.0
        pairs.append({
            "key": f"step:{s.get('index')}:{node}", "basis": "dossier", "kind": "step", "subject": {"place_id": node},
            "observed": {"date": s.get("evidence_date"), "delay_d": od if od > 0 else None, "publisher": pub, "url": url, "text": s.get("effect")},
            "model": {"flagged": flagged, "delay_d": s.get("predicted_delay_d"), "lag_d": dg.get("first_lag_d") if dg else None,
                      "date": _add_days(start, dg["first_lag_d"]) if (dg and start and dg.get("first_lag_d") is not None) else None},
            "window": s.get("window"),
        })
    for node in rep.get("predicted_unobserved", []):
        dg = digest.get(node)
        pairs.append({"key": f"unobserved:{node}", "basis": "dossier", "kind": "step", "subject": {"place_id": node}, "observed": None,
                      "model": {"flagged": True, "delay_d": dg.get("first_delay_d") if dg else None, "lag_d": dg.get("first_lag_d") if dg else None,
                                "date": _add_days(start, dg["first_lag_d"]) if (dg and start and dg.get("first_lag_d") is not None) else None}})
    return pairs, {"state": "ok", "scorecard": eng["scorecard"], "stream_hash": rep.get("stream_hash")}


def _engines(root: Path, files: _Files, ep: dict, disr: dict | None) -> list[dict]:
    out = []
    for eng in ENGINES:
        chain = _chain(root, ep_id(ep), eng["id"])
        pw_pairs, pw = _portwatch_pairs(root, files, ep, eng, disr) if ep["val01"] else ([], {"state": "no_result"})
        ds_pairs, ds = _record_pairs(root, files, ep, eng, chain)
        refused = pw.get("state") == "refused"
        available = (chain is not None) and (pw["state"] == "ok" or ds["state"] == "ok")
        reason = None
        if refused:
            reason = "outside_core"        # the seed is a shell-tier place: the engines accept core places only
        elif not available:
            reason = "not_run"             # no scored run of this configuration on this episode (single TEST run rule)
        doc: dict[str, Any] = {"id": eng["id"], "label": eng["label"], "available": available, "reason": reason}
        if available:
            doc["prereg"] = {"name": Path(eng["prereg"]).name, **files.use(eng["prereg"], "preregistration")}
            if pw.get("config_hash"):
                doc["config_hash"] = pw["config_hash"]
            if ds["state"] == "ok":
                doc["scorecard"] = {"name": Path(eng["scorecard"]).name, **files.use(eng["scorecard"], f"curated-record scorecard, {eng['id']}")}
            else:
                doc["scorecard"] = {"name": Path(pw["result"]).name, **files.use(pw["result"], f"VAL-01 result, {eng['id']}")}
            cf = f"{CHAINS}/{ep_id(ep)}__{eng['id']}.json"
            files.use(cf, f"recorded chain, {eng['id']}")
            doc["chain"] = chain
            doc["pairs"] = pw_pairs + ds_pairs
        out.append(doc)
    return out


def ep_id(ep: dict) -> str:
    return ep["_id"]


# ── simulation layer ────────────────────────────────────────────────────────────────────────────────────────────
def _simulation(root: Path, files: _Files, ep: dict, observed: dict) -> dict | None:
    ev = ep["sim_event"]
    if not ev or not _exists(root, SIM_SCORECARD):
        return None
    sc = jload(root, SIM_SCORECARD)
    files.use(SIM_SCORECARD, "simulation scorecard")
    files.use(SIM_PREREG, "simulation preregistration")
    pairs = []
    for m in sc.get("metrics", []):
        if m.get("event") != ev:
            continue
        unit, timing = METRIC_UNITS.get(m["id"], ("", False))
        pairs.append({
            "key": f"metric:{m['id']}", "basis": "simulation", "kind": "metric", "metric_id": m["id"], "subject": {"label": METRIC_LABELS.get(m["id"], m["metric"])},
            "unit": unit, "timing": timing, "band_text": m.get("band"),
            "observed": ({"value": m.get("observed")} if m.get("observed") is not None else None),
            "model": {"value": m.get("sim"), "previous": m.get("v1_sim")},
            "grade": m.get("grade"), "previous_grade": m.get("v1_grade"), "obs_detail": m.get("obs_detail") or None, "sim_detail": m.get("sim_detail") or None,
        })
    series = []
    raw = None
    rel_raw = f"data/sim_backtest/results_v2/{SIM_RAW[ev]}_raw.json"
    if _exists(root, rel_raw):
        raw = jload(root, rel_raw)
        files.use(rel_raw, "simulation daily series")
    if raw and ev in SIM_SERIES:
        for s in SIM_SERIES[ev]:
            if s["disrupted"] in raw and s["twin"] in raw:
                series.append({"id": s["id"], "label": s["label"], "unit": s["unit"], "start": str(raw["start_at"])[:10],
                               "disrupted": raw[s["disrupted"]], "twin": raw[s["twin"]]})
    # reported figures against what the simulator did with them
    acc = ((raw or {}).get("reassignment_accounting") or {}).get("sea_baltimore", {}).get("substitutes", {}) if raw else {}
    for r in observed["reported"]:
        sub = acc.get(r["place_id"])
        pairs.append({
            "key": f"reported:{r['id']}", "basis": "reported", "kind": "reported", "subject": {"place_id": r["place_id"], "label": r["label"]},
            "unit": "%", "observed": {"value": r["value"], "text": "of Baltimore-bound containers rerouted", "publisher": r["publisher"], "url": r["url"], "date": r["published"]},
            "model": {"value": sub.get("calls") if sub else None, "text": "calls reassigned", "unit": "calls"},
        })
    return {"id": "sim", "label": "Simulation layer", "prereg": {"name": Path(SIM_PREREG).name, **files.use(SIM_PREREG, "simulation preregistration")},
            "scorecard": {"name": Path(SIM_SCORECARD).name, **files.use(SIM_SCORECARD, "simulation scorecard")},
            "pairs": pairs, "series": series}


# ── public API ──────────────────────────────────────────────────────────────────────────────────────────────────
def _card(eid: str, ep: dict, root: Path) -> dict:
    badges = ["PortWatch"]
    for t in ep["timeline"]:
        if t["badge"] not in badges:
            badges.append(t["badge"])
    return {"id": eid, "title": ep["title"], "date_range": {"start": ep["start"], "end": ep["end"]}, "one_liner": ep["one_liner"],
            "restricted": ep["restricted"], "place_id": ep["place"], "badges": badges[:5],
            "status": {"split": ep["split"], "not_blind": ep["not_blind"]}}


def list_episodes(root: Path | None = None, include_restricted: bool = False) -> dict:
    root = Path(root or REPO_ROOT)
    cards = []
    for eid in DEFAULT_ORDER:
        ep = EPISODES[eid]
        if ep["restricted"] and not include_restricted:
            continue
        cards.append(_card(eid, ep, root))
    return {"schema": LIST_SCHEMA, "mode": MODE, "include_restricted": bool(include_restricted), "episodes": cards}


def build_episode(eid: str, root: Path | None = None) -> dict | None:
    root = Path(root or REPO_ROOT)
    base = EPISODES.get(eid)
    if base is None:
        return None
    ep = {**base, "_id": eid}
    files = _Files(root)
    observed = _observed(ep, files, root)
    disr = _disruption(root, files, ep)
    engines = _engines(root, files, ep, disr)
    sim = _simulation(root, files, ep, observed)
    window = {"start": ep["start"], "end": ep["end"]}
    dates = [p[0] for s in observed["series"] for p in s["points"]]
    if dates:
        window = {"start": min(dates), "end": max(dates)}
    return {
        "schema": SCHEMA, "mode": MODE, **_card(eid, ep, root), "window": window, "disruption": disr,
        "observed": observed, "engines": engines, "simulation": sim,
        "provenance": {"files": files.as_list(), "assembled_from": "stored files", "mode": MODE},
    }
