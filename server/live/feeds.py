"""Feed poller: reads what the adapters already persisted and feeds the hub.

The adapters (and the ingestion cycle in scripts/serve_api.py or an
external ``adapters.run_once``) write three things this module reads;
it never calls an adapter's fetch() and never changes an adapter:

  * ``<data>/snapshots/<adapter>.json`` — the latest pack per adapter
    (records + provenance). Re-read only when (mtime_ns, size) changes.
  * ``<data>/events.jsonl`` — disruption events. Re-read on change.
  * ``<data>/records.jsonl`` — the append-only RecordStore. TAILED
    incrementally (byte offset); only vessel / aircraft / hub-count rows
    are parsed. This is where vessel and aircraft HISTORY comes from, so
    tracks can hold more than the latest cycle.

Per-feed health combines the pack provenance (what the feed last
answered, and when) with the in-process registry health entry when one
exists. All file reads happen on the poller thread, never on the event
loop.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

from .hub import FEED_IDS, LiveHub

try:
    from store.store import _fold_events
except ImportError:                                  # pragma: no cover
    from server.store.store import _fold_events

log = logging.getLogger("supermap.live")

TAIL_INITIAL_BYTES = 8 * 1024 * 1024     # cold start reads at most this
TAIL_STEP_BYTES = 8 * 1024 * 1024        # and at most this per poll
_TAIL_MARKERS = ('"ais_position"', '"air_traffic"', '"icao24"',
                 '"aircraft_state"', '"aircraft_position"',
                 '"air_position"', '"air_state"')


def _sig(path: Path):
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


class JsonlTail:
    """Incremental reader of an append-only JSONL file."""

    def __init__(self, path: Path, markers=_TAIL_MARKERS,
                 initial_bytes: int = TAIL_INITIAL_BYTES,
                 step_bytes: int = TAIL_STEP_BYTES):
        self.path = path
        self.markers = markers
        self.initial_bytes = initial_bytes
        self.step_bytes = step_bytes
        self.offset: int | None = None
        self.bad_lines = 0

    def read_new(self) -> list[dict]:
        try:
            size = self.path.stat().st_size
        except OSError:
            return []
        start = self.offset
        skip_partial = False
        if start is None:
            start = max(0, size - self.initial_bytes)
            skip_partial = start > 0
        elif size < start:            # truncated / rotated: start over
            start = max(0, size - self.initial_bytes)
            skip_partial = start > 0
        if size == start:
            self.offset = start
            return []
        want = min(size - start, self.step_bytes)
        with self.path.open("rb") as fh:
            fh.seek(start)
            blob = fh.read(want)
        cut = blob.rfind(b"\n")
        if cut < 0:                   # no complete line yet
            if want < size - start:   # a single line bigger than the step
                self.offset = start + want
            return []
        blob = blob[:cut + 1]
        self.offset = start + cut + 1
        lines = blob.split(b"\n")
        if skip_partial:
            lines = lines[1:]
        out = []
        for raw in lines:
            if not raw.strip():
                continue
            text = raw.decode("utf-8", "replace")
            if not any(m in text for m in self.markers):
                continue
            try:
                row = json.loads(text)
            except ValueError:
                self.bad_lines += 1
                continue
            if isinstance(row, dict):
                out.append(row)
        return out


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


class FeedPoller:
    def __init__(self, hub: LiveHub, *, data_root, events_path=None,
                 health_fn=None, interval_s: float = 2.0):
        self.hub = hub
        self.data_root = Path(data_root)
        self.snap_dir = self.data_root / "snapshots"
        self.events_path = Path(events_path) if events_path \
            else self.data_root / "events.jsonl"
        self.records_path = self.data_root / "records.jsonl"
        self.health_fn = health_fn
        self.interval_s = max(0.05, float(interval_s))
        self._pack_sig: dict = {}
        self._pack_meta: dict = {}
        self._events_sig = None
        self._tail = JsonlTail(self.records_path)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._start_lock = threading.Lock()
        self._poll_lock = threading.Lock()
        self.polls = 0
        self.last_error: str | None = None
        self.last_poll_at: float | None = None

    def _collector_owns(self, feed: str) -> bool:
        """True while a continuous collector feeds this vessel feed: the
        sampled pack / RecordStore rows of the legacy adapter then carry
        the same vessels at second precision and would only add
        duplicate track points."""
        return feed == "aisstream" and feed in self.hub._collector

    # -- one pass ------------------------------------------------------
    def _health(self) -> dict:
        if self.health_fn is None:
            return {}
        try:
            return {e.get("adapter"): e for e in (self.health_fn() or [])
                    if isinstance(e, dict)}
        except Exception:
            return {}

    def poll_once(self) -> dict:
        with self._poll_lock:
            health = self._health()
            ingested = {}
            for feed in FEED_IDS:
                path = self.snap_dir / f"{feed}.json"
                sig = _sig(path)
                if sig != self._pack_sig.get(feed) or feed not in self._pack_meta:
                    self._pack_sig[feed] = sig
                    pack = _read_json(path) if sig else None
                    if isinstance(pack, dict):
                        recs = pack.get("records") \
                            if isinstance(pack.get("records"), list) else []
                        prov = pack.get("provenance") \
                            if isinstance(pack.get("provenance"), dict) \
                            else None
                        self._pack_meta[feed] = {
                            "prov": prov, "records": len(recs),
                            "note": pack.get("note")}
                        if recs and not self._collector_owns(feed):
                            ingested[feed] = self.hub.ingest(recs, prov)
                    else:
                        self._pack_meta[feed] = {
                            "prov": None, "records": None,
                            "note": "no snapshot pack has been written for "
                                    "this feed yet"}
                meta = self._pack_meta[feed]
                self.hub.set_feed(feed, provenance=meta["prov"],
                                  records=meta["records"], note=meta["note"],
                                  adapter=health.get(feed))
            # events history
            esig = _sig(self.events_path)
            if esig != self._events_sig:
                self._events_sig = esig
                rows = []
                if esig:
                    try:
                        for line in self.events_path.read_text(
                                encoding="utf-8").splitlines():
                            if line.strip():
                                try:
                                    rows.append(json.loads(line))
                                except ValueError:
                                    pass
                    except OSError:
                        rows = []
                if rows:
                    # fold append-only lifecycle lines exactly like
                    # EventStore.read_all (latest line per occurrence,
                    # expiry passed -> resolved)
                    ingested["events_file"] = self.hub.ingest(
                        _fold_events(rows))
            # vessel / aircraft history from the RecordStore tail
            tail_rows = self._tail.read_new()
            if tail_rows:
                recs, groups = [], {}
                for row in tail_rows:
                    rec = row.get("record")
                    if not isinstance(rec, dict):
                        continue
                    if rec.get("record_type") == "ais_position" and \
                            self._collector_owns("aisstream"):
                        continue      # the continuous collector owns AIS
                    prov = row.get("provenance")
                    key = json.dumps(prov, sort_keys=True)
                    groups.setdefault(key, (prov, []))[1].append(rec)
                for _, (prov, group) in groups.items():
                    ingested.setdefault("records_tail", {"accepted": 0})
                    st = self.hub.ingest(group, prov)
                    ingested["records_tail"]["accepted"] += st["accepted"]
            self.hub.refresh_health()
            self.polls += 1
            self.last_poll_at = time.time()
            return ingested

    # -- thread lifecycle ----------------------------------------------
    def ensure_started(self) -> None:
        """First call does one synchronous pass (so the first request
        sees data) then starts the daemon thread. Call from a worker
        thread, never directly on the event loop."""
        with self._start_lock:
            if self._thread is not None:
                return
            try:
                self.poll_once()
            except Exception as exc:     # never break the request
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.exception("live poll failed")
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run, name="supermap-live-poller", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self.interval_s):
            try:
                self.poll_once()
                self.last_error = None
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.exception("live poll failed")

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout=2.0)
        self._thread = None


def env_float(name: str, default: float) -> float:
    try:
        v = float(os.environ.get(name, ""))
        return v if v == v else default
    except ValueError:
        return default


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, ""))
    except ValueError:
        return default
