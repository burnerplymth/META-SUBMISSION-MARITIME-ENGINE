"""CSV and XLSX files of an exposure result, written with the exports pipeline (reports.exports / reports.xlsx).

Stateless: the file is built from the request and returned; nothing is written to disk. Every text cell
goes through the pipeline's formula guard (``reports.exports.neutralise``). The CSV carries the label on
every row (a provenance column); the workbook carries the label, the scenario and the assumptions on a
Summary sheet. No file holds a total row across shipments in the row table: the summary figures sit on
the Summary sheet under their own names.
"""
from __future__ import annotations

from .evaluate import LABEL, VALUE_LABEL

CREATED = "2026-01-01T00:00:00Z"        # pinned: the file is a function of its inputs, not of a clock
COLUMNS = [
    ("Shipment", None), ("Origin as given", None), ("Destination as given", None), ("Mode", None),
    ("Lane", None), ("Status", None), ("Baseline low", "d"), ("Baseline median", "d"), ("Baseline high", "d"),
    ("Added low", "d"), ("Added median", "d"), ("Added high", "d"), ("Value", "USD"),
    ("Value in affected shipments", "USD"), ("Route", None), ("Label", None),
]
STATUS_WORDS = {"unaffected": "Unaffected", "delayed": "Delayed", "rerouted": "Rerouted",
                "not_served": "Not served", "unresolved": "Unresolved"}


def _guards():
    try:
        from reports.exports import neutralise, to_csv_bytes
        from reports import xlsx
    except Exception:  # pragma: no cover - imported as server.* from the repo root
        from server.reports.exports import neutralise, to_csv_bytes  # type: ignore
        from server.reports import xlsx  # type: ignore
    return neutralise, to_csv_bytes, xlsx


def _rows(req: dict, doc: dict) -> list[list]:
    by_id = {s["id"]: s for s in req["shipments"]}
    lanes = {b["lane_key"]: b for b in doc["by_lane"]}
    rows = []
    for s in doc["shipments"]:
        src = by_id[s["id"]]
        lane = lanes.get(s["lane_key"])
        lane_txt = f"{lane['origin_name']} to {lane['destination_name']}" if lane else ""
        b, a = s.get("baseline_days") or {}, s.get("added_days") or {}
        rows.append([
            s["id"], src["origin"], src["destination"], src["mode"], lane_txt, STATUS_WORDS.get(s["status"], s["status"]),
            b.get("low"), b.get("median"), b.get("high"), a.get("low"), a.get("median"), a.get("high"),
            s.get("value_usd"), s.get("value_in_affected_usd") if s["status"] in ("delayed", "rerouted") else None,
            s.get("route_label") or "", LABEL,
        ])
    return rows


def build_csv(req: dict, doc: dict) -> bytes:
    _n, to_csv_bytes, _x = _guards()
    return to_csv_bytes(COLUMNS, _rows(req, doc))


def build_xlsx(req: dict, doc: dict) -> bytes:
    neutralise, _c, xlsx = _guards()
    from reports.exports import header_of  # noqa: E402  (same package as the guard above)
    rows = [[neutralise(v) for v in r] for r in _rows(req, doc)]
    sc, sm = doc["scenario"], doc["summary"]
    cover = [
        [("Shipment exposure", "title")],
        [(LABEL, "muted")],
        [],
        [("Scenario", "section"), (neutralise(sc.get("label") or ""), None)],
        [("Shipments", "section"), (sm["n"], "int")],
        [("Affected", "section"), (sm["n_affected"], "int")],
        [(VALUE_LABEL, "section"), (sm["value_in_affected_usd"], "float")],
        [("Added days, median range", "section"),
         (f"{sm['added_days_median_range']['low']:g} to {sm['added_days_median_range']['high']:g} d"
          if sm.get("added_days_median_range") else "", None)],
        [],
        [("Assumptions", "section")],
    ] + [[(a, "wrap")] for a in sm["assumptions"]]
    lane_hdr = ["Origin", "Destination", "Mode", "Shipments", "Status", "Baseline median [d]", "Added low [d]",
                "Added median [d]", "Added high [d]", "Route", "Notes"]
    lane_rows = []
    for b in doc["by_lane"]:
        a, bl = b.get("added_days") or {}, b.get("baseline_days") or {}
        lane_rows.append([neutralise(b["origin_name"]), neutralise(b["destination_name"]), b["mode"], b["n_shipments"],
                          STATUS_WORDS.get(b["status"], b["status"]), bl.get("median"), a.get("low"), a.get("median"),
                          a.get("high"), neutralise(b.get("route_label") or ""),
                          "; ".join(neutralise(c) for c in b.get("notes_chips", []))])
    sheets = [xlsx.text_sheet("Summary", cover, widths=[34, 60]),
              xlsx.table_sheet("Shipments", header_of(COLUMNS), rows),
              xlsx.table_sheet("Lanes", lane_hdr, lane_rows)]
    return xlsx.write_workbook(sheets, CREATED, title="Shipment exposure")
