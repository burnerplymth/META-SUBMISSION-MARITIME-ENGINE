#!/usr/bin/env python3
"""Extract a SMALL track sample from one NOAA / BOEM MarineCadastre daily AIS zip.

Source: US Coast Guard AIS via MarineCadastre.gov (https://marinecadastre.gov/ais/), public domain
("AIS data ... are in the public domain", MarineCadastre data-use statement). The daily zip is
~320 MB; this script streams the CSV inside it and never writes the CSV to disk.

It keeps one bounding box, cargo and tanker vessels (AIS ship type 70-89), one position per vessel
per ``--step-min`` minutes, and replaces the MMSI by a salted hash (``vessel_id_hash``). The
output is the ``tracks`` block that ``scripts/build_research_cache.py`` bundles.

    python scripts/research_ais_extract.py --zip AIS_2021_11_10.zip --out tracks.json \
        --bbox -118.45 33.45 -118.05 33.78 --step-min 20 --place sea_losangeles
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import zipfile

SALT = "supermap-research-v1"


def vessel_hash(mmsi: str) -> str:
    return "v_" + hashlib.sha256((SALT + str(mmsi)).encode()).hexdigest()[:8]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--bbox", nargs=4, type=float, required=True,
                    metavar=("MINLON", "MINLAT", "MAXLON", "MAXLAT"))
    ap.add_argument("--step-min", type=int, default=20)
    ap.add_argument("--place", required=True)
    ap.add_argument("--min-points", type=int, default=6)
    a = ap.parse_args()
    x0, y0, x1, y1 = a.bbox
    last: dict[str, int] = {}
    pts: dict[str, list] = {}
    kinds: dict[str, str] = {}
    n_rows = 0
    with zipfile.ZipFile(a.zip) as z:
        name = [i.filename for i in z.infolist() if i.filename.lower().endswith(".csv")][0]
        with z.open(name) as raw:
            rd = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8", newline=""))
            for row in rd:
                n_rows += 1
                try:
                    lat, lon = float(row["LAT"]), float(row["LON"])
                except (ValueError, KeyError):
                    continue
                if not (x0 <= lon <= x1 and y0 <= lat <= y1):
                    continue
                try:
                    vt = int(float(row.get("VesselType") or 0))
                except ValueError:
                    vt = 0
                if not 70 <= vt <= 89:
                    continue
                ts = row["BaseDateTime"]            # 2021-11-10T00:00:07
                minute = int(ts[11:13]) * 60 + int(ts[14:16])
                mm = row["MMSI"]
                if mm in last and minute - last[mm] < a.step_min:
                    continue
                last[mm] = minute
                pts.setdefault(mm, []).append({"t": ts + "Z", "lon": round(lon, 4), "lat": round(lat, 4)})
                kinds[mm] = "cargo" if 70 <= vt <= 79 else "tanker"
    tracks = []
    for mm in sorted(pts):
        p = pts[mm]
        if len(p) < a.min_points:
            continue
        p.sort(key=lambda r: r["t"])
        dur = (int(p[-1]["t"][11:13]) * 60 + int(p[-1]["t"][14:16])
               - int(p[0]["t"][11:13]) * 60 - int(p[0]["t"][14:16])) / 60.0
        tracks.append({"vessel_id_hash": vessel_hash(mm), "points": p, "origin": None,
                       "destination": None, "duration_h": round(dur, 2), "class": kinds[mm]})
    doc = {"place": a.place, "bbox": a.bbox, "step_min": a.step_min, "rows_scanned": n_rows,
           "vessel_filter": "AIS ship type 70-89 (cargo, tanker)", "tracks": tracks}
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, separators=(",", ":"))
    print(f"rows scanned {n_rows}; tracks kept {len(tracks)}; points {sum(len(t['points']) for t in tracks)}")


if __name__ == "__main__":
    main()
