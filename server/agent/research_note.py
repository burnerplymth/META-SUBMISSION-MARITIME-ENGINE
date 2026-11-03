"""The research note: written by Meta AI, checked by code.

``write_note`` sends the session's evidence entries (ids, arguments, the
numbers and strings the tools returned) to the model through the one provider
seam (``LLMProvider.complete`` with the ``research_note`` prompt; structured
output on the Meta endpoint, a labelled RECORDED note on the fixture
transport). The reply is only a proposal: ``research_verify.verify_note``
removes or flags anything the evidence does not hold, and the parts that need
no model are built here from the evidence, deterministically:

  data used     every series that was assembled: dataset, place, unit, source,
                first and last observed day, state (cached, live, not_served)
  methods       one line per analysis kind that was run
  limitations   the standing line, plus a sentence per caveat chip the tools
                returned (code only; the model's own limitation sentences go
                to ``context_unchecked``)
  findings      ONLY sentences the code rendered from a closed template after
                checking the model's structured claim against the evidence
                (``research_claims``); state is always "verified"
  context_unchecked  everything else the model wrote that passed the lexical
                screen, labelled "Context (unchecked)", never counted as verified
  label         RECORDED or LIVE, model id, prompt id
  question      the user's own words, not the model's

The note holds findings and ids, never raw series; figures are drawn at export
time from the chart specs the analyses returned.
"""
from __future__ import annotations

import json

from .research_claims import catalogue
from .research_evidence import ResearchState, canonical, sha256_hex
from .research_verify import CONTEXT_CHIP, CONTEXT_LABEL, verify_note

PROMPT = "research_note"
STATS_LABEL = "Descriptive statistics on historical data. Associations, not causes. Not a forecast."
RECORDED_LABEL = "RECORDED SCRIPT (fixture transport, not a live model)"
MAX_PAYLOAD_CHARS = 60000

CHIP_LIMITS = {
    "insufficient_n": "The sample is too small for an interval.",
    "short_window": "The window is short.",
    "gaps_in_series": "The series has gaps; missing days are left empty.",
    "autocorrelated_daily_data": "Daily data are autocorrelated; intervals use block resampling.",
    "block_bootstrap": "Intervals use a moving-block bootstrap.",
    "multiple_lags_examined": "Several lags were examined; the peak is chosen from them.",
    "single_event": "One event only.",
    "few_events": "Few events.",
    "data_dependent_selection": "The reported value is selected from the data.",
    "modelled_atmosphere": "Weather values are modelled reanalysis, not station readings.",
    "modelled_count": "Scene counts are modelled detector output, not vessel counts.",
    "simulated": "Simulated values are shown beside, not merged with, observed values.",
    "estimate_not_observation": "Values are estimates, not observations.",
    "cloud_unusable_excluded": "Cloudy or no-data scenes are excluded.",
    "recorded_not_live": "Recorded replies, not live.",
    "no_control_series": "No control series.",
    "weekday_pattern_only": "The profile describes weekday pattern only.",
    "change_points_are_segmentations": "Change points are segmentations of the series.",
}
METHODS = {
    "catalog": "Dataset catalogue read as served.",
    "episodes": "Curated episode library read as served; each event carries a publisher and a date.",
    "assemble": "Series assembled from cached historical datasets over the stated window; missing days are left empty.",
    "event_study": "Event study: values indexed to the mean of the pre-event baseline window, averaged across events.",
    "before_after": "Before and after: statistics on either side of a split date within the stated window.",
    "lagged_correlation": "Lagged rank correlation (Spearman) over lags up to the stated maximum.",
    "change_points": "Change-point segmentation of the series (CUSUM or PELT-lite).",
    "distribution": "Distribution summary by group: counts, mean, spread and percentiles.",
    "seasonality": "Weekday and month profiles and autocorrelation of the series.",
    "model_overlay": "Model overlay: the simulated series shown beside the observed series; metrics describe overlap only.",
    "scene_scan": "Scene counts: detector output on listed scenes; scenes above the cloud or no-data limits are not counted.",
}


class NoteError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


# ------------------------------------------------------------- the payload
def build_payload(research: ResearchState) -> dict:
    ev = research.evidence.payload_view()
    payload = {"question": research.question[:600], "evidence": ev, "templates": catalogue()}
    if len(json.dumps(payload, default=str)) > MAX_PAYLOAD_CHARS:
        payload["evidence"] = research.evidence.payload_view(max_values=15)
    return payload


def source_signature(research: ResearchState, transport: str) -> str:
    rows = [(e["id"], e["args_sha256"], e["result_sha256"]) for e in research.evidence.items]
    return sha256_hex({"q": research.question, "e": rows, "t": transport})[:16]


# ---------------------------------------------------- deterministic parts
def data_used(evidence: list[dict]) -> list[dict]:
    """One row per assembled dataset (places merged), with the catalogue's
    title and licence when the catalogue was read."""
    cat: dict = {}
    for e in evidence:
        if e["kind"] == "catalog":
            cat.update((e.get("meta") or {}).get("datasets") or {})
    rows: dict[str, dict] = {}
    for e in evidence:
        if e["kind"] != "assemble":
            continue
        for key, s in ((e.get("meta") or {}).get("series") or {}).items():
            ds = s.get("dataset") or key
            r = rows.setdefault(ds, {
                "id": ds, "title": (cat.get(ds) or {}).get("title") or ds,
                "source": s.get("source") or (cat.get(ds) or {}).get("source"),
                "licence": (cat.get(ds) or {}).get("licence"), "unit": s.get("unit"),
                "places": [], "first_obs": s.get("first_obs"), "last_obs": s.get("last_obs"),
                "state": s.get("state"), "evidence": []})
            if s.get("place") and s["place"] not in r["places"]:
                r["places"].append(s["place"])
            for k, pick in (("first_obs", min), ("last_obs", max)):
                vals = [v for v in (r.get(k), s.get(k)) if v]
                r[k] = pick(vals) if vals else None
            if s.get("state") == "not_served":
                r["state"] = "not_served"
            if e["id"] not in r["evidence"]:
                r["evidence"].append(e["id"])
    for e in evidence:
        if e["kind"] == "scene_scan":
            rows.setdefault("scene_scan", {
                "id": "scene_scan", "title": "Satellite scene detections",
                "source": "Recorded detector replies on listed scenes", "licence": None,
                "unit": "objects found", "places": [e["args"].get("place")], "first_obs": None,
                "last_obs": None, "state": "recorded", "evidence": []})["evidence"].append(e["id"])
    return list(rows.values())


def methods_for(evidence: list[dict]) -> list[str]:
    kinds = []
    for e in evidence:
        if e["kind"] in METHODS and e["kind"] not in kinds:
            kinds.append(e["kind"])
    order = ["catalog", "episodes", "assemble"]
    first_seen = {k: i for i, k in enumerate(kinds)}
    ordered = sorted(kinds, key=lambda k: (order.index(k) if k in order else 3, first_seen[k]))
    return [METHODS[k] for k in ordered]


def limitations_for(evidence: list[dict], model_limits: list[str]) -> list[str]:
    out = [STATS_LABEL]
    chips = []
    for e in evidence:
        for c in e.get("caveat_chips") or []:
            if c not in chips:
                chips.append(c)
    for c in chips:
        out.append(CHIP_LIMITS.get(c, c.replace("_", " ").capitalize() + "."))
    for s in model_limits:
        if s not in out:
            out.append(s)
    return out[:14]


def verification_status(rep: dict) -> str:
    """VERIFIED only when at least one finding was proposed and every proposed
    finding was a structured claim the code bound to the evidence and rendered
    itself; anything else is UNVERIFIED."""
    ok = rep["proposed"] > 0 and rep["verified"] == rep["proposed"] \
        and not (rep["flagged"] or rep["rejected"] or rep["dropped_no_evidence"])
    return "VERIFIED" if ok else "UNVERIFIED"


def verification_chips(rep: dict) -> list[str]:
    chips = ["Verified" if verification_status(rep) == "VERIFIED" else "Unverified",
             f"{rep['verified']} of {rep['proposed']} findings checked"]
    if rep["flagged"]:
        chips.append(f"{rep['flagged']} flagged")
    if rep["rejected"]:
        chips.append(f"{rep['rejected']} removed")
    if rep["dropped_no_evidence"]:
        chips.append(f"{rep['dropped_no_evidence']} without evidence")
    if rep.get("unchecked"):
        chips.append(f"{rep['unchecked']} unchecked")
    return chips


# -------------------------------------------------------------- the call
def write_note(research: ResearchState, provider, *, force: bool = False) -> tuple[dict, bool]:
    """-> (note, cached). Calls the model at most once (not at all when the
    same evidence already has a note). Raises NoteError, or the provider's
    own errors (CapExceeded, ProviderDisabled, ...)."""
    transport = getattr(provider, "transport_name", "fixture")
    sig = source_signature(research, transport)
    with research.lock:
        if not force:
            for n in research.notes:
                if n.get("source_signature") == sig:
                    research.note_state = "ready"
                    return n, True
    evidence = research.evidence.dump()
    if not any(e["kind"] not in ("catalog", "episodes") for e in evidence):
        raise NoteError("no_evidence", "there is no analysis evidence to write a note from")
    payload = build_payload(research)
    resp = provider.complete(PROMPT, payload)
    try:
        raw = json.loads(resp["text"])
    except (ValueError, TypeError):
        raise NoteError("model_reply_not_json", "the model reply was not a JSON object") from None
    clean, rep = verify_note(raw, evidence, research.question)
    live = resp.get("transport") == "http"
    label = {"state": "LIVE" if live else "RECORDED",
             "text": ("Written by Meta AI; findings written by code from checked claims."
                      if live else RECORDED_LABEL),
             "statistics": STATS_LABEL, "model": resp.get("model"),
             "prompt_id": f"{PROMPT}@{resp.get('prompt_version')}"}
    note = {
        "title": clean["title"], "question": research.question[:600], "label": label,
        "data_used": data_used(evidence), "findings": clean["findings"],
        "context_unchecked": clean["context"] + [
            {"id": f"L{i}", "kind": "limitation", "text": t, "state": "unchecked", "evidence": [],
             "label": CONTEXT_LABEL, "chip": CONTEXT_CHIP} for i, t in enumerate(clean["limitations"], 1)],
        "limitations": limitations_for(evidence, []),
        "methods": methods_for(evidence),
        "evidence_ids": [e["id"] for e in evidence],
        "verification": {**rep, "status": verification_status(rep),
                         "chips": verification_chips(rep)},
        "model_call": {"transport": resp.get("transport"), "model": resp.get("model"),
                       "prompt_version": resp.get("prompt_version"),
                       "cost_usd": resp.get("cost_usd", 0.0),
                       "estimated_cost_usd": resp.get("estimated_cost_usd", 0.0),
                       "tokens_in": resp.get("tokens_in"), "tokens_out": resp.get("tokens_out"),
                       "structured_output": bool(resp.get("structured_output"))},
        "source_signature": sig,
    }
    note["id"] = "rn_" + sha256_hex({k: v for k, v in note.items() if k != "model_call"})[:12]
    with research.lock:
        research.notes = [n for n in research.notes if n["id"] != note["id"]] + [note]
        research.notes = research.notes[-5:]
        research.note_state, research.note_error = "ready", None
    return note, False


def note_digest(note: dict, cached: bool) -> dict:
    """What the model sees after write_research_note: counts, never text."""
    v = note["verification"]
    return {"note_id": note["id"], "title": note["title"], "label": note["label"]["state"],
            "status": v.get("status"), "proposed": v["proposed"], "verified": v["verified"], "flagged": v["flagged"],
            "rejected": v["rejected"], "dropped_no_evidence": v["dropped_no_evidence"],
            "unchecked": v.get("unchecked", 0),
            "findings": [{"id": f["id"], "state": f["state"], "evidence": f["evidence"]}
                         for f in note["findings"]],
            "cached": cached,
            "cost_usd": 0.0 if cached else note["model_call"].get("cost_usd", 0.0)}


def write_for_tool(research: ResearchState, provider, ctx) -> dict:
    try:
        note, cached = write_note(research, provider)
    except NoteError as e:
        from .tools import ToolError
        raise ToolError(e.code, e.message) from None
    return note_digest(note, cached)


def public_note(note: dict, research: ResearchState) -> dict:
    """The note as the API serves it: with the compact evidence table."""
    ids = set(note.get("evidence_ids") or [])
    table = [e for e in research.evidence.public() if e["id"] in ids]
    return {**{k: v for k, v in note.items() if k != "source_signature"}, "evidence": table}
