# Third-party notices

SupermAP vendors third-party material as source files (icons, fonts, data
extracts, recorded API replies) and depends on npm and PyPI packages that are
installed at build time. This file lists each, with its licence and the
attribution text where one is required. Section "Unresolved terms" lists what
has not been confirmed. This is an inventory, not legal advice.

## Lucide icons (web/app/src/ui/vendor/lucide)

A subset of the Lucide icon set (lucide-static 1.61.0), vendored as SVG files
and compiled into `web/app/src/ui/iconData.ts` by `scripts/build-icons.mjs`.
Source: https://github.com/lucide-icons/lucide . The allow-list of icons is in
that script. Lucide is licensed under the ISC licence, with an MIT notice for
the icons derived from the Feather project. The licence text, verbatim from the
upstream `LICENSE` file, follows (also kept beside the icons in
`web/app/src/ui/vendor/lucide/LICENSE`).

```text
ISC License

Copyright (c) 2026 Lucide Icons and Contributors

Permission to use, copy, modify, and/or distribute this software for any
purpose with or without fee is hereby granted, provided that the above
copyright notice and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.

---


The following Lucide icons are derived from the Feather project:

airplay, alert-circle, alert-octagon, alert-triangle, aperture, arrow-down-circle, arrow-down-left, arrow-down-right, arrow-down, arrow-left-circle, arrow-left, arrow-right-circle, arrow-right, arrow-up-circle, arrow-up-left, arrow-up-right, arrow-up, at-sign, calendar, cast, check, chevron-down, chevron-left, chevron-right, chevron-up, chevrons-down, chevrons-left, chevrons-right, chevrons-up, circle, clipboard, clock, code, columns, command, compass, corner-down-left, corner-down-right, corner-left-down, corner-left-up, corner-right-down, corner-right-up, corner-up-left, corner-up-right, crosshair, database, divide-circle, divide-square, dollar-sign, download, external-link, feather, frown, hash, headphones, help-circle, info, italic, key, layout, life-buoy, link-2, link, loader, lock, log-in, log-out, maximize, meh, minimize, minimize-2, minus-circle, minus-square, minus, monitor, moon, more-horizontal, more-vertical, move, music, navigation-2, navigation, octagon, pause-circle, percent, plus-circle, plus-square, plus, power, radio, rss, search, server, share, shopping-bag, sidebar, smartphone, smile, square, table-2, tablet, target, terminal, trash-2, trash, triangle, tv, type, upload, x-circle, x-octagon, x-square, x, zoom-in, zoom-out

The MIT License (MIT) (for the icons listed above)


Copyright (c) 2013-present Cole Bemis

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Fonts (web/app/src/assets/fonts, web/map/src/assets/fonts)

Self-hosted, no CDN, latin subsets from Fontsource (`@fontsource/ibm-plex-sans`
and `@fontsource/ibm-plex-mono`), each family with its licence text beside the
font files. Inter and JetBrains Mono are not shipped.

| Family | Weights | Licence | Licence text |
|---|---|---|---|
| IBM Plex Sans | 400, 500, 600 | SIL Open Font License 1.1 | `web/app/src/assets/fonts/LICENSE-ibm-plex-sans.txt` (copy in `web/map/src/assets/fonts/`) |
| IBM Plex Mono | 400, 500 | SIL Open Font License 1.1 | `web/app/src/assets/fonts/LICENSE-ibm-plex-mono.txt` (copy in `web/map/src/assets/fonts/`) |
| Orbitron (retro skins only) | as shipped | SIL Open Font License 1.1 | `web/app/src/assets/fonts/orbitron-OFL.txt` |

Copyright (c) IBM Corp. with Reserved Font Name "Plex". The logo, mark and
icons are original or drawn for this project; no Apple, Final Cut Pro, LSEG,
Bloomberg or Palantir assets are used.

# Third-party data and software

The entries below cover the Port Twin data. The inventory after the next
separator covers everything else; each data pack also carries its own
provenance record.

| Component | Used for | Licence / terms | Where |
|---|---|---|---|
| OpenStreetMap data (c) OpenStreetMap contributors, via the OSM API 0.6 `/map` call | Port Twin vector geometry (water, coastline, quays, piers, breakwaters, terminals, yards, cranes, rail, roads, anchorages, fairways, locks) and the port profiles derived from it | ODbL 1.0 (https://opendatacommons.org/licenses/odbl/1-0/). Attribution required; a publicly distributed derived database carries share-alike obligations (caveat, not legal advice). | `data/ports_geo/`, `data/ports_geo/LICENSE-ODbL.txt` |
| Copernicus Sentinel-2 L2A imagery, via Microsoft Planetary Computer STAC / titiler | Toggleable true-colour underlay (10 m) under the port maps | Copernicus Sentinel data are free and open (Regulation (EU) No 1159/2013 and the Copernicus data terms); attribution "Contains modified Copernicus Sentinel data" | `data/ports_geo/imagery/` |
| Rodrigue & Notteboom, *The Geography of Transport Systems* / Port Economics, Management and Policy (stacking densities) | Cited as the source range for the yard m2-per-TEU parameter (figures only, no text or data copied) | Cited reference | `server/ports/geo.py` |
| Pillow | Palette PNG quantisation of the imagery underlay (build time) and SCL cloud screening | HPND (Pillow licence) | `.venv`, already a repo dependency |


## Window manager (dockview, installed from npm)

`dockview-react`, `dockview` and `dockview-core`, all pinned to the exact
version **8.5.1** in `web/app/package.json`, so the
transitive `dockview` and `dockview-core` do not float to the newer 8.6.x.
Source: https://github.com/dockview/dockview . MIT licence, copyright (c) 2021
mathuo; the licence text ships in each package as `LICENCE.md`:

```text
MIT License

Copyright (c) 2021 mathuo

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

Only the MIT core is used. dockview 8.5.1 also references an optional
`dockview-enterprise` package (keyboard navigation, Smart Guides and similar
modules, a separate licence): it is **not installed and not used**. The same
keys (F6, Ctrl+], Ctrl+[) and the float snapping are implemented in
`web/app/src/windows/` on the MIT API. The dockview skin is SupermAP's own
(`web/app/src/windows/dock.css`, tokens only).


---

# Inventory of data, services and dependencies

The sections above are the notices for individual components. This inventory
covers the remainder. It is not legal advice, and it does not claim certainty
it does not have: a licence or term marked **verify** was not confirmed against
the publisher's current page (terms were not available from several
publisher sites). Licence texts for bundled third-party files sit beside them or
are listed here.

Reading guide for the data table: **Bundled** = copies of the data are committed
in this repository; **Live** = fetched by a running server or browser at use
time, nothing committed; **Both** = a committed snapshot and a live path.

## A. Unresolved terms and what is shown

| # | Item | Status | What the product shows |
|---|---|---|---|
| 1 | IMF PortWatch (IMF data terms) | The IMF terms (https://www.imf.org/en/about/copyright-and-terms) allow copying, deriving, publishing and distributing the data with attribution in the form "Source: International Monetary Fund, <database name>, <link to dataset>", a statement of any material transformation next to the citation, and no distortion. Bulk automated download and commercial reuse need the IMF's permission (contact: the IMF copyright office named on the terms page). Bulk copies are committed (`data/snapshots/portwatch*.json`, `data/graph/portwatch_registry_pin.json`, history cache). | Every PortWatch-derived figure, series, chart caption, report footer and export carries "Source: International Monetary Fund, PortWatch, https://portwatch.imf.org" with the note "Transformed: 30-day windows, shares and capacity weights" (source-credit registry `data/sources/credits.json`). |
| 2 | OpenSky Network (research and non-commercial terms) | The API is offered for research and non-commercial use. The public deployment serves the stored snapshot only: the Render blueprint (`render.yaml`) sets `SUPERMAP_SNAPSHOT_ONLY_FEEDS=opensky`, so no OpenSky request (anonymous or keyed) is made from the public link and the continuous collector is not built. A local run with `./run.sh --live` can still fetch; that is the user's own research use. | A small committed snapshot (1.4 KB) labelled Snapshot with its date; no raw state vectors are committed. Credit: "Source: OpenSky Network, https://opensky-network.org". |
| 3 | UN Comtrade (licence agreement) | https://comtrade.un.org/licenseagreement.html prohibits copying, automated downloading, redistribution, publication and commercial exploitation without prior written UN permission. The repository therefore ships **derived aggregates only** (`data/impact/comtrade_aggregates.json`: import shares per routing region; `data/snapshots/comtrade.json`: chapter and partner shares). The record-level files (`data/impact/comtrade_extracts.json`, `data/history_cache/un_comtrade/`) are git-ignored and excluded from the image; `scripts/fetch_comtrade.py` rebuilds them locally with the user's own key. | Shares only, never a USD amount, each with "UN Comtrade" and "Derived from UN Comtrade; not the original records"; never summed into a single "trade at risk" figure. |
| 4 | Drewry World Container Index and Freightos index (commercial products) | Drewry's terms (https://www.drewry.co.uk/terms) do not allow copying, reproducing or republishing content without prior written consent and prohibit commercial exploitation of data; no clause allows quoting index values. `data/impact/rates.json` still marks them `NEEDS-LICENCE` and therefore serves no index value; the SCFI is `NOT-CLEARED`. | A plain link to the publisher's page and the chip "Not served". No index series, no value. |
| 5 | Sea-lane network (MARNET via `searoute`, crediting Eurostat SeaRoute under EUPL-1.2) | Whether the EUPL reaches the derived waypoints is unsettled. `data/graph/sea_routes.json` and `data/graph/lane_geometry.json` are both derived from it. | Lane polylines on the map; the credit is in the map attribution and in the sidecar provenance. |
| 6 | OpenStreetMap-derived port geometry (ODbL, bundled) | Share-alike applies to the derived database in `data/ports_geo/`. | Attribution on every view that draws it; the licence text is `data/ports_geo/LICENSE-ODbL.txt`. |
| 7 | AISStream terms | Terms text not found. Nothing is bundled. | Positions labelled as a terrestrial sample, when a key is configured. |
| 8 | CARTO basemap, MapLibre demo glyph server, Stooq and Wikipedia-compiled lists | Marked **verify** in section D. GDACS: the terms page (https://gdacs.org/About/termofuse.aspx) states that earthquake, tsunami and cyclone notifications are automatic, not reviewed by experts, and not a substitute for official alerts; reuse is governed by the European Commission legal notice (CC BY 4.0 with acknowledgement, per the Commission's reuse notice). | Attribution as listed there. GDACS shows "Source: GDACS (European Commission JRC and UN OCHA)" with "Automatic alerts; not an official warning". |
| 9 | Meta SAM 3.1 mask codec | The decoder in `server/satwatch/sam31.py` is an independent implementation of the published wire format; the SAM licence text was not confirmed. | Outlines are drawn from decoded masks; counts and boxes do not depend on the decoder. |
| 10 | World Bank CPPI 2025 annex (CC BY-NC) | Not included in this repository. | None. A label string naming CPPI/LSCI as an efficiency source remains in the frozen `graph.json`; no CPPI value was used and the UI does not display it. |
| 11 | Esri World Imagery | Not used; the satellite basemap is EOX Sentinel-2 cloudless 2016 (CC BY 4.0) and NASA GIBS. | None. |
| 12 | Filing excerpts (`server/money/exposures.json`) | Verbatim EDGAR passages are stored; some mention conflict or sanctions. | `/money/exposures` and the UI withhold the out-of-scope excerpts (excerpt null plus a reason); the filing citation is kept. |
| 13 | Red Sea replay episode (`data/replay/red_sea_2024.json`, its scorecards, `data/sim_backtest/red_sea/`) | Accepted as a documented shipping-disruption backtest episode with a stated source (UNCTAD). Editing it would change the sha256 recorded in nine published scorecards and break the determinism pins. | The app's content guard withholds it from the default demo path. |
| 14 | IGN France BD ORTHO aerial source (`ign-fr` in the aerial source registry) | IGN has published its public data, including BD ORTHO, under Licence Ouverte / Etalab 2.0 since 1 January 2021; attribution is required ("Source: IGN, BD ORTHO"). This entry follows secondary sources (OpenStreetMap France wiki, Etalab licence text: a link to the source and an effective mention of authorship); IGN's own current wording was not available. No IGN scene or fixture ships. Confirm against the product metadata before shipping a scene. Digitaal Vlaanderen: orthophotos are offered under the Modellicentie gratis hergebruik (free reuse, naming required) and, for the 2025 mosaic, the Vlaamse Open Data licentie v1.2; the catalogue entries differ, so confirm the licence field of the exact dataset and year; recommended credit "Bron: Orthofotomozaiek Vlaanderen [year], Digitaal Vlaanderen". No Belgian scene ships. | The source is listed in the registry with the attribution "Source: IGN, BD ORTHO"; no scene is drawn. |

## A1. Source credits in the product

Every external source the product shows or exports is listed once in `data/sources/credits.json` (display line, licence, link, and whether attribution is required in the product). The Sources list in the top bar, the map attribution control, panel source lines, the provenance drawer, report and chart footers, every export file and every pack manifest are drawn from it; a source cannot appear in the product without an entry, and an entry marked required is always rendered.

## A2. Share-alike and copyleft notices

Exact notices for the two datasets whose licences carry conditions on the data itself. They are independent of the licence the repository code carries.

**OpenStreetMap, ODbL 1.0 (`data/ports_geo/`).** Notice: "Contains information from OpenStreetMap, which is made available under the Open Database License (ODbL) 1.0. (c) OpenStreetMap contributors. https://www.openstreetmap.org/copyright". The directory `data/ports_geo/` (34 port geometries, their profiles and manifest) is a derived database under section 4.4 of the ODbL: it stays under the ODbL 1.0 whatever licence is chosen for the code, with the licence text at `data/ports_geo/LICENSE-ODbL.txt`. Anyone who publicly uses a further derived database must offer it under the ODbL and keep the attribution. The attribution is shown in the map attribution control and on every Port Twin view.

**Sea-lane network, EUPL-1.2 credit (`data/graph/sea_routes.json`, `data/graph/lane_geometry.json`).** Notice: "Sea-lane network derived from MARNET (Eurostat SeaRoute, European Union Public Licence v1.2, https://eupl.eu/1.2/en) as shipped in searoute-py 1.6.0 (Apache-2.0)". Whether the EUPL-1.2 reaches these derived waypoints is unsettled; until it is settled both files keep the credit above, are shipped as a separate data directory with their provenance record, and are not relicensed. If the EUPL applies, the files stay under the EUPL-1.2; its Appendix lists the compatible licences. A clean-room rebuild would change hash-pinned files and is out of scope.

## B. Software dependencies

Full machine-generated list: `licenses/npm-licences.csv` (every package in the
`package-lock.json` files with its declared licence and prod or dev scope).
The Python list below comes from `server/api/requirements.txt`, the imports
under `server/` and `scripts/`, and the licence metadata of the installed
packages.

### B.1 npm (browser app and map package)

| Project | Runtime ("prod") packages | Dev packages | Licences found (prod) |
|---|---|---|---|
| `web/app` | 133 | 108 | MIT 94, ISC 23, BSD-3-Clause 7, BSD-2-Clause 2, Apache-2.0 2, BlueOak-1.0.0 1, 0BSD 1, (MIT AND Zlib) 1, (MPL-2.0 OR Apache-2.0) 1, MIT OR "see licence file" 1 |
| `web/map` | 78 | 112 | MIT 51, ISC 15, BSD-3-Clause 6, BSD-2-Clause 2, Apache-2.0 2, BlueOak-1.0.0 1, 0BSD 1 |

No GPL, LGPL, AGPL, EUPL, SSPL, "non-commercial" or unknown licence is declared by any package in the lock files. One optional package declares a choice that includes MPL-2.0 (`dompurify`, "MPL-2.0 OR Apache-2.0", Apache-2.0 chosen); it is installed by npm for `jspdf` but is not bundled (see the `jspdf` row). The only non-permissive-looking entry is `caniuse-lite` (CC-BY-4.0, dev dependency of the build tool, not shipped in the bundle; attribution to the caniuse project if its data is ever redistributed). Direct dependencies and what they are:

| Package | Pinned in | Licence | Notes |
|---|---|---|---|
| `react`, `react-dom` 18.3.1 | app, map | MIT | |
| `maplibre-gl` 4.7.1 | app, map | BSD-3-Clause | Brings `@mapbox/*` helpers (BSD-2/3), `pbf`, `ieee754`, `rw` (BSD-3). |
| `@deck.gl/core`, `layers`, `extensions`, `mapbox` 9.x | app, map | MIT | Brings `apache-arrow` and `flatbuffers` (Apache-2.0), `@webgpu/types` (BSD-3). |
| `@xyflow/react` 12.x | app | MIT | Brings `d3-ease` (BSD-3-Clause) and `tslib` (0BSD). |
| `jspdf` 4.2.1, `svg2pdf.js` 2.9.0 (pinned exactly) | app | MIT | Run report PDFs (one-click download, vector charts, selectable text), loaded on demand in their own chunk. Bring `fast-png`, `iobuffer`, `fflate`, `@babel/runtime`, `pako` ((MIT AND Zlib)), `svgpath`, `cssesc`, `specificity` and `font-family-papandreou` (all MIT). `jspdf` also lists optional `canvg`, `html2canvas`, `core-js` (MIT) and `dompurify` ((MPL-2.0 OR Apache-2.0), Apache-2.0 chosen), plus their helpers (`rgbcolor` MIT OR "see licence file", `raf`, `performance-now`, `svg-pathdata`, `stackblur-canvas`, `text-segmentation`, `css-line-break`, `utrie`, `base64-arraybuffer`, `regenerator-runtime`): npm installs them, but they serve only `jsPDF.html()` and SVG-as-image, which the reports do not use; `web/app/vite.config.ts` replaces the three entry points with an empty module so none of them is bundled or emitted. |
| `dockview`, `dockview-core`, `dockview-react` 8.5.1 | app | MIT | `dockview-enterprise` is not in the lock file and is not used (verified: zero matches). Details above. |
| `vite`, `typescript`, `@vitejs/plugin-react`, `@types/*` | dev | MIT, Apache-2.0 (TypeScript) | Build only. |
| Lucide icon subset (vendored SVG, not an npm dependency) | app | ISC, plus MIT for Feather-derived icons | Texts above and in `web/app/src/ui/vendor/lucide/LICENSE`. |

Platform binaries pulled in as optional packages (esbuild, rollup, a lzma binding): all MIT in the lock file.

### B.2 Python (server)

| Package | Needed for | Licence (declared metadata) | Ships in repo? | Notes |
|---|---|---|---|---|
| `fastapi` (>=0.110; venv has 0.162.0) | API server | MIT | No (pip) | Brings `starlette` (BSD-3-Clause), `pydantic` and `pydantic_core` (MIT), `typing_extensions` (PSF-2.0), `typing-inspection`, `annotated-types`, `annotated-doc` (MIT). |
| `uvicorn` (>=0.29; 0.54.0) | API server | BSD-3-Clause | No | Brings `click` (BSD-3-Clause), `h11` (MIT). |
| `httpx` (>=0.27; 0.28.1) | outbound feeds | BSD-3-Clause | No | Brings `httpcore` (BSD-3), `anyio` (MIT), `idna` (BSD-3), `certifi` (**MPL-2.0**, file-level copyleft; used unmodified, installed by pip, not copied into the repo). |
| `Pillow` (>=10; 13.0.0) | PNG/SCL screening, palette quantisation | MIT-CMU in the installed 13.0.0 metadata (an HPND-style permissive licence; the earlier note above says HPND) | No | |
| `searoute` (>=1.6.0; 1.6.0) | **generation time only**: `scripts/build_sea_routes.py` | Apache-2.0 (metadata). Credits Eurostat SeaRoute, EUPL-1.2 | Its derived output `data/graph/sea_routes.json` is committed | See section D, row "Sea-lane network". Brings `networkx` (BSD-3), `geojson` (BSD), `opentelemetry-api` (Apache-2.0). The engine never imports it. |
| `openpyxl` | fetch time (`server/realism_data/fetch.py`) | MIT (verify installed version) | No | Not in `requirements.txt`; imported lazily. |
| `py7zr` | fetch time only (`server/realism_data/fetch.py`) | **LGPL-2.1-or-later** (verify) | No | Not in `requirements.txt`; imported lazily by a hand-run fetch script; not distributed or linked into the served app. Flagged because LGPL is the only copyleft-licensed package named anywhere in the code. |

### B.3 Container image (`Dockerfile`)

The repository does not contain an image; `docker build` makes one. The runtime stage starts from `python:3.12-slim` (Debian userland packages under their own licences, including GPL-licensed tools, plus CPython under the PSF licence) and installs the pinned set in `server/api/requirements-runtime.txt` (fastapi 0.162.0, starlette 1.10.0, uvicorn 0.54.0, httpx 0.28.1, Pillow 13.0.0 and their pinned dependencies). The web stage starts from `node:22-slim` and is not part of the final image; the built app (`web/app/dist`) is copied across. Someone who publishes the built image redistributes those base layers and owes their notices; this repository does not publish an image. Inventory only, not legal advice.

## C. Fonts

| Family | Files | Licence | Licence text |
|---|---|---|---|
| IBM Plex Sans (400, 500, 600, latin) | `web/app/src/assets/fonts/`, `web/map/src/assets/fonts/` | SIL OFL 1.1, Copyright IBM Corp., Reserved Font Name "Plex" | `LICENSE-ibm-plex-sans.txt` beside the files |
| IBM Plex Mono (400, 500, latin) | same two folders | SIL OFL 1.1, Copyright IBM Corp., Reserved Font Name "Plex" | `LICENSE-ibm-plex-mono.txt` beside the files |
| IBM Plex Sans 400, 600 and IBM Plex Mono 400 as TrueType (latin) | `web/app/src/assets/fonts/pdf/` | SIL OFL 1.1, as above | The same two licence texts. These are the bundled woff2 files unwrapped to TrueType (glyph outlines and names unchanged, no subsetting beyond the latin set already shipped); jsPDF embeds TrueType only. They are embedded in the run report PDFs. |
| Orbitron (500, 700, 900; retro skins only) | `web/app/src/assets/fonts/` | SIL OFL 1.1, Copyright 2018 The Orbitron Project Authors | `orbitron-OFL.txt` |

Inter and JetBrains Mono are not shipped. The OFL requires the licence text to travel with the font files
and forbids selling the fonts on their own; bundling in an application is
allowed. No font is loaded from a CDN (`fonts.css` is self-hosted).

## D. Data, tiles and services

| Source | What is used | Licence or terms | Attribution text (as required or as used) | Bundled or live | Notes |
|---|---|---|---|---|---|
| **OpenStreetMap** (OSM API 0.6 extracts) | Port Twin geometry for 34 ports: water, quays, piers, cranes, rail, roads, anchorages, fairways, locks, plus profiles derived from it | ODbL 1.0. Attribution required; a publicly distributed **derived database** must be offered under ODbL (share-alike), and a produced work (a map image) needs attribution and a pointer to the licence. Not legal advice. | "© OpenStreetMap contributors" (and link to openstreetmap.org/copyright). Carried in `data/ports_geo/manifest.json`, `LICENSE-ODbL.txt`, map credit line. | Bundled: `data/ports_geo/*.geojson`, `profiles/`, `manifest.json`; also shown through the CARTO basemap (live) | The geometry and the derived profiles are an ODbL derived database. Share-alike means anyone redistributing **that folder** must keep it under ODbL; it does **not** by itself make the application code ODbL, because the code is a separate work that reads the data. Keep the folder clearly labelled; do not put it under a non-commercial licence (ODbL permits commercial use); exclude it from the code licence's scope. |
| **CARTO basemap styles** (`dark-matter-gl-style`, `positron-gl-style`, via `basemaps.cartocdn.com`) | Default light and dark basemap in `web/map/src/theme/basemaps.ts` | CARTO's basemap terms; free use is historically for non-commercial use with attribution, heavier or commercial use needs a CARTO plan (**verify**). Underlying data OSM ODbL. | "© OpenStreetMap contributors · © CARTO" (as in code) | Live (nothing bundled) | **Verify** if the product is ever monetised. If the CDN is unreachable the bundled coastline underlay shows. |
| **EOX Sentinel-2 cloudless, 2016 layer** (`tiles.maps.eox.at`, layer `s2cloudless_3857`) | SATELLITE basemap, STATIC source (imagery context, z0 to z14) | **CC BY 4.0** (attribution only, commercial use allowed) for the 2016 layer, as stated in its WMTS capabilities abstract and in EOX's published terms. EOX's 2018 to 2025 layers are **CC BY-NC-SA 4.0 and are not used**; no such layer id appears in the repository. | "EOxCloudless https://cloudless.eox.at by EOX IT Services GmbH (Contains modified Copernicus Sentinel data 2016 & 2017)" (shown expanded in the map attribution control) | Live, hot-linked tiles; nothing bundled | Keep the attribution text exact and legible. |
| **NASA GIBS** (Global Imagery Browse Services, `gibs.earthdata.nasa.gov`, layer `VIIRS_NOAA21_CorrectedReflectance_TrueColor`) | SATELLITE basemap, LATEST DAILY source (250 m, z0 to z9, an explicit acquisition day, chip `Imagery YYYY-MM-DD`) | NASA Earthdata data policy: full and open sharing, no exclusive access, non-discriminatory. No key. NOAA-21 is used because NASA announced that new Suomi NPP data delivery would cease. | "NASA GIBS / Earthdata" on the map; `README.md` carries NASA's GIBS acknowledgement sentence | Live, hot-linked tiles; nothing bundled |  |
| **MapLibre demo glyphs** (`demotiles.maplibre.org/font`) | Font stacks named by the satellite style | MapLibre demo server; demo use (**verify** glyph font licence, historically Open Sans/Noto under open licences) | none required by the server | Live | Not meant for production traffic. |
| **Natural Earth** (land 110m via npm `world-atlas` 2.0.2, and the 1:50m land layer) | Coastline underlay (`web/map/src/data/land-110m.geo.json`), sim land test (`server/sim/data/ne_land_110m.geojson`), and the lane-geometry land check (`server/lanegeo/data/ne_land_50m.geojson`, about 1 MB) | Public domain | Not required; credited "Natural Earth (public domain)" in the map credit when the underlay shows | Bundled | Provenance note in `web/map/src/data/README-land-underlay.md`. |
| **Copernicus Sentinel-2 L2A** (via Microsoft Planetary Computer STAC and titiler, and Element84 Earth Search) | 10 m port crops as the Port Twin underlay; Satellite desk scenes | Copernicus data are free, full and open (Regulation (EU) No 1159/2013 and the Copernicus data terms). Planetary Computer's own service terms for its API and titiler (**verify**). | "Contains modified Copernicus Sentinel data" plus year and ESA (code: "Contains modified Copernicus Sentinel data, ESA") | Both: 34 crops in `data/ports_geo/imagery/` and about 29 MB of scene PNGs in `data/snapshots/satwatch/`; live fetch in the satwatch module | Keep the attribution on every view that shows the imagery. |
| **Copernicus Sentinel-1 GRD** | SAR scenes and ship-detection demo | as above | as above | Both (`data/snapshots/satwatch/multi/s1_*`) |  |
| **Landsat Collection 2** (USGS) and **NAIP** (USDA) | Satellite desk sources | Public domain | "Landsat imagery courtesy of the U.S. Geological Survey"; "Imagery courtesy of USDA Farm Service Agency (NAIP), via USGS" | Both (`data/snapshots/satwatch/multi/`) |  |
| **Aerial orthophoto services** (aerial source registry in `server/satwatch/sources.py`: PDOK Luchtfoto, Netherlands; PNOA, Spain, Instituto Geografico Nacional de Espana; Digitaal Vlaanderen orthophoto, Belgium; IGN BD ORTHO, France; plus NAIP above) | Aerial desk: one window of a port, fetched on demand by an operator. Three recorded demo scenes ship in `server/satwatch/fixtures/aerial/` (Rotterdam Maasvlakte from PDOK, Valencia from PNOA, Long Beach from NAIP) with a preview JPEG and the recorded SAM replies; no Belgian or French scene ships | PDOK: CC BY 4.0 (service metadata states open data, cc-by). PNOA: CC BY 4.0 (service metadata, scne.es). Digitaal Vlaanderen: its geographic web services terms of use, free of charge (**verify** the licence text before any scene is fetched). IGN France: see section A, item 14. Not legal advice. | Per scene, in the scene record and the chip tooltip: "Luchtfoto 2025, Beeldmateriaal Nederland, via PDOK (CC BY 4.0)"; "PNOA orthophotography, Instituto Geografico Nacional de Espana (IGN), CC BY 4.0"; "Source: IGN, BD ORTHO (production year per scene)" | Both: three preview JPEGs committed (about 180 KB); live fetch only behind the operator token | The visible chip is short ("PDOK · CC BY", "IGN ES · CC BY"); the full credit is in the tooltip and the export. |
| **US Coast Guard Nationwide AIS via MarineCadastre.gov** (NOAA and BOEM) | A thinned sample of vessel tracks for one US port episode (`data/research/tracks.json.gz`, 36 KB, built by `scripts/research_ais_extract.py`); shown in the Research window as a recorded track sample | US government data; MarineCadastre states that the AIS data are in the public domain (**verify** the wording on the product page) | "US Coast Guard Nationwide AIS via MarineCadastre.gov" (also in the provenance block of the file) | Bundled (one day, thinned) |  |
| **Research cache** (`data/research/`: PortWatch series, EIA spot series, Open-Meteo weather series, Sentinel-1 scene metadata, episode event sources) | Recorded input of the Research window; each file carries its source and retrieval time in the manifest. The event list stores a title, date, publisher and link for each event, with no article text | As the individual sources above (PortWatch: unconfirmed licence, see section A, item 1; EIA: public domain; Open-Meteo: CC BY 4.0; Sentinel: free, full and open). Publisher pages are linked, not copied | Source line per event in the Research window; "Weather data by Open-Meteo.com" | Bundled (146 KB in all) | As PortWatch. |
| **IMF PortWatch** (ArcGIS FeatureServer: ports database, daily port activity, chokepoints) | Port registry (2,065 records pinned), vessel-call series, chokepoint transits; basis of `graph.json` capacity shares, calibration windows, live port activity | IMF data terms (https://www.imf.org/en/about/copyright-and-terms): copy, derive, publish and distribute with attribution, state material transformations next to the citation, do not distort; bulk automated download and commercial reuse need permission from the IMF copyright office named on the terms page Not legal advice. | "Source: International Monetary Fund, PortWatch, https://portwatch.imf.org" and "Transformed: 30-day windows, shares and capacity weights", shown beside every PortWatch-derived series in the product, in chart captions, report footers and every export (CSV, JSON, XLSX, HTML, PDF, pack manifest). | Both: `data/snapshots/portwatch*.json`, `data/records.jsonl`, `data/graph/portwatch_*_pin.json` (1.5 MB), `data/history_cache/imf_portwatch`, `data/graph_extra_candidates/inputs/portwatch_*` | Attribution and transformation notes are in place; bulk automated download and commercial reuse need the IMF's permission. |
| **NOAA / National Weather Service** (`api.weather.gov` alerts) | Weather events for ports | US government work, public domain; NWS asks API clients to identify themselves with a User-Agent | "NOAA/NWS" credit is courteous; code labels source as NOAA/NWS | Both (`data/snapshots/noaa.json`, `data/events.jsonl`) |  |
| **GDACS** (Global Disaster Alert and Coordination System; JRC, European Commission, UN) | Hazard events (cyclone, flood, earthquake) | GDACS terms of use (https://gdacs.org/About/termofuse.aspx): earthquake, tsunami and cyclone notifications are automatic, not reviewed by experts, and "not meant to substitute nor to override any official information or alert message". The page defers reuse to the European Commission legal notice; the Commission's reuse notice authorises reuse under CC BY 4.0 with credit (secondary reading, not confirmed on the legal-notice page). Not legal advice. | "Source: GDACS (European Commission JRC and UN OCHA), https://www.gdacs.org" with "Automatic alerts; not an official warning" | Both (`data/snapshots/gdacs.json`, `data/history_cache/gdacs/`) | The not-an-official-alert wording travels with every GDACS event. |
| **GDELT** (DOC 2.0 API) | News article titles and URLs for the news panel | GDELT is described by its publisher as free and open for any use with citation (**verify** current wording). Headlines and linked articles belong to their publishers. | "Source: The GDELT Project (gdeltproject.org)"; link to each article | Both (`data/snapshots/gdelt.json`: 37 titles and URLs) | Titles and links only; headlines on war and political subjects are not in the committed pack. |
| **OpenSky Network** (REST API, OAuth2) | Aircraft positions near air hubs; a live collector | The API is offered for **research and non-commercial purposes**; commercial users are told to contact OpenSky. Publications should cite the OpenSky paper (Schäfer, Strohmeier, Lenders, Martinovic, Wilhelm, IPSN 2014). | "Source: OpenSky Network, https://opensky-network.org" with "Snapshot, not live"; cite the 2014 paper where results are published | Both: tiny snapshot `data/snapshots/opensky.json` (1.4 KB) and a credit counter; raw state vectors are not committed | On a public link `SUPERMAP_SNAPSHOT_ONLY_FEEDS=opensky` (set in `render.yaml`) serves the stored snapshot only, labelled Snapshot with its date, with no anonymous or keyed request. Never commit credentials (none found). |
| **AISStream** (websocket, API key) | Vessel positions (terrestrial AIS sample) | Terms text **not found** (the documentation page states no SLA and no browser connections); **verify** the account terms | "Vessel data from AISStream (aisstream.io)" as a courtesy | Live; snapshot is empty (0 records) | Nothing is bundled; verify the account terms before showing it publicly. |
| **U.S. EIA** (Open Data API v2) | Crude spot prices, days of supply | US government work, public domain; EIA asks for acknowledgement (`eia.gov/about/copyrights_reuse.php`) | "Source: U.S. Energy Information Administration" | Both: `data/snapshots/eia.json`, `data/history_cache/eia/`, `data/graph_sidecar_sources/eia_days_of_supply.json` |  |
| **UN Comtrade** (v1 API) | Derived import shares per reporter, HS group and routing region (impact layer); chapter and partner shares (Live context); volume labels in `graph.json` ("UN Comtrade 2024", frozen text) | UN copyright. Licence agreement (https://comtrade.un.org/licenseagreement.html): copying, automated downloading, redistribution, publication and commercial exploitation are prohibited without prior written UN permission. Derived, substantially transformed data is treated differently by secondary sources, which is the basis for shipping aggregates only. Not legal advice. | "Source: UN Comtrade (comtrade.un.org)" and "Derived from UN Comtrade; not the original records" wherever a Comtrade-derived figure is displayed or exported | Derived only: `data/impact/comtrade_aggregates.json`, `data/snapshots/comtrade.json`. Not shipped: `data/impact/comtrade_extracts.json`, `data/history_cache/un_comtrade/` (git-ignored; rebuilt with `scripts/fetch_comtrade.py` and the user's own key) | Redistribution of the original records needs prior written UN permission; only derived aggregates are shipped. |
| **Open-Meteo** (archive API `archive-api.open-meteo.com/v1/archive`, forecast API `api.open-meteo.com/v1/forecast`, marine API `marine-api.open-meteo.com/v1/marine`) | Historical weather for episode replays (archive); forecast and wave conditions fetched live when a simulator run is created (forecast, marine) | Data **CC BY 4.0**. The free API is for **non-commercial use** (fewer than 10,000 calls a day); commercial use needs a paid plan. (Per the publisher's terms page.) | "Weather data by Open-Meteo.com" (CC BY 4.0) | Archive: `data/history_cache/open_meteo_archive/` (8 KB). Forecast and marine: live only, cached on disk for 6 h; a failed fetch shows weather as unavailable. | The non-commercial limit matters if the product is monetised. |
| **U.S. CBP** border wait times | Ground-crossing wait times | US government work, public domain | "Source: U.S. Customs and Border Protection" | Both (`data/snapshots/cbp.json`, 7 KB) |  |
| **NTAD / BTS** (US DOT, intermodal facilities) | Rail ramp locations for ground places | US government data, public domain (**verify** that the hosted layer carries no extra terms) | "Source: U.S. Department of Transportation, Bureau of Transportation Statistics (NTAD)" | Both (`data/snapshots/ntad.json`, `data/graph/ntad_rail_pin.json`) |  |
| **OurAirports** | Large airports with IATA codes | Public domain (declared by the maintainer) | "OurAirports (ourairports.com)" courtesy | Bundled (`data/graph/ourairports_pin.json`, 404 KB) |  |
| **UN/LOCODE** (UNECE) | Place codes (`locode`) on ports | UNECE publication (**verify** reuse terms; code lists of this kind are widely redistributed) | "UN/LOCODE, UNECE" | Bundled as fields in graph and pins |  |
| **Wikipedia-compiled lists** (busiest airports, ACI cargo rankings, Lloyd's List container-port ranking as compiled on Wikipedia, city coordinates) | Presence and rank checks when choosing which places to include; coordinates for a few corridor cities | Wikipedia text is CC BY-SA 4.0; rankings and coordinates are facts, but a compilation may be protected; the underlying rankings are ACI's and Lloyd's List's (**verify**) | "Wikipedia contributors, CC BY-SA 4.0" plus the original publisher | Bundled: `data/graph/air_ranking_pin.json`, `data/graph_extra_candidates/inputs/` | Only ranks and coordinates are copied (no prose); keep the source URL in each file. |
| **Carrier and trade-press service rotations** (30 named sources: Hapag-Lloyd, Maersk, CMA CGM, Ocean Alliance releases, Container News, Hellenic Shipping News and others) | 129 service rotations (port sequences) in `data/graph/services.json` | Port sequences are facts; the articles are copyrighted. The sidecar stores sequences and a first-25-word excerpt only (its own `licensing` note) | Per entry: publisher, title, URL, date (already in the file) | Bundled | Keep the excerpt limit at 25 words; do not add article text. |
| **World Bank / S&P Global CPPI 2025 annex** (CC BY-NC 3.0 IGO) | Formerly `data/graph/port_service.json` and its raw extract | **Not bundled.** The files and the `GET /graph/port-service` route do not exist; nothing in the product reads them. | n/a | Not bundled | None. |
| **World Bank CPPI 2023 ranking** (names and ranks only, 405 ports) | `data/graph_extra_candidates/inputs/cppi_2023_ranking.json`, used by a coverage check; rank cited in a few candidate notes; a label string in `graph.json` names CPPI as an efficiency source (label only, no CPPI value was used) | **CC BY 3.0 IGO** (attribution only, commercial use allowed). Per the World Bank repository record for handle 10986/41707 (`dc.rights`). A different edition from the removed 2025 annex. | "World Bank and S&P Global Market Intelligence, The Container Port Performance Index 2023, CC BY 3.0 IGO" (also in `README.md`) | Bundled (12 KB) |  |
| **UNCTAD** (UNCTADstat `US.PortCalls`; port LSCI label) | Economy-level median time in port (not attributed to ports) | CC BY 3.0 IGO (per the repo's source record) | "Source: UNCTAD, UNCTADstat" | Bundled: `data/graph_sidecar_sources/unctad_US_PortCalls.csv` (0.23 MB) |  |
| **U.S. Census Bureau** (monthly trade and inventories, MTIS) | Inventory-to-sales ratios | US government work; public-domain status is stated less explicitly than EIA's (the sidecar notes this) | "Source: U.S. Census Bureau" | Bundled: `data/graph_sidecar_sources/census_inventory_ratios.json` |  |
| **IEA oil stocks, Eurostat oil stocks** | Probed, **not used**: the pages were not served or were the wrong measure; the manifest records this under `not_served` | n/a | n/a | Nothing bundled |  |
| **Money layer** (`server/money`): SEC EDGAR, Frankfurter (ECB reference rates), FRED, Stooq | Company exposure text from public filings; FX; quotes (quarantined from the engine) | SEC: public filings, fair-access rules (User-Agent, request rate). Frankfurter: free open API on ECB rates. FRED: needs a key, series have per-series terms (**verify**). Stooq: terms unclear (**verify**). | "Source: SEC EDGAR"; "ECB reference rates via Frankfurter"; "FRED, Federal Reserve Bank of St. Louis"; Stooq as required | Bundled: `server/money/exposures.json` (verbatim filing excerpts) | The Stooq terms are the least clear; the Stooq path is optional. |
| **Sea-lane network** (MARNET, shipped inside the `searoute` PyPI package 1.6.0, Apache-2.0, crediting Eurostat SeaRoute, EUPL-1.2) | Lane polylines and distances for 3,642 sea legs: `data/graph/sea_routes.json` (2.8 MB); the smoothed, land-checked adaptation of the same waypoints, `data/graph/lane_geometry.json` (1.5 MB, 2,072 place pairs), which is what the map draws | The package licence is Apache-2.0; its upstream credit is EUPL-1.2, a copyleft licence. Whether the EUPL reaches the derived waypoints, including `lane_geometry.json`, is **unsettled**. Not legal advice. | The sidecar's `provenance.network` carries the credit; "Sea-lane network derived from MARNET (Eurostat SeaRoute, EUPL-1.2) as shipped in searoute-py (Apache-2.0)" | Bundled (derived output only; the package is generation-time) | Options: keep with attribution, or rebuild from Natural Earth. If a non-copyleft licence is chosen for the code, confirm the EUPL point first. |
| **Meta Model API** (Muse Spark `muse-spark-1.3`, SAM 3.1 `sam-3.1`, Muse Voice Transcribe `muse-voice-transcribe-1.0`) | Classify, explain, ask, query, agent mode, satellite vision read, SAM detections, voice input | Meta's API and model terms (**verify**; the SAM licence text was not confirmed). Muse Spark Open weights are not used. | "Meta AI" label in the UI; model id in the popover | Live when switched on; recorded real replies are committed as fixtures | The API key lives only in the environment and is not in the tree. |
