"""SupermAP ingestion adapters."""
from .base import (AdapterError, BaseAdapter, FetchResult, NeedsKeyError,
                   provenance, utcnow_iso)
from .registry import (all_adapters, build_default_registry, get,
                       health_report, register, register_health_only)

__all__ = [
    "AdapterError", "BaseAdapter", "FetchResult", "NeedsKeyError",
    "all_adapters", "build_default_registry", "get", "health_report",
    "provenance", "register", "register_health_only", "utcnow_iso",
]
