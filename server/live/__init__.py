"""LIVE TERMINAL STREAM backend.

An in-memory, bounded live state hub fed from the adapters' latest
records, a Server-Sent Events stream, a pull snapshot, and a bounded
vessel / aircraft track store with a spatial query.

Doctrine: provenance {source, retrieved_at, mode} on every record;
LIVE only when the feed truly answered recently (stale = SNAPSHOT);
a section whose feed delivers nothing is NOT SERVED, never faked or
backfilled; the whole surface is gated by SUPERMAP_LIVE_TWIN (flag
off => every /live/* route 404s). No LLM in this path.
"""
