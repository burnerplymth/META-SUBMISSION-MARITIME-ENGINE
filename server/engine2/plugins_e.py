"""ENGINE REV 2 · PROFILE e (``rev2e``) plug-in registry.

Profile e = profile d with ONE gate rule replaced: the Panama booked-slot tier.

In profile d (plugins_d.acp_panama_booked_slots) booked vessels are served at once at a strict
priority, the booked rate being ``booked_share x min(arrivals, slot count)``. Whatever arrives above the
slot count is therefore counted as UNBOOKED and drains only through the residual slots
(slot count - booked rate = 5.4 % of the slot count, about 0.97 vessels a day against 18 slots). In a long
partial cut the unbooked backlog never reaches the front of that queue until the cut ends, so the
reported root wait is the length of the cut less the days it takes the backlog to form (203.7 d for a
215 d cut). This is an arithmetic artefact of the tier, not a property of the canal: the same demand
and cut without the tier give 87.9 d.

Profile e keeps the sourced slot count and the sourced booked share, and removes the strict priority:

  * the service rate in a partial cut is the slot count (lambda0 x (1 - loss)), as in d;
  * slots are allocated in reservation order, so the whole queue is served at the slot count and the
    wait is backlog / slot count (a vessel without a reservation takes the next free reservation);
  * a reservation holder is committed: only the arrivals above the reservation count
    (booked_share x min(arrivals, slot count)) respond to the wait by diverting, as in d.

Every other mechanism, number and spec of profile d is carried over unchanged, and the profile d registry
(plugins_d.py, hash ``D_HASH``), the rev2c registry and the default ENGINE REV 2 registry are NOT edited.
A rev2e run is stamped with this registry's hash and the label ``ENGINE REV 2 · PROFILE e``.

Status vocabulary as in plugins.py: SOURCED / CAL / CONVENTION / TEMPORARY / NUMERICAL.
"""
from __future__ import annotations

import copy

from engine.canonical import canonical_json, sha256_hex

from .plugins import ENGINE_REV
from .plugins_d import D_PLUGINS, D_VERSION, _entry

PROFILE = "rev2e"
PROFILE_KEY = "e"
PROFILE_LABEL = "ENGINE REV 2 · PROFILE e"
E_VERSION = "2.4.0"

_OVERRIDE: dict = {}
_NEW: list = []

_D_BY_NAME = {p["name"]: p for p in D_PLUGINS}


def _panama_spec(d_spec: dict) -> dict:
    return {
        "id": "acp_panama_reserved_slots", "match": d_spec["match"],
        "discipline": "slot_reserved",
        "params": dict(d_spec["params"]),
        "rule": "(1) during an impairment the service rate is the slot count lambda0 x (1 - loss), capped at "
                "mu_max (as in profile d); (2) while capacity is partly cut (0 < loss < 1) slots are "
                "allocated in RESERVATION ORDER: there is no strict priority of the booked class over the "
                "queue, so the whole queue is served at the slot count and the wait is backlog / slot "
                "count (a vessel without a reservation takes the next free reservation); (3) a vessel "
                "that already holds a reservation is committed and does not divert: the committed rate is "
                "booked_share x min(arrivals, slot count), only the rest of the arrivals respond to the "
                "wait (the profile d split, unchanged). A full closure and an open gate are plain FIFO",
        "source": d_spec["source"] + ". Profile e keeps this slot count and this booked share unchanged; "
                "what it removes is profile d's reading of the share as a strict priority that leaves "
                "everything above the slot count to the residual slots. Nothing in the cited "
                "advisory states a strict priority of booked over unbooked transits at the canal, and the "
                "advisory's non-booked wait series was not fetched, so the choice between the two "
                "readings is a modelling convention, reported beside both results",
    }


_OVERRIDE["gate_specs"] = {
    **_D_BY_NAME["gate_specs"],
    "value": [_panama_spec(s) if s["id"] == "acp_panama_booked_slots" else copy.deepcopy(s)
              for s in _D_BY_NAME["gate_specs"]["value"]],
    "where_used": _D_BY_NAME["gate_specs"]["where_used"] + "; profile_e.ProfileE (the Panama entry has "
                  "discipline slot_reserved)",
}

_NEW.append(_entry(
    "slot_queue_rule",
    {"rule": "slot_reserved: wait = backlog / slot count; committed (reservation) rate = booked_share x "
             "min(arrivals, slot count) does not divert; no strict priority",
     "replaces": "profile d slot_booked: wait = backlog / (slot count - booked rate), booked rate served at "
                 "once"},
    "rule",
    "engine.py p_fifo hook (wait read on the whole service rate; the FIFO wait series of the place is not "
    "reduced by the booked rate); profile_e.ProfileE._gate_slot_reserved",
    "profile d's tier gives the whole backlog above the slot count to the residual slots (5.4 % of the slot "
    "count) and reports a root wait of about the length of the cut (203.7 d for 215 d). That "
    "is the arithmetic of the tier: the same cut without it gives 87.9 d",
    "CONVENTION",
    "modelling choice, not a sourced regime. Sourced inputs kept from profile d: slot count (ACP Advisory "
    "A-04-2024), booked share 0.9459 (702 transits, 664 booked, January 2024; ambiguity disclosed in the "
    "spec). Not fetched and therefore not used: an ACP series of non-booked waits",
    "ACP daily booked and unbooked transit and wait series (monthly operations summaries)"))


def _compose() -> list:
    out = []
    for p in D_PLUGINS:
        out.append(dict(_OVERRIDE.get(p["name"], p)))
    out.extend(dict(p) for p in _NEW)
    return out


E_PLUGINS = _compose()
E_BY_NAME = {p["name"]: p for p in E_PLUGINS}
CHANGED = sorted(set(_OVERRIDE) | {p["name"] for p in _NEW})


def E_PLUG(name):
    return E_BY_NAME[name]["value"]


def registry() -> dict:
    return {"engine_rev": ENGINE_REV, "label": PROFILE_LABEL, "profile": PROFILE,
            "version": E_VERSION, "based_on": {"profile": "rev2d", "version": D_VERSION},
            "plugins": E_PLUGINS}


E_HASH = sha256_hex(canonical_json(registry()))
