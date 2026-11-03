import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

// Standalone page build for the map module.
// The page imports the real graph from ../../../data/graph/graph.json
// (read-only, at build time) so the module demos on real data without a
// GET /graph API endpoint.
// The web app consumes src/index.ts directly and supplies its own data; this
// config only serves the page entry (index.html -> src/main.tsx).
export default defineConfig({
  plugins: [react()],
  /* The app consumes this package's source directly, so both
   * sides must resolve to ONE react/react-dom copy. */
  resolve: { dedupe: ["react", "react-dom"] },
  server: {
    fs: {
      allow: [fileURLToPath(new URL("../../", import.meta.url))],
    },
  },
  build: {
    /* Realistic warning limit — the main chunk is
     * ~6.6 MB (deck.gl + maplibre + the bundled real graph JSON);
     * warn when it grows past 7 MB. */
    chunkSizeWarningLimit: 7000,
  },
});
