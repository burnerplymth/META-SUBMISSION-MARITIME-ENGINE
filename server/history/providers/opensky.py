"""OpenSky Network - HISTORY-CAPABLE only through the authenticated
airport flight lists; state vectors are CURRENT-ONLY.

Behaviour with OAuth2 client credentials in the environment
(token request OK):
  * ``/states/all?time=<old>`` -> HTTP 403 "Historical data more than 1
    hour ago can only be retrieved with /states/own": a historical map of
    where aircraft WERE is NOT available to this tier;
  * ``/flights/arrival?airport=EDDF&begin=&end=`` -> HTTP 200 with the
    arrivals in a window ~6, ~45 and ~1500 days back (an empty window is
    HTTP 404 ``[]`` - a 120-day-back probe returned that, so coverage is
    patchy). The endpoint takes windows of at most 2 days; this provider
    asks one UTC day per call, so a daily arrivals series costs one
    request per day (cap 12/day => at most 12 days per UTC day of budget).

Observation: ``flights_arrivals`` / ``flights_departures`` per air_*
place whose graph aliases carry a 4-letter ICAO code. Credentials come
from OPENSKY_CLIENT_ID / OPENSKY_CLIENT_SECRET at call time and are
never stored or logged. The count is the number of flights OpenSky's
estimate assigns to the airport that day; it is OpenSky's receiver
coverage, not the airport's official movement count.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone

from ..transport import HttpRequest
from ..util import day_range, iso_day
from .base import CAPABLE, HistoryProvider, NotServed, not_served

API = "https://opensky-network.org/api"
TOKEN_URL = ("https://auth.opensky-network.org/auth/realms/opensky-network/"
             "protocol/openid-connect/token")
SOURCE_LABEL = "OpenSky Network"
MAX_DAYS = 12
_ICAO = re.compile(r"^[A-Z]{4}$")
FLOW = {"flights_arrivals": "arrival", "flights_departures": "departure"}


def icao_for(node: dict) -> str | None:
    for alias in node.get("aliases") or []:
        if isinstance(alias, str) and _ICAO.match(alias):
            return alias
    return None


class OpenSkyProvider(HistoryProvider):
    name = "opensky"
    label = SOURCE_LABEL
    capability = CAPABLE
    budget_key = "opensky"
    supports_observations = True
    history_window = ("airport arrival/departure flight lists (<=2-day "
                      "windows, coverage patchy); state vectors: current "
                      "only (>1 h old -> 403)")
    verified = {
        "checked_on": "2026-10-25",
        "result": "PARTIAL: /flights/* OK, /states/all?time= 403",
        "how": ("token OK; flights/arrival EDDF returned HTTP 200 for "
                "windows ~6, ~45 and ~1500 days back and 404 [] for ~120 "
                "days back; states/all?time=<2021> -> 403"),
    }
    notes = ("A research-tier account is needed for historical state "
             "vectors; this tier gets airport flight lists only. Daily "
             "counts cost one request per day.")

    def credential_state(self):
        ok = self.svc.getenv("OPENSKY_CLIENT_ID") and self.svc.getenv(
            "OPENSKY_CLIENT_SECRET")
        return "present" if ok else "missing"

    def metrics(self) -> list[dict]:
        return [{"metric": m, "applies_to": "air_* with an ICAO alias",
                 "unit": "flights/day", "label": m.replace("_", " "),
                 "native_field": None} for m in FLOW]

    def _token(self) -> str:
        cid = self.svc.getenv("OPENSKY_CLIENT_ID")
        sec = self.svc.getenv("OPENSKY_CLIENT_SECRET")
        if not (cid and sec):
            raise NotServed("no_credential",
                            "OPENSKY_CLIENT_ID / OPENSKY_CLIENT_SECRET not set")
        req = HttpRequest("POST", TOKEN_URL, form={
            "grant_type": "client_credentials", "client_id": cid,
            "client_secret": sec})
        doc = self.json_body(self.http(req), "OpenSky token")
        tok = doc.get("access_token") if isinstance(doc, dict) else None
        if not tok:
            raise NotServed("auth_rejected", "OpenSky: no access_token")
        return tok

    def observations(self, place: str, metric: str, start: date,
                     end: date) -> dict:
        if metric not in FLOW:
            return not_served(self.name, "unsupported_metric",
                              f"one of {sorted(FLOW)}", place=place,
                              metric=metric)
        try:
            book = self.svc.book()
            if place not in book:
                raise NotServed("unknown_place", f"{place!r} is not a graph place")
            icao = icao_for(book.nodes[place])
            if not icao:
                raise NotServed("no_series_for_place",
                                f"{place} has no ICAO alias")
        except NotServed as ns:
            return not_served(self.name, ns.reason, ns.detail, place=place,
                              metric=metric)
        days = day_range(start, end)
        truncated = len(days) > MAX_DAYS
        days = days[:MAX_DAYS]
        series, missing, errors = [], [], []
        retrieved = []
        token_box: dict = {}
        for d in days:
            query = {"series": f"{FLOW[metric]}|{icao}", "from": iso_day(d),
                     "to": iso_day(d)}

            def fetcher(d=d):
                if "t" not in token_box:
                    token_box["t"] = self._token()
                b = int(datetime(d.year, d.month, d.day,
                                 tzinfo=timezone.utc).timestamp())
                req = HttpRequest("GET", f"{API}/flights/{FLOW[metric]}",
                                  params={"airport": icao, "begin": str(b),
                                          "end": str(b + 86399)},
                                  headers={"Authorization":
                                           "Bearer " + token_box["t"]})
                resp = self.http(req)
                if resp.status == 404:   # OpenSky: no flights in the window
                    return ({"airport": icao, "count": 0, "empty_window": True},
                            req.public(), "EMPTY", "HTTP 404 [] - no flights")
                doc = self.json_body(resp, "OpenSky flights")
                if not isinstance(doc, list):
                    raise NotServed("bad_payload", "OpenSky: expected a list")
                return ({"airport": icao, "count": len(doc)}, req.public(),
                        "OK", f"{len(doc)} flights")

            try:
                doc, hit = self.through_cache(query, fetcher)
            except NotServed as ns:
                errors.append({"date": iso_day(d), "reason": ns.reason,
                               "detail": ns.detail})
                missing.append(iso_day(d))
                if ns.reason in ("budget_exhausted", "no_credential",
                                 "rate_limited", "policy_denied",
                                 "auth_rejected", "not_cached_fetch_disabled"):
                    # nothing further can be served either: stop here
                    missing.extend(iso_day(x) for x in days[days.index(d) + 1:])
                    break
                continue
            retrieved.append(doc["provenance"]["retrieved_at"])
            series.append({"date": iso_day(d), "value": doc["payload"]["count"]})
        if not series:
            first = errors[0] if errors else {"reason": "no_data", "detail": ""}
            return not_served(self.name, first["reason"], first["detail"],
                              place=place, metric=metric, errors=errors)
        return {"served": True, "source": self.name, "place": place,
                "metric": metric, "icao": icao, "unit": "flights/day",
                "label": f"OpenSky {FLOW[metric]} flights at {icao}",
                "frequency": "daily", "series": series,
                "coverage": {"from": iso_day(start), "to": iso_day(end),
                             "n": len(series), "missing_dates": missing,
                             "truncated_to_days": MAX_DAYS if truncated else None},
                "errors": errors,
                "provenance": {"source": SOURCE_LABEL,
                               "retrieved_at": max(retrieved), "mode": "SNAPSHOT"}}
