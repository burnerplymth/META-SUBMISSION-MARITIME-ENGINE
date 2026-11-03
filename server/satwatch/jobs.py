"""Scan jobs: create-then-poll orchestration over ``scan.run_scan``.

    POST /satwatch/scan {places: [...] | "all", days?, mint?}  -> 202 job
    GET  /satwatch/scan/{job_id}                               -> job
    GET  /satwatch/scan                                        -> recent jobs

A job iterates the requested anchorage crops sequentially (one worker
thread, bounded queue): STAC search with cloud screening, crops, the
detector over BOTH crops, record write. Each place ends in exactly one
honest state:

    done      record written; usable (now + baseline) pair found
    degraded  record written but imagery is thin: no covering scene, no
              usable pair under the AOI cloud gate, or catalog failover
              without croppable ids (the record's ``notes`` say which)
    failed    the place raised (STAC down, detector refused, ...); the
              error class and a short message are kept, nothing is faked
    pending / running   not finished yet

``detection_status`` per place is the detector's own (AWAITING_META_KEY
when no detector ran; counts stay null). The job NEVER fabricates a
result for a place that failed, and a feed outage for one place never
aborts the others. Jobs live in memory (restart forgets the list; the
written scan records persist).
"""
from __future__ import annotations

import queue
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from . import scan as scan_mod
from .detected import stamp
from .detect import DetectorConfigError, get_detector

MAX_JOBS_KEPT = 50
MAX_QUEUED = 4
MAX_PLACES_PER_JOB = 40
MAX_DAYS = 365


class JobBusy(RuntimeError):
    """Too many jobs queued."""


class UnknownPlace(ValueError):
    pass


class MintNeedsReview(ValueError):
    """A scan job asked to mint: scans never mint, a reviewed mint does."""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _short(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}"
    return text[:300]


class ScanJobManager:
    def __init__(self, out_dir: Path, *, aois_path: Path = scan_mod.AOIS_PATH,
                 detector_factory: Callable[[Optional[str]], object] = get_detector,
                 runner: Callable[..., dict] = scan_mod.run_scan,
                 http=None, mint_fn: Optional[Callable[[dict], dict]] = None,
                 start_thread: bool = True) -> None:
        self.out_dir = Path(out_dir)
        self.aois_path = Path(aois_path)
        self._detector_factory = detector_factory
        self._runner = runner
        self._http = http
        self._mint_fn = mint_fn
        self._jobs: dict[str, dict] = {}
        self._order: list[str] = []
        self._queue: "queue.Queue[str]" = queue.Queue()
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._start_thread = start_thread

    # ----- registry -------------------------------------------------------
    def aois(self) -> dict[str, dict]:
        return scan_mod.load_aois(self.aois_path)

    def places(self) -> list[dict]:
        """Every configured anchorage, with its placement honesty flags and
        the state of its last stored scan (None when never scanned)."""
        out = []
        for node_id, a in self.aois().items():
            last = None
            rec_path = self.out_dir / f"{node_id}.json"
            if rec_path.is_file():
                try:
                    import json
                    rec = json.loads(rec_path.read_text(encoding="utf-8"))
                    last = {"retrieved_at": rec.get("retrieved_at"),
                            "imagery_dates": rec.get("imagery_dates"),
                            "usable_pair": rec.get("usable_pair"),
                            "detection_status": (rec.get("detection") or {}).get("status")}
                except Exception:
                    last = None
            out.append({
                "node_id": node_id, "name": a["name"],
                "placement": a.get("placement", "CURATED"),
                "anchorage_verified": a.get("anchorage_verified", True),
                "core_bbox": a["core_bbox"],
                "capacity_rank": a.get("capacity_rank"),
                "last_scan": last,
            })
        return out

    # ----- submit / read --------------------------------------------------
    def submit(self, places, days: int = 90, mint: bool = False,
               detector: Optional[str] = None) -> dict:
        aois = self.aois()
        if places == "all":
            wanted = list(aois)
        elif isinstance(places, list) and places and all(isinstance(p, str) for p in places):
            wanted = list(dict.fromkeys(places))
        else:
            raise ValueError('places must be a non-empty list of node ids or "all"')
        unknown = [p for p in wanted if p not in aois]
        if unknown:
            raise UnknownPlace(f"unknown anchorage(s): {unknown}")
        if len(wanted) > MAX_PLACES_PER_JOB:
            raise ValueError(f"at most {MAX_PLACES_PER_JOB} places per job")
        if not isinstance(days, int) or isinstance(days, bool) or not 7 <= days <= MAX_DAYS:
            raise ValueError(f"days must be an integer in 7..{MAX_DAYS}")
        if mint:
            raise MintNeedsReview(
                "a scan never mints: review the scan, then POST "
                "/satwatch/signals/mint with the reviewed places")
        det = self._detector_factory(detector)   # DetectorConfigError -> caller (422)
        with self._lock:
            pending = sum(1 for j in self._jobs.values()
                          if j["status"] in ("queued", "running"))
            if pending >= MAX_QUEUED:
                raise JobBusy(f"{pending} scan jobs already queued/running")
            job_id = "scan_" + uuid.uuid4().hex[:12]
            job = {
                "job_id": job_id, "status": "queued", "created_at": _now(),
                "started_at": None, "finished_at": None,
                "request": {"places": "all" if places == "all" else wanted,
                            "days": days, "mint": bool(mint),
                            "detector": detector},
                "detector": {"id": getattr(det, "name", None),
                             "label": getattr(det, "label", None)},
                "progress": {"total": len(wanted), "finished": 0},
                "items": [{"node_id": n, "name": aois[n]["name"], "status": "pending",
                           "usable_pair": None, "imagery_dates": None,
                           "detection_status": None, "counts": None,
                           "signal": None, "error": None, "notes": []}
                          for n in wanted],
            }
            self._jobs[job_id] = job
            self._order.append(job_id)
            while len(self._order) > MAX_JOBS_KEPT:
                old = self._order.pop(0)
                if self._jobs.get(old, {}).get("status") in ("done", "failed", "partial"):
                    self._jobs.pop(old, None)
                else:
                    self._order.insert(0, old)
                    break
            self._queue.put(job_id)
        self._ensure_worker()
        return self._snapshot(job)

    def get(self, job_id: str) -> Optional[dict]:
        with self._lock:
            job = self._jobs.get(job_id)
            return self._snapshot(job) if job else None

    def list_jobs(self) -> list[dict]:
        with self._lock:
            return [self._snapshot(self._jobs[i]) for i in reversed(self._order)
                    if i in self._jobs]

    @staticmethod
    def _snapshot(job: dict) -> dict:
        import copy
        return copy.deepcopy(job)

    # ----- execution ------------------------------------------------------
    def _ensure_worker(self) -> None:
        if not self._start_thread:
            return
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, daemon=True,
                                                name="satwatch-scan-worker")
                self._thread.start()

    def _loop(self) -> None:
        while True:
            try:
                job_id = self._queue.get(timeout=30)
            except queue.Empty:
                return
            self._run_job(job_id)

    def drain(self) -> None:
        """Run every queued job on the calling thread."""
        while True:
            try:
                job_id = self._queue.get_nowait()
            except queue.Empty:
                return
            self._run_job(job_id)

    def _set(self, job: dict, **kw) -> None:
        with self._lock:
            job.update(kw)

    def _run_job(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            return
        self._set(job, status="running", started_at=_now())
        aois = self.aois()
        try:
            detector = self._detector_factory(job["request"].get("detector"))
        except DetectorConfigError as exc:  # env changed since submit
            for it in job["items"]:
                it.update(status="failed", error=_short(exc))
            self._set(job, status="failed", finished_at=_now())
            return
        days = job["request"]["days"]
        for item in job["items"]:
            with self._lock:
                item["status"] = "running"
            try:
                kw = {"http": self._http} if self._http is not None else {}
                rec = self._runner(aois[item["node_id"]], self.out_dir, days=days,
                                   detector=detector, **kw)
                det = rec.get("detection") or {}
                with self._lock:
                    item.update(
                        status="done" if rec.get("usable_pair") else "degraded",
                        usable_pair=rec.get("usable_pair"),
                        imagery_dates=rec.get("imagery_dates"),
                        detection_status=det.get("status"),
                        counts=det.get("counts"),
                        **stamp(det.get("counts")),
                        notes=list(rec.get("notes") or []))
                if job["request"]["mint"] and self._mint_fn is not None:
                    try:
                        sig = self._mint_fn(rec)
                    except Exception as exc:  # a mint failure never fails the scan
                        sig = {"minted": False, "reasons": [f"mint_error: {_short(exc)}"]}
                    with self._lock:
                        item["signal"] = {"minted": sig.get("minted"),
                                          "reasons": sig.get("reasons"),
                                          "event_id": (sig.get("event") or {}).get("id")}
            except Exception as exc:
                with self._lock:
                    item.update(status="failed", error=_short(exc))
            with self._lock:
                job["progress"]["finished"] += 1
        failed = sum(1 for i in job["items"] if i["status"] == "failed")
        status = "failed" if failed == len(job["items"]) else \
            "partial" if failed else "done"
        self._set(job, status=status, finished_at=_now())
