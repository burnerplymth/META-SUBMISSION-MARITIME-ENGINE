"""POST /research/assemble: aligned historical series, events, scenes and tracks for 1..5 places.

Serves the bundle (data/research/) first and, only when online fetching is explicitly enabled,
falls back to the history layer (itself cache-first and budgeted). Nothing is simulated and a gap is
a ``null`` with a coverage note, never a fill.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import datasets as D
from .bundle import Bundle
from .util import (ASSEMBLY_RE, CATEGORIES, EXCLUDED_CATEGORIES, ID_RE, LABEL_ASSEMBLE, MAX_PLACES,
                   MAX_SCENES, MAX_SPAN_DAYS, SCENE_CLOUD_MAX, SCENE_NODATA_MAX, ResearchError,
                   canonical, check_depth, day_list, only_keys, parse_day, sha256_hex, str_list)

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
KEEP_ASSEMBLIES = 64
_TOP = {"places", "from", "to", "series", "events", "scenes", "tracks"}


def fetch_allowed() -> bool:
    """Online fetching is OFF unless SUPERMAP_RESEARCH_FETCH is auto/on/1 and the network is not blocked."""
    if os.environ.get("SUPERMAP_BLOCK_NETWORK") == "1":
        return False
    return os.environ.get("SUPERMAP_RESEARCH_FETCH", "off").strip().lower() in ("auto", "on", "1", "true")


def normalise_request(body) -> dict:
    """Validate and canonicalise an assemble request (defaults filled in)."""
    if not isinstance(body, dict):
        raise ResearchError("invalid_request", "body must be a JSON object")
    check_depth(body)
    only_keys(body, _TOP, "request")
    places = str_list(body.get("places"), "places", max_len=MAX_PLACES)
    if not places:
        raise ResearchError("invalid_request", "places must hold 1 to 5 place ids")
    places = list(dict.fromkeys(places))
    start, end = parse_day(body.get("from"), "from"), parse_day(body.get("to"), "to")
    if end < start:
        raise ResearchError("invalid_request", "to must be on or after from")
    if (end - start).days + 1 > MAX_SPAN_DAYS:
        raise ResearchError("window_too_large", "the window is longer than 5 years (1826 days)")
    series = sorted(set(str_list(body.get("series", []), "series")))
    known = set(D.SERIES) | set(D.NOT_SERVED)
    for s in series:
        if s not in known:
            raise ResearchError("unknown_dataset", f"unknown series dataset {s!r}")
        if D.NOT_SERVED.get(s, {}).get("kind") == "events":
            raise ResearchError("invalid_request", "use events.source for event datasets")

    ev = body.get("events")
    events = None
    if ev is not None:
        if not isinstance(ev, dict):
            raise ResearchError("invalid_request", "events must be an object")
        only_keys(ev, {"categories", "source"}, "events")
        cats = str_list(ev.get("categories", []), "events.categories")
        for c in cats:
            if c not in CATEGORIES:
                raise ResearchError("invalid_request", f"unknown event category {c!r}")
        src = ev.get("source", "episodes")
        if src not in ("episodes", "gdelt", "both"):
            raise ResearchError("invalid_request", "events.source must be episodes, gdelt or both")
        events = {"categories": sorted(set(cats)), "source": src}

    sc = body.get("scenes")
    scenes = {"enabled": False, "sources": ["sentinel-2", "sentinel-1"], "max_scenes": MAX_SCENES}
    if sc is not None:
        if not isinstance(sc, dict):
            raise ResearchError("invalid_request", "scenes must be an object")
        only_keys(sc, {"enabled", "sources", "max_scenes"}, "scenes")
        scenes["enabled"] = bool(sc.get("enabled", False))
        if "sources" in sc:
            srcs = str_list(sc["sources"], "scenes.sources", max_len=2)
            for s in srcs:
                if s not in D.SCENE_SENSORS:
                    raise ResearchError("invalid_request", "scenes.sources are sentinel-2 or sentinel-1")
            scenes["sources"] = sorted(set(srcs))
        if "max_scenes" in sc:
            m = sc["max_scenes"]
            if isinstance(m, bool) or not isinstance(m, int) or not 1 <= m <= MAX_SCENES:
                raise ResearchError("invalid_request", f"scenes.max_scenes must be 1..{MAX_SCENES}")
            scenes["max_scenes"] = m
    tr = body.get("tracks")
    tracks = {"enabled": False}
    if tr is not None:
        if not isinstance(tr, dict):
            raise ResearchError("invalid_request", "tracks must be an object")
        only_keys(tr, {"enabled"}, "tracks")
        tracks["enabled"] = bool(tr.get("enabled", False))
    return {"places": places, "from": start.isoformat(), "to": end.isoformat(), "series": series,
            "events": events, "scenes": scenes, "tracks": tracks}


def assembly_id_for(norm: dict, online: bool) -> str:
    return "asm_" + sha256_hex(canonical({"request": norm, "online": bool(online)}))[:16]


class AssemblyStore:
    """Hash-addressed, bounded store of finished assemblies under <data>/research_cache/assemblies."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self._lock = threading.Lock()

    def path(self, asm_id: str) -> Path:
        if not ASSEMBLY_RE.match(asm_id):
            raise ResearchError("invalid_request", "assembly_id is malformed")
        return self.root / f"{asm_id}.json"

    def get(self, asm_id: str) -> dict | None:
        p = self.path(asm_id)
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def put(self, asm_id: str, doc: dict) -> None:
        p = self.path(asm_id)
        with self._lock:
            try:
                self.root.mkdir(parents=True, exist_ok=True)
                fd, tmp = tempfile.mkstemp(dir=str(self.root), suffix=".tmp")
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(doc, fh, separators=(",", ":"), sort_keys=True)
                os.replace(tmp, p)
                files = sorted(self.root.glob("asm_*.json"), key=lambda q: q.stat().st_mtime, reverse=True)
                for old in files[KEEP_ASSEMBLIES:]:
                    try:
                        old.unlink()
                    except OSError:
                        pass
            except OSError:
                pass            # a read-only data dir must not take the route down


class Assembler:
    def __init__(self, bundle: Bundle, store: AssemblyStore, *, book=None, history=None,
                 online: bool | None = None):
        self.bundle = bundle
        self.store = store
        self._book = book            # callable -> PlaceBook-like (nodes, __contains__)
        self.history = history       # HistoryService or None
        self._online = online

    @property
    def online(self) -> bool:
        return fetch_allowed() if self._online is None else self._online

    # ---------------------------------------------------------------- places
    def place_meta(self, place: str) -> dict:
        book = self._book() if self._book else None
        if book is None or place not in book:
            raise ResearchError("unknown_place", f"{place!r} is not a known place", 404)
        n = book.nodes[place]
        return {"id": place, "name": n.get("name") or place, "kind": n.get("kind"),
                "lat": n.get("lat"), "lon": n.get("lon"),
                "portwatch": book.portwatch_series(place) is not None}

    def _layer(self, place: str) -> str | None:
        book = self._book() if self._book else None
        spec = book.portwatch_series(place) if book is not None else None
        return spec["layer"] if spec else None

    # -------------------------------------------------------------- assemble
    def assemble(self, body) -> dict:
        norm = normalise_request(body)
        online = self.online
        asm_id = assembly_id_for(norm, online)
        cached = self.store.get(asm_id)
        if cached is not None:
            return cached
        start, end = parse_day(norm["from"], "from"), parse_day(norm["to"], "to")
        metas = [self.place_meta(p) for p in norm["places"]]
        dates = day_list(start, end)
        notes: list[dict] = []
        series: dict = {}
        for dsid in norm["series"]:
            self._add_series(dsid, metas, start, end, series, notes, online)
        events = self._events(norm, metas, start, end, notes, online)
        scenes = self._scenes(norm, metas, start, end, notes)
        tracks = self._tracks(norm, metas, start, end, notes)
        out = {
            "assembly_id": asm_id, "label": LABEL_ASSEMBLE,
            "window": {"from": norm["from"], "to": norm["to"], "days": len(dates)},
            "places": metas,
            "timeline": {"t": dates, "series": series},
            "events": events, "scenes": scenes, "tracks": tracks,
            "coverage_notes": notes,
            "request": norm,
            "fetch_mode": "online (cache first, budgeted)" if online else "offline (bundled cache only)",
        }
        size = len(canonical(out))
        if size > MAX_RESPONSE_BYTES:
            raise ResearchError("assembly_too_large", "the assembly exceeds 2 MB; narrow the window or series")
        self.store.put(asm_id, out)
        return out

    # ---------------------------------------------------------------- series
    def _add_series(self, dsid, metas, start, end, series, notes, online):
        if dsid in D.NOT_SERVED:
            d = D.NOT_SERVED[dsid]
            series[f"{dsid}:global"] = {
                "dataset": dsid, "place": "global", "unit": d["unit"], "values": [],
                "source": d["source"], "state": "not_served",
                "provenance": {"source": d["source"], "retrieved_at": None, "mode": None}}
            notes.append({"chip": "not_served", "dataset": dsid, "place": None,
                          "text": "NOT SERVED: " + d["reason"]})
            return
        d = D.SERIES[dsid]
        targets = [{"id": "global"}] if d["kind"] == "economic" else metas
        for m in targets:
            place = m["id"]
            if d["applies_to"] in ("port", "chokepoint"):
                layer = self._layer(place) if place != "global" else None
                if layer is None:
                    notes.append({"chip": "no_series_for_place", "dataset": dsid, "place": place,
                                  "text": f"{dsid} has no series for {place}"})
                    continue
                if layer != d["applies_to"]:
                    notes.append({"chip": "not_applicable", "dataset": dsid, "place": place,
                                  "text": f"{dsid} applies to {d['applies_to']} places; {place} is a {layer}"})
                    continue
            got = self.bundle.series_values(dsid, place, start, end)
            state = "cached"
            retrieved = None
            if got is not None:
                values, retrieved = got
            elif online and self.history is not None and place != "global":
                values, retrieved, state = self._live_series(dsid, place, start, end)
            else:
                values = None
            if values is None:
                chip = "not_in_cache" if not online else "no_data"
                notes.append({"chip": chip, "dataset": dsid, "place": place,
                              "text": f"No {dsid} data for {place} in this window"
                                      + ("" if online else " (offline: bundled cache only)")})
                continue
            present = sum(1 for v in values if v is not None)
            if present < len(values):
                notes.append({"chip": "gaps_in_series", "dataset": dsid, "place": place,
                              "text": f"{present} of {len(values)} days have an observation",
                              "days_with_data": present, "days": len(values)})
            weather = d["kind"] == "weather"
            series[f"{dsid}:{place}"] = {
                "dataset": dsid, "place": place, "unit": d["unit"], "values": values,
                "source": d["source"], "state": state,
                "provenance": {"source": d["source"], "retrieved_at": retrieved,
                               "mode": "SNAPSHOT", "modelled": bool(weather)}}

    def _live_series(self, dsid, place, start, end):
        """History-layer fallback (cache first; a network call only if the cache misses)."""
        metric = {"portwatch_port_calls": "port_calls", "portwatch_port_calls_cargo": "port_calls_cargo",
                  "portwatch_transits": "transits", "portwatch_transits_cargo": "transits_cargo",
                  "portwatch_transit_capacity": "transit_capacity",
                  "weather_wind_max_kmh": "wind_speed_10m_max", "weather_gust_max_kmh": "wind_gusts_10m_max",
                  "weather_precip_mm": "precipitation_sum"}.get(dsid)
        if metric is None:
            return None, None, "not_served"
        try:
            r = self.history.observations(place, metric, start, end)
        except Exception:                      # noqa: BLE001 - a provider error is NOT SERVED, not a 500
            return None, None, "not_served"
        if not r.get("served"):
            return None, None, "not_served"
        by = {x["date"]: x["value"] for x in r["series"]}
        vals = [by.get(t) for t in day_list(start, end)]
        if all(v is None for v in vals):
            return None, None, "not_served"
        hit = (r.get("cache") or {}).get("hit", True)
        return vals, (r.get("provenance") or {}).get("retrieved_at"), ("cached" if hit else "live")

    # ---------------------------------------------------------------- events
    def _events(self, norm, metas, start, end, notes, online) -> list[dict]:
        ev = norm["events"]
        if ev is None:
            return []
        cats = ev["categories"]
        wanted = set(cats) if cats else set(CATEGORIES) - set(EXCLUDED_CATEGORIES)
        for c in cats:
            if c in EXCLUDED_CATEGORIES:
                notes.append({"chip": "category_not_in_library", "dataset": "events_curated", "place": None,
                              "text": f"Category {c} is not in the episode library"})
                wanted.discard(c)
        out: list[dict] = []
        pset = {m["id"] for m in metas}
        if ev["source"] in ("episodes", "both"):
            for ep in self.bundle.episodes():
                for e in ep["events"]:
                    if e["category"] not in wanted:
                        continue
                    if e["place"] is not None:
                        if e["place"] not in pset:
                            continue
                    elif not pset & set(ep["places"]):
                        continue
                    t0 = e["t"][:10]
                    t1 = (e["t_end"] or e["t"])[:10]
                    if t1 < start.isoformat() or t0 > end.isoformat():
                        continue
                    out.append({**e, "origin": "episodes"})
        if ev["source"] in ("gdelt", "both"):
            notes.append({"chip": "not_served", "dataset": "events_gdelt", "place": None,
                          "text": "NOT SERVED: " + D.NOT_SERVED["events_gdelt"]["reason"]})
        out.sort(key=lambda e: (e["t"], e["id"]))
        if not out and ev["source"] in ("episodes", "both"):
            notes.append({"chip": "no_events_in_library", "dataset": "events_curated", "place": None,
                          "text": "No curated events for these places, window and categories"})
        return out

    # ---------------------------------------------------------------- scenes
    def _scenes(self, norm, metas, start, end, notes) -> list[dict]:
        sc = norm["scenes"]
        if not sc["enabled"]:
            return []
        sam_by_scene = {}
        for e in self.bundle.sam_recorded():
            sam_by_scene.setdefault(e["scene_id"], e)
        rows = []
        for m in metas:
            for sensor in sc["sources"]:
                found = self.bundle.scenes(m["id"], sensor, start, end)
                if not found:
                    notes.append({"chip": "no_scenes", "dataset": D.SCENE_SENSORS[sensor], "place": m["id"],
                                  "text": f"No {sensor} acquisitions listed for {m['id']} in this window"})
                for s in found:
                    cloud, nod = s.get("cloud_pct"), s.get("nodata_pct")
                    usable = (cloud is None or cloud <= SCENE_CLOUD_MAX) and \
                             (nod is None or nod <= SCENE_NODATA_MAX)
                    rec = sam_by_scene.get(s["scene_id"])
                    rows.append({"t": s["date"], "place": m["id"], "source": sensor,
                                 "scene_id": s["scene_id"], "cloud_pct": cloud, "nodata_pct": nod,
                                 "usable": bool(usable),
                                 "sam": ({"prompt": rec["prompt"], "count": rec["count"],
                                          "note": rec.get("note"), "recorded": True,
                                          "label": "MODELLED"} if rec else None)})
        rows.sort(key=lambda r: (r["t"], r["place"], r["source"], r["scene_id"]))
        cap = sc["max_scenes"]
        if len(rows) > cap:
            n = len(rows)
            idx = sorted({round(i * (n - 1) / (cap - 1)) for i in range(cap)}) if cap > 1 else [0]
            notes.append({"chip": "scenes_subsampled", "dataset": "scenes", "place": None,
                          "text": f"{len(idx)} of {n} listed acquisitions kept, evenly spaced in time",
                          "listed": n, "kept": len(idx)})
            rows = [rows[i] for i in idx]
        return rows

    # ---------------------------------------------------------------- tracks
    def _tracks(self, norm, metas, start, end, notes) -> list[dict]:
        if not norm["tracks"]["enabled"]:
            return []
        out = []
        for m in metas:
            tr, prov = self.bundle.tracks(m["id"], start, end)
            if tr:
                for t in tr:
                    out.append({**t, "place": m["id"], "provenance": prov})
            else:
                notes.append({"chip": "no_open_track_data", "dataset": "tracks_ais", "place": m["id"],
                              "text": "No open track data for this window"})
        return out
