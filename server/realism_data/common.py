"""Shared pieces of the inventory-days sidecar.

The sidecar is DATA ONLY. It is opt-in, adds no id to the graph, overrides
no fixed number, and nothing in server/engine, server/engine2,
server/sim or server/portsim reads it.

Label vocabulary of a row's ``figure_label``:
  SOURCED   a number read from a response the builder fetched (the response is
            stored under data/graph_sidecar_sources/ and hashed)
  MODELLED  anything else; it must say so. No MODELLED row ships in these files:
            the graph's own inventory_days defaults are described in a block,
            labelled MODELLED, not as rows.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_GRAPH = REPO_ROOT / "data" / "graph"
SOURCES_DIR = REPO_ROOT / "data" / "graph_sidecar_sources"

SCHEMA_INVENTORY = "supermap.inventory_days/1"
GRAPH_PREFIX = "9743a288fb5d670d"

LABELS = ("SOURCED", "MODELLED")
# a row's source URL must be https on one of these publishers (suffix match)
URL_HOSTS = ("unctad.org", "eia.gov", "census.gov")
LICENCE_HOSTS = ("creativecommons.org",)
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

# --- the URLs rows may cite (single source of truth; fetch.py verifies each) ---
UNCTAD_DATA_URL = "https://unctadstat-api.unctad.org/bulkdownload/US.PortCalls/US_PortCalls"
UNCTAD_LANDING_URL = "https://unctadstat.unctad.org/datacentre/dataviewer/US.PortCalls"
EIA_PAGE_URL = "https://www.eia.gov/dnav/pet/pet_sum_sndw_dcus_nus_w.htm"
EIA_HIST_URL = "https://www.eia.gov/dnav/pet/hist/LeafHandler.ashx?n=PET&s={sid}&f=W"
CENSUS_ZIP_URL = "https://www.census.gov/econ_getzippedfile/?programCode={p}"
CENSUS_PAGE_URL = "https://www.census.gov/econ/currentdata/?programCode={p}"
CENSUS_PROGRAMS = ("MTIS", "MRTS", "MWTS", "M3")
EIA_SERIES = ("W_EPC0_VSD_NUS_DAYS", "W_EPM0_VSD_NUS_DAYS", "W_EPJK_VSD_NUS_DAYS",
              "W_EPD0_VSD_NUS_DAYS", "W_EPLLPZ_VSD_NUS_DAYS")


def all_row_urls() -> list[str]:
    urls = [UNCTAD_DATA_URL, UNCTAD_LANDING_URL, EIA_PAGE_URL]
    urls += [EIA_HIST_URL.format(sid=s) for s in EIA_SERIES]
    for p in CENSUS_PROGRAMS:
        urls += [CENSUS_ZIP_URL.format(p=p), CENSUS_PAGE_URL.format(p=p)]
    return urls


class SidecarError(ValueError):
    """The sidecar is unusable as a whole (never a per-row problem)."""


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_sha(doc: dict) -> str:
    body = {k: v for k, v in doc.items() if k != "content_sha256"}
    return hashlib.sha256(canonical(body).encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dumps(doc: dict, row_keys: tuple[str, ...] = ()) -> str:
    """Deterministic text. Top level pretty (indent 1, sorted keys); each array
    named in ``row_keys`` is written one compact row per line, so a diff of a
    rebuilt file is a diff of rows."""
    keep = {k: doc[k] for k in row_keys if k in doc}
    rest = {k: v for k, v in doc.items() if k not in keep}
    # build by hand so key order stays sorted
    parts = []
    for k in sorted(doc):
        if k in keep:
            rows = ",\n".join("  " + json.dumps(r, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
                              for r in keep[k])
            parts.append(f' {json.dumps(k)}: [\n{rows}\n ]' if keep[k] else f" {json.dumps(k)}: []")
        else:
            body = json.dumps(rest[k], indent=1, sort_keys=True, ensure_ascii=False)
            body = body.replace("\n", "\n ")
            parts.append(f" {json.dumps(k)}: {body}")
    return "{\n" + ",\n".join(parts) + "\n}\n"


def num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float("inf")


def url_ok(url, hosts: tuple = URL_HOSTS) -> bool:
    if not isinstance(url, str):
        return False
    u = urlparse(url)
    if u.scheme != "https" or not u.netloc or " " in url:
        return False
    host = u.hostname or ""
    return any(host == h or host.endswith("." + h) for h in hosts)


def licence_ok(lic) -> bool:
    return (isinstance(lic, dict) and bool(lic.get("name")) and bool(lic.get("note"))
            and url_ok(lic.get("url"), URL_HOSTS + LICENCE_HOSTS))


class Checks:
    """Counts every executed rule so the validator can print PASS / FAIL totals."""

    def __init__(self):
        self.passed = 0
        self.failed: list[str] = []

    def __call__(self, ok: bool, label: str, bag: list | None = None) -> bool:
        if ok:
            self.passed += 1
        else:
            self.failed.append(label)
            if bag is not None:
                bag.append(label)
        return bool(ok)


def env_path(env_name: str, filename: str) -> Path:
    """<env_name>, else <SUPERMAP_DATA_DIR>/graph/<filename> when that exists,
    else the repository's data/graph/<filename> (same rule as extra.json)."""
    env = os.environ.get(env_name)
    if env:
        return Path(env)
    data_dir = os.environ.get("SUPERMAP_DATA_DIR")
    if data_dir:
        p = Path(data_dir) / "graph" / filename
        if p.exists():
            return p
    return DATA_GRAPH / filename


def validate_row_provenance(row: dict, chk: Checks, bad: list, tag: str, sources: dict) -> None:
    """The fields every row of either sidecar carries."""
    sid = row.get("source_id")
    chk(sid in sources, f"{tag}: source_id {sid!r} is declared", bad)
    chk(isinstance(row.get("source_name"), str) and bool(row.get("source_name")), f"{tag}: source_name", bad)
    chk(url_ok(row.get("source_url")), f"{tag}: source_url is https on a declared publisher", bad)
    chk(isinstance(row.get("retrieved_at"), str) and bool(TS_RE.match(row["retrieved_at"])),
        f"{tag}: retrieved_at is ISO-8601 Z", bad)
    chk(row.get("figure_label") in LABELS, f"{tag}: figure_label in {LABELS}", bad)
    if sid in sources:
        chk(row.get("source_name") == sources[sid].get("name"), f"{tag}: source_name equals the source block", bad)
        chk(row.get("retrieved_at") == sources[sid].get("retrieved_at"),
            f"{tag}: retrieved_at equals the source block", bad)


# ---- label normalisation used to match annex ports to graph places (build) and to re-check them (loader) ----
COUNTRY_ALIAS = {  # label variants of the same country; used for the match confidence only
    "viet nam": "vietnam", "bahamas the": "the bahamas", "republic of korea": "south korea",
    "korea republic of": "south korea", "hong kong sar china": "hong kong china", "st lucia": "saint lucia",
    "turkiye": "turkey", "netherlands": "the netherlands", "russian federation": "russia",
    "united states of america": "united states", "iran islamic republic of": "iran", "egypt arab rep": "egypt",
    "korea": "south korea", "taiwan china": "taiwan", "venezuela rb": "venezuela",
}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def strip_port_of(s: str) -> str:
    s = norm(s)
    s = re.sub(r"^port of ", "", s)
    return re.sub(r" port$", "", s)


def country_norm(s: str) -> str:
    n = norm(s)
    return COUNTRY_ALIAS.get(n, n)


