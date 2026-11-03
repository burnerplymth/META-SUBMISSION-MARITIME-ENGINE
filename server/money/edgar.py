"""SEC EDGAR adapter — no key, declared User-Agent.

EDGAR fair-access discipline (https://www.sec.gov/os/accessing-edgar-data):
  * a declared User-Agent on every request, taken ONLY from env
    SUPERMAP_EDGAR_UA (the operator's real contact). There is no build
    default: live fetching REFUSES with MissingEdgarUserAgentError when
    the env var is not set — no request is made. The pure
    parsers below are offline and unaffected;
  * <= 10 requests/second — this client throttles to one request per
    0.15 s within a process;
  * no bulk hammering: the exposure harvest fetches one annual report
    per company.

Endpoints used:
  * company tickers:  https://www.sec.gov/files/company_tickers.json
  * submissions:      https://data.sec.gov/submissions/CIK##########.json
  * filing documents: https://www.sec.gov/Archives/edgar/data/<cik>/
                      <accession-nodashes>/<primary_document>
  * full-text search: https://efts.sec.gov/LATEST/search-index?q=...
"""
from __future__ import annotations

import html
import json
import os
import re
import time

from .common import (AdapterError, health_dict, http_get, http_get_json,
                     provenance)

SOURCE = "SEC EDGAR"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVES_DOC_URL = ("https://www.sec.gov/Archives/edgar/data/{cik}/"
                    "{accession_nodashes}/{primary_document}")
FTS_URL = "https://efts.sec.gov/LATEST/search-index"
ANNUAL_FORMS = ("20-F", "10-K", "40-F")

_last_request_at = 0.0


class MissingEdgarUserAgentError(AdapterError):
    """Live EDGAR fetch refused: SUPERMAP_EDGAR_UA is not set.

    SEC fair access requires a declared User-Agent carrying the
    operator's real contact; shipping a placeholder example contact
    as a silent fallback misdeclares every request, so the fetch
    refuses instead."""


def declared_user_agent() -> str:
    ua = os.environ.get("SUPERMAP_EDGAR_UA")
    if not ua or not ua.strip():
        raise MissingEdgarUserAgentError(
            "SUPERMAP_EDGAR_UA is not set: live EDGAR fetching requires "
            "a declared User-Agent with the operator's real contact "
            "(SEC fair-access rule); refusing to fetch — set "
            "SUPERMAP_EDGAR_UA and retry. No request was made.")
    return ua


def _throttle() -> None:
    global _last_request_at
    wait = 0.15 - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.monotonic()


def parse_tickers(payload: dict) -> dict[str, dict]:
    """company_tickers.json -> {TICKER: {cik, ticker, company}}."""
    out = {}
    for row in payload.values():
        out[row["ticker"].upper()] = {
            "cik": int(row["cik_str"]),
            "ticker": row["ticker"].upper(),
            "company": row["title"],
        }
    return out


def latest_annual_filing(submissions: dict) -> dict | None:
    """Most recent annual report (20-F / 10-K / 40-F) from a submissions
    payload's filings.recent table. Pure function of the payload."""
    recent = (submissions.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    best = None
    for i, form in enumerate(forms):
        if form in ANNUAL_FORMS:
            cand = {
                "form": form,
                "filed_date": recent["filingDate"][i],
                "report_date": recent["reportDate"][i],
                "accession": recent["accessionNumber"][i],
                "primary_document": recent["primaryDocument"][i],
            }
            if best is None or cand["filed_date"] > best["filed_date"]:
                best = cand
    return best


def filing_url(cik: int, accession: str, primary_document: str) -> str:
    return ARCHIVES_DOC_URL.format(
        cik=cik, accession_nodashes=accession.replace("-", ""),
        primary_document=primary_document)


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def html_to_text(raw: str) -> str:
    """Crude but deterministic filing-text extraction: drop script/style,
    strip tags, unescape entities, collapse whitespace. Excerpts produced
    from this text layer are verbatim at the text layer (the filing's own
    words, markup removed) — stated as such wherever they are stored."""
    raw = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
    text = _TAG_RE.sub(" ", raw)
    return _WS_RE.sub(" ", html.unescape(text)).strip()


def _is_taxonomy_noise(excerpt: str) -> bool:
    """XBRL taxonomy dumps embedded in filing text layers (member-name
    lists like 'ups:FacilityNotes...WorldportLouisville...Member') are
    not narrative evidence. A window dense in taxonomy markers is
    skipped so the search reaches the filing's actual prose."""
    return excerpt.count("Member") >= 3 or ":Member" in excerpt \
        or "us-gaap:" in excerpt


def find_excerpt(text: str, phrases: list[str],
                 require_any: list[str] | None = None,
                 window: int = 300) -> tuple[str, str] | None:
    """First (phrase, excerpt) whose +/-window-char context contains the
    phrase and, when require_any is given, at least one of those words.
    The excerpt is a verbatim slice of `text`. None when nothing matches
    — a missing excerpt is data, never a reason to invent one."""
    low = text.lower()
    for phrase in phrases:
        needle = phrase.lower()
        start = 0
        while True:
            idx = low.find(needle, start)
            if idx < 0:
                break
            lo = max(0, idx - window)
            hi = min(len(text), idx + len(phrase) + window)
            excerpt = text[lo:hi].strip()
            if _is_taxonomy_noise(excerpt):
                start = idx + len(needle)
                continue
            if require_any:
                ctx = excerpt.lower()
                if not any(w.lower() in ctx for w in require_any):
                    start = idx + len(needle)
                    continue
            return phrase, excerpt
    return None


class EDGARClient:
    name = "edgar"
    source = SOURCE

    def __init__(self) -> None:
        self._last_success_at: str | None = None
        self._note: str | None = None
        self._state = "DOWN"

    def _get(self, url: str, timeout: int = 60):
        # Resolve the UA first: the refusal must fire before any
        # throttle wait and, above all, before any request is made.
        ua = declared_user_agent()
        _throttle()
        return http_get(url, user_agent=ua, timeout=timeout)

    def _ok(self, prov: dict) -> None:
        self._last_success_at = prov["retrieved_at"]
        self._state, self._note = "LIVE", None

    def company_tickers(self) -> dict:
        status, payload, _ = http_get_json(
            TICKERS_URL, user_agent=declared_user_agent(), timeout=60)
        if status != 200 or not isinstance(payload, dict):
            self._state, self._note = "DOWN", f"EDGAR tickers HTTP {status}"
            raise AdapterError(self._note)
        prov = provenance(SOURCE, "LIVE")
        self._ok(prov)
        return {"records": parse_tickers(payload), "provenance": prov}

    def submissions(self, cik: int) -> dict:
        status, payload, _ = http_get_json(
            SUBMISSIONS_URL.format(cik=cik),
            user_agent=declared_user_agent(), timeout=60)
        if status != 200 or not isinstance(payload, dict):
            self._state, self._note = "DOWN", f"EDGAR submissions HTTP {status}"
            raise AdapterError(self._note)
        prov = provenance(SOURCE, "LIVE")
        self._ok(prov)
        return {"records": payload, "provenance": prov}

    def filing_text(self, url: str) -> dict:
        status, body, _ = self._get(url, timeout=180)
        if status != 200:
            self._state, self._note = "DOWN", f"EDGAR filing HTTP {status}"
            raise AdapterError(self._note)
        prov = provenance(SOURCE, "LIVE")
        self._ok(prov)
        return {"records": html_to_text(body.decode("utf-8", "replace")),
                "provenance": prov}

    def full_text_search(self, query: str, forms: str = "20-F,10-K") -> dict:
        import urllib.parse
        url = FTS_URL + "?" + urllib.parse.urlencode(
            {"q": f'"{query}"', "forms": forms})
        status, payload, _ = http_get_json(
            url, user_agent=declared_user_agent(), timeout=60)
        if status != 200 or not isinstance(payload, dict):
            self._state, self._note = "DOWN", f"EDGAR FTS HTTP {status}"
            raise AdapterError(self._note)
        prov = provenance(SOURCE, "LIVE")
        self._ok(prov)
        hits = ((payload.get("hits") or {}).get("hits")) or []
        return {"records": hits, "provenance": prov}

    def health(self) -> dict:
        return health_dict(self.name, self._state, self._last_success_at,
                           "LIVE" if self._state == "LIVE" else None,
                           self._note)


def dumps_records(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)
