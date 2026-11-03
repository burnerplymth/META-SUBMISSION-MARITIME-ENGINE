# SupermAP

SupermAP is a supply-chain "domino effects" terminal. A map and a set of windows show how a disruption at one place
(a port, a canal, an air hub, a ground crossing) spreads along legs to other places: which place is hit next,
through which leg, by which channel, and with what delay. A planner or analyst picks a place and a disruption (a
closure, a strike, a capacity cut, congestion) and gets a causal chain that can be inspected step by step.

The engine is deterministic: the same inputs always produce the same chain, with a stream hash on every run. A
Monte Carlo layer shows the spread of outcomes around that chain. Models from the Meta Model API (Muse Spark, SAM 3.1) are used when a key is configured; without one the app runs on
recorded data and recorded model replies, and says so.

## Features

- Map with places, legs and the causal chain of a run; chain, timeline and detail windows; provenance on every figure.
- Scenario builder and scenario timeline: several shocks on one timeline, handed on to Monte Carlo and My shipments.
- Monte Carlo spread, exposure of a lane or shipment, watchlists with alert rules, lane planner.
- Port twin (simulated port geometry and service) and a vessel and aircraft simulator.
- Replay of recorded disruptions (Ever Given, Los Angeles and Long Beach, Baltimore and others) against the model.
- Satellite desk: scenes, detections and a written reading of a crop (Meta Model API, when switched on).
- Research workbench over a recorded historical archive, with a report tab.
- Disruption brief, chat threads and the Meta AI window (a tool-using agent whose actions wait for confirmation).
- Export centre: CSV, JSON, XLSX, HTML and PDF run reports, each with its source credits.
- A guided tour: press Tour in the top bar, or `Ctrl K` and type `tour quick`.

## Stack

- Server: Python, FastAPI, uvicorn, httpx, Pillow (`server/`, pinned in `server/api/requirements-runtime.txt`).
- Web app: React 18, Vite, TypeScript, dockview windows (`web/app/`); map: MapLibre GL and deck.gl (`web/map/`).
- Data: the place and leg network, recorded feed snapshots and replay episodes (`data/`); model prompts (`prompts/`).

## Quick start

Requires Python 3.10 or newer and Node 20 or newer, or Docker. No key and no network are needed after the install.

```bash
./run.sh                     # creates .venv, builds the web app, seeds data, serves http://127.0.0.1:8143
PORT=9000 ./run.sh           # another port
./run.sh --live              # feeds may use the network; the model is live only if configured (see below)
docker compose up --build    # the same app in a container on http://localhost:8080
```

The first `./run.sh` takes a few minutes (pip, npm, one web build); later runs start in seconds.

## Deploy on Render

`render.yaml` is a Render Blueprint for one Docker web service built from the `Dockerfile`.

1. In the Render dashboard choose New, Blueprint, and select this repository.
2. The service type is Docker. The app needs at least 2 GB of RAM and 4 GB is recommended: set `plan` in
   `render.yaml` (or the instance type in the dashboard) accordingly. A 512 MB instance is too small.
3. Add `META_API_KEY` in the Render dashboard. The Blueprint selects the live transport and lets visitors use it without a token; spend is bounded by the daily cap and the per-session and per-visitor limits in `render.yaml`. To restrict it, set `SUPERMAP_DEMO_TOKEN` (16 or more characters) and remove `SUPERMAP_ALLOW_OPEN_LIVE`.
4. The filesystem is ephemeral; a redeploy resets stored runs. Keep one instance, as the run store and rate
   limiters live in the process. The health check is `/health`.

## Configuration

| Variable | Meaning |
|---|---|
| `SUPERMAP_LLM_TRANSPORT` | `fixture` (default): recorded model replies. `http`: calls the Meta Model API at `https://api.meta.ai/v1`. |
| `META_API_KEY` | Optional. Read only when the transport is `http`. Set it in the environment or the Render dashboard, never in a file. |
| `SUPERMAP_DEMO_TOKEN` | 16 or more random characters. Required with a live transport, otherwise the app refuses to start; visitors without it cannot spend. `SUPERMAP_ALLOW_OPEN_LIVE=1` runs open on purpose. |
| `SUPERMAP_LLM_DAILY_CAP_USD` | Daily spend cap for live model calls (default 2). |
| `SUPERMAP_BLOCK_NETWORK` | `1` (default in `./run.sh` and the image): no feed touches the network and each serves its recorded snapshot. |

Offline mode removes every API key from the process, forces the recorded transport and labels each non-live source
Snapshot, Down or Not served. A recorded answer is labelled Recorded everywhere and is never shown as live.

## What the labels mean

| Label | Meaning |
|---|---|
| Simulated | Produced by the simulator or the port twin from scripted vessels, aircraft and rules. |
| Modelled | Produced by the engine or an allocation rule, not observed. |
| Recorded | A saved answer or episode, replayed as stored. |
| Live | Fetched from a feed or the model at the time of use. |
| Snapshot | The last stored copy of a feed, with its date. |

The network has 992 places and 4,550 legs. 1,011 legs take their share from IMF PortWatch; the other 3,539 (78%) are
modelled allocations. "Calibrated" applies only to the sourced parts: engine constants that carry a source or an
explicit assumption label, core-place capacities, and the PortWatch-derived leg shares.

## Scope

SupermAP is a what-if and tracing tool for logistics planners, risk analysts and researchers who need an auditable
causal trace rather than a forecast. A chain shows how a stated disruption would propagate under the model's rules;
it is not a forecast of what will happen. SupermAP makes no accuracy or forecasting claim: its held-out replay benchmark
(pre-registered in `data/eval/val01/preregistration.md`, results beside it) found no engine configuration
distinguishable from trivial baselines. The interface
speaks of places, legs and causal chains; the API field names `nodes` and `edges` are legacy.

## Licence

Copyright (C) 2026.

SupermAP's own source code is licensed under the GNU Affero General Public License, version 3 (see [`LICENSE`](LICENSE)). If you run a modified version as a network service, the AGPL requires you to offer that version's source code to its users.

Third-party material in this repository is not relicensed and keeps its own terms: data sets, fonts, icons, the map and satellite data credited below, and the SAM mask codec in `server/satwatch/sam31.py` (see [`NOTICE.md`](NOTICE.md)). Those terms are in [`THIRD-PARTY.md`](THIRD-PARTY.md), `NOTICE.md` and `licenses/`. Some of them, such as the OpenSky research and non-commercial terms and the ODbL on the port geometry, restrict further use of that material.

## Data and licences

Third-party data, software, fonts and models, with their licences and unresolved terms, are listed in
[`THIRD-PARTY.md`](THIRD-PARTY.md). [`NOTICE.md`](NOTICE.md) covers the SAM mask codec and
[`licenses/`](licenses/) holds licence texts and the npm licence inventory. Every source shown in the product is in
`data/sources/credits.json`. Attributions required by the main sources:

- Map data (c) OpenStreetMap contributors (ODbL 1.0); basemap styles (c) CARTO. Port geometry for 34 ports is derived
  from OpenStreetMap and is licensed ODbL (`data/ports_geo/LICENSE-ODbL.txt`).
- Satellite basemap (imagery context). Static: "EOxCloudless https://cloudless.eox.at by EOX IT Services GmbH
  (Contains modified Copernicus Sentinel data 2016 & 2017)", CC BY 4.0 (2016 layer only). Daily: "We acknowledge the use
  of imagery provided by services from NASA's Global Imagery Browse Services (GIBS), part of NASA's Earth Science Data
  and Information System (ESDIS)."
- Contains modified Copernicus Sentinel data (ESA). Landsat courtesy of the U.S. Geological Survey; NAIP courtesy of
  USDA Farm Service Agency. Aerial scenes: PDOK Luchtfoto 2025, Beeldmateriaal Nederland (CC BY 4.0); PNOA
  orthophotography, Instituto Geografico Nacional de Espana (CC BY 4.0). Vessel track sample: US Coast Guard AIS via
  MarineCadastre.gov.
- Port activity and chokepoint transits: Source: International Monetary Fund, PortWatch, https://portwatch.imf.org.
  Transformed: 30-day windows, shares and capacity weights (IMF terms; see `THIRD-PARTY.md`).
- Weather alerts: NOAA/NWS. Hazard events: GDACS (European Commission JRC, UN). Energy data: U.S. EIA. Trade data: UN
  Comtrade (derived shares; not the original records). News metadata: The GDELT Project. Aircraft data: the OpenSky
  Network (Schafer et al., IPSN 2014), research and non-commercial terms. Vessel data: AISStream. Weather:
  Open-Meteo.com (CC BY 4.0). Coastlines: Natural Earth (public domain).
- Container-port time statistics: UNCTAD (CC BY 3.0 IGO). Port ranking: World Bank and S&P Global Market
  Intelligence, The Container Port Performance Index 2023, CC BY 3.0 IGO.
- Sea-lane geometry derived from MARNET as shipped in searoute-py (Apache-2.0), which credits Eurostat SeaRoute
  (EUPL-1.2).
- Fonts: IBM Plex Sans and Mono and Orbitron, SIL Open Font License 1.1. Icons: Lucide (ISC) with Feather-derived
  icons (MIT).
- Models: Meta AI (Muse family and SAM 3.1) through the Meta Model API.
