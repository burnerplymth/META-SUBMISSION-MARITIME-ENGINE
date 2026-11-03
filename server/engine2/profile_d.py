"""ENGINE REV 2 · PROFILE d: the object an Engine2 consults (``Engine2(graph, profile=ProfileD(...))``).

It carries the rev2c calibration, the sea-lane geometry, the rotation coupling, and applies the
registry's data to the engine's per-place arrays (``attach``):

  gate specs      every place gets the default gate; a spec whose ``match`` selects it overrides the
                  discipline: ``fifo_bounded`` (spill-back of exogenous traffic), ``slot_booked``
                  (fixed slots, booked / unbooked), ``convoy`` (departure windows). No place id is
                  written in this file or in engine.py: the ids live in the registry's specs;
  demand response the logistic scale of the route-choice rule;
  rotation        zeta per leg and the recovery fraction.

Pure function of (calibration, sea routes, rotation zeta, registry).
"""
from __future__ import annotations

from engine.canonical import canonical_json, sha256_hex

from .calibration import default_calibration
from .plugins_d import D_HASH, D_PLUG, PROFILE_KEY, PROFILE_LABEL
from .sea_routes import load_sea_routes, retime_graph


def _selects(match: dict, node_id: str) -> bool:
    if match.get("all"):
        return True
    return node_id in set(match.get("ids", ()))


class ProfileD:
    key = PROFILE_KEY
    label = PROFILE_LABEL
    hash = D_HASH

    ABLATIONS = ("retime", "sourced_gates", "spill", "graded_choice", "rotation",
                 "alternatives", "alternatives_conditional", "substitution")

    def __init__(self, calibration=None, sea=None, zeta: dict | None = None, ablate=(),
                 specs: list | None = None):
        """``ablate`` (analysis only, never journaled by the API): names from ABLATIONS that switch one
        mechanism of the profile off, for the leave-one-out tables. The empty default is the profile."""
        bad = sorted(set(ablate) - set(self.ABLATIONS))
        if bad:
            raise ValueError(f"unknown ablation: {bad}")
        self.ablate = frozenset(ablate)
        # ``specs``: gate specs other than the registry's, e.g. on a synthetic graph
        self.specs = list(specs) if specs is not None else list(D_PLUG("gate_specs"))
        self.cal = calibration if calibration is not None else default_calibration()
        self.sea = sea if sea is not None else load_sea_routes()
        self.zeta = {} if "rotation" in self.ablate else dict(zeta or {})
        self.digest = sha256_hex(canonical_json(
            [self.sea.digest(), sorted(self.zeta.items()), self.cal.digest, D_HASH,
             sorted(self.ablate), self.specs if specs is not None else None]))

    # ---- graph ------------------------------------------------------------
    def retime(self, graph):
        return graph if "retime" in self.ablate else retime_graph(graph, self.sea)

    # ---- journal ----------------------------------------------------------
    def stamp(self) -> dict:
        return {"engine_rev": 2, "engine_label": PROFILE_LABEL, "engine2_registry_hash": D_HASH,
                "profile": PROFILE_KEY, "sea_routes_sha256": self.sea.content_sha256}

    # ---- engine arrays ----------------------------------------------------
    def attach(self, eng) -> None:
        n = len(eng.ids)
        for spec in self.specs:
            if "sourced_gates" in self.ablate and spec["match"].get("all") is not True:
                continue
            for i, nid in enumerate(eng.ids):
                if not _selects(spec["match"], nid):
                    continue
                getattr(self, "_gate_" + spec["discipline"])(eng, i, spec["params"])
        eng.p_div = (0.0 if "graded_choice" in self.ablate
                     else float(D_PLUG("diversion_logistic")["scale_d"]))
        if "spill" in self.ablate:
            eng.p_spill = [False] * n
        # rotation coupling: zeta per primary leg, coupled throughput per place
        eng.p_zeta = [float(self.zeta.get(k, 0.0)) for k in eng.leg_key]
        for i in range(n):
            cc = sum(eng.p_zeta[lg] * eng.V[lg] for lg in eng.in_prim[i])
            eng.p_cc[i] = min(cc, eng.lam0[i])
        eng.p_rho = float(D_PLUG("rotation_recovery")["absorbed_fraction_per_call"])

    # ---- gate disciplines (selected by spec, applied per place) ----------------
    @staticmethod
    def _gate_fifo_bounded(eng, i, params):
        eng.p_spill[i] = bool(params.get("spill_exogenous", False))

    @staticmethod
    def _gate_slot_booked(eng, i, params):
        eng.p_slot[i] = True
        eng.p_tier[i] = float(params["booked_share"])

    @staticmethod
    def _gate_convoy(eng, i, params):
        node = eng.graph.nodes[eng.ids[i]]
        wn = ws = 0.0
        for lg in eng.in_prim[i]:
            sender = eng.graph.nodes[eng.ids[eng.leg_p[lg]]]
            if float(sender["lat"]) < float(node["lat"]):
                wn += eng.V[lg]           # arrives from the south: northbound
            else:
                ws += eng.V[lg]
        tot = wn + ws
        w_nb, w_sb = ((wn / tot, ws / tot) if tot > 0 else (1 / 2, 1 / 2))
        mix = []
        if w_nb > 0:
            mix.append((w_nb, float(params["northbound"])))
        if w_sb > 0:
            mix.append((w_sb, float(params["southbound"])))
        eng.p_conv[i] = mix
