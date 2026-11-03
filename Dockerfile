# syntax=docker/dockerfile:1
# SupermAP: one image, one port. Stage 1 builds the web app; stage 2 is the Python runtime that serves the
# API and the built app on the same origin (scripts/serve_api.py --web). No secret is baked in: the Meta key
# is injected at run time. Default behaviour is offline / recorded.
#
#   docker build -t supermap .
#   docker run --rm -p 8080:8080 supermap            # http://localhost:8080
#
# The runtime stage has no node and no Chromium (about 400 MB more image and 300 MB more memory):
# the server-rendered PDF and PNG charts answer 501 renderer_unavailable; everything else exports, including the
# vector run-report PDFs, which the browser writes itself. Plan for at least 2 GB of RAM (4 GB recommended).
#
# Base images are overridable (e.g. a registry mirror): --build-arg PYTHON_IMAGE=... --build-arg NODE_IMAGE=...
ARG NODE_IMAGE=node:22-slim
ARG PYTHON_IMAGE=python:3.12-slim

# ---- stage 1: web build ----------------------------------------------------------------------------------
FROM ${NODE_IMAGE} AS web
WORKDIR /src
# web/app bundles ../map/src and the network copy data/graph/graph.json: keep that relative layout.
COPY web/map/package.json web/map/package-lock.json web/map/
COPY web/app/package.json web/app/package-lock.json web/app/
RUN cd web/map && npm ci --no-audit --no-fund \
 && cd ../app && npm ci --no-audit --no-fund
COPY web/map web/map
COPY web/app web/app
COPY data/graph/graph.json data/graph/graph.json
# the source-credit registry is shared by the app, the map and the server
COPY data/sources data/sources
# The image only bundles the web app (no type-check step).
RUN cd web/app && npx vite build

# ---- stage 2: runtime ------------------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN useradd --create-home --uid 10001 supermap
WORKDIR /app
COPY server/api/requirements-runtime.txt /tmp/requirements-runtime.txt
RUN pip install -r /tmp/requirements-runtime.txt && rm /tmp/requirements-runtime.txt
COPY --chown=supermap:supermap server ./server
COPY --chown=supermap:supermap scripts ./scripts
COPY --chown=supermap:supermap prompts ./prompts
COPY --chown=supermap:supermap data ./data
COPY --from=web --chown=supermap:supermap /src/web/app/dist ./web/app/dist
USER supermap
# Stored runs the app reads (gitignored in the repo, so rebuilt here, deterministically, no network).
RUN mkdir -p data/runs data/sim_runs && python scripts/seed_runtime.py > /tmp/seed.log 2>&1 && tail -n 3 /tmp/seed.log

# Defaults: offline and recorded. A deploy flips them with run-time env only.
ENV SUPERMAP_HOST=0.0.0.0 \
    PORT=8080 \
    SUPERMAP_WEB_DIST=/app/web/app/dist \
    SUPERMAP_DATA_DIR=/app/data \
    SUPERMAP_BACKUP=0 \
    SUPERMAP_BLOCK_NETWORK=1 \
    SUPERMAP_LLM_TRANSPORT=fixture
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import os,urllib.request as u; u.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT','8080'), timeout=4)"
CMD ["python", "scripts/serve_api.py"]
