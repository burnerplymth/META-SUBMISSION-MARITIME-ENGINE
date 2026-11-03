import { defineConfig, type Plugin } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';

/* jsPDF loads three optional packages on demand (html(), SVG-as-image). The run reports use neither, so they are replaced by an
 * empty module: nothing of them is bundled or emitted, and the PDF code stays one small lazy chunk. */
const jspdfOptional = (): Plugin => ({
  name: 'jspdf-optional-stub',
  enforce: 'pre',
  resolveId(id, importer) {
    return importer && /node_modules[\\/]jspdf[\\/]/.test(importer) && ['canvg', 'dompurify', 'html2canvas'].includes(id) ? '\0jspdf-optional-stub' : null;
  },
  load(id) { return id === '\0jspdf-optional-stub' ? 'export default undefined;' : null; },
});

// The API (server/api/app.py) sends no CORS headers, so in dev/preview the
// browser reaches it through this proxy. Set VITE_API_BASE to call the API
// directly instead (see src/api.ts); default base is "" (same-origin/proxy).
const API = process.env.SUPERMAP_API_ORIGIN || 'http://localhost:8000';
/* The prefix set must cover EVERY route prefix the API serves
 * (server/api/app.py routes + mounted routers: health /health, llm
 * /llm, desks /desks, the public namespace under /api): a prefix
 * missing here falls through to the SPA fallback in preview/dev and
 * returns index.html instead of API JSON (note /runs is shadowed only
 * by the '/run' string prefix, so it is listed explicitly). */
const proxy = Object.fromEntries(
  ['/montecarlo', '/research', '/exposure', '/run', '/runs', '/fork', '/ensemble', '/ensemble-stats', '/api', '/chain', '/health', '/scorecard', '/events', '/graph', '/llm', '/money', '/satwatch', '/lenses', '/alerts', '/watchlists', '/sim', '/impact', '/portwatch', '/livetwin', '/desks', '/feeds', '/live', '/ports', '/reports', '/exports', '/history', '/realism', '/scenarios', '/lanes', '/replay', '/voice', '/agent', '/brief', '/shocks', '/compare', '/openapi.json'].map((p) => [
    p,
    { target: API, changeOrigin: true },
  ]),
);
export default defineConfig({
  plugins: [react(), jspdfOptional()],
  /* The app bundles the map package's source (../../map/src),
   * which has its own react peer copy — dedupe so exactly one
   * react/react-dom ships. fs.allow mirrors web/map's config: the
   * app reads across the workspace root in dev (the seed's dynamic
   * graph import reaches ../../../data/graph/graph.json). */
  resolve: { dedupe: ['react', 'react-dom'] },
  server: {
    port: 5173,
    proxy,
    fs: { allow: [fileURLToPath(new URL('../../', import.meta.url))] },
  },
  preview: { port: 4173, proxy },
  /* Boot performance. Chunking only, no behaviour change:
   *  - the map (maplibre + deck.gl, ~1.9 MB) is already behind a dynamic
   *    import (panels/core.tsx); its libraries get their own long-lived
   *    chunks so an app-code change does not invalidate them and the
   *    two big ones download in parallel;
   *  - react and the chain-diagram libraries are split from app code for
   *    the same cache reason;
   *  - panels outside the default screen are dynamic imports
   *    (panels/lazy.ts, panels/history/index.ts).
   * The one chunk that stays over the limit is the bundled offline copy
   * of the network (graph.json, ~4.4 MB, imported dynamically by
   * seed.ts as the API-down fallback): it is data, loaded only when the
   * API cannot serve the same file. */
  build: {
    chunkSizeWarningLimit: 1200,
    rollupOptions: {
      output: {
        manualChunks(id: string) {
          /* Vite's dynamic-import helper must not land in a library
           * chunk (it would make the entry import the map libraries). */
          if (id.includes('vite/preload-helper') || id.includes('vite/modulepreload-polyfill')) return 'vendor-react';
          if (!id.includes('node_modules')) return undefined;
          if (/node_modules\/(react|react-dom|scheduler|use-sync-external-store)\//.test(id)) return 'vendor-react';
          if (/node_modules\/(@xyflow|d3-[a-z-]+|classcat|zustand)\//.test(id)) return 'vendor-flow';
          if (/node_modules\/(jspdf|svg2pdf\.js|svgpath|cssesc|specificity|font-family-papandreou|fast-png|iobuffer)\//.test(id)) return 'vendor-pdf';
          if (/node_modules\/maplibre-gl\//.test(id)) return 'vendor-maplibre';
          if (/node_modules\/(@deck\.gl|@luma\.gl|@math\.gl|@loaders\.gl|@probe\.gl|mjolnir\.js|earcut|@mapbox)\//.test(id)) return 'vendor-deck';
          return undefined;
        },
      },
    },
  },
});
