"""IMF PortWatch DAILY layers adapter (IMPACT layer).

Second PortWatch adapter, separate from the snapshot loader
(portwatch.py) because it reads two different services and serves a
different store input (data/snapshots/portwatch_daily.json):

  * Daily_Ports_Data (layer 0) — one row per port per day: port calls and
    import/export volumes by the five PortWatch vessel classes. The
    import/export figures are ESTIMATED metric tons of cargo, modelled
    by the IMF from AIS draught changes x vessel deadweight (WP/20/57);
    records therefore carry the class label —
    "SOURCED — PORTWATCH AIS ESTIMATE" — on every surface downstream.
  * Daily_Chokepoints_Data (layer 0) — transit counts and transit
    tonnage by class per chokepoint per day.

Windows: a node's trailing-30d window is the
30 days ending at THAT NODE's own max date in the layer, and the window
dates are stored in the record — never relabelled "last 30 days" from
today. Tier-1 country aggregates use one shared window anchored
at the ports layer's global max date, also stored.

Like portwatch.py this is a snapshot loader by design: fetch writes the
last-good pack (BaseAdapter machinery, name "portwatch_daily"), health
reports SNAPSHOT, and records are aggregates over served rows — the raw
daily rows (~5.9M in the layer) are never mirrored.

Join: Daily layers key on the same portid as the ports
database. Graph port -> portid comes from, in order: the graph build's
pinned join input (data/graph/portwatch_join_input.json), the ports
database matched by LOCODE (compact form vs node aliases) or by name
(NodeIndex). A graph port that matches nothing, or matches but has no
daily rows, is NAMED in the coverage record — never silently dropped
and never given a substitute port's data.
"""
from __future__ import annotations

import json
import urllib.parse
from datetime import date, timedelta
from pathlib import Path

from .base import AdapterError, BaseAdapter, FetchResult, REPO_ROOT, provenance
from .http_util import MAX_RESPONSE_BYTES, NetworkBlocked, network_blocked
from .nodes import NodeIndex
from .portwatch import BASE, PORTS_URL

DAILY_PORTS_URL = f"{BASE}/Daily_Ports_Data/FeatureServer/0/query"
DAILY_CHOKE_URL = f"{BASE}/Daily_Chokepoints_Data/FeatureServer/0/query"
SOURCE = "IMF PortWatch (daily layers)"
GOODS_CLASS_LABEL = "SOURCED — PORTWATCH AIS ESTIMATE"

CLASSES = ("container", "dry_bulk", "general_cargo", "roro", "tanker")
WINDOW_DAYS = 30
FETCH_BUFFER_DAYS = 120  # chokepoint rows: >= any node's window behind max
FETCH_SPAN_DAYS = 45     # port rows: 30d window + 15d of per-port lag;
                         # days_present in each record shows truncation
PAGE = 1000  # these services cap a page at 1,000 rows regardless of the
             # requested size (observed: a 2,000 request to the
             # ports database returned exactly 1,000 rows, silently)

# Printed portid overrides (the graph build's PORT_OVERRIDE idiom):
# graph places whose ports-database entry carries a compound name that
# neither the LOCODE nor the name index can match, verified against the
# database's own rows (portname quoted per entry). Each
# mapping is one-to-one. sea_longbeach is deliberately NOT here: the
# database serves the combined "Los Angeles-Long Beach" complex
# (port664), which sea_losangeles carries — assigning the same portid
# to Long Beach as well would serve one complex's flows twice.
PORTID_OVERRIDES = {
    "sea_guangzhou": "port425",    # DB: "Guangzhou (Nansha)"; graph alias "Nansha"
    "sea_taicang": "port1253",     # DB: "Suzhou (Taicang)"; graph alias "Taicang Port"
    "sea_tianjin": "port1297",     # DB: "Tianjin Xin Gang"; graph alias "Xingang"
    "sea_nhavasheva": "port776",   # DB: "Mumbai-Jawaharlal Nehru (Nhava Sheva)"; alias "Jawaharlal Nehru Port"
    "sea_tangermed": "port1265",   # DB: "Tangier-Mediterranean"; graph alias "Tangier Med"
}

# Graph chokepoint id -> Daily_Chokepoints_Data portname (the layer's own
# naming is stored in every record, so the mapping is never invisible).
# chk_danish's deep-water route in the layer is the Oresund Strait entry.
# chk_mozambique has NO entry in the layer (28 chokepoints listed)
# and is therefore a named miss, not a mapping.
CHOKEPOINT_LAYER_NAMES = {
    "chk_suez": "Suez Canal",
    "chk_panama": "Panama Canal",
    "chk_turkish": "Bosporus Strait",
    "chk_babelmandeb": "Bab el-Mandeb Strait",
    "chk_malacca": "Malacca Strait",
    "chk_hormuz": "Strait of Hormuz",
    "chk_capeofgoodhope": "Cape of Good Hope",
    "chk_gibraltar": "Gibraltar Strait",
    "chk_dover": "Dover Strait",
    "chk_danish": "Oresund Strait",
    "chk_taiwan": "Taiwan Strait",
}

_PORT_FIELDS = (["date", "portid", "portname", "country", "ISO3"]
                + [f"portcalls_{c}" for c in CLASSES] + ["portcalls_cargo", "portcalls"]
                + [f"import_{c}" for c in CLASSES] + ["import_cargo", "import"]
                + [f"export_{c}" for c in CLASSES] + ["export_cargo", "export"])
_CHOKE_FIELDS = (["date", "portid", "portname"]
                 + [f"n_{c}" for c in CLASSES] + ["n_cargo", "n_total"]
                 + [f"capacity_{c}" for c in CLASSES] + ["capacity_cargo", "capacity"])


def _query(url: str, params: dict) -> dict:
    """POST form-encoded query. GET is unusable for this adapter's long
    queries: the ArcGIS host's IIS front end 404s query strings over
    ~2,048 bytes (the country-statistics URL is ~2,300), so every query
    here POSTs — verified live."""
    import urllib.request

    # Kill switch (offline / recorded demo): same contract as http_util.
    # NetworkBlocked is not an AdapterError, so BaseAdapter.fetch falls
    # back to the labelled last-good pack exactly as for a real failure.
    if network_blocked():
        raise NetworkBlocked(
            f"egress blocked by SUPERMAP_BLOCK_NETWORK for {url}")
    body = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "User-Agent": "supermap-adapters/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            status = resp.status
            raw = resp.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                # this adapter's own urllib read gets the
                # same 5 MiB cap as http_util's read paths.
                raise AdapterError(
                    f"PortWatch daily response exceeds the "
                    f"{MAX_RESPONSE_BYTES}-byte cap at {url}; aborted")
            payload = json.loads(raw.decode("utf-8", "replace"))
    except AdapterError:
        raise
    except Exception as exc:  # HTTPError, URLError, JSONDecodeError, ...
        raise AdapterError(f"PortWatch daily query failed at {url}: {exc}") from exc
    if status != 200 or not isinstance(payload, dict):
        raise AdapterError(f"PortWatch daily query HTTP {status} at {url}")
    if payload.get("error"):
        raise AdapterError(f"PortWatch daily query error at {url}: {payload['error']}")
    return payload


def _rows(payload: dict) -> list[dict]:
    return [f.get("attributes", {}) for f in payload.get("features", [])]


def _paged(url: str, params: dict, query=_query, order_by: str | None = "ObjectId") -> list[dict]:
    """All rows for a query, following ArcGIS resultOffset pagination.
    order_by=None for grouped-statistics queries: their output has no
    ObjectId and the service rejects the whole request if it is named.
    The offset advances by the rows actually returned: services cap a
    page below the requested size (the ports database caps at 1,000),
    and exceededTransferLimit is the signal that more pages exist."""
    out: list[dict] = []
    offset = 0
    while True:
        page_params = dict(params, resultOffset=offset, resultRecordCount=PAGE)
        if order_by:
            page_params["orderByFields"] = order_by
        payload = query(url, page_params)
        batch = _rows(payload)
        out.extend(batch)
        if len(batch) < PAGE:
            break
        offset += len(batch)
    return out


def _stat_max_date(url: str, query=_query) -> str:
    payload = query(url, {
        "where": "1=1", "f": "json",
        "outStatistics": json.dumps([{
            "statisticType": "max", "onStatisticField": "date",
            "outStatisticFieldName": "max_date"}]),
    })
    rows = _rows(payload)
    if not rows or not rows[0].get("max_date"):
        raise AdapterError(f"no max date from {url}")
    return str(rows[0]["max_date"])[:10]


def _d(s: str) -> date:
    return date.fromisoformat(str(s)[:10])


def _mean(rows: list[dict], field: str) -> float:
    vals = [r.get(field) for r in rows if isinstance(r.get(field), (int, float))]
    if not vals:
        return 0.0
    return round(sum(vals) / len(vals), 2)


def _sum(rows: list[dict], field: str) -> float:
    return round(sum(r.get(field) or 0 for r in rows), 2)


def _class_block(rows: list[dict], prefix: str, total_field: str, agg) -> dict:
    block = {c: agg(rows, f"{prefix}_{c}") for c in CLASSES}
    block["cargo"] = agg(rows, f"{prefix}_cargo")
    block["total"] = agg(rows, total_field)
    return block


def _window(rows: list[dict], node_max: date) -> tuple[list[dict], dict]:
    start = node_max - timedelta(days=WINDOW_DAYS - 1)
    in_window = [r for r in rows if start <= _d(r["date"]) <= node_max]
    days = len({r["date"] for r in in_window})
    return in_window, {"start": start.isoformat(), "end": node_max.isoformat(),
                       "days_present": days, "window_days": WINDOW_DAYS}


def _graph_ports() -> list[dict]:
    graph = json.loads((REPO_ROOT / "data" / "graph" / "graph.json")
                       .read_text(encoding="utf-8"))
    return [n for n in graph["nodes"] if n.get("kind") == "port"], graph


def _portid_map(idx: NodeIndex, ports_db: list[dict]) -> dict[str, str]:
    """graph port node_id -> PortWatch portid (see module docstring)."""
    out: dict[str, str] = {}
    join_path = REPO_ROOT / "data" / "graph" / "portwatch_join_input.json"
    graph_ports, _ = _graph_ports()
    port_ids = {n["id"] for n in graph_ports}
    for nid, pid in PORTID_OVERRIDES.items():
        if nid in port_ids:
            out[nid] = pid
    if join_path.exists():
        join = json.loads(join_path.read_text(encoding="utf-8"))
        for rec in join.get("records", []):
            if rec.get("node_id") in port_ids and rec.get("portid"):
                out[rec["node_id"]] = rec["portid"]
    by_locode: dict[str, str] = {}
    for p in ports_db:
        code = (p.get("LOCODE") or "").replace(" ", "").upper()
        if code:
            by_locode[code] = p.get("portid")
    for n in graph_ports:
        if n["id"] in out:
            continue
        for alias in [n.get("name", "")] + list(n.get("aliases") or []):
            pid = by_locode.get(str(alias).replace(" ", "").upper())
            if pid:
                out[n["id"]] = pid
                break
    # name matching (ports DB portname -> NodeIndex) for the remainder
    for p in ports_db:
        node_id = idx.by_locode(p.get("LOCODE") or "") or idx.by_name(p.get("portname") or "")
        if node_id in port_ids and node_id not in out and p.get("portid"):
            out[node_id] = p["portid"]
    return out


def fetch_pack(query=_query) -> list[dict]:
    """Build the daily-layer records (pure over the query seam; the
    adapter wraps this with the BaseAdapter snapshot discipline)."""
    prov = provenance(SOURCE, "SNAPSHOT")
    idx = NodeIndex.load()
    graph_ports, graph = _graph_ports()
    port_nodes = {n["id"]: n for n in graph_ports}
    choke_nodes = {n["id"]: n for n in graph["nodes"] if n.get("kind") == "chokepoint"}

    ports_db = _paged(PORTS_URL, {
        "where": "1=1", "outFields": "portid,portname,LOCODE",
        "returnGeometry": "false", "f": "json"}, query)
    portid_of = _portid_map(idx, ports_db)

    records: list[dict] = []
    missing_ports: list[str] = []
    missing_chokes: list[str] = []

    # ---- ports ------------------------------------------------------
    ports_max = _stat_max_date(DAILY_PORTS_URL, query)
    ports_max_d = _d(ports_max)
    global_window = {"start": (ports_max_d - timedelta(days=WINDOW_DAYS - 1)).isoformat(),
                     "end": ports_max_d.isoformat(), "window_days": WINDOW_DAYS}
    graph_portids = sorted({pid for pid in portid_of.values()})
    # Rows over a bounded span ending at the layer max. Each node's
    # window anchors at its own newest served date inside the span;
    # a node whose reporting lags gets a visibly shorter
    # window (days_present is printed in the record), and a node with
    # no rows in the span is named below — never backfilled. (The
    # per-portid max-date statistics alternative was rejected by the
    # service for large IN lists — 400 at 83 portids, verified live
    # — while row queries accept them.)
    rows: list[dict] = []
    if graph_portids:
        fetch_from = (ports_max_d - timedelta(days=FETCH_SPAN_DAYS - 1)).isoformat()
        in_list = ",".join(f"'{p}'" for p in graph_portids)
        rows = _paged(DAILY_PORTS_URL, {
            "where": f"portid IN ({in_list}) AND date >= DATE '{fetch_from}'",
            "outFields": ",".join(_PORT_FIELDS),
            "returnGeometry": "false", "f": "json"}, query)
    by_portid: dict[str, list[dict]] = {}
    for r in rows:
        by_portid.setdefault(r.get("portid"), []).append(r)
    unmatched_ports: list[str] = []
    no_rows_ports: list[str] = []
    for node_id in sorted(port_nodes):
        pid = portid_of.get(node_id)
        if not pid:
            unmatched_ports.append(node_id)
            missing_ports.append(node_id)
            continue
        node_rows = by_portid.get(pid)
        if not node_rows:
            # Mapped, but the layer served no rows in the fetch window:
            # either absent from Daily_Ports_Data or stale beyond
            # STALE_PORT_DAYS behind the layer max — named, never
            # backfilled.
            no_rows_ports.append(node_id)
            missing_ports.append(node_id)
            continue
        node_max = max(_d(r["date"]) for r in node_rows)
        win_rows, window = _window(node_rows, node_max)
        gwin_rows = [r for r in node_rows
                     if _d(global_window["start"]) <= _d(r["date"]) <= ports_max_d]
        head = node_rows[0]
        records.append({
            "record_type": "port_goods",
            "node_id": node_id,
            "portid": pid,
            "portname": head.get("portname"),
            "country": head.get("country"),
            "iso3": head.get("ISO3"),
            "figure_class": GOODS_CLASS_LABEL,
            "window": window,
            "import_tonnes_per_day": _class_block(win_rows, "import", "import", _mean),
            "export_tonnes_per_day": _class_block(win_rows, "export", "export", _mean),
            "portcalls_per_day": _class_block(win_rows, "portcalls", "portcalls", _mean),
            "tier1_window": global_window,
            "tier1_import_tonnes": _class_block(gwin_rows, "import", "import", _sum),
            "tier1_export_tonnes": _class_block(gwin_rows, "export", "export", _sum),
            "dedupe_key": f"portwatch_daily:port:{pid}:{window['end']}",
            "provenance": dict(prov),
        })

    # ---- country totals (Tier-1 denominators) -----------------
    stat_fields = ([f"import_{c}" for c in CLASSES] + ["import_cargo", "import"]
                   + [f"export_{c}" for c in CLASSES] + ["export_cargo", "export"])
    stats = []
    for field in stat_fields:
        stats.append({"statisticType": "sum", "onStatisticField": field,
                      "outStatisticFieldName": f"sum_{field}"})
    totals = _rows(query(DAILY_PORTS_URL, {
        "where": f"date >= DATE '{global_window['start']}' AND date <= DATE '{ports_max}'",
        "groupByFieldsForStatistics": "ISO3,country",
        "outStatistics": json.dumps(stats),
        "f": "json"}))
    port_counts = _paged(DAILY_PORTS_URL, {
        "where": f"date >= DATE '{global_window['start']}' AND date <= DATE '{ports_max}'",
        "groupByFieldsForStatistics": "ISO3,portid",
        "outStatistics": json.dumps([{
            "statisticType": "count", "onStatisticField": "portid",
            "outStatisticFieldName": "n_rows"}]),
        "returnGeometry": "false", "f": "json"}, query, order_by=None)
    ports_per_iso: dict[str, int] = {}
    for r in port_counts:
        if r.get("ISO3"):
            ports_per_iso[r["ISO3"]] = ports_per_iso.get(r["ISO3"], 0) + 1
    graph_ports_per_iso: dict[str, int] = {}
    for rec in records:
        if rec["record_type"] == "port_goods" and rec.get("iso3"):
            graph_ports_per_iso[rec["iso3"]] = graph_ports_per_iso.get(rec["iso3"], 0) + 1
    for t in totals:
        iso = t.get("ISO3")
        if not iso:
            continue
        imp = {c: t.get(f"sum_import_{c}") or 0 for c in CLASSES}
        imp["cargo"] = t.get("sum_import_cargo") or 0
        imp["total"] = t.get("sum_import") or 0
        exp = {c: t.get(f"sum_export_{c}") or 0 for c in CLASSES}
        exp["cargo"] = t.get("sum_export_cargo") or 0
        exp["total"] = t.get("sum_export") or 0
        records.append({
            "record_type": "country_goods",
            "iso3": iso,
            "country": t.get("country"),
            "figure_class": GOODS_CLASS_LABEL,
            "window": global_window,
            "import_tonnes": imp,
            "export_tonnes": exp,
            "ports_in_layer": ports_per_iso.get(iso, 0),
            "graph_ports_in_country": graph_ports_per_iso.get(iso, 0),
            "dedupe_key": f"portwatch_daily:country:{iso}:{ports_max}",
            "provenance": dict(prov),
        })

    # ---- chokepoints --------------------------------------------------
    choke_max = _stat_max_date(DAILY_CHOKE_URL, query)
    choke_max_d = _d(choke_max)
    # Authoritative portname -> portid list for the layer, from a grouped
    # statistics query (the distinct-values query intermittently returned
    # a truncated name list under load — observed once, when the
    # Oresund/Taiwan entries vanished and two chokepoints went missing).
    layer_by_name = {}
    name_stats = _rows(query(DAILY_CHOKE_URL, {
        "where": "1=1", "groupByFieldsForStatistics": "portid,portname",
        "outStatistics": json.dumps([{
            "statisticType": "max", "onStatisticField": "date",
            "outStatisticFieldName": "max_date"}]),
        "f": "json"}))
    for r in name_stats:
        if r.get("portname") and r.get("portid"):
            layer_by_name[r["portname"]] = r["portid"]
    wanted = {}
    for node_id, layer_name in CHOKEPOINT_LAYER_NAMES.items():
        if node_id not in choke_nodes:
            continue
        pid = layer_by_name.get(layer_name)
        if pid:
            wanted[node_id] = (pid, layer_name)
        else:
            missing_chokes.append(node_id)
    for node_id in sorted(choke_nodes):
        if node_id not in CHOKEPOINT_LAYER_NAMES:
            missing_chokes.append(node_id)
    choke_rows: list[dict] = []
    if wanted:
        fetch_from = (choke_max_d - timedelta(days=FETCH_BUFFER_DAYS)).isoformat()
        in_list = ",".join(f"'{pid}'" for pid, _ in wanted.values())
        choke_rows = _paged(DAILY_CHOKE_URL, {
            "where": f"portid IN ({in_list}) AND date >= DATE '{fetch_from}'",
            "outFields": ",".join(_CHOKE_FIELDS),
            "returnGeometry": "false", "f": "json"}, query)
    by_choke: dict[str, list[dict]] = {}
    for r in choke_rows:
        by_choke.setdefault(r.get("portid"), []).append(r)
    for node_id in sorted(wanted):
        pid, layer_name = wanted[node_id]
        node_rows = by_choke.get(pid)
        if not node_rows:
            missing_chokes.append(node_id)
            continue
        node_max = max(_d(r["date"]) for r in node_rows)
        win_rows, window = _window(node_rows, node_max)
        records.append({
            "record_type": "chokepoint_transit",
            "node_id": node_id,
            "portid": pid,
            "portname": layer_name,
            "figure_class": GOODS_CLASS_LABEL,
            "window": window,
            "transits_per_day": _class_block(
                win_rows, "n", "n_total", _mean),
            "transit_tonnes_per_day": _class_block(
                win_rows, "capacity", "capacity", _mean),
            "dedupe_key": f"portwatch_daily:chokepoint:{pid}:{window['end']}",
            "provenance": dict(prov),
        })

    records.append({
        "record_type": "daily_coverage",
        "ports_layer_max_date": ports_max,
        "chokepoints_layer_max_date": choke_max,
        "tier1_window": global_window,
        "graph_ports_total": len(port_nodes),
        "graph_ports_covered": len(port_nodes) - len(missing_ports),
        "ports_missing": sorted(missing_ports),
        "ports_unmatched_no_portid": sorted(unmatched_ports),
        "ports_mapped_no_daily_rows": sorted(no_rows_ports),
        "ports_db_rows": len(ports_db),
        "portids_mapped": len(portid_of),
        "chokepoints_total": len(choke_nodes),
        "chokepoints_missing": sorted(set(missing_chokes)),
        "dedupe_key": f"portwatch_daily:coverage:{ports_max}:{choke_max}",
        "provenance": dict(prov),
    })
    return records


class PortWatchDailyAdapter(BaseAdapter):
    name = "portwatch_daily"
    source = SOURCE
    health_max_age_s = 172800  # stale-LIVE threshold
    snapshot_by_design = True

    def _fetch_live(self) -> FetchResult:
        records = fetch_pack(_query)
        return FetchResult(records=records,
                           provenance=provenance(SOURCE, "SNAPSHOT"))
