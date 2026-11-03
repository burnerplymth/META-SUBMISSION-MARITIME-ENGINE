"""FastAPI app implementing the run API.

Endpoints:
  POST /run            {source: scenario|live, scenario|live, params?} -> 201
  POST /fork           {run_id, at_seq, overrides}                     -> 201
  GET  /chain/{run_id} -> {run_id, status, event_count, stream_hash,
                           events, edges[]}   (edges materialised)
  GET  /scorecard      -> stored scorecard.json, read-only;
                           404 in the standard error shape when absent
  GET  /graph          -> stored data/graph/graph.json verbatim, read-only;
                           404 in the standard error shape when
                           absent. The parsed graph is cached in
                           memory keyed by the file's mtime, served with
                           a SHA-256 ETag (If-None-Match -> 304) and
                           gzip-encoded when the client accepts gzip
  GET  /events         -> {events: [...]} from data/events.jsonl in
                           file order, read-only; an empty or
                           absent store is {events: []} with 200, and lines
                           failing validation are skipped + counted in a
                           top-level "invalid_lines" field (never served,
                           never a 500)
  GET  /money/exposures -> server/money/exposures.json (out-of-scope excerpts withheld),
                           read-only; 404 in the standard error
                           shape when absent
  GET  /money/node/{node_id} -> panel.py's per-node money panel for
                           one graph node; quotes come from the
                           app's quote fetcher (see
                           _default_money_quote_fetcher: unpriced by
                           default, live Stooq only under
                           SUPERMAP_MONEY_LIVE_QUOTES=1) and any ticker
                           without a quote is served unpriced (null
                           price fields) exactly as panel.py specifies —
                           never compensated or fabricated. A node_id
                           outside the graph is a 404.
  GET  /satwatch/scans  -> {scans: [...]}: every scan record in
                           data/snapshots/satwatch/ (*.json, filename
                           order), read-only; an absent
                           directory is {scans: []} with 200, and a
                           malformed record file is skipped + counted
                           in a top-level "invalid_lines" field, exactly
                           like /events (never served, never a 500)
  GET  /satwatch/crops/{filename} -> one crop PNG from the same satwatch
                           directory; basename-only, .png-only —
                           anything else (traversal, subpaths, other
                           extensions, missing files) is a 404
  GET  /lenses          -> server/engine/lenses.json verbatim,
                           read-only; 404 in the standard error
                           shape when absent
  GET  /runs            -> catalogue of ALL stored runs:
                           chain runs, ensemble-stats flat jobs,
                           ensemble tree jobs and sim runs, as
                           {runs: [{id, kind, title, created_at,
                           status, facts}]} — metadata only (journals
                           are read for their disruption / seed /
                           timing fields; result payloads are never
                           served), titles built server-side from the
                           journals in the web run-reference idiom,
                           newest first, a corrupt file skipped
                           (never a 500)
  GET  /impact/goods    -> {goods: [{code, label}]}: the IMPACT
                           layer's CMD_LABELS, imported from
                           server/impact/derive.py (never copied)
  GET  /livetwin/status -> {"enabled": bool}, always 200 (the flag
                           without a 404 on the boot path)
  GET  /livetwin/feeds  -> LIVE TWIN feeds: 404
                           live_twin_off unless SUPERMAP_LIVE_TWIN is
                           truthy (1/true/yes, resolved once in
                           create_app onto app.state); when on, the
                           five section feeds derived from the adapter
                           registry's health_report() plus the
                           OpenSky snapshot pack's records as `air`
                           and the AISStream pack as `vessels`
  GET  /feeds, /feeds/eia, /feeds/comtrade
                       -> CONTEXT feeds, read
                           only: {feed, label, state, mode, provenance,
                           note, count, record_types, records[]} from
                           the adapters' snapshot packs + health. Every
                           record carries provenance (relabelled
                           SNAPSHOT when the adapter is not LIVE);
                           figures are SOURCED, never summed; an empty
                           or DOWN feed answers 200 with state DOWN,
                           records [] and the reason. Market
                           quarantine: never an engine/run input
  GET  /portwatch       -> PortWatch activity snapshot
                           (data/snapshots/portwatch.json) verbatim:
                           {adapter, provenance, records[]} in the shape
                           the web map's PortWatchSnapshot type expects
                           404 in the standard error shape when
                           the snapshot is absent — never an
                           empty-records 200 that looks like data
  POST /ensemble       causal ensemble: the same scenario
                           spec shape as POST /run -> engine.ensemble.
                           run_ensemble's ranked bottlenecks verbatim in
                           structure (hits are COUNTS over the fixed
                           27-variant grid, never probabilities), each
                           entry plus worst_delay_d over the grid, plus
                           the centre-grid base run's stream identity
  POST /ensemble-stats ensemble statistics: create a
                           job that runs up to 10,000 causal variants
                           of one disruption (declared axes x levels,
                           deterministic selection, incremental
                           aggregation) -> 201 job summary; poll
                           GET /ensemble-stats/{id}; aggregates at
                           GET /ensemble-stats/{id}/results, the
                           per-branch view at .../branches, one branch's
                           firing variants at .../branch?branch=<key>,
                           and one variant's re-derived full chain at
                           .../variants/{index}. Shares are counts of
                           variants, never real-world chances; jobs
                           write only to their own store (never the
                           run store, Ledger or scorecard)
  POST /ensemble-stats/tree/quote
                       ensemble statistics, TREE mode:
                           quote the full expansion of
                           one disruption as a tree (branching x depth
                           x the declared axes, in axis order) against
                           the run cap — no job, no runs, nothing
                           persisted; the quote's arithmetic is the
                           refusal's arithmetic
  POST /ensemble-stats/tree
                       Create a tree job (same body, plus branching /
                           depth) -> 201 job summary; a tree quoted
                           over the cap is refused 422 tree_too_large
                           before anything is persisted. Poll
                           GET /ensemble-stats/tree/{id}; the list is
                           GET /ensemble-stats/tree; the light tree
                           view at GET /ensemble-stats/tree/{id}/tree,
                           one member's aggregates at
                           .../nodes/{index}, and one member's
                           re-derived full chain at
                           .../variants/{index}. Tree jobs write only
                           to their own store (data/ensemble_tree/ by
                           default) — the flat jobs' store and list
                           never see a tree record
  POST /ensemble-stats/explorations
                       ensemble statistics, EXPLORATIONS
                           (interactive deepening): a
                           saved exploration whose members are
                           ordinary flat jobs; POST creates one rooted
                           at a flat job, GET lists them, GET
                           /ensemble-stats/explorations/{id} serves
                           the detail (members, breadcrumbs, each
                           member's next axis), POST .../rename
                           renames it, and POST .../expand deepens a
                           member — a new flat job fixing every axis
                           at the chosen parent variant's combination
                           except the next not-yet-varied axis, swept
                           over the supplied values. Exploration
                           records (pointers + display metadata only)
                           live under the flat store's explorations/
                           subdirectory and never appear as flat jobs
  GET  /api/v1/*       public API: read-only, keyless,
                           per-IP token-bucket rate-limited namespace —
                           graph / events / scorecard / ledger (public
                           projection) / health, delegating to the
                           handlers above; docs at GET /api/v1/docs
  GET|POST /alerts/rules, GET|PUT|DELETE /alerts/rules/{id},
  GET  /alerts/feed    alert rules + fired-alerts feed
                           (server/alerts): rules are
                           user-defined over served facts (adapter
                           state, new events, run outcomes, ledger
                           locks/grades) and evaluate on the existing
                           seams — adapter-cycle end (run_once) and
                           run/fork completion (the hooks in post_run /
                           post_fork below) — never on a read.
  GET|POST /watchlists, GET|PUT|DELETE /watchlists/{id},
  POST /watchlists/{id}/nodes, DELETE /watchlists/{id}/nodes/{node_id},
  GET  /watchlists/{id}/view
                       watchlists: named node-id lists persisted
                           in the same alerts store; the view serves
                           each node's current state read off the
                           served sources (graph file, events file,
                           run store) — display only, no new facts.
  GET  /               service info (graph source, run count)

Errors: one shape everywhere — {error: {code, message}} with a 4xx status,
no stack traces. Params out of bounds are REJECTED, never clamped.

Graph: SUPERMAP_GRAPH env, else data/graph/graph.json when present,
else the engine's built-in fixture graph (journal records which).
GET /graph serves the graph FILE (SUPERMAP_GRAPH env, else
data/graph/graph.json; injectable via create_app(graph_path=...)).
The file is re-read and re-parsed only when its mtime changes
(the parsed document, its serialised body, its gzip body and its
SHA-256 ETag are cached in memory per app instance), so a regenerated
graph is still served without a restart while unchanged-graph
requests never re-parse it. GZipMiddleware is deliberately NOT used
app-wide: it would buffer the /live/stream SSE response, and the API
also serves binary FileResponses (satwatch PNG crops, scenario-pack ZIPs) whose
bytes must go out untouched — so gzip is applied to the /graph
payload only, in the handler below.
Live disruptions: SUPERMAP_EVENTS_FILE env (JSON or JSONL of
events), else data/events.jsonl / data/disruptions.json when present.
They are resolved LAZILY per request through the EventStore
(lifecycle fold applied) — never frozen into a boot-time closure —
so an event appended after boot is runnable via source=live without
a restart. create_app(disruptions=...) still injects a fixed mapping
verbatim.
GET /events reads the same events source (SUPERMAP_EVENTS_FILE env, else
data/events.jsonl; injectable via create_app(events_path=...)), also
re-read on every request.
The file-backed sources follow the same resolution pattern
(explicit argument > env > repo default) and are likewise re-read on
every request: exposures (SUPERMAP_EXPOSURES_FILE env, else
server/money/exposures.json; injectable via
create_app(exposures_path=...)), the satwatch snapshot directory
(SUPERMAP_SATWATCH_DIR env, else data/snapshots/satwatch/; injectable
via create_app(satwatch_dir=...)), and lenses (SUPERMAP_LENSES_FILE
env, else server/engine/lenses.json; injectable via
create_app(lenses_path=...)). The PortWatch snapshot behind
GET /portwatch resolves the same way (SUPERMAP_PORTWATCH_FILE env,
else data/snapshots/portwatch.json; injectable via
create_app(portwatch_path=...)). The quote lookup behind
GET /money/node/{node_id} is injectable via
create_app(money_quote_fetcher=...) — a callable ticker -> quote dict
(as returned by money.stooq.latest_quote) or None; the default yields
None (unpriced) unless SUPERMAP_MONEY_LIVE_QUOTES=1, in which case it
attempts a live Stooq fetch per ticker and still yields None for any
ticker Stooq cannot price (see _default_money_quote_fetcher).
The health router (server/adapters/health.py) is mounted when it exists;
the import is defensive. At create_app()
time the router's adapter registry (server/adapters/registry.py) is
populated with the full default adapter set — see the registry seam
in create_app() — so GET /health lists every registered adapter.
The LLM router (server/llm/router.py, prefix "/llm": classify, explain,
ask, status, ledger lock/grade/list + the public ledger page at
GET /llm/ledger/public, served as HTML by the router itself) is mounted
the same defensive way — see the llm seam in create_app() — and is
injectable via create_app(llm_router=...), mirroring adapter_registry.
The default mount is built with this app's runs_dir, so
/llm/explain resolves runs from the same store the API writes.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

from engine.canonical import stream_hash
from engine.engine import Engine, EngineError, validate_disruption, validate_params
from engine.fork import fork_events
from engine.graph import Graph, GraphError
from engine import fixtures as engine_fixtures
from .store import RunStore, derive_run_id, replay_journal, state_from_record
from .display_mode import with_display_mode
from .offload import offload_blocking_routes
from .security import (body_limit_for, install_security, public_source,
                       cors_origins, cookie_secure, request_client, scrub_paths)

try:  # ENGINE REV 2 (opt-in, server/engine2): engine_rev=2 on POST /run
    from engine2 import Engine2
    from engine2.fork import fork_events2
    from engine2.journal import (engine_for as _engine_for2, get_profile_engine2,
                                 profile_stamp as _profile_stamp, replay_journal2,
                                 rev_stamp as _rev2_stamp)
    from engine2.plugins_d import PROFILE_KEY as _PROFILE_KEY
except Exception:  # pragma: no cover - rev 2 unavailable: rev 1 serves alone
    Engine2 = None  # type: ignore[assignment]
    fork_events2 = replay_journal2 = _rev2_stamp = _engine_for2 = None  # type: ignore[assignment]
    get_profile_engine2 = _profile_stamp = _PROFILE_KEY = None  # type: ignore[assignment]

# keys a profile run adds to its journal core (a fork of a profile run copies them)
_PROFILE_JOURNAL_KEYS = ("engine_label", "engine2_registry_hash", "profile", "sea_routes_sha256",
                         "profile_digest", "services_file_sha256", "table_sha256", "routes")


def _parse_profile(body: dict, engine_rev):
    """(profile, error): absent / null -> None (the default engine, unchanged bytes); 'd' -> ENGINE
    REV 2 profile d, 'e' -> ENGINE REV 2 profile e (opt-in, need engine_rev 2); anything else -> 422
    invalid_request."""
    if not isinstance(body, dict) or body.get("profile") is None:
        return None, None
    prof = body["profile"]
    if not isinstance(prof, str) or prof not in ("d", "e") or _PROFILE_KEY is None:
        return None, _err("invalid_request", "profile must be 'd' or 'e'")
    if engine_rev != 2:
        return None, _err("invalid_request", "profile requires engine_rev 2")
    return prof, None


def _parse_engine_rev(body: dict):
    """(rev, error): absent / null / 1 -> 1 (engine v1, unchanged bytes);
    2 -> ENGINE REV 2; anything else -> 422 invalid_request."""
    if not isinstance(body, dict) or body.get("engine_rev") is None:
        return 1, None
    rev = body["engine_rev"]
    if isinstance(rev, bool) or not isinstance(rev, int) or rev not in (1, 2):
        return None, _err("invalid_request", "engine_rev must be 1 or 2")
    if rev == 2 and Engine2 is None:
        return None, _err("invalid_request", "engine_rev 2 is unavailable")
    return rev, None
from .memtrim import release_free_memory
from .sim_loop import (SimLoopRefused, fold_interventions as sim_fold_interventions,
                       mount_sim_loop)

try:  # causal ensemble (engine, read-only use)
    from engine.ensemble import (
        DURATION_MULTS as ENS_DURATION_MULTS,
        QS_GRID as ENS_QS_GRID,
        THR_GRID as ENS_THR_GRID,
        run_ensemble,
    )
except Exception:  # imported as server.api.* from the repo root instead
    try:
        from server.engine.ensemble import (
            DURATION_MULTS as ENS_DURATION_MULTS,
            QS_GRID as ENS_QS_GRID,
            THR_GRID as ENS_THR_GRID,
            run_ensemble,
        )
    except Exception:  # pragma: no cover - ensemble unavailable
        run_ensemble = None  # type: ignore[assignment]
        ENS_QS_GRID = ENS_THR_GRID = ENS_DURATION_MULTS = ()  # type: ignore[assignment]

try:  # the ONE materialise_edges, in the cycle-free leaf
    from chain_edges import materialise_edges
except Exception:  # imported as server.api.* from the repo root instead
    from server.chain_edges import materialise_edges  # type: ignore

try:  # public surface helpers (same package either way)
    from .public_api import DOCS_HTML, TokenBucketLimiter, public_ledger_view
except Exception:  # pragma: no cover - imported as a top-level module
    from api.public_api import DOCS_HTML, TokenBucketLimiter, public_ledger_view  # type: ignore

try:  # Validator + EventStore — server/ on sys.path
    from store.store import EventStore, validate_c3_event
    from store.store import _fold_events as _store_fold_events
except Exception:  # imported as server.api.* from the repo root instead
    try:
        from server.store.store import EventStore, validate_c3_event
        from server.store.store import _fold_events as _store_fold_events
    except Exception:  # pragma: no cover - store unavailable
        validate_c3_event = None  # type: ignore[assignment]
        EventStore = None  # type: ignore[assignment]
        _store_fold_events = None  # type: ignore[assignment]

try:  # source citations (derived at serve time; pure functions)
    from store.citations import with_citation as _with_citation
except Exception:  # imported as server.api.* from the repo root instead
    try:
        from server.store.citations import with_citation as _with_citation
    except Exception:  # pragma: no cover - never block /events on this
        def _with_citation(ev):  # type: ignore[misc]
            return ev

try:  # money panel + Stooq quotes — same dual-import convention.
    from money.panel import build_money_panel
    from money.stooq import StooqAdapter, latest_quote
except Exception:  # imported as server.api.* from the repo root instead
    try:
        from server.money.panel import build_money_panel
        from server.money.stooq import StooqAdapter, latest_quote
    except Exception:  # pragma: no cover - money layer unavailable
        build_money_panel = None  # type: ignore[assignment]
        StooqAdapter = None  # type: ignore[assignment]
        latest_quote = None  # type: ignore[assignment]

try:  # content scope guard for the exposures API — same dual-import convention.
    from ports.content_scope import guard_exposures as _guard_exposures
except Exception:  # imported as server.api.* from the repo root instead
    from server.ports.content_scope import guard_exposures as _guard_exposures

try:  # alerts & watchlists (server/alerts) — same dual-import convention.
    from alerts.store import AlertsStore, new_id as alerts_new_id, now_iso as alerts_now_iso
    from alerts.validate import (
        RuleValidationError as AlertsValidationError,
        validate_node_add_payload,
        validate_rule_payload,
        validate_watchlist_payload,
    )
    from alerts import evaluator as alerts_evaluator
    from alerts import ownership as alerts_ownership
    from alerts.ownership import OwnershipError as AlertsOwnershipError
except Exception:  # imported as server.api.* from the repo root instead
    try:
        from server.alerts.store import AlertsStore, new_id as alerts_new_id, now_iso as alerts_now_iso  # type: ignore
        from server.alerts.validate import (  # type: ignore
            RuleValidationError as AlertsValidationError,
            validate_node_add_payload,
            validate_rule_payload,
            validate_watchlist_payload,
        )
        from server.alerts import evaluator as alerts_evaluator  # type: ignore
        from server.alerts import ownership as alerts_ownership  # type: ignore
        from server.alerts.ownership import OwnershipError as AlertsOwnershipError  # type: ignore
    except Exception:  # pragma: no cover - alerts layer unavailable
        AlertsStore = None  # type: ignore[assignment]
        alerts_evaluator = None  # type: ignore[assignment]
        alerts_ownership = None  # type: ignore[assignment]

        class AlertsOwnershipError(ValueError):  # type: ignore[no-redef]
            """Unreachable stand-in (alerts layer unavailable)."""

try:  # SIM (server/sim) — same dual-import convention.
    from sim.store import SimStore, derive_sim_run_id
    from sim.state import (DEFAULT_TICKS as SIM_DEFAULT_TICKS,
                           MAX_TICKS as SIM_MAX_TICKS,
                           TICK_SECONDS as SIM_TICK_SECONDS, state_at as sim_state_at)
    from sim.runner import build_record as sim_build_record
    from sim.weather import (fetch_snapshot as sim_fetch_snapshot,
                             lane_weather_at as sim_lane_weather_at,
                             node_weather_at as sim_node_weather_at)
    from sim.simulate import fmt_ts as sim_fmt_ts, parse_ts as sim_parse_ts
    from sim.markers import compute_markers as sim_compute_markers
    from sim.twin import consistency_block as sim_consistency_block
    from sim.derive import TierContractError as SimTierContractError
except Exception:  # imported as server.api.* from the repo root instead
    try:
        from server.sim.store import SimStore, derive_sim_run_id  # type: ignore
        from server.sim.state import (  # type: ignore
            DEFAULT_TICKS as SIM_DEFAULT_TICKS,
            MAX_TICKS as SIM_MAX_TICKS,
            TICK_SECONDS as SIM_TICK_SECONDS, state_at as sim_state_at)
        from server.sim.runner import build_record as sim_build_record  # type: ignore
        from server.sim.weather import (  # type: ignore
            fetch_snapshot as sim_fetch_snapshot,
            lane_weather_at as sim_lane_weather_at,
            node_weather_at as sim_node_weather_at)
        from server.sim.simulate import fmt_ts as sim_fmt_ts, parse_ts as sim_parse_ts  # type: ignore
        from server.sim.markers import compute_markers as sim_compute_markers  # type: ignore
        from server.sim.twin import consistency_block as sim_consistency_block  # type: ignore
        from server.sim.derive import TierContractError as SimTierContractError  # type: ignore
    except Exception:  # pragma: no cover - sim layer unavailable
        SimStore = None  # type: ignore[assignment]
        sim_compute_markers = None  # type: ignore[assignment]
        sim_consistency_block = None  # type: ignore[assignment]
        sim_lane_weather_at = None  # type: ignore[assignment]
        sim_node_weather_at = None  # type: ignore[assignment]

        class SimTierContractError(Exception):  # type: ignore[no-redef]
            """Unreachable stand-in (sim layer unavailable)."""

try:  # Ensemble statistics service (server/ensstats) — same convention.
    from ensstats.jobs import (BoundedJobPool,
                               BranchNotFound as EnsBranchNotFound,
                               EnsembleStatsJobs,
                               JobNotFound as EnsJobNotFound,
                               JobNotReady as EnsJobNotReady,
                               VariantNotFound as EnsVariantNotFound)
    from ensstats.store import EnsembleStatsStore
except Exception:  # imported as server.api.* from the repo root instead
    try:
        from server.ensstats.jobs import (  # type: ignore
            BoundedJobPool, BranchNotFound as EnsBranchNotFound,
            EnsembleStatsJobs, JobNotFound as EnsJobNotFound,
            JobNotReady as EnsJobNotReady,
            VariantNotFound as EnsVariantNotFound)
        from server.ensstats.store import EnsembleStatsStore  # type: ignore
    except Exception:  # pragma: no cover - ensemble stats unavailable
        BoundedJobPool = None  # type: ignore[assignment]
        EnsembleStatsJobs = None  # type: ignore[assignment]
        EnsembleStatsStore = None  # type: ignore[assignment]

try:  # Ensemble TREE service (server/ensstats/tree_jobs.py) — same
    # dual-import convention, alongside the flat ensstats imports.
    from ensstats.tree_jobs import (
        EnsembleTreeJobs,
        TreeJobNotFound as EnsTreeJobNotFound,
        TreeJobNotReady as EnsTreeJobNotReady,
        TreeNodeNotFound as EnsTreeNodeNotFound,
        TreeVariantNotFound as EnsTreeVariantNotFound)
    from ensstats.tree_store import TreeJobStore
except Exception:  # imported as server.api.* from the repo root instead
    try:
        from server.ensstats.tree_jobs import (  # type: ignore
            EnsembleTreeJobs,
            TreeJobNotFound as EnsTreeJobNotFound,
            TreeJobNotReady as EnsTreeJobNotReady,
            TreeNodeNotFound as EnsTreeNodeNotFound,
            TreeVariantNotFound as EnsTreeVariantNotFound)
        from server.ensstats.tree_store import TreeJobStore  # type: ignore
    except Exception:  # pragma: no cover - ensemble tree unavailable
        EnsembleTreeJobs = None  # type: ignore[assignment]
        TreeJobStore = None  # type: ignore[assignment]
        EnsTreeJobNotFound = None  # type: ignore[assignment]
        EnsTreeJobNotReady = None  # type: ignore[assignment]
        EnsTreeNodeNotFound = None  # type: ignore[assignment]
        EnsTreeVariantNotFound = None  # type: ignore[assignment]

try:  # Ensemble EXPLORATIONS service (server/ensstats/explorations.py)
    # — same dual-import convention, alongside the flat ensstats imports.
    from ensstats.explorations import (
        EnsembleExplorations,
        ExplorationNotFound as EnsExplorationNotFound)
except Exception:  # imported as server.api.* from the repo root instead
    try:
        from server.ensstats.explorations import (  # type: ignore
            EnsembleExplorations,
            ExplorationNotFound as EnsExplorationNotFound)
    except Exception:  # pragma: no cover - explorations unavailable
        EnsembleExplorations = None  # type: ignore[assignment]
        EnsExplorationNotFound = None  # type: ignore[assignment]

try:  # Ensemble tree core defaults (read-only use).
    from engine import ensemble_tree as ens_tree
except Exception:  # imported as server.api.* from the repo root instead
    try:
        from server.engine import ensemble_tree as ens_tree  # type: ignore
    except Exception:  # pragma: no cover - tree core unavailable
        ens_tree = None  # type: ignore[assignment]

try:  # IMPACT layer (server/impact) — same dual-import convention.
    from impact.store import ImpactStore
    from impact.derive import CMD_LABELS, ImpactService
except Exception:  # imported as server.api.* from the repo root instead
    try:
        from server.impact.store import ImpactStore  # type: ignore
        from server.impact.derive import CMD_LABELS, ImpactService  # type: ignore
    except Exception:  # pragma: no cover - impact layer unavailable
        ImpactStore = None  # type: ignore[assignment]
        ImpactService = None  # type: ignore[assignment]
        CMD_LABELS = None  # type: ignore[assignment]

REPO_ROOT = Path(__file__).resolve().parents[2]

# Stdlib logging for the api package. An unhandled exception is
# logged server-side with its traceback and a per-request id, and the
# same id is returned to the caller as the X-Request-Id header.
log = logging.getLogger("supermap.api")
if not log.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter(
        "%(asctime)s %(name)s %(levelname)s %(message)s"))
    log.addHandler(_handler)
    log.setLevel(logging.INFO)

ERROR_STATUS = {
    "unknown_node": 422, "unknown_param": 422, "param_out_of_bounds": 422,
    "missing_capacity_loss": 422, "invalid_kind": 422, "invalid_shape": 422,
    "invalid_request": 422, "empty_overrides": 422, "at_seq_out_of_range": 422,
    "unknown_override": 422, "disruption_resolved": 422, "runaway": 422,
    "unknown_disruption": 404, "unknown_run": 404, "bad_json": 400,
    # Desks codes that default to 422 are
    # plain not-found conditions.
    "unknown_event": 404, "unknown_place": 404, "replay_not_stored": 404,
    "scorecard_not_found": 404, "scorecard_unreadable": 500,
    "graph_not_found": 404, "graph_unreadable": 500,
    "events_unreadable": 500,
    "exposures_not_found": 404, "exposures_unreadable": 500,
    "node_not_found": 404, "money_unavailable": 500,
    "crop_not_found": 404,
    "lenses_not_found": 404, "lenses_unreadable": 500,
    "portwatch_not_found": 404, "portwatch_unreadable": 500,
    # The /llm family's stable codes (mapping documented in
    # server/llm/router.py's module docstring).
    "run_not_found": 404, "entry_not_found": 404,
    "entry_already_exists": 409, "already_graded": 409,
    "lock_before_knowable": 409, "ledger_invalid": 422,
    "grade_without_evidence": 422, "spend_cap_reached": 429,
    "provider_error": 502, "internal_error": 500,
    # Framework-level failures (unmatched route,
    # wrong method) mapped into the standard shape by the
    # StarletteHTTPException handler in create_app().
    "not_found": 404, "method_not_allowed": 405, "http_error": 400,
    # Mutating-route request-body size cap (see MAX_BODY_BYTES).
    "payload_too_large": 413,
    # A new sim record would fill the store / the volume.
    "storage_full": 507, "store_full": 507,
    # Sim-loop codes that would otherwise fall through to the 422
    # default. A build that cannot be queued is the server's failure (503).
    "sim_build_failed": 503, "intervention_refused": 422,
    "runs_not_comparable": 422,
    # Public namespace rate limit + ensemble.
    "rate_limited": 429, "ensemble_unavailable": 500,
    "ledger_unavailable": 500,
    # Alerts & watchlists.
    "rule_not_found": 404, "watchlist_not_found": 404,
    "alerts_unavailable": 500,
    # Another owner's rule/watchlist.
    "forbidden": 403,
    # Tick simulation.
    "unknown_sim_run": 404, "unknown_sim_vehicle": 404,
    "sim_unavailable": 500,
    "derivation_error": 422,
    # Ensemble statistics: causal-variant jobs.
    "ensemble_stats_not_found": 404, "ensemble_stats_not_ready": 409,
    "sim_run_not_ready": 409,
    "ensemble_stats_unavailable": 500, "branch_not_found": 404,
    "variant_not_found": 404, "unknown_axis": 422,
    "axis_not_applicable": 422,
    # Ensemble tree mode: over-cap refusals + members.
    "tree_too_large": 422, "tree_node_not_found": 404,
    # Ensemble explorations: interactive deepening.
    "exploration_not_found": 404, "no_next_axis": 422,
    # IMPACT layer: additive /impact/* namespace.
    "impact_unavailable": 500,
    # LIVE TWIN: the flag is off.
    "live_twin_off": 404,
    # Graph services layer (opt-in sidecar, read-only routes).
    "services_not_found": 404, "services_unreadable": 409, "unknown_service": 404,
    # Graph extra sidecar (opt-in, read-only route).
    "extra_not_found": 404, "extra_unreadable": 409,
    # Inventory-days reference sidecar (opt-in, read-only route).
    "inventory_days_not_found": 404, "inventory_days_unreadable": 409,
    # Lane geometry (read-only route).
    "lane_geometry_unreadable": 409, "no_lane_path": 404,
    # Monte Carlo (input-uncertainty simulation).
    "montecarlo_not_found": 404, "montecarlo_not_ready": 409,
    "montecarlo_too_large": 422, "montecarlo_unavailable": 500,
    "input_not_exposed": 422,
}

# Ensemble-statistics job ids are server-derived ("ens_" + 16 hex);
# anything else is not a job id (traversal included).
_ENS_JOB_ID_RE = re.compile(r"^ens_[0-9a-f]{16}$")

# LIVE TWIN section feeds: adapter-registry names and
# their product labels, in section order.
_LIVE_TWIN_FEEDS = (
    ("noaa", "NOAA weather alerts"),
    ("gdacs", "GDACS hazard alerts"),
    ("opensky", "OpenSky aircraft"),
    ("aisstream", "AISStream vessels"),
    ("portwatch", "PortWatch port activity"),
)

# Exploration ids likewise ("exp_" + 16 hex).
_ENS_EXP_ID_RE = re.compile(r"^exp_[0-9a-f]{16}$")

# Computed-payload cache bounds for the sim read
# path (create_app wires the caches; see the sim payload cache block there).
# State payloads are per (sim_run_id, tick): 512 entries covers every
# tick of a max-size run (384 hour-ticks) plus a second run's hot
# ticks; a payload serialises to 0.20-0.32 MB on the real tiered
# record in data/sim_runs/ (~1 MB parsed), so the entries bound is
# ~0.5 GB worst case at that roster size, in practice <=384 entries
# for one run's full timeline.
# Markers are per sim run (plus the chain-store fingerprint — see
# _chain_store_fingerprint): 128 entries is far beyond the number of
# runs a session compares; marker payloads are small (capped lists).
# Request-body size cap for mutating routes (64 KiB; no legitimate
# body comes close; the largest are small JSON run/sim specs).
MAX_BODY_BYTES = 64 * 1024

_F1_LOCK = threading.Lock()  # guards the sim payload LRUs
SIM_STATE_CACHE_MAX = 512
# Public-link survival: the 512-entry bound alone allowed 512 x 3.2 MB (about
# 1.6 GB parsed) of state payloads, so the state cache is also bounded in bytes
# (SUPERMAP_SIM_STATE_CACHE_MB, default 96). Cost of a payload is estimated as
# twice its JSON length (a parsed payload is about twice its serialised size).
SIM_STATE_CACHE_DEFAULT_MB = 96
SIM_STATE_RAM_FACTOR = 2
SIM_MARKERS_CACHE_MAX = 128

# SIM async builds: POST /sim/run is create-then-poll.
# Builds execute on a bounded pool mirroring the ensstats
# BoundedJobPool semantics: SIM_JOB_POOL_WORKERS workers plus
# a bounded queue of SIM_JOB_POOL_QUEUE; a submission beyond the bound
# is refused with the existing "rate_limited" code (HTTP 429). The
# in-memory job registry on app.state keeps at most
# SIM_JOB_REGISTRY_FINISHED_MAX finished (failed / complete) entries —
# building entries are never evicted.
SIM_JOB_POOL_WORKERS = 2
SIM_JOB_POOL_QUEUE = 8
# Public-link survival: a cold 30-tick build peaks at 0.9-1.25 GB, so two at
# once do not fit a 2 GB machine. SUPERMAP_SIM_BUILD_WORKERS sets the worker
# count (the deploy profile in fly.toml sets 1); builds beyond it wait in the
# queue and a full queue is the existing 429 rate_limited.
SIM_BUILD_WORKERS_ENV = "SUPERMAP_SIM_BUILD_WORKERS"
SIM_OUT_OF_MEMORY_MESSAGE = (
    "the server ran out of memory building this run; try fewer ticks or a "
    "coarser tick size, or retry once other builds finish")


def _sim_build_workers() -> int:
    try:
        n = int(os.environ.get(SIM_BUILD_WORKERS_ENV, SIM_JOB_POOL_WORKERS))
    except (TypeError, ValueError):
        n = SIM_JOB_POOL_WORKERS
    return max(1, min(n, 8))
SIM_JOB_REGISTRY_FINISHED_MAX = 32


class _SimFallbackPool:
    """BoundedJobPool-semantics stand-in used only when the ensstats
    pool class is unavailable (pragma: the ensstats package is a hard
    sibling of this app in every real deployment). Same interface:
    reserve() claims one of (workers + queue) slots without blocking,
    submit_reserved() hands the callable to a fixed worker set, and a
    slot is released by the worker when the job finishes."""

    def __init__(self, workers: int = SIM_JOB_POOL_WORKERS,
                 queue_size: int = SIM_JOB_POOL_QUEUE, name: str = "sim-pool"):
        import queue as _queue
        self._slots = threading.BoundedSemaphore(workers + queue_size)
        self._queue: _queue.Queue = _queue.Queue()
        for i in range(workers):
            threading.Thread(target=self._loop, name=f"{name}-{i}",
                             daemon=True).start()

    def _loop(self) -> None:
        while True:
            fn = self._queue.get()
            try:
                fn()
            finally:
                self._slots.release()
                self._queue.task_done()

    def reserve(self) -> bool:
        return self._slots.acquire(blocking=False)

    def submit_reserved(self, fn) -> None:
        self._queue.put(fn)

    def release(self) -> None:
        self._slots.release()


def _default_money_quote_fetcher(ticker: str) -> dict | None:
    """Quote for one ticker, or None when unpriced.

    Default (SUPERMAP_MONEY_LIVE_QUOTES unset): no network call, None
    for every ticker. That is a deliberate choice, not a shortcut:
    Stooq has not served a quote from this deployment
    (bot-verification challenge page, or the connection hangs
    ~30 s before dropping), so a live attempt per request would cost
    up to ~40 s per ticker to produce exactly panel.py's
    unpriced-nulls output anyway. That unpriced output is the correct
    output when Stooq is unreachable, and nothing is fabricated to
    compensate for it.

    With SUPERMAP_MONEY_LIVE_QUOTES=1 the fetcher does attempt the
    live Stooq fetch (StooqAdapter.fetch_history + latest_quote) and
    still yields None for any ticker Stooq cannot price. A different
    quote source can be injected via create_app(money_quote_fetcher=).
    """
    if os.environ.get("SUPERMAP_MONEY_LIVE_QUOTES") != "1":
        return None
    if StooqAdapter is None or latest_quote is None:
        return None
    try:
        result = StooqAdapter().fetch_history(ticker)
        return latest_quote(result["records"])
    except Exception:
        return None


# Caller-supplied ids echoed into error messages are capped at this
# length: an unbounded id is never reflected
# verbatim — longer id-like tokens are truncated with an ellipsis.
_ID_ECHO_MAX = 80
_ID_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-./]{%d,}" % (_ID_ECHO_MAX + 1))


def _cap_echoed_ids(message: str) -> str:
    return _ID_TOKEN_RE.sub(lambda m: m.group(0)[:_ID_ECHO_MAX] + "…",
                            message)


def _satwatch_attr(module: str, attr: str):
    """Resolve ``satwatch.<module>.<attr>`` under either import identity
    (server/ on sys.path -> "satwatch.*"; repo root -> "server.satwatch.*"),
    or None when the package cannot be imported."""
    import importlib
    for root in ("satwatch", "server.satwatch"):
        try:
            return getattr(importlib.import_module(f"{root}.{module}"), attr)
        except Exception:
            continue
    return None


def _priors_for_display(vessel_class, priors):
    """Served form of a stored vessel prior: a ro-ro carrier's capacity is
    rated in CEU (car equivalent units); records written before the unit
    was corrected say TEU for it. Changing the stored label would move
    every record byte (stored hashes), so the unit is corrected here, at
    serve time; every other class and field is served as stored."""
    cap = (priors or {}).get("capacity")
    if vessel_class == "roro" and isinstance(cap, dict) and cap.get("unit") == "TEU":
        return {**priors, "capacity": {**cap, "unit": "CEU"}}
    return priors


def _safe_exc(exc: BaseException) -> str:
    """The part of an exception that is safe to serve: never a file name,
    an absolute path or an errno text. The full exception goes to the log."""
    logging.getLogger("supermap.api").warning("request failed: %r", exc)
    if isinstance(exc, json.JSONDecodeError):
        return f"invalid JSON (line {exc.lineno}, column {exc.colno})"
    if isinstance(exc, IsADirectoryError):
        return "is not a file"
    if isinstance(exc, PermissionError):
        return "permission denied"
    if isinstance(exc, FileNotFoundError):
        return "file not found"
    if isinstance(exc, OSError):
        return "i/o error"
    if isinstance(exc, UnicodeDecodeError):
        return "not valid text"
    return type(exc).__name__


_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9._:\-]{1,64}")


def _err(code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=ERROR_STATUS.get(code, 422),
                        content={"error": {"code": code,
                                           "message": _cap_echoed_ids(scrub_paths(message))}})


def _pagination(req: Request):
    """Additive pagination: optional ?limit=&offset= query
    params. Returns (limit, offset, error) — limit None means "all
    remaining" (today's default full response); invalid values are a
    standard 422, never clamped/coerced."""
    limit = None
    offset = 0
    raw_limit = req.query_params.get("limit")
    raw_offset = req.query_params.get("offset")
    if raw_limit is not None:
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            return None, 0, _err("invalid_request", "limit must be an integer")
        if limit < 0 or str(limit) != raw_limit.strip():
            return None, 0, _err("invalid_request",
                                 "limit must be a non-negative integer")
    if raw_offset is not None:
        try:
            offset = int(raw_offset)
        except (TypeError, ValueError):
            return None, 0, _err("invalid_request", "offset must be an integer")
        if offset < 0 or str(offset) != raw_offset.strip():
            return None, 0, _err("invalid_request",
                                 "offset must be a non-negative integer")
    return limit, offset, None


def _page(items: list, limit, offset: int) -> list:
    if offset:
        items = items[offset:]
    if limit is not None:
        items = items[:limit]
    return items


# ---------------------------------------------------------------------------
# GET /runs catalogue helpers. Titles mirror the web
# run-reference idiom (web/app/src/format.ts: fmtDay + runRef): place
# name + disruption kind + day, e.g. "Suez Canal closure · 06 Apr 23".
# ---------------------------------------------------------------------------
_MONS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
         "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _parse_iso(value) -> datetime | None:
    """Parse an ISO-8601 timestamp (Z or offset), or None."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _fmt_day(value) -> str | None:
    """"2023-04-06T00:00:00Z" -> "06 Apr 23" (format.ts fmtDay, UTC)."""
    parsed = _parse_iso(value)
    if parsed is None:
        return None
    return (f"{parsed.day:02d} {_MONS[parsed.month - 1]} "
            f"{parsed.year % 100:02d}")


def _kind_words(kind) -> str:
    """Disruption kind -> spaced words ("capacity_cut" -> "capacity
    cut"), as in the reference example ("Suez Canal closure")."""
    return str(kind or "").replace("_", " ").strip()


def _prettified_id(node_id: str) -> str:
    """Last-resort place label for a node the loaded graph does not
    name: the id prettified (format.ts nodeLabel's fallback) — never
    the raw id itself."""
    words = [w for w in re.split(r"[_\\-]+", str(node_id)) if w]
    drop = {"chk", "sea", "air", "gnd", "rail", "port"}
    while len(words) > 1 and words[0].lower() in drop:
        words.pop(0)
    return " ".join(w.capitalize() for w in words) or "Unknown place"


# Sim records are journal-complete and can be ~170 MB (the stored
# node_series / segments payloads): the catalogue must read METADATA
# ONLY. Small files parse whole; large ones are probed by a chunked
# key search + a bounded raw_decode of just the wanted values (the
# store writes with sort_keys=True, so "status" follows "sim_run_id"
# within a few bytes and "weather_window" is the final key).
_SIM_META_FULL_LOAD_MAX = 4 * 1024 * 1024
_SIM_META_KEYS = (b'"sim_run_id"', b'"created_sim_time"', b'"journal"',
                  b'"weather_window"')


def _find_keys_offsets(path: Path, keys, limit: int = 8) -> dict:
    """{key: [offsets]} for every key, in ONE chunked scan of the
    file (chunk overlap covers a key straddling a boundary)."""
    found: dict = {key: [] for key in keys}
    overlap = max(len(k) for k in keys) + 2
    try:
        size = path.stat().st_size
        with open(path, "rb") as fh:
            pos = 0
            carry = b""
            while pos < size:
                chunk = fh.read(4 * 1024 * 1024)
                if not chunk:
                    break
                blob = carry + chunk
                base = pos - len(carry)
                for key in keys:
                    offsets = found[key]
                    start = 0
                    while len(offsets) < limit:
                        idx = blob.find(key, start)
                        if idx < 0:
                            break
                        offset = base + idx
                        if not offsets or offsets[-1] != offset:
                            offsets.append(offset)
                        start = idx + 1
                carry = blob[-overlap:]
                pos += len(chunk)
    except OSError:
        return {key: [] for key in keys}
    return found


def _decode_value_at(path: Path, key_offset: int, key: bytes):
    """JSON value for the key at `key_offset`, raw_decoded from a
    bounded window — the rest of the file is never parsed."""
    try:
        with open(path, "rb") as fh:
            fh.seek(key_offset + len(key))
            window = fh.read(2 * 1024 * 1024).decode("utf-8", "replace")
    except OSError:
        return None
    window = window.lstrip()
    if not window.startswith(":"):
        return None
    window = window[1:].lstrip()
    try:
        value, _ = json.JSONDecoder().raw_decode(window)
    except ValueError:
        return None
    return value


def _sim_record_meta(path: Path) -> dict | None:
    """The catalogue's metadata slice of one sim record file, or None
    when the file is corrupt/partial (the caller then skips it).
    Only sim_run_id / status / created_sim_time / journal /
    weather_window are ever extracted."""
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size <= _SIM_META_FULL_LOAD_MAX:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        return record if isinstance(record, dict) else None
    meta: dict = {"sim_run_id": path.stem}
    found = _find_keys_offsets(path, _SIM_META_KEYS)
    for key in _SIM_META_KEYS:
        name = key.decode().strip('"')
        offsets = found.get(key) or []
        # "journal" is wanted at its top-level (first plausible)
        # occurrence; the trailing keys at their last occurrence.
        candidates = offsets if name == "journal" else offsets[-1:]
        for offset in candidates:
            value = _decode_value_at(path, offset, key)
            if name == "journal" and not (
                    isinstance(value, dict)
                    and ("disruptions" in value or "seed" in value)):
                continue
            if value is not None:
                meta[name] = value
                break
    if not isinstance(meta.get("journal"), dict):
        return None
    # status: in the store's sorted layout it is the key immediately
    # after sim_run_id; read that small window directly.
    for offset in (found.get(b'"sim_run_id"') or []):
        try:
            with open(path, "rb") as fh:
                fh.seek(offset)
                window = fh.read(512).decode("utf-8", "replace")
        except OSError:
            continue
        match = re.search(r'"status"\s*:\s*"([^"]*)"', window)
        if match:
            meta["status"] = match.group(1)
            break
    return meta


_SIM_META_CACHE: dict = {}
_SIM_META_CACHE_MAX = 512


def _sim_record_meta_cached(path: Path) -> dict | None:
    """_sim_record_meta memoised on (mtime_ns, size): GET /runs is a
    page-load route and must not rescan every large record per call
    A rewritten record changes its stat key."""
    try:
        st = path.stat()
    except OSError:
        return None
    key = str(path)
    stamp = (st.st_mtime_ns, st.st_size)
    hit = _SIM_META_CACHE.get(key)
    if hit is not None and hit[0] == stamp:
        return hit[1]
    meta = _sim_record_meta(path)
    if len(_SIM_META_CACHE) >= _SIM_META_CACHE_MAX:
        _SIM_META_CACHE.pop(next(iter(_SIM_META_CACHE)))
    _SIM_META_CACHE[key] = (stamp, meta)
    return meta


def _etag_matches(if_none_match: str | None, etag: str) -> bool:
    """True when an If-None-Match header value matches `etag`.

    Handles the RFC 9110 forms a client can send here: "*", a single
    quoted tag, a comma-separated list, and weak (W/) tags — weak tags
    compare equal for GET revalidation."""
    if not if_none_match:
        return False
    for part in if_none_match.split(","):
        tag = part.strip()
        if tag == "*":
            return True
        if tag.startswith("W/"):
            tag = tag[2:].strip()
        if tag == etag:
            return True
    return False


def _accepts_gzip(accept_encoding: str | None) -> bool:
    """True when an Accept-Encoding header permits gzip.

    An explicit ``gzip`` entry decides by its q-value (q=0 refuses);
    otherwise a ``*`` entry with q>0 permits it."""
    if not accept_encoding:
        return False
    star_q: float | None = None
    for part in accept_encoding.split(","):
        token, _, params = part.strip().partition(";")
        token = token.strip().lower()
        if not token:
            continue
        q = 1.0
        for param in params.split(";"):
            param = param.strip()
            if param.startswith("q="):
                try:
                    q = float(param[2:])
                except ValueError:
                    q = 0.0
        if token == "gzip":
            return q > 0
        if token == "*":
            star_q = q
    return bool(star_q)


def load_graph() -> tuple[Graph, str]:
    env = os.environ.get("SUPERMAP_GRAPH")
    if env:
        return Graph(json.loads(Path(env).read_text(encoding="utf-8"))), env
    data_env = os.environ.get("SUPERMAP_DATA_DIR")
    if data_env:
        cand = Path(data_env) / "graph" / "graph.json"
        if cand.exists():
            return Graph(json.loads(cand.read_text(encoding="utf-8"))), str(cand)
    real = REPO_ROOT / "data" / "graph" / "graph.json"
    if real.exists():
        return Graph(json.loads(real.read_text(encoding="utf-8"))), str(real)
    return Graph(engine_fixtures.fixture_graph()), "engine-fixture"


def _parse_tick(raw) -> int | None:
    """ASCII-decimal integer (optional leading minus) or None. ``--5``,
    ``1_0``, ``+5`` and non-ASCII digits are refused here instead of
    passing a loose isdigit() test and raising in int()."""
    if not isinstance(raw, str):
        return None
    body = raw[1:] if raw.startswith("-") else raw
    if not (body.isascii() and body.isdigit()):
        return None
    return int(raw)


def _clear_adapter_registry(registry_mod) -> None:
    """Empty the module-global adapter registry, in place.

    Delegates to the registry's own ``clear_registry()`` — the ONE
    clear, which empties both the fetchable adapter dict and the
    health-only set (money adapters) together, so an
    explicitly empty/injected registry never leaves stale health-only
    entries behind. The dict fallback below only covers a registry
    module that does not offer it; both paths mutate the dicts in
    place, never rebind, so the health router sees the change.
    """
    clear = getattr(registry_mod, "clear_registry", None)
    if callable(clear):
        clear()
        return
    reg = getattr(registry_mod, "_REGISTRY", None)
    if isinstance(reg, dict):
        reg.clear()
    health_only = getattr(registry_mod, "_HEALTH_ONLY", None)
    if isinstance(health_only, dict):
        health_only.clear()


def _annotate_key_gated(adapter) -> None:
    """Surface NEEDS-KEY for a key-gated adapter, without any fetch.

    ``needs_key()`` is a pure configuration check (``key_env`` set, env
    var absent) — the same check BaseAdapter.fetch() makes before it
    would connect. A key-gated adapter therefore truthfully cannot be
    LIVE, and the documented representation (the health-state enum has no
    NEEDS-KEY value) is state=DOWN plus a
    "NEEDS-KEY: ..." note. The note below reuses fetch()'s exact
    wording; state stays the initial DOWN and last_success_at stays
    None — nothing about a fetch is asserted or faked.
    """
    try:
        needs_key = adapter.needs_key()
    except Exception:
        return
    if not needs_key:
        return
    health = getattr(adapter, "health", None)
    if health is not None and not getattr(health, "note", None):
        health.note = f"NEEDS-KEY: set {adapter.key_env} to enable live fetch"


def _default_sim_weather_fetcher():
    """Resilient Open-Meteo fetcher for the sim weather snapshot, or None
    (sim.weather's own client) when disabled or unimportable."""
    if os.environ.get("SUPERMAP_SIM_WEATHER_ROBUST", "1") == "0":
        return None
    try:
        try:
            from adapters.open_meteo import robust_fetch_json
        except ImportError:
            from server.adapters.open_meteo import robust_fetch_json
        return robust_fetch_json
    except Exception:
        return None


def _populate_adapter_registry(registry_mod, injected) -> list:
    """Populate the adapter registry for one create_app() call.

    ``injected`` (create_app's ``adapter_registry`` argument):
      * None (default) or True -> the full default registry
        (registry.build_default_registry(): every adapter defined);
      * False, {} or [] -> explicitly empty: the registry is cleared
        and /health honestly reports zero adapters;
      * a dict of adapters, a list/tuple of adapters, or a single
        adapter -> exactly those are registered, nothing else.
    Returns the registered adapter objects.
    """
    if injected is None or injected is True:
        _clear_adapter_registry(registry_mod)
        registry_mod.build_default_registry()
        adapters = list(registry_mod.all_adapters())
        for adapter in adapters:
            _annotate_key_gated(adapter)
        return adapters
    _clear_adapter_registry(registry_mod)
    if injected is False:
        return []
    if isinstance(injected, dict):
        adapters = list(injected.values())
    elif isinstance(injected, (list, tuple, set, frozenset)):
        adapters = list(injected)
    else:  # a single adapter object
        adapters = [injected]
    for adapter in adapters:
        registry_mod.register(adapter)
    return adapters


def create_app(graph: Graph | None = None, graph_source: str = "injected",
               runs_dir: str | Path | None = None,
               disruptions: dict | None = None,
               scorecard_path: str | Path | None = None,
               graph_path: str | Path | None = None,
               events_path: str | Path | None = None,
               exposures_path: str | Path | None = None,
               satwatch_dir: str | Path | None = None,
               lenses_path: str | Path | None = None,
               portwatch_path: str | Path | None = None,
               money_quote_fetcher=None,
               adapter_registry=None,
               llm_router=None,
               ledger_path: str | Path | None = None,
               api_v1_burst: int | None = None,
               api_v1_refill_per_sec: float | None = None,
               alerts_dir: str | Path | None = None,
               sim_runs_dir: str | Path | None = None,
               sim_weather_fetcher=None,
               ensemble_stats_dir: str | Path | None = None,
               ensemble_tree_dir: str | Path | None = None,
               impact_data_dir: str | Path | None = None,
               impact_snapshots_dir: str | Path | None = None) -> FastAPI:
    if graph is None:
        graph, graph_source = load_graph()
    engine = Engine(graph)
    engine2 = Engine2(graph) if Engine2 is not None else None   # ENGINE REV 2
    # ONE data root for every file-backed store this app
    # serves — SUPERMAP_DATA_DIR when set, else the repo data/ dir.
    # Each store's own SUPERMAP_*_DIR / *_FILE override still takes
    # precedence over the root-derived default (explicit argument >
    # per-store env > data root).
    data_root = Path(os.environ.get(
        "SUPERMAP_DATA_DIR", str(REPO_ROOT / "data")))
    store = RunStore(runs_dir or os.environ.get(
        "SUPERMAP_RUNS_DIR", str(data_root / "runs")))
    # Live disruptions are NOT loaded into a boot-time closure:
    # an event appended after boot must be visible to
    # /run, /ensemble and /sim/run without a restart. When the caller
    # injects `disruptions` explicitly that mapping is used verbatim;
    # otherwise _current_disruptions() below resolves
    # the events store lazily on every request.
    _injected_disruptions = disruptions
    if scorecard_path is None:
        scorecard_path = os.environ.get(
            "SUPERMAP_SCORECARD", str(data_root / "replay" / "scorecard.json"))
    scorecard_path = Path(scorecard_path)
    # GET /graph + GET /events sources, resolved exactly like
    # the scorecard: explicit argument > env > repo default. Note the
    # graph FILE is served as stored; it is deliberately independent of
    # the (possibly injected / fixture) engine Graph above.
    if graph_path is None:
        graph_path = os.environ.get(
            "SUPERMAP_GRAPH", str(data_root / "graph" / "graph.json"))
    graph_path = Path(graph_path)
    if events_path is None:
        events_path = os.environ.get(
            "SUPERMAP_EVENTS_FILE", str(data_root / "events.jsonl"))
    events_path = Path(events_path)

    def _read_folded_events() -> list:
        """Current event state from the events store, with the
        EventStore lifecycle fold applied: later lines for
        an occurrence replace earlier ones and expiry-derived
        `resolved` is applied. Tolerant like GET /events:
        any content problem yields the rows that could be
        read (or []), never an exception out of a request."""
        path = events_path
        if not path.exists():
            alt = data_root / "disruptions.json"
            if alt.exists():
                path = alt
            else:
                return []
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            return []
        if not text.strip():
            return []
        if path.suffix == ".jsonl":
            if EventStore is not None:
                try:
                    return EventStore(path).read_all()
                except Exception:
                    pass  # fall through to the tolerant parse below
            records = []
            for line in text.splitlines():
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if isinstance(rec, dict):
                    records.append(rec)
            if _store_fold_events is not None:
                try:
                    return _store_fold_events(records)
                except Exception:
                    return records
            return records
        try:
            data = json.loads(text)
        except Exception:
            return []
        if isinstance(data, list):
            records = data
        elif isinstance(data, dict) and isinstance(data.get("events"), list):
            records = data["events"]
        else:
            return []
        records = [r for r in records if isinstance(r, dict)]
        if _store_fold_events is not None:
            try:
                return _store_fold_events(records)
            except Exception:
                return records
        return records

    def _current_disruptions() -> dict:
        """Live disruptions by id, resolved at REQUEST time.

        An explicitly injected mapping (create_app(disruptions=...))
        is returned verbatim; otherwise the events store is re-read
        and folded on every call, so an event appended after boot is
        immediately runnable via source=live."""
        if _injected_disruptions is not None:
            return _injected_disruptions
        try:
            rows = _read_folded_events()
        except Exception:
            return {}
        return {r["id"]: r for r in rows
                if isinstance(r, dict) and "id" in r}

    # More sources, resolved exactly like the ones above:
    # explicit argument > env > repo default.
    if exposures_path is None:
        exposures_path = os.environ.get(
            "SUPERMAP_EXPOSURES_FILE",
            str(REPO_ROOT / "server" / "money" / "exposures.json"))
    exposures_path = Path(exposures_path)
    if satwatch_dir is None:
        satwatch_dir = os.environ.get(
            "SUPERMAP_SATWATCH_DIR",
            str(data_root / "snapshots" / "satwatch"))
    satwatch_dir = Path(satwatch_dir)
    if lenses_path is None:
        lenses_path = os.environ.get(
            "SUPERMAP_LENSES_FILE",
            str(REPO_ROOT / "server" / "engine" / "lenses.json"))
    lenses_path = Path(lenses_path)
    # PortWatch snapshot source, resolved exactly like the
    # ones above: explicit argument > env > repo default.
    if portwatch_path is None:
        portwatch_path = os.environ.get(
            "SUPERMAP_PORTWATCH_FILE",
            str(data_root / "snapshots" / "portwatch.json"))
    portwatch_path = Path(portwatch_path)
    # IMPACT layer sources, resolved like the ones above.
    if impact_data_dir is None:
        impact_data_dir = os.environ.get(
            "SUPERMAP_IMPACT_DIR", str(data_root / "impact"))
    impact_data_dir = Path(impact_data_dir)
    if impact_snapshots_dir is None:
        impact_snapshots_dir = os.environ.get(
            "SUPERMAP_SNAPSHOTS_DIR", str(data_root / "snapshots"))
    impact_snapshots_dir = Path(impact_snapshots_dir)
    if money_quote_fetcher is None:
        money_quote_fetcher = _default_money_quote_fetcher

    app = FastAPI(title="SupermAP Run API", version="1.0")

    # CORS: one rule for CORS and the cross-site write check
    # (security.cors_origins(): SUPERMAP_CORS_ORIGINS or the Vite dev and
    # preview origins, plus SUPERMAP_ALLOWED_ORIGINS). No wildcard, no
    # credentials.
    _cors_origins = cors_origins()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-Scenario-Token", "X-Demo-Token", "X-Admin-Token",
                       "Authorization", "X-Request-Id"],
        expose_headers=["X-Request-Id", "Retry-After", "Allow"],
    )

    app.state.data_root = data_root
    app.state.impact_data_dir = impact_data_dir
    app.state.impact_snapshots_dir = impact_snapshots_dir
    @app.middleware("http")
    async def _request_context(request: Request, call_next):
        incoming = request.headers.get("x-request-id")
        # Echoed in a response header and written to the log, so only a plain token is kept.
        request_id = incoming if incoming and _REQUEST_ID_RE.fullmatch(incoming) \
            else uuid.uuid4().hex
        request.state.request_id = request_id
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            _cl = request.headers.get("content-length")
            if _cl is not None:
                try:
                    _n = int(_cl)
                except ValueError:
                    _n = 0
                _cap = body_limit_for(request.url.path, MAX_BODY_BYTES)
                if _n > _cap:
                    resp = _err("payload_too_large",
                                f"request body exceeds the {_cap}-byte "
                                "limit for mutating routes")
                    resp.headers["X-Request-Id"] = request_id
                    return resp
        response = await call_next(request)
        response.headers.setdefault("X-Request-Id", request_id)
        return response

    app.state.engine = engine
    app.state.store = store
    app.state.graph_source = graph_source
    app.state.scorecard_path = scorecard_path
    app.state.graph_path = graph_path
    # /graph payload cache — one entry {key, etag, body,
    # gzip_body} keyed by (st_mtime_ns, st_size) of the graph file;
    # None until the first successful parse. Per app instance.
    app.state.graph_cache: dict | None = None
    app.state.events_path = events_path
    app.state.exposures_path = exposures_path
    app.state.satwatch_dir = satwatch_dir
    app.state.lenses_path = lenses_path
    app.state.portwatch_path = portwatch_path
    app.state.money_quote_fetcher = money_quote_fetcher
    # LIVE TWIN flag: resolved ONCE here from
    # SUPERMAP_LIVE_TWIN — truthy is "1"/"true"/"yes"
    # (case-insensitive); anything else, including unset, is OFF.
    app.state.live_twin = os.environ.get(
        "SUPERMAP_LIVE_TWIN", "").strip().lower() in ("1", "true", "yes")
    # Public namespace: per-IP token bucket. Limits resolve
    # argument > env > production defaults (burst 120, refill 2/s — see
    # server/api/public_api.py and the docs page at /api/v1/docs). The
    # env seam allows a tiny burst to exercise the 429 path;
    # production defaults are never lowered by the seam's
    # presence alone.
    if api_v1_burst is None:
        try:
            api_v1_burst = int(os.environ.get("SUPERMAP_API_V1_BURST", "120"))
        except ValueError:
            api_v1_burst = 120
    if api_v1_refill_per_sec is None:
        try:
            api_v1_refill_per_sec = float(
                os.environ.get("SUPERMAP_API_V1_REFILL_PER_SEC", "2"))
        except ValueError:
            api_v1_refill_per_sec = 2.0
    app.state.api_v1_limiter = TokenBucketLimiter(
        burst=api_v1_burst, refill_per_sec=api_v1_refill_per_sec)
    app.state.api_v1_limits = {"burst": api_v1_burst,
                               "refill_per_sec": api_v1_refill_per_sec}
    if ledger_path is None and os.environ.get("SUPERMAP_LEDGER_FILE"):
        ledger_path = os.environ["SUPERMAP_LEDGER_FILE"]
    elif ledger_path is None and os.environ.get("SUPERMAP_DATA_DIR"):
        # The LedgerStore's own default is repo-hard-coded;
        # the app layer wires the unified root in here (the value is
        # passed to the llm router's build_router and used by the
        # public-ledger loader below).
        ledger_path = str(data_root / "ledger.jsonl")
    app.state.ledger_path = Path(ledger_path) if ledger_path else None
    # Alerts & watchlists: resolved exactly like the
    # other file-backed sources — explicit argument > env > repo
    # default (data/alerts/). The store is directory-backed JSONL;
    # constructing it touches no files until a rule/watchlist/fired
    # alert is actually written.
    if alerts_dir is None:
        alerts_dir = os.environ.get(
            "SUPERMAP_ALERTS_DIR", str(data_root / "alerts"))
    app.state.alerts_dir = Path(alerts_dir)
    app.state.alerts_store = AlertsStore(app.state.alerts_dir) \
        if AlertsStore is not None else None
    if app.state.alerts_store is not None:
        try:  # raw owner tokens on disk -> one-way keys
            app.state.alerts_store.migrate_owner_tokens()
        except Exception:
            log.warning("alerts owner-token migration failed", exc_info=True)
    # Sim run store, resolved like the other
    # stores — explicit argument > env > repo default (data/sim_runs/).
    # The weather fetcher is injectable (a callable
    # (url, params) -> parsed JSON for sim.weather.fetch_snapshot);
    # None means the real Open-Meteo client.
    if sim_runs_dir is None:
        sim_runs_dir = os.environ.get(
            "SUPERMAP_SIM_RUNS_DIR", str(data_root / "sim_runs"))
    app.state.sim_runs_dir = Path(sim_runs_dir)
    app.state.sim_store = SimStore(app.state.sim_runs_dir) \
        if SimStore is not None else None
    # The default Open-Meteo client stalls on
    # ~40 % of new connections and has no retry/quota handling (see
    # adapters/open_meteo.py); the default is
    # the resilient fetcher. SUPERMAP_SIM_WEATHER_ROBUST=0 restores
    # sim.weather's own client; an injected fetcher always wins.
    app.state.sim_weather_fetcher = sim_weather_fetcher \
        if sim_weather_fetcher is not None else _default_sim_weather_fetcher()
    # SIM async builds: the single-flight job
    # registry for POST /sim/run's create-then-poll contract.
    # Jobs are IN-MEMORY ONLY — nothing about a building/failed job is
    # persisted; after a restart the registry is empty, the status
    # route honestly 404s an id with no stored record, and a re-POST
    # of the same body re-derives the same sim_run_id and rebuilds
    # idempotently (the stored record is the only durable state, and
    # an existing record short-circuits creation exactly as before).
    # Registry shape: sim_run_id -> {"status": "building" | "failed"
    # | "complete", "started_at": ISO-8601 str, "error": None |
    # {"code", "message"}}, guarded by sim_jobs_lock; finished entries
    # are evicted beyond the last SIM_JOB_REGISTRY_FINISHED_MAX,
    # building entries never. Builds run on sim_job_pool (bounded:
    # SIM_JOB_POOL_WORKERS workers + SIM_JOB_POOL_QUEUE queued,
    # mirroring ensstats BoundedJobPool).
    app.state.sim_jobs: dict = {}
    app.state.sim_jobs_lock = threading.Lock()
    app.state.sim_job_workers = _sim_build_workers()
    app.state.sim_job_pool = (
        BoundedJobPool(workers=app.state.sim_job_workers,
                       queue_size=SIM_JOB_POOL_QUEUE, name="sim-pool")
        if BoundedJobPool is not None
        else _SimFallbackPool(workers=app.state.sim_job_workers,
                              name="sim-pool"))
    # Computed-payload caches for the sim read
    # path. Sim records are immutable per id (content-hash ids — see
    # server/sim/store.py), and state_at / compute_markers are pure
    # functions of the stored record, so a computed payload can be
    # served verbatim forever: no invalidation beyond LRU eviction.
    # The ONE exception is markers' chain_event component, which
    # also reads the chain run store — its cache key therefore carries
    # a cheap stat fingerprint of that store (below), so a chain run
    # created after a markers fetch is still picked up on the next
    # fetch, exactly as the uncached code did. Payloads are shared
    # objects; handlers and FastAPI only read/serialise them.
    app.state.sim_weather_cache: dict = {}
    app.state.sim_state_cache: OrderedDict = OrderedDict()
    try:
        _state_mb = int(os.environ.get("SUPERMAP_SIM_STATE_CACHE_MB",
                                       SIM_STATE_CACHE_DEFAULT_MB))
    except (TypeError, ValueError):
        _state_mb = SIM_STATE_CACHE_DEFAULT_MB
    app.state.sim_state_cache_max_bytes = max(1, _state_mb) << 20
    app.state.sim_state_cache_bytes = 0
    app.state.sim_markers_cache: OrderedDict = OrderedDict()
    # Ensemble statistics: job store, resolved like the
    # other stores — explicit argument > env > repo default
    # (data/ensemble_stats/). Jobs compute on background threads and
    # write only here: never the run store, Ledger or scorecard.
    if ensemble_stats_dir is None:
        ensemble_stats_dir = os.environ.get(
            "SUPERMAP_ENSEMBLE_STATS_DIR",
            str(data_root / "ensemble_stats"))
    app.state.ensemble_stats_dir = Path(ensemble_stats_dir)
    app.state.ensstats_jobs = EnsembleStatsJobs(
        engine, EnsembleStatsStore(app.state.ensemble_stats_dir)) \
        if EnsembleStatsJobs is not None and EnsembleStatsStore is not None \
        else None
    # Ensemble TREE jobs: their OWN store directory,
    # resolved exactly like the flat jobs' store — explicit argument >
    # env > repo default (data/ensemble_tree/). The separation is
    # structural: the flat store's listing/summary must never see a
    # tree record (its summary would not know the tree fields).
    if ensemble_tree_dir is None:
        ensemble_tree_dir = os.environ.get(
            "SUPERMAP_ENSEMBLE_TREE_DIR",
            str(data_root / "ensemble_tree"))
    app.state.ensemble_tree_dir = Path(ensemble_tree_dir)
    app.state.enstree_jobs = EnsembleTreeJobs(
        engine, TreeJobStore(app.state.ensemble_tree_dir)) \
        if EnsembleTreeJobs is not None and TreeJobStore is not None \
        else None
    # Ensemble EXPLORATIONS: interactive deepening over
    # the flat jobs. Their store is rooted at a subdirectory of the
    # RESOLVED flat store directory (<ensemble_stats_dir>/
    # explorations): the flat store's listing globs only its own
    # directory, so exploration records never appear as flat jobs.
    # The service wraps the SAME flat jobs object the flat routes use.
    app.state.ens_explorations = EnsembleExplorations(
        app.state.ensstats_jobs,
        EnsembleStatsStore(app.state.ensemble_stats_dir / "explorations")) \
        if EnsembleExplorations is not None \
        and EnsembleStatsStore is not None \
        and app.state.ensstats_jobs is not None \
        else None

    @app.exception_handler(EngineError)
    async def _engine_error(_req: Request, exc: EngineError):
        return _err(exc.code, str(exc))

    @app.exception_handler(GraphError)
    async def _graph_error(_req: Request, exc: GraphError):
        return _err("invalid_request", str(exc))

    # Safety net for the /llm family: the llm router already
    # translates its own failures into the standard shape inside its
    # handlers (see server/llm/router.py), so these handlers only fire
    # for an llm exception raised OUTSIDE those handlers (e.g. from an
    # injected router). The imports are defensive, like every other
    # cross-module seam in this module: an unavailable llm package must
    # never take app creation down.
    _LLMRequestError = _LedgerError = _classify_ledger_error = None
    for _llm_modname in ("llm.router", "server.llm.router"):
        try:
            import importlib as _importlib
            _llm_mod = _importlib.import_module(_llm_modname)
            _LLMRequestError = getattr(_llm_mod, "LLMRequestError", None)
            _classify_ledger_error = getattr(
                _llm_mod, "classify_ledger_error", None)
            _ledger_mod = _importlib.import_module(
                _llm_modname.rsplit(".router", 1)[0] + ".ledger")
            _LedgerError = getattr(_ledger_mod, "LedgerError", None)
            break
        except Exception:
            _LLMRequestError = _LedgerError = _classify_ledger_error = None
            continue
    if _LLMRequestError is not None:
        @app.exception_handler(_LLMRequestError)
        async def _llm_request_error(_req: Request, exc):
            return JSONResponse(
                status_code=exc.status,
                content={"error": {"code": exc.code, "message": exc.message}})
    if _LedgerError is not None:
        @app.exception_handler(_LedgerError)
        async def _ledger_error(_req: Request, exc):
            if _classify_ledger_error is not None:
                code, status = _classify_ledger_error(exc)
            else:  # pragma: no cover - router module without the mapper
                code, status = "ledger_invalid", 422
            return JSONResponse(
                status_code=status,
                content={"error": {"code": code, "message": str(exc)}})

    @app.exception_handler(ValueError)
    async def _value_error(_req: Request, exc: ValueError):
        # No request input may produce a bare
        # 500. A bad run_id (e.g. URL-decoded ".." traversal) is an
        # unknown run -> standard 404 shape; any other ValueError
        # escaping a handler is a malformed request -> standard 422.
        if str(exc) == "bad run_id":
            return _err("unknown_run", "unknown run_id")
        return _err("invalid_request", scrub_paths(exc))

    # The module docstring promises "one shape
    # everywhere", so framework-level failures (an
    # unmatched route or a wrong method, whose FastAPI default is a
    # {"detail": ...} body, e.g. GET /money/node/ with an empty id, or
    # GET /run) are mapped into the standard shape too, and any
    # still-unhandled exception gets a standard-shape 500 (no stack trace,
    # no leaked internals) instead of Starlette's plain-text body.
    from starlette.exceptions import HTTPException as _StarletteHTTPError

    @app.exception_handler(_StarletteHTTPError)
    async def _http_error(_req: Request, exc):
        if exc.status_code == 404:
            return _err("not_found", "resource not found")
        if exc.status_code == 405:
            resp = _err("method_not_allowed", "method not allowed")
            allow = next((v for k, v in (getattr(exc, "headers", None) or {}).items()
                          if str(k).lower() == "allow"), None)
            if allow:
                resp.headers["Allow"] = allow
            return resp
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": "http_error",
                               "message": str(exc.detail)}})

    # FastAPI's own request validation (typed path / query /
    # body parameters, e.g. `body: dict | None`) would answer
    # {"detail": [{..., "input": <the request body>}]}: the wrong shape,
    # an echo of the caller's input, and a RecursionError -> 500 when
    # that input is deeply nested. Same house shape as everywhere
    # else, built from the location and message only.
    from fastapi.exceptions import RequestValidationError as _ReqValError

    @app.exception_handler(_ReqValError)
    async def _validation_error(_req: Request, exc):
        errors = exc.errors()
        parts = []
        for err in errors[:5]:
            loc = ".".join(str(p)[:60] for p in (err.get("loc") or ())[:6])
            parts.append(f"{loc}: {str(err.get('msg', 'invalid'))[:120]}")
        bad_json = any(err.get("type") == "json_invalid" for err in errors)
        return _err("bad_json" if bad_json else "invalid_request",
                    "; ".join(parts) or "invalid request")

    @app.exception_handler(Exception)
    async def _unhandled_error(req: Request, exc: Exception):
        request_id = getattr(req.state, "request_id", "-")
        log.error("unhandled error request_id=%s method=%s path=%s",
                  request_id, req.method, req.url.path, exc_info=exc)
        resp = _err("internal_error", "internal error")
        resp.headers["X-Request-Id"] = request_id
        return resp

    # -- alert evaluation seams -------------------------------------------
    # Facts are gathered from the same sources this app serves (events
    # file, ledger file, run store, graph file, the adapter registry's
    # health board). Evaluation is a no-op until at least one enabled
    # rule exists, and a failure inside it must never break the request
    # that triggered it (the autolock discipline) — it is swallowed
    # here and surfaces only as alerts that did not fire.
    def _alerts_health() -> list:
        registry_mod = getattr(app.state, "adapter_registry", None)
        if registry_mod is not None:
            try:
                return registry_mod.health_report()
            except Exception:
                pass
        return []

    def _alerts_facts() -> dict:
        ledger_file = app.state.ledger_path or (data_root / "ledger.jsonl")
        return alerts_evaluator.gather_facts(
            events_path=events_path, ledger_path=ledger_file,
            runs_dir=store.dir, graph_path=graph_path,
            health=_alerts_health())

    def _alerts_evaluate() -> list:
        if app.state.alerts_store is None or alerts_evaluator is None:
            return []
        try:
            rules = app.state.alerts_store.list_rules()
            if not any(r.get("enabled", True) is True for r in rules):
                return []
            return alerts_evaluator.evaluate(
                app.state.alerts_store, _alerts_facts())
        except Exception:
            return []

    def _alerts_prime() -> None:
        if app.state.alerts_store is None or alerts_evaluator is None:
            return
        try:
            alerts_evaluator.prime(app.state.alerts_store, _alerts_facts())
        except Exception:
            pass

    # ------------------------------------------------------------------
    # GET /runs — catalogue of ALL stored runs. Metadata only:
    # journals are read for their disruption / seed / timing fields and
    # no result payload (events, impacts, branch aggregates, sim
    # series) is ever served. A corrupt/partial file is SKIPPED, never
    # a 500. Titles are built here, from the loaded graph's place
    # names, in the web run-reference idiom (format.ts runRef: place +
    # disruption kind + day); a journal that cannot yield one is listed
    # under its kind label (+ date when one exists) — the raw id
    # travels in `id` only, never as a title.
    # ------------------------------------------------------------------
    def _place_name(node_id) -> str:
        node = graph.nodes.get(node_id) if isinstance(node_id, str) else None
        if isinstance(node, dict) and node.get("name"):
            return str(node["name"])
        return _prettified_id(str(node_id or ""))

    def _disruption_title(disruption, fallback_label: str) -> str:
        """Place + kind + day from one disruption spec (the runRef
        idiom), or the kind-label fallback when the spec cannot yield
        a human title."""
        if isinstance(disruption, dict) and disruption.get("node_id"):
            title = _place_name(disruption["node_id"])
            words = _kind_words(disruption.get("kind"))
            if words:
                title = f"{title} {words}"
            day = _fmt_day(disruption.get("started_at"))
            if day:
                title = f"{title} · {day}"
            return title
        return fallback_label

    def _catalogue_entry(run_id, kind, title, created_at, status,
                         facts) -> dict:
        parsed = _parse_iso(created_at)
        return {"id": run_id, "kind": kind, "title": title,
                "created_at": created_at if parsed is not None else None,
                "status": status, "facts": facts,
                "_ts": parsed.timestamp() if parsed is not None else None}

    def _chain_catalogue() -> list:
        entries = []
        try:
            paths = sorted(store.dir.glob("*.json"), key=lambda p: p.name)
        except OSError:
            return entries
        for path in paths:
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue  # corrupt/partial: skipped, never a 500
            if not isinstance(record, dict):
                continue
            journal = record.get("journal")
            if not isinstance(journal, dict):
                continue
            disruption = journal.get("disruption")
            if not isinstance(disruption, dict):
                parent = journal.get("parent_journal")
                disruption = parent.get("disruption") \
                    if isinstance(parent, dict) else None
            started_at = disruption.get("started_at") \
                if isinstance(disruption, dict) else None
            fallback = "Chain run"
            day = _fmt_day(started_at)
            if day:
                fallback = f"{fallback} · {day}"
            facts = {
                "node_id": disruption.get("node_id")
                if isinstance(disruption, dict) else None,
                "disruption_kind": disruption.get("kind")
                if isinstance(disruption, dict) else None,
                "started_at": started_at,
                "event_count": record.get("event_count"),
            }
            entries.append(_catalogue_entry(
                record.get("run_id") or path.stem, "chain",
                _disruption_title(disruption, fallback), started_at,
                record.get("status"), facts))
        return entries

    def _ensemble_catalogue() -> list:
        entries = []
        jobs = app.state.ensstats_jobs
        summaries = None
        if jobs is not None:
            try:
                summaries = jobs.list_summaries(limit=100000)
            except Exception:
                summaries = None
        if summaries is None:
            # Service unavailable: read the flat store directly (its
            # loader already reads corrupt files as absent).
            summaries = []
            if EnsembleStatsStore is not None:
                flat_store = EnsembleStatsStore(app.state.ensemble_stats_dir)
                for job_id in flat_store.list_ids():
                    record = flat_store.load(job_id)
                    if record is not None:
                        summaries.append(record)
        for summary in summaries:
            if not isinstance(summary, dict) or not summary.get("id"):
                continue
            disruption = summary.get("disruption")
            planned = summary.get("variants_planned")
            created_at = summary.get("created_at")
            fallback = "Ensemble"
            day = _fmt_day(disruption.get("started_at")
                           if isinstance(disruption, dict) else None) \
                or _fmt_day(created_at)
            if day:
                fallback = f"{fallback} · {day}"
            title = _disruption_title(disruption, fallback)
            if isinstance(planned, int):
                title = f"{title} · {planned:,} variants"
            entries.append(_catalogue_entry(
                summary["id"], "ensemble", title, created_at,
                summary.get("status"),
                {"disruption": disruption,
                 "variants_planned": planned,
                 "variants_requested": summary.get("variants_requested"),
                 "completed_at": summary.get("completed_at")}))
        return entries

    def _ensemble_tree_catalogue() -> list:
        entries = []
        jobs = app.state.enstree_jobs
        summaries = None
        if jobs is not None:
            try:
                summaries = jobs.list_summaries(limit=100000)
            except Exception:
                summaries = None
        if summaries is None:
            summaries = []
            if TreeJobStore is not None:
                tree_store = TreeJobStore(app.state.ensemble_tree_dir)
                for job_id in tree_store.list_ids():
                    record = tree_store.load_meta(job_id)
                    if record is not None:
                        summaries.append(record)
        for summary in summaries:
            if not isinstance(summary, dict) or not summary.get("id"):
                continue
            disruption = summary.get("disruption")
            quote = summary.get("quote") if isinstance(
                summary.get("quote"), dict) else {}
            total = quote.get("total_runs_including_base")
            if total is None:
                total = quote.get("quoted_variants")
            created_at = summary.get("created_at")
            fallback = "Ensemble tree"
            day = _fmt_day(disruption.get("started_at")
                           if isinstance(disruption, dict) else None) \
                or _fmt_day(created_at)
            if day:
                fallback = f"{fallback} · {day}"
            title = _disruption_title(disruption, fallback)
            if isinstance(total, int):
                title = f"{title} · {total:,} variants"
            entries.append(_catalogue_entry(
                summary["id"], "ensemble-tree", title, created_at,
                summary.get("status"),
                {"disruption": disruption,
                 "branching": summary.get("branching"),
                 "depth": summary.get("depth"),
                 "variants": total,
                 "completed_at": summary.get("completed_at")}))
        return entries

    def _sim_catalogue() -> list:
        entries = []
        sim_dir = app.state.sim_runs_dir
        try:
            paths = sorted(sim_dir.glob("*.json"), key=lambda p: p.name)
        except OSError:
            return entries
        for path in paths:
            meta = _sim_record_meta_cached(path)
            if meta is None:
                continue  # corrupt/partial: skipped, never a 500
            journal = meta.get("journal")
            if not isinstance(journal, dict):
                continue
            disruptions = journal.get("disruptions")
            n_disruptions = len(disruptions) \
                if isinstance(disruptions, list) else None
            seed = journal.get("seed")
            start_at = journal.get("start_at")
            parts = ["Simulation"]
            day = _fmt_day(start_at)
            if day:
                parts.append(day)
            if n_disruptions is not None:
                parts.append(f"{n_disruptions} disruptions")
            if seed is not None:
                parts.append(f"seed {seed}")
            window = meta.get("weather_window")
            window_state = window.get("state") \
                if isinstance(window, dict) else (
                    window if isinstance(window, str) else None)
            created_at = meta.get("created_sim_time") or start_at
            entries.append(_catalogue_entry(
                meta.get("sim_run_id") or path.stem, "sim",
                " · ".join(parts), created_at, meta.get("status"),
                {"disruptions": n_disruptions, "seed": seed,
                 "start_at": start_at, "tick": journal.get("tick"),
                 "ticks": journal.get("ticks"),
                 "created_sim_time": meta.get("created_sim_time"),
                 "weather_window": window_state}))
        return entries

    @app.get("/runs")
    async def list_runs():
        """Every stored run of all four kinds, newest first where a
        timestamp exists (untimestamped entries last)."""
        entries = (_chain_catalogue() + _ensemble_catalogue()
                   + _ensemble_tree_catalogue() + _sim_catalogue())
        entries.sort(key=lambda e: (e["_ts"] is None,
                                    -(e["_ts"] or 0.0)))
        for entry in entries:
            entry.pop("_ts", None)
        return {"runs": entries}

    @app.get("/")
    async def root():
        return {"service": "supermap-run-api", "contract": "run API v1.0",
                "graph_source": public_source(graph_source, REPO_ROOT),
                "graph_hash": graph.hash,
                "graph_version": graph.version,
                "nodes": len(graph.nodes), "edges": len(graph.edges)}

    @app.post("/run", status_code=201)
    async def post_run(req: Request):
        try:
            body = await req.json()
        except Exception:
            return _err("bad_json", "request body must be JSON")
        if not isinstance(body, dict):
            return _err("invalid_request", "request body must be an object")
        source = body.get("source")
        params_in = body.get("params") or {}
        if not isinstance(params_in, dict):
            return _err("invalid_request", "params must be an object")
        engine_rev, rev_err = _parse_engine_rev(body)
        if rev_err is not None:
            return rev_err
        profile, prof_err = _parse_profile(body, engine_rev)
        if prof_err is not None:
            return prof_err
        try:
            params = validate_params(params_in)
            if source == "scenario":
                scenario = body.get("scenario")
                if scenario is not None and not isinstance(scenario, dict):
                    return _err("invalid_request", "scenario must be an object")
                spec = (scenario or {}).get("disruption")
                if spec is None:
                    return _err("invalid_request", "scenario.disruption is required")
                from engine.engine import validate_disruption
                # engine.graph is the CORE-tier view (core/shell tiers):
                # chain runs validate against it, so a disruption at a
                # SHELL node is refused as unknown to the chain engine.
                spec = validate_disruption(engine.graph, spec)
                journal_core = {"kind": "run", "graph_hash": graph.hash,
                                "disruption": spec, "params": params}
            elif source == "live":
                live = body.get("live")
                if live is not None and not isinstance(live, dict):
                    return _err("invalid_request", "live must be an object")
                did = (live or {}).get("disruption_id")
                if did is not None and not isinstance(did, str):
                    return _err("invalid_request", "disruption_id must be a string")
                event = _current_disruptions().get(did)
                if event is None:
                    return _err("unknown_disruption", f"unknown disruption_id: {did!r}")
                if event.get("status") == "resolved":
                    return _err("disruption_resolved",
                                f"disruption {did} is resolved; live runs require an active event")
                spec = {"node_id": event["node_id"], "kind": event["kind"],
                        "capacity_loss": event.get("capacity_loss"),
                        "duration_d": event.get("expected_duration_d") or 7.0,
                        "shape": "step",
                        "started_at": event.get("started_at") or event.get("detected_at")}
                from engine.engine import validate_disruption
                spec = validate_disruption(engine.graph, spec)
                journal_core = {"kind": "run", "graph_hash": graph.hash,
                                "disruption": spec, "params": params,
                                "live_disruption_id": did}
            else:
                return _err("invalid_request", "source must be 'scenario' or 'live'")
        except EngineError as exc:
            return _err(exc.code, str(exc))

        # ENGINE REV 2 is journaled in the core, so its run id differs from
        # the rev-1 id of the same scenario; rev-1 bodies add nothing here
        # (their ids and bytes are unchanged).
        rev_extra = {}
        run_engine = engine2 if engine_rev == 2 else engine
        if engine_rev == 2:
            journal_core = {**journal_core, **_rev2_stamp()}
            rev_extra = {"engine_rev": 2, "engine_label": journal_core["engine_label"]}
        # ROUTES TABLE from the graph services layer (opt-in, rev 2 only):
        # the selection, the sidecar's file hash and the table digest fold
        # into the journal core ONLY when the body asks, so every other
        # body keeps its run id and bytes.
        routes_sel = None
        if "routes" in body and body["routes"] is not None and body["routes"] is not False:
            if engine_rev != 2:
                return _err("invalid_request", "routes requires engine_rev 2")
            value = True if body["routes"] == "services" else body["routes"]
            if isinstance(value, str):
                return _err("invalid_request", "routes must be 'services' or an object")
            try:
                from graph_services.runtime import selection_for_run
                from engine2.journal import get_routed_engine2, routes_stamp
            except Exception:  # imported as server.api.* from the repo root
                from server.graph_services.runtime import selection_for_run  # type: ignore
                from server.engine2.journal import get_routed_engine2, routes_stamp  # type: ignore
            started = journal_core["disruption"].get("started_at") or ""
            routes_sel, _svc, routes_err = selection_for_run(
                graph.raw, graph.hash, value, started)
            if routes_err:
                return _err("invalid_request", routes_err)
            if profile is None:
                run_engine = get_routed_engine2(graph, routes_sel)
                journal_core = {**journal_core, **routes_stamp(run_engine, routes_sel)}
            rev_extra = {**rev_extra, "routes": "services"}
        if profile is not None:
            # ENGINE REV 2 profile d: journaled (label, registry hash, geometry hash, services
            # file hash, routes selection), so its run ids differ from every default id
            try:
                run_engine = get_profile_engine2(graph, routes_sel, profile)
            except Exception as exc:
                return _err("invalid_request", f"profile {profile} unavailable: {_safe_exc(exc)}")
            journal_core = {**journal_core, **_profile_stamp(run_engine)}
            rev_extra = {**rev_extra, "engine_label": journal_core["engine_label"], "profile": profile}
        # GRAPH EXTRA sidecar (opt-in, rev 2 only; data/graph/extra.json):
        # the selection, the file hash and the digests of the lane table
        # and merged graph fold into the journal core ONLY when the body
        # asks. It composes with ``routes``: the services table is then
        # built on the merged core and carries the sidecar's lanes.
        if "extra" in body and body["extra"] is not None and body["extra"] is not False:
            if engine_rev != 2:
                return _err("invalid_request", "extra requires engine_rev 2")
            if profile is not None:
                return _err("invalid_request", "extra cannot be combined with profile")
            try:
                from graph_extra.runtime import selection_for_run as extra_selection_for_run
                from engine2.journal import extra_stamp, get_extra_engine2
            except Exception:  # imported as server.api.* from the repo root
                from server.graph_extra.runtime import selection_for_run as extra_selection_for_run  # type: ignore
                from server.engine2.journal import extra_stamp, get_extra_engine2  # type: ignore
            extra_sel, _ex, extra_err = extra_selection_for_run(
                graph.raw, graph.hash, body["extra"], "engine2")
            if extra_err:
                return _err("invalid_request", extra_err)
            run_engine = get_extra_engine2(graph, extra_sel, routes_sel)
            journal_core = {**journal_core, **extra_stamp(run_engine, extra_sel)}
            if routes_sel:
                journal_core = {**journal_core, **routes_stamp(run_engine, routes_sel)}
            rev_extra = {**rev_extra, "extra": "graph_extra"}
        run_id = derive_run_id(journal_core)
        if store.exists(run_id):
            record = store.load(run_id)
            return JSONResponse(status_code=201, content={
                "run_id": run_id, "status": record["status"],
                "event_count": record["event_count"],
                "stream_hash": record["stream_hash"], **rev_extra})
        journal = {**journal_core, "graph_version": graph.version}
        state = run_engine.run(run_id, journal["disruption"], params)
        record = store.save(run_id, journal, state)
        _alerts_evaluate()  # alerts seam: run_outcome rules see this run
        return JSONResponse(status_code=201, content={
            "run_id": run_id, "status": record["status"],
            "event_count": record["event_count"],
            "stream_hash": record["stream_hash"], **rev_extra})

    @app.post("/ensemble")
    async def post_ensemble(req: Request):
        """Ensembles surface: POST /ensemble runs the
        engine's fixed 27-variant causal ensemble (engine.ensemble.
        run_ensemble — queue-sensitivity x threshold x duration grid)
        for one disruption and returns its result: ranked recurring
        bottlenecks whose ``hits`` are COUNTS of variant runs (never
        percentages, never probabilities), the variant count, and the
        base run's stream identity. Input is the same shape POST /run
        accepts ({source: scenario|live, scenario|live, params?}, or a
        bare {disruption: ...}); validation is strict —
        wrong types are rejected in the standard error shape, never
        coerced, never a 500. Each ranked entry additionally carries
        ``worst_delay_d``: the maximum delay_d recorded for that node
        across the 27 variant runs (a delay in days, computed on the
        same grid), and the top level carries ``base_run`` — the
        deterministic centre-grid run (queue_sensitivity 1.0,
        hit_threshold 0.15, duration x1.0) identified exactly as
        POST /run would identify it (derive_run_id + stream_hash).
        Fully deterministic: identical inputs give identical payloads.
        Nothing is persisted — an ensemble is a computation, not a run.
        """
        if run_ensemble is None:
            return _err("ensemble_unavailable",
                        "ensemble engine (server/engine/ensemble.py) is unavailable")
        try:
            body = await req.json()
        except Exception:
            return _err("bad_json", "request body must be JSON")
        if not isinstance(body, dict):
            return _err("invalid_request", "request body must be an object")
        params_in = body.get("params") or {}
        if not isinstance(params_in, dict):
            return _err("invalid_request", "params must be an object")
        engine_rev, rev_err = _parse_engine_rev(body)
        if rev_err is not None:
            return rev_err
        eng = engine2 if engine_rev == 2 else engine
        if body.get("routes") is not None and body.get("routes") is not False:
            return _err("invalid_request", "routes is not supported on /ensemble")
        if body.get("profile") is not None:
            return _err("invalid_request", "profile is not supported on /ensemble")
        if body.get("extra") is not None and body.get("extra") is not False:
            return _err("invalid_request", "extra is not supported on /ensemble")
        try:
            # Params are validated for /run parity (unknown / out-of-
            # bounds rejected) even though the ensemble grid itself is
            # fixed: a malformed request is malformed either way.
            validate_params(params_in)
            if body.get("source") not in (None, "scenario", "live"):
                return _err("invalid_request", "source must be 'scenario' or 'live'")
            if "disruption" in body:
                spec = validate_disruption(engine.graph, body.get("disruption"))
            elif body.get("source") == "scenario" or "scenario" in body:
                scenario = body.get("scenario")
                if scenario is not None and not isinstance(scenario, dict):
                    return _err("invalid_request", "scenario must be an object")
                spec_raw = (scenario or {}).get("disruption")
                if spec_raw is None:
                    return _err("invalid_request", "scenario.disruption is required")
                spec = validate_disruption(engine.graph, spec_raw)
            elif body.get("source") == "live" or "live" in body:
                live = body.get("live")
                if live is not None and not isinstance(live, dict):
                    return _err("invalid_request", "live must be an object")
                did = (live or {}).get("disruption_id")
                if did is not None and not isinstance(did, str):
                    return _err("invalid_request", "disruption_id must be a string")
                event = _current_disruptions().get(did)
                if event is None:
                    return _err("unknown_disruption", f"unknown disruption_id: {did!r}")
                if event.get("status") == "resolved":
                    return _err("disruption_resolved",
                                f"disruption {did} is resolved; live runs require an active event")
                spec = validate_disruption(engine.graph, {
                    "node_id": event["node_id"], "kind": event["kind"],
                    "capacity_loss": event.get("capacity_loss"),
                    "duration_d": event.get("expected_duration_d") or 7.0,
                    "shape": "step",
                    "started_at": event.get("started_at") or event.get("detected_at")})
            else:
                return _err("invalid_request", "scenario.disruption is required")
        except EngineError as exc:
            return _err(exc.code, str(exc))

        result = run_ensemble(eng, spec)
        # worst_delay_d per node over the SAME fixed grid (read-only
        # re-run of the grid; delays are days, not probabilities).
        worst: dict[str, float] = {}
        n = 0
        for qs in ENS_QS_GRID:
            for thr in ENS_THR_GRID:
                for mult in ENS_DURATION_MULTS:
                    n += 1
                    variant = dict(spec)
                    variant["duration_d"] = spec["duration_d"] * mult
                    vstate = eng.run(f"ens_{n}", variant,
                                     {"queue_sensitivity": qs,
                                      "hit_threshold": thr})
                    for ev in vstate.events:
                        d = float(ev["delay_d"])
                        if d > worst.get(ev["node_id"], float("-inf")):
                            worst[ev["node_id"]] = d
        ranked = []
        for entry in result["ranked_bottlenecks"]:
            ranked.append({**entry,
                           "worst_delay_d": round(worst.get(entry["node_id"], 0.0), 6)})
        # Base run identity: the centre-grid variant, derived exactly
        # as POST /run derives a scenario run with those params.
        base_params = {"queue_sensitivity": 1.0, "hit_threshold": 0.15}
        base_journal_core = {"kind": "run", "graph_hash": graph.hash,
                             "disruption": spec, "params": base_params}
        rev_extra = {}
        if engine_rev == 2:
            base_journal_core = {**base_journal_core, **_rev2_stamp()}
            rev_extra = {"engine_rev": 2,
                         "engine_label": base_journal_core["engine_label"]}
        base_run_id = derive_run_id(base_journal_core)
        base_state = eng.run(base_run_id, spec, base_params)
        return {**rev_extra,
            "variants": result["variants"],
            "ranked_bottlenecks": ranked,
            "base_run": {"run_id": base_run_id,
                         "stream_hash": stream_hash(base_state.events),
                         "event_count": len(base_state.events)},
            "disruption": spec,
            "grid": {"queue_sensitivity": list(ENS_QS_GRID),
                     "hit_threshold": list(ENS_THR_GRID),
                     "duration_mult": list(ENS_DURATION_MULTS)},
        }

    # ------------------------------------------- ensemble statistics
    # Statistics layer: jobs over the engine's
    # causal-variant statistics (server/engine/ensemble_stats.py +
    # server/ensstats/). Validation mirrors POST /ensemble (strict
    # validation, spec resolved against engine.graph — the CORE-tier
    # view, so a disruption at a SHELL place is refused as unknown to
    # the chain engine, the same seam as the Ledger rule).
    def _resolve_ensemble_spec(body: dict):
        """(spec, error) — the /run + /ensemble input shapes."""
        if body.get("source") not in (None, "scenario", "live"):
            return None, _err("invalid_request",
                              "source must be 'scenario' or 'live'")
        try:
            if "disruption" in body:
                return validate_disruption(engine.graph,
                                           body.get("disruption")), None
            if body.get("source") == "scenario" or "scenario" in body:
                scenario = body.get("scenario")
                if scenario is not None and not isinstance(scenario, dict):
                    return None, _err("invalid_request",
                                      "scenario must be an object")
                spec_raw = (scenario or {}).get("disruption")
                if spec_raw is None:
                    return None, _err("invalid_request",
                                      "scenario.disruption is required")
                return validate_disruption(engine.graph, spec_raw), None
            if body.get("source") == "live" or "live" in body:
                live = body.get("live")
                if live is not None and not isinstance(live, dict):
                    return None, _err("invalid_request", "live must be an object")
                did = (live or {}).get("disruption_id")
                if did is not None and not isinstance(did, str):
                    return None, _err("invalid_request",
                                      "disruption_id must be a string")
                event = _current_disruptions().get(did)
                if event is None:
                    return None, _err("unknown_disruption",
                                      f"unknown disruption_id: {did!r}")
                if event.get("status") == "resolved":
                    return None, _err(
                        "disruption_resolved",
                        f"disruption {did} is resolved; live runs require "
                        f"an active event")
                return validate_disruption(engine.graph, {
                    "node_id": event["node_id"], "kind": event["kind"],
                    "capacity_loss": event.get("capacity_loss"),
                    "duration_d": event.get("expected_duration_d") or 7.0,
                    "shape": "step",
                    "started_at": event.get("started_at")
                    or event.get("detected_at")}), None
            return None, _err("invalid_request",
                              "scenario.disruption is required")
        except EngineError as exc:
            return None, _err(exc.code, str(exc))

    def _ens_jobs_or_err():
        jobs = app.state.ensstats_jobs
        if jobs is None:
            return None, _err("ensemble_stats_unavailable",
                              "ensemble statistics service "
                              "(server/ensstats) is unavailable")
        return jobs, None

    def _ens_job_id(job_id: str):
        """(ok, error) — job ids are server-derived; anything else is
        simply not a job (traversal included), in the 404 shape."""
        if not _ENS_JOB_ID_RE.match(job_id or ""):
            return None, _err("ensemble_stats_not_found",
                              f"unknown ensemble statistics job: {job_id!r}")
        return job_id, None

    @app.post("/ensemble-stats")
    async def post_ensemble_stats(req: Request):
        """Create (or return) an ensemble-statistics job: up to
        10,000 deterministic causal variants of one disruption over
        the declared axes, aggregated incrementally on a background
        thread. 201 with the job summary; poll GET
        /ensemble-stats/{id}. Identical requests derive the same job
        id and return the existing job (idempotent, like /sim/run).
        """
        jobs, err = _ens_jobs_or_err()
        if err is not None:
            return err
        try:
            body = await req.json()
        except Exception:
            return _err("bad_json", "request body must be JSON")
        if not isinstance(body, dict):
            return _err("invalid_request", "request body must be an object")
        axes = body.get("axes")
        if axes is not None and not isinstance(axes, dict):
            return _err("invalid_request", "axes must be an object")
        spec, err = _resolve_ensemble_spec(body)
        if err is not None:
            return err
        try:
            summary = jobs.create(spec, axes, body.get("count", 1000))
        except EngineError as exc:
            return _err(exc.code, str(exc))
        return JSONResponse(status_code=201, content=summary)

    # --------------------------------------------- ensemble tree mode
    # TREE mode: the same disruption, expanded as a tree
    # (branching x depth over the declared axes, in axis order) by
    # engine.ensemble_tree + server/ensstats/tree_jobs.py. The whole
    # block is declared BEFORE the GET /ensemble-stats list route
    # above all {job_id} routes, so the literal "tree" segment can
    # never be captured as a job id. Tree job ids share the ens_
    # format and the _ens_job_id validation.
    def _enstree_jobs_or_err():
        jobs = app.state.enstree_jobs
        if jobs is None:
            return None, _err("ensemble_stats_unavailable",
                              "ensemble statistics service "
                              "(server/ensstats) is unavailable")
        return jobs, None

    def _enstree_body(req_body):
        """(branching, depth, budget) passed through from the JSON
        body: branching is an int, a list of ints, or null; depth
        and budget are ints or null; an absent key is None, exactly
        like an explicit null — the core defaults apply downstream
        in the service. Values pass through RAW — the service
        validates their types (non-integer JSON values are rejected,
        never coerced)."""
        return (req_body.get("branching"), req_body.get("depth"),
                req_body.get("budget"))

    async def _enstree_parse(req: Request):
        """(jobs, spec, axes, branching, depth, budget, error) for
        the two POST tree routes — the flat routes' body discipline."""
        jobs, err = _enstree_jobs_or_err()
        if err is not None:
            return None, None, None, None, None, None, err
        try:
            body = await req.json()
        except Exception:
            return None, None, None, None, None, None, _err(
                "bad_json", "request body must be JSON")
        if not isinstance(body, dict):
            return None, None, None, None, None, None, _err(
                "invalid_request", "request body must be an object")
        axes = body.get("axes")
        if axes is not None and not isinstance(axes, dict):
            return None, None, None, None, None, None, _err(
                "invalid_request", "axes must be an object")
        spec, err = _resolve_ensemble_spec(body)
        if err is not None:
            return None, None, None, None, None, None, err
        branching, depth, budget = _enstree_body(body)
        return jobs, spec, axes, branching, depth, budget, None

    @app.post("/ensemble-stats/tree/quote")
    async def post_ensemble_tree_quote(req: Request):
        """Quote a full tree expansion against the run cap: axes,
        per-level fanout, the run arithmetic and the time/size
        estimates. NO job is created, nothing is persisted and
        nothing runs — a quote is a computation."""
        jobs, spec, axes, branching, depth, budget, err = \
            await _enstree_parse(req)
        if err is not None:
            return err
        try:
            return jobs.quote(spec, axes, branching, depth, budget)
        except EngineError as exc:
            return _err(exc.code, str(exc))

    @app.post("/ensemble-stats/tree")
    async def post_ensemble_tree(req: Request):
        """Create (or return) an ensemble tree job. The request is
        quoted first: a tree over the run cap is refused 422
        tree_too_large BEFORE anything is persisted. 201 with the
        job summary; poll GET /ensemble-stats/tree/{id}. Identical
        requests derive the same job id and return the existing job
        (idempotent, like the flat jobs)."""
        jobs, spec, axes, branching, depth, budget, err = \
            await _enstree_parse(req)
        if err is not None:
            return err
        try:
            summary = jobs.create(spec, axes, branching, depth, budget)
        except EngineError as exc:
            return _err(exc.code, str(exc))
        return JSONResponse(status_code=201, content=summary)

    @app.get("/ensemble-stats/tree")
    async def list_ensemble_tree():
        jobs, err = _enstree_jobs_or_err()
        if err is not None:
            return err
        return {"jobs": jobs.list_summaries()}

    @app.get("/ensemble-stats/tree/{job_id}")
    async def get_ensemble_tree(job_id: str):
        jobs, err = _enstree_jobs_or_err()
        if err is not None:
            return err
        _, err = _ens_job_id(job_id)
        if err is not None:
            return err
        record = jobs.get(job_id)
        if record is None:
            return _err("ensemble_stats_not_found",
                        f"unknown ensemble statistics job: {job_id!r}")
        return jobs.summary(record)

    def _enstree_complete(job_id: str, fetch):
        jobs, err = _enstree_jobs_or_err()
        if err is not None:
            return err
        _, err = _ens_job_id(job_id)
        if err is not None:
            return err
        try:
            return fetch(jobs)
        except EnsTreeJobNotFound:
            return _err("ensemble_stats_not_found",
                        f"unknown ensemble statistics job: {job_id!r}")
        except EnsTreeJobNotReady as exc:
            progress = exc.record.get("progress") or {}
            return _err("ensemble_stats_not_ready",
                        f"job {job_id} is {exc.record['status']} "
                        f"({progress.get('done')} of "
                        f"{progress.get('quoted')} variants computed)")

    @app.get("/ensemble-stats/tree/{job_id}/tree")
    async def get_ensemble_tree_tree(job_id: str):
        return _enstree_complete(job_id, lambda jobs: jobs.tree(job_id))

    @app.get("/ensemble-stats/tree/{job_id}/nodes/{index}")
    async def get_ensemble_tree_node(job_id: str, index: int):
        """One tree member's aggregates over its part of the tree:
        the stored aggregates when that part was expanded, replay-
        derived (stream hash verified) when it was not."""
        def fetch(jobs):
            try:
                return jobs.node(job_id, index)
            except EnsTreeNodeNotFound:
                return _err("tree_node_not_found",
                            f"job {job_id} has no tree member {index}")
            except EngineError as exc:
                return _err(exc.code, str(exc))
        return _enstree_complete(job_id, fetch)

    @app.get("/ensemble-stats/tree/{job_id}/variants/{index}")
    async def get_ensemble_tree_variant(job_id: str, index: int):
        """One tree member's full causal chain, re-derived by
        replaying its stored combination (the stream hash is verified
        against the stored record before serving). Tree members
        publish at completion, so any member of a job that is not
        complete is a 409 — there is no partial tree view."""
        jobs, err = _enstree_jobs_or_err()
        if err is not None:
            return err
        _, err = _ens_job_id(job_id)
        if err is not None:
            return err
        record = jobs.get(job_id)
        if record is None:
            return _err("ensemble_stats_not_found",
                        f"unknown ensemble statistics job: {job_id!r}")
        try:
            out = jobs.variant(job_id, index)
        except EnsTreeVariantNotFound:
            return _err("variant_not_found",
                        f"job {job_id} has no variant {index}")
        except EnsTreeJobNotReady as exc:
            progress = exc.record.get("progress") or {}
            return _err("ensemble_stats_not_ready",
                        f"variant {index} is not computed yet "
                        f"({progress.get('done')} of "
                        f"{progress.get('quoted')} variants computed)")
        except EngineError as exc:
            return _err(exc.code, str(exc))
        return {
            "id": job_id,
            "variant": out["variant"],
            "combination": out["combination"],
            "events": out["events"],
            "edges": materialise_edges(
                out["events"], record["disruption"]["node_id"]),
            "labelling": {
                "note": "this chain was re-derived by replaying the "
                        "stored variant (seed + axis combination) "
                        "through the deterministic engine; its stream "
                        "hash matches the stored record, so it is the "
                        "variant's exact causal chain",
            },
        }

    # ---------------------------------------- ensemble explorations
    # INTERACTIVE DEEPENING mode: saved explorations whose
    # members are ordinary flat jobs (server/ensstats/explorations.py).
    # The whole block is declared BEFORE the GET /ensemble-stats list
    # route and every /ensemble-stats/{job_id} route below, next to
    # the tree block, so the literal "explorations" segment is never
    # captured as a job id. An exploration record stores pointers +
    # display metadata only — aggregates stay with the flat jobs,
    # served per member at the flat results route.
    def _ens_explorations_or_err():
        svc = app.state.ens_explorations
        if svc is None:
            return None, _err("ensemble_stats_unavailable",
                              "ensemble statistics service "
                              "(server/ensstats) is unavailable")
        return svc, None

    def _ens_exp_id(exp_id: str):
        """(ok, error) — exploration ids are server-derived; a
        malformed id is simply not an exploration, in the 404 shape."""
        if not _ENS_EXP_ID_RE.match(exp_id or ""):
            return None, _err("exploration_not_found",
                              f"unknown exploration: {exp_id!r}")
        return exp_id, None

    @app.post("/ensemble-stats/explorations")
    async def post_ensemble_exploration(req: Request):
        """Create a saved exploration rooted at an existing flat
        job. 201 with the exploration detail payload."""
        svc, err = _ens_explorations_or_err()
        if err is not None:
            return err
        try:
            body = await req.json()
        except Exception:
            return _err("bad_json", "request body must be JSON")
        if not isinstance(body, dict):
            return _err("invalid_request", "request body must be an object")
        root_job_id = body.get("root_job_id")
        if root_job_id is not None and not isinstance(root_job_id, str):
            return _err("invalid_request", "root_job_id must be a string")
        try:
            record = svc.create(body.get("name"), root_job_id)
        except EnsJobNotFound:
            return _err("ensemble_stats_not_found",
                        f"unknown ensemble statistics job: {root_job_id!r}")
        except EngineError as exc:
            return _err(exc.code, str(exc))
        return JSONResponse(status_code=201,
                            content=svc.detail(record["id"]))

    @app.get("/ensemble-stats/explorations")
    async def list_ensemble_explorations():
        svc, err = _ens_explorations_or_err()
        if err is not None:
            return err
        return {"explorations": svc.list_entries()}

    @app.get("/ensemble-stats/explorations/{exp_id}")
    async def get_ensemble_exploration(exp_id: str):
        svc, err = _ens_explorations_or_err()
        if err is not None:
            return err
        _, err = _ens_exp_id(exp_id)
        if err is not None:
            return err
        try:
            return svc.detail(exp_id)
        except EnsExplorationNotFound:
            return _err("exploration_not_found",
                        f"unknown exploration: {exp_id!r}")

    @app.post("/ensemble-stats/explorations/{exp_id}/rename")
    async def post_ensemble_exploration_rename(exp_id: str, req: Request):
        svc, err = _ens_explorations_or_err()
        if err is not None:
            return err
        _, err = _ens_exp_id(exp_id)
        if err is not None:
            return err
        try:
            body = await req.json()
        except Exception:
            return _err("bad_json", "request body must be JSON")
        if not isinstance(body, dict):
            return _err("invalid_request", "request body must be an object")
        try:
            record = svc.rename(exp_id, body.get("name"))
        except EnsExplorationNotFound:
            return _err("exploration_not_found",
                        f"unknown exploration: {exp_id!r}")
        except EngineError as exc:
            return _err(exc.code, str(exc))
        return svc.detail(record["id"])

    @app.post("/ensemble-stats/explorations/{exp_id}/expand")
    async def post_ensemble_exploration_expand(exp_id: str, req: Request):
        """Deepen one member of the exploration: a new flat job that
        holds every axis at the chosen parent variant's combination
        except the next not-yet-varied axis, swept over the supplied
        values. 201 with the expansion, the child job summary and
        the quote (axis, values count, grid size, the child job's
        own count note)."""
        svc, err = _ens_explorations_or_err()
        if err is not None:
            return err
        _, err = _ens_exp_id(exp_id)
        if err is not None:
            return err
        try:
            body = await req.json()
        except Exception:
            return _err("bad_json", "request body must be JSON")
        if not isinstance(body, dict):
            return _err("invalid_request", "request body must be an object")
        parent_job_id = body.get("parent_job_id")
        variant_index = body.get("variant_index")
        try:
            out = svc.expand(exp_id, parent_job_id, variant_index,
                             body.get("values"))
        except EnsExplorationNotFound:
            return _err("exploration_not_found",
                        f"unknown exploration: {exp_id!r}")
        except EnsJobNotFound:
            return _err("ensemble_stats_not_found",
                        f"unknown ensemble statistics job: {parent_job_id!r}")
        except EnsVariantNotFound:
            return _err("variant_not_found",
                        f"job {parent_job_id} has no variant {variant_index}")
        except EnsJobNotReady as exc:
            progress = exc.record.get("progress") or {}
            return _err("ensemble_stats_not_ready",
                        f"job {parent_job_id} is {exc.record['status']} "
                        f"({progress.get('done')} of "
                        f"{progress.get('planned')} variants computed)")
        except EngineError as exc:
            return _err(exc.code, str(exc))
        return JSONResponse(status_code=201, content=out)

    @app.get("/ensemble-stats")
    async def list_ensemble_stats():
        jobs, err = _ens_jobs_or_err()
        if err is not None:
            return err
        return {"jobs": jobs.list_summaries()}

    @app.get("/ensemble-stats/{job_id}")
    async def get_ensemble_stats(job_id: str):
        jobs, err = _ens_jobs_or_err()
        if err is not None:
            return err
        _, err = _ens_job_id(job_id)
        if err is not None:
            return err
        record = jobs.get(job_id)
        if record is None:
            return _err("ensemble_stats_not_found",
                        f"unknown ensemble statistics job: {job_id!r}")
        return jobs.summary(record)

    def _ens_complete(job_id: str, fetch):
        jobs, err = _ens_jobs_or_err()
        if err is not None:
            return err
        _, err = _ens_job_id(job_id)
        if err is not None:
            return err
        try:
            return fetch(jobs)
        except EnsJobNotFound:
            return _err("ensemble_stats_not_found",
                        f"unknown ensemble statistics job: {job_id!r}")
        except EnsJobNotReady as exc:
            done = exc.record["progress"]["done"]
            planned = exc.record["progress"]["planned"]
            return _err("ensemble_stats_not_ready",
                        f"job {job_id} is {exc.record['status']} "
                        f"({done} of {planned} variants computed)")

    @app.get("/ensemble-stats/{job_id}/results")
    async def get_ensemble_stats_results(job_id: str):
        return _ens_complete(job_id, lambda jobs: jobs.results(job_id))

    @app.get("/ensemble-stats/{job_id}/branches")
    async def get_ensemble_stats_branches(job_id: str):
        return _ens_complete(job_id, lambda jobs: jobs.branches(job_id))

    @app.get("/ensemble-stats/{job_id}/branch")
    async def get_ensemble_stats_branch(job_id: str, req: Request):
        branch = req.query_params.get("branch")
        if not branch:
            return _err("invalid_request",
                        "branch query parameter is required (a branch "
                        "key exactly as served by the branches view)")
        def fetch(jobs):
            try:
                return jobs.branch_detail(job_id, branch)
            except EnsBranchNotFound:
                return _err("branch_not_found",
                            f"unknown branch for this job: {branch!r}")
        return _ens_complete(job_id, fetch)

    @app.get("/ensemble-stats/{job_id}/variants/{index}")
    async def get_ensemble_stats_variant(job_id: str, index: int):
        """One variant's full causal chain, re-derived by replaying
        its stored seed + axis combination (the stream hash is
        verified against the stored record before serving). Works
        for any variant already computed, including while the job is
        still running; a variant not yet computed is a 409."""
        jobs, err = _ens_jobs_or_err()
        if err is not None:
            return err
        _, err = _ens_job_id(job_id)
        if err is not None:
            return err
        record = jobs.get(job_id)
        if record is None:
            return _err("ensemble_stats_not_found",
                        f"unknown ensemble statistics job: {job_id!r}")
        try:
            out = jobs.variant(job_id, index)
        except EnsVariantNotFound:
            return _err("variant_not_found",
                        f"job {job_id} has no variant {index}")
        except EnsJobNotReady as exc:
            done = exc.record["progress"]["done"]
            planned = exc.record["progress"]["planned"]
            return _err("ensemble_stats_not_ready",
                        f"variant {index} is not computed yet "
                        f"({done} of {planned} variants computed)")
        except EngineError as exc:
            return _err(exc.code, str(exc))
        return {
            "id": job_id,
            "variant": out["variant"],
            "combination": out["combination"],
            "events": out["events"],
            "edges": materialise_edges(
                out["events"], record["disruption"]["node_id"]),
            "labelling": {
                "note": "this chain was re-derived by replaying the "
                        "stored variant (seed + axis combination) "
                        "through the deterministic engine; its stream "
                        "hash matches the stored record, so it is the "
                        "variant's exact causal chain",
            },
        }

    @app.post("/fork", status_code=201)
    async def post_fork(req: Request):
        try:
            body = await req.json()
        except Exception:
            return _err("bad_json", "request body must be JSON")
        if not isinstance(body, dict):
            return _err("invalid_request", "request body must be an object")
        parent_id = body.get("run_id")
        record = store.load(parent_id) if isinstance(parent_id, str) else None
        if record is None:
            return _err("unknown_run", f"unknown run_id: {parent_id!r}")
        at_seq = body.get("at_seq")
        if not isinstance(at_seq, int) or isinstance(at_seq, bool):
            return _err("invalid_request", "at_seq must be an integer")
        overrides = body.get("overrides")
        if not isinstance(overrides, dict) or not overrides:
            return _err("empty_overrides", "overrides must be a non-empty object")
        parent_journal = record["journal"]
        journal_core = {"kind": "fork", "graph_hash": graph.hash,
                        "parent_run_id": parent_id, "at_seq": at_seq,
                        "overrides": overrides, "parent_journal": parent_journal}
        # A fork honours the STORED engine rev of its parent: a rev-2 parent
        # forks under ENGINE REV 2 (journaled, so the child id differs from
        # any rev-1 id); a parent with no engine_rev key is rev 1, unchanged.
        parent_rev = parent_journal.get("engine_rev", 1)
        rev_extra = {}
        if parent_rev == 2:
            if engine2 is None:
                return _err("invalid_request", "engine_rev 2 is unavailable")
            journal_core = {**journal_core, **_rev2_stamp()}
            if parent_journal.get("profile"):
                # a profile run forks under the same profile, geometry and services
                journal_core.update({k: parent_journal[k] for k in _PROFILE_JOURNAL_KEYS
                                     if k in parent_journal})
            rev_extra = {"engine_rev": 2, "engine_label": journal_core["engine_label"]}
            if parent_journal.get("routes"):
                # a routed parent forks under the same routes table
                journal_core["routes"] = parent_journal["routes"]
            if parent_journal.get("extra"):
                # an extra parent forks under the same sidecar view
                journal_core["extra"] = parent_journal["extra"]
        child_id = derive_run_id(journal_core)
        if store.exists(child_id):
            rec = store.load(child_id)
            return JSONResponse(status_code=201, content={
                "run_id": child_id, "parent_run_id": parent_id, "at_seq": at_seq,
                "status": rec["status"], "event_count": rec["event_count"],
                "stream_hash": rec["stream_hash"], **rev_extra})
        try:
            if parent_rev == 2:
                parent_state = replay_journal2(engine2, parent_journal, parent_id)
                child_state = fork_events2(_engine_for2(engine2, parent_journal),
                                           parent_state,
                                           parent_journal["disruption"],
                                           parent_journal["params"], at_seq,
                                           overrides, child_id)
            else:
                parent_state = state_from_record(record)
                child_state = fork_events(engine, parent_state,
                                          parent_journal["disruption"],
                                          parent_journal["params"], at_seq,
                                          overrides, child_id)
        except EngineError as exc:
            return _err(exc.code, str(exc))
        journal = {**journal_core, "graph_version": graph.version,
                   "disruption": parent_journal["disruption"],
                   "params": parent_journal["params"]}
        rec = store.save(child_id, journal, child_state)
        _alerts_evaluate()  # alerts seam: run_outcome rules see this fork
        return JSONResponse(status_code=201, content={
            "run_id": child_id, "parent_run_id": parent_id, "at_seq": at_seq,
            "status": rec["status"], "event_count": rec["event_count"],
            "stream_hash": rec["stream_hash"], **rev_extra})

    @app.get("/scorecard")
    async def get_scorecard():
        """Scorecard: serves the stored scorecard.json
        read-only. The file is re-read on every request so a regenerated
        artifact is served without a restart; this endpoint never
        generates or mutates it. Absent artifact -> 404 in the standard
        error shape."""
        if not scorecard_path.exists():
            return _err("scorecard_not_found",
                        "no scorecard artifact has been generated yet "
                        f"(expected artifact: {scorecard_path.name})")
        try:
            return json.loads(scorecard_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return _err("scorecard_unreadable",
                        f"scorecard artifact exists but cannot be read: {_safe_exc(exc)}")

    @app.get("/scorecard/rev2-calibrated")
    async def get_scorecard_rev2_calibrated():
        """BACKTEST ENGINE REV 2 CALIBRATED: the stored card,
        read-only, separate from /scorecard (whose response is unchanged)."""
        p = data_root / "replay" / "rev2_calibrated" / "scorecard.json"
        if not p.exists():
            return _err("scorecard_not_found", "no rev2-calibrated scorecard has been generated")
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:
            return _err("scorecard_unreadable", f"scorecard artifact cannot be read: {_safe_exc(exc)}")

    @app.get("/scorecard/rev2d")
    async def get_scorecard_rev2d():
        """BACKTEST ENGINE REV 2 \u00b7 PROFILE d: the stored card, read-only,
        separate from /scorecard (whose response is unchanged)."""
        p = data_root / "replay" / "rev2d" / "scorecard.json"
        if not p.exists():
            return _err("scorecard_not_found", "no rev2d scorecard has been generated")
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:
            return _err("scorecard_unreadable", f"scorecard artifact cannot be read: {_safe_exc(exc)}")

    @app.get("/graph")
    async def get_graph(request: Request):
        """Graph: serves data/graph/graph.json exactly as
        stored (nodes/edges + metadata), read-only. CACHING: the
        shipped file is several MB, so re-reading + re-parsing +
        re-serialising it on every request would be the API's
        dominant cost. The parsed document is
        cached in memory keyed by the file's (mtime_ns, size):
        while the mtime is unchanged the file is not re-read at all,
        and a regenerated graph (new mtime) is picked up without a
        restart. Responses carry an ETag — the quoted SHA-256 of the
        file bytes (chosen over mtime+size alone, so a same-size
        same-mtime rewrite cannot validate stale) — plus
        Cache-Control: no-cache, and an If-None-Match that matches
        gets a 304 with an empty body. Clients accepting gzip get the
        pre-computed gzip body (computed once per parse; the plain
        body is byte-identical to the JSONResponse
        serialisation). GETs never
        mutate the file. Absent artifact -> 404 in the standard
        error shape."""
        if not graph_path.exists():
            return _err("graph_not_found",
                        "no graph artifact has been generated yet "
                        f"(expected artifact: {graph_path.name})")
        try:
            st = graph_path.stat()
        except Exception as exc:
            return _err("graph_unreadable",
                        f"graph artifact exists but cannot be read: {_safe_exc(exc)}")
        cache_key = (st.st_mtime_ns, st.st_size)
        cached = app.state.graph_cache
        if cached is None or cached["key"] != cache_key:
            try:
                raw = graph_path.read_bytes()
                doc = json.loads(raw.decode("utf-8"))
                # Byte-identical to Starlette JSONResponse.render():
                # same dumps arguments it uses, applied once here.
                body = json.dumps(doc, ensure_ascii=False, allow_nan=False,
                                  separators=(",", ":")).encode("utf-8")
                gzip_body = gzip.compress(body, compresslevel=6, mtime=0)
            except Exception as exc:
                return _err("graph_unreadable",
                            f"graph artifact exists but cannot be read: {_safe_exc(exc)}")
            cached = {"key": cache_key,
                      "etag": '"' + hashlib.sha256(raw).hexdigest() + '"',
                      "body": body, "gzip_body": gzip_body}
            app.state.graph_cache = cached
        headers = {"ETag": cached["etag"], "Cache-Control": "no-cache",
                   "Vary": "Accept-Encoding"}
        if _etag_matches(request.headers.get("if-none-match"),
                         cached["etag"]):
            return Response(status_code=304, headers=headers)
        if _accepts_gzip(request.headers.get("accept-encoding")):
            return Response(content=cached["gzip_body"],
                            media_type="application/json",
                            headers={**headers,
                                     "Content-Encoding": "gzip"})
        return Response(content=cached["body"],
                        media_type="application/json", headers=headers)

    # ------------------------------------------------ graph services layer
    # Read-only views of the opt-in sidecar data/graph/services.json
    # The graph file itself is never touched.
    def _load_services_or_err():
        try:
            from graph_services import ServicesError, load_services
        except Exception:  # imported as server.api.* from the repo root
            from server.graph_services import ServicesError, load_services  # type: ignore
        try:
            return load_services(graph.raw, graph.hash), None
        except OSError:
            return None, _err("services_not_found",
                              "no services sidecar (data/graph/services.json) is installed")
        except ServicesError as exc:
            return None, _err("services_unreadable", scrub_paths(exc))

    def _services_query(req: Request):
        """(eras, include_unverified, include_unstated, error) from the query."""
        from graph_services.loader import ERAS
        raw = req.query_params.get("eras")
        eras = None
        if raw:
            eras = sorted(set(x for x in raw.split(",") if x))
            if not eras or any(x not in ERAS for x in eras):
                return None, False, False, _err(
                    "invalid_request", f"eras must be a comma list drawn from {list(ERAS)}")
        flags = []
        for key in ("include_unverified", "include_unstated"):
            v = req.query_params.get(key, "0")
            if v not in ("0", "1"):
                return None, False, False, _err("invalid_request", f"{key} must be 0 or 1")
            flags.append(v == "1")
        return eras, flags[0], flags[1], None

    @app.get("/graph/extra")
    def get_graph_extra():
        """The graph completeness sidecar (data/graph/extra.json, opt-in via
        `extra` on /sim/run and rev-2 /run): counts by tier, lane families,
        disabled lanes, position notes, validation report. Read-only."""
        try:
            from graph_extra import ExtraError, load_extra
        except Exception:  # imported as server.api.* from the repo root
            from server.graph_extra import ExtraError, load_extra  # type: ignore
        try:
            return load_extra(graph.raw, graph.hash).summary()
        except OSError:
            return _err("extra_not_found",
                        "no extra sidecar (data/graph/extra.json) is installed")
        except ExtraError as exc:
            return _err("extra_unreadable", scrub_paths(exc))

    @app.get("/graph/inventory-days")
    def get_graph_inventory_days():
        """Reference sidecar data/graph/inventory_days.json: EIA days of
        supply and Census inventory ratios, each scoped to the United States
        class it describes (SOURCED). Read-only summary; not an engine input."""
        try:
            from realism_data import SidecarError, load_inventory_days
        except Exception:  # imported as server.api.* from the repo root
            from server.realism_data import SidecarError, load_inventory_days  # type: ignore
        try:
            return load_inventory_days(graph.raw, graph.hash).summary()
        except OSError:
            return _err("inventory_days_not_found",
                        "no inventory-days sidecar (data/graph/inventory_days.json) is installed")
        except SidecarError as exc:
            return _err("inventory_days_unreadable", scrub_paths(exc))

    @app.get("/graph/services")
    def get_graph_services():
        """The services sidecar: counts, hashes, policy, validation report,
        per-era counts under the default selection."""
        svc, bad = _load_services_or_err()
        if bad is not None:
            return bad
        out = svc.summary()
        sel_counts = {}
        for era in ("pre_red_sea_crisis", "red_sea_diversion", "partial_suez_return_2026"):
            sel, _ = svc.select(eras=[era])
            sel_counts[era] = len(sel)
        out["default_selection_by_era"] = sel_counts
        vp = svc.variant_pairs(graph.raw)
        out["routing_variant_pairs"] = {
            "basis": ("same-loop pairs of one operator family (Jaccard >= 0.75 on mapped "
                      "places, different publication months): an analyst hint, MODELLED"),
            "pairs": len(vp), "usable_under_default_policy": sum(1 for p in vp if p["usable"]),
            "usable_with_different_routing": [
                p for p in vp if p["usable"] and p["legs_with_different_routing"]]}
        out["entries"] = [{"service_id": e["service_id"], "operator": e["operator"],
                           "code": e["code"], "era": e["era"], "trade": e["trade"],
                           "calls": len(e["calls"]), "via_basis": e["via_basis"],
                           "flags": e["flags"]} for e in svc.entries]
        return out

    @app.get("/graph/services/routes")
    def get_graph_services_routes(req: Request):
        """The rev-2 ROUTES TABLE that `routes: 'services'` would use for a
        selection (?eras=a,b&include_unverified=0|1&include_unstated=0|1):
        bound graph legs, chains, added days, Cape lanes, skipped legs."""
        eras, inc_u, inc_s, bad = _services_query(req)
        if bad is not None:
            return bad
        svc, bad = _load_services_or_err()
        if bad is not None:
            return bad
        try:
            from graph_services.runtime import route_services
            from engine.engine import core_subgraph as _core
            from engine2.routes import build_routes_table
            from engine2.plugins import plug
        except Exception:  # imported as server.api.* from the repo root
            from server.graph_services.runtime import route_services  # type: ignore
            from server.engine.engine import core_subgraph as _core  # type: ignore
            from server.engine2.routes import build_routes_table  # type: ignore
            from server.engine2.plugins import plug  # type: ignore
        from graph_services.loader import ERAS
        selection = {"file_sha256": svc.file_sha256, "eras": eras or list(ERAS),
                     "include_unverified": inc_u, "include_unstated": inc_s}
        r = route_services(graph.raw, graph.hash, selection)
        table = build_routes_table(_core(graph), r["od"], r["meta"], plug("teu_per_call"))
        return {"selection": selection, **table.to_doc()}

    @app.get("/graph/services/{service_id}")
    def get_graph_service(service_id: str):
        svc, bad = _load_services_or_err()
        if bad is not None:
            return bad
        e = svc.by_id.get(service_id)
        if e is None:
            return _err("unknown_service", f"unknown service_id: {service_id!r}")
        return e

    def _events_feed_states() -> dict:
        """Adapter name -> health state now (empty when the board is
        unavailable: nothing can then earn LIVE)."""
        registry_mod = getattr(app.state, "adapter_registry", None)
        if registry_mod is None:
            return {}
        try:
            return {str(e.get("adapter", "")).lower(): e.get("state")
                    for e in registry_mod.health_report()}
        except Exception:
            return {}

    @app.get("/events")
    async def get_events(req: Request = None):
        """Disruption feed: serves the store's events file
        as {events: [...]} in file order (newest last, as stored),
        read-only, re-read on every request. The .jsonl
        store is read through EventStore.read_all(), so the
        lifecycle fold (later line for an occurrence replaces its
        earlier state; expiry-derived `resolved`) reaches this
        endpoint — the raw file is never served line-for-line. An
        absent or empty store
        is data, not an error: {events: []} with 200. A line that is
        not valid JSON, not an object, or fails validation
        (validate_c3_event) is SKIPPED — never served — and counted in a
        top-level "invalid_lines" field, present only when > 0; a bad
        line never produces a 500. Node existence is not re-checked
        here: the store enforced referential integrity at write time,
        and this read path must not drop otherwise-valid events
        over graph-version skew."""
        if not events_path.exists():
            return {"events": []}
        try:
            text = events_path.read_text(encoding="utf-8")
        except Exception as exc:
            return _err("events_unreadable",
                        f"events store exists but cannot be read: {_safe_exc(exc)}")
        if events_path.suffix == ".jsonl":
            records: list = []
            invalid = 0
            for line in text.splitlines():
                if not line.strip():
                    continue  # blank lines are file padding, not data
                try:
                    records.append(json.loads(line))
                except Exception:
                    invalid += 1
        else:  # a .json events source (list, or {events: [...]})
            try:
                data = json.loads(text) if text.strip() else []
            except Exception as exc:
                return _err("events_unreadable",
                            f"events store exists but cannot be read: {_safe_exc(exc)}")
            # A scalar/null root or a non-list
            # "events" value would raise AttributeError/TypeError out
            # of this handler (bare 500). A mis-shaped store is
            # unreadable, in the standard shape — never a crash.
            if isinstance(data, list):
                records = data
            elif isinstance(data, dict) \
                    and isinstance(data.get("events", []), list):
                records = data.get("events", [])
            else:
                return _err("events_unreadable",
                            "events store exists but cannot be read: "
                            "root must be a list or an object with an "
                            "'events' list")
            invalid = 0
        events = []
        for rec in records:
            try:
                if not isinstance(rec, dict):
                    raise ValueError("event is not an object")
                if validate_c3_event is not None:
                    validate_c3_event(rec)
            except Exception:
                invalid += 1
                continue
            events.append(rec)
        # Fold lifecycle lines into current state. The
        # .jsonl store goes through EventStore.read_all() itself;
        # EventStore's index load is strict about torn lines, so a
        # store the endpoint's tolerant counting above accepted can
        # still refuse construction — then the store module's own
        # fold is applied to the validated records instead (same
        # semantics, same module). A .json source folds the same way.
        if _store_fold_events is not None:
            folded = None
            if events_path.suffix == ".jsonl" and EventStore is not None:
                try:
                    folded = EventStore(events_path).read_all()
                except Exception:
                    folded = None
            if folded is None:
                try:
                    folded = _store_fold_events(events)
                except Exception:
                    folded = events
            events = []
            for rec in folded:
                try:
                    if not isinstance(rec, dict):
                        raise ValueError("event is not an object")
                    if validate_c3_event is not None:
                        validate_c3_event(rec)
                except Exception:
                    continue  # already counted at the raw-line pass
                events.append(rec)
        # Additive, derived `citation` (human-readable source page);
        # evidence_url is untouched and required fields never change.
        events = [_with_citation(rec) for rec in events]
        # Additive, derived `display_mode` (LIVE only while the
        # producing feed is LIVE now AND the record is fresh); the stored
        # `mode` and the stored bytes are never changed.
        _fs = _events_feed_states()
        events = [with_display_mode(rec, _fs) for rec in events]
        if req is not None:
            limit, offset, perr = _pagination(req)
            if perr is not None:
                return perr
            events = _page(events, limit, offset)
        body: dict = {"events": events}
        if invalid > 0:
            body["invalid_lines"] = invalid
        return body

    def _read_exposures():
        """Exposures doc, re-read on every call (file-backed, like
        /graph). Returns (doc, None) or (None, standard error response)."""
        if not exposures_path.exists():
            return None, _err("exposures_not_found",
                              "no exposures artifact has been generated yet "
                              f"(expected artifact: {exposures_path.name})")
        try:
            doc = json.loads(exposures_path.read_text(encoding="utf-8"))
            # content scope: out-of-scope filing excerpts are withheld for
            # EVERY consumer of this API (the stored file is unchanged);
            # the filing citation and excerpt_missing_reason are kept.
            return (_guard_exposures(doc) if isinstance(doc, dict) else doc), None
        except Exception as exc:
            return None, _err("exposures_unreadable",
                              f"exposures artifact exists but cannot be read: {_safe_exc(exc)}")

    @app.get("/money/exposures")
    async def get_money_exposures():
        """Exposure mapping: serves
        server/money/exposures.json read-only, with out-of-scope filing
        excerpts withheld (excerpt null + excerpt_missing_reason; the
        stored file is unchanged),
        re-read on every request like /graph. Absent artifact -> 404
        in the standard error shape."""
        doc, err = _read_exposures()
        if err is not None:
            return err
        return doc

    @app.get("/money/node/{node_id}")
    async def get_money_node(node_id: str):
        """Money panel for one node: the per-node entry of
        money.panel.build_money_panel() over the stored exposures doc,
        read-only. Quotes are looked up per exposed ticker through the
        app's quote fetcher (unpriced by default — see
        _default_money_quote_fetcher for why, and for the
        SUPERMAP_MONEY_LIVE_QUOTES=1 live-Stooq opt-in); a ticker the
        fetcher cannot price is passed as None, so panel.py lists the
        company unpriced (null price fields) — that behaviour is the
        contract, and no price is ever fabricated here. A node_id that
        is not in the graph -> 404 in the standard error shape (note:
        distinct from the engine's 422 "unknown_node", which belongs to
        run validation)."""
        if node_id not in graph.nodes:
            return _err("node_not_found", f"unknown node_id: {node_id!r}")
        if build_money_panel is None:
            return _err("money_unavailable",
                        "money panel (server/money/panel.py) is unavailable")
        doc, err = _read_exposures()
        if err is not None:
            return err
        # A parseable-but-mis-shaped exposures doc
        # (non-object root, "mappings" not a list, mapping entries
        # missing the fields panel.py dereferences) would raise
        # AttributeError/KeyError inside build_money_panel — a bare
        # 500. A wrong-shaped root is exposures_unreadable; individual
        # malformed mapping entries are skipped, mirroring the
        # /events and /satwatch/scans record conventions (never served,
        # never a crash). Well-formed entries are served as stored.
        if not isinstance(doc, dict) \
                or not isinstance(doc.get("mappings", []), list):
            return _err("exposures_unreadable",
                        "exposures artifact exists but is malformed: "
                        "expected an object with a 'mappings' list")

        def _well_formed(m) -> bool:
            return isinstance(m, dict) and all(
                isinstance(m.get(k), str) and m.get(k)
                for k in ("node_id", "ticker", "company", "exposure_kind")
            ) and isinstance(m.get("filing"), dict) \
                and isinstance(m["filing"].get("url"), str)

        doc = {**doc, "mappings": [m for m in doc.get("mappings", [])
                                   if _well_formed(m)]}
        tickers = sorted({m["ticker"] for m in doc["mappings"]
                          if m.get("node_id") == node_id})
        quotes: dict[str, dict | None] = {}
        for ticker in tickers:
            try:
                quotes[ticker] = money_quote_fetcher(ticker)
            except Exception:
                quotes[ticker] = None
        panel = build_money_panel([node_id], doc, quotes)
        return panel[node_id]

    # ------------------------------------------------------------------
    # IMPACT layer — additive namespace. Every figure in these
    # payloads is a {value, unit, class, source, as_of, derivation}
    # object; absent data is served as an ABSENT figure, never a zero
    # and never a substitute. No payload carries a cross-class
    # aggregate or a forward price figure (server/impact enforces this
    # with a payload schema guard).
    # ------------------------------------------------------------------
    def _impact_service():
        if ImpactStore is None or ImpactService is None:
            return None
        return ImpactService(ImpactStore(
            snapshots_dir=impact_snapshots_dir, impact_dir=impact_data_dir,
            graph_path=graph_path))

    @app.get("/impact/goods")
    async def get_impact_goods():
        """The goods catalogue: {goods: [{code, label}]} built from
        the IMPACT layer's CMD_LABELS (server/impact/derive.py),
        IMPORTED — never copied — so this payload can never drift
        from the labels the derivations themselves use."""
        if CMD_LABELS is None:
            return _err("impact_unavailable", "impact layer is unavailable")
        return {"goods": [{"code": code, "label": label}
                           for code, label in CMD_LABELS.items()]}

    @app.get("/impact/run/{run_id}")
    async def get_impact_run(run_id: str):
        """Impact of one stored run: volume at risk per unit class
        (each affected place counted once, at its worst), slowdown,
        goods profile per unit class, economy exposure (Tier-1 port
        share, Tier-2 trade share, concentration watchlist) and price
        context. Unknown run -> 404 in the standard error shape."""
        svc = _impact_service()
        if svc is None:
            return _err("impact_unavailable", "impact layer is unavailable")
        record = store.load(run_id)
        if record is None:
            return _err("run_not_found", f"unknown run_id: {run_id!r}")
        try:
            return svc.run_payload(record)
        except ValueError as exc:
            return _err("impact_unavailable", f"impact derivation failed: {_safe_exc(exc)}")

    @app.get("/impact/node/{node_id}")
    async def get_impact_node(node_id: str):
        """One place's impact block: published capacity, goods
        composition (or the labelled fallback / an ABSENT line),
        and its country's Tier-1 exposure row for ports.
        A place id not in the build -> 404 in the standard shape."""
        svc = _impact_service()
        if svc is None:
            return _err("impact_unavailable", "impact layer is unavailable")
        try:
            payload = svc.node_payload(node_id)
        except ValueError as exc:
            return _err("impact_unavailable", f"impact derivation failed: {_safe_exc(exc)}")
        if payload is None:
            return _err("node_not_found", f"unknown node_id: {node_id!r}")
        return payload

    @app.get("/impact/exposure")
    async def get_impact_exposure(country: str | None = None):
        """One country's exposure block: national port tonnage
        (PortWatch AIS estimates, shared Tier-1 window), each build
        port's share of it, and a Tier-2 ABSENT line (Tier-2 is
        computed per run). `country` must be an ISO3 code."""
        svc = _impact_service()
        if svc is None:
            return _err("impact_unavailable", "impact layer is unavailable")
        if not country or not re.fullmatch(r"[A-Za-z]{3}", country):
            return _err("invalid_request",
                        "query parameter 'country' must be an ISO3 code")
        try:
            return svc.exposure_payload(country.upper())
        except ValueError as exc:
            return _err("impact_unavailable", f"impact derivation failed: {_safe_exc(exc)}")

    @app.get("/impact/analogues")
    async def get_impact_analogues(channel: str | None = None):
        """The printed historical-analogue set, optionally
        filtered to one channel. HISTORICAL evidence only — the set is
        static, versioned data, never interpolated onto a live run."""
        svc = _impact_service()
        if svc is None:
            return _err("impact_unavailable", "impact layer is unavailable")
        return svc.analogues_payload(channel)

    @app.get("/impact/rates")
    async def get_impact_rates():
        """Price context: dated observed-index citations
        with licence states and STALE stamps — CITED, NOT A FEED. No
        value here is computed by this product and no forward claim
        is made anywhere in the payload."""
        svc = _impact_service()
        if svc is None:
            return _err("impact_unavailable", "impact layer is unavailable")
        return svc.rates_payload()

    # ------------------------------------------------------------------
    # LIVE TWIN feeds — flag-gated section data. Feed
    # states come from the adapter registry's health_report() (the
    # same source as /api/v1/health); record counts and the air /
    # vessels payloads come from the adapters' stored snapshot packs
    # under the app's data root. Nothing is fabricated: a field an
    # entry or pack does not carry is served as null / omitted, the
    # OpenSky pack's per-hub aggregate records go out as-is (no
    # per-aircraft positions exist), and no new weather data is
    # invented — the noaa/gdacs rows carry the hazard states.
    # ------------------------------------------------------------------
    def _snapshot_pack(name: str) -> dict | None:
        path = data_root / "snapshots" / f"{name}.json"
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        return doc if isinstance(doc, dict) else None

    @app.get("/livetwin/status")
    async def get_livetwin_status():
        # The flag as a plain 200 answer. The client reads it before
        # asking for the feed board, so a flag-off boot makes no request
        # that answers 404 (the browser logs every 404 as a console
        # error, caught or not). /livetwin/feeds keeps its contract.
        return {"enabled": bool(getattr(app.state, "live_twin", False))}

    @app.get("/livetwin/feeds")
    async def get_livetwin_feeds():
        if not getattr(app.state, "live_twin", False):
            return _err("live_twin_off",
                        "live twin is off (set SUPERMAP_LIVE_TWIN=1 "
                        "to enable it)")
        health = _alerts_health()
        by_name = {entry.get("adapter"): entry for entry in health
                   if isinstance(entry, dict)}
        packs = {name: _snapshot_pack(name) for name, _ in _LIVE_TWIN_FEEDS}
        feeds = []
        for name, label in _LIVE_TWIN_FEEDS:
            entry = by_name.get(name) or {}
            pack = packs.get(name) or {}
            records = pack.get("records")
            provenance = pack.get("provenance") \
                if isinstance(pack.get("provenance"), dict) else {}
            feeds.append({
                "id": name,
                "label": label,
                "state": entry.get("state"),
                "retrieved_at": entry.get("last_success_at")
                or provenance.get("retrieved_at"),
                "fresh": entry.get("fresh"),
                "records": len(records)
                if isinstance(records, list) else None,
                "note": entry.get("note") or pack.get("note"),
            })
        opensky_records = (packs.get("opensky") or {}).get("records")
        ais_pack = packs.get("aisstream") or {}
        ais_records = ais_pack.get("records")
        return {
            "enabled": True,
            "generated_at": datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"),
            "feeds": feeds,
            "air": opensky_records
            if isinstance(opensky_records, list) else [],
            "vessels": {
                "state": (by_name.get("aisstream") or {}).get("state"),
                "records": ais_records
                if isinstance(ais_records, list) else [],
                "note": ais_pack.get("note")
                or (by_name.get("aisstream") or {}).get("note"),
            },
        }

    # ------------------------------------------------------------------
    # CONTEXT feeds: read-only GET /feeds,
    # /feeds/eia, /feeds/comtrade. Served from the adapters' stored
    # snapshot packs + the registry's health board — the same sources
    # as /livetwin/feeds. Market quarantine: these routes only READ
    # adapter output; nothing here feeds a run, a journal or the
    # engine. Honesty: when the adapter is not LIVE the records go out
    # relabelled mode=SNAPSHOT (original retrieved_at kept); a feed with
    # no records serves state DOWN + an empty list + the reason (200,
    # never a fabricated value); figures are SOURCED context and are
    # never summed into a headline.
    # ------------------------------------------------------------------
    def _context_feed_payload(name: str, label: str) -> dict:
        health = {e.get("adapter"): e for e in _alerts_health()
                  if isinstance(e, dict)}.get(name) or {}
        pack = _snapshot_pack(name) or {}
        raw = pack.get("records") if isinstance(pack.get("records"), list) \
            else []
        pack_prov = pack.get("provenance") \
            if isinstance(pack.get("provenance"), dict) else {}
        state = health.get("state") or "DOWN"
        live = state == "LIVE"
        records = []
        for rec in raw:
            if not isinstance(rec, dict):
                continue
            rec = dict(rec)
            prov = dict(rec.get("provenance") or pack_prov)
            if not prov.get("source") or not prov.get("retrieved_at"):
                continue  # a number that cannot produce its chip is removed
            if not live:
                prov["mode"] = "SNAPSHOT"
            rec["provenance"] = prov
            records.append(rec)
        by_type: dict = {}
        for rec in records:
            kind = str(rec.get("record_type"))
            by_type[kind] = by_type.get(kind, 0) + 1
        if not records:
            state = "DOWN"
        note = health.get("note") or pack.get("note")
        if not records and not note:
            note = "no records have been ingested yet"
        envelope = None
        if records:
            envelope = {
                "source": pack_prov.get("source")
                or records[0]["provenance"].get("source"),
                "retrieved_at": pack_prov.get("retrieved_at")
                or records[0]["provenance"].get("retrieved_at"),
                "mode": "LIVE" if live else "SNAPSHOT",
            }
        return {
            "feed": name,
            "label": label,
            "state": state,
            "mode": envelope["mode"] if envelope else None,
            "last_success_at": health.get("last_success_at"),
            "fresh": health.get("fresh"),
            "note": note,
            "provenance": envelope,
            "figure_label": "SOURCED",
            "context_only": True,
            "quarantine": "market-context feed: read-only context; never "
                          "an engine input, never in a run journal; "
                          "figures are individual sourced values and are "
                          "not summed into any headline",
            "count": len(records),
            "record_types": by_type,
            "records": records,
        }

    _CONTEXT_FEEDS = (
        ("eia", "US EIA energy prices and crude imports"),
        ("comtrade", "UN Comtrade derived shares"),
    )

    @app.get("/feeds")
    async def get_context_feeds_index():
        feeds = []
        for name, label in _CONTEXT_FEEDS:
            body = _context_feed_payload(name, label)
            feeds.append({k: body[k] for k in
                          ("feed", "label", "state", "mode",
                           "last_success_at", "note", "count",
                           "provenance")})
        return {"feeds": feeds}

    @app.get("/feeds/eia")
    async def get_feed_eia():
        return _context_feed_payload("eia", dict(_CONTEXT_FEEDS)["eia"])

    @app.get("/feeds/comtrade")
    async def get_feed_comtrade():
        return _context_feed_payload(
            "comtrade", dict(_CONTEXT_FEEDS)["comtrade"])

    @app.get("/satwatch/scans")
    async def get_satwatch_scans():
        """Satwatch scans: serves every scan record in the
        satwatch snapshot directory as {scans: [...]} in filename
        order, read-only, re-read on every request. An absent
        directory is data, not an error: {scans: []} with 200. A record
        file that is not parseable JSON, or parses to a non-object, is
        SKIPPED — never served — and counted in a top-level
        "invalid_lines" field (present only when > 0), mirroring the
        /events convention; a bad file never produces a 500. Non-JSON
        files in the directory (the crop PNGs) are not records and are
        ignored, not counted."""
        if not satwatch_dir.is_dir():
            return {"scans": []}
        scans: list = []
        invalid = 0
        for path in sorted(satwatch_dir.glob("*.json"), key=lambda p: p.name):
            try:
                rec = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(rec, dict):
                    raise ValueError("scan record is not an object")
            except Exception:
                invalid += 1
                continue
            # Detections are always served as satwatch-overlay/1 items
            # (pre-contract point records are re-labelled, never re-detected).
            _norm = _satwatch_attr("overlay", "normalise_scan")
            if _norm is not None:
                try:
                    rec = _norm(rec)
                except Exception:
                    pass
            scans.append(rec)
        body: dict = {"scans": scans}
        if invalid > 0:
            body["invalid_lines"] = invalid
        return body

    # The :path converter is deliberate (same reasoning as /chain): a
    # URL-decoded filename containing "/" (e.g. %2f traversal) must
    # still reach this handler so it can answer with the standard 404
    # shape, instead of missing the route and falling through to
    # FastAPI's default 404 body. Validation then makes traversal
    # impossible: only a bare basename ending in .png is ever joined
    # onto satwatch_dir.
    @app.get("/satwatch/crops/{filename:path}")
    async def get_satwatch_crop(filename: str):
        """Satwatch crop PNG: serves one crop image from
        the satwatch snapshot directory, read-only. Basename only and
        .png only: traversal, subpaths, other extensions and missing
        files are all a 404 in the standard error shape."""
        if (not filename or filename != Path(filename).name
                or "/" in filename or "\\" in filename
                or Path(filename).suffix != ".png"):
            return _err("crop_not_found", f"unknown crop: {filename!r}")
        path = satwatch_dir / filename
        if not path.is_file():
            return _err("crop_not_found", f"unknown crop: {filename!r}")
        return FileResponse(path, media_type="image/png")

    @app.get("/lenses")
    async def get_lenses():
        """Commodity lenses: serves
        server/engine/lenses.json exactly as stored, read-only,
        re-read on every request like /graph. Absent artifact -> 404
        in the standard error shape."""
        if not lenses_path.exists():
            return _err("lenses_not_found",
                        "no lenses artifact has been generated yet "
                        f"(expected artifact: {lenses_path.name})")
        try:
            return json.loads(lenses_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return _err("lenses_unreadable",
                        f"lenses artifact exists but cannot be read: {_safe_exc(exc)}")

    @app.get("/portwatch")
    async def get_portwatch():
        """PortWatch activity snapshot: serves the stored
        snapshot document (data/snapshots/portwatch.json) verbatim —
        {adapter, provenance, records[]} in the shape the web map's
        PortWatchSnapshot type expects — read-only, re-read on every
        request through the same file-backed snapshot pattern as
        /lenses and /scorecard. The document is served exactly as the
        adapter stored it, with its provenance (source / retrieved_at
        / mode) intact: no timestamp is refreshed or fabricated here,
        and the records go out unfiltered (the map's loader keeps the
        port_activity rows for its dot layer). Absent snapshot -> 404
        in the standard error shape — never an empty-records 200,
        which the client would read as real zero-activity data (on
        the error the client honestly draws nothing)."""
        if not portwatch_path.exists():
            return _err("portwatch_not_found",
                        "no PortWatch snapshot has been stored yet "
                        f"(expected artifact: {portwatch_path.name})")
        try:
            return json.loads(portwatch_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return _err("portwatch_unreadable",
                        f"PortWatch snapshot exists but cannot be read: {_safe_exc(exc)}")

    # ── Public API ────────────────────────
    # Read-only, keyless namespace /api/v1/. The graph/events/scorecard
    # handlers below DELEGATE to the app's own handlers above (same
    # closures, same file paths, same validation) — no data logic is
    # forked. The ledger handler reads through the same LedgerStore
    # merged view as GET /llm/ledger/public and projects it through
    # public_api.public_ledger_view (the public page's field rules).
    # Writes need no explicit handlers: FastAPI answers POST/PUT/DELETE
    # on these GET-only routes with 405, which the Starlette handler
    # above maps into the standard error shape. Versioning is the
    # namespace itself (see /api/v1/docs).
    def _v1_rate_check(request: Request):
        """None when the caller is within the bucket, else the 429."""
        # The key is the real peer (uvicorn already
        # applied its own trusted-proxy rewrite) -- X-Forwarded-For is
        # honoured only for SUPERMAP_TRUSTED_PROXIES peers (security.py),
        # never copied from the request.
        client = request_client(request)
        if app.state.api_v1_limiter.allow(client):
            return None
        lim = app.state.api_v1_limits
        return _err("rate_limited",
                    "public API rate limit exceeded for this client "
                    f"(burst {lim['burst']}, refill {lim['refill_per_sec']}/s); "
                    "retry after a short pause")

    def _load_public_ledger() -> dict:
        LedgerStore = None
        for modname in ("llm.ledger", "server.llm.ledger"):
            try:
                import importlib as _il
                LedgerStore = getattr(_il.import_module(modname),
                                      "LedgerStore", None)
                if LedgerStore is not None:
                    break
            except Exception:
                LedgerStore = None
                continue
        if LedgerStore is None:
            raise RuntimeError("ledger store unavailable")
        store_obj = LedgerStore(app.state.ledger_path) \
            if app.state.ledger_path is not None else LedgerStore()
        view = public_ledger_view(store_obj.entries(), store_obj.accuracy())
        if getattr(store_obj, "torn_tail_detected", False):
            # A torn final line was dropped on read — surface
            # it in the returned data; the read itself must not 500.
            view["warnings"] = [
                "torn final ledger line dropped on read"]
        return view

    @app.get("/api/v1/docs", response_class=HTMLResponse)
    async def get_api_v1_docs():
        """Public docs page: self-contained static HTML, no external
        assets. Exempt from the rate bucket (it is the documentation
        for the limits themselves)."""
        return HTMLResponse(content=DOCS_HTML)

    @app.get("/api/v1/health")
    def get_api_v1_health(request: Request):
        # A SYNC handler on purpose — FastAPI runs it
        # in the threadpool, off the event loop, like the adapters
        # router's GET /health. The handler does only synchronous work
        # (rate check + registry read); as an async handler it queued
        # behind every other handler's loop work.
        limited = _v1_rate_check(request)
        if limited is not None:
            return limited
        body: dict = {"status": "ok", "service": "supermap-run-api",
                      "version": "v1"}
        registry_mod = getattr(app.state, "adapter_registry", None)
        if registry_mod is not None:
            try:
                adapters = registry_mod.health_report()
                summary = {"LIVE": 0, "SNAPSHOT": 0, "DOWN": 0}
                for entry in adapters:
                    summary[entry["state"]] = summary.get(entry["state"], 0) + 1
                body["adapters"] = adapters
                body["summary"] = summary
                # The top-level status is DERIVED, not
                # hard-coded — "ok" only when nothing is DOWN and
                # nothing is stale (the fresh/age_s fields: a
                # non-fresh entry is degraded even when its state
                # word is SNAPSHOT); anything else is "degraded".
                degraded = any(
                    entry.get("state") == "DOWN"
                    or entry.get("fresh") is False
                    for entry in adapters)
                body["status"] = "degraded" if degraded else "ok"
            except Exception:
                pass  # health detail unavailable; the ok line stands
        return body

    @app.get("/api/v1/graph")
    async def get_api_v1_graph(request: Request):
        limited = _v1_rate_check(request)
        if limited is not None:
            return limited
        return await get_graph(request)

    @app.get("/api/v1/events")
    async def get_api_v1_events(request: Request):
        limited = _v1_rate_check(request)
        if limited is not None:
            return limited
        return await get_events(request)

    @app.get("/api/v1/scorecard")
    async def get_api_v1_scorecard(request: Request):
        limited = _v1_rate_check(request)
        if limited is not None:
            return limited
        return await get_scorecard()

    @app.get("/api/v1/ledger")
    async def get_api_v1_ledger(request: Request):
        limited = _v1_rate_check(request)
        if limited is not None:
            return limited
        limit, offset, perr = _pagination(request)
        if perr is not None:
            return perr
        try:
            view = _load_public_ledger()
            if limit is not None or offset:
                view = {**view, "entries": _page(view["entries"], limit, offset)}
            return view
        except Exception as exc:
            return _err("ledger_unavailable",
                        f"public ledger is unavailable: {_safe_exc(exc)}")

    @app.get("/chain/")
    @app.get("/chain")
    async def get_chain_empty():
        # Empty run_id: no run can match it — standard 404 shape
        # (the bare /chain/{run_id} route would otherwise fall through
        # to FastAPI's default non-standard 404 body).
        return _err("unknown_run", "unknown run_id: ''")

    # The :path converter is deliberate: a URL-decoded run_id
    # containing "/" (e.g. %2f traversal) must still reach this handler
    # so it can answer with the standard 404 shape, instead of missing
    # the route and falling through to FastAPI's default 404 body.
    @app.get("/chain/{run_id:path}")
    async def get_chain(run_id: str):
        try:
            record = store.load(run_id)
        except ValueError:
            record = None
        if record is None:
            return _err("unknown_run", f"unknown run_id: {run_id!r}")
        events = record["events"]
        rev_extra = ({"engine_rev": 2, "engine_label": record["journal"]["engine_label"]}
                     if record["journal"].get("engine_rev") == 2 else {})
        return {"run_id": run_id, "status": record["status"],
                "event_count": record["event_count"],
                "stream_hash": record["stream_hash"],
                "events": events,
                "edges": materialise_edges(
                    events, record["journal"]["disruption"]["node_id"]),
                **rev_extra}

    # -- alerts & watchlists endpoints ------------------------------
    # Rules/watchlists persist in app.state.alerts_store (data/alerts/).
    # Validation is server/alerts/validate.py (strict: wrong
    # types are a 422 in the standard shape, never coerced, never a
    # 500). Creating or replacing a rule primes the evaluator baseline
    # (evaluator.prime), so a rule only fires on facts that arrive
    # after it exists in its current form.
    def _alerts_adapter_names() -> set | None:
        registry_mod = getattr(app.state, "adapter_registry", None)
        if registry_mod is None:
            return None
        try:
            return {a.name for a in registry_mod.all_adapters()}
        except Exception:
            return None

    def _alerts_unavailable():
        return _err("alerts_unavailable",
                    "alerts layer (server/alerts) is unavailable")

    # -- ownership wiring ----------------------
    # The ownership library (server/alerts/ownership.py) keeps
    # rules/watchlists from being anonymously writable/deletable over
    # HTTP. The wiring, per the library's own contract:
    #   * CREATE (POST): resolve_owner() reuses the caller's
    #     ``supermap_owner`` cookie or mints a fresh token; the token is
    #     stamped on the record via the store's ``owner=`` and the
    #     minted cookie is set on the response (HttpOnly, SameSite=Lax,
    #     Path=/, Secure when the request itself is https).
    #   * WRITE/DELETE of an EXISTING record (PUT, node add/remove,
    #     DELETE): the stored owner must match the caller's cookie, or
    #     the caller must present the admin token (x-admin-token header
    #     vs SUPERMAP_ADMIN_TOKEN). No cookie is minted on these paths —
    #     a caller with no cookie is anonymous and is rejected 403 in
    #     the standard error shape when the record is owned.
    #   * LEGACY records (no owner field) keep
    #     the library's grandfathering: writable/deletable by anyone;
    #     the first authorised write by a cookie-holding (or freshly
    #     minted) caller stamps that caller as owner.
    #   * READS stay open: the library gates writes/deletes only.
    def _owner_cookie(req: Request):
        if alerts_ownership is None:
            return None
        return req.cookies.get(alerts_ownership.COOKIE_NAME)

    def _admin_header(req: Request):
        if alerts_ownership is None:
            return None
        return req.headers.get(alerts_ownership.ADMIN_HEADER)

    def _resolve_owner(req: Request):
        """(owner_token, minted) for a CREATE path."""
        if alerts_ownership is None:
            return None, False
        return alerts_ownership.resolve_owner(_owner_cookie(req))

    def _with_owner_cookie(resp, req: Request, token, minted):
        if minted and token and alerts_ownership is not None:
            resp.set_cookie(
                alerts_ownership.COOKIE_NAME, token,
                httponly=True, samesite="lax", path="/",
                secure=cookie_secure(req))
        return resp

    def _authorize_existing(entry: dict, req: Request):
        """None when the caller may write/delete ``entry``; otherwise
        the standard-shape 403 response. Legacy unowned entries are
        grandfathered inside authorize_delete (any caller passes)."""
        if alerts_ownership is None:
            return None
        try:
            alerts_ownership.authorize_delete(
                entry, owner=_owner_cookie(req),
                admin_token=_admin_header(req))
        except AlertsOwnershipError as exc:
            return _err(exc.code, exc.message)
        return None

    def _write_owner(existing: dict, req: Request):
        """(owner_to_stamp, minted) for a write to an existing record
        the caller is already authorised for: an owned record keeps
        its owner (an admin writing without the cookie must not steal
        or orphan it); a legacy record is claimed by the caller's
        resolved (possibly freshly minted) token."""
        if existing.get("owner"):
            return alerts_ownership.OwnerKey(existing["owner"]), False
        return _resolve_owner(req)

    def _pub(entry: dict, req: Request, token=None) -> dict:
        """The ONLY serialisation path for rules/watchlists.
        The stored owner key never leaves the server; callers get
        ``mine`` (does THIS request's cookie own it) instead."""
        if alerts_ownership is None:
            return entry
        if token is None or isinstance(token, alerts_ownership.OwnerKey):
            token = _owner_cookie(req)  # write path: judged by the cookie
        return alerts_ownership.public_record(entry, token)

    @app.get("/alerts/rules")
    async def list_alert_rules(req: Request):
        if app.state.alerts_store is None:
            return _alerts_unavailable()
        return {"rules": [_pub(r, req)
                          for r in app.state.alerts_store.list_rules()]}

    @app.post("/alerts/rules", status_code=201)
    async def create_alert_rule(req: Request):
        if app.state.alerts_store is None:
            return _alerts_unavailable()
        try:
            body = await req.json()
        except Exception:
            return _err("bad_json", "request body must be JSON")
        try:
            normalized = validate_rule_payload(
                body, node_ids=set(graph.nodes),
                adapter_names=_alerts_adapter_names())
        except AlertsValidationError as exc:
            return _err(exc.code, exc.message)
        ts = alerts_now_iso()
        rule = {"id": alerts_new_id("alr"), **normalized,
                "created_at": ts, "updated_at": ts}
        owner, minted = _resolve_owner(req)
        try:
            rule = app.state.alerts_store.upsert_rule(rule, owner=owner)
        except AlertsValidationError as exc:
            return _err(exc.code, exc.message)
        _alerts_prime()
        return _with_owner_cookie(
            JSONResponse(status_code=201, content=_pub(rule, req, owner)),
            req, owner, minted)

    @app.get("/alerts/rules/{rule_id}")
    async def get_alert_rule(rule_id: str, req: Request):
        if app.state.alerts_store is None:
            return _alerts_unavailable()
        rule = app.state.alerts_store.get_rule(rule_id)
        if rule is None:
            return _err("rule_not_found", f"unknown rule_id: {rule_id!r}")
        return _pub(rule, req)

    @app.put("/alerts/rules/{rule_id}")
    async def replace_alert_rule(rule_id: str, req: Request):
        if app.state.alerts_store is None:
            return _alerts_unavailable()
        existing = app.state.alerts_store.get_rule(rule_id)
        if existing is None:
            return _err("rule_not_found", f"unknown rule_id: {rule_id!r}")
        denied = _authorize_existing(existing, req)
        if denied is not None:
            return denied
        try:
            body = await req.json()
        except Exception:
            return _err("bad_json", "request body must be JSON")
        try:
            normalized = validate_rule_payload(
                body, node_ids=set(graph.nodes),
                adapter_names=_alerts_adapter_names())
        except AlertsValidationError as exc:
            return _err(exc.code, exc.message)
        rule = {"id": rule_id, **normalized,
                "created_at": existing.get("created_at"),
                "updated_at": alerts_now_iso()}
        owner, minted = _write_owner(existing, req)
        try:
            rule = app.state.alerts_store.upsert_rule(rule, owner=owner)
        except AlertsValidationError as exc:
            return _err(exc.code, exc.message)
        _alerts_prime()
        return _with_owner_cookie(
            JSONResponse(content=_pub(rule, req, owner)), req, owner, minted)

    @app.delete("/alerts/rules/{rule_id}")
    async def delete_alert_rule(rule_id: str, req: Request):
        if app.state.alerts_store is None:
            return _alerts_unavailable()
        try:
            deleted = app.state.alerts_store.delete_rule(
                rule_id, owner=_owner_cookie(req),
                admin_token=_admin_header(req))
        except AlertsOwnershipError as exc:
            return _err(exc.code, exc.message)
        if not deleted:
            return _err("rule_not_found", f"unknown rule_id: {rule_id!r}")
        try:  # drop the rule's evaluator condition memory with it
            state = app.state.alerts_store.load_state()
            if isinstance(state.get("rules"), dict):
                state["rules"].pop(rule_id, None)
                app.state.alerts_store.save_state(state)
        except Exception:
            pass
        return {"deleted": True, "rule_id": rule_id}

    @app.get("/alerts/feed")
    async def get_alerts_feed(req: Request):
        if app.state.alerts_store is None:
            return _alerts_unavailable()
        raw_limit = req.query_params.get("limit", "100")
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            return _err("invalid_request", "limit must be an integer")
        if str(limit) != str(raw_limit).strip() and raw_limit not in (str(limit),):
            return _err("invalid_request", "limit must be an integer")
        if not 1 <= limit <= 500:
            return _err("invalid_request", "limit must be within 1..500")
        rule_id = req.query_params.get("rule_id")
        return {"alerts": app.state.alerts_store.read_fired(
            limit=limit, rule_id=rule_id)}

    @app.get("/watchlists")
    async def list_watchlists(req: Request):
        if app.state.alerts_store is None:
            return _alerts_unavailable()
        return {"watchlists": [
            _pub(w, req) for w in app.state.alerts_store.list_watchlists()]}

    @app.post("/watchlists", status_code=201)
    async def create_watchlist(req: Request):
        if app.state.alerts_store is None:
            return _alerts_unavailable()
        try:
            body = await req.json()
        except Exception:
            return _err("bad_json", "request body must be JSON")
        try:
            normalized = validate_watchlist_payload(body)
        except AlertsValidationError as exc:
            return _err(exc.code, exc.message)
        ts = alerts_now_iso()
        watchlist = {"id": alerts_new_id("wat"), **normalized, "node_ids": [],
                     "created_at": ts, "updated_at": ts}
        owner, minted = _resolve_owner(req)
        try:
            watchlist = app.state.alerts_store.upsert_watchlist(
                watchlist, owner=owner)
        except AlertsValidationError as exc:
            return _err(exc.code, exc.message)
        return _with_owner_cookie(
            JSONResponse(status_code=201, content=_pub(watchlist, req, owner)),
            req, owner, minted)

    @app.get("/watchlists/{watchlist_id}")
    async def get_watchlist(watchlist_id: str, req: Request):
        if app.state.alerts_store is None:
            return _alerts_unavailable()
        watchlist = app.state.alerts_store.get_watchlist(watchlist_id)
        if watchlist is None:
            return _err("watchlist_not_found",
                        f"unknown watchlist_id: {watchlist_id!r}")
        return _pub(watchlist, req)

    @app.put("/watchlists/{watchlist_id}")
    async def rename_watchlist(watchlist_id: str, req: Request):
        if app.state.alerts_store is None:
            return _alerts_unavailable()
        existing = app.state.alerts_store.get_watchlist(watchlist_id)
        if existing is None:
            return _err("watchlist_not_found",
                        f"unknown watchlist_id: {watchlist_id!r}")
        denied = _authorize_existing(existing, req)
        if denied is not None:
            return denied
        try:
            body = await req.json()
        except Exception:
            return _err("bad_json", "request body must be JSON")
        try:
            normalized = validate_watchlist_payload(body)
        except AlertsValidationError as exc:
            return _err(exc.code, exc.message)
        watchlist = {**existing, **normalized, "updated_at": alerts_now_iso()}
        owner, minted = _write_owner(existing, req)
        try:
            watchlist = app.state.alerts_store.upsert_watchlist(
                watchlist, owner=owner)
        except AlertsValidationError as exc:
            return _err(exc.code, exc.message)
        return _with_owner_cookie(
            JSONResponse(content=_pub(watchlist, req, owner)),
            req, owner, minted)

    @app.delete("/watchlists/{watchlist_id}")
    async def delete_watchlist(watchlist_id: str, req: Request):
        if app.state.alerts_store is None:
            return _alerts_unavailable()
        try:
            deleted = app.state.alerts_store.delete_watchlist(
                watchlist_id, owner=_owner_cookie(req),
                admin_token=_admin_header(req))
        except AlertsOwnershipError as exc:
            return _err(exc.code, exc.message)
        if not deleted:
            return _err("watchlist_not_found",
                        f"unknown watchlist_id: {watchlist_id!r}")
        return {"deleted": True, "watchlist_id": watchlist_id}

    @app.post("/watchlists/{watchlist_id}/nodes")
    async def add_watchlist_node(watchlist_id: str, req: Request):
        if app.state.alerts_store is None:
            return _alerts_unavailable()
        existing = app.state.alerts_store.get_watchlist(watchlist_id)
        if existing is None:
            return _err("watchlist_not_found",
                        f"unknown watchlist_id: {watchlist_id!r}")
        denied = _authorize_existing(existing, req)
        if denied is not None:
            return denied
        try:
            body = await req.json()
        except Exception:
            return _err("bad_json", "request body must be JSON")
        try:
            node_id = validate_node_add_payload(body, node_ids=set(graph.nodes))
        except AlertsValidationError as exc:
            return _err(exc.code, exc.message)
        owner, minted = _write_owner(existing, req)
        node_ids = list(existing.get("node_ids") or [])
        if node_id not in node_ids:
            node_ids.append(node_id)
            existing = {**existing, "node_ids": node_ids,
                        "updated_at": alerts_now_iso()}
            try:
                existing = app.state.alerts_store.upsert_watchlist(
                    existing, owner=owner)
            except AlertsValidationError as exc:
                return _err(exc.code, exc.message)
        return _with_owner_cookie(
            JSONResponse(content=_pub(existing, req, owner)),
            req, owner, minted)

    @app.delete("/watchlists/{watchlist_id}/nodes/{node_id}")
    async def remove_watchlist_node(watchlist_id: str, node_id: str,
                                    req: Request):
        if app.state.alerts_store is None:
            return _alerts_unavailable()
        existing = app.state.alerts_store.get_watchlist(watchlist_id)
        if existing is None:
            return _err("watchlist_not_found",
                        f"unknown watchlist_id: {watchlist_id!r}")
        denied = _authorize_existing(existing, req)
        if denied is not None:
            return denied
        owner, minted = _write_owner(existing, req)
        node_ids = [n for n in (existing.get("node_ids") or []) if n != node_id]
        if node_ids != list(existing.get("node_ids") or []):
            existing = {**existing, "node_ids": node_ids,
                        "updated_at": alerts_now_iso()}
            try:
                existing = app.state.alerts_store.upsert_watchlist(
                    existing, owner=owner)
            except AlertsValidationError as exc:
                return _err(exc.code, exc.message)
        return _with_owner_cookie(
            JSONResponse(content=_pub(existing, req, owner)),
            req, owner, minted)

    @app.get("/watchlists/{watchlist_id}/view")
    async def get_watchlist_view(watchlist_id: str, req: Request):
        """Per-node current state for one watchlist, display only:
        graph identity (from the graph FILE, as served by /graph),
        the node's latest events (from the events file, as served
        by /events), and its chain involvement across stored runs
        (runs counted from the run store; "latest" = most recently
        modified run file). Nothing here computes a new fact — every
        value is read off a served source."""
        if app.state.alerts_store is None:
            return _alerts_unavailable()
        watchlist = app.state.alerts_store.get_watchlist(watchlist_id)
        if watchlist is None:
            return _err("watchlist_not_found",
                        f"unknown watchlist_id: {watchlist_id!r}")
        graph_nodes: dict[str, dict] = {}
        try:
            graph_doc = json.loads(graph_path.read_text(encoding="utf-8"))
            for node in graph_doc.get("nodes", []):
                if isinstance(node, dict) and node.get("id"):
                    graph_nodes[node["id"]] = node
        except Exception:
            graph_nodes = {}
        events_by_node: dict[str, list] = {}
        for ev in alerts_evaluator.gather_facts(events_path=events_path)["events"]:
            events_by_node.setdefault(ev.get("node_id"), []).append(ev)
        for rows in events_by_node.values():
            rows.sort(key=lambda e: str(e.get("detected_at", "")), reverse=True)
        run_files = []
        if store.dir.exists():
            run_files = sorted(store.dir.glob("*.json"),
                               key=lambda p: p.stat().st_mtime, reverse=True)
        run_records = []
        for path in run_files:
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(record, dict) and record.get("run_id"):
                run_records.append(record)
        nodes_view = []
        for node_id in watchlist.get("node_ids") or []:
            node = graph_nodes.get(node_id) or {}
            node_events = events_by_node.get(node_id, [])
            involved = [r for r in run_records
                        if any(isinstance(e, dict) and e.get("node_id") == node_id
                               for e in r.get("events") or [])]
            delays = [float(e["delay_d"]) for r in involved
                      for e in r.get("events") or []
                      if isinstance(e, dict) and e.get("node_id") == node_id
                      and isinstance(e.get("delay_d"), (int, float))]
            nodes_view.append({
                "node_id": node_id,
                "name": node.get("name"),
                "mode": node.get("mode"),
                "kind": node.get("kind"),
                "active_events": sum(
                    1 for e in node_events if e.get("status") != "resolved"),
                "latest_events": [
                    {"id": e.get("id"), "kind": e.get("kind"),
                     "severity": e.get("severity"), "status": e.get("status"),
                     "detected_at": e.get("detected_at")}
                    for e in node_events[:3]],
                "chain": {
                    "runs_involved": len(involved),
                    "run_ids": [r["run_id"] for r in involved[:10]],
                    "max_delay_d": max(delays) if delays else None,
                    "latest_run_id": involved[0]["run_id"] if involved else None,
                },
            })
        return {"watchlist": _pub(watchlist, req), "nodes": nodes_view}

    # -- SIM routes (additive only) ---------------------
    # POST /sim/run creates (or idempotently returns) a tick-simulation
    # run over the pinned graph; GET /sim/run/{id} serves its journal +
    # derivation summary (plus tick_seconds / start_at /
    # end_at / seed / final-tick state_hash for the timeline ruler);
    # GET /sim/run/{id}/markers serves timeline markers derived
    # only from stored facts; GET /sim/run/{id}/vehicle/{entity_id}
    # ?tick=N serves one vehicle's generated identity
    # (MODELLED) + its state entry at the tick; GET /sim/state/{id}?tick=N is a pure
    # function of the stored record; GET /sim/weather/{id} serves the
    # pinned Open-Meteo snapshot, with absence as a 200 state, not an
    # error.
    # Validation is strict throughout: unknown keys / wrong types
    # are 422 in the standard shape, never coerced, never clamped.
    # Reads are served through the store's
    # record LRU + the state/markers payload caches wired in
    # create_app — records are immutable per content-hash id, so
    # cached responses are byte-identical to uncached ones.

    def _f1_cached(cache: OrderedDict, key, max_entries: int, compute,
                   weigh=None, max_bytes: int | None = None):
        """Bounded-LRU memo for pure payload computations. With
        ``weigh`` (value -> estimated bytes) and ``max_bytes`` the memo is
        also bounded in bytes (newest entry always kept)."""
        with _F1_LOCK:  # handlers run on worker threads (offload.py)
            if key in cache:
                cache.move_to_end(key)
                return cache[key][0] if weigh else cache[key]
        value = compute()
        weight = weigh(value) if weigh else 0
        with _F1_LOCK:
            if weigh:
                if key in cache:
                    app.state.sim_state_cache_bytes -= cache[key][1]
                cache[key] = (value, weight)
                app.state.sim_state_cache_bytes += weight
                cache.move_to_end(key)
                while len(cache) > 1 and (
                        len(cache) > max_entries
                        or (max_bytes is not None
                            and app.state.sim_state_cache_bytes > max_bytes)):
                    _k, (_v, w) = cache.popitem(last=False)
                    app.state.sim_state_cache_bytes -= w
            else:
                cache[key] = value
                while len(cache) > max_entries:
                    cache.popitem(last=False)
        return value

    def _sim_state_payload(record: dict, tick: int) -> dict:
        """state_at(record, tick) through the state cache.

        The payload is a pure function of the immutable record, so
        the cached object is byte-identical to a fresh computation
        (its state_hash is computed inside state_at, pre-cache). The
        cache is bounded in entries AND bytes."""
        return _f1_cached(
            app.state.sim_state_cache,
            (record["sim_run_id"], tick), SIM_STATE_CACHE_MAX,
            lambda: sim_state_at(record, tick),
            weigh=lambda v: len(json.dumps(v)) * SIM_STATE_RAM_FACTOR,
            max_bytes=app.state.sim_state_cache_max_bytes)

    def _chain_store_fingerprint() -> tuple:
        """(name, size, mtime_ns) per chain run file — a stat-only
        fingerprint of the chain store, so the markers cache (whose
        chain_event markers read that store) notices a newly created
        Chain run without re-reading any record. Costs one directory
        scan + stat per file, microseconds next to a markers
        computation over a tiered-graph record."""
        try:
            paths = sorted(store.dir.glob("*.json"))
        except OSError:
            return ()
        fp = []
        for p in paths:
            try:
                st = p.stat()
            except OSError:
                continue
            fp.append((p.name, st.st_size, st.st_mtime_ns))
        return tuple(fp)

    def _sim_summary(record: dict) -> dict:
        d = record["derivation"]
        w = record.get("weather")
        j = record["journal"]
        # Everything the timeline ruler needs in
        # the summary itself — tick size in seconds, the run's span
        # [start_at, end_at) with end_at = start_at + ticks x
        # tick_seconds, the seed, and the FINAL tick's state_hash (a
        # pure function of the stored record, like every state).
        tick_s = SIM_TICK_SECONDS[j["tick"]]
        start_epoch = sim_parse_ts(j["start_at"])
        return {
            "sim_run_id": record["sim_run_id"], "status": record["status"],
            "tick_size": j["tick"],
            "tick_seconds": tick_s,
            "ticks": j["ticks"],
            "start_at": j["start_at"],
            "end_at": sim_fmt_ts(start_epoch + tick_s * j["ticks"]),
            "seed": j["seed"],
            "state_hash": _sim_state_payload(record, j["ticks"] - 1)["state_hash"]
            if j["ticks"] >= 1 else None,
            "entity_count": d["entity_count"],
            "vessel_count": d["vessel_count"],
            "aircraft_count": d["aircraft_count"],
            "weather_window": record.get("weather_window"),
            "provenance": {
                **record["provenance"],
                "weather": ({"source": w["source"],
                             "retrieved_at": w["retrieved_at"],
                             "hash": w["hash"]} if w else None),
                "weather_reason": record.get("weather_reason"),
            },
        }

    # The summary of a stored run is a pure function of an IMMUTABLE record (its id is the hash of the journal core), so
    # it is computed once per id and kept. Without it every first POST / status read of a stored run
    # parsed the whole record (a 182 MB demo run: about 5 s of CPU and 600 MB of resident memory) just to rebuild the same
    # few kilobytes. It is filled from the record as stored (what every reader sees), never from the in-memory build.
    sim_summary_memo: OrderedDict = OrderedDict()
    sim_summary_lock = threading.Lock()
    SIM_SUMMARY_MEMO_MAX = 64

    def _sim_summary_remember(sim_run_id: str, record: dict) -> dict:
        summary = _sim_summary(record)
        with sim_summary_lock:
            sim_summary_memo[sim_run_id] = summary
            sim_summary_memo.move_to_end(sim_run_id)
            while len(sim_summary_memo) > SIM_SUMMARY_MEMO_MAX:
                sim_summary_memo.popitem(last=False)
        return summary

    def _sim_summary_known(sim_run_id: str):
        """The kept summary while the run is still stored (an evicted run is not served from memory), else None."""
        with sim_summary_lock:
            summary = sim_summary_memo.get(sim_run_id)
        if summary is not None and app.state.sim_store.exists(sim_run_id):
            return summary
        return None

    def _sim_weather_snapshot(weather_graph):
        """Fetch the weather snapshot, reusing a successful one for
        SUPERMAP_SIM_WEATHER_TTL_S seconds (default 1800; 0 = never reuse).
        The snapshot hash folds into the run id, so without reuse every
        POST of identical inputs would get a new id and a full rebuild.
        The snapshot keeps its own retrieved_at stamp; failures
        are never cached."""
        try:
            ttl = float(os.environ.get("SUPERMAP_SIM_WEATHER_TTL_S", "1800"))
        except ValueError:
            ttl = 1800.0
        key = hashlib.sha256("|".join(
            sorted(n["id"] for n in weather_graph["nodes"])).encode()).hexdigest()
        cache = app.state.sim_weather_cache
        now = time.monotonic()
        hit = cache.get(key)
        if ttl > 0 and hit is not None and now - hit[0] < ttl:
            return hit[1], None
        snapshot, reason = sim_fetch_snapshot(
            weather_graph, fetch_json=app.state.sim_weather_fetcher)
        if snapshot is not None and ttl > 0:
            cache.clear()
            cache[key] = (now, snapshot)
        return snapshot, reason

    _place_name_map: dict[str, str] = {}

    def _place_names() -> dict[str, str]:
        """Place id -> display name, for sim marker labels (built once)."""
        if not _place_name_map:
            for nid, n in graph.nodes.items():
                nm = n.get("name") if isinstance(n, dict) else None
                if isinstance(nm, str) and nm:
                    _place_name_map[nid] = nm
        return _place_name_map

    def _chain_run_records() -> list[dict]:
        """Every stored chain run record, READ-ONLY (for chain
        markers): scanned from this app's existing run store in
        filename order; unreadable files are skipped, never written,
        and the chain store's behaviour is untouched."""
        records = []
        try:
            paths = sorted(store.dir.glob("*.json"))
        except OSError:
            return records
        for p in paths:
            try:
                rec = store.load(p.stem)
            except Exception:
                continue
            if isinstance(rec, dict) and rec.get("events"):
                records.append(rec)
        return records

    # -- SIM async build machinery ------------------
    # POST /sim/run is create-then-poll: all synchronous validation
    # is unchanged, but the (potentially minutes-long) build itself
    # runs on the bounded sim_job_pool and the client polls
    # GET /sim/run/{id}/status. The worker below runs the EXACT build
    # tail a synchronous handler would run inline — same
    # sim_build_record call, same sim_run_id assignment, same store
    # save — so a record built asynchronously is byte-identical to
    # one built synchronously.

    def _sim_now_iso() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _sim_is_building(sim_run_id: str) -> bool:
        with app.state.sim_jobs_lock:
            entry = app.state.sim_jobs.get(sim_run_id)
            return entry is not None and entry.get("status") == "building"

    def _sim_evict_finished_locked() -> None:
        """Bound the registry: evict finished entries beyond the last
        SIM_JOB_REGISTRY_FINISHED_MAX (insertion order = finish order
        for finished entries; the worker re-inserts on completion).
        Building entries are never evicted. Caller holds the lock."""
        finished = [k for k, v in app.state.sim_jobs.items()
                    if v.get("status") != "building"]
        excess = len(finished) - SIM_JOB_REGISTRY_FINISHED_MAX
        for key in finished[:max(0, excess)]:
            del app.state.sim_jobs[key]

    def _sim_finish_job(sim_run_id: str, status: str,
                        error: dict | None) -> None:
        with app.state.sim_jobs_lock:
            entry = app.state.sim_jobs.pop(sim_run_id, None)
            if entry is None:
                entry = {"started_at": None}
            entry["status"] = status
            entry["error"] = error
            # Re-insert so finished entries order by finish time for
            # the eviction bound above.
            app.state.sim_jobs[sim_run_id] = entry
            _sim_evict_finished_locked()

    def _sim_run_build(sim_run_id: str, journal_core: dict,
                       snapshot, weather_reason) -> None:
        """Pool worker: the build, then the build's memory goes back to the OS. The release runs after the build
        function has RETURNED: inside it the finished record is still a live local and nothing can be freed."""
        try:
            _sim_run_build_job(sim_run_id, journal_core, snapshot, weather_reason)
        finally:
            release_free_memory()

    def _sim_run_build_job(sim_run_id: str, journal_core: dict,
                           snapshot, weather_reason) -> None:
        """The exact build tail, verbatim."""
        try:
            # A cold build is the memory peak (0.9-1.25 GB): free the
            # parsed-record cache first; reads simply re-parse.
            app.state.sim_store.clear_cache()
            record = sim_build_record(graph.raw, graph.hash, graph.version,
                                      journal_core, snapshot, weather_reason,
                                      getattr(app.state, "sim_cache_dir", None))
            record["sim_run_id"] = sim_run_id
            app.state.sim_store.save(record)
        except SimTierContractError as exc:
            # Tier contract: the same code/message as the synchronous
            # 422, surfaced through the status route as the job's failure.
            _sim_finish_job(sim_run_id, "failed",
                            {"code": "derivation_error",
                             "message": str(exc)})
            return
        except MemoryError:
            _sim_finish_job(sim_run_id, "failed",
                            {"code": "sim_build_failed",
                             "message": SIM_OUT_OF_MEMORY_MESSAGE})
            return
        except Exception as exc:
            _sim_finish_job(sim_run_id, "failed",
                            {"code": "sim_build_failed",
                             "message": scrub_paths(exc)})
            return
        _sim_finish_job(sim_run_id, "complete", None)

    @app.post("/sim/run", status_code=201)
    async def post_sim_run(req: Request):
        if app.state.sim_store is None:
            return _err("sim_unavailable", "sim layer (server/sim) is unavailable")
        try:
            body = await req.json()
        except Exception:
            return _err("bad_json", "request body must be JSON")
        if not isinstance(body, dict):
            return _err("invalid_request", "request body must be an object")
        return _sim_create(body)

    def _sim_create(body: dict, dry: bool = False):
        """Everything POST /sim/run does after the body is parsed. With
        ``dry`` it stops once the run id is known and returns it as a str
        (the demo route and the boot-time demo build share this exact
        path, so their id is the id a POST of the same body gets)."""
        extra = sorted(set(body) - {"disruptions", "tick", "ticks", "seed",
                                    "start_at", "weather", "airspace",
                                    "storm_cells", "interventions",
                                    "services", "extra", "lane_geometry"})
        if extra:
            return _err("invalid_request", f"unknown sim request keys: {extra}")
        tick = body.get("tick", "day")
        if not isinstance(tick, str) or tick not in SIM_TICK_SECONDS:
            return _err("invalid_request",
                        f"tick must be one of {sorted(SIM_TICK_SECONDS)}")
        ticks = body.get("ticks", SIM_DEFAULT_TICKS[tick])
        if not isinstance(ticks, int) or isinstance(ticks, bool) \
                or not 1 <= ticks <= SIM_MAX_TICKS[tick]:
            return _err("invalid_request",
                        f"ticks must be an integer in 1..{SIM_MAX_TICKS[tick]} "
                        f"for tick={tick!r} (rejected, never clamped)")
        seed = body.get("seed")
        if not isinstance(seed, int) or isinstance(seed, bool):
            return _err("invalid_request", "seed must be an integer")
        weather_flag = body.get("weather", True)
        if not isinstance(weather_flag, bool):
            return _err("invalid_request", "weather must be a boolean")
        start_at_in = body.get("start_at")
        if start_at_in is not None and not isinstance(start_at_in, str):
            return _err("invalid_request", "start_at must be an ISO-8601 string")
        disruptions_in = body.get("disruptions", [])
        if not isinstance(disruptions_in, list):
            return _err("invalid_request", "disruptions must be a list")
        # Resolve every disruption to its effective event-shaped spec.
        effective: list[dict] = []
        try:
            for i, item in enumerate(disruptions_in):
                if not isinstance(item, dict):
                    return _err("invalid_request",
                                f"disruptions[{i}] must be an object")
                if "disruption_id" in item:
                    if set(item) != {"disruption_id"}:
                        return _err("invalid_request",
                                    f"disruptions[{i}]: a live entry carries "
                                    "only disruption_id")
                    did = item["disruption_id"]
                    if not isinstance(did, str):
                        return _err("invalid_request",
                                    "disruption_id must be a string")
                    event = _current_disruptions().get(did)
                    if event is None:
                        return _err("unknown_disruption",
                                    f"unknown disruption_id: {did!r}")
                    if event.get("status") == "resolved":
                        return _err("disruption_resolved",
                                    f"disruption {did} is resolved; sim runs "
                                    "require an active event")
                    raw = {"node_id": event["node_id"], "kind": event["kind"],
                           "capacity_loss": event.get("capacity_loss"),
                           "duration_d": event.get("expected_duration_d") or 7.0,
                           "shape": "step",
                           "started_at": event.get("started_at")
                           or event.get("detected_at")}
                else:
                    keys = set(item)
                    allowed = {"node_id", "kind", "capacity_loss",
                               "duration_d", "started_at", "shape"}
                    if keys - allowed:
                        return _err("invalid_request",
                                    f"disruptions[{i}]: unknown keys: "
                                    f"{sorted(keys - allowed)}")
                    raw = dict(item)
                spec = validate_disruption(graph, raw)
                if spec["shape"] != "step":
                    return _err("invalid_shape",
                                "sim v1 supports step-shape disruptions only; "
                                f"got shape={spec['shape']!r} (never "
                                "silently reinterpreted)")
                spec = {**spec, "sim_id": f"d{i + 1}"}
                effective.append(spec)
        except EngineError as exc:
            return _err(exc.code, str(exc))
        # SIM-native shock constructs, additive to
        # the /sim/run input ONLY (the /run contract never sees
        # them). Strict: exact key sets, real numbers (no bools,
        # no coercion), anchors must name graph places, timestamps
        # must parse. Violations are 422 in the standard error shape.
        def _sim_shock_number(value, field, i, kind_name):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return None, _err("invalid_request",
                                  f"{kind_name}[{i}].{field} must be a number")
            return float(value), None

        airspace_eff: list[dict] = []
        airspace_in = body.get("airspace", [])
        if not isinstance(airspace_in, list):
            return _err("invalid_request", "airspace must be a list")
        for i, item in enumerate(airspace_in):
            if not isinstance(item, dict):
                return _err("invalid_request", f"airspace[{i}] must be an object")
            if set(item) != {"anchor", "radius_km", "started_at", "duration_d"}:
                return _err("invalid_request",
                            f"airspace[{i}]: keys must be exactly anchor, "
                            "radius_km, started_at, duration_d")
            anchor = item["anchor"]
            if not isinstance(anchor, str) or anchor not in graph.nodes:
                return _err("invalid_request",
                            f"airspace[{i}].anchor: unknown place: {anchor!r}")
            radius, err = _sim_shock_number(item["radius_km"], "radius_km",
                                            i, "airspace")
            if err:
                return err
            if not 0.0 < radius <= 20000.0:
                return _err("invalid_request",
                            f"airspace[{i}].radius_km out of bounds (0, 20000]")
            duration, err = _sim_shock_number(item["duration_d"], "duration_d",
                                              i, "airspace")
            if err:
                return err
            if not 0.0 < duration <= 3650.0:
                return _err("invalid_request",
                            f"airspace[{i}].duration_d out of bounds (0, 3650]")
            if not isinstance(item["started_at"], str):
                return _err("invalid_request",
                            f"airspace[{i}].started_at must be an ISO-8601 string")
            try:
                started = sim_fmt_ts(sim_parse_ts(item["started_at"]))
            except (ValueError, TypeError):
                return _err("invalid_request",
                            f"airspace[{i}].started_at is not a valid "
                            f"ISO-8601 timestamp: {item['started_at']!r}")
            airspace_eff.append({"anchor": anchor, "radius_km": radius,
                                 "duration_d": duration,
                                 "started_at": started,
                                 "sim_id": f"a{i + 1}"})
        storm_eff: list[dict] = []
        storm_in = body.get("storm_cells", [])
        if not isinstance(storm_in, list):
            return _err("invalid_request", "storm_cells must be a list")
        for i, item in enumerate(storm_in):
            if not isinstance(item, dict):
                return _err("invalid_request",
                            f"storm_cells[{i}] must be an object")
            if set(item) != {"from_node", "to_node", "radius_km",
                             "started_at", "duration_d"}:
                return _err("invalid_request",
                            f"storm_cells[{i}]: keys must be exactly "
                            "from_node, to_node, radius_km, started_at, "
                            "duration_d")
            for field in ("from_node", "to_node"):
                if not isinstance(item[field], str) \
                        or item[field] not in graph.nodes:
                    return _err("invalid_request",
                                f"storm_cells[{i}].{field}: unknown place: "
                                f"{item[field]!r}")
            radius, err = _sim_shock_number(item["radius_km"], "radius_km",
                                            i, "storm_cells")
            if err:
                return err
            if not 0.0 < radius <= 20000.0:
                return _err("invalid_request",
                            f"storm_cells[{i}].radius_km out of bounds "
                            "(0, 20000]")
            duration, err = _sim_shock_number(item["duration_d"],
                                              "duration_d", i, "storm_cells")
            if err:
                return err
            if not 0.0 < duration <= 3650.0:
                return _err("invalid_request",
                            f"storm_cells[{i}].duration_d out of bounds "
                            "(0, 3650]")
            if not isinstance(item["started_at"], str):
                return _err("invalid_request",
                            f"storm_cells[{i}].started_at must be an "
                            "ISO-8601 string")
            try:
                started = sim_fmt_ts(sim_parse_ts(item["started_at"]))
            except (ValueError, TypeError):
                return _err("invalid_request",
                            f"storm_cells[{i}].started_at is not a valid "
                            f"ISO-8601 timestamp: {item['started_at']!r}")
            storm_eff.append({"from_node": item["from_node"],
                              "to_node": item["to_node"],
                              "radius_km": radius, "duration_d": duration,
                              "started_at": started,
                              "sim_id": f"s{i + 1}"})
        # start_at: explicit, else earliest disruption start, else the
        # API's current UTC hour — recorded in the journal either way.
        try:
            if start_at_in is not None:
                start_at = sim_fmt_ts(sim_parse_ts(start_at_in))
            else:
                starts = [s["started_at"] for s in effective if s.get("started_at")]
                if starts:
                    start_at = sim_fmt_ts(min(sim_parse_ts(s) for s in starts))
                else:
                    from datetime import datetime as _dt, timezone as _tz
                    now = _dt.now(_tz.utc).replace(minute=0, second=0,
                                                   microsecond=0)
                    start_at = sim_fmt_ts(now.timestamp())
        except (ValueError, TypeError):
            return _err("invalid_request",
                        f"start_at is not a valid ISO-8601 timestamp: "
                        f"{start_at_in!r}")
        # Sim loop: tick-stamped interventions fold into the three
        # effective lists ONLY when the key is present (an absent or
        # empty list leaves the journal core, hence the id, untouched).
        if body.get("interventions"):
            try:
                effective, airspace_eff, storm_eff = sim_fold_interventions(
                    body["interventions"], graph, ticks,
                    sim_parse_ts(start_at), SIM_TICK_SECONDS[tick],
                    sim_fmt_ts, validate_disruption, EngineError,
                    effective, airspace_eff, storm_eff)
            except SimLoopRefused as exc:
                return _err(exc.code, exc.message)
        elif "interventions" in body and not isinstance(
                body["interventions"], list):
            return _err("invalid_request", "interventions must be a list")
        # Services layer (opt-in sidecar, data/graph/services.json): the
        # selection and the sidecar's file hash fold into the journal core
        # ONLY when the body opts in, so every other body keeps its id.
        services_sel = None
        if "services" in body and body["services"] is not None \
                and body["services"] is not False:
            try:
                from graph_services.runtime import selection_for_run
            except Exception:  # imported as server.api.* from the repo root
                from server.graph_services.runtime import selection_for_run  # type: ignore
            services_sel, _svc, svc_err = selection_for_run(
                graph.raw, graph.hash, body["services"], start_at)
            if svc_err:
                return _err("invalid_request", svc_err)
        # Graph extra sidecar (opt-in, data/graph/extra.json): same rule -
        # the selection and the file hash fold into the journal core ONLY
        # when the body opts in. The weather snapshot is then fetched for
        # the merged sim view so the added places carry a series too.
        extra_sel = None
        weather_graph = graph.raw
        if "extra" in body and body["extra"] is not None \
                and body["extra"] is not False:
            try:
                from graph_extra.runtime import (selection_for_run as extra_selection_for_run,
                                                 sim_graph as extra_sim_graph)
            except Exception:  # imported as server.api.* from the repo root
                from server.graph_extra.runtime import (  # type: ignore
                    selection_for_run as extra_selection_for_run,
                    sim_graph as extra_sim_graph)
            extra_sel, _ex, extra_err = extra_selection_for_run(
                graph.raw, graph.hash, body["extra"], "sim")
            if extra_err:
                return _err("invalid_request", extra_err)
            weather_graph = extra_sim_graph(graph.raw, graph.hash, extra_sel)[0]
        # Lane geometry (opt-in, data/graph/lane_geometry.json over the pinned sea_routes sidecar): the
        # two content hashes fold into the journal core ONLY when the body says true, so every other
        # body keeps its id and every byte of its record.
        lane_geo_sel = None
        if "lane_geometry" in body and body["lane_geometry"] is not None:
            if not isinstance(body["lane_geometry"], bool):
                return _err("invalid_request", "lane_geometry must be a boolean")
            if body["lane_geometry"]:
                try:
                    from lanegeo.sim import selection_for_run as lane_selection_for_run
                except Exception:  # imported as server.api.* from the repo root
                    from server.lanegeo.sim import selection_for_run as lane_selection_for_run  # type: ignore
                try:
                    lane_geo_sel = lane_selection_for_run()
                except Exception as exc:
                    return _err("lane_geometry_unreadable", _safe_exc(exc))
        # Weather: pinned at creation; failure is a labelled absence.
        snapshot, weather_reason = None, None
        if weather_flag:
            snapshot, weather_reason = _sim_weather_snapshot(weather_graph)
        else:
            weather_reason = "weather not requested (weather: false)"
        journal_core = {
            "graph_hash": graph.hash, "graph_version": graph.version,
            "disruptions": effective, "tick": tick, "ticks": ticks,
            "seed": seed, "start_at": start_at,
            "weather_snapshot_hash": snapshot["hash"] if snapshot else None,
        }
        if services_sel:
            journal_core["services"] = services_sel
        if extra_sel:
            journal_core["extra"] = extra_sel
        if lane_geo_sel:
            journal_core["lane_geometry"] = lane_geo_sel
        # Airspace / storm-cell constructs fold into the journal core (hence into
        # the sim_run_id) only when present, so bodies without them keep
        # their ids byte-identically.
        if airspace_eff:
            journal_core["airspace"] = airspace_eff
        if storm_eff:
            journal_core["storm_cells"] = storm_eff
        sim_run_id = derive_sim_run_id(journal_core)
        if dry:
            return sim_run_id
        if app.state.sim_store.exists(sim_run_id):
            summary = _sim_summary_known(sim_run_id)
            if summary is None:
                summary = _sim_summary_remember(
                    sim_run_id, app.state.sim_store.load(sim_run_id))
            return JSONResponse(status_code=201, content=summary)
        # Create-then-poll: register a single-flight
        # job and hand the build to the bounded pool. A job already
        # building for this id is JOINED (202, no second build, no
        # pool slot consumed — mirrors the ensstats duplicate rule).
        # A failed entry is not joined: a re-POST retries the build.
        # The pool slot is reserved BEFORE registration:
        # saturation is the existing 429 rate_limited.
        with app.state.sim_jobs_lock:
            entry = app.state.sim_jobs.get(sim_run_id)
            if entry is not None and entry.get("status") == "building":
                return JSONResponse(
                    status_code=202,
                    content={"sim_run_id": sim_run_id, "status": "building"})
            full = app.state.sim_store.storage_refusal()
            if full:
                return _err("storage_full", full)
            pool = app.state.sim_job_pool
            if not pool.reserve():
                return _err(
                    "rate_limited",
                    "sim build queue is full "
                    f"({app.state.sim_job_workers} running, "
                    f"{SIM_JOB_POOL_QUEUE} queued); retry once a "
                    "running build completes")
            app.state.sim_jobs.pop(sim_run_id, None)
            app.state.sim_jobs[sim_run_id] = {
                "status": "building", "started_at": _sim_now_iso(),
                "error": None}
        try:
            pool.submit_reserved(
                lambda: _sim_run_build(sim_run_id, journal_core,
                                       snapshot, weather_reason))
        except Exception:
            _sim_finish_job(sim_run_id, "failed",
                            {"code": "sim_build_failed",
                             "message": "sim build could not be queued"})
            return _err("sim_build_failed",
                        "sim build could not be queued")
        return JSONResponse(
            status_code=202,
            content={"sim_run_id": sim_run_id, "status": "building"})

    # The demo run (server/sim/demo.py): one fixed request, hence one fixed
    # run id, built once at boot and pinned against eviction.
    def _sim_demo_request() -> dict:
        try:
            from sim.demo import demo_request
        except Exception:  # imported as server.api.* from the repo root
            from server.sim.demo import demo_request  # type: ignore
        return demo_request()

    @app.get("/sim/demo")
    async def get_sim_demo():
        """The demo run: its fixed request, its id and where it stands
        (ready | building | failed | absent). Read-only; builds nothing."""
        if app.state.sim_store is None:
            return _err("sim_unavailable", "sim layer (server/sim) is unavailable")
        body = _sim_demo_request()
        sid = _sim_create(body, dry=True)
        if not isinstance(sid, str):
            return sid
        store = app.state.sim_store
        if store.exists(sid):
            status = "ready"
        else:
            with app.state.sim_jobs_lock:
                entry = app.state.sim_jobs.get(sid)
                status = (entry or {}).get("status") or "absent"
            status = {"building": "building", "failed": "failed"}.get(status, "absent")
        return {"sim_run_id": sid, "status": status,
                "pinned": store.is_pinned(sid), "request": body}

    def _sim_demo_prebuild() -> str | None:
        """Pin the demo run and start its build (no-op when it is stored).
        Returns the run id, or None when the request does not validate on
        this graph. Called once at boot when SUPERMAP_SIM_DEMO_PREBUILD=1."""
        if app.state.sim_store is None:
            return None
        body = _sim_demo_request()
        sid = _sim_create(body, dry=True)
        if not isinstance(sid, str):
            return None
        app.state.sim_store.pin(sid)
        if not app.state.sim_store.exists(sid):
            _sim_create(body)
        # The Simulation window always asks for the demo run's undisrupted twin (its baseline);
        # building it here too means opening the demo is a cache hit, not a second 40 s wait.
        twin = _sim_create({**body, "disruptions": []}, dry=True)
        if isinstance(twin, str):
            app.state.sim_store.pin(twin)
            if not app.state.sim_store.exists(twin):
                _sim_create({**body, "disruptions": []})
        return sid

    app.state.sim_demo_prebuild = _sim_demo_prebuild

    # Registered BEFORE /sim/run/{sim_run_id:path} (and alongside
    # /markers and /vehicle): the {sim_run_id:path} converter would
    # otherwise swallow the "/status" suffix as part of the id.
    @app.get("/sim/run/{sim_run_id}/status")
    async def get_sim_run_status(sim_run_id: str):
        """Create-then-poll status for POST /sim/run:
        a stored record is ready (with its summary, the same payload
        the 201 creation response carries); a building job reports
        its start; a failed job reports its stored error verbatim;
        an id with neither record nor job is the existing 404."""
        if app.state.sim_store is None:
            return _err("sim_unavailable", "sim layer (server/sim) is unavailable")
        known = _sim_summary_known(sim_run_id)
        if known is not None:
            return {"sim_run_id": sim_run_id, "status": "ready",
                    "summary": known}
        record = app.state.sim_store.load(sim_run_id)
        if record is not None:
            return {"sim_run_id": sim_run_id, "status": "ready",
                    "summary": _sim_summary_remember(sim_run_id, record)}
        with app.state.sim_jobs_lock:
            entry = app.state.sim_jobs.get(sim_run_id)
            entry = dict(entry) if entry is not None else None
        if entry is not None and entry.get("status") == "building":
            return {"sim_run_id": sim_run_id, "status": "building",
                    "started_at": entry.get("started_at")}
        if entry is not None and entry.get("status") == "failed":
            return {"sim_run_id": sim_run_id, "status": "failed",
                    "error": entry.get("error")}
        return _err("unknown_sim_run", f"unknown sim_run_id: {sim_run_id!r}")

    # Registered BEFORE /sim/run/{id}: the {sim_run_id:path} converter
    # would otherwise swallow the "/markers" suffix as part of the id.
    @app.get("/sim/run/{sim_run_id}/markers")
    async def get_sim_markers(sim_run_id: str, labels: str | None = None):
        """Timeline markers derived ONLY from
        stored facts — the sim record's journal windows + event
        segments, plus (for chain_event) the stored chain events of a
        Chain run for the same live disruption, read from the existing
        run store read-only. Computed on request; no new stored
        state, no simulation. Unknown id -> the existing 404 shape."""
        if app.state.sim_store is None or sim_compute_markers is None:
            return _err("sim_unavailable", "sim layer (server/sim) is unavailable")
        record = app.state.sim_store.load(sim_run_id)
        if record is None:
            if _sim_is_building(sim_run_id):
                return _err("sim_run_not_ready",
                            f"sim run {sim_run_id} is still building")
            return _err("unknown_sim_run", f"unknown sim_run_id: {sim_run_id!r}")
        # Markers are a pure function of the sim record plus
        # the chain store, so the cache key pairs the run id with
        # the chain store's stat fingerprint — a new chain run changes
        # the fingerprint and the markers recompute, preserving the
        # absent-then-present chain behaviour exactly.
        # Place-name labels are OPT-IN (?labels=place): the default
        # payload keeps the id-bearing labels so a body's markers
        # bytes stay identical.
        use_names = labels == "place"
        key = (record["sim_run_id"], _chain_store_fingerprint(), use_names)
        return _f1_cached(
            app.state.sim_markers_cache, key, SIM_MARKERS_CACHE_MAX,
            lambda: {"sim_run_id": record["sim_run_id"],
                     **sim_compute_markers(
                         record, _chain_run_records(),
                         _place_names() if use_names else None)})

    # Registered BEFORE /sim/run/{id} for the same reason as
    # /markers: the {sim_run_id:path} converter would otherwise
    # swallow the "/vehicle/..." suffix as part of the id.
    @app.get("/sim/run/{sim_run_id}/vehicle/{entity_id}")
    async def get_sim_vehicle(sim_run_id: str, entity_id: str,
                              req: Request):
        """One vehicle's generated identity (MODELLED)
        plus its state entry at ?tick=N. Identity is the derivation
        entity's stored facts (a pure function of the entity id);
        state is served through the same _sim_state_payload cache as
        GET /sim/state, so the entry bytes match the state route
        exactly. Tick validation mirrors get_sim_state exactly:
        tick is required, must be an integer, and must lie in
        0..ticks-1. A vehicle with no state entry at the tick has
        not departed yet — state is null with a stated reason, not
        an error. Weather is sampled from the run's pinned snapshot
        at the vehicle's position (lane series while EN_ROUTE, node
        series at a place); null when no snapshot is pinned."""
        if app.state.sim_store is None:
            return _err("sim_unavailable", "sim layer (server/sim) is unavailable")
        record = app.state.sim_store.load(sim_run_id)
        if record is None:
            if _sim_is_building(sim_run_id):
                return _err("sim_run_not_ready",
                            f"sim run {sim_run_id} is still building")
            return _err("unknown_sim_run", f"unknown sim_run_id: {sim_run_id!r}")
        raw_tick = req.query_params.get("tick")
        tick = _parse_tick(raw_tick)
        if tick is None:
            return _err("invalid_request",
                        "tick query parameter must be an integer")
        if not 0 <= tick < record["journal"]["ticks"]:
            return _err("invalid_request",
                        f"tick out of range 0..{record['journal']['ticks'] - 1}")
        ent = next((e for e in record["derivation"]["entities"]
                    if e["id"] == entity_id), None)
        if ent is None:
            return _err("unknown_sim_vehicle",
                        f"unknown entity id in sim run {sim_run_id!r}: "
                        f"{entity_id!r}")
        payload = _sim_state_payload(record, tick)
        entry = next((e for e in payload["entities"]
                      if e["id"] == entity_id), None)
        identity: dict = {
            # Generated identity is MODELLED throughout.
            "basis": "MODELLED",
            "mode": ent["mode"], "lane": ent["lane"],
            "from": ent["from"], "to": ent["to"],
            "speed_kmh": ent["speed_kmh"],
            "speed_class": ent["speed_class"],
            "dwell_h": ent["dwell_h"],
        }
        if ent["mode"] == "sea":
            identity["vessel_name"] = ent["vessel_name"]
            identity["vessel_class"] = ent["vessel_class"]
            identity["vessel_priors"] = _priors_for_display(
                ent["vessel_class"], ent["vessel_priors"])
        else:
            identity["tail"] = ent["tail"]
            identity["aircraft_type"] = ent["aircraft_type"]
            identity["cls"] = ent["cls"]
            identity["rotation"] = ent["rotation"]
            identity["home_hub"] = ent["home_hub"]
            if ent.get("dwell_hub_h") is not None:
                identity["dwell_hub_h"] = ent["dwell_hub_h"]
        snapshot = record.get("weather")
        weather = None
        if snapshot is not None and entry is not None:
            tick_time = sim_parse_ts(payload["sim_time"])
            if entry["state"] == "EN_ROUTE":
                weather = sim_lane_weather_at(
                    snapshot, ent["lane"], ent["from"], tick_time)
            else:
                weather = sim_node_weather_at(
                    snapshot, entry["node"], tick_time)
        resp: dict = {
            "sim_run_id": record["sim_run_id"], "entity_id": entity_id,
            "tick": tick, "sim_time": payload["sim_time"],
            "identity": identity, "state": entry,
            "weather": weather,
            "weather_source": snapshot.get("source") if snapshot else None,
        }
        if entry is None:
            resp["state_reason"] = "vehicle has not departed at this tick"
        return resp

    # Sim loop (server/api/sim_loop.py): fork / journal / lineage
    # / compare / verify. Mounted HERE, before the {sim_run_id:path}
    # catch-all below, which would otherwise swallow "/journal" and
    # "/verify" as part of the id.
    if SimStore is not None:
        mount_sim_loop(app, SimpleNamespace(
            graph=graph, err=_err, sim_summary=_sim_summary,
            tick_seconds=SIM_TICK_SECONDS, parse_ts=sim_parse_ts,
            fmt_ts=sim_fmt_ts, validate_disruption=validate_disruption,
            engine_error=EngineError, derive_sim_run_id=derive_sim_run_id,
            build_record=lambda *a: sim_build_record(*a),
            tier_error=SimTierContractError,
            out_of_memory_message=SIM_OUT_OF_MEMORY_MESSAGE,
            registry_finished_max=SIM_JOB_REGISTRY_FINISHED_MAX))

    @app.get("/sim/run/{sim_run_id:path}")
    async def get_sim_run(sim_run_id: str):
        if app.state.sim_store is None:
            return _err("sim_unavailable", "sim layer (server/sim) is unavailable")
        record = app.state.sim_store.load(sim_run_id)
        if record is None:
            if _sim_is_building(sim_run_id):
                return _err("sim_run_not_ready",
                            f"sim run {sim_run_id} is still building")
            return _err("unknown_sim_run", f"unknown sim_run_id: {sim_run_id!r}")
        d = record["derivation"]
        w = record.get("weather")
        return {
            **_sim_summary(record),
            "journal": record["journal"],
            # Additive: per-node sim added-delay stats
            # vs the undisrupted twin (stored at creation) + the
            # chain consistency block when a chain run links (markers
            # link rule, read-only); null when no link exists.
            "sim_delay": record.get("sim_delay_by_node") or {},
            "consistency": (sim_consistency_block(record, _chain_run_records())
                            if sim_consistency_block is not None else None),
            "derivation": {
                "lanes": d["lanes"],
                "paths": d.get("paths", {}),
                "split": d["split"], "basis": d["basis"],
                "headway_coarsening": d["headway_coarsening"],
                "longest_lane_duration_h": d["longest_lane_duration_h"],
                "preroll_s": d["preroll_s"],
                # Additive: the air module block —
                # class split + counts, integrator hubs, turnaround
                # bands, bank/wave schedule, and the shock kind
                # interpretation table incl. the ground-stop gap.
                "air": d.get("air"),
            },
            "weather": ({"source": w["source"],
                         "retrieved_at": w["retrieved_at"],
                         "hash": w["hash"]} if w else None),
            "weather_reason": record.get("weather_reason"),
        }

    @app.get("/sim/state/{sim_run_id:path}")
    async def get_sim_state(sim_run_id: str, req: Request):
        if app.state.sim_store is None:
            return _err("sim_unavailable", "sim layer (server/sim) is unavailable")
        record = app.state.sim_store.load(sim_run_id)
        if record is None:
            if _sim_is_building(sim_run_id):
                return _err("sim_run_not_ready",
                            f"sim run {sim_run_id} is still building")
            return _err("unknown_sim_run", f"unknown sim_run_id: {sim_run_id!r}")
        raw_tick = req.query_params.get("tick")
        tick = _parse_tick(raw_tick)
        if tick is None:
            return _err("invalid_request",
                        "tick query parameter must be an integer")
        if not 0 <= tick < record["journal"]["ticks"]:
            return _err("invalid_request",
                        f"tick out of range 0..{record['journal']['ticks'] - 1}")
        return _sim_state_payload(record, tick)

    @app.get("/sim/weather/{sim_run_id:path}")
    async def get_sim_weather(sim_run_id: str, request: Request):
        if app.state.sim_store is None:
            return _err("sim_unavailable", "sim layer (server/sim) is unavailable")
        record = app.state.sim_store.load(sim_run_id)
        if record is None:
            if _sim_is_building(sim_run_id):
                return _err("sim_run_not_ready",
                            f"sim run {sim_run_id} is still building")
            return _err("unknown_sim_run", f"unknown sim_run_id: {sim_run_id!r}")
        if record.get("weather") is None:
            # Absence is a state, not an error.
            payload = {"weather": None,
                       "reason": record.get("weather_reason") or "weather not pinned"}
        else:
            payload = {"weather": record["weather"]}
        # A PINNED-weather document is ~5.4 MB per tick of
        # full cell grids (163 MB for a 30-tick run), which
        # would make weather runs unopenable in the browser. Only the
        # envelope gets /graph's gzip treatment: the body is the
        # same json.dumps serialisation Starlette's JSONResponse
        # produced for the returned dict (byte-identical decoded),
        # gzipped when the client accepts gzip. Error responses keep
        # the standard _err envelope, exactly as /graph's do.
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False,
                          separators=(",", ":")).encode("utf-8")
        headers = {"Vary": "Accept-Encoding"}
        if _accepts_gzip(request.headers.get("accept-encoding")):
            return Response(content=gzip.compress(body, compresslevel=6,
                                                  mtime=0),
                            media_type="application/json",
                            headers={**headers,
                                     "Content-Encoding": "gzip"})
        return Response(content=body, media_type="application/json",
                        headers=headers)

    # Health router (server/adapters/health.py) — mounted when present,
    # AND its adapter registry populated at startup (otherwise the
    # router would be mounted while the registry stays empty, so /health
    # reports zero adapters). The registry populated here is the one
    # the mounted router actually reads: health.py binds it as
    # ``mod.registry``, so population targets that exact module object,
    # whichever import name ("adapters.*" / "server.adapters.*") loaded.
    #
    # STARTUP-STATE CHOICE: no adapter fetch() cycle is run here — app
    # creation must never block on the network. Registered adapters
    # therefore report the documented initial health (state=DOWN,
    # mode=None, last_success_at=None: registered, never run), which is
    # the truthful last-known/configured state until a real fetch cycle
    # (e.g. adapters.run_once) mutates the same health objects and
    # /health reports observed LIVE/SNAPSHOT/DOWN. The one annotation
    # made at startup is NEEDS-KEY for key-gated adapters, because that
    # is a pure configuration fact (see _annotate_key_gated), never a
    # claim that a fetch happened. A broken adapter module must never
    # take app creation down, so population failures are swallowed the
    # same way the mount always has been.
    app.state.adapter_registry = None
    for modname in ("adapters.health", "server.adapters.health"):
        try:
            import importlib
            mod = importlib.import_module(modname)
            router = getattr(mod, "router", None)
            registry_mod = getattr(mod, "registry", None)
            if registry_mod is not None:
                app.state.adapter_registry = registry_mod
                try:
                    _populate_adapter_registry(registry_mod, adapter_registry)
                except Exception:
                    pass  # mount the router anyway; /health then reads
                    # whatever registry state exists (possibly empty)
            if router is not None:
                app.include_router(router)
            break
        except Exception:
            continue

    # LLM router (server/llm/router.py) — mounted when present, with
    # the same defensive dual-import convention as the health router
    # above ("llm.*" with server/ on sys.path, "server.llm.*" imported
    # as a package from the repo root). Injectable via create_app's
    # ``llm_router`` argument, mirroring ``adapter_registry``:
    #   * None (default) or True -> mount the llm router when it can be
    #     imported and is not None (llm/router.py itself sets
    #     router=None when FastAPI is unavailable in that process);
    #   * False -> mount nothing: the app boots without /llm routes;
    #   * an APIRouter instance -> mount exactly that router.
    # A missing or broken llm package must never take app creation
    # down, so import/mount failures are swallowed the same way the
    # health mount always has been, and the app serves everything else.
    # The mounted router (or None) is exposed as app.state.llm_router.
    #
    # The default mount is built via
    # llm.router.build_router(runs_dir=<this app's run store>) so the
    # Explainer (and AskTheMap's run scan) resolve runs from the SAME
    # store this app writes (consulted first, with fallback to the
    # llm package's default store), so explaining a run the
    # API has just created in a non-default runs dir works. An
    # explicitly injected router instance is mounted exactly as given
    # (its builder configuration is the caller's). If build_router is
    # unavailable or fails, fall back to the module-level router.
    app.state.llm_router = None
    if llm_router is not False:
        _llm_router = None
        if llm_router is None or llm_router is True:
            import importlib
            for _modname in ("llm.router", "server.llm.router"):
                try:
                    _mod = importlib.import_module(_modname)
                except Exception:
                    continue
                _build_router = getattr(_mod, "build_router", None)
                if _build_router is not None:
                    try:
                        # Ledger-route mounting: the
                        # router resolves ledger references against
                        # THIS app's ledger file and event store.
                        _llm_router = _build_router(
                            runs_dir=store.dir,
                            ledger_path=app.state.ledger_path,
                            events_path=events_path)
                    except Exception:
                        _llm_router = None
                if _llm_router is None:
                    _llm_router = getattr(_mod, "router", None)
                if _llm_router is not None:
                    break
        else:  # an explicitly injected router object
            _llm_router = llm_router
        if _llm_router is not None:
            try:
                app.include_router(_llm_router)
                app.state.llm_router = _llm_router
            except Exception:
                app.state.llm_router = None

    # ── Chain scenario loop (journal / compare / verify / shock book /
    # scenario library): server/api/chain_loop.py, API only, read-only over
    # the run store. Defensive like the desks seam below.
    try:
        from .chain_loop import register_chain_loop
        register_chain_loop(app, {
            "err": _err, "engine": engine, "engine2": engine2, "store": store,
            "replay_journal": replay_journal, "state_from_record": state_from_record,
        })
        app.state.chain_loop = True
    except Exception:
        app.state.chain_loop = False

    # ── Analysis Desks routes (additive /desks/*) ──
    # Defensive like the llm seam: an unavailable desks module must
    # never take the API down; the surfaces then honestly 404.
    try:
        from .desks_routes import register_desks_routes
        register_desks_routes(app, {
            "err": _err, "engine": engine, "store": store,
            "graph_path": graph_path, "events_path": events_path,
            "scorecard_path": scorecard_path,
            "impact_service": _impact_service,
            "data_root": data_root,
            "ledger_path": app.state.ledger_path,
            "read_events": _read_folded_events,
            "event_view": lambda rec: with_display_mode(
                _with_citation(rec), _events_feed_states()),
        })
        app.state.desks_routes = True
    except Exception:
        app.state.desks_routes = False

    # LIVE TERMINAL STREAM: /live/* SSE + snapshot + tracks.
    # Flag-gated at request time by app.state.live_twin (off => 404).
    try:
        try:
            from live.routes import register_live_routes
        except ImportError:
            from server.live.routes import register_live_routes
        register_live_routes(app, {
            "err": _err, "data_root": data_root, "events_path": events_path,
            "health": _alerts_health})
    except Exception:
        app.state.live_routes = False

    # Satellite Watch scan service, overlay, brief and signals routes
    # (server/satwatch/routes.py). Defensive like the desks seam: a broken
    # satwatch package must never take the API down.
    _register_satwatch = _satwatch_attr("routes", "register_satwatch_routes")
    try:
        if _register_satwatch is None:
            raise ImportError("satwatch routes unavailable")
        _register_satwatch(app, {
            "satwatch_dir": satwatch_dir, "events_path": events_path,
            "graph_path": graph_path})
        app.state.satwatch_routes = True
    except Exception:
        app.state.satwatch_routes = False

    # Muse Spark vision read of a stored satellite crop:
    # /satwatch/vision/{plan,run}. Served from the llm package because the
    # satwatch package imports no llm module; defensive like the seam above.
    try:
        try:
            from llm.satvision_routes import register_satvision_routes
        except ImportError:
            from server.llm.satvision_routes import register_satvision_routes  # type: ignore
        register_satvision_routes(app, {"satwatch_dir": satwatch_dir})
    except Exception:
        app.state.llm_satvision_routes = False

    # Ports tracker (/ports*) and report/export centre
    # (/reports*, /exports*). Additive and defensive like the seams above.
    for _optional_mount in ("ports.routes:register_ports_routes",
                    "reports.routes:register_report_routes"):
        try:
            import importlib as _il_f
            _m, _fn = _optional_mount.split(":")
            getattr(_il_f.import_module(_m), _fn)(app)
        except Exception:
            logging.getLogger(__name__).warning(
                "optional route mount skipped: %s", _optional_mount, exc_info=True)

    # ── History backplay / fork / realism routes ──
    # /history/* and /realism*: additive, read-only against the engine and
    # sim; defensive like the desks seam (a missing/broken package leaves
    # the rest of the API untouched and the routes honestly 404).
    try:
        try:
            from history.routes import register_history_routes
        except ImportError:
            from server.history.routes import register_history_routes  # type: ignore
        register_history_routes(app, {
            "data_root": data_root, "graph_path": graph_path,
            "repo_data_dir": (data_root if (data_root / "replay").is_dir()
                              else REPO_ROOT / "data"),
            "run_store": store, "get_sim_store": lambda: app.state.sim_store,
            "live_events": _read_folded_events,
            "engine_graph": engine.graph, "graph": graph,
            "graph_hash": graph.hash,
            "validate_disruption": validate_disruption,
            "validate_params": validate_params, "derive_run_id": derive_run_id,
        })
        app.state.history_routes = True
    except Exception:
        app.state.history_routes = False

    # ── Research workbench: /research/* historical archive queries,
    # event studies (descriptive statistics only), recorded scene scans and exports. Offline by default.
    # Defensive like the seams above.
    try:
        try:
            from research.routes import register_research_routes
        except ImportError:
            from server.research.routes import register_research_routes  # type: ignore

        def _research_book():
            svc = getattr(app.state, "history_service", None)
            if svc is not None:
                return svc.book()
            from history.places import PlaceBook
            return PlaceBook.load(graph_path)

        register_research_routes(app, {
            "data_root": data_root, "book": _research_book,
            "history": lambda: getattr(app.state, "history_service", None)})
        app.state.research_routes = True
    except Exception:
        logging.getLogger(__name__).warning("research mount skipped", exc_info=True)
        app.state.research_routes = False

    # ── Lane planner: POST /lanes/plan, additive and
    # read-only; ranges from named deterministic variants, composed from
    # engine2 and the graph sidecars (no engine code is touched). Defensive
    # like the seams above: a missing or broken package leaves the API as it was.
    try:
        try:
            from laneplan.routes import register_lane_plan_routes
        except ImportError:
            from server.laneplan.routes import register_lane_plan_routes  # type: ignore
        register_lane_plan_routes(app, {"err": _err, "graph": graph})
        app.state.lane_plan_routes = True
    except Exception:
        app.state.lane_plan_routes = False

    # ── Voice input: POST /voice/transcribe and
    # GET /voice/status. Speech to text through Meta (or a labelled RECORDED
    # fixture). Defensive like the seams
    # above: a missing or broken package leaves the API as it was.
    try:
        try:
            from voice.routes import register_voice_routes
        except ImportError:
            from server.voice.routes import register_voice_routes  # type: ignore
        register_voice_routes(app, {})
        app.state.voice_routes = True
    except Exception:
        app.state.voice_routes = False

    # ── Agent mode: /agent/* tool-calling sessions.
    # Calls the app's own routes in-process (ASGI), so it can do only what the
    # HTTP API can. Additive and defensive like the seams above: a broken
    # package leaves the API untouched and /agent/* honestly 404s.
    try:
        try:
            from agent.routes import register_agent_routes
        except ImportError:
            from server.agent.routes import register_agent_routes  # type: ignore
        register_agent_routes(app, {"graph": graph, "data_root": data_root})
        app.state.agent_routes = True
    except Exception:
        logging.getLogger(__name__).warning("agent mount skipped", exc_info=True)
        app.state.agent_routes = False

    # Lane geometry: GET /graph/lanes/geometry, additive and read-only.
    # Defensive like the seams above.
    try:
        try:
            from lanegeo.routes import register_lane_geometry_routes
        except ImportError:
            from server.lanegeo.routes import register_lane_geometry_routes  # type: ignore
        register_lane_geometry_routes(app, {"err": _err, "graph": graph})
        app.state.lane_geometry_routes = True
    except Exception:
        app.state.lane_geometry_routes = False

    # Shipment exposure: GET /exposure/sample, POST /exposure/evaluate and
    # /exposure/export. Stateless and read-only. Defensive like the seams above.
    try:
        try:
            from exposure.routes import register_exposure_routes
        except ImportError:
            from server.exposure.routes import register_exposure_routes  # type: ignore
        register_exposure_routes(app, {"graph": graph})
        app.state.exposure_routes = True
    except Exception:
        logging.getLogger(__name__).warning("exposure routes skipped", exc_info=True)
        app.state.exposure_routes = False

    # Monte Carlo (input-uncertainty simulation, seeded; outside the engine):
    # /montecarlo*. Defensive like the seams above.
    try:
        try:
            from montecarlo.routes import register_montecarlo_routes
        except ImportError:
            from server.montecarlo.routes import register_montecarlo_routes  # type: ignore
        register_montecarlo_routes(app, {"err": _err, "graph": graph,
                                         "core_graph": engine.graph,
                                         "data_root": data_root})
        app.state.montecarlo_routes = True
    except Exception:
        app.state.montecarlo_routes = False
        logging.getLogger(__name__).warning("montecarlo routes skipped", exc_info=True)
    # ── Disruption brief: /brief*. A one-page note
    # written from a stored run; every figure comes from the engine and the
    # writer's text is machine-checked against it.
    # Additive and defensive like the seams above: a broken package leaves the
    # API untouched and /brief* honestly 404s.
    try:
        try:
            from brief.routes import register_brief_routes
        except ImportError:
            from server.brief.routes import register_brief_routes  # type: ignore
        register_brief_routes(app, {})
        app.state.brief_routes = True
    except Exception:
        logging.getLogger(__name__).warning("brief mount skipped", exc_info=True)
        app.state.brief_routes = False

    # Replay window: /replay/episodes read-only over
    # stored files, plus the XLSX writer for the comparison table. Defensive
    # like the seams above.
    try:
        from .replay_routes import register_replay_routes
        register_replay_routes(app, {})
        app.state.replay_routes = True
    except Exception:
        app.state.replay_routes = False
        logging.getLogger(__name__).warning("replay routes skipped", exc_info=True)

    # Demo-token gate unlock link: /demo/*.
    # Defensive like the seams above; the gate itself lives in llm/demo_gate.py.
    try:
        from .demo_routes import register_demo_routes
        register_demo_routes(app)
    except Exception as exc:
        if type(exc).__name__ == "DemoTokenError":
            raise       # a demo token shorter than 16 characters never starts live mode: not a skippable seam
        logging.getLogger(__name__).warning("demo routes skipped", exc_info=True)

    # ── Scenario builder: /scenarios/* validate, save, organise and
    # run many shocks on one timeline. Additive and defensive
    # like the seams above: a missing or broken package leaves the API as it was.
    try:
        try:
            from scenario.routes import register_scenario_routes
        except ImportError:
            from server.scenario.routes import register_scenario_routes  # type: ignore
        register_scenario_routes(app, {"engine": engine, "engine2": engine2, "graph": graph,
                                       "store": store})
        app.state.scenario_routes = True
    except Exception:
        logging.getLogger(__name__).warning("scenario mount skipped", exc_info=True)
        app.state.scenario_routes = False

    # Boot-time demo build (the deployment sets SUPERMAP_SIM_DEMO_PREBUILD=1): the demo run
    # is too large to ship in the repository, so it is built once and pinned. Off by
    # default (a build is 40-120 CPU-seconds); a failure only leaves the run absent.
    if os.environ.get("SUPERMAP_SIM_DEMO_PREBUILD") == "1" and getattr(app.state, "sim_demo_prebuild", None):
        def _demo_boot():
            time.sleep(3.0)
            try:
                app.state.sim_demo_prebuild()
            except Exception:
                logging.getLogger(__name__).warning("demo sim prebuild failed", exc_info=True)
        threading.Thread(target=_demo_boot, name="sim-demo-prebuild", daemon=True).start()

    # These two MUST remain the last statements in create_app so the
    # offload wrapper and security middleware cover every route registered above.
    # Run every async-def handler off the event loop.
    # KEEP THIS LAST: it wraps the routes registered above, so any route
    # added after it would stay on the loop.
    offload_blocking_routes(app)
    # Per-client rate limits on every mutating route,
    # chunk-proof body cap. Also last: it sits just inside CORS.
    install_security(app, max_body_bytes=MAX_BODY_BYTES)

    return app


app = create_app()
