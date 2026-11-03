"""SupermAP money layer.

QUARANTINE: nothing in
this package is an engine input. Market data is consumed by the UI money
panel and by reports only; no run journal may reference it. The modules
here import nothing from server/engine or server/api.

Provenance discipline mirrors the adapter framework: every externally-sourced record
carries {source, retrieved_at, mode}, mode in {LIVE, SNAPSHOT, MODELED}.
A number that cannot produce its provenance is not shipped.
"""
