# SupermAP web app

React 18, Vite 5 and TypeScript front end for SupermAP. It shows places and legs on a map and lets a user open
windows (causal chain, replay, scenario builder, Monte Carlo, exposure, port twin, satellite desk, research,
brief, Meta AI) on top of the API served by `server/`.

The app bundles the map package in `../map/src`, so install the dependencies of both folders.

## Run

```bash
# API from the repository root (a scratch copy of data/ keeps data/ clean)
SUPERMAP_DATA_DIR=<copy of data/> python scripts/serve_api.py 8195

# development server with hot reload on :5173
cd web/map && npm ci && cd ../app && npm ci
SUPERMAP_API_ORIGIN=http://localhost:8195 npm run dev

# production build (written to dist/, served by the API with --web)
npm run build
```

`vite.config.ts` proxies every API route prefix to `SUPERMAP_API_ORIGIN` (default `http://localhost:8000`);
`VITE_API_BASE` calls an API directly instead (see `.env.example`). No key is ever sent to the browser. When the
API is unreachable the app starts on bundled copies of the network and a recorded replay and labels them as
offline copies.

`./run.sh` at the repository root does all of this in one step.

## Notes

- On-screen words are places, legs and causal chain. JSON field names such as `node_id` and `edges` are API
  names and appear only in code and payloads.
- Every figure carries a label of its origin: Simulated, Modelled, Recorded, Live or Snapshot.
- Source credits come from `data/sources/credits.json`, shared with the map and the server.
