/* Plate-carree plot of places for the viewer's MAP tab: pure
 * projection and hit-testing. No basemap tiles; the places themselves
 * draw the coastline. */
export interface PlacePoint { id: string; name: string; kind: string; lat: number; lon: number }

export function readPlaces(nodes: readonly unknown[]): PlacePoint[] {
  const out: PlacePoint[] = [];
  for (const n of nodes) {
    const o = n as Record<string, unknown>;
    if (typeof o?.id === 'string' && typeof o.lat === 'number' && typeof o.lon === 'number' && Number.isFinite(o.lat) && Number.isFinite(o.lon)) {
      out.push({ id: o.id, name: typeof o.name === 'string' ? o.name : o.id, kind: typeof o.kind === 'string' ? o.kind : 'place', lat: o.lat, lon: o.lon });
    }
  }
  return out;
}

export interface Projector { x: (lon: number) => number; y: (lat: number) => number; w: number; h: number }
/** Fit lon -180..180 and lat -60..80 into w x h, letterboxed. */
export function projector(w: number, h: number, pad = 8): Projector {
  const lonSpan = 360; const latSpan = 140;
  const s = Math.min((w - pad * 2) / lonSpan, (h - pad * 2) / latSpan);
  const ox = (w - lonSpan * s) / 2; const oy = (h - latSpan * s) / 2;
  return { x: (lon) => ox + (lon + 180) * s, y: (lat) => oy + (80 - Math.max(-60, Math.min(80, lat))) * s, w, h };
}

export function nearestPlace(points: readonly PlacePoint[], pr: Projector, px: number, py: number, maxPx = 10): PlacePoint | null {
  let best: PlacePoint | null = null; let bd = maxPx;
  for (const p of points) {
    const d = Math.hypot(pr.x(p.lon) - px, pr.y(p.lat) - py);
    if (d <= bd) { bd = d; best = p; }
  }
  return best;
}
