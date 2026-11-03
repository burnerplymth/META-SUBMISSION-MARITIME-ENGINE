"""Ops launcher: serve the API immediately; ingestion runs in the
background.

Uvicorn starts at once and the ingestion cycle runs in
a background daemon thread: single-flight (a cycle in progress is
never overlapped — the next tick is skipped while one runs),
recurring on an interval (SUPERMAP_INGEST_INTERVAL_S, default
900 s), and every failure is logged and swallowed — a failed cycle
never blocks or kills serving; the health board simply stays
truthful about the adapters' last observed state.

Isolation: the cycle runs on a DEDICATED
single-worker executor (``supermap-ingestion``), never on a request
thread, an anyio threadpool thread, or the event loop; and the cycle
itself is deadline-bounded — adapters/run_once fetches concurrently
under SUPERMAP_ADAPTER_TIMEOUT_S (default 90 s) per adapter, so a
blocked-egress cycle is bounded by its slowest adapter deadline
plus routing instead of by the SUM of every adapter's 30–120 s
socket timeouts × retries × paged queries. Read routes therefore never queue
behind a full sequential cycle.

Order matters: create_app() repopulates
the adapter registry with fresh instances, so cycles must run AFTER
app creation and under the `adapters.*` module identity
(cwd/PYTHONPATH=server/) for GET /health to show truthful
LIVE/SNAPSHOT states instead of never-run DOWN.
Single-origin web UI (public demo mode): ``--web web/app/dist`` (or env
``SUPERMAP_WEB_DIST``) layers the built web app over the API with
``api.webui.WebUI`` so one port serves both; the API is untouched (see that
module's docstring). ``--host`` / ``SUPERMAP_HOST`` picks the bind address
(default 127.0.0.1; a container binds 0.0.0.0).

Usage: .venv/bin/python scripts/serve_api.py [port] [--host H] [--web DIST]"""
import concurrent.futures
import logging
import os
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
import uvicorn
from api.app import create_app

log = logging.getLogger("supermap.serve_api")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(name)s %(levelname)s %(message)s")

from api import logscrub
logscrub.install()      # the access log must never hold the demo token (/demo/unlock?token=...)

# Event-loop responsiveness: request work runs on worker threads that share the interpreter lock with
# the loop thread. The default 5 ms switch interval lets one CPU-bound worker hold the lock for 5 ms before the loop
# thread (accepting, parsing, writing every response) gets a turn; a shorter interval bounds that wait.
try:
    sys.setswitchinterval(float(os.environ.get("SUPERMAP_SWITCH_INTERVAL_MS", "5")) / 1000.0)
except ValueError:
    pass

app = create_app()

_cycle_lock = threading.Lock()
_ingestion_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="supermap-ingestion")


def _run_one_cycle() -> bool:
    """Run one ingestion cycle, single-flight, on the dedicated
    ingestion executor. Returns False (and logs at debug level) when
    a cycle is already in progress. The caller (the scheduler thread)
    waits for completion so ticks stay serialised; request handling
    never runs on this executor and this executor never runs request
    work."""
    if not _cycle_lock.acquire(blocking=False):
        log.debug("ingestion cycle skipped: previous cycle still running")
        return False
    try:
        def _cycle() -> dict:
            from adapters.run_once import run_cycle
            return run_cycle()

        s = _ingestion_executor.submit(_cycle).result()
        print("ingestion cycle health:",
              {h["adapter"]: h["state"] for h in s["health"]}, flush=True)
    except Exception as exc:  # a failed cycle never blocks serving
        log.exception("ingestion cycle failed: %s", exc)
    finally:
        _cycle_lock.release()
    return True


def _ingestion_loop(stop: threading.Event) -> None:
    """Background loop: first cycle immediately, then every interval."""
    try:
        interval = float(os.environ.get("SUPERMAP_INGEST_INTERVAL_S", "900"))
    except ValueError:
        interval = 900.0
    interval = max(10.0, interval)
    while not stop.is_set():
        _run_one_cycle()
        stop.wait(interval)


def start_background_ingestion() -> threading.Thread:
    """Start the single-flight recurring ingestion scheduler (daemon,
    so it never delays interpreter exit or uvicorn shutdown). The
    scheduler only submits cycles to the dedicated executor above."""
    stop = threading.Event()
    thread = threading.Thread(target=_ingestion_loop, args=(stop,),
                              name="supermap-ingestion-scheduler",
                              daemon=True)
    thread.stop_event = stop  # type: ignore[attr-defined]
    thread.start()
    return thread


def _parse_args(argv: list[str]):
    import argparse
    ap = argparse.ArgumentParser(description="SupermAP API launcher")
    ap.add_argument("port", nargs="?", type=int,
                    default=int(os.environ.get("PORT")
                                or os.environ.get("SUPERMAP_PORT") or 8143))
    ap.add_argument("--host", default=os.environ.get("SUPERMAP_HOST",
                                                      "127.0.0.1"))
    ap.add_argument("--web", default=os.environ.get("SUPERMAP_WEB_DIST") or None,
                    help="serve this built web app (web/app/dist) on the "
                         "same port")
    ap.add_argument("--no-ingest", action="store_true",
                    default=os.environ.get("SUPERMAP_INGEST", "").lower()
                    in ("0", "off", "false", "no"),
                    help="do not start the background ingestion loop")
    return ap.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args(sys.argv[1:])
    asgi = app
    if args.web:
        from api.webui import WebUI
        asgi = WebUI(app, args.web)
        print(f"web UI: {Path(args.web).resolve()} served on the API port",
              flush=True)
    if os.environ.get("SUPERMAP_COMPRESS", "1").strip().lower() not in ("0", "off", "false", "no"):
        from api.compress import Compress
        asgi = Compress(asgi)       # gzip + ETag for JSON and text answers (see api/compress.py); SUPERMAP_COMPRESS=0 turns it off
    if not args.no_ingest:
        start_background_ingestion()
    def _env_int(name: str, default: int | None) -> int | None:
        try:
            return int(os.environ[name])
        except (KeyError, ValueError):
            return default

    # One process only: the run store, caches and rate limiters live in memory. An explicit workers=1 also stops
    # uvicorn from reading WEB_CONCURRENCY, which hosts such as Render set automatically.
    uvicorn.run(asgi, host=args.host, port=args.port, workers=1,
                # Connection handling. A visitor's browser holds up to six keep-alive connections; the
                # uvicorn default of 5 s closes them between polls, which costs a reconnect (and a TLS handshake behind a
                # proxy) each time. SUPERMAP_LIMIT_CONCURRENCY (unset = no limit) makes the server answer 503 beyond
                # that many simultaneous requests instead of queueing work on a small host.
                timeout_keep_alive=_env_int("SUPERMAP_KEEPALIVE_S", 20) or 20,
                limit_concurrency=_env_int("SUPERMAP_LIMIT_CONCURRENCY", None),
                proxy_headers=True,
                forwarded_allow_ips=os.environ.get(
                    "SUPERMAP_FORWARDED_ALLOW_IPS", "127.0.0.1"))
