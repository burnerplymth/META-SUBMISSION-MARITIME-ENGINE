"""SupermAP deterministic propagation engine.

No RNG anywhere in this package: propagation is a pure function of
(graph, disruption spec, params). Same inputs -> byte-identical event stream.
"""
from .canonical import canonical_json, stream_hash, sha256_hex
from .graph import Graph, GraphError, load_graph
from .engine import Engine, EngineError, DEFAULT_PARAMS, PARAM_BOUNDS, run_scenario
from .fork import fork_events, divergence
from .ensemble import run_ensemble
from .backtest import run_backtest
from .lens import LensError, apply_lens, get_lens, lens_ensemble_stats, list_lenses

__all__ = [
    "canonical_json", "stream_hash", "sha256_hex",
    "Graph", "GraphError", "load_graph",
    "Engine", "EngineError", "DEFAULT_PARAMS", "PARAM_BOUNDS", "run_scenario",
    "fork_events", "divergence", "run_ensemble", "run_backtest",
    "LensError", "apply_lens", "get_lens", "lens_ensemble_stats", "list_lenses",
]
