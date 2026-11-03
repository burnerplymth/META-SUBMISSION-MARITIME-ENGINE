"""NL query -> STRUCTURED FILTERS ONLY (the server half of resolveQuery).

``POST /llm/query {text}`` returns the same resolved shape the web's
``resolveQuery`` produces - ``{text, filters}`` - where ``text`` is what
the lexical engine ranks and ``filters`` is the closed ``SearchFilters``
vocabulary. The model may PARSE language; it may never rank, invent an
entity, or answer in prose. This module enforces that by construction,
not by trust:

* the model's reply must be a JSON object with EXACTLY the closed
  field set (extra keys such as ``answer`` / ``entities`` -> rejected);
* every filter value must be a member of its closed enum (no fuzzy
  matching, no coercion; ``severity_min`` an int 1..5, ``date_floor_days``
  one of the allowed windows);
* ``text`` may only contain words the USER typed (token-subset check
  after normalisation), so no entity can be introduced, and is capped
  at the user's own length;
* anything failing a check, a provider error, or a cap/kill-switch
  refusal falls back to the exact lexical resolution
  ``{text: <user text>, filters: {}}`` with ``source: "lexical_fallback"``
  and a short machine reason - the model's rejected text is never echoed;
* a reply that resolves to nothing at all (no filters, empty text) also
  falls back: an unparsed query stays a plain lexical query.

The output is never an engine input: it narrows a search box, nothing
else.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from . import provenance as prov
from .provider import CapExceeded, LLMProvider, ProviderError

CATEGORIES = ("places", "panels", "events", "companies", "ledger", "runs",
              "watchlists", "goods")
MODES = ("sea", "air", "ground")
DATE_WINDOWS = (1, 7, 30, 90, 365)
MAX_TEXT_CHARS = 300

_ALLOWED = {"text", "categories", "modes", "severity_min", "date_floor_days"}


class _Reject(Exception):
    """Internal: the model reply failed a post-check (reason code only)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _tokens(s: str) -> list[str]:
    s = s.lower()
    return re.findall(r"[a-z0-9]+", s)


def _enum_list(value, allowed: tuple, field: str):
    if value is None:
        return None
    if not isinstance(value, list) or not value \
            or any(not isinstance(v, str) or v not in allowed for v in value):
        raise _Reject(f"{field}_not_in_enum")
    return list(dict.fromkeys(value))


def validate_reply(raw: object, user_text: str) -> dict:
    """Strict post-check of one parsed model reply -> normalised fields."""
    if not isinstance(raw, dict):
        raise _Reject("not_an_object")
    if set(raw) != _ALLOWED:
        raise _Reject("fields_not_exactly_the_closed_set")
    text = raw["text"]
    if not isinstance(text, str):
        raise _Reject("text_not_string")
    if len(text) > len(user_text):
        raise _Reject("text_longer_than_input")
    user_tokens = set(_tokens(user_text))
    if any(t not in user_tokens for t in _tokens(text)):
        raise _Reject("text_introduces_words_not_typed")
    cats = _enum_list(raw["categories"], CATEGORIES, "categories")
    modes = _enum_list(raw["modes"], MODES, "modes")
    sev = raw["severity_min"]
    if sev is not None and (isinstance(sev, bool) or not isinstance(sev, int)
                            or not 1 <= sev <= 5):
        raise _Reject("severity_min_invalid")
    days = raw["date_floor_days"]
    if days is not None and (isinstance(days, bool) or not isinstance(days, int)
                             or days not in DATE_WINDOWS):
        raise _Reject("date_floor_days_invalid")
    return {"text": text.strip(), "categories": cats, "modes": modes,
            "severity_min": sev, "date_floor_days": days}


def to_filters(parsed: dict, now: datetime) -> dict:
    """Closed-vocabulary parse -> web ``SearchFilters`` (camelCase keys)."""
    f: dict = {}
    if parsed["categories"]:
        f["categories"] = parsed["categories"]
    if parsed["modes"]:
        f["modes"] = parsed["modes"]
    if parsed["severity_min"] is not None:
        f["severities"] = list(range(parsed["severity_min"], 6))
    if parsed["date_floor_days"] is not None:
        floor = now - timedelta(days=parsed["date_floor_days"])
        f["dateFloor"] = floor.strftime("%Y-%m-%dT%H:%M:%SZ")
    return f


class NLQuery:
    def __init__(self, provider: Optional[LLMProvider] = None,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.provider = provider or LLMProvider.from_env()
        self._clock = clock

    def cost_preview(self, text: str) -> dict:
        return self.provider.cost_preview("query", {"text": text})

    def _fallback(self, text: str, reason: str, res: Optional[dict]) -> dict:
        return {
            "text": text, "filters": {}, "source": "lexical_fallback",
            "rejected": reason,
            "provenance": prov.from_result(res, "query") if res
            else prov.none("no usable model reply; lexical resolution served"),
        }

    def resolve(self, text: str) -> dict:
        text = text.strip()
        if not text or len(text) > MAX_TEXT_CHARS:
            raise ValueError(f"text must be 1..{MAX_TEXT_CHARS} characters")
        res: Optional[dict] = None
        try:
            res = self.provider.complete("query", {"text": text})
        except (CapExceeded, ProviderError):
            return self._fallback(text, "provider_unavailable", None)
        except OSError:                      # network down on the http transport
            return self._fallback(text, "transport_error", None)
        try:
            parsed = validate_reply(json.loads(res["text"]), text)
        except (json.JSONDecodeError, TypeError, ValueError):
            return self._fallback(text, "not_json", res)
        except _Reject as exc:
            return self._fallback(text, exc.code, res)
        filters = to_filters(parsed, self._clock())
        if not filters and not parsed["text"]:
            return self._fallback(text, "nothing_resolved", res)
        return {
            "text": parsed["text"], "filters": filters, "source": "nl",
            "rejected": None, "provenance": prov.from_result(res, "query"),
        }
