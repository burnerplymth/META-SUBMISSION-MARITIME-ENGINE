"""Compact payload encoding: columnar entity arrays, provenance tables,
gzip helper. Pure functions; no hub state."""
from __future__ import annotations

import gzip
import json
import math

from .tracks import ALT, HDG, LAT, LON, SPD, T


def dumps(obj) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)


def _r(v, nd):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) \
        else round(v, nd)


def _i(v):
    return None if v != v else int(round(v))


class ProvTable:
    """Dictionary-encodes provenance triples: rows carry a small index,
    the table carries {source, retrieved_at, mode} once."""

    def __init__(self):
        self._idx: dict = {}
        self.rows: list[dict] = []

    def index(self, prov: tuple) -> int:
        i = self._idx.get(prov)
        if i is None:
            i = len(self.rows)
            self._idx[prov] = i
            self.rows.append({"source": prov[0], "retrieved_at": prov[1],
                              "mode": prov[2]})
        return i


def encode_entities(entities, now: float, kind: str, mode_for, *,
                    include_names: bool = False, with_age: bool = True):
    """Columnar arrays for ``entities`` (already ordered). Returns
    (columns, provenance_table_rows). ``mode_for(prov) -> prov`` maps a
    stored provenance triple to the effective one (LIVE only when the
    feed answered recently)."""
    table = ProvTable()
    ids, lat, lon, spd, hdg, ts, prov_ix = [], [], [], [], [], [], []
    alt, names = [], []
    ages = []
    for e in entities:
        row = e.pos[-6:]
        ids.append(e.id)
        lat.append(round(row[LAT], 5))
        lon.append(round(row[LON], 5))
        spd.append(_r(row[SPD], 1))
        hdg.append(_i(row[HDG]))
        ts.append(int(row[T]))
        if with_age:
            ages.append(int(max(0, now - row[T])))
        prov_ix.append(table.index(mode_for(e.prov)))
        if kind == "aircraft":
            alt.append(_i(row[ALT]))
        if include_names:
            names.append(e.name)
    cols = {"id": ids, "lat": lat, "lon": lon, "spd_kn": spd, "hdg": hdg,
            "t": ts}
    if with_age:
        cols["age_s"] = ages
    if kind == "aircraft":
        cols["alt_m"] = alt
    if include_names:
        cols["name"] = names
    cols["p"] = prov_ix
    return cols, table.rows


def encode_track(positions: list[tuple], now: float, kind: str):
    cols = {"t": [int(p[T]) for p in positions],
            "lat": [round(p[LAT], 5) for p in positions],
            "lon": [round(p[LON], 5) for p in positions],
            "spd_kn": [_r(p[SPD], 1) for p in positions],
            "hdg": [_i(p[HDG]) for p in positions]}
    if kind == "aircraft":
        cols["alt_m"] = [_i(p[ALT]) for p in positions]
    return cols


def maybe_gzip(body: bytes, accept_encoding: str | None,
               min_bytes: int = 1024):
    """(body, headers) — gzip only when the client accepts it and the
    body is big enough to be worth it. Level 5: ~fast, ~good."""
    if accept_encoding and "gzip" in accept_encoding.lower() \
            and len(body) >= min_bytes:
        return gzip.compress(body, compresslevel=5, mtime=0), \
            {"Content-Encoding": "gzip", "Vary": "Accept-Encoding"}
    return body, {"Vary": "Accept-Encoding"}


# ----------------------------------------------------------------------
# Columnar frames: the whole held fleet of one kind as parallel lists,
# newest report first (then id), built once and re-used by many requests.
# ----------------------------------------------------------------------
class Frame:
    """Immutable columnar view of one entity kind at ``built_at``.

    ``select`` filters by bbox / max age by list comprehension over
    cached lists, so a request costs O(held) cheap comparisons plus
    O(returned) column copies instead of re-reading every entity's
    packed track."""

    __slots__ = ("id", "kind", "built_at", "version", "n", "ids", "lat",
                 "lon", "spd", "hdg", "t", "alt", "name", "p", "prov_rows")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    def select(self, bbox=None, max_age_s=None) -> list[int]:
        """Row indices (newest first) matching the filters."""
        lat, lon, t = self.lat, self.lon, self.t
        n = self.n
        if max_age_s is not None:
            cutoff = self.built_at - max_age_s
            # t is sorted descending: the rows within max age are a prefix
            lo, hi = 0, n
            while lo < hi:
                mid = (lo + hi) // 2
                if t[mid] >= cutoff:
                    lo = mid + 1
                else:
                    hi = mid
            n = lo
        if bbox is None:
            return list(range(n))
        min_lon, min_lat, max_lon, max_lat = bbox
        if min_lon <= max_lon:
            return [i for i in range(n) if min_lat <= lat[i] <= max_lat
                    and min_lon <= lon[i] <= max_lon]
        return [i for i in range(n) if min_lat <= lat[i] <= max_lat
                and (lon[i] >= min_lon or lon[i] <= max_lon)]

    def columns(self, sel: list[int], *, with_age: bool = True,
                include_names: bool = False):
        """(columns, provenance rows) for the selected rows."""
        now = self.built_at
        t = self.t
        cols = {"id": [self.ids[i] for i in sel],
                "lat": [self.lat[i] for i in sel],
                "lon": [self.lon[i] for i in sel],
                "spd_kn": [self.spd[i] for i in sel],
                "hdg": [self.hdg[i] for i in sel],
                "t": [t[i] for i in sel]}
        if with_age:
            cols["age_s"] = [max(0, int(now - t[i])) for i in sel]
        if self.alt is not None:
            cols["alt_m"] = [self.alt[i] for i in sel]
        if include_names:
            cols["name"] = [self.name[i] for i in sel]
        p = [self.p[i] for i in sel]
        used = sorted(set(p))
        if len(used) == len(self.prov_rows):
            prov = self.prov_rows
        else:
            remap = {u: j for j, u in enumerate(used)}
            p = [remap[x] for x in p]
            prov = [self.prov_rows[u] for u in used]
        cols["p"] = p
        return cols, prov


def build_frame(rows, kind: str, built_at: float, mode_for, *, version: int,
                frame_id: int) -> Frame:
    """``rows``: iterable of (t, id, lat, lon, spd, hdg, alt, name, prov)
    in any order; sorted newest first, then id."""
    rows = sorted(rows, key=lambda r: (-r[0], r[1]))
    table = ProvTable()
    memo: dict = {}
    ids, lat, lon, spd, hdg, ts, alt, names, p = ([] for _ in range(9))
    for t, i, la, lo, sp, hd, al, nm, prov in rows:
        ids.append(i)
        lat.append(round(la, 5))
        lon.append(round(lo, 5))
        spd.append(None if sp != sp else round(sp, 1))
        hdg.append(None if hd != hd else int(round(hd)))
        ts.append(int(t))
        if kind == "aircraft":
            alt.append(None if al != al else int(round(al)))
        names.append(nm)
        ix = memo.get(prov)
        if ix is None:
            ix = memo[prov] = table.index(mode_for(prov))
        p.append(ix)
    return Frame(id=frame_id, kind=kind, built_at=built_at, version=version,
                 n=len(ids), ids=ids, lat=lat, lon=lon, spd=spd, hdg=hdg,
                 t=ts, alt=alt if kind == "aircraft" else None, name=names,
                 p=p, prov_rows=table.rows)
