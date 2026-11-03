"""Analysis Desks routes: CRIT, LANE/REROUTE,
REPLAY, VERIFY, CHAN, COVER, LENS, REPORT, COST, WX, EVAL.

Additive only — every route lives under /desks/. Registered from
create_app via register_desks_routes(app, ctx) with the app's own
engine/store/paths, so no data logic is forked. Handlers follow the
house error shape ({"error": {"code", "message"}}) via the _err
callable passed in ctx.
"""
from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import threading
import time
import uuid
import zipfile
from pathlib import Path

from fastapi import Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse

REPO_ROOT = Path(__file__).resolve().parents[2]


def _md_to_html(md: str, title: str) -> str:
    """Minimal, escaping Markdown->HTML for the report export (headings,
    lists, paragraphs, inline code/bold). The report's own generator
    only emits those constructs."""
    out = []
    in_list = False
    for line in md.splitlines():
        s = line.rstrip()
        if s.startswith("- ") or s.startswith("* "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_inline(s[2:])}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        if s.startswith("### "):
            out.append(f"<h3>{_inline(s[4:])}</h3>")
        elif s.startswith("## "):
            out.append(f"<h2>{_inline(s[3:])}</h2>")
        elif s.startswith("# "):
            out.append(f"<h1>{_inline(s[2:])}</h1>")
        elif s.strip():
            out.append(f"<p>{_inline(s)}</p>")
    if in_list:
        out.append("</ul>")
    body = "\n".join(out)
    return (f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<title>{html.escape(title)}</title></head>"
            f"<body>{body}</body></html>")


try:
    from reports.csv_guard import neutralise as _neutralise_csv_cell
except ImportError:  # imported as server.api.* from the repo root
    from server.reports.csv_guard import neutralise as _neutralise_csv_cell  # type: ignore
# (the one shared formula-injection guard; mirrors the web exporter's
# neutraliseCsvCell)


def _inline(text: str) -> str:
    esc = html.escape(text)
    parts = esc.split("`")
    for i in range(1, len(parts), 2):
        parts[i] = f"<code>{parts[i]}</code>"
    return "".join(parts)


from .security import scrub_paths


def register_desks_routes(app, ctx: dict) -> None:
    _err = ctx["err"]
    read_events = ctx["read_events"]
    event_view = ctx.get("event_view") or (lambda rec: rec)
    engine = ctx["engine"]
    store = ctx["store"]
    graph_path: Path = ctx["graph_path"]
    replay_dir = Path(ctx["scorecard_path"]).parent
    import os as _os
    # The sweep and pack stores follow the app's one data root
    # (SUPERMAP_DATA_DIR) like every other store; the per-store env
    # overrides still win.
    data_root = Path(ctx.get("data_root") or (REPO_ROOT / "data"))
    crit_dir = Path(_os.environ.get("SUPERMAP_CRIT_DIR",
                                    str(data_root / "criticality")))
    reports_dir = Path(_os.environ.get("SUPERMAP_REPORTS_DIR",
                                       str(data_root / "reports")))
    eval_set_path = REPO_ROOT / "data" / "eval" / "labelled_set.json"
    events_path: Path = ctx["events_path"]
    impact_service = ctx.get("impact_service")  # factory or None

    def _load_run(run_id: str):
        try:
            return store.load(run_id)
        except ValueError:
            return None

    def _load_graph_doc():
        """Parsed graph file, or None when it cannot be read AS a
        graph (unparseable, non-object document, nodes/edges not
        lists, a node without an id) — callers map None to the
        graph_unreadable error, like GET /graph."""
        try:
            doc = json.loads(graph_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(doc, dict):
            return None
        nodes = doc.get("nodes", [])
        edges = doc.get("edges", [])
        if not isinstance(nodes, list) or not isinstance(edges, list):
            return None
        if any(not isinstance(n, dict) or "id" not in n for n in nodes):
            return None
        if any(not isinstance(e, dict) for e in edges):
            return None
        return doc

    # ── WHYNOT ──────────────────────────────────────────────────────
    # Served on its own route, NOT on GET /chain: the chain-payload shape
    # is a fixed interface, so the additive not_hit record stays out of that payload.
    @app.get("/desks/whynot/{run_id:path}")
    async def desks_whynot(run_id: str):
        record = _load_run(run_id)
        if record is None:
            return _err("unknown_run", f"unknown run_id: {run_id!r}")
        return {"run_id": run_id, "not_hit": record.get("not_hit", [])}

    # ── CRIT ────────────────────────────────────────────────────────
    # The sweep runs on a bounded worker pool
    # (the ensstats families' pool class), never a fresh thread per
    # POST.
    try:
        from ensstats.jobs import BoundedJobPool
    except Exception:  # imported as server.api.* from the repo root
        from server.ensstats.jobs import BoundedJobPool  # type: ignore
    crit_pool = BoundedJobPool(name="crit-pool")
    crit_job: dict = {"state": "idle", "done": 0, "total": 0}

    @app.get("/desks/crit")
    async def desks_crit():
        from crit.sweep import load_sweep
        sweep = load_sweep(crit_dir, engine.graph)
        if sweep is None:
            return {"status": "not_computed",
                    "graph_version": engine.graph.version,
                    "graph_hash": engine.graph.hash,
                    "job": crit_job, "rows": []}
        return {"status": "stored", "job": crit_job, **sweep}

    crit_done_at = [0.0]    # time.monotonic() when the last sweep finished

    def _crit_cooldown_s() -> float:
        try:
            return max(0.0, float(_os.environ.get("SUPERMAP_CRIT_COOLDOWN_S", "0")))
        except ValueError:
            return 0.0

    # The claim below is check-then-set on shared state. The route is lock-exempt (offload.LOCK_EXEMPT_PATHS),
    # so it does not use the app-wide mutation lock and carries its own small lock.
    crit_claim_lock = threading.Lock()

    @app.post("/desks/crit/run")
    async def desks_crit_run():
        with crit_claim_lock:
            return _crit_claim_and_start()

    def _crit_claim_and_start():
        if crit_job["state"] == "running":
            return {"job": crit_job}
        # The sweep is deterministic, so re-running it
        # earns nothing; inside the cool-down the finished job is returned and
        # no new sweep starts (default 0 = no cool-down).
        left = _crit_cooldown_s() - (time.monotonic() - crit_done_at[0]) \
            if crit_done_at[0] else 0.0
        if crit_job["state"] == "complete" and left > 0:
            return {"job": {**crit_job, "cooldown_s": round(left)}}
        from crit.sweep import run_sweep, store_sweep

        def work():
            try:
                def prog(done, total):
                    crit_job.update({"done": done, "total": total})
                sweep = run_sweep(engine, progress=prog)
                store_sweep(sweep, crit_dir)
                crit_job.update({"state": "complete",
                                 "elapsed_s": sweep["elapsed_s"]})
                crit_done_at[0] = time.monotonic()
            except Exception as exc:  # job failure is served, not hidden
                crit_job.update({"state": "failed",
                                 "error": scrub_paths(exc)})

        if not crit_pool.reserve():
            return _err("rate_limited",
                        "criticality sweep queue is full; retry once "
                        "a running sweep completes")
        # Claim the job HERE, on the request path: the
        # "running" state is set before the worker thread starts, so a
        # burst of POSTs cannot each slip past the guard above and
        # spawn its own thread before the first worker marks the
        # job running. Setting it synchronously keeps a duplicate
        # POST returning the existing job.
        # Clear every result key from a previous run at claim time:
        # a stale elapsed_s (or a failed run's error) must never show
        # during or after the next run.
        for key in ("elapsed_s", "error"):
            crit_job.pop(key, None)
        crit_job.update({"state": "running", "done": 0, "total": 0})
        crit_pool.submit_reserved(work)
        return {"job": crit_job}

    @app.get("/desks/crit/job")
    async def desks_crit_job():
        return {"job": crit_job}

    # ── LANE + REROUTE ──────────────────────────────────────────────
    @app.post("/desks/lane")
    async def desks_lane(req: Request):
        from lane.routing import stress_test
        try:
            body = await req.json()
        except Exception:
            return _err("bad_json", "request body must be JSON")
        if not isinstance(body, dict):
            return _err("invalid_request", "request body must be a JSON object")
        origin, dest = body.get("origin"), body.get("destination")
        for field, val in (("origin", origin), ("destination", dest)):
            if not isinstance(val, str) or val not in engine.graph.nodes:
                return _err("unknown_place", f"unknown {field} place: {val!r}")
        if origin == dest:
            return _err("invalid_request", "origin and destination must differ")
        result = stress_test(engine.graph, origin, dest)
        result["method"] = ("shortest path over served leg transit times; "
                            "single points of failure found by re-searching "
                            "the path with each place on it removed")
        return result

    @app.post("/desks/reroute")
    async def desks_reroute(req: Request):
        from lane.routing import bypass_explore
        try:
            body = await req.json()
        except Exception:
            return _err("bad_json", "request body must be JSON")
        if not isinstance(body, dict):
            return _err("invalid_request", "request body must be a JSON object")
        place = body.get("place_id")
        if not isinstance(place, str) or place not in engine.graph.nodes:
            return _err("unknown_place", f"unknown place: {place!r}")
        dest = body.get("destination")
        if dest is not None and (not isinstance(dest, str)
                                 or dest not in engine.graph.nodes):
            return _err("unknown_place", f"unknown destination place: {dest!r}")
        return bypass_explore(engine.graph, place, dest)

    # ── REPLAY ──────────────────────────────────────────────────────
    def _replay_doc(event_id: str) -> dict | None:
        if (not event_id or "/" in event_id or "\\" in event_id
                or ".." in event_id or "\x00" in event_id):
            return None
        if event_id == "scorecard":
            return None  # the scorecard is not a replay (list route parity)
        path = replay_dir / f"{event_id}.json"
        if path.resolve().parent != replay_dir.resolve() or not path.exists():
            return None
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        return doc if isinstance(doc, dict) else None

    @app.get("/desks/replays")
    async def desks_replays():
        # Like /events: a malformed record is skipped + counted,
        # never served and never a 500 for every caller.
        out = []
        invalid = 0
        for path in sorted(replay_dir.glob("*.json")):
            if path.name == "scorecard.json":
                continue
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                invalid += 1
                continue
            steps = doc.get("observed_steps", []) if isinstance(doc, dict) else None
            if not isinstance(doc, dict) or not isinstance(steps, list):
                invalid += 1
                continue
            out.append({
                "event_id": doc.get("id") or path.stem,
                "title": doc.get("title"),
                "disruption": doc.get("disruption"),
                "observed_steps": len(steps),
                # Honest field: the stored backtest records carry the
                # disruption + observed timeline steps; no per-day run
                # state was stored by the harness.
                "per_day_run_state": False,
                "stored": True,
            })
        return {"replays": out, "invalid_files": invalid}

    @app.get("/desks/replay/{event_id}")
    async def desks_replay(event_id: str):
        doc = _replay_doc(event_id)
        if doc is None:
            return _err("replay_not_stored",
                        f"no stored replay for event: {event_id!r}")
        doc = {**doc, "per_day_run_state": False}
        return doc

    # ── VERIFY ──────────────────────────────────────────────────────
    @app.post("/desks/verify/{run_id:path}")
    async def desks_verify(run_id: str):
        from .store import replay_journal
        from engine.canonical import stream_hash
        record = _load_run(run_id)
        if record is None:
            return _err("unknown_run", f"unknown run_id: {run_id!r}")
        journal = record.get("journal")
        try:
            if journal is None:
                raise ValueError("run record has no journal")
            state = replay_journal(engine, journal, run_id)
            recomputed = stream_hash(state.events)
        except Exception as exc:
            return {"run_id": run_id, "match": False,
                    "error": f"replay failed: {scrub_paths(exc)}",
                    "stored_stream_hash": record.get("stream_hash"),
                    "journal": journal}
        return {
            "run_id": run_id,
            "match": recomputed == record["stream_hash"],
            "stored_stream_hash": record["stream_hash"],
            "recomputed_stream_hash": recomputed,
            "graph_version": journal.get("graph_version"),
            "journal_graph_hash": journal.get("graph_hash"),
            "current_graph_hash": engine.graph.hash,
            "graph_matches_journal": journal.get("graph_hash") == engine.graph.hash,
            "journal": journal,
        }

    # ── CHAN ────────────────────────────────────────────────────────
    @app.get("/desks/channels/{run_id:path}")
    async def desks_channels(run_id: str, req: Request):
        from engine.engine import CHANNELS
        record = _load_run(run_id)
        if record is None:
            return _err("unknown_run", f"unknown run_id: {run_id!r}")
        events = record["events"]
        ticks = sorted({ev["tick"] for ev in events})
        raw_tick = req.query_params.get("tick")
        if raw_tick is None:
            tick = ticks[-1] if ticks else 0
        else:
            try:
                tick = int(raw_tick)
            except (TypeError, ValueError):
                return _err("invalid_request", "tick must be an integer")
            # Params out of bounds are REJECTED, never clamped (and
            # never answered with a fabricated all-QUIET state).
            if not ticks or tick < ticks[0] or tick > ticks[-1]:
                return _err("invalid_request",
                            "tick is outside the run's tick range")
        counts = {c: 0 for c in CHANNELS}
        for ev in events:
            if ev["tick"] == tick and ev["channel"] in counts:
                counts[ev["channel"]] += 1
        totals = {c: 0 for c in CHANNELS}
        for ev in events:
            if ev["channel"] in totals:
                totals[ev["channel"]] += 1
        return {
            "run_id": run_id, "tick": tick, "ticks": ticks,
            "channels": [
                {"channel": c, "state": "ACTIVE" if counts[c] else "QUIET",
                 "events_at_tick": counts[c], "events_total": totals[c]}
                for c in sorted(CHANNELS)],
        }

    # ── COVER ───────────────────────────────────────────────────────
    @app.get("/desks/coverage")
    async def desks_coverage():
        doc = _load_graph_doc()
        if doc is None:
            return _err("graph_unreadable", "graph artifact cannot be read")
        places = []
        counts: dict[str, int] = {}
        unclassed = 0
        for n in doc.get("nodes", []):
            tier = n.get("tier")
            if tier in ("core", "shell"):
                cls = tier.upper()
                counts[cls] = counts.get(cls, 0) + 1
            else:
                cls = None
                unclassed += 1
            places.append({"place_id": n["id"], "name": n.get("name"),
                           "kind": n.get("kind"), "tier": tier,
                           "coverage_class": cls})
        return {"graph_version": doc.get("version"),
                "legend": [{"class": k, "places": v}
                           for k, v in sorted(counts.items())],
                "unclassed": unclassed, "places": places}

    # ── LENS ────────────────────────────────────────────────────────
    @app.get("/desks/lens/{run_id:path}")
    async def desks_lens(run_id: str, req: Request):
        from impact.derive import CLASSES, CLASS_LABELS
        goods_class = req.query_params.get("class")
        if goods_class not in CLASSES:
            return _err("invalid_request",
                        f"class must be one of {sorted(CLASSES)}")
        record = _load_run(run_id)
        if record is None:
            return _err("unknown_run", f"unknown run_id: {run_id!r}")
        svc = impact_service() if impact_service else None
        if svc is None:
            return _err("impact_unavailable", "impact layer is unavailable")
        from reports.pack import materialise_edges
        chain_places = sorted({ev["node_id"] for ev in record["events"]})
        places = []
        state_by_place: dict[str, str] = {}
        for pid in chain_places:
            goods = svc.node_goods(pid)
            state = "unknown"
            share = None
            if goods.get("state") == "SOURCED":
                mix = (goods.get("combined_mix") or {})
                fig = mix.get(goods_class)
                if fig is not None and fig.get("value") is not None:
                    share = fig["value"]
                    state = "carries" if share > 0 else "not_carried"
            places.append({"place_id": pid, "state": state, "share": share})
            state_by_place[pid] = state
        legs = []
        for e in materialise_edges(record["events"],
                                   record["journal"]["disruption"]["node_id"]):
            legs.append({"from": e["from_node"], "to": e["to_node"],
                         "state": state_by_place.get(e["from_node"], "unknown")})
        return {"run_id": run_id, "goods_class": goods_class,
                "goods_label": CLASS_LABELS[goods_class],
                "method": ("a place carries the class when its served "
                           "goods mix (IMPACT layer) has a share above "
                           "zero for it; a leg carries the class when "
                           "its origin place does; places whose mix is "
                           "not served are unknown, never absent"),
                "places": places, "legs": legs}

    # ── REPORT ──────────────────────────────────────────────────────
    def _report_docs(record: dict):
        from reports.pack import (build_summary, load_ledger_entries)
        # the app's ledger (SUPERMAP_LEDGER_FILE / SUPERMAP_DATA_DIR aware), the same
        # file /llm/ledger and /api/v1/ledger read; never a repo-hard-coded path
        ledger_path = ctx.get("ledger_path") or (Path(ctx["data_root"]) / "ledger.jsonl")
        entries, present = load_ledger_entries(ledger_path, record["run_id"])
        scorecard = None
        sc_path = Path(ctx["scorecard_path"])
        if sc_path.exists():
            try:
                scorecard = json.loads(sc_path.read_text(encoding="utf-8"))
            except Exception:
                scorecard = None
        md = build_summary(record, scorecard, entries, present)
        return md, entries, present

    @app.get("/desks/report/{run_id:path}")
    async def desks_report(run_id: str, req: Request):
        record = _load_run(run_id)
        if record is None:
            return _err("unknown_run", f"unknown run_id: {run_id!r}")
        fmt = req.query_params.get("format", "md")
        md, _entries, _present = _report_docs(record)
        if fmt == "md":
            return PlainTextResponse(md, media_type="text/markdown")
        if fmt == "html":
            return HTMLResponse(_md_to_html(md, f"SupermAP report — {run_id}"))
        return _err("invalid_request", "format must be md or html")

    pack_cache: dict[str, str] = {}  # run_id -> run-record hash served

    def _pack_fingerprint(run_id: str, record: dict) -> str:
        store_dir = getattr(store, "dir", None)
        if store_dir is not None:
            try:
                return hashlib.sha256(
                    (Path(store_dir) / f"{run_id}.json").read_bytes()
                ).hexdigest()
            except OSError:
                pass
        return hashlib.sha256(json.dumps(
            record, sort_keys=True, default=str).encode()).hexdigest()

    @app.get("/desks/pack/{run_id:path}")
    async def desks_pack(run_id: str):
        record = _load_run(run_id)
        if record is None:
            return _err("unknown_run", f"unknown run_id: {run_id!r}")
        fingerprint = _pack_fingerprint(run_id, record)
        out_path = reports_dir / f"pack-{run_id}.zip"
        if pack_cache.get(run_id) == fingerprint and out_path.exists():
            return FileResponse(out_path, media_type="application/zip",
                                filename=f"supermap-pack-{run_id}.zip")
        md, entries, present = _report_docs(record)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("run.json", json.dumps(record, indent=1, sort_keys=True))
            sio = io.StringIO()
            w = csv.writer(sio)
            w.writerow(["seq", "tick", "at", "place_id", "channel",
                        "mechanism", "delay_d", "magnitude", "parent_id"])
            for ev in record["events"]:
                w.writerow([_neutralise_csv_cell(v) for v in (
                            ev.get("seq"), ev.get("tick"), ev.get("at"),
                            ev.get("node_id"), ev.get("channel"),
                            ev.get("mechanism"), ev.get("delay_d"),
                            ev.get("magnitude"), ev.get("parent_id"))])
            zf.writestr("steps.csv", sio.getvalue())
            zf.writestr("report.md", md)
            zf.writestr("report.html", _md_to_html(md, f"SupermAP report — {run_id}"))
            manifest = {
                "pack": "supermap-scenario-pack",
                "run_id": run_id,
                "files": {
                    "run.json": "stored run record from the run store (journal, events, impacts, not_hit)",
                    "steps.csv": "chain steps from the stored run record's event stream",
                    "report.md": "scenario report generated by server/reports/pack.py build_summary",
                    "report.html": "the same report rendered to HTML",
                    "manifest.json": "this source manifest",
                },
                "ledger_file_present": present,
                "ledger_entries_for_run": len(entries),
                "graph_version": record["journal"].get("graph_version"),
                "graph_hash": record["journal"].get("graph_hash"),
                "stream_hash": record["stream_hash"],
            }
            zf.writestr("manifest.json",
                        json.dumps(manifest, indent=1, sort_keys=True))
        reports_dir.mkdir(parents=True, exist_ok=True)
        # Unique tmp + atomic replace: a concurrent reader streams
        # either the old pack or the new one, never a half-rewrite.
        tmp_path = reports_dir / f".pack-{run_id}.{uuid.uuid4().hex}.tmp"
        tmp_path.write_bytes(buf.getvalue())
        tmp_path.replace(out_path)
        pack_cache[run_id] = fingerprint
        return FileResponse(out_path, media_type="application/zip",
                            filename=f"supermap-pack-{run_id}.zip")

    # ── COST ────────────────────────────────────────────────────────
    @app.get("/desks/cost")
    async def desks_cost():
        import os
        try:  # the same runtime path the provider writes to
            from llm.paths import default_spend_path
        except Exception:
            from server.llm.paths import default_spend_path
        spend_path = default_spend_path()
        # The spend store is an
        # append-only JSONL record stream (reserve/settle/release/
        # legacy), not a single JSON document.
        # Spend is read through the provider's own store reader
        # (LLMProvider._spend_data(), the compatibility view over its
        # locked record reader: JSONL parsing, legacy-document
        # migration, fail-closed on corruption) and its status() for
        # the kill-switch block. An unreadable/corrupt store is an
        # error in the standard shape, never a silent zero.
        #
        # COUNT MAPPING (onto this surface's existing shape): only
        # `settle` records — completed calls — and migrated `legacy`
        # records are spend events: they alone count in `entries`,
        # in per-action `calls`, and in the estimated/actual/accrued
        # totals (record fields estimated_cost_usd / actual_cost_usd
        # / accrued_usd). `reserve` records are
        # pre-call cap reservations, not completed spend, and
        # `release` / `settle_failed` records are bookkeeping: none of
        # them counts here (outstanding reservations still bound the
        # cap inside provider.status(), whose accrued figure the
        # kill-switch block reports as before).
        provider = None
        records = None
        status = None
        read_error: Exception | None = None
        cost_fields = ("estimated_cost_usd", "actual_cost_usd",
                       "accrued_usd")

        def _has_bad_cost_field(recs) -> bool:
            return any(
                isinstance(r, dict)
                and r.get("type") in ("settle", "legacy")
                and any(r.get(f) is not None
                        and (isinstance(r.get(f), bool)
                             or not isinstance(r.get(f), (int, float)))
                        for f in cost_fields)
                for r in recs)

        for modname in ("llm.provider", "server.llm.provider"):
            try:
                import importlib
                mod = importlib.import_module(modname)
                provider = mod.LLMProvider(spend_path=spend_path)
                records = provider._spend_data()["entries"]
                # Validate BEFORE provider.status(): status() sums
                # today's settle records with float(), so a bad-typed
                # cost field would otherwise escape through the read
                # error path (502 with exception text) instead of the
                # clean 422 below.
                if _has_bad_cost_field(records):
                    return _err("invalid_request",
                                "spend record cost fields must be numbers")
                status = provider.status()
                break
            except Exception as exc:
                provider = None
                records = None
                status = None
                if read_error is None:
                    read_error = exc  # the FIRST failure is the real cause
                continue
        if records is None:
            return _err("provider_error",
                        "spend store cannot be read via the LLM "
                        f"provider: {scrub_paths(read_error)}")
        entries = [r for r in records
                   if isinstance(r, dict)
                   and r.get("type") in ("settle", "legacy")]
        per_action: dict[str, dict] = {}
        totals = {"estimated_usd": 0.0, "actual_usd": 0.0, "accrued_usd": 0.0}
        field_of = {"estimated_usd": "estimated_cost_usd",
                    "actual_usd": "actual_cost_usd",
                    "accrued_usd": "accrued_usd"}
        for e in entries:
            for field in field_of.values():
                v = e.get(field)
                if v is not None and (isinstance(v, bool)
                                      or not isinstance(v, (int, float))):
                    return _err("invalid_request",
                                "spend record cost fields must be numbers")
        for e in entries:
            action = e.get("prompt") or "unknown"
            b = per_action.setdefault(action, {
                "action": action, "calls": 0, "estimated_usd": 0.0,
                "actual_usd": 0.0, "accrued_usd": 0.0, "transport": e.get("transport")})
            b["calls"] += 1
            for k, field in field_of.items():
                v = float(e.get(field) or 0.0)
                b[k] += v
                totals[k] += v
        kill = None
        if status is not None:
            kill = {"cap_usd": status.get("cap_usd"),
                    "spent_usd": status.get("spent_usd"),
                    "accrued_usd": status.get("accrued_usd"),
                    "cap_reached": status.get("cap_reached"),
                    "transport": status.get("transport"),
                    "model": status.get("model")}
        # the store's file NAME only: an absolute path is the server's
        # directory layout
        return {"source": f"served spend ledger ({Path(spend_path).name})",
                "entries": len(entries),
                "per_action": sorted(per_action.values(),
                                     key=lambda b: b["action"]),
                "totals": {k: round(v, 6) for k, v in totals.items()},
                "kill_switch": kill}

    # ── WX ──────────────────────────────────────────────────────────
    @app.get("/desks/wx/{event_id}")
    async def desks_wx(event_id: str):
        # The SAME lifecycle-folded reader /events uses (later lines for an
        # occurrence replace earlier ones, expiry resolves), plus the derived
        # citation and display_mode: never the first raw line with its stored mode.
        event = None
        try:
            for rec in read_events():
                if isinstance(rec, dict) and rec.get("id") == event_id:
                    event = event_view(rec)
                    break
        except Exception:
            event = None
        if event is None:
            return _err("unknown_event", f"unknown event id: {event_id!r}")
        graph_doc = _load_graph_doc()
        if graph_doc is None:
            return _err("graph_unreadable", "graph artifact cannot be read")
        by_id = {n["id"]: n for n in graph_doc.get("nodes", [])}
        primary_id = event.get("node_id")
        primary = by_id.get(primary_id)
        connected_ids = sorted({
            e["to"] for e in graph_doc.get("edges", [])
            if e.get("from") == primary_id and "to" in e} | {
            e["from"] for e in graph_doc.get("edges", [])
            if e.get("to") == primary_id and "from" in e})
        return {
            "event": event,
            "mapping": {
                "method": ("primary place is the feed item's own served "
                           "place mapping; connected places are the "
                           "places joined to it by a served leg"),
                "primary_place": ({"place_id": primary_id,
                                   "name": primary.get("name"),
                                   "kind": primary.get("kind")}
                                  if primary else None),
                "connected_places": [
                    {"place_id": pid, "name": by_id[pid].get("name"),
                     "kind": by_id[pid].get("kind")}
                    for pid in connected_ids if pid in by_id],
            },
            "seed": ({"source": "scenario",
                      "method": ("duration_d 7.0 is a fixed default "
                                 "with no event basis; capacity_loss "
                                 "is unset"),
                      "scenario": {"disruption": {
                          "node_id": primary_id, "kind": "weather",
                          "capacity_loss": None,
                          "duration_d": 7.0, "shape": "step",
                          "started_at": event.get("started_at")}}}
                     if primary_id else None),
        }

    # ── EVAL ────────────────────────────────────────────────────────
    @app.get("/desks/eval")
    async def desks_eval():
        from eval.harness import AWAITING, load_labelled_set, set_summary
        if not eval_set_path.exists():
            return {"status": AWAITING, "labelled_set": None, "grades": None}
        labelled = load_labelled_set(eval_set_path)
        transport = None
        for modname in ("llm.provider", "server.llm.provider"):
            try:
                import importlib
                mod = importlib.import_module(modname)
                transport = mod.LLMProvider().transport_name
                break
            except Exception:
                continue
        return {"status": AWAITING if transport != "http" else "READY",
                "transport": transport,
                "labelled_set": set_summary(labelled),
                "grades": None}
