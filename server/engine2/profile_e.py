"""ENGINE REV 2 · PROFILE e: profile d with the Panama booked-slot tier replaced by reservation-order slots.

``Engine2(graph, profile=ProfileE(...))``. Everything is inherited from ``ProfileD`` (calibration, sea-lane
geometry, rotation coupling, route choice, convoy and default gates); the only difference is the gate
spec registry (plugins_e.E_PLUGINS) and the discipline ``slot_reserved``, which sets the same per-place
arrays as ``slot_booked`` plus ``p_fifo`` (the wait is read on the whole slot count, no strict priority).
No place id is written in this file: the ids live in the registry's specs.

Pure function of (calibration, sea routes, rotation zeta, registry).
"""
from __future__ import annotations

from engine.canonical import canonical_json, sha256_hex

from .plugins_e import E_HASH, E_PLUG, PROFILE_KEY, PROFILE_LABEL
from .profile_d import ProfileD


class ProfileE(ProfileD):
    key = PROFILE_KEY
    label = PROFILE_LABEL
    hash = E_HASH

    def __init__(self, calibration=None, sea=None, zeta: dict | None = None, ablate=(),
                 specs: list | None = None):
        super().__init__(calibration=calibration, sea=sea, zeta=zeta, ablate=ablate, specs=specs)
        # ``specs`` replaces the registry's gate specs, as for profile d
        self.specs = list(specs) if specs is not None else list(E_PLUG("gate_specs"))
        self.digest = sha256_hex(canonical_json(
            [self.sea.digest(), sorted(self.zeta.items()), self.cal.digest, E_HASH,
             sorted(self.ablate), self.specs if specs is not None else None]))

    def stamp(self) -> dict:
        return {"engine_rev": 2, "engine_label": PROFILE_LABEL, "engine2_registry_hash": E_HASH,
                "profile": PROFILE_KEY, "sea_routes_sha256": self.sea.content_sha256}

    @staticmethod
    def _gate_slot_reserved(eng, i, params):
        eng.p_slot[i] = True
        eng.p_tier[i] = float(params["booked_share"])
        eng.p_fifo[i] = True
