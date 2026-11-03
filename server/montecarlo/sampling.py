"""Request parsing, input distributions and the seeded sampler.

Sampling lives OUTSIDE the engine. One private ``random.Random(seed)`` per
job; for every simulation ONE ``random()`` draw per input in the fixed order
INPUT_ORDER, whether or not the input varies (so holding one input fixed
never shifts another input's stream). Inverse-CDF transforms below use only
+ - * / and sqrt, so the values are identical on every platform.
"""
from __future__ import annotations

import math
import random

try:  # dual-import convention, as in server/api/app.py
    from engine.constants import const
    from engine.engine import PARAM_BOUNDS, EngineError, validate_disruption
    from engine.ensemble import DURATION_MULTS, QS_GRID
except Exception:  # imported as server.montecarlo.* from the repo root
    from server.engine.constants import const  # type: ignore
    from server.engine.engine import PARAM_BOUNDS, EngineError, validate_disruption  # type: ignore
    from server.engine.ensemble import DURATION_MULTS, QS_GRID  # type: ignore

INPUT_ORDER = ("duration_d", "capacity_loss", "queue_sensitivity")
# Named in the contract, but the engine has no per-run parameter for them.
NOT_EXPOSED = ("base_speed_factor", "queue_service_rate", "demand_factor")
DISTS = ("triangular", "uniform", "fixed")
N_MIN, N_MAX_HARD, N_DEFAULT = 20, 1000, 200
SEED_LIMIT = 2 ** 53
MAX_DEPTH = 8
TOP_KEYS = frozenset({"disruption", "engine_rev", "profile", "n", "seed", "distributions"})
DIS_KEYS = ("node_id", "kind", "capacity_loss", "duration_d", "shape", "started_at", "detected_at")
CAPACITY_SPREAD = 0.25


class MCError(EngineError):
    """Validation / service error; ``code`` maps to the house error shape."""

    def __init__(self, message: str, code: str = "invalid_request"):
        super().__init__(message, code=code)


def _bounds() -> dict:
    return {
        "duration_d": (0.0, float(const("duration_max_d")), True),     # (lo, hi], lo exclusive
        "capacity_loss": (0.0, 1.0, True),
        "queue_sensitivity": (float(PARAM_BOUNDS["queue_sensitivity"][0]),
                              float(PARAM_BOUNDS["queue_sensitivity"][1]), False),
    }


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _num(v, field: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise MCError(f"{field} must be a number, got {type(v).__name__}")
    f = float(v)
    if not math.isfinite(f):
        raise MCError(f"{field} must be finite")
    return f


def _depth_ok(obj, limit: int = MAX_DEPTH) -> bool:
    stack = [(obj, 1)]
    while stack:
        cur, d = stack.pop()
        if isinstance(cur, (dict, list)):
            if d > limit:
                return False
            kids = cur.values() if isinstance(cur, dict) else cur
            stack.extend((k, d + 1) for k in kids)
    return True


def _r6(x: float) -> float:
    return round(float(x), 6)


def default_inputs(disruption: dict) -> dict:
    """The default range of each input, from the engine's own named variants
    (DURATION_MULTS, QS_GRID) and a plus or minus 25% rule for capacity loss."""
    dur = float(disruption["duration_d"])
    dmax = float(const("duration_max_d"))
    d_lo, d_hi = dur * min(DURATION_MULTS), min(dur * max(DURATION_MULTS), dmax)
    loss = float(disruption["capacity_loss"])
    if disruption["kind"] == "closure":
        cap = {"dist": "fixed", "low": loss, "mode": loss, "high": loss}
    else:
        cap = {"dist": "triangular", "low": loss * (1 - CAPACITY_SPREAD), "mode": loss,
               "high": min(1.0, loss * (1 + CAPACITY_SPREAD))}
    q_lo, q_hi = min(QS_GRID), max(QS_GRID)
    q_mode = float(const("queue_sensitivity_default"))
    out = {
        "duration_d": {"dist": "triangular", "low": d_lo, "mode": dur, "high": d_hi},
        "capacity_loss": cap,
        "queue_sensitivity": {"dist": "triangular", "low": q_lo, "mode": q_mode, "high": q_hi},
    }
    for spec in out.values():
        for k in ("low", "mode", "high"):
            spec[k] = _r6(spec[k])
        if spec["dist"] == "triangular" and not spec["low"] < spec["high"]:
            spec.update(dist="fixed", low=spec["mode"], high=spec["mode"])
        spec["source"] = "default"
    return out


def _parse_dist(name: str, raw, disruption: dict) -> dict:
    if not isinstance(raw, dict):
        raise MCError(f"distributions.{name} must be an object")
    extra = set(raw) - {"dist", "low", "mode", "high"}
    if extra:
        raise MCError(f"distributions.{name}: unknown keys {sorted(extra)[:4]}")
    kind = raw.get("dist")
    if not isinstance(kind, str) or kind not in DISTS:
        raise MCError(f"distributions.{name}.dist must be one of {list(DISTS)}")
    lo_b, hi_b, lo_excl = _bounds()[name]

    def inside(v: float, what: str) -> float:
        if (v <= lo_b if lo_excl else v < lo_b) or v > hi_b:
            raise MCError(
                f"distributions.{name}.{what}={v!r} out of bounds "
                f"{'(' if lo_excl else '['}{lo_b}, {hi_b}] (rejected, never clamped)",
                code="param_out_of_bounds")
        return v

    if kind == "fixed":
        raw_mode = raw.get("mode", raw.get("low", raw.get("high")))
        if raw_mode is None:
            raise MCError(f"distributions.{name}: fixed needs mode")
        v = inside(_num(raw_mode, f"distributions.{name}.mode"), "mode")
        for k in ("low", "high"):
            if raw.get(k) is not None and _num(raw[k], f"distributions.{name}.{k}") != v:
                raise MCError(f"distributions.{name}: fixed needs low = mode = high")
        out = {"dist": "fixed", "low": v, "mode": v, "high": v}
    else:
        if raw.get("low") is None or raw.get("high") is None:
            raise MCError(f"distributions.{name}: {kind} needs low and high")
        lo = inside(_num(raw["low"], f"distributions.{name}.low"), "low")
        hi = inside(_num(raw["high"], f"distributions.{name}.high"), "high")
        if not lo < hi:
            raise MCError(f"distributions.{name}: low must be below high")
        if kind == "uniform":
            mode = (lo + hi) / 2.0
        else:
            if raw.get("mode") is None:
                raise MCError(f"distributions.{name}: triangular needs mode")
            mode = inside(_num(raw["mode"], f"distributions.{name}.mode"), "mode")
            if not lo <= mode <= hi:
                raise MCError(f"distributions.{name}: need low <= mode <= high")
        out = {"dist": kind, "low": lo, "mode": mode, "high": hi}
    out = {k: (_r6(v) if k != "dist" else v) for k, v in out.items()}
    if out["dist"] != "fixed" and not out["low"] < out["high"]:
        raise MCError(f"distributions.{name}: range is too narrow at 6 decimals")
    out["source"] = "request"
    if name == "capacity_loss" and disruption["kind"] == "closure" and not (
            out["dist"] == "fixed" and out["mode"] == float(disruption["capacity_loss"])):
        raise MCError("capacity_loss is fixed at 1.0 for kind=closure; it cannot be sampled")
    return out


def resolve_inputs(disruption: dict, requested) -> list:
    """[{name, dist, low, mode, high, source}] in INPUT_ORDER."""
    if requested is None:
        requested = {}
    if not isinstance(requested, dict):
        raise MCError("distributions must be an object")
    for name in requested:
        if name in NOT_EXPOSED:
            raise MCError(
                f"input {name!r} is not exposed by the engine at run time and cannot be "
                f"sampled; sampleable inputs: {list(INPUT_ORDER)}", code="input_not_exposed")
        if name not in INPUT_ORDER:
            raise MCError(f"unknown input {str(name)[:40]!r}; sampleable inputs: "
                          f"{list(INPUT_ORDER)}", code="unknown_axis")
    defaults = default_inputs(disruption)
    out = []
    for name in INPUT_ORDER:
        spec = _parse_dist(name, requested[name], disruption) if name in requested \
            else defaults[name]
        out.append({"name": name, **spec})
    return out


def parse_request(body, core_graph, *, max_n: int, engine_revs_available=(1, 2)) -> dict:
    """The validated request: {disruption, engine_rev, profile, n, seed, inputs}.
    Raises MCError (an EngineError) with the house error codes."""
    if not isinstance(body, dict):
        raise MCError("request body must be an object")
    if not _depth_ok(body):
        raise MCError(f"request nests deeper than {MAX_DEPTH} levels")
    extra = set(body) - TOP_KEYS
    if extra:
        raise MCError(f"unknown request keys: {sorted(str(k)[:30] for k in extra)[:4]}")
    rev = body.get("engine_rev", 1)
    rev = 1 if rev is None else rev
    if not _is_int(rev) or rev not in (1, 2):
        raise MCError("engine_rev must be 1 or 2")
    if rev not in engine_revs_available:
        raise MCError(f"engine_rev {rev} is unavailable")
    profile = body.get("profile")
    if profile is not None:
        if not isinstance(profile, str) or profile not in ("c", "d", "e"):
            raise MCError("profile must be 'c', 'd' or 'e'")
        if rev != 2:
            raise MCError("profile requires engine_rev 2")
    n = body.get("n", N_DEFAULT)
    if not _is_int(n):
        raise MCError(f"n must be an integer in [{N_MIN}, {N_MAX_HARD}]")
    if n < N_MIN or n > N_MAX_HARD:
        raise MCError(f"n must be an integer in [{N_MIN}, {N_MAX_HARD}]")
    if n > max_n:
        raise MCError(f"n={n} is above this deployment's cap of {max_n} "
                      "(SUPERMAP_MC_MAX_N)", code="montecarlo_too_large")
    seed = body.get("seed", 1)
    if not _is_int(seed) or abs(seed) > SEED_LIMIT:
        raise MCError(f"seed must be an integer with |seed| <= 2**53")
    seed = abs(seed)          # random.Random(-5) and Random(5) are the same stream: one job, not two
    raw = body.get("disruption")
    if not isinstance(raw, dict):
        raise MCError("disruption is required and must be an object")
    raw = {k: raw[k] for k in raw if k in DIS_KEYS or k in ("place_id", "place")}
    for alias in ("place_id", "place"):
        if alias in raw:
            val = raw.pop(alias)
            if raw.get("node_id") is not None and raw["node_id"] != val:
                raise MCError(f"disruption.{alias} conflicts with node_id")
            raw["node_id"] = val
    spec = validate_disruption(core_graph, raw)       # the same code /run uses
    inputs = resolve_inputs(spec, body.get("distributions"))
    return {"disruption": spec, "engine_rev": rev, "profile": profile,
            "n": n, "seed": seed, "inputs": inputs}


# ------------------------------------------------------------------ sampler
def _draw(spec: dict, u: float) -> float:
    lo, mode, hi = spec["low"], spec["mode"], spec["high"]
    if spec["dist"] == "fixed" or not lo < hi:
        return mode
    if spec["dist"] == "uniform":
        v = lo + (hi - lo) * u
    else:
        c = (mode - lo) / (hi - lo)
        if u < c:
            v = lo + math.sqrt(u * (hi - lo) * (mode - lo))
        else:
            v = hi - math.sqrt((1.0 - u) * (hi - lo) * (hi - mode))
    return min(max(_r6(v), lo), hi)


def sample_inputs(rng: random.Random, inputs: list) -> dict:
    """One simulation's input values; exactly one rng.random() per input."""
    return {spec["name"]: _draw(spec, rng.random()) for spec in inputs}


def make_rng(seed: int) -> random.Random:
    return random.Random(seed)
