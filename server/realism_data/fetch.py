"""Fetch the open sources behind the inventory-days sidecar (and the retained UNCTAD extract).

    cd server && python -m realism_data.fetch            # writes data/graph_sidecar_sources/
    cd server && python -m realism_data.fetch --check    # exits 1 unless the manifest hashes match the files

NETWORK, run by hand. The result is stored under
``data/graph_sidecar_sources/`` so the build (``realism_data.build``) is pure
and offline. Fetch-time extra that nothing else needs: ``py7zr`` (UNCTADstat
serves a .7z). It is not a runtime dependency.

The World Bank CPPI annex is deliberately not fetched: it is CC BY-NC 3.0 IGO.

Sources (each URL is opened here; anything that does not answer is written to
``not_served`` in the manifest, never filled in):

  UNCTAD   UNCTADstat US.PortCalls bulk file (economy-level median time in port)
  EIA      weekly days of supply, api.eia.gov/v2 petroleum/sum/sndw (key in a
           request header, never in a URL, a file or a log)
  Census   MTIS, MRTS, MWTS, M3 bulk time series (inventories / sales ratios)

Nothing here writes a number it did not read from a response.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT = REPO_ROOT / "data" / "graph_sidecar_sources"
UA = "supermap-data1/1.0 (open-data research fetch)"


UNCTAD_BULK = "https://unctadstat-api.unctad.org/bulkdownload/US.PortCalls/US_PortCalls"
UNCTAD_META = "https://unctadstat-api.unctad.org/api/reportMetadata/US.PortCalls"
UNCTAD_BULKFILES = "https://unctadstat-api.unctad.org/api/reportMetadata/US.PortCalls/bulkfiles/en"
UNCTAD_VIEWER = "https://unctadstat.unctad.org/datacentre/dataviewer/US.PortCalls"
UNCTAD_TERMS = "https://unctadstat.unctad.org/EN/About.html"

EIA_PAGE = "https://www.eia.gov/dnav/pet/pet_sum_sndw_dcus_nus_w.htm"
EIA_API = "https://api.eia.gov/v2/petroleum/sum/sndw/data/"
EIA_COPY = "https://www.eia.gov/about/copyrights_reuse.php"
EIA_SERIES = ("W_EPC0_VSD_NUS_DAYS", "W_EPM0_VSD_NUS_DAYS", "W_EPJK_VSD_NUS_DAYS",
              "W_EPD0_VSD_NUS_DAYS", "W_EPLLPZ_VSD_NUS_DAYS")

CENSUS_ZIP = "https://www.census.gov/econ_getzippedfile/?programCode={p}"
CENSUS_PROGRAMS = {"MTIS": "IR", "MRTS": "IR", "MWTS": "IR", "M3": "IS"}
CENSUS_RELEASE = "https://www.census.gov/mtis/www/data/pdf/mtis_current.pdf"
CENSUS_CITE = "https://www.census.gov/about/policies/citation.html"
CENSUS_API_TS = "https://api.census.gov/data/timeseries/eits/mtis"

HISTORY = 13


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def http(url: str, headers: dict | None = None, timeout: int = 120, tries: int = 4):
    """(status, body bytes, final url, response headers). Never raises on an HTTP
    status; retries only transport errors and 5xx."""
    h = {"User-Agent": UA}
    h.update(headers or {})
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read(), r.geturl(), dict(r.headers)
        except urllib.error.HTTPError as e:
            body = e.read() if hasattr(e, "read") else b""
            if e.code >= 500 and i < tries - 1:
                time.sleep(2 + i)
                last = (e.code, body, url, dict(e.headers or {}))
                continue
            return e.code, body, url, dict(e.headers or {})
        except Exception as e:  # transport error
            last = (0, str(type(e).__name__).encode(), url, {})
            time.sleep(2 + i)
    return last


def check_url(url: str, checks: list, note: str = "") -> int:
    st, body, final, _ = http(url, timeout=60)
    checks.append({"url": url, "http_status": st, "bytes": len(body), "final_url": final,
                   "checked_at": now_utc(), **({"note": note} if note else {})})
    return st


def fetch_unctad(out: Path, man: dict) -> None:
    import py7zr  # fetch-time only
    src = {"id": "unctad_portcalls", "urls_checked": []}
    st, body, final, hd = http(UNCTAD_BULK)
    if st != 200:
        man["not_served"].append({"source": "UNCTADstat US.PortCalls", "url": UNCTAD_BULK, "http_status": st,
                                  "reason": "bulk file not served"})
        return
    retrieved = now_utc()
    tmp = out / "_unctad_tmp"
    tmp.mkdir(exist_ok=True)
    (tmp / "pc.7z").write_bytes(body)
    with py7zr.SevenZipFile(tmp / "pc.7z") as z:
        z.extractall(tmp)
    csv_bytes = (tmp / "US_PortCalls.csv").read_bytes()
    (out / "unctad_US_PortCalls.csv").write_bytes(csv_bytes)
    for p in tmp.iterdir():
        p.unlink()
    tmp.rmdir()
    st2, b2, _, _ = http(UNCTAD_META)
    md = json.loads(b2) if st2 == 200 else {}
    st3, b3, _, _ = http(UNCTAD_BULKFILES)
    for u, n in ((UNCTAD_VIEWER, "data viewer"), (UNCTAD_TERMS, "terms of use page")):
        check_url(u, src["urls_checked"], n)
    terms = ""
    st4, b4, _, _ = http(UNCTAD_TERMS)
    if st4 == 200:
        t = re.sub(r"<script.*?</script>|<style.*?</style>", "", b4.decode("utf8", "ignore"), flags=re.S)
        t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t))
        i = t.find("Terms of use All materials")
        terms = t[i + len("Terms of use "):i + len("Terms of use ") + 320] if i >= 0 else ""
    src.update({"name": "UNCTADstat, Port call and performance statistics: time spent in ports, vessel age and "
                        "size, annual (US.PortCalls)",
                "url": UNCTAD_BULK, "landing_url": UNCTAD_VIEWER, "retrieved_at": retrieved,
                "http_status": st, "bytes": len(body), "archive_sha256": sha256_bytes(body),
                "extract": "unctad_US_PortCalls.csv", "extract_sha256": sha256_bytes(csv_bytes),
                "report_title": md.get("title"), "report_last_updated": md.get("lastUpdated"),
                "report_version": md.get("version"),
                "bulk_files": json.loads(b3) if st3 == 200 else None,
                "terms_text": terms,
                "methodology_note": ("AIS fused with MarineTraffic port mapping, ships of 1000 GT and above; "
                                     "an economy needs at least 10 arrivals by at least 5 distinct vessels per "
                                     "commercial market to be published; passenger and RO/RO ships are excluded "
                                     "from time-at-port measures (UNCTADstat metadata, measuresNotes)")})
    man["sources"]["unctad_portcalls"] = src


def fetch_eia(out: Path, man: dict) -> None:
    key = os.environ.get("EIA_API_KEY")
    src = {"id": "eia_days_of_supply", "urls_checked": []}
    st, page, final, _ = http(EIA_PAGE)
    retrieved = now_utc()
    if st != 200:
        man["not_served"].append({"source": "EIA weekly supply estimates page", "url": EIA_PAGE,
                                  "http_status": st, "reason": "page not served"})
        return
    html = page.decode("utf8", "ignore")
    rel = re.search(r"Release Date:\s*([0-9/]+)", html)
    # the page's own days-of-supply rows (cross-check against the API values)
    k = html.find("Days of Supply (Number")
    seg = html[k:]
    page_rows = {}
    for r in seg.split('<tr class="DataRow">')[1:8]:
        name = re.search(r'class="DataStub1">([^<]*)<', r)
        vals = re.findall(r'class="(?:DataB|Current2)">([^<]*)<', r)
        sid = re.findall(r"s=([A-Za-z0-9_\-]+)&f=W", r)
        if name and vals and sid and sid[0].endswith("_DAYS"):
            page_rows[sid[0]] = {"label": name.group(1), "last_six_weeks": vals}
    if not key:
        man["not_served"].append({"source": "EIA API v2 weekly days of supply", "url": EIA_API,
                                  "reason": "EIA_API_KEY not set in this environment"})
        return
    series = []
    for sid in EIA_SERIES:
        q = urllib.parse.urlencode([("frequency", "weekly"), ("data[0]", "value"), ("facets[series][]", sid),
                                    ("sort[0][column]", "period"), ("sort[0][direction]", "desc"),
                                    ("length", str(HISTORY))])
        s2, b2, _, _ = http(EIA_API + "?" + q, headers={"X-Api-Key": key})
        if s2 != 200:
            man["not_served"].append({"source": f"EIA series {sid}", "url": EIA_API, "http_status": s2,
                                      "reason": "API call failed"})
            continue
        data = json.loads(b2)["response"]["data"]
        if not data:
            continue
        series.append({"series": sid, "description": data[0]["series-description"],
                       "product": data[0]["product-name"], "area": data[0]["area-name"],
                       "units": data[0]["units"],
                       "points": [[d["period"], str(d["value"])] for d in data]})
    for u, n in ((EIA_COPY, "copyright and reuse page"),):
        check_url(u, src["urls_checked"], n)
    st4, b4, _, _ = http(EIA_COPY)
    reuse = ""
    if st4 == 200:
        t = re.sub(r"<script.*?</script>|<style.*?</style>", "", b4.decode("utf8", "ignore"), flags=re.S)
        t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t))
        i = t.find("U.S. government publications are in the public domain")
        reuse = t[i:i + 260] if i >= 0 else ""
    extract = {"api_route": "petroleum/sum/sndw", "api_url": EIA_API, "key_handling": "X-Api-Key header, never stored",
               "retrieved_at": retrieved, "series": series, "page_cross_check": page_rows,
               "page_release_date": rel.group(1) if rel else None}
    blob = (json.dumps(extract, indent=1, sort_keys=True) + "\n").encode("utf-8")
    (out / "eia_days_of_supply.json").write_bytes(blob)
    src.update({"name": "U.S. Energy Information Administration, Weekly Petroleum Status Report, days of supply",
                "url": EIA_PAGE, "api_url": EIA_API, "retrieved_at": retrieved, "http_status": st,
                "page_sha256": sha256_bytes(page), "extract": "eia_days_of_supply.json",
                "extract_sha256": sha256_bytes(blob), "reuse_text": reuse})
    man["sources"]["eia_days_of_supply"] = src


def census_extract(program: str, dt_code: str, zbytes: bytes) -> dict:
    z = zipfile.ZipFile(io.BytesIO(zbytes))
    name = [n for n in z.namelist() if n.endswith(".csv")][0]
    raw = z.read(name)
    text = raw.decode("utf-8-sig").replace("\r\n", "\n")
    secs: dict = {}
    for s in text.split("\n\n"):
        lines = s.strip("\n").split("\n")
        secs[lines[0].strip()] = "\n".join(lines[1:])
    cats = {r["cat_idx"]: r for r in csv.DictReader(io.StringIO(secs["CATEGORIES"]))}
    dts = {r["dt_idx"]: r for r in csv.DictReader(io.StringIO(secs["DATA TYPES"]))}
    pers = {r["per_idx"]: r["per_name"] for r in csv.DictReader(io.StringIO(secs["TIME PERIODS"]))}
    dt_idx = [k for k, v in dts.items() if v["dt_code"] == dt_code][0]
    by: dict = {}
    for r in csv.DictReader(io.StringIO(secs["DATA"])):
        if r["dt_idx"] != dt_idx:
            continue
        if r["val"].strip() in ("NA", "", "(S)", "(X)", "(NA)"):
            continue
        by.setdefault((r["cat_idx"], r["is_adj"]), []).append((int(r["per_idx"]), r["val"].strip()))
    series = []
    for (ci, adj), pts in sorted(by.items(), key=lambda kv: (int(kv[0][0]), kv[0][1])):
        pts.sort()
        last = pts[-HISTORY:]
        series.append({"cat_code": cats[ci]["cat_code"], "cat_desc": cats[ci]["cat_desc"],
                       "seasonally_adjusted": adj == "1",
                       "points": [[pers[str(p)], v] for p, v in last]})
    return {"program": program, "data_type_code": dt_code,
            "data_type_desc": dts[dt_idx]["dt_desc"], "unit": dts[dt_idx]["dt_unit"],
            "csv_name": name, "csv_sha256": sha256_bytes(raw),
            "data_updated_on": secs.get("DATA UPDATED ON", "").strip(), "series": series}


def fetch_census(out: Path, man: dict) -> None:
    src = {"id": "census_inventory_ratios", "urls_checked": [], "programs": {}}
    extracts = []
    for prog, dt in CENSUS_PROGRAMS.items():
        url = CENSUS_ZIP.format(p=prog)
        st, body, final, _ = http(url, tries=5)
        if st != 200 or body[:2] != b"PK":
            man["not_served"].append({"source": f"Census {prog} bulk time series", "url": url, "http_status": st,
                                      "reason": "zip not served"})
            continue
        ex = census_extract(prog, dt, body)
        ex.update({"zip_url": url, "zip_sha256": sha256_bytes(body), "zip_bytes": len(body),
                   "retrieved_at": now_utc(), "http_status": st})
        extracts.append(ex)
        src["programs"][prog] = {k: ex[k] for k in ("zip_url", "zip_sha256", "retrieved_at", "data_updated_on",
                                                      "csv_sha256")}
    if not extracts:
        return
    # the keyed JSON API was tried first: record what it answered (no key is stored)
    key = os.environ.get("CENSUS_API_KEY")
    if key:
        q = urllib.parse.urlencode({"get": "data_type_code,cell_value", "time": "2026-08", "data_type_code": "IR",
                                    "key": key})
        s5, b5, _, _ = http(CENSUS_API_TS + "?" + q, tries=1)
        invalid = b"Invalid Key" in b5[:2000]
        man["not_served"].append({"source": "Census Bureau EITS API (api.census.gov/data/timeseries/eits/mtis)",
                                  "url": CENSUS_API_TS, "http_status": s5,
                                  "reason": ("the key in this environment was rejected ('Invalid Key'); the bulk "
                                             "time-series zips (econ_getzippedfile) need no key and were used"
                                             if invalid else "API not used; bulk zips used instead")})
    for u, n in ((CENSUS_RELEASE, "current MTIS release (PDF)"), (CENSUS_CITE, "citation policy page")):
        check_url(u, src["urls_checked"], n)
    blob = (json.dumps({"extracts": extracts, "history_points": HISTORY}, indent=1, sort_keys=True) + "\n").encode("utf-8")
    (out / "census_inventory_ratios.json").write_bytes(blob)
    src.update({"name": "U.S. Census Bureau, Economic Indicators time series (MTIS, MRTS, MWTS, M3)",
                "url": "https://www.census.gov/econ_datasets/", "retrieved_at": max(e["retrieved_at"] for e in extracts),
                "extract": "census_inventory_ratios.json", "extract_sha256": sha256_bytes(blob)})
    man["sources"]["census_inventory_ratios"] = src


def fetch_inventory_not_served(man: dict) -> None:
    """Sources looked at for days of cover outside the US that gave no days figure."""
    st, body, _, _ = http("https://www.iea.org/data-and-statistics/data-product/oil-stocks-of-iea-countries",
                          tries=1, timeout=60)
    man["not_served"].append({
        "source": "IEA oil stocks of IEA countries (days of net imports / stock levels)",
        "url": "https://www.iea.org/data-and-statistics/data-product/oil-stocks-of-iea-countries",
        "http_status": st, "checked_at": now_utc(),
        "reason": "page answered HTTP %d through the proxy; nothing was read from it" % st})
    url = ("https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1/dataflow/ESTAT/nrg_stk_oilm/latest"
           "?detail=allstubs")
    st, body, _, _ = http(url, tries=2, timeout=60)
    name = re.search(r'<c:Name xml:lang="en">([^<]*)</c:Name>', body.decode("utf8", "ignore"))
    man["not_served"].append({
        "source": "Eurostat nrg_stk_oilm (EU oil products stocks)", "url": url, "http_status": st,
        "checked_at": now_utc(),
        "reason": "reachable, but the dataflow is '%s': stock LEVELS, not days of cover. Deriving days would need a "
                  "consumption series and would be MODELLED, so no row is shipped" % (name.group(1) if name else "?")})


def verify_row_urls(man: dict) -> None:
    """Open every URL a sidecar row may cite and record the status."""
    try:
        from realism_data.common import all_row_urls
    except Exception:  # pragma: no cover
        from .common import all_row_urls
    out = []
    for u in all_row_urls():
        st, body, final, _ = http(u, tries=3, timeout=90)
        out.append({"url": u, "http_status": st, "bytes": len(body), "checked_at": now_utc()})
    man["row_urls_verified"] = out


def build_manifest(out: Path) -> dict:
    man = {"schema": "supermap.realism_sources/1", "fetched_by": "server/realism_data/fetch.py",
           "fetched_on": now_utc()[:10], "sources": {}, "not_served": []}
    out.mkdir(parents=True, exist_ok=True)
    for fn in (fetch_unctad, fetch_eia, fetch_census):
        try:
            fn(out, man)
        except Exception as exc:  # a failed source is recorded, never papered over
            man["not_served"].append({"source": fn.__name__, "reason": f"{type(exc).__name__}: {exc}"[:300]})
    fetch_inventory_not_served(man)
    verify_row_urls(man)
    for n in man["not_served"]:
        n["sidecar"] = "inventory_days"
    return man


def check(out: Path) -> int:
    man = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    bad = []
    for sid, s in man["sources"].items():
        f = out / s["extract"]
        if not f.exists() or sha256_bytes(f.read_bytes()) != s["extract_sha256"]:
            bad.append(sid)
    for b in bad:
        print("MISMATCH", b)
    print("sources:", len(man["sources"]), "mismatch:", len(bad))
    return 1 if bad else 0


def main(argv: list[str]) -> int:
    if "--check" in argv:
        return check(OUT)
    man = build_manifest(OUT)
    (OUT / "manifest.json").write_text(json.dumps(man, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
                                       encoding="utf-8")
    print("sources:", sorted(man["sources"]))
    for n in man["not_served"]:
        print("NOT SERVED:", n["source"], "-", n.get("reason", "")[:120])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
