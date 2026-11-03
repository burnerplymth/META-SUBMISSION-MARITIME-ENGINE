"""GET /research/export: flat tables, a manifest and the label, as CSV, XLSX or JSON.

Reuses the repo's export pipeline: the single formula guard (``reports.exports.neutralise``) is applied
to every string cell, and the XLSX writer is ``reports.xlsx``. The guard is also applied to the
manifest and label lines of the CSV.
"""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone

from . import datasets as D
from .util import LABEL_ASSEMBLE, ResearchError, sha256_hex

try:  # server/ on sys.path (house convention)
    from reports.exports import neutralise
    from reports import xlsx as _xlsx
except Exception:  # pragma: no cover - alternate import root
    try:
        from server.reports.exports import neutralise  # type: ignore
        from server.reports import xlsx as _xlsx  # type: ignore
    except Exception:
        _xlsx = None

        def neutralise(value):          # same rule as reports.exports.neutralise
            if isinstance(value, str) and (value.lstrip()[:1] in ("=", "+", "-", "@") or value[:1] in ("\t", "\r")):
                return "'" + value
            return value


def tables(asm: dict) -> dict[str, tuple[list[str], list[list]]]:
    t = asm["timeline"]["t"]
    keys = sorted(asm["timeline"]["series"])
    ser = asm["timeline"]["series"]
    head = ["date"] + [f"{k} [{ser[k]['unit']}]" for k in keys]
    rows = []
    for i, d in enumerate(t):
        rows.append([d] + [(ser[k]["values"][i] if ser[k]["values"] else None) for k in keys])
    events = (["id", "t", "t_end", "category", "title", "place", "publisher", "url", "source_date", "origin"],
              [[e["id"], e["t"], e.get("t_end"), e["category"], e["title"], e.get("place"),
                e["source"]["publisher"], e["source"]["url"], e["source"]["date"], e.get("origin")]
               for e in asm["events"]])
    scenes = (["t", "place", "source", "scene_id", "cloud_pct", "nodata_pct", "usable", "sam_prompt",
               "sam_count", "sam_recorded"],
              [[s["t"], s["place"], s["source"], s["scene_id"], s["cloud_pct"], s["nodata_pct"], s["usable"],
                (s["sam"] or {}).get("prompt"), (s["sam"] or {}).get("count"), (s["sam"] or {}).get("recorded")]
               for s in asm["scenes"]])
    trk = (["vessel_id_hash", "place", "t", "lon", "lat"],
           [[tr["vessel_id_hash"], tr.get("place"), p["t"], p["lon"], p["lat"]]
            for tr in asm["tracks"] for p in tr["points"]])
    notes = (["chip", "dataset", "place", "text"],
             [[n["chip"], n.get("dataset"), n.get("place"), n["text"]] for n in asm["coverage_notes"]])
    return {"timeline": (head, rows), "events": events, "scenes": scenes, "tracks": trk,
            "coverage_notes": notes}


def manifest(asm: dict, tabs: dict) -> dict:
    used = sorted({v["dataset"] for v in asm["timeline"]["series"].values()})
    attrib = []
    for d in used:
        spec = D.SERIES.get(d) or D.NOT_SERVED.get(d)
        if spec:
            attrib.append({"dataset": d, "source": spec["source"], "licence": spec["licence"],
                           "attribution": spec["attribution"]})
    if asm["scenes"]:
        for s in D.SCENES.values():
            attrib.append({"dataset": "scenes", "source": s["source"], "licence": s["licence"],
                           "attribution": s["attribution"]})
            break
    if asm["tracks"]:
        attrib.append({"dataset": "tracks_ais", "source": D.TRACKS["source"], "licence": D.TRACKS["licence"],
                       "attribution": D.TRACKS["attribution"]})
    return {"label": LABEL_ASSEMBLE, "assembly_id": asm["assembly_id"], "window": asm["window"],
            "places": [p["id"] for p in asm["places"]], "request": asm.get("request"),
            "fetch_mode": asm.get("fetch_mode"),
            "tables": {k: {"rows": len(v[1]), "columns": v[0]} for k, v in tabs.items()},
            "sources": attrib,
            "notes": "Descriptive data export. Weather is modelled reanalysis. Not a forecast."}


def _guard(row):
    return [neutralise(v) for v in row]


def to_csv(asm: dict) -> bytes:
    tabs = tables(asm)
    man = manifest(asm, tabs)
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\n")
    for line in (f"label: {man['label']}", f"assembly_id: {man['assembly_id']}",
                 f"window: {man['window']['from']} to {man['window']['to']}",
                 f"places: {', '.join(man['places'])}", "table: timeline (daily; blank = no observation)"):
        w.writerow([neutralise("# " + line)])
    for s in man["sources"]:
        w.writerow([neutralise("# source: " + s["attribution"] + " | licence: " + s["licence"])])
    head, rows = tabs["timeline"]
    w.writerow(_guard(head))
    for r in rows:
        w.writerow(_guard(r))
    return buf.getvalue().encode("utf-8")


def to_json(asm: dict) -> bytes:
    tabs = tables(asm)
    man = manifest(asm, tabs)
    doc = {"label": man["label"], "manifest": man,
           "tables": {k: {"columns": v[0], "rows": [_guard(r) for r in v[1]]} for k, v in tabs.items()}}
    return json.dumps(doc, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def to_xlsx(asm: dict) -> bytes:
    if _xlsx is None:
        raise ResearchError("xlsx_unavailable", "the workbook writer is not available", 503)
    tabs = tables(asm)
    man = manifest(asm, tabs)
    cover = [[(neutralise("Research export"), "header")], [(neutralise(man["label"]), None)],
             [(neutralise("assembly_id: " + man["assembly_id"]), None)],
             [(neutralise(f"window: {man['window']['from']} to {man['window']['to']}"), None)],
             [(neutralise("places: " + ", ".join(man["places"])), None)]]
    for s in man["sources"]:
        cover.append([(neutralise(s["attribution"] + " | licence: " + s["licence"]), None)])
    sheets = [_xlsx.text_sheet("Manifest", cover, [90])]
    for name, (head, rows) in tabs.items():
        sheets.append(_xlsx.table_sheet(name, [neutralise(h) for h in head], [_guard(r) for r in rows]))
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return _xlsx.write_workbook(sheets, created, "SupermAP research export")


def content_digest(data: bytes) -> str:
    return sha256_hex(data)
