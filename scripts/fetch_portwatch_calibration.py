"""Fetch the PortWatch daily series that calibrate ENGINE REV 2's peak service
rate mu_max (engine rev 2 calibration).

Fixed window: 2022-07-01 .. 2023-10-29 inclusive (473 days). It ends
the day before the earliest 2023 backtest episode starts (Panama 2023-10-30) and
starts after the 2022 Shanghai lockdown. Output: the raw daily counts, compact,
in data/calibration/portwatch_daily_window.json (so the derivation is
reproducible offline). Stdlib only. Run: python scripts/fetch_portwatch_calibration.py
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BASE = "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services"
CHOKE = BASE + "/Daily_Chokepoints_Data/FeatureServer/0/query"
PORTS = BASE + "/Daily_Ports_Data/FeatureServer/0/query"
START, END = "2022-07-01", "2023-10-29"
OUT = REPO / "data" / "calibration" / "portwatch_daily_window.json"

# graph chokepoint id -> PortWatch portname (portid resolved by name)
CHOKE_NAMES = {
    "chk_suez": "Suez Canal", "chk_panama": "Panama Canal",
    "chk_babelmandeb": "Bab el-Mandeb Strait", "chk_malacca": "Malacca Strait",
    "chk_hormuz": "Strait of Hormuz", "chk_capeofgoodhope": "Cape of Good Hope",
    "chk_gibraltar": "Gibraltar Strait", "chk_dover": "Dover Strait",
    "chk_danish": "Oresund Strait", "chk_taiwan": "Taiwan Strait",
    "chk_turkish": "Bosporus Strait", "chk_mozambique": "Mozambique Channel",
}


EXTRA_PORTS = [
    ("sea_bremerhaven", "port168", "Bremerhaven"), ("sea_busan", "port1065", "Busan"),
    ("sea_caimep", "port903", "Cai Mep"), ("sea_colombo", "port254", "Colombo"),
    ("sea_guangzhou", "port425", "Guangzhou (Nansha)"), ("sea_haiphong", "port434", "Hai Phong"),
    ("sea_hamburg", "port446", "Hamburg"), ("sea_hochiminh", "port1291", "Saigon"),
    ("sea_hongkong", "port474", "Hong Kong"), ("sea_houston", "port481", "Houston (US-TX)"),
    ("sea_kaohsiung", "port541", "Kaohsiung"), ("sea_longbeach", "port664", "Los Angeles-Long Beach"),
    ("sea_losangeles", "port664", "Los Angeles-Long Beach"), ("sea_mundra", "port777", "Mundra"),
    ("sea_nhavasheva", "port776", "Mumbai-Jawaharlal Nehru (Nhava Sheva)"),
    ("sea_portklang", "port960", "Port Klang"), ("sea_qingdao", "port1069", "Qingdao Port"),
    ("sea_salalah", "port746", "Salalah"), ("sea_singapore", "port1201", "Singapore"),
    ("sea_taicang", "port1253", "Suzhou (Taicang)"), ("sea_tanjungpelepas", "port1269", "Tanjung Pelepas"),
    ("sea_tianjin", "port1297", "Tianjin Xin Gang"),
]


def post(url: str, form: dict) -> dict:
    data = urllib.parse.urlencode(form).encode()
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=60) as r:
                return json.loads(r.read())
        except Exception as e:  # noqa: BLE001
            if attempt == 3:
                raise
            time.sleep(2 * (attempt + 1))
    raise RuntimeError("unreachable")


def fetch_series(url, where, fields):
    rows, off = [], 0
    while True:
        j = post(url, {"f": "json", "where": where, "outFields": ",".join(fields),
                       "orderByFields": "date ASC", "returnGeometry": "false",
                       "resultOffset": str(off), "resultRecordCount": "1000"})
        feats = j.get("features", [])
        rows += [f["attributes"] for f in feats]
        if len(feats) < 1000:
            return rows
        off += 1000


def dense(rows, field):
    """Daily vector START..END; None for a missing day."""
    by = {}
    for r in rows:
        d = r["date"]
        if isinstance(d, (int, float)):
            d = datetime.fromtimestamp(d / 1000, tz=timezone.utc).date().isoformat()
        by[str(d)[:10]] = r.get(field)
    d0, d1 = date.fromisoformat(START), date.fromisoformat(END)
    out = []
    for i in range((d1 - d0).days + 1):
        out.append(by.get(date.fromordinal(d0.toordinal() + i).isoformat()))
    return out


def main() -> int:
    graph = json.loads((REPO / "data/graph/graph.json").read_text())
    core_ports = {n["id"] for n in graph["nodes"] if n.get("tier", "core") == "core" and n["kind"] == "port"}
    join = json.loads((REPO / "data/graph/portwatch_join_input.json").read_text())["records"]
    pid_of = {}
    for r in join:
        if r["node_id"] in core_ports:
            pid_of.setdefault(r["node_id"], (r["portid"], r["portname"]))
    # core ports the join file lacks, matched against data/graph/portwatch_registry_pin.json
    # by LOCODE / name (LA and Long Beach share PortWatch's combined record port664)
    for node, pid, nm in EXTRA_PORTS:
        pid_of.setdefault(node, (pid, nm))
    result = {"window": {"start": START, "end": END}, "places": {}}
    # chokepoints: resolve ids by name from one day
    names = post(CHOKE, {"f": "json", "where": "date = DATE '2023-09-01'",
                         "outFields": "portid,portname", "returnGeometry": "false"})
    cid = {f["attributes"]["portname"]: f["attributes"]["portid"] for f in names["features"]}
    for node, nm in CHOKE_NAMES.items():
        if nm not in cid:
            result["places"][node] = {"error": f"portname {nm!r} not served"}
            continue
        where = f"portid = '{cid[nm]}' AND date >= DATE '{START}' AND date <= DATE '{END}'"
        rows = fetch_series(CHOKE, where, ["date", "portid", "n_total"])
        result["places"][node] = {"layer": "chokepoint", "portid": cid[nm], "portname": nm,
                                  "metric": "n_total", "counts": dense(rows, "n_total")}
        print(node, len(rows), file=sys.stderr)
    for node, (pid, nm) in sorted(pid_of.items()):
        where = f"portid = '{pid}' AND date >= DATE '{START}' AND date <= DATE '{END}'"
        rows = fetch_series(PORTS, where, ["date", "portid", "portcalls", "portcalls_container"])
        result["places"][node] = {"layer": "port", "portid": pid, "portname": nm,
                                  "metric": "portcalls_container|portcalls",
                                  "container": dense(rows, "portcalls_container"),
                                  "all": dense(rows, "portcalls")}
        print(node, len(rows), file=sys.stderr)
        time.sleep(0.2)
    result["retrieved_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    result["source"] = "IMF PortWatch daily layers (" + CHOKE + " ; " + PORTS + ")"
    body = json.dumps(result, sort_keys=True, separators=(",", ":"))
    result["payload_sha256"] = hashlib.sha256(body.encode()).hexdigest()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")
    print("wrote", OUT, len(body), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
