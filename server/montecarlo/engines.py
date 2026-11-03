"""Engine selection for Monte Carlo runs: the same public entry points the
rest of the API uses (rev 1 ``Engine``, rev 2 ``Engine2``, profile c / d / e).
Nothing here edits or wraps engine behaviour; it only picks the engine and
reports its identity (label and registry hash) for the job id.
"""
from __future__ import annotations

import threading

try:  # dual-import convention, as in server/api/app.py
    from engine.constants import CONSTANTS_HASH
    from engine.engine import Engine
except Exception:  # imported as server.montecarlo.* from the repo root
    from server.engine.constants import CONSTANTS_HASH  # type: ignore
    from server.engine.engine import Engine  # type: ignore

REV1_LABEL = "ENGINE REV 1"

_LOCK = threading.Lock()
_CAL: dict = {}


def engine_key(engine_rev: int, profile) -> str:
    return "1" if engine_rev == 1 else ("2" + (profile or ""))


def _rev2_modules():
    try:
        from engine2 import Engine2
        from engine2.calibration import default_calibration
        from engine2.journal import get_engine2, get_profile_engine2, profile_stamp, rev_stamp
        from engine2.plugins_cal import CAL_HASH, PROFILE_LABEL
    except Exception:  # imported as server.montecarlo.* from the repo root
        from server.engine2 import Engine2  # type: ignore
        from server.engine2.calibration import default_calibration  # type: ignore
        from server.engine2.journal import (get_engine2, get_profile_engine2,  # type: ignore
                                            profile_stamp, rev_stamp)
        from server.engine2.plugins_cal import CAL_HASH, PROFILE_LABEL  # type: ignore
    return Engine2, default_calibration, get_engine2, get_profile_engine2, profile_stamp, \
        rev_stamp, CAL_HASH, PROFILE_LABEL


def rev2_available() -> bool:
    try:
        _rev2_modules()
        return True
    except Exception:
        return False


def resolve(graph, engine_rev: int, profile):
    """(engine, identity) where identity is a JSON-able dict that pins which
    engine the draws run through (folded into the job id)."""
    if engine_rev == 1:
        return Engine(graph), {"engine_rev": 1, "engine_label": REV1_LABEL,
                               "constants_hash": CONSTANTS_HASH}
    (Engine2, default_calibration, get_engine2, get_profile_engine2, profile_stamp,
     rev_stamp, cal_hash, cal_label) = _rev2_modules()
    if profile is None:
        return get_engine2(graph), dict(rev_stamp())
    if profile == "c":
        cal = default_calibration()
        with _LOCK:
            hit = _CAL.get(id(graph))
            if hit is None or hit[0] is not graph or hit[2] != cal.digest:
                _CAL.clear()
                hit = (graph, Engine2(graph, calibration=cal), cal.digest)
                _CAL[id(graph)] = hit
        return hit[1], {**rev_stamp(), "engine_label": cal_label, "profile": "c",
                        "registry_hash": cal_hash, "calibration_digest": cal.digest}
    eng = get_profile_engine2(graph, None, profile)
    return eng, dict(profile_stamp(eng))
