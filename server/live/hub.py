"""The live state hub: bounded, in-memory, deterministic.

Fed by ``LiveHub.ingest`` (adapter records) and ``LiveHub.set_feed``
(per-feed provenance + adapter health). It owns:

  * section state (events, ports, borders, per-hub aircraft counts),
  * the vessel / aircraft ``TrackStore`` s,
  * a replay ring of published messages (bounded by count and bytes),
  * the set of connected stream clients (bounded count, bounded
    per-client queue with a drop-oldest policy).

Honesty rules enforced here:
  * mode is derived at READ time: a record stored as LIVE reads as
    SNAPSHOT once its retrieved_at is older than the feed's max age;
  * a topic with nothing to show is reported ``served: false`` /
    NOT SERVED with a reason, never filled;
  * records without a valid position / id / provenance are rejected
    and counted, never repaired.

Thread model: every public method takes ``self._lock`` (an RLock). The
poller thread calls ingest; request threads and the event loop call
the readers. Publishing to a client is an O(1) deque append plus a
``call_soon_threadsafe`` wake, so a slow client can never block the
hub or the loop.
"""
from __future__ import annotations

import os
import threading
import time
from collections import Counter, OrderedDict, deque
from dataclasses import dataclass

from .normalize import (is_aircraft_position, iso_z, norm_aircraft,
                        norm_border, norm_event, norm_hub_count, norm_port,
                        norm_vessel, norm_vessel_static, parse_iso)
from .payload import (Frame, ProvTable, build_frame, dumps, encode_entities,
                      encode_track)
from .tracks import TrackStore

TOPICS = ("events", "vessels", "aircraft", "ports", "borders", "health")

# (feed id, label, topic) in section order.
FEEDS = (
    ("noaa", "NOAA weather alerts", "events"),
    ("gdacs", "GDACS hazard alerts", "events"),
    ("opensky", "OpenSky aircraft", "aircraft"),
    ("aisstream", "AISStream vessels", "vessels"),
    ("portwatch", "PortWatch port activity", "ports"),
    ("cbp", "CBP border waits", "borders"),
)
FEED_IDS = tuple(f[0] for f in FEEDS)
FEED_LABEL = {f[0]: f[1] for f in FEEDS}
FEED_TOPIC = {f[0]: f[2] for f in FEEDS}
# Stale-LIVE thresholds mirroring the adapters' health_max_age_s; the
# adapter registry's own max_age_s wins when it is available.
DEFAULT_MAX_AGE_S = {"noaa": 3600.0, "gdacs": 21600.0, "opensky": 3600.0,
                     "aisstream": 3600.0, "portwatch": 86400.0,
                     "cbp": 3600.0}
SOURCE_FEED = {"NOAA/NWS": "noaa", "GDACS": "gdacs",
               "OpenSky Network": "opensky", "AISStream": "aisstream",
               "IMF PortWatch": "portwatch", "CBP Border Wait Times": "cbp"}
HUB_HISTORY = 12


@dataclass(frozen=True)
class Msg:
    seq: int
    topic: str
    eid: str          # "<epoch>-<seq>"
    text: str         # the JSON data line
    size: int


class Client:
    """One connected stream consumer. ``q`` is a bounded deque: when it
    is full the OLDEST message is dropped and counted, so a stalled
    reader costs bounded memory and never back-pressures the hub."""

    __slots__ = ("id", "topics", "q", "dropped", "loop", "wake_evt",
                 "connected_at", "closed", "_lock", "owner")

    def __init__(self, cid: int, topics: frozenset, qmax: int, owner=None):
        self.owner = owner      # the caller's rate-limit identity (an IP), or None
        self.id = cid
        self.topics = topics
        self.q: deque = deque(maxlen=qmax)
        self.dropped = 0
        self.loop = None
        self.wake_evt = None
        self.connected_at = time.time()
        self.closed = False
        self._lock = threading.Lock()

    def bind(self, loop, evt) -> None:
        self.loop, self.wake_evt = loop, evt

    def push(self, msg: Msg) -> None:
        with self._lock:
            if len(self.q) == self.q.maxlen:
                self.dropped += 1
            self.q.append(msg)
        self.wake()

    def wake(self) -> None:
        loop, evt = self.loop, self.wake_evt
        if loop is None or evt is None:
            return
        try:
            loop.call_soon_threadsafe(evt.set)
        except RuntimeError:   # loop closed: the client is gone
            self.closed = True

    def drain(self, max_n: int = 200):
        with self._lock:
            out = []
            while self.q and len(out) < max_n:
                out.append(self.q.popleft())
            dropped, self.dropped = self.dropped, 0
            more = bool(self.q)
        return out, dropped, more


OWNER_CAP = "owner_cap"     # subscribe() result: this address already holds its share of the stream slots


class LiveHub:
    def __init__(self, *, clock=time.time, ring_max: int = 2000,
                 ring_bytes: int = 16 * 1024 * 1024, max_clients: int = 64,
                 client_queue: int = 256, max_vessels: int = 20000,
                 max_aircraft: int = 20000, track_len: int = 40,
                 max_events: int = 500, max_ports: int = 2000,
                 max_borders: int = 1000, chunk: int = 500,
                 vessel_track_len: int | None = None,
                 vessel_thin_s: float = 0.0, max_static: int = 20000,
                 frame_ttl_s: float = 0.0, vessel_ttl_s: float | None = None,
                 aircraft_ttl_s: float | None = None,
                 max_per_client: int | None = None):
        self.clock = clock
        self._lock = threading.RLock()
        self.epoch = format(int(clock() * 1000) & 0xFFFFFFFFFF, "x")
        self.seq = 0
        self.ring_max, self.ring_bytes = ring_max, ring_bytes
        self._ring: deque = deque()
        self._ring_size = 0
        self.max_clients, self.client_queue = max_clients, client_queue
        # one address may not fill the shared slots (SUPERMAP_LIVE_MAX_PER_CLIENT, default 8)
        if max_per_client is None:
            try:
                max_per_client = int(os.environ.get("SUPERMAP_LIVE_MAX_PER_CLIENT", "8"))
            except ValueError:
                max_per_client = 8
        self.max_per_client = max(1, int(max_per_client))
        self._per_owner: dict[str, int] = {}
        self.chunk = chunk
        self._clients: dict[int, Client] = {}
        self._next_client = 1
        self.vessels = TrackStore("vessel", max_entities=max_vessels,
                                  track_len=vessel_track_len or track_len,
                                  thin_s=vessel_thin_s)
        self.aircraft = TrackStore("aircraft", max_entities=max_aircraft,
                                   track_len=track_len)
        # AIS static data for vessels not (yet) held: bounded LRU, popped
        # into the entity when its first position arrives.
        self.max_static = max(0, int(max_static))
        self._static: "OrderedDict[str, tuple]" = OrderedDict()
        self.static_applied = 0
        # continuous collectors (server/live/collectors.py) register here
        self._collector: dict[str, dict] = {}
        self.collectors: dict[str, object] = {}
        self.vessel_ttl_s, self.aircraft_ttl_s = vessel_ttl_s, aircraft_ttl_s
        # columnar frame cache (payload.Frame), one per kind
        self.frame_ttl_s = max(0.0, float(frame_ttl_s))
        self._frames: dict[str, Frame] = {}
        self._frame_locks = {"vessel": threading.Lock(),
                             "aircraft": threading.Lock()}
        self._frame_seq = 0
        self._kstats: dict = {}
        self.max_events, self.max_ports, self.max_borders = \
            max_events, max_ports, max_borders
        self._events: dict[str, dict] = {}
        self._ports: "OrderedDict[str, dict]" = OrderedDict()
        self._borders: "OrderedDict[str, dict]" = OrderedDict()
        self._hubs: dict[str, dict] = {}
        self._feeds: dict[str, dict] = {}
        self._health_sig = None
        self.rejected = {f: 0 for f in FEED_IDS}
        self.ignored = 0
        self.dropped_total = 0
        self.closed = False
        # Optional callable -> True once the hosting server is shutting
        # down (see routes._detect_server); streams then end promptly.
        self.stop_probe = None

    # ------------------------------------------------------------------
    # modes and feed health
    # ------------------------------------------------------------------
    def _feed_entry(self, feed: str) -> dict:
        """The freshest of the pack/registry entry (set_feed) and the
        continuous collector entry (set_collector_feed): whichever
        feed answered most recently describes the feed."""
        pack = self._feeds.get(feed) or {}
        col = self._collector.get(feed)
        if not col:
            return pack
        if not pack.get("prov"):
            return col
        if not col.get("prov"):
            return pack
        pt = parse_iso(pack["prov"].get("retrieved_at"))
        ct = parse_iso(col["prov"].get("retrieved_at"))
        return col if ct is not None and (pt is None or ct >= pt) else pack

    def max_age_s(self, feed: str) -> float:
        ad = self._feed_entry(feed).get("adapter") or {}
        v = ad.get("max_age_s")
        return float(v) if isinstance(v, (int, float)) and v > 0 \
            else DEFAULT_MAX_AGE_S.get(feed, 21600.0)

    def entity_max_age_s(self, feed: str) -> float:
        """How old an ENTITY report may be and still read LIVE. A
        continuous collector states its own (a vessel legitimately
        reports every few minutes); otherwise the feed max age."""
        col = self._collector.get(feed)
        v = ((col or {}).get("adapter") or {}).get("entity_max_age_s")
        return float(v) if isinstance(v, (int, float)) and v > 0 \
            else self.max_age_s(feed)

    def effective(self, prov: tuple, feed: str, now: float | None = None):
        """Stored provenance triple -> effective one. LIVE only when the
        record was retrieved within the feed's max age; anything older
        (or unparseable) reads SNAPSHOT."""
        source, ret, mode = prov
        if mode == "LIVE":
            now = self.clock() if now is None else now
            t = parse_iso(ret)
            if t is None or now - t > self.max_age_s(feed) or (
                    (self._feed_entry(feed).get("adapter") or {}).get(
                        "state") == "SNAPSHOT"):
                # older than the feed's max age, or the adapter itself
                # reports it is serving a snapshot (same rule as
                # feed_view, so an item never reads LIVE under a
                # SNAPSHOT feed chip)
                mode = "SNAPSHOT"
        return (source, ret, mode)

    def effective_entity(self, prov: tuple, feed: str,
                         now: float | None = None):
        """``effective`` for vessel / aircraft rows: uses the entity max
        age, and reads SNAPSHOT whenever the continuous collector that
        writes the rows is not currently connected and receiving."""
        source, ret, mode = prov
        if mode == "LIVE":
            now = self.clock() if now is None else now
            t = parse_iso(ret)
            col = self._collector.get(feed)
            if t is None or now - t > self.entity_max_age_s(feed) or (
                    col is not None and (col.get("adapter") or {}).get(
                        "state") in ("SNAPSHOT", "DOWN")):
                mode = "SNAPSHOT"
        return (source, ret, mode)

    def set_feed(self, feed: str, *, provenance: dict | None,
                 records: int | None, note: str | None = None,
                 adapter: dict | None = None) -> None:
        """Record what a feed last told us (its pack provenance + the
        adapter health entry). Publishes a ``health`` message when the
        served picture changed."""
        with self._lock:
            self._feeds[feed] = {"prov": dict(provenance) if provenance
                                 else None, "records": records,
                                 "note": note, "adapter": adapter}
            self._publish_health_if_changed()

    def set_collector_feed(self, feed: str, *, provenance: dict | None,
                           records: int | None, note: str | None = None,
                           adapter: dict | None = None) -> None:
        """Same as ``set_feed`` but written by a continuous collector
        thread (``adapter`` carries state, max_age_s and
        entity_max_age_s). The poller never overwrites it."""
        with self._lock:
            self._collector[feed] = {
                "prov": dict(provenance) if provenance else None,
                "records": records, "note": note, "adapter": adapter}
            self._publish_health_if_changed()

    def feed_view(self, feed: str, now: float | None = None) -> dict:
        now = self.clock() if now is None else now
        with self._lock:
            fi = self._feed_entry(feed)
            prov = fi.get("prov")
            ad = fi.get("adapter") or {}
            max_age = self.max_age_s(feed)
            records = fi.get("records")
            retrieved_at = prov.get("retrieved_at") if prov else None
            t = parse_iso(retrieved_at)
            age = None if t is None else max(0.0, now - t)
            fresh = age is not None and age <= max_age
            if prov is None or (records == 0 and prov.get("mode") != "LIVE"):
                state, note = "DOWN", fi.get("note") or ad.get("note") or \
                    "no data has been received from this feed"
            elif prov.get("mode") == "LIVE" and fresh and \
                    ad.get("state") != "SNAPSHOT":
                state, note = "LIVE", ad.get("note") or fi.get("note")
            else:
                state = "SNAPSHOT"
                note = ad.get("note") or fi.get("note") or (
                    "last answer is older than the feed's max age"
                    if prov.get("mode") == "LIVE" else
                    "snapshot data, not a live answer")
            return {"id": feed, "label": FEED_LABEL.get(feed, feed),
                    "topic": FEED_TOPIC.get(feed),
                    "state": state, "mode": state if state != "DOWN"
                    else None,
                    "source": prov.get("source") if prov else None,
                    "retrieved_at": retrieved_at,
                    "age_s": None if age is None else round(age, 1),
                    "max_age_s": max_age, "fresh": bool(fresh),
                    "records": records, "note": note,
                    "adapter_state": ad.get("state"),
                    "rejected": self.rejected.get(feed, 0)}

    def feeds_view(self, now: float | None = None) -> list[dict]:
        now = self.clock() if now is None else now
        return [self.feed_view(f, now) for f in FEED_IDS]

    def _health_signature(self):
        sig = []
        for f in FEED_IDS:
            v = self.feed_view(f)
            sig.append((f, v["state"], v["retrieved_at"], v["records"],
                        v["note"]))
        return tuple(sig)

    def _publish_health_if_changed(self) -> None:
        sig = self._health_signature()
        if sig != self._health_sig:
            self._health_sig = sig
            self._emit("health", {"op": "status",
                                  "feeds": self.feeds_view()})

    def refresh_health(self) -> None:
        """Re-evaluate time-derived state (a LIVE feed going stale) and
        publish if it flipped. Called by the poller every tick."""
        with self._lock:
            self._publish_health_if_changed()

    # ------------------------------------------------------------------
    # ingest
    # ------------------------------------------------------------------
    def ingest(self, records, envelope_provenance: dict | None = None) -> dict:
        """Route adapter records into hub state and publish the deltas.
        Returns {accepted, rejected, ignored, changed}."""
        stats = {"accepted": 0, "rejected": 0, "ignored": 0, "changed": 0}
        with self._lock:
            up = {"events": [], "vessels": [], "aircraft": [], "ports": [],
                  "borders": []}
            rm = {"events": [], "vessels": [], "aircraft": []}
            for rec in records:
                if not isinstance(rec, dict):
                    stats["ignored"] += 1
                    continue
                self._route(rec, envelope_provenance, stats, up, rm)
            self.ignored += stats["ignored"]
            self._publish_deltas(up, rm)
        return stats

    def _reject(self, rec, stats, default_feed):
        src = (rec.get("provenance") or {}).get("source") \
            if isinstance(rec.get("provenance"), dict) else None
        feed = SOURCE_FEED.get(src, default_feed)
        self.rejected[feed] = self.rejected.get(feed, 0) + 1
        stats["rejected"] += 1

    def _route(self, rec, env, stats, up, rm) -> None:
        rtype = rec.get("record_type")
        rid = rec.get("id")
        if isinstance(rid, str) and rid.startswith("evt_"):
            row = norm_event(rec, env)
            if row is None:
                return self._reject(rec, stats, "noaa")
            stats["accepted"] += 1
            old = self._events.get(row["id"])
            sig = self._event_sig(row)
            if old is None or self._event_sig(old) != sig:
                self._events[row["id"]] = row
                up["events"].append(row)
                stats["changed"] += 1
                self._trim_events(rm)
            return
        if rtype == "ais_static":
            row = norm_vessel_static(rec)
            if row is None:
                stats["ignored"] += 1
                return
            stats["accepted"] += 1
            self._apply_static(row)
            return
        if rtype == "ais_position" or (rec.get("mmsi") and "lat" in rec
                                       and rtype is None):
            row = norm_vessel(rec, env)
            if row is None:
                return self._reject(rec, stats, "aisstream")
            stats["accepted"] += 1
            if self._static and self.vessels.get(row["id"]) is None:
                st = self._static.pop(row["id"], None)
                if st is not None:
                    row["name"] = st[0] or row["name"]
                    row["attrs"] = {**st[1], **row["attrs"]}
            self._add_entity(self.vessels, row, up["vessels"], rm["vessels"],
                             stats)
            return
        if is_aircraft_position(rec):
            row = norm_aircraft(rec, env)
            if row is None:
                return self._reject(rec, stats, "opensky")
            stats["accepted"] += 1
            self._add_entity(self.aircraft, row, up["aircraft"],
                             rm["aircraft"], stats)
            return
        if rtype == "air_traffic":
            row = norm_hub_count(rec, env)
            if row is None:
                return self._reject(rec, stats, "opensky")
            stats["accepted"] += 1
            cur = self._hubs.get(row["node_id"])
            hist = cur["history"] if cur else []
            if any(h[0] == row["t"] for h in hist):
                return                      # same observation again
            hist.append((row["t"], row["count"]))
            hist.sort()
            del hist[:-HUB_HISTORY]         # keep the newest observations
            if row["t"] < hist[0][0]:
                return                      # older than everything held
            if cur is None or row["t"] >= cur["t"]:
                self._hubs[row["node_id"]] = {
                    "node_id": row["node_id"], "count": row["count"],
                    "t": row["t"], "prov": row["prov"], "history": hist}
            stats["changed"] += 1
            up["aircraft"].append(("hub", self._hubs[row["node_id"]]))
            return
        if rtype == "border_wait":
            row = norm_border(rec, env)
            if row is None:
                return self._reject(rec, stats, "cbp")
            stats["accepted"] += 1
            self._upsert_keyed(self._borders, row, up["borders"], stats,
                               self.max_borders)
            return
        if rtype == "port_activity":
            row = norm_port(rec, env)
            if row is None:
                return self._reject(rec, stats, "portwatch")
            stats["accepted"] += 1
            self._upsert_keyed(self._ports, row, up["ports"], stats,
                               self.max_ports)
            return
        stats["ignored"] += 1

    def _apply_static(self, row) -> None:
        """AIS static data: decorate the held vessel, or park it (bounded
        LRU) until its first position arrives. Never creates a vessel."""
        ent = self.vessels.get(row["id"])
        if ent is not None:
            changed = False
            if row["name"] and ent.name != row["name"]:
                ent.name, changed = row["name"], True
            for k, v in row["attrs"].items():
                if ent.attrs.get(k) != v:
                    ent.attrs[k], changed = v, True
            if changed:
                self.vessels.version += 1
                self.static_applied += 1
            return
        if not self.max_static:
            return
        old = self._static.pop(row["id"], None)
        name = row["name"] or (old[0] if old else None)
        attrs = {**(old[1] if old else {}), **row["attrs"]}
        self._static[row["id"]] = (name, attrs)
        while len(self._static) > self.max_static:
            self._static.popitem(last=False)

    def prune_stale(self, *, vessel_ttl_s: float | None = None,
                    aircraft_ttl_s: float | None = None) -> dict:
        """Drop entities whose newest report is older than their TTL
        (defaults: the hub's ``vessel_ttl_s`` / ``aircraft_ttl_s``; None
        = keep) and tell stream clients with ``op: remove``."""
        v_ttl = self.vessel_ttl_s if vessel_ttl_s is None else vessel_ttl_s
        a_ttl = self.aircraft_ttl_s if aircraft_ttl_s is None \
            else aircraft_ttl_s
        out = {"vessels": 0, "aircraft": 0}
        with self._lock:
            now = self.clock()
            rm = {"events": [], "vessels": [], "aircraft": []}
            if v_ttl:
                rm["vessels"] = self.vessels.prune_older(now - v_ttl)
            if a_ttl:
                rm["aircraft"] = self.aircraft.prune_older(now - a_ttl)
            out = {"vessels": len(rm["vessels"]),
                   "aircraft": len(rm["aircraft"])}
            if out["vessels"] or out["aircraft"]:
                up = {"events": [], "vessels": [], "aircraft": [],
                      "ports": [], "borders": []}
                self._publish_deltas(up, rm)
        return out

    @staticmethod
    def _event_sig(row):
        return (row["kind"], row["severity"], row["status"], row["title"],
                row["started_at"], row["expires_at"], row["node_id"])

    def _trim_events(self, rm) -> None:
        over = len(self._events) - self.max_events
        if over > 0:
            victims = sorted(self._events.values(),
                             key=lambda r: (r["t"], r["id"]))[:over]
            for v in victims:
                del self._events[v["id"]]
                rm["events"].append(v["id"])

    def _add_entity(self, store: TrackStore, row, upl, rml, stats) -> None:
        changed, evicted = store.add(
            row["id"], row["t"], row["lat"], row["lon"], spd=row["spd"],
            hdg=row["hdg"], alt=row["alt"], name=row["name"],
            attrs=row["attrs"], prov=row["prov"])
        if changed:
            stats["changed"] += 1
            upl.append(row["id"])
        rml.extend(evicted)

    def _upsert_keyed(self, table: OrderedDict, row, upl, stats, cap) -> None:
        old = table.get(row["key"])
        new_sig = {k: v for k, v in row.items() if k != "prov"}
        if old is None or {k: v for k, v in old.items() if k != "prov"} \
                != new_sig:
            table[row["key"]] = row
            table.move_to_end(row["key"])
            upl.append(row)
            stats["changed"] += 1
            while len(table) > cap:
                table.popitem(last=False)
        elif old["prov"] != row["prov"]:
            table[row["key"]] = row     # refresh provenance, no delta

    # ------------------------------------------------------------------
    # publishing
    # ------------------------------------------------------------------
    def _publish_deltas(self, up, rm) -> None:
        now = self.clock()
        # fixed topic order -> deterministic seq assignment
        if up["events"] or rm["events"]:
            rows = sorted(up["events"], key=lambda r: (-r["t"], r["id"]))
            for i in range(0, max(len(rows), 1), self.chunk):
                part = rows[i:i + self.chunk]
                table = ProvTable()
                items = [self._event_item(r, table, now) for r in part]
                body = {"op": "upsert", "n": len(items), "items": items,
                        "provenance": table.rows}
                if i == 0 and rm["events"]:
                    body["removed"] = sorted(rm["events"])
                self._emit("events", body)
        for kind, store, topic in (("vessel", self.vessels, "vessels"),
                                   ("aircraft", self.aircraft, "aircraft")):
            ids = [x for x in up[topic] if isinstance(x, str)]
            hubs = [x[1] for x in up[topic] if isinstance(x, tuple)]
            feed = "aisstream" if kind == "vessel" else "opensky"
            if ids:
                ents = [store.get(i) for i in sorted(set(ids))]
                ents = [e for e in ents if e is not None]
                for i in range(0, len(ents), self.chunk):
                    part = ents[i:i + self.chunk]
                    cols, prov = encode_entities(
                        part, now, kind,
                        lambda p, f=feed: self.effective_entity(p, f, now),
                        with_age=False)
                    self._emit(topic, {"op": "upsert", "n": len(part),
                                       "cols": cols, "provenance": prov})
            if hubs:
                table = ProvTable()
                items = [self._hub_item(h, table, now)
                         for h in sorted(hubs, key=lambda h: h["node_id"])]
                self._emit(topic, {"op": "hubs", "n": len(items),
                                   "items": items,
                                   "provenance": table.rows})
            if rm[topic]:
                gone = sorted(set(rm[topic]))
                for i in range(0, len(gone), 2000):
                    self._emit(topic, {"op": "remove",
                                       "ids": gone[i:i + 2000]})
        for topic, feed in (("ports", "portwatch"), ("borders", "cbp")):
            rows = up[topic]
            if not rows:
                continue
            rows = sorted(rows, key=lambda r: r["key"])
            for i in range(0, len(rows), self.chunk):
                table = ProvTable()
                items = [self._keyed_item(r, table, feed, now)
                         for r in rows[i:i + self.chunk]]
                self._emit(topic, {"op": "upsert", "n": len(items),
                                   "items": items,
                                   "provenance": table.rows})

    def _event_item(self, r, table, now):
        feed = SOURCE_FEED.get(r["prov"][0], "noaa")
        prov = self.effective(r["prov"], feed, now)
        status = r["status"]
        exp = parse_iso(r.get("expires_at"))
        if exp is not None and exp < now and status in ("active",
                                                        "developing"):
            status = "resolved"       # expiry passed: same fold as the store
        item = {"id": r["id"], "node_id": r["node_id"], "kind": r["kind"],
                "severity": r["severity"], "status": status,
                "title": r["title"], "detected_at": r["detected_at"],
                "started_at": r["started_at"], "expires_at": r["expires_at"],
                "evidence_url": r["evidence_url"], "p": table.index(prov)}
        if r.get("citation"):  # only when a human-readable page exists
            item["citation"] = r["citation"]
        return item

    def _hub_item(self, h, table, now):
        prov = self.effective(h["prov"], "opensky", now)
        return {"node_id": h["node_id"], "aircraft_in_bbox": h["count"],
                "observed_at": iso_z(h["t"]),
                "age_s": int(max(0, now - h["t"])),
                "recent": [c for _, c in list(h["history"])],
                "p": table.index(prov)}

    def _keyed_item(self, r, table, feed, now):
        item = {k: v for k, v in r.items() if k not in ("prov", "key")}
        if feed == "portwatch":
            item["id"] = r["key"]
        item["p"] = table.index(self.effective(r["prov"], feed, now))
        return item

    def _emit(self, topic: str, body: dict) -> Msg:
        self.seq += 1
        body = {"seq": self.seq, "topic": topic,
                "at": iso_z(self.clock()), **body}
        text = dumps(body)
        msg = Msg(self.seq, topic, f"{self.epoch}-{self.seq}", text,
                  len(text))
        self._ring.append(msg)
        self._ring_size += msg.size
        while len(self._ring) > self.ring_max or \
                (self._ring_size > self.ring_bytes and len(self._ring) > 1):
            old = self._ring.popleft()
            self._ring_size -= old.size
        for c in list(self._clients.values()):
            if topic in c.topics:
                before = c.dropped
                c.push(msg)
                self.dropped_total += c.dropped - before
        return msg

    # ------------------------------------------------------------------
    # clients / resume
    # ------------------------------------------------------------------
    def last_event_id(self) -> str:
        with self._lock:
            return f"{self.epoch}-{self.seq}"

    def subscribe(self, topics, last_event_id: str | None = None, owner: str | None = None):
        """Register a client. Returns (client, replay_msgs, reset_reason,
        head_id), None when the clients cap is reached, or ``OWNER_CAP`` when
        ``owner`` (an address) already holds ``max_per_client`` streams. ``reset_reason`` is set
        when the resume point cannot be honoured (different epoch,
        evicted from the ring, or ahead of the head); the caller then
        tells the client to re-fetch /live/snapshot. Registration and
        replay are atomic, so no message is missed or duplicated."""
        with self._lock:
            if self.closed or len(self._clients) >= self.max_clients:
                return None
            if owner is not None and self._per_owner.get(owner, 0) >= self.max_per_client:
                return OWNER_CAP
            c = Client(self._next_client, frozenset(topics),
                       self.client_queue, owner)
            self._next_client += 1
            replay, reset = [], None
            if last_event_id:
                ep, _, sq = last_event_id.partition("-")
                try:
                    last = int(sq)
                except ValueError:
                    last = None
                if ep != self.epoch or last is None:
                    reset = "unknown_epoch"
                elif last > self.seq:
                    reset = "ahead_of_head"
                elif last < self.seq:
                    oldest = self._ring[0].seq if self._ring else self.seq + 1
                    if last + 1 < oldest:
                        reset = "ring_overflow"
                    else:
                        replay = [m for m in self._ring
                                  if m.seq > last and m.topic in c.topics]
            self._clients[c.id] = c
            if owner is not None:
                self._per_owner[owner] = self._per_owner.get(owner, 0) + 1
            return c, replay, reset, f"{self.epoch}-{self.seq}"

    def unsubscribe(self, client: Client) -> None:
        with self._lock:
            client.closed = True
            if self._clients.pop(client.id, None) is not None and client.owner is not None:
                n = self._per_owner.get(client.owner, 0) - 1
                if n > 0:
                    self._per_owner[client.owner] = n
                else:
                    self._per_owner.pop(client.owner, None)

    def close(self) -> None:
        """Wake every client so its stream ends (graceful shutdown)."""
        with self._lock:
            self.closed = True
            for c in self._clients.values():
                c.closed = True
                c.wake()

    # ------------------------------------------------------------------
    # readers
    # ------------------------------------------------------------------
    def topic_status(self, topic: str, now: float | None = None) -> dict:
        """{served, status, reason} for one topic, honest about empty."""
        now = self.clock() if now is None else now
        with self._lock:
            feeds = [self.feed_view(f, now) for f in FEED_IDS
                     if FEED_TOPIC[f] == topic]
            n = {"events": len(self._events), "vessels": len(self.vessels),
                 "aircraft": len(self.aircraft), "ports": len(self._ports),
                 "borders": len(self._borders),
                 "health": 1}[topic]
            if topic == "aircraft" and not n and self._hubs:
                reason = ("per-aircraft positions are NOT SERVED: the "
                          "OpenSky adapter currently delivers per-hub "
                          "counts only (see hubs)")
                return {"served": False, "status": "NOT SERVED",
                        "count": 0, "reason": reason}
            if n or (topic == "events" and any(
                    f["state"] != "DOWN" for f in feeds)):
                states = [f["state"] for f in feeds if f["state"] != "DOWN"]
                status = "LIVE" if "LIVE" in states else "SNAPSHOT"
                return {"served": True, "status": status, "count": n,
                        "reason": None}
            notes = "; ".join(f"{f['label']}: {f['note']}" for f in feeds
                              if f["note"])
            return {"served": False, "status": "NOT SERVED", "count": 0,
                    "reason": notes or "no records received from the feed"}

    def snapshot(self, topics=TOPICS, *, entity_limit: int = 1000,
                 events_limit: int = 100, ports_limit: int = 100,
                 borders_limit: int = 200) -> dict:
        """Pull equivalent of the stream: consistent state + the
        ``last_event_id`` to resume from."""
        # columnar frames are built (chunked, lock-friendly) BEFORE the
        # consistent read below, never inside the long hold
        frames = {}
        if entity_limit:
            if "vessels" in topics:
                frames["vessel"] = self.frame("vessel")
            if "aircraft" in topics:
                frames["aircraft"] = self.frame("aircraft")
        with self._lock:
            now = self.clock()
            out: dict = {"generated_at": iso_z(now),
                         "epoch": self.epoch, "seq": self.seq,
                         "last_event_id": f"{self.epoch}-{self.seq}",
                         "topics": [t for t in TOPICS if t in topics]}
            if "health" in topics:
                out["health"] = {
                    "feeds": self.feeds_view(now),
                    "hub": self.stats(),
                }
            if "events" in topics:
                out["events"] = self._events_section(now, events_limit)
            if "vessels" in topics:
                out["vessels"] = self._entity_section(
                    self.vessels, "vessel", "vessels", "aisstream", now,
                    entity_limit, frames.get("vessel"))
            if "aircraft" in topics:
                sec = self._entity_section(
                    self.aircraft, "aircraft", "aircraft", "opensky", now,
                    entity_limit, frames.get("aircraft"))
                table = ProvTable()
                hubs = [self._hub_item(h, table, now) for h in
                        sorted(self._hubs.values(),
                               key=lambda h: h["node_id"])]
                sec["hubs"] = {"served": bool(hubs), "items": hubs,
                               "provenance": table.rows,
                               "status": "NOT SERVED" if not hubs else
                               self._best_status(table.rows)}
                out["aircraft"] = sec
            if "ports" in topics:
                out["ports"] = self._keyed_section(
                    self._ports, "ports", "portwatch", now, ports_limit,
                    lambda r: (-(r["vessel_count_total"] or 0), r["key"]))
            if "borders" in topics:
                out["borders"] = self._keyed_section(
                    self._borders, "borders", "cbp", now, borders_limit,
                    lambda r: (-(r["wait_min"] or 0), r["key"]))
            return out

    @staticmethod
    def _best_status(prov_rows) -> str:
        return "LIVE" if any(p["mode"] == "LIVE" for p in prov_rows) \
            else "SNAPSHOT"

    def _events_section(self, now, limit):
        st = self.topic_status("events", now)
        rows = sorted(self._events.values(), key=lambda r: (-r["t"], r["id"]))
        table = ProvTable()
        items = [self._event_item(r, table, now) for r in rows[:limit]]
        return {**st, "total": len(rows), "count": len(items),
                "items": items, "provenance": table.rows}

    def _entity_section(self, store, kind, topic, feed, now, limit, fr=None):
        st = self.topic_status(topic, now)
        sec = {**st, "total": len(store)}
        if limit and len(store):
            fr = fr or self.frame(kind)
            sel = fr.select()[:limit]
            cols, prov = fr.columns(sel)
            sec.update({"count": len(sel), "truncated": fr.n > len(sel),
                        "columns": cols, "provenance": prov})
        else:
            sec.update({"count": 0, "truncated": False, "columns": {},
                        "provenance": []})
        return sec

    def _keyed_section(self, table, topic, feed, now, limit, keyfn):
        st = self.topic_status(topic, now)
        rows = sorted(table.values(), key=keyfn)
        pt = ProvTable()
        items = [self._keyed_item(r, pt, feed, now) for r in rows[:limit]]
        return {**st, "total": len(rows), "count": len(items),
                "items": items, "provenance": pt.rows}

    # ------------------------------------------------------------------
    # columnar frames
    # ------------------------------------------------------------------
    def _store_feed(self, kind: str):
        return (self.vessels, "aisstream") if kind == "vessel" \
            else (self.aircraft, "opensky")

    def _frame_fresh(self, fr, store, now: float, max_stale_s) -> bool:
        if fr is None:
            return False
        ttl = self.frame_ttl_s if max_stale_s is None else max_stale_s
        age = now - fr.built_at
        if ttl > 0:
            return age <= ttl
        return fr.version == store.version and age <= 0

    def frame(self, kind: str, *, max_stale_s: float | None = None) -> Frame:
        """Columnar view of every held ``kind`` entity.

        Cached: re-built at most once per ``frame_ttl_s`` (or whenever
        the store changed when that is 0). Single-flight: while one
        thread rebuilds, concurrent callers get the previous frame
        instead of piling up. The rebuild reads the store in slices of
        2,000 entities, taking the hub lock per slice, so ingest and
        the event loop are never blocked for the whole O(held) pass."""
        store, feed = self._store_feed(kind)
        now = self.clock()
        fr = self._frames.get(kind)
        if self._frame_fresh(fr, store, now, max_stale_s):
            return fr
        lock = self._frame_locks[kind]
        if not lock.acquire(blocking=fr is None):
            return fr                      # someone else is rebuilding
        try:
            now = self.clock()
            fr = self._frames.get(kind)
            if self._frame_fresh(fr, store, now, max_stale_s):
                return fr
            with self._lock:
                ids = list(store._ents.keys())
                version = store.version
            rows = []
            for i in range(0, len(ids), 2000):
                with self._lock:
                    ents = store._ents
                    for ent_id in ids[i:i + 2000]:
                        e = ents.get(ent_id)
                        if e is None or not e.pos:
                            continue
                        t, la, lo, sp, hd, al = e.pos[-6:]
                        rows.append((t, ent_id, la, lo, sp, hd, al, e.name,
                                     e.prov))
            self._frame_seq += 1
            fr = build_frame(
                rows, kind, now,
                lambda p: self.effective_entity(p, feed, now),
                version=version, frame_id=self._frame_seq)
            self._frames[kind] = fr
            return fr
        finally:
            lock.release()

    def entities(self, kind: str, *, bbox=None, limit: int = 1000,
                 max_age_s: float | None = None,
                 include_names: bool = False,
                 frame: Frame | None = None) -> dict:
        topic = "vessels" if kind == "vessel" else "aircraft"
        store, feed = self._store_feed(kind)
        fr = frame or self.frame(kind)
        sel_all = fr.select(bbox, max_age_s)
        sel = sel_all[:limit]
        cols, prov = fr.columns(sel, include_names=include_names)
        now = fr.built_at
        with self._lock:
            st = self.topic_status(topic, now)
            fv = self.feed_view(feed, now)
        return {"kind": kind, "generated_at": iso_z(now), **st,
                "feed": {"id": feed, "state": fv["state"],
                         "retrieved_at": fv["retrieved_at"],
                         "age_s": fv["age_s"], "note": fv["note"]},
                "bbox": list(bbox) if bbox else None,
                "held": fr.n, "matched": len(sel_all), "count": len(sel),
                "truncated": len(sel_all) > len(sel),
                "columns": cols, "provenance": prov}

    def entity_detail(self, kind: str, ent_id: str, track_limit: int):
        store = self.vessels if kind == "vessel" else self.aircraft
        feed = "aisstream" if kind == "vessel" else "opensky"
        with self._lock:
            ent = store.get(ent_id)
            if ent is None:
                return None
            now = self.clock()
            prov = self.effective_entity(ent.prov, feed, now)
            lat = ent.latest()
            positions = ent.positions(track_limit)
            return {"kind": kind, "id": ent.id, "name": ent.name,
                    "attrs": dict(ent.attrs),
                    "provenance": {"source": prov[0],
                                   "retrieved_at": prov[1],
                                   "mode": prov[2]},
                    "latest": {"t": int(lat[0]), "lat": round(lat[1], 5),
                               "lon": round(lat[2], 5),
                               "age_s": int(max(0, now - lat[0]))},
                    "positions_held": ent.n_positions(),
                    "track": {"n": len(positions),
                              "columns": encode_track(positions, now, kind)},
                    "feed": {"id": feed,
                             "state": self.feed_view(feed, now)["state"]},
                    "generated_at": iso_z(now)}

    def stats(self) -> dict:
        with self._lock:
            mem = (self.vessels.memory_estimate_bytes()
                   + self.aircraft.memory_estimate_bytes()
                   + self._ring_size + len(self._static) * 450)
            return {"epoch": self.epoch, "seq": self.seq,
                    "clients": len(self._clients),
                    "max_clients": self.max_clients,
                    "max_per_client": self.max_per_client,
                    "client_queue": self.client_queue,
                    "dropped_total": self.dropped_total,
                    "ring": {"messages": len(self._ring),
                             "bytes": self._ring_size,
                             "max_messages": self.ring_max,
                             "max_bytes": self.ring_bytes,
                             "oldest_seq": self._ring[0].seq
                             if self._ring else None},
                    "held": {"events": len(self._events),
                             "vessels": len(self.vessels),
                             "vessel_positions":
                             self.vessels.positions_total,
                             "aircraft": len(self.aircraft),
                             "aircraft_positions":
                             self.aircraft.positions_total,
                             "static_pending": len(self._static),
                             "air_hubs": len(self._hubs),
                             "ports": len(self._ports),
                             "borders": len(self._borders)},
                    "evicted": {"vessels": self.vessels.evicted_total,
                                "aircraft": self.aircraft.evicted_total},
                    "expired": {"vessels": self.vessels.expired_total,
                                "aircraft": self.aircraft.expired_total},
                    "memory_estimate_bytes": mem}

    def _kind_stats(self, kind: str) -> dict:
        """Counts, report-age percentiles and mode split for one kind in
        one O(held) pass over the stores (no columnar frame), cached for
        2 s when the hub runs with a frame ttl."""
        store, feed = self._store_feed(kind)
        now = self.clock()
        hit = self._kstats.get(kind)
        if hit is not None and self.frame_ttl_s > 0 and now - hit[0] < 2.0:
            return hit[1]
        with self._lock:
            ents = store._ents.values()
            lts = [e.last_t for e in ents]
            provs = Counter(e.prov for e in ents)
            held, pos = len(store), store.positions_total
        lts.sort(reverse=True)           # newest first -> ages ascending
        n = len(lts)

        def age(q):
            return None if not n else max(
                0, int(now - lts[min(n - 1, int(q * n))]))
        modes: dict = {}
        for prov, cnt in provs.items():
            m = self.effective_entity(prov, feed, now)[2]
            modes[m] = modes.get(m, 0) + cnt
        out = {"held": held, "positions_held": pos,
               "track_len": store.track_len,
               "max_entities": store.max_entities,
               "evicted_total": store.evicted_total,
               "expired_total": store.expired_total,
               "report_age_s": {"p50": age(0.5), "p95": age(0.95),
                                "max": None if not n else
                                max(0, int(now - lts[-1]))},
               "modes": modes}
        self._kstats[kind] = (now, out)
        return out

    def live_stats(self) -> dict:
        """The cheap /live/stats view: counts by kind, report-age
        percentiles, feed states, collector state and budget. One O(held)
        pass per kind, cached 2 s; it never builds a columnar frame."""
        kinds = {kind: self._kind_stats(kind)
                 for kind in ("vessel", "aircraft")}
        with self._lock:
            now = self.clock()
            hub = self.stats()
            feeds = [{k: v for k, v in self.feed_view(f, now).items()
                      if k in ("id", "state", "age_s", "records", "note",
                               "rejected")} for f in FEED_IDS
                     if f in ("opensky", "aisstream")]
            cols = dict(self.collectors)
        collectors = {}
        for name, c in cols.items():
            try:
                collectors[name] = c.status()
            except Exception as exc:       # a broken collector never 500s
                collectors[name] = {"state": "ERROR",
                                    "error": type(exc).__name__}
        return {"generated_at": iso_z(now), "kinds": kinds, "feeds": feeds,
                "collectors": collectors,
                "hub": {"seq": hub["seq"], "clients": hub["clients"],
                        "dropped_total": hub["dropped_total"],
                        "memory_estimate_bytes":
                        hub["memory_estimate_bytes"],
                        "ring": hub["ring"]}}
