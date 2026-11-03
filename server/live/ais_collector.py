"""Continuous AIS collector: ONE persistent AISStream websocket.

Replaces the 40-second sampling windows with a connection that stays up
inside a daemon thread under the live hub, so the vessel count
accumulates over minutes toward what terrestrial coverage allows.

  * subscription: the whole world (one bounding box) with a
    ``FilterMessageTypes`` list: PositionReport, Standard/Extended
    Class B position reports, ShipStaticData and StaticDataReport
    (names, ship type, dimensions, destination come from static data);
  * ONE connection (AISStream's rule). The API key is read from the
    environment by the manager, sent only inside the subscribe frame,
    never logged, stored, put in a URL or echoed in a status field;
  * reconnect with exponential backoff + jitter; a connection that stays
    up for ``stable_after_s`` resets the backoff; a subscription error
    from the server backs off for minutes (no hammering);
  * no unbounded queue: the reader thread parses, folds each message
    into a per-vessel ``pending`` dict (latest report per MMSI) and
    flushes it into the hub every ``flush_s`` (or when it holds
    ``max_pending`` vessels) in chunks, on the SAME thread; the kernel
    socket buffer is the only buffer between the network and the parser;
  * rate guard: more than ``max_msgs_per_s`` messages in one second are
    shed (counted), so a flood cannot starve the API of the GIL;
  * shutdown: ``stop()`` sets an event checked every <= 1 s (the socket
    read timeout), closes the socket and joins.

Honesty: free AISStream is TERRESTRIAL ONLY. Open ocean has no receivers;
several chokepoint watch areas showed nothing in the measured windows.
A vessel is held with the last report it sent;
nothing is interpolated, and a vessel not heard from for ``vessel_ttl_s``
is dropped from the hub rather than kept as a ghost.
"""
from __future__ import annotations

import json
import logging
import random
import threading
import time
from collections import deque
from datetime import date, datetime, timezone

try:
    from adapters.ws_client import WebSocketError, WsStream
except ImportError:                                  # pragma: no cover
    from server.adapters.ws_client import WebSocketError, WsStream

from .normalize import iso_z

log = logging.getLogger("supermap.live.ais")

SOURCE = "AISStream"
STREAM_URL = "wss://stream.aisstream.io/v0/stream"
WORLD = [[[-90.0, -180.0], [90.0, 180.0]]]
POSITION_TYPES = ("PositionReport", "StandardClassBPositionReport",
                  "ExtendedClassBPositionReport")
STATIC_TYPES = ("ShipStaticData", "StaticDataReport")
DEFAULT_TYPES = POSITION_TYPES + STATIC_TYPES
FLUSH_CHUNK = 500
HONEST_NOTE = ("terrestrial AIS only: open ocean and several chokepoints "
               "have little or no receiver coverage")

_DAY_EPOCH: dict = {}


def _t_utc(text) -> float | None:
    """AISStream ``time_utc`` ("2026-01-01 09:28:28.809448578 +0000 UTC")
    -> epoch seconds, without strptime (hot path)."""
    try:
        day = text[:10]
        base = _DAY_EPOCH.get(day)
        if base is None:
            y, m, d = int(day[:4]), int(day[5:7]), int(day[8:10])
            base = datetime(y, m, d, tzinfo=timezone.utc).timestamp()
            if len(_DAY_EPOCH) > 8:
                _DAY_EPOCH.clear()
            _DAY_EPOCH[day] = base
        frac = 0.0
        if len(text) > 20 and text[19] == ".":
            frac = float("0" + text[19:text.index(" ", 19)])
        return base + int(text[11:13]) * 3600 + int(text[14:16]) * 60 \
            + int(text[17:19]) + frac
    except (TypeError, ValueError, IndexError):
        return None


class AisCollector:
    name = "aisstream"
    feed = "aisstream"

    def __init__(self, hub, *, api_key: str | None, connect=None,
                 url: str = STREAM_URL, clock=time.time, rng=None,
                 boxes=None, types=DEFAULT_TYPES, flush_s: float = 2.0,
                 max_pending: int = 20000, max_msgs_per_s: int = 1500,
                 recv_timeout_s: float = 1.0, idle_timeout_s: float = 60.0,
                 backoff_base_s: float = 2.0, backoff_max_s: float = 120.0,
                 stable_after_s: float = 60.0, live_window_s: float = 120.0,
                 vessel_entity_max_age_s: float = 900.0,
                 auth_backoff_s: float = 300.0):
        self.hub = hub
        self._key = api_key
        self._connect = connect or (lambda u: WsStream(u))
        self.url = url
        self.clock = clock
        self._rng = rng or random.Random()
        self.boxes = boxes or WORLD
        self.types = list(types)
        self.flush_s = flush_s
        self.max_pending = max_pending
        self.max_msgs_per_s = max_msgs_per_s
        self.recv_timeout_s = recv_timeout_s
        self.idle_timeout_s = idle_timeout_s
        self.backoff_base_s = backoff_base_s
        self.backoff_max_s = backoff_max_s
        self.stable_after_s = stable_after_s
        self.live_window_s = live_window_s
        self.entity_max_age_s = vessel_entity_max_age_s
        self.auth_backoff_s = auth_backoff_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._start_lock = threading.Lock()
        self._conn = None
        # folded messages awaiting the next flush (bounded by max_pending)
        self._pending: dict[str, tuple] = {}
        self._pending_static: dict[str, dict] = {}
        # status
        has_key = bool(api_key)
        self.state = "STARTING" if has_key else "DOWN"
        self.note = None if has_key else (
            "NEEDS-KEY: set AISSTREAM_API_KEY to collect the live AIS "
            "stream")
        self.connected = False
        self.connects = 0
        self.reconnects = 0
        self.attempt = 0
        self.last_error: str | None = None
        self.last_connect_at: float | None = None
        self.last_message_at: float | None = None
        self.first_message_at: float | None = None
        self.started_at: float | None = None
        self.msgs_total = 0
        self.by_type: dict[str, int] = {}
        self.positions_total = 0
        self.static_total = 0
        self.parse_errors = 0
        self.bad_position = 0
        self.shed = 0
        self.flushes = 0
        self.flush_errors = 0
        self.confirmed = False
        self.backoff_s = 0.0
        self._sec = 0
        self._sec_n = 0
        self._hist: deque = deque(maxlen=40)   # (t, msgs_total) per flush
        self._prune_at = 0.0
        self._pub_at = 0.0
        self._pub_state = None
        self.publish_state(force=True)

    # -- lifecycle -------------------------------------------------------
    def start(self) -> None:
        if not self._key:
            return
        with self._start_lock:
            if self._thread is not None:
                return
            self._stop.clear()
            self.started_at = self.clock()
            self._thread = threading.Thread(
                target=self._run, name="supermap-live-ais", daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        conn = self._conn
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout=timeout)
        self._thread = None
        self.connected = False

    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # -- connection loop -------------------------------------------------
    def _subscribe_frame(self) -> dict:
        return {"APIKey": self._key, "BoundingBoxes": self.boxes,
                "FilterMessageTypes": self.types}

    def _run(self) -> None:
        while not self._stop.is_set():
            up_at = None
            fatal = False
            try:
                conn = self._connect(self.url)
                self._conn = conn
                conn.open()
                conn.send_json(self._subscribe_frame())
                self.connects += 1
                if self.connects > 1:
                    self.reconnects += 1
                self.connected = True
                self.confirmed = False
                up_at = self.last_connect_at = self.clock()
                self.state = "LIVE"
                self.note = None
                self.publish_state(force=True)
                fatal = self._read_loop(conn)
            except Exception as exc:
                self.last_error = self._describe(exc)
            finally:
                self.connected = False
                c, self._conn = self._conn, None
                if c is not None:
                    try:
                        c.close()
                    except Exception:
                        pass
                self._flush(final=True)
            if self._stop.is_set():
                break
            if up_at is not None and self.clock() - up_at >= \
                    self.stable_after_s and self.msgs_total:
                self.attempt = 0
            self.attempt += 1
            self.backoff_s = self._next_backoff(fatal)
            self.state = "SNAPSHOT" if self.first_message_at else "DOWN"
            self.note = (f"AISStream disconnected ({self.last_error or 'closed'}"
                         f"); reconnecting in {self.backoff_s:.0f} s")
            self.publish_state(force=True)
            end = time.monotonic() + self.backoff_s
            while not self._stop.is_set():
                left = end - time.monotonic()
                if left <= 0:
                    break
                self._stop.wait(min(left, 2.0))
                self.publish_state()

    def _next_backoff(self, fatal: bool) -> float:
        if fatal:
            return self.auth_backoff_s
        b = min(self.backoff_max_s,
                self.backoff_base_s * (2 ** min(self.attempt - 1, 10)))
        return b + self._rng.uniform(0, b * 0.25)

    @staticmethod
    def _describe(exc: BaseException) -> str:
        # type + short text; ws/ssl errors carry no request data and the
        # key lives only in the subscribe frame body
        return f"{type(exc).__name__}: {str(exc)[:100]}"

    def _read_loop(self, conn) -> bool:
        """Read until the connection drops, stop is set or the stream is
        idle. Returns True when the server rejected the subscription."""
        last_flush = time.monotonic()
        idle_since = time.monotonic()
        while not self._stop.is_set():
            payload = conn.recv(self.recv_timeout_s)
            mono = time.monotonic()
            if payload is None:
                if mono - idle_since > self.idle_timeout_s:
                    raise WebSocketError(
                        f"no message for {self.idle_timeout_s:g} s")
            else:
                idle_since = mono
                if self._handle(payload):
                    return True
            if mono - last_flush >= self.flush_s or \
                    len(self._pending) >= self.max_pending:
                last_flush = mono
                self._flush()
        return False

    # -- message handling ------------------------------------------------
    def _handle(self, payload: bytes) -> bool:
        """Fold one message. Returns True on a fatal server error."""
        # rate guard (per wall-clock second)
        sec = int(time.monotonic())
        if sec != self._sec:
            self._sec, self._sec_n = sec, 0
        self._sec_n += 1
        if self._sec_n > self.max_msgs_per_s:
            self.shed += 1
            return False
        try:
            msg = json.loads(payload)
        except ValueError:
            self.parse_errors += 1
            return False
        if not isinstance(msg, dict):
            self.parse_errors += 1
            return False
        recv_t = self.clock()
        if msg.get("error"):
            self.last_error = f"server error: {str(msg['error'])[:100]}"
            return True
        mt = msg.get("MessageType")
        self.msgs_total += 1
        self.by_type[mt] = self.by_type.get(mt, 0) + 1
        self.last_message_at = recv_t
        if self.first_message_at is None:
            self.first_message_at = recv_t
        if mt == "SubscriptionConfirmation":
            self.confirmed = True
            return False
        md = msg.get("MetaData")
        body = (msg.get("Message") or {}).get(mt)
        if not isinstance(md, dict) or not isinstance(body, dict):
            return False
        mmsi = md.get("MMSI")
        if not isinstance(mmsi, int) or mmsi <= 0 or mmsi > 999999999:
            return False
        key = str(mmsi)
        if mt in POSITION_TYPES:
            lat = body.get("Latitude", md.get("latitude"))
            lon = body.get("Longitude", md.get("longitude"))
            if lat is None or lon is None:
                self.bad_position += 1
                return False
            t = _t_utc(md.get("time_utc"))
            if t is None or abs(t - recv_t) > 3600:
                t = recv_t
            name = md.get("ShipName")
            self._pending[key] = (
                t, lat, lon, body.get("Sog"), body.get("Cog"),
                body.get("NavigationalStatus"),
                name.strip() if isinstance(name, str) else None)
            self.positions_total += 1
            if mt == "ExtendedClassBPositionReport":
                self._static_from(key, body, body.get("Name"),
                                  body.get("Type"), None, body)
            return False
        if mt in STATIC_TYPES:
            self.static_total += 1
            if mt == "ShipStaticData":
                self._static_from(key, body, body.get("Name"),
                                  body.get("Type"), body.get("Destination"),
                                  body)
            else:       # StaticDataReport: part A = name, part B = type/dims
                ra = body.get("ReportA") or {}
                rb = body.get("ReportB") or {}
                self._static_from(key, rb, ra.get("Name"),
                                  rb.get("ShipType"), None, rb)
            return False
        return False

    def _static_from(self, key, src, name, ship_type, dest, dim_src) -> None:
        rec = {"record_type": "ais_static", "mmsi": key,
               "ship_name": name if isinstance(name, str) else None}
        if isinstance(ship_type, int) and ship_type > 0:
            rec["ship_type"] = ship_type
        cs = src.get("CallSign")
        if isinstance(cs, str) and cs.strip():
            rec["callsign"] = cs
        if isinstance(dest, str) and dest.strip():
            rec["destination"] = dest
        imo = src.get("ImoNumber")
        if isinstance(imo, int) and imo > 0:
            rec["imo"] = imo
        dr = src.get("MaximumStaticDraught")
        if isinstance(dr, (int, float)) and dr > 0:
            rec["draught_m"] = dr
        dim = dim_src.get("Dimension")
        if isinstance(dim, dict):
            try:
                ln = int(dim.get("A", 0)) + int(dim.get("B", 0))
                bm = int(dim.get("C", 0)) + int(dim.get("D", 0))
            except (TypeError, ValueError):
                ln = bm = 0
            if ln > 0:
                rec["length_m"] = ln
            if bm > 0:
                rec["beam_m"] = bm
        self._pending_static[key] = rec
        if len(self._pending_static) > self.max_pending:
            self._flush()

    # -- flush into the hub ----------------------------------------------
    def _flush(self, final: bool = False) -> None:
        try:
            self._flush_inner()
        except Exception as exc:       # a hub error must not drop the stream
            self.flush_errors += 1
            self.last_error = f"flush failed ({type(exc).__name__})"
            log.exception("ais flush failed")

    def _flush_inner(self) -> None:
        pend, self._pending = self._pending, {}
        stat, self._pending_static = self._pending_static, {}
        now = self.clock()
        # retrieved_at floored to 10 s (conservative: never newer than the
        # truth) so the provenance table stays a handful of rows
        prov = {"source": SOURCE, "retrieved_at": iso_z(now // 10 * 10),
                "mode": "LIVE"}
        if stat:
            batch = []
            for rec in stat.values():
                batch.append(rec)
                if len(batch) >= FLUSH_CHUNK:
                    self.hub.ingest(batch, prov)
                    batch = []
            if batch:
                self.hub.ingest(batch, prov)
        if pend:
            batch = []
            for mm, (t, lat, lon, sog, cog, nav, name) in pend.items():
                batch.append({
                    "record_type": "ais_position", "mmsi": mm,
                    "ship_name": name or None, "lat": lat, "lon": lon,
                    "sog": sog, "cog": cog, "nav_status": nav,
                    "reported_at_epoch": t, "provenance": prov})
                if len(batch) >= FLUSH_CHUNK:
                    self.hub.ingest(batch, prov)
                    batch = []
            if batch:
                self.hub.ingest(batch, prov)
        self.flushes += 1
        self._hist.append((time.monotonic(), self.msgs_total))
        mono = time.monotonic()
        if mono >= self._prune_at:
            self._prune_at = mono + 30.0
            ttl = getattr(self.hub, "vessel_ttl_s", None)
            if ttl:
                self.hub.prune_stale(vessel_ttl_s=ttl)
        self.publish_state()

    # -- feed + status ---------------------------------------------------
    def rate_msgs_s(self, window_s: float = 30.0) -> float | None:
        h = list(self._hist)
        if len(h) < 2:
            return None
        t1, n1 = h[-1]
        t0, n0 = h[0]
        for t, n in h:
            if t1 - t <= window_s:
                t0, n0 = t, n
                break
        return round((n1 - n0) / (t1 - t0), 1) if t1 > t0 else None

    def _effective_state(self) -> str:
        if self.state == "LIVE":
            if not self.connected or self.last_message_at is None or \
                    self.clock() - self.last_message_at > self.live_window_s:
                return "SNAPSHOT"
        return self.state

    def publish_state(self, *, force: bool = False) -> None:
        mono = time.monotonic()
        state = self._effective_state()
        if not force and state == self._pub_state and \
                mono - self._pub_at < 10.0:
            return
        self._pub_at, self._pub_state = mono, state
        prov = None
        if self.last_message_at is not None:
            prov = {"source": SOURCE,
                    "retrieved_at": iso_z(self.last_message_at),
                    "mode": "LIVE"}
        note = self.note
        n = len(self.hub.vessels)
        if state == "LIVE":
            note = (f"LIVE continuous AIS stream, {n} vessels held "
                    f"({HONEST_NOTE}); positions are each vessel's last "
                    "report, not interpolated")
        self.hub.set_collector_feed(
            self.feed, provenance=prov, records=n if prov else None,
            note=note, adapter={"state": state, "note": note,
                                "max_age_s": self.live_window_s,
                                "entity_max_age_s": self.entity_max_age_s})

    def adapter_sample(self, max_age_s: float = 900.0) -> list[dict]:
        """Latest report per vessel held by the hub, as plain dicts, for
        the legacy aisstream adapter (it must not open a second
        connection while this collector holds the only one)."""
        out = []
        now = self.hub.clock()
        with self.hub._lock:
            for e in self.hub.vessels.entities():
                if not e.pos or now - e.last_t > max_age_s:
                    continue
                t, lat, lon, sog, cog, _alt = e.pos[-6:]
                out.append({"mmsi": e.id, "ship_name": e.name,
                            "lat": lat, "lon": lon,
                            "sog": None if sog != sog else sog,
                            "cog": None if cog != cog else cog,
                            "nav_status": e.attrs.get("nav_status"),
                            "reported_at": iso_z(t)})
        return out

    def status(self) -> dict:
        now = self.clock()
        return {
            "name": self.name, "state": self._effective_state(),
            "note": self.note, "thread_alive": self.alive(),
            "connected": self.connected, "subscription_confirmed":
            self.confirmed, "connects": self.connects,
            "reconnects": self.reconnects, "attempt": self.attempt,
            "backoff_s": round(self.backoff_s, 1),
            "last_error": self.last_error,
            "uptime_s": None if self.last_connect_at is None
            or not self.connected else round(now - self.last_connect_at, 1),
            "last_message_age_s": None if self.last_message_at is None
            else round(now - self.last_message_at, 1),
            "msgs_total": self.msgs_total, "msgs_by_type": dict(self.by_type),
            "msgs_per_s_30s": self.rate_msgs_s(),
            "positions_total": self.positions_total,
            "static_total": self.static_total,
            "parse_errors": self.parse_errors,
            "bad_position": self.bad_position, "shed": self.shed,
            "flushes": self.flushes, "flush_errors": self.flush_errors,
            "pending": len(self._pending),
            "vessels_held": len(self.hub.vessels),
            "subscription": {"types": self.types,
                             "boxes": len(self.boxes), "world": self.boxes
                             == WORLD},
            "coverage": HONEST_NOTE,
        }
