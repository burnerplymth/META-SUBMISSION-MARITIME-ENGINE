#!/usr/bin/env bash
# SupermAP: one command from a fresh clone to a running demo.
#
#   ./run.sh                 offline / recorded demo (default, safe)
#   ./run.sh --live          let the feeds and the model use the network and your keys
#   PORT=9000 ./run.sh       another port (default 8143)
#   HOST=0.0.0.0 ./run.sh    listen on all interfaces (default 127.0.0.1)
#
# What it does, idempotently (a second run skips whatever is already done):
#   1. .venv  (python3 >= 3.10) with the pinned runtime packages
#   2. web/app/dist  (npm ci + vite build; skipped when dist exists or SUPERMAP_WEB_DIST is set)
#   3. .demo/data    a scratch copy of data/ so the tracked files stay untouched, plus seeded stored runs
#   (delete .demo/ to reset the scratch data copy)
#   4. one process, one port: the API and the built web app on the same origin
#
# Offline / recorded mode (default): no feed touches the network (each serves its recorded snapshot,
# labelled SNAPSHOT or NOT SERVED in the app), every API key in your shell is ignored, and the model
# runs on the recorded fixture transport (labelled FIXTURE / RECORDED, never shown as live).
# Nothing in this mode can spend a Meta credit.
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$PWD"

MODE=offline
for a in "$@"; do
  case "$a" in
    --live) MODE=live ;;
    --offline) MODE=offline ;;
    -h|--help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $a (try --help)" >&2; exit 2 ;;
  esac
done
PORT="${PORT:-8143}"; HOST="${HOST:-127.0.0.1}"
DEMO="$ROOT/.demo"; DATA="$DEMO/data"; VENV="$ROOT/.venv"
say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
die() { printf 'run.sh: %s\n' "$*" >&2; exit 1; }

# ---- 1. python venv -------------------------------------------------------------------------
PY=""
for c in python3.12 python3.13 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then PY="$c"; break; fi
done
[ -n "$PY" ] || die "python 3.10 or newer is required (or use: docker compose up --build)"
REQ=server/api/requirements-runtime.txt
if [ ! -x "$VENV/bin/python" ]; then say "creating .venv with $($PY -V)"; "$PY" -m venv "$VENV"; fi
# An existing venv that already imports the runtime packages is used as it is and never modified;
# otherwise install the pinned runtime set.
if ! "$VENV/bin/python" -c 'import fastapi, uvicorn, httpx, PIL' 2>/dev/null; then
  say "installing runtime packages (first run only)"
  "$VENV/bin/python" -m pip install -q --disable-pip-version-check -r "$REQ" || \
    die "pip install failed (no network? use Docker instead: docker compose up --build)"
fi

# ---- 2. web app -----------------------------------------------------------------------------
DIST="${SUPERMAP_WEB_DIST:-$ROOT/web/app/dist}"
if [ ! -f "$DIST/index.html" ]; then
  command -v npm >/dev/null 2>&1 || die "web/app/dist is missing and npm is not installed (Node 20+). Install Node, or use: docker compose up --build"
  say "building the web app (first run only, about a minute)"
  ( cd web/map && npm ci --no-audit --no-fund --loglevel=error )
  ( cd web/app && npm ci --no-audit --no-fund --loglevel=error && npm run build --silent )
  [ -f "$DIST/index.html" ] || die "web build did not produce $DIST/index.html"
fi

# ---- 3. scratch data + stored runs ----------------------------------------------------------
if [ ! -f "$DATA/.seeded" ]; then
  say "preparing .demo/data (scratch copy of data/, tracked files stay untouched)"
  rm -rf "$DATA"; mkdir -p "$DATA"
  # sim_runs and runs are gitignored runtime state (sim_runs can be gigabytes): do not copy them.
  tar -C data --exclude=./sim_runs --exclude=./runs -cf - . | tar -C "$DATA" -xf -
  mkdir -p "$DATA/runs" "$DATA/sim_runs"
  "$VENV/bin/python" scripts/seed_runtime.py --data-dir "$DATA" > "$DEMO/seed.log" 2>&1 \
    || die "seeding failed, see .demo/seed.log"
  touch "$DATA/.seeded"
fi

# ---- 4. environment -------------------------------------------------------------------------
export SUPERMAP_DATA_DIR="$DATA" SUPERMAP_BACKUP=0 SUPERMAP_WEB_DIST="$DIST" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
if [ "$MODE" = offline ]; then
  # Strip every credential and force the recorded paths: nothing here reaches the network or Meta.
  unset AISSTREAM_API_KEY EIA_API_KEY UN_COMTRADE_API_KEY FRED_API_KEY OPENSKY_CLIENT_ID OPENSKY_CLIENT_SECRET \
        SUPERMAP_LLM_API_KEY SUPERMAP_LLM_ENDPOINT SUPERMAP_LLM_MODEL META_API_KEY META_MODEL_API_KEY \
        SUPERMAP_META_API_KEY SATWATCH_SAM_WORKER_URL SATWATCH_SAM_WORKER_TOKEN SATWATCH_DETECTOR \
        SUPERMAP_VOICE_API_KEY SUPERMAP_VOICE_ENDPOINT SUPERMAP_ADMIN_TOKEN
  export SUPERMAP_BLOCK_NETWORK=1 SUPERMAP_LLM_TRANSPORT=fixture SUPERMAP_VOICE_TRANSPORT=fixture
  LABEL="OFFLINE / RECORDED (no network, no model key; every non-live source is labelled in the app)"
else
  export SUPERMAP_LLM_DAILY_CAP_USD="${SUPERMAP_LLM_DAILY_CAP_USD:-2}"
  LABEL="LIVE (feeds may use the network; the model is live only if SUPERMAP_LLM_TRANSPORT=http and a key are set; daily cap \$${SUPERMAP_LLM_DAILY_CAP_USD})"
fi

if (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; then
  die "port $PORT is in use (try: PORT=$((PORT+1)) ./run.sh)"
fi

URL="http://$([ "$HOST" = 0.0.0.0 ] && echo localhost || echo "$HOST"):$PORT"
mkdir -p "$DEMO"
say "mode: $LABEL"

say "open  $URL   (Ctrl-C to stop)"
exec "$VENV/bin/python" scripts/serve_api.py "$PORT" --host "$HOST" --web "$DIST"
