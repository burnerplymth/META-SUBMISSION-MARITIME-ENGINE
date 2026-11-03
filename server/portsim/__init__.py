"""PORT TWIN: deterministic discrete-event port operations simulation.

A package beside server/ports (the tracker); it reads the graph, the
PortWatch daily snapshot and port profiles, and never writes to them.
"""
from .registry import MODEL_LABEL, MODEL_VERSION, REGISTRY_HASH

__all__ = ["MODEL_LABEL", "MODEL_VERSION", "REGISTRY_HASH"]
