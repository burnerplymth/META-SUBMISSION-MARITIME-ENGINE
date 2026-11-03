"""Bounded vessel / aircraft track store.

Memory is bounded twice: at most ``max_entities`` entities per kind
(least-recently-updated evicted, deterministic) and at most
``track_len`` positions per entity. Positions are packed into one flat
``array('d')`` per entity (6 doubles each: t, lat, lon, spd_kn, hdg,
alt_m; NaN = not reported) so a position costs 48 bytes, not a tuple of
boxed floats. Nothing is ever interpolated or invented: a track holds
only positions a feed reported.
"""
from __future__ import annotations

import math
from array import array
from collections import OrderedDict

NAN = float("nan")
STRIDE = 6          # t, lat, lon, spd_kn, hdg, alt_m
T, LAT, LON, SPD, HDG, ALT = range(6)
POSITION_BYTES = STRIDE * 8
ENTITY_OVERHEAD_BYTES = 700    # Entity + dict slot + attrs + name


class Entity:
    __slots__ = ("id", "kind", "name", "attrs", "pos", "prov", "last_t")

    def __init__(self, ent_id: str, kind: str):
        self.id = ent_id
        self.kind = kind
        self.name: str | None = None
        self.attrs: dict = {}
        self.pos = array("d")
        self.prov: tuple = ("", "", "")   # (source, retrieved_at, mode)
        self.last_t = -math.inf

    def n_positions(self) -> int:
        return len(self.pos) // STRIDE

    def latest(self) -> tuple:
        return tuple(self.pos[-STRIDE:])

    def positions(self, limit: int | None = None) -> list[tuple]:
        n = self.n_positions()
        start = 0 if limit is None else max(0, n - limit)
        return [tuple(self.pos[i * STRIDE:(i + 1) * STRIDE])
                for i in range(start, n)]


def _nz(v) -> float:
    return NAN if v is None else float(v)


class TrackStore:
    """One kind (vessel | aircraft). Not thread-safe by itself: the hub
    serialises access under its lock."""

    def __init__(self, kind: str, *, max_entities: int = 20000,
                 track_len: int = 40, thin_s: float = 0.0):
        self.kind = kind
        self.max_entities = max(1, int(max_entities))
        self.track_len = max(1, int(track_len))
        # Track thinning for chatty continuous feeds: a report less than
        # ``thin_s`` after the last FROZEN point REPLACES the head point
        # (the head always tracks the latest report; frozen points stay
        # >= thin_s apart), so a fast reporter cannot burn
        # the whole ring in a minute. 0 = keep every report.
        self.thin_s = max(0.0, float(thin_s))
        self._ents: "OrderedDict[str, Entity]" = OrderedDict()
        self.evicted_total = 0
        self.expired_total = 0
        self.positions_total = 0     # currently held
        # bumped on every mutation that can change a served row; the
        # hub's columnar frame cache keys on it.
        self.version = 0

    def __len__(self) -> int:
        return len(self._ents)

    def get(self, ent_id: str) -> Entity | None:
        return self._ents.get(ent_id)

    def entities(self):
        return self._ents.values()

    def memory_estimate_bytes(self) -> int:
        # packed positions + ~400 B/entity of object and dict overhead
        # (+ the attrs dict when an entity has one, ~300 B)
        return self.positions_total * POSITION_BYTES \
            + len(self._ents) * ENTITY_OVERHEAD_BYTES

    def prune_older(self, cutoff_t: float) -> list[str]:
        """Drop entities whose newest report is older than ``cutoff_t``
        (epoch s). Returns the removed ids."""
        dead = [i for i, e in self._ents.items() if e.last_t < cutoff_t]
        for i in dead:
            old = self._ents.pop(i)
            self.positions_total -= old.n_positions()
        if dead:
            self.expired_total += len(dead)
            self.version += 1
        return dead

    def add(self, ent_id: str, t: float, lat: float, lon: float, *,
            spd=None, hdg=None, alt=None, name=None, attrs=None,
            prov: tuple = ("", "", "")) -> tuple[bool, list[str]]:
        """Insert one reported position. Returns (changed, evicted_ids).

        ``changed`` is False for an exact duplicate (same t, lat, lon)
        or for a report older than everything held when the track is
        full. Out-of-order reports are inserted in time order."""
        ent = self._ents.get(ent_id)
        evicted: list[str] = []
        if ent is None:
            ent = Entity(ent_id, self.kind)
            self._ents[ent_id] = ent
            while len(self._ents) > self.max_entities:
                old_id, old = self._ents.popitem(last=False)
                self.positions_total -= old.n_positions()
                self.evicted_total += 1
                evicted.append(old_id)
        row = (float(t), float(lat), float(lon), _nz(spd), _nz(hdg), _nz(alt))
        pos = ent.pos
        n = len(pos) // STRIDE
        changed = True
        if n == 0:
            pos.extend(row)
        elif t >= pos[(n - 1) * STRIDE + T]:
            base = (n - 1) * STRIDE
            if pos[base + T] == row[T] and pos[base + LAT] == row[LAT] \
                    and pos[base + LON] == row[LON]:
                changed = False
                if prov[1] > ent.prov[1]:
                    ent.prov = prov   # same observation, fresher retrieval
            elif self.thin_s and n >= 2 and \
                    pos[base + T] - pos[(n - 2) * STRIDE + T] < self.thin_s:
                pos[base:base + STRIDE] = array("d", row)   # head moves
                self.version += 1
                self._touch(ent, ent_id, t, name, attrs, prov)
                return True, evicted
            else:
                pos.extend(row)
        else:
            # out-of-order: find the insertion index (tracks are short)
            idx = n
            while idx > 0 and pos[(idx - 1) * STRIDE + T] > t:
                idx -= 1
            dup = idx > 0 and pos[(idx - 1) * STRIDE + T] == t and \
                pos[(idx - 1) * STRIDE + LAT] == row[LAT] and \
                pos[(idx - 1) * STRIDE + LON] == row[LON]
            if dup or (n >= self.track_len and idx == 0):
                changed = False
            else:
                pos[idx * STRIDE:idx * STRIDE] = array("d", row)
        if changed:
            self.version += 1
            self.positions_total += 1
            n2 = len(pos) // STRIDE
            if n2 > self.track_len:
                drop = n2 - self.track_len
                del pos[:drop * STRIDE]
                self.positions_total -= drop
            self._touch(ent, ent_id, pos[-STRIDE + T], name, attrs, prov)
        elif evicted:
            self.version += 1
        return changed, evicted

    def _touch(self, ent, ent_id, newest, name, attrs, prov) -> None:
        if newest >= ent.last_t:
            ent.last_t = newest
            ent.prov = prov
            if name:
                ent.name = name
            if attrs:
                ent.attrs.update(attrs)
        self._ents.move_to_end(ent_id)

    def query(self, bbox=None, *, max_age_s=None, now=None, limit=None):
        """Entities whose latest position is inside ``bbox`` =
        (min_lon, min_lat, max_lon, max_lat); min_lon > max_lon means
        the box crosses the antimeridian. Newest report first, then id.
        Returns (matched_total, rows[:limit])."""
        rows = []
        for ent in self._ents.values():
            if not ent.pos:
                continue
            lat = ent.pos[-STRIDE + LAT]
            lon = ent.pos[-STRIDE + LON]
            if bbox is not None:
                min_lon, min_lat, max_lon, max_lat = bbox
                if lat < min_lat or lat > max_lat:
                    continue
                if min_lon <= max_lon:
                    if lon < min_lon or lon > max_lon:
                        continue
                elif not (lon >= min_lon or lon <= max_lon):
                    continue
            if max_age_s is not None and now is not None \
                    and now - ent.last_t > max_age_s:
                continue
            rows.append(ent)
        rows.sort(key=lambda e: (-e.last_t, e.id))
        total = len(rows)
        if limit is not None:
            rows = rows[:limit]
        return total, rows
