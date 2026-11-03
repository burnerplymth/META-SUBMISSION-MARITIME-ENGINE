"""Public API surface.

Helpers for the read-only, keyless public namespace ``/api/v1/`` wired
in ``server/api/app.py``:

* ``TokenBucketLimiter`` — a simple in-process per-IP token bucket.
  Production defaults (stated in the public docs page): burst capacity
  120 requests per IP, refilled at 2 requests/second (≈120 requests
  per minute sustained). Generous enough to explore every
  endpoint repeatedly, strict enough that scripted bulk scraping is
  throttled with a 429 in the standard error shape. The
  limits are read from the environment at ``create_app()`` time —
  ``SUPERMAP_API_V1_BURST`` and ``SUPERMAP_API_V1_REFILL_PER_SEC`` —
  so a tiny burst (e.g. 3) can be set without touching production
  defaults; ``create_app()`` keyword arguments override both.

* ``public_ledger_view`` — the JSON projection of the ledger with
  exactly the content rules of the public ledger page
  (``GET /llm/ledger/public``, rendered by ``server/llm/ledger_page.py``):
  the page shows, per entry, only entry_id / locked_at / disruption_id /
  run_id / supersedes, per predicted step only seq / node_id / channel /
  delay_d / at, and per grade only graded_at / hit_steps / missed_steps /
  ungradeable_steps / delay_error_d / evidence (source / retrieved_at /
  mode). Internal event fields (parent_id, mechanism, magnitude, tick, the
  per-event run_id, …) are NOT part of the public view and are stripped
  here — this projection is the single place that stripping happens for
  the JSON surface, applied to the same ``LedgerStore.entries()`` merged
  read view the page renders from (no forked data logic).

* ``DOCS_HTML`` — the self-contained docs page served at
  ``GET /api/v1/docs`` (no external assets, no scripts).
"""
from __future__ import annotations

import threading
import time

# Production rate-limit defaults (see module docstring + docs page).
DEFAULT_BURST = 120
DEFAULT_REFILL_PER_SEC = 2.0


# The bucket map is bounded — a flood of distinct keys (e.g.
# spoofed forwarding headers) must not grow it without limit.
MAX_BUCKET_KEYS = 5000
BUCKET_IDLE_TTL_S = 600.0


class TokenBucketLimiter:
    """Per-key token bucket: ``burst`` tokens, refilled continuously at
    ``refill_per_sec`` tokens/second. State is in-process only (per API
    process, per client IP) — a restart resets every bucket, which is
    the documented behaviour for this keyless public surface.
    Access is serialised by a lock (handlers run on multiple threads),
    and the map is capped at MAX_BUCKET_KEYS entries: on overflow,
    entries idle longer than BUCKET_IDLE_TTL_S are evicted first,
    then the least-recently-used entries."""

    def __init__(self, burst: int = DEFAULT_BURST,
                 refill_per_sec: float = DEFAULT_REFILL_PER_SEC):
        self.burst = max(1, int(burst))
        self.refill_per_sec = max(0.0, float(refill_per_sec))
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def _evict_locked(self, now: float) -> None:
        if len(self._buckets) < MAX_BUCKET_KEYS:
            return
        idle = [k for k, (_tok, last) in self._buckets.items()
                if now - last > BUCKET_IDLE_TTL_S]
        for k in idle:
            del self._buckets[k]
        while len(self._buckets) >= MAX_BUCKET_KEYS:
            oldest = min(self._buckets, key=lambda k: self._buckets[k][1])
            del self._buckets[oldest]

    def allow(self, key: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            if key not in self._buckets:
                self._evict_locked(now)
            tokens, last = self._buckets.get(key, (float(self.burst), now))
            tokens = min(float(self.burst),
                         tokens + max(0.0, now - last) * self.refill_per_sec)
            if tokens >= 1.0:
                self._buckets[key] = (tokens - 1.0, now)
                return True
            self._buckets[key] = (tokens, now)
            return False


def public_ledger_view(entries: list[dict], accuracy: dict | None) -> dict:
    """Project merged LedgerStore entries to the public page's fields.

    Mirrors server/llm/ledger_page.render_page field-for-field: the
    grading rule is published verbatim on the page, and the LEDGER
    accuracy line (never the BACKTEST figure) is carried as ``accuracy``
    exactly as LedgerStore.accuracy() computes it.
    """
    public_entries = []
    for e in entries:
        grade = e.get("grade")
        public_grade = None
        if grade is not None:
            public_grade = {
                "graded_at": grade.get("graded_at"),
                "hit_steps": grade.get("hit_steps", []),
                "missed_steps": grade.get("missed_steps", []),
                "delay_error_d": grade.get("delay_error_d"),
                "evidence": [
                    {"source": prov.get("source"),
                     "retrieved_at": prov.get("retrieved_at"),
                     "mode": prov.get("mode")}
                    for prov in (grade.get("evidence") or [])
                ],
            }
            if grade.get("ungradeable_steps"):
                public_grade["ungradeable_steps"] = grade["ungradeable_steps"]
        entry = {
            "entry_id": e.get("entry_id"),
            "locked_at": e.get("locked_at"),
            "disruption_id": e.get("disruption_id"),
            "run_id": e.get("run_id"),
            "predicted_chain": [
                {"seq": ev.get("seq"), "node_id": ev.get("node_id"),
                 "channel": ev.get("channel"), "delay_d": ev.get("delay_d"),
                 "at": ev.get("at")}
                for ev in (e.get("predicted_chain") or [])
            ],
            "grade": public_grade,
        }
        if e.get("supersedes"):
            entry["supersedes"] = e["supersedes"]
        public_entries.append(entry)
    return {"entries": public_entries, "accuracy": accuracy}


DOCS_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SupermAP — Public API v1</title>
<style>
body{font-family:ui-monospace,Menlo,Consolas,monospace;background:#0b0b0c;color:#e5e5e5;
margin:2rem auto;max-width:52rem;padding:0 1rem;line-height:1.55}
h1{color:#ff9e1b}h2{color:#ff9e1b;margin-top:2rem}code{background:#1d1d20;padding:.1rem .35rem}
pre{background:#111;border:1px solid #333;padding:1rem;overflow:auto;white-space:pre-wrap}
table{border-collapse:collapse;margin:1rem 0;width:100%}td,th{border:1px solid #333;
padding:.4rem .6rem;text-align:left;vertical-align:top}.muted{color:#9ca3af}
</style></head><body>
<h1>SupermAP Public API — v1</h1>
<p>A read-only, keyless public surface over the same data the SupermAP app
serves. The namespace itself is the version: this is <code>/api/v1/</code>;
a future incompatible surface would ship as <code>/api/v2/</code> and v1
would keep its contracts. All endpoints are <code>GET</code> only — write
methods (<code>POST</code>/<code>PUT</code>/<code>DELETE</code>) return
<code>405</code> in the standard error shape
<code>{"error": {"code": "method_not_allowed", "message": "method not allowed"}}</code>.
Every error from this namespace uses that same shape; there are no stack
traces and no other error format.</p>

<h2>Rate limits</h2>
<p>Keyless, per client IP, in-process token bucket: burst of
<strong>120 requests</strong>, refilled at <strong>2 requests/second</strong>
(about 120 requests/minute sustained). Exceeding the bucket returns
<code>429</code> with code <code>rate_limited</code> in the standard error
shape. The bucket lives in the API process memory; a restart resets it.</p>

<h2>Endpoints</h2>
<table>
<tr><th>Endpoint</th><th>What it returns</th></tr>
<tr><td><code>GET /api/v1/health</code></td>
<td>Service + adapter health: <code>{status, service, version, adapters, summary}</code>
— the same observed adapter states as the app's <code>GET /health</code>
(LIVE / SNAPSHOT / DOWN, never asserted without a real fetch cycle).</td></tr>
<tr><td><code>GET /api/v1/graph</code></td>
<td>The calibrated supply-chain network verbatim as stored
(<code>data/graph/graph.json</code>): places (key <code>nodes</code>), legs
(key <code>edges</code>) and metadata.
No parameters. 404 <code>graph_not_found</code> if no graph artifact exists.</td></tr>
<tr><td><code>GET /api/v1/events</code></td>
<td><code>{events: [...]}</code> — the disruption feed in file
order. Lines that fail validation are skipped and counted in a top-level
<code>invalid_lines</code> field (present only when &gt; 0); they are never
served. An empty store is <code>{events: []}</code> with 200.</td></tr>
<tr><td><code>GET /api/v1/scorecard</code></td>
<td>The stored backtest scorecard verbatim (BACKTEST, see the
conventions below). 404 <code>scorecard_not_found</code> when no artifact
has been generated yet.</td></tr>
<tr><td><code>GET /api/v1/ledger</code></td>
<td><code>{entries: [...], accuracy: {...}}</code> — the public chain
ledger view: the same fields the public ledger page shows, nothing more.
Per entry: <code>entry_id, locked_at, disruption_id, run_id</code>, the
locked steps (<code>seq, node_id, channel, delay_d, at</code> only), the
grade when one exists, and the ledger-only count summary.</td></tr>
<tr><td><code>GET /api/v1/docs</code></td><td>This page.</td></tr>
</table>
<p class="muted">Parameters: the v1 GETs take no required parameters and no
request bodies. Unknown routes under <code>/api/v1/</code> return 404 in the
standard error shape.</p>

<h2>Example response excerpts</h2>
<p><code>GET /api/v1/health</code></p>
<pre>{"status": "ok", "service": "supermap-run-api", "version": "v1",
 "adapters": [{"adapter": "noaa", "state": "LIVE", "mode": "LIVE",
               "last_success_at": "2026-01-01T00:00:00Z"}],
 "summary": {"LIVE": 2, "SNAPSHOT": 4, "DOWN": 2}}</pre>
<p><code>GET /api/v1/ledger</code></p>
<pre>{"entries": [{"entry_id": "led_…", "locked_at": "2026-01-01T00:00:00Z",
  "disruption_id": "evt_…", "run_id": "run_…",
  "predicted_chain": [{"seq": 0, "node_id": "gnd_nogales",
    "channel": "congestion", "delay_d": 3.5, "at": "2026-01-01T00:00:00Z"}],
  "grade": null}], "accuracy": {"graded_entries": 0, "accuracy": null}}</pre>
<p><code>GET /api/v1/events</code></p>
<pre>{"events": [{"id": "evt_…", "node_id": "sea_savannah", "kind": "weather",
  "status": "active", "source": "NOAA/NWS", "mode": "LIVE",
  "display_mode": "SNAPSHOT",
  "provenance": {"source": "NOAA/NWS", "retrieved_at": "…", "mode": "LIVE"}}]}</pre>

<h2>Data-honesty conventions</h2>
<p><strong>Provenance.</strong> Records carry their provenance as served —
<code>source</code>, <code>retrieved_at</code> and <code>mode</code> fields
are passed through verbatim and never invented. A record without a mode
carries none; the API does not backfill one. <code>mode</code> is how the
record was obtained when it was obtained. <code>display_mode</code> on an
event is what it may be called now: <code>LIVE</code> only while its feed is
LIVE on the health board and the record is younger than that feed's max age;
otherwise <code>SNAPSHOT</code> or <code>RECORDED</code>.</p>
<p><strong>MODELLED vs SOURCED.</strong> <code>SOURCED</code> values come from
a cited external source (a filing, a feed, an observation).
<code>MODELLED</code> values are outputs of the deterministic causal engine
or a stated derivation rule — they are labelled MODELLED wherever they are
published and are never presented as observed facts.</p>
<p><strong>CORE vs SHELL places.</strong> Every place carries a
<code>tier</code>. <code>core</code> places are calibration-grade:
sourced capacities; they alone may originate Ledger locks and alone feed
the backtest/scorecard. <code>shell</code> places are routing-grade:
registry-verified coordinates plus observed activity presence (IMF
PortWatch vessel calls, OurAirports, NTAD), with capacities labelled
<code>ESTIMATE</code> or <code>NOT_PUBLISHED</code> on the place itself.
Provenance is reported per tier in the graph's <code>metadata</code> —
never as one blended figure.</p>
<p><strong>BACKTEST vs LEDGER.</strong> The scorecard (<code>/api/v1/scorecard</code>)
is a <strong>BACKTEST</strong>: historical scenarios replayed through the
engine and graded against recorded outcomes. The ledger
(<code>/api/v1/ledger</code>) is the live, append-only record of chains
locked <em>before</em> their outcomes were knowable and graded afterwards.
Their counts are computed from different populations and are never mixed
or substituted for one another.</p>
<p><strong>Counts are counts.</strong> Where this product publishes ensemble
results (in the app), hit figures are recurrence counts over a fixed,
enumerated parameter-variant grid — never percentages presented as
forecast probabilities.</p>
</body></html>
"""
