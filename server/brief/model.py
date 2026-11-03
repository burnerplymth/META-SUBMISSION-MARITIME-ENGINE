"""Brief request, section registry and constants.

Closed request keys (an unknown key is 422 invalid_request, the house rule):

    run_id            required, the stored run the brief is written from
    mc_job_id         optional, a finished Monte Carlo job ("mc_" + 16 hex)
    exposure          optional, {evaluation_id} or {summary} (inline), or the
                      exposure.evaluate/1 document itself
    lane_plan         optional, {origin, destination} place ids; planned with
                      the run's own disruption
    research_episode_id  optional, an episode id from GET /research/episodes
    include           optional, section ids (default: every section whose
                      input is supplied)
    style             "one_page" (the only style)
    regenerate        optional bool, write again even when the same brief exists
"""
from __future__ import annotations

import hashlib
import json
import re

SCHEMA = "brief/1"
PROMPT_NAME = "disruption_brief"
STYLE = "one_page"

LABEL = "Modelled from the deterministic engine. Not a forecast."

# id, title, needs (request key that must be supplied for the section to be
# included by default), model (the writer adds a lead paragraph and bullets).
SECTIONS: tuple[dict, ...] = (
    {"id": "situation", "title": "Situation", "needs": None, "model": True},
    {"id": "chain", "title": "Causal chain", "needs": None, "model": True},
    {"id": "places", "title": "Affected places and legs", "needs": None, "model": True},
    {"id": "delay", "title": "Delay ranges", "needs": "lane_plan", "model": True},
    {"id": "uncertainty", "title": "Uncertainty", "needs": "mc_job_id", "model": True},
    {"id": "exposure", "title": "Exposure", "needs": "exposure", "model": True},
    {"id": "context", "title": "Historical context", "needs": "research_episode_id", "model": True},
    {"id": "data", "title": "Data status", "needs": None, "model": True},
    {"id": "method", "title": "Method and limits", "needs": None, "model": False},
)
SECTION_IDS = tuple(s["id"] for s in SECTIONS)
SECTION_BY_ID = {s["id"]: s for s in SECTIONS}

REQUEST_KEYS = {"run_id", "mc_job_id", "exposure", "lane_plan", "research_episode_id",
                "include", "style", "regenerate"}

ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
MC_RE = re.compile(r"^mc_[0-9a-f]{16}$")
PLACE_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")

# Bounds (a brief is a page, not a data dump).
MAX_EVENT_ROWS = 10
MAX_PLACE_ROWS = 5
MAX_LEG_ROWS = 5
MAX_ROUTE_ROWS = 3
MAX_MC_PLACES = 5
MAX_EXPOSED_PLACES = 5
MAX_CONTEXT_EVENTS = 4
MAX_FACTS = 120
MAX_WORDS = 450

METHOD_LINES = (
    "The engine is deterministic: the same inputs return the same causal chain.",
    "Every delay is modelled; ranges are named variants or a seeded input-uncertainty simulation.",
    "Each figure is read from the engine output and registered in the fact register.",
    "The writer supplies wording only; statements are machine-checked against the register.",
    "Values are conditional on the stated inputs and are not a forecast.",
)


class BriefError(Exception):
    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _s(body: dict, key: str, rx: re.Pattern | None = None, required=False):
    v = body.get(key)
    if v is None:
        if required:
            raise BriefError("invalid_request", f"{key} (string) is required")
        return None
    if not isinstance(v, str) or not v or (rx is not None and not rx.match(v)):
        raise BriefError("invalid_request", f"{key} is not a valid id")
    return v


def parse_request(body) -> dict:
    """Validate and normalise a POST /brief body."""
    if not isinstance(body, dict):
        raise BriefError("invalid_request", "body must be a JSON object")
    extra = sorted(set(body) - REQUEST_KEYS)
    if extra:
        raise BriefError("invalid_request", f"unknown key {extra[0]!r}")
    req: dict = {"run_id": _s(body, "run_id", ID_RE, required=True)}
    req["mc_job_id"] = _s(body, "mc_job_id", MC_RE)
    req["research_episode_id"] = _s(body, "research_episode_id", ID_RE)
    style = body.get("style", STYLE)
    if style != STYLE:
        raise BriefError("invalid_request", f"style must be {STYLE!r}")
    req["style"] = style
    regen = body.get("regenerate", False)
    if not isinstance(regen, bool):
        raise BriefError("invalid_request", "regenerate must be true or false")
    req["regenerate"] = regen

    lane = body.get("lane_plan")
    if lane is not None:
        if not isinstance(lane, dict) or set(lane) - {"origin", "destination"}:
            raise BriefError("invalid_request", "lane_plan must be {origin, destination}")
        o, d = lane.get("origin"), lane.get("destination")
        if not (isinstance(o, str) and PLACE_RE.match(o or "")
                and isinstance(d, str) and PLACE_RE.match(d or "")):
            raise BriefError("invalid_request", "lane_plan needs origin and destination place ids")
        lane = {"origin": o, "destination": d}
    req["lane_plan"] = lane

    exp = body.get("exposure")
    if exp is not None:
        if not isinstance(exp, dict):
            raise BriefError("invalid_request", "exposure must be an object")
        if "evaluation_id" in exp and set(exp) == {"evaluation_id"}:
            if not isinstance(exp["evaluation_id"], str) or not ID_RE.match(exp["evaluation_id"]):
                raise BriefError("invalid_request", "evaluation_id is not a valid id")
            exp = {"evaluation_id": exp["evaluation_id"]}
        else:
            # Inline: the exposure.evaluate/1 document, or a plain summary.
            if len(json.dumps(exp, default=str)) > 200_000:
                raise BriefError("payload_too_large", "exposure summary is too large", 413)
            exp = {"inline": exp}
    req["exposure"] = exp

    inc = body.get("include")
    if inc is not None:
        if not isinstance(inc, list) or not all(isinstance(i, str) for i in inc) or len(inc) > 16:
            raise BriefError("invalid_request", "include must be a list of section ids")
        bad = [i for i in inc if i not in SECTION_IDS]
        if bad:
            raise BriefError("invalid_request", f"unknown section {bad[0]!r}")
    req["include"] = inc
    return req


def included_sections(req: dict) -> list[str]:
    """Section ids in document order. Default: every section whose input is
    supplied. An explicit ``include`` is honoured, in document order (a
    section whose input is absent then shows NOT SERVED)."""
    if req.get("include"):
        want = set(req["include"]) | {"situation", "method"}
        return [i for i in SECTION_IDS if i in want]
    out = []
    for s in SECTIONS:
        need = s["needs"]
        if need is None or req.get(need):
            out.append(s["id"])
    return out


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def sha256_hex(text: str | bytes) -> str:
    b = text.encode("utf-8") if isinstance(text, str) else text
    return hashlib.sha256(b).hexdigest()
