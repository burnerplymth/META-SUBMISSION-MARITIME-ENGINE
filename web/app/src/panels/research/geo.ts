/* The AIS sample map: an equirectangular fit of a few vessel tracks into a small box. Pure. */
import type { PlaceRef, TrackRow } from './types';

export interface MapView { w: number; h: number; lines: Array<{ id: string; d: string }>; dots: Array<{ id: string; name: string; x: number; y: number }> }

export function fitTracks(tracks: TrackRow[], places: PlaceRef[], w = 360, h = 220, pad = 10): MapView | null {
  const pts = tracks.flatMap((t) => t.points);
  if (!pts.length) return null;
  let minLon = Infinity, maxLon = -Infinity, minLat = Infinity, maxLat = -Infinity;
  for (const p of pts) { minLon = Math.min(minLon, p.lon); maxLon = Math.max(maxLon, p.lon); minLat = Math.min(minLat, p.lat); maxLat = Math.max(maxLat, p.lat); }
  const kx = Math.cos(((minLat + maxLat) / 2) * Math.PI / 180);
  const spanX = Math.max((maxLon - minLon) * kx, 1e-4), spanY = Math.max(maxLat - minLat, 1e-4);
  const s = Math.min((w - 2 * pad) / spanX, (h - 2 * pad) / spanY);
  const ox = (w - spanX * s) / 2, oy = (h - spanY * s) / 2;
  const X = (lon: number) => Math.round((ox + (lon - minLon) * kx * s) * 10) / 10;
  const Y = (lat: number) => Math.round((oy + (maxLat - lat) * s) * 10) / 10;
  const lines = tracks.map((t) => ({ id: t.id, d: t.points.map((p, i) => `${i ? 'L' : 'M'}${X(p.lon)} ${Y(p.lat)}`).join('') }));
  const dots = places.filter((p) => p.lat !== null && p.lon !== null && p.lon >= minLon && p.lon <= maxLon && p.lat >= minLat && p.lat <= maxLat)
    .map((p) => ({ id: p.id, name: p.name, x: X(p.lon as number), y: Y(p.lat as number) }));
  return { w, h, lines, dots };
}
