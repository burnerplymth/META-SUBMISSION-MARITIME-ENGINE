import type { BasemapMode } from "../types";
import {
  UNDERLAY_FILL_LAYER_ID,
  UNDERLAY_OUTLINE_LAYER_ID,
  UNDERLAY_SOURCE_ID,
} from "./underlay";

/**
 * Theme switch without a style reload.
 *
 * CARTO dark-matter and positron are the SAME style skeleton: the same
 * vector source (identical tile URL template), the same 95 layer ids and
 * identical layout; only the paint values and the sprite differ. A theme
 * switch via `map.setStyle(url, { diff: false })` is a full style
 * rebuild that re-created the source (every tile re-fetched and
 * re-parsed in the worker), the glyph and sprite state and the layer
 * stack, then rendered several WebGL frames while labels faded back in.
 * On a software GL stack (swiftshader, a headless browser) that kept
 * the GPU process busy for 1 to 4 seconds and the page repaint queued
 * behind it.
 *
 * A diffed `setStyle` with the other family's style JSON only issues the
 * `setPaintProperty` / `setSprite` operations that really differ. The
 * helpers here are the pure parts: deciding when the shortcut applies,
 * carrying the bundled coastline underlay across (it is a custom source
 * and layers MapLibre would otherwise remove as "not in the new style"),
 * and a small in-memory cache of the style JSON so the shortcut is
 * synchronous. Colours are untouched: the same style JSON is loaded,
 * and the existing theme re-paint still runs after it.
 */

/** Minimal structural view of a style document (kept loose on purpose:
 *  the real StyleSpecification is much wider and we only splice). */
export interface StyleDoc {
  version?: number;
  sources?: Record<string, unknown>;
  layers?: Array<{ id: string; type?: string } & Record<string, unknown>>;
  transition?: { duration?: number; delay?: number };
  [k: string]: unknown;
}

/** True when `prev -> next` is a dark/light family swap (the only pair
 *  that shares a style skeleton). Satellite and first loads are not. */
export function isFamilySwap(prev: BasemapMode | null, next: BasemapMode): boolean {
  return (prev === "dark" && next === "light") || (prev === "light" && next === "dark");
}

const UNDERLAY_LAYER_ORDER = [UNDERLAY_FILL_LAYER_ID, UNDERLAY_OUTLINE_LAYER_ID];

/**
 * Build the style a diffed `setStyle` should move to: `next` (the other
 * family's CARTO style) with the coastline underlay source and layers
 * copied from `prev` (the live, serialised style) and with an instant
 * paint transition (a theme switch is a state change, not an animation:
 * 300 ms of cross-fade is 300 ms of extra GPU frames). Pure; neither
 * input is mutated.
 */
export function withUnderlayCarried(prev: StyleDoc | undefined, next: StyleDoc): StyleDoc {
  const out: StyleDoc = { ...next, transition: { duration: 0, delay: 0 } };
  const prevSource = prev?.sources?.[UNDERLAY_SOURCE_ID];
  const prevLayers = (prev?.layers ?? []).filter((l) => UNDERLAY_LAYER_ORDER.includes(l.id));
  if (prevSource === undefined && prevLayers.length === 0) return out;
  if (prevSource !== undefined) out.sources = { ...(next.sources ?? {}), [UNDERLAY_SOURCE_ID]: prevSource };
  const base = (next.layers ?? []).filter((l) => !UNDERLAY_LAYER_ORDER.includes(l.id));
  // Same slot ensureUnderlay uses: beneath the first non-background layer.
  let at = base.findIndex((l) => l.type !== "background");
  if (at < 0) at = base.length;
  const ordered = UNDERLAY_LAYER_ORDER.map((id) => prevLayers.find((l) => l.id === id)).filter(
    (l): l is NonNullable<typeof l> => l !== undefined,
  );
  out.layers = [...base.slice(0, at), ...ordered, ...base.slice(at)];
  return out;
}

/* ---- style JSON cache ---------------------------------------------- */

const jsonCache = new Map<string, StyleDoc>();
const inflight = new Map<string, Promise<StyleDoc | null>>();

export function cachedStyleJson(url: string): StyleDoc | undefined {
  return jsonCache.get(url);
}

/** Fetch (once) and remember a style document. Never throws and never
 *  retries by itself: an unreachable host leaves the cache empty, and the
 *  caller then takes the ordinary full `setStyle` path (the one that owns
 *  the BASEMAP UNREACHABLE chip and the local fallback style). */
export function prefetchStyleJson(
  url: string,
  fetchImpl: typeof fetch | undefined = typeof fetch === "function" ? fetch : undefined,
): Promise<StyleDoc | null> {
  const hit = jsonCache.get(url);
  if (hit) return Promise.resolve(hit);
  const running = inflight.get(url);
  if (running) return running;
  if (!fetchImpl) return Promise.resolve(null);
  const p = (async () => {
    try {
      const res = await fetchImpl(url, { credentials: "omit" });
      if (!res.ok) return null;
      const json = (await res.json()) as StyleDoc;
      if (!json || typeof json !== "object" || !Array.isArray(json.layers)) return null;
      jsonCache.set(url, json);
      return json;
    } catch {
      return null;
    } finally {
      inflight.delete(url);
    }
  })();
  inflight.set(url, p);
  return p;
}

