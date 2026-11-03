"""IMF PortWatch daily layers - HISTORY-CAPABLE (verified).

Two ArcGIS FeatureServer layers hold ~6 years of DAILY rows (the same
services ``server/adapters/portwatch_daily.py`` reads for its trailing
window):

  * ``Daily_Chokepoints_Data`` - transits (n_*) and transit capacity per
    chokepoint per day, by the five PortWatch vessel classes;
  * ``Daily_Ports_Data`` - port calls, import/export tonnage per port
    per day. Import/export are IMF-MODELLED estimates from AIS draught
    changes (WP/20/57) and carry "SOURCED - PORTWATCH AIS ESTIMATE".

Observations: per place, per day. Events: INFERRED from an observed drop
(rule ``portwatch_drop_v1``, series_math.infer_episodes) - mode MODELED.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta, timezone

from ..series_math import infer_episodes
from ..transport import HttpRequest
from ..util import iso_day, parse_day, day_range
from .base import (CAPABLE, HistoryProvider, NotServed, not_served)

BASE = "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services"
CHOKE_URL = f"{BASE}/Daily_Chokepoints_Data/FeatureServer/0/query"
PORTS_URL = f"{BASE}/Daily_Ports_Data/FeatureServer/0/query"
SOURCE_LABEL = "IMF PortWatch (daily layers)"
PAGE = 1000
MAX_PAGES = 6
CLASSES = ("container", "dry_bulk", "general_cargo", "roro", "tanker")
ESTIMATE_LABEL = "SOURCED — PORTWATCH AIS ESTIMATE"

# Only the fields the metrics use are fetched and cached (fixtures stay
# small): counts by class, and the all-vessel capacity / import / export.
_CHOKE_FIELDS = (["date", "portid", "portname"]
                 + [f"n_{c}" for c in CLASSES] + ["n_cargo", "n_total",
                                                  "capacity"])
_PORT_FIELDS = (["date", "portid", "portname"]
                + [f"portcalls_{c}" for c in CLASSES] + ["portcalls_cargo",
                                                        "portcalls",
                                                        "import", "export"])


def metric_table(layer: str) -> dict:
    """metric name -> (native field, unit, label)."""
    t: dict = {}
    if layer == "chokepoint":
        t["transits"] = ("n_total", "vessels/day", "Transits, all vessels")
        t["transits_cargo"] = ("n_cargo", "vessels/day", "Transits, cargo vessels")
        for c in CLASSES:
            t[f"transits_{c}"] = (f"n_{c}", "vessels/day", f"Transits, {c}")
        t["transit_capacity"] = ("capacity", "dwt-tonnes/day",
                                 "Transit capacity (vessel deadweight)")
    else:
        t["port_calls"] = ("portcalls", "calls/day", "Port calls, all vessels")
        t["port_calls_cargo"] = ("portcalls_cargo", "calls/day",
                                 "Port calls, cargo vessels")
        for c in CLASSES:
            t[f"port_calls_{c}"] = (f"portcalls_{c}", "calls/day",
                                    f"Port calls, {c}")
        t["import_tonnes"] = ("import", "tonnes/day",
                              "Import cargo (PortWatch AIS estimate)")
        t["export_tonnes"] = ("export", "tonnes/day",
                              "Export cargo (PortWatch AIS estimate)")
    return t


def default_metric(layer: str) -> str:
    return "transits" if layer == "chokepoint" else "port_calls"


def _day_of(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc).date().isoformat()
    text = str(value)[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


class PortWatchProvider(HistoryProvider):
    name = "imf_portwatch"
    label = SOURCE_LABEL
    capability = CAPABLE
    budget_key = "imf_portwatch"
    supports_events = True
    supports_observations = True
    history_window = ("daily rows from 2019-01-01 to ~1 week behind today "
                      "(chokepoints: 28 listed incl. Suez, Panama, "
                      "Bab el-Mandeb, Hormuz, Malacca; ports: ~2,000)")
    verified = {
        "checked_on": "2026-10-25", "result": "OK",
        "how": ("POST form query to Daily_Chokepoints_Data and "
                "Daily_Ports_Data FeatureServer/0 with a date range in the "
                "WHERE clause; the seeded fixtures "
                "under data/history_cache/imf_portwatch/ were fetched by "
                "this provider on that date"),
    }
    notes = ("The graph's place -> series join is the daily adapter's own "
             "(chk_* by layer name; ports by pinned portid). sea_longbeach "
             "has NO series of its own: PortWatch serves the combined "
             "Los Angeles-Long Beach complex under sea_losangeles. Port "
             "import/export tonnage is a MODELLED estimate and is labelled "
             "so; the default metrics (transits, port_calls) are counts.")

    # --------------------------------------------------------- plumbing
    def _series_spec(self, place: str) -> dict:
        book = self.svc.book()
        if place not in book:
            raise NotServed("unknown_place", f"{place!r} is not a graph place")
        spec = book.portwatch_series(place)
        if spec is None:
            raise NotServed("no_series_for_place",
                            f"PortWatch has no daily series joined to {place}")
        return spec

    def _fetch_rows(self, spec: dict, start: date, end: date):
        if spec["layer"] == "chokepoint":
            url, fields = CHOKE_URL, _CHOKE_FIELDS
            name = spec["portname"].replace("'", "''")
            where = f"portname = '{name}'"
        else:
            url, fields = PORTS_URL, _PORT_FIELDS
            where = f"portid = '{spec['portid']}'"
        where += (f" AND date >= DATE '{iso_day(start)}'"
                  f" AND date <= DATE '{iso_day(end)}'")
        rows: list[dict] = []
        offset = 0
        last_req = None
        for _ in range(MAX_PAGES):
            form = {"where": where, "outFields": ",".join(fields),
                    "orderByFields": "date ASC", "returnGeometry": "false",
                    "f": "json", "resultOffset": str(offset),
                    "resultRecordCount": str(PAGE)}
            req = HttpRequest("POST", url, form=form)
            last_req = req
            resp = self.http(req)
            doc = self.json_body(resp, "PortWatch daily query")
            if not isinstance(doc, dict) or doc.get("error"):
                raise NotServed("bad_payload", "PortWatch returned an error "
                                f"object: {str(doc.get('error'))[:120]}")
            batch = [f.get("attributes", {}) for f in doc.get("features", [])]
            rows.extend(batch)
            if len(batch) < PAGE:
                break
            offset += len(batch)
        else:
            raise NotServed("range_too_large",
                            f"more than {MAX_PAGES * PAGE} rows for one query")
        clean = []
        seen = set()
        for r in rows:
            d = _day_of(r.get("date"))
            if d is None or d in seen:
                continue
            seen.add(d)
            row = {"date": d}
            for f in fields:
                if f in ("date",):
                    continue
                if f in r:
                    row[f] = r[f]
            clean.append(row)
        clean.sort(key=lambda x: x["date"])
        pub = last_req.public() if last_req else {}
        return clean, pub

    def _rows(self, place: str, start: date, end: date):
        spec = self._series_spec(place)
        series_key = ("chokepoint|" + spec["portname"] if spec["layer"] == "chokepoint"
                      else "port|" + spec["portid"])
        query = {"series": series_key, "from": iso_day(start), "to": iso_day(end)}

        def fetcher():
            rows, pub = self._fetch_rows(spec, start, end)
            status = "OK" if rows else "EMPTY"
            note = (f"{len(rows)} daily rows" if rows
                    else "the layer returned no rows for this range")
            payload = {"layer": spec["layer"], "service":
                       "Daily_Chokepoints_Data" if spec["layer"] == "chokepoint"
                       else "Daily_Ports_Data", "spec": spec, "rows": rows}
            return payload, pub, status, note

        doc, hit = self.through_cache(query, fetcher)
        return spec, doc, hit

    # ----------------------------------------------------- observations
    def metrics(self) -> list[dict]:
        out = []
        for layer in ("chokepoint", "port"):
            for m, (field, unit, label) in metric_table(layer).items():
                out.append({"metric": m, "applies_to": layer, "unit": unit,
                            "label": label, "native_field": field})
        out.append({"metric": "activity", "applies_to": "any",
                    "unit": "count/day",
                    "label": "transits (chokepoints) or port_calls (ports)",
                    "native_field": None})
        return out

    def observations(self, place: str, metric: str, start: date,
                     end: date) -> dict:
        try:
            spec = self._series_spec(place)
            layer = spec["layer"]
            table = metric_table(layer)
            m = default_metric(layer) if metric in ("activity", "", None) else metric
            if m not in table:
                raise NotServed("unsupported_metric",
                                f"{metric!r} not served for a {layer}; "
                                f"one of {sorted(table)}")
            spec, doc, hit = self._rows(place, start, end)
        except NotServed as ns:
            return not_served(self.name, ns.reason, ns.detail, place=place,
                              metric=metric)
        field, unit, label = table[m]
        by_day = {r["date"]: r.get(field) for r in doc["payload"]["rows"]}
        series = []
        missing = []
        for d in day_range(start, end):
            key = iso_day(d)
            v = by_day.get(key)
            if v is None:
                missing.append(key)
            else:
                series.append({"date": key, "value": v})
        prov = dict(doc["provenance"])
        out = {
            "served": True, "source": self.name, "place": place,
            "metric": m, "native_field": field, "unit": unit, "label": label,
            "frequency": "daily", "layer": layer,
            "series": series,
            "coverage": {"from": iso_day(start), "to": iso_day(end),
                         "n": len(series), "missing_dates": missing},
            "provenance": prov,
            "cache": self.cache_info(doc, hit),
        }
        if m in ("import_tonnes", "export_tonnes"):
            out["class_label"] = ESTIMATE_LABEL
        if layer == "port":
            out["portname"] = (doc["payload"]["rows"] or [{}])[0].get("portname")
        else:
            out["portname"] = spec.get("portname")
        return out

    # ----------------------------------------------------------- events
    def events(self, start: date, end: date, places=None) -> dict:
        """MODELED events inferred from an observed drop at ``places``."""
        if not places:
            return not_served(self.name, "places_required",
                              "PortWatch events are inferred per place; "
                              "pass places=<graph place ids>")
        events = []
        skipped = []
        retrieved = []
        lead = start - timedelta(days=35)
        for place in places:
            obs = self.observations(place, "activity", lead, end)
            if not obs.get("served"):
                skipped.append({"place": place, "reason": obs.get("reason"),
                                "detail": obs.get("detail")})
                continue
            series = {p["date"]: p["value"] for p in obs["series"]}
            retrieved.append(obs["provenance"]["retrieved_at"])
            for ep in infer_episodes(series, start, end):
                ev = self._episode_event(place, obs, ep, end)
                if ev:
                    events.append(ev)
        events.sort(key=lambda e: (e["started_at"], e["id"]))
        if not events and skipped and len(skipped) == len(list(places)):
            return not_served(self.name, skipped[0]["reason"] or "no_series",
                              skipped[0]["detail"] or "", skipped=skipped)
        return {"served": True, "source": self.name, "events": events,
                "skipped_places": skipped,
                "inference": ("portwatch_drop_v1: 7-day mean <= 70% of the "
                              "preceding 28-day baseline; MODELED, not an "
                              "observed incident record"),
                "provenance": {"source": SOURCE_LABEL + " — inferred "
                               "(portwatch_drop_v1)",
                               "retrieved_at": max(retrieved) if retrieved else None,
                               "mode": "MODELED"}}

    def _episode_event(self, place: str, obs: dict, ep: dict, end: date):
        loss = ep.get("mean_loss_fraction")
        if loss is None or loss <= 0:
            return None
        loss = round(min(max(loss, 0.01), 1.0), 2)
        onset: date = ep["onset"]
        detected: date = ep["detected"]
        retrieved_at = obs["provenance"]["retrieved_at"]
        sev = 5 if loss >= 0.7 else 4 if loss >= 0.5 else 3 if loss >= 0.35 else 2
        key = f"portwatch_drop_v1:{place}:{iso_day(onset)}"
        duration = float((ep["last_low"] - onset).days + 1)
        ev = {
            "id": "evt_%s_%s_capacity_cut_%s" % (
                onset.strftime("%Y%m%d"), place,
                hashlib.sha256(key.encode()).hexdigest()[:8]),
            "node_id": place, "kind": "capacity_cut", "severity": sev,
            "capacity_loss": loss,
            "started_at": iso_day(onset) + "T00:00:00Z",
            "detected_at": iso_day(detected) + "T00:00:00Z",
            "expected_duration_d": duration,
            "retrieved_at": retrieved_at,
            "source": SOURCE_LABEL + " — inferred (portwatch_drop_v1)",
            "evidence_url": "https://portwatch.imf.org/",
            "status": "resolved" if ep["closed"] else "active",
            "mode": "MODELED", "dedupe_key": key,
            "historical": True,
            "inference": {
                "rule": "portwatch_drop_v1", "metric": obs["metric"],
                "baseline_per_day": round(ep["baseline"], 3),
                "mean_loss_fraction": loss,
                "episode_closed": bool(ep["closed"]),
                "observed_through": iso_day(min(end, ep["last_low"]))
                if not ep["closed"] else iso_day(ep["last_low"]),
                "note": ("capacity_loss is the observed fall in the "
                         "activity count, inferred - not a sourced figure; "
                         "expected_duration_d is the observed low stretch"
                         + ("" if ep["closed"] else
                            " so far (a lower bound; still low at the end "
                            "of the window)")),
            },
            "provenance": {"source": SOURCE_LABEL + " — inferred "
                           "(portwatch_drop_v1)",
                           "retrieved_at": retrieved_at, "mode": "MODELED"},
        }
        return ev
