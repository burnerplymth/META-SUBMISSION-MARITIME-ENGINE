"""Source citations for served feed items.

A DisruptionEvent carries ``evidence_url``: for NOAA/NWS that is an
``api.weather.gov`` JSON endpoint, which reads as unfinished when shown
to a person. This module derives, at serving time and from fields the
feed itself provided, a human-readable **citation**::

    {"publisher": "NWS Wakefield VA",
     "title": "Small Craft Advisory - Coastal waters from ...",
     "url": "https://www.weather.gov/wwamap/wwatxtget.php?cwa=akq&wwa=...",
     "domain": "weather.gov",
     "kind": "ALERT",                    # ARTICLE | ALERT | REPORT | PORTAL
     "published_at": "2025-01-15T10:28:00Z"}   # optional

Rules:

* additive and derived: nothing here is stored, the required
  fields are untouched and ``evidence_url`` stays on the record;
* never invented: every part comes from a field of the record. When a
  source has no human-readable page the function returns ``None`` and
  the item carries no ``citation`` key (the UI then shows only the
  provenance chip, no link);
* hardened: https only, no userinfo, no IP literals, host allow-list
  per kind (ARTICLE hosts are open by nature, so they must be plain
  public DNS names), URL length cap, control characters stripped from
  every text part. A feed cannot inject ``javascript:`` or odd schemes.

Pure functions, no I/O, no clock, no LLM: same record in, same citation
out.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit, urlunsplit

KINDS = ("ARTICLE", "ALERT", "REPORT", "PORTAL")
URL_MAX = 2048
TITLE_MAX = 160
PUBLISHER_MAX = 80

# Host allow-list per kind. ``None`` = any public DNS name (articles come
# from arbitrary news sites), still subject to the structural checks.
KIND_HOSTS: dict[str, frozenset[str] | None] = {
    "ALERT": frozenset({"www.weather.gov", "forecast.weather.gov"}),
    "REPORT": frozenset({"www.gdacs.org"}),
    "PORTAL": frozenset({"portwatch.imf.org"}),
    "ARTICLE": None,
}

_HOST_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+(xn--[a-z0-9-]{1,59}|[a-z]{2,63})$")
_OFFICE_RE = re.compile(r"^[a-z]{3}$")
_WMO_RE = re.compile(r"\b([KPT][A-Z]{3}) \d{6}\b")
# WMO station id -> WFO id where the two differ (verified: weather.gov/sju
# answers 200, weather.gov/jsj answers 404).
_WMO_OFFICE_OVERRIDE = {"jsj": "sju"}
_SENDER_FROM_HEADLINE_RE = re.compile(r"\bby (NWS [^\n]+?)\s*$")

# Rows without ``nws.office``: the issuing office is recovered from the
# headline's "by NWS <name>" suffix through this table. Every pair was taken
# from live api.weather.gov alerts (senderName + WMOidentifier) or checked
# against api.weather.gov/offices/<id>. An office not listed yields no citation (never a guess).
NWS_OFFICE_BY_SENDER: dict[str, str] = {
    "NWS Albuquerque NM": "abq", "NWS Anchorage AK": "afc",
    "NWS Baltimore MD/Washington DC": "lwx", "NWS Boston/Norton MA": "box",
    "NWS Charleston SC": "chs", "NWS Charleston WV": "rlx",
    "NWS Cheyenne WY": "cys", "NWS Detroit/Pontiac MI": "dtx",
    "NWS El Paso Tx/Santa Teresa NM": "epz", "NWS Eureka CA": "eka",
    "NWS Fairbanks AK": "afg", "NWS Flagstaff AZ": "fgz",
    "NWS Gaylord MI": "apx", "NWS Grand Junction CO": "gjt",
    "NWS Grand Rapids MI": "grr", "NWS Gray ME": "gyx",
    "NWS Great Falls MT": "tfx", "NWS Hastings NE": "gid",
    "NWS Honolulu HI": "hfo", "NWS Jacksonville FL": "jax",
    "NWS Juneau AK": "ajk", "NWS Key West FL": "key",
    "NWS Lake Charles LA": "lch", "NWS Las Vegas NV": "vef",
    "NWS Los Angeles/Oxnard CA": "lox", "NWS Marquette MI": "mqt",
    "NWS Medford OR": "mfr", "NWS Melbourne FL": "mlb",
    "NWS Miami FL": "mfl", "NWS Mobile AL": "mob",
    "NWS Mount Holly NJ": "phi", "NWS New Orleans LA": "lix",
    "NWS Newport/Morehead City NC": "mhx", "NWS Northern Indiana": "iwx",
    "NWS Peachtree City GA": "ffc", "NWS Phoenix AZ": "psr",
    "NWS Portland OR": "pqr", "NWS Pueblo CO": "pub",
    "NWS Salt Lake City UT": "slc", "NWS San Diego CA": "sgx",
    "NWS San Francisco CA": "mtr", "NWS San Juan PR": "sju",
    "NWS Seattle WA": "sew", "NWS Tallahassee FL": "tae",
    "NWS Tampa Bay Ruskin FL": "tbw", "NWS Tiyan GU": "gum",
    "NWS Tucson AZ": "twc", "NWS Upton NY": "okx",
    "NWS Wakefield VA": "akq", "NWS Wilmington NC": "ilm",
    "NWS Wilmington OH": "iln",
}


# ---------------------------------------------------------------- helpers

def clean_text(value: object, cap: int) -> str | None:
    """Printable single-line text, whitespace collapsed, capped with an
    ellipsis; ``None`` when not a string or nothing printable remains."""
    if not isinstance(value, str):
        return None
    text = unicodedata.normalize("NFC", value)
    # Cc/Cf (controls, bidi overrides), Cs, Co, Cn and line/para separators
    text = "".join(" " if unicodedata.category(c) in
                   ("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp") else c
                   for c in text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return None
    if len(text) > cap:
        text = text[: cap - 1].rstrip() + "…"
    return text


def safe_url(url: object, kind: str) -> str | None:
    """Return the normalised https URL, or ``None`` when it is not safe
    to show as a link for ``kind``."""
    if not isinstance(url, str) or not url or len(url) > URL_MAX:
        return None
    if any(ord(c) <= 0x20 or ord(c) == 0x7F or c in '<>"\\^`{|}' for c in url):
        return None
    # Non-ASCII invisibles too: C1 controls, bidi overrides and
    # zero-width characters (Cf), line/paragraph separators, NBSP-style
    # spaces, unassigned and private-use code points. A link whose text
    # reorders or hides part of itself is not one a person can verify.
    if any(unicodedata.category(c) in ("Cc", "Cf", "Cs", "Co", "Cn",
                                       "Zl", "Zp", "Zs") for c in url):
        return None
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme.lower() != "https":
        return None
    if parts.username is not None or parts.password is not None:
        return None
    if port not in (None, 443):
        return None
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        return None
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    if not _HOST_RE.match(host):
        return None  # IP literal, localhost, single label, odd characters
    allowed = KIND_HOSTS.get(kind, frozenset())
    if allowed is not None and host not in allowed:
        return None
    return urlunsplit(("https", host, parts.path or "/", parts.query, parts.fragment))


def _domain_of(url: str) -> str:
    host = urlsplit(url).hostname or ""
    return host[4:] if host.startswith("www.") else host


def to_utc_iso(value: object) -> str | None:
    """ISO-8601 (with or without offset, 'Z' accepted), a GDELT
    ``20261024T053000Z`` stamp or a naive GDACS stamp -> ``...Z`` UTC."""
    if not isinstance(value, str) or not value.strip():
        return None
    s = value.strip()
    try:
        if re.fullmatch(r"\d{8}T\d{6}Z?", s):
            dt = datetime.strptime(s[:15], "%Y%m%dT%H%M%S")
        else:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def nws_office_id(wmo_identifier: object) -> str | None:
    """The issuing Weather Forecast Office id (lower-case, 3 letters) from
    an alert's ``parameters.WMOidentifier`` (e.g. ``"WHUS71 KAKQ 251028"``
    -> ``"akq"``). ``None`` when the identifier does not carry one."""
    if isinstance(wmo_identifier, list):
        wmo_identifier = wmo_identifier[0] if wmo_identifier else None
    if not isinstance(wmo_identifier, str):
        return None
    m = _WMO_RE.search(wmo_identifier)
    if not m:
        return None
    office = m.group(1)[1:].lower()
    return _WMO_OFFICE_OVERRIDE.get(office, office)


def make_citation(*, kind: str, publisher: object, title: object, url: object,
                  published_at: object = None) -> dict | None:
    """Validate and assemble one citation; ``None`` if any required part
    (publisher, title, safe url) is missing or unsafe."""
    if kind not in KINDS:
        return None
    safe = safe_url(url, kind)
    pub = clean_text(publisher, PUBLISHER_MAX)
    ttl = clean_text(title, TITLE_MAX)
    if safe is None or pub is None or ttl is None:
        return None
    out = {"publisher": pub, "title": ttl, "url": safe,
           "domain": _domain_of(safe), "kind": kind}
    when = to_utc_iso(published_at)
    if when:
        out["published_at"] = when
    return out


# ------------------------------------------------------------ per source

def _nws_citation(ev: dict, nws: dict) -> dict | None:
    headline = nws.get("headline") if isinstance(nws.get("headline"), str) else ""
    sm = _SENDER_FROM_HEADLINE_RE.search(headline)
    sender = clean_text(nws.get("sender"), PUBLISHER_MAX) or (
        clean_text(sm.group(1), PUBLISHER_MAX) if sm else None)
    office = nws.get("office")
    office = office.lower() if isinstance(office, str) and _OFFICE_RE.match(office.lower()) else None
    if office is None and sender:
        office = NWS_OFFICE_BY_SENDER.get(sender)
    if office is None:
        return None  # issuing office unknown -> no page to point at
    event_class = clean_text(nws.get("event"), 80)
    area = clean_text(nws.get("area"), 120)
    title = (f"{event_class} — {area}" if event_class and area
             else event_class or clean_text(headline, TITLE_MAX))
    if ev.get("status") == "active" and event_class:
        # the office's own page for this alert class, carrying the full text
        url = ("https://www.weather.gov/wwamap/wwatxtget.php?cwa="
               f"{office}&wwa={quote(event_class, safe='')}")
    else:  # ended/unknown: the alert text page would be empty, the office home is stable
        url = f"https://www.weather.gov/{office}/"
    return make_citation(
        kind="ALERT", publisher=sender or f"NWS {office.upper()}", title=title, url=url,
        published_at=nws.get("sent") or nws.get("effective") or ev.get("started_at"))


def _gdacs_citation(ev: dict, gd: dict) -> dict | None:
    url = safe_url(gd.get("report_url"), "REPORT") or safe_url(ev.get("evidence_url"), "REPORT")
    if url is None:
        etype, eid = gd.get("eventtype"), gd.get("eventid")
        if isinstance(etype, str) and re.fullmatch(r"[A-Z]{2}", etype) \
                and isinstance(eid, int) and not isinstance(eid, bool) and eid > 0:
            url = f"https://www.gdacs.org/report.aspx?eventtype={etype}&eventid={eid}"
    return make_citation(kind="REPORT", publisher="GDACS", title=gd.get("name"),
                         url=url, published_at=gd.get("fromdate") or ev.get("started_at"))


def _article_citation(art: dict) -> dict | None:
    url = safe_url(art.get("url"), "ARTICLE")
    if url is None:
        return None
    domain = _domain_of(url)
    return make_citation(kind="ARTICLE", publisher=domain, title=art.get("title"),
                         url=url, published_at=art.get("seendate") or art.get("published_at"))


def _portal_citation(ev: dict) -> dict | None:
    return make_citation(kind="PORTAL", publisher="IMF PortWatch", title="PortWatch",
                         url=ev.get("evidence_url"),
                         published_at=ev.get("started_at"))


def derive_citation(ev: object) -> dict | None:
    """The citation for one served feed item, or ``None`` (no human page)."""
    if not isinstance(ev, dict):
        return None
    try:
        nws, gd, art = ev.get("nws"), ev.get("gdacs"), ev.get("article")
        if isinstance(nws, dict):
            return _nws_citation(ev, nws)
        if isinstance(gd, dict):
            return _gdacs_citation(ev, gd)
        if isinstance(art, dict):
            return _article_citation(art)
        if ev.get("record_type") == "feed_item":  # a raw GDELT feed record
            return _article_citation(ev)
        if str(ev.get("source") or "").startswith("IMF PortWatch"):
            return _portal_citation(ev)
    except Exception:  # a hostile record must never break serving
        return None
    return None


def with_citation(ev: object) -> object:
    """A shallow copy of ``ev`` with a derived ``citation``; any
    ``citation`` already present on the record is discarded first (the
    feed does not get to choose what we link to)."""
    if not isinstance(ev, dict):
        return ev
    out = {k: v for k, v in ev.items() if k != "citation"}
    cit = derive_citation(out)
    if cit is not None:
        out["citation"] = cit
    return out
