/* What the Research window asks for. Pure: an episode in, the assemble request and the analysis specs out.
 * The recorded copies in ./fixtures were built from the same rules. */
import type { AnalysisKind, Assembly, Episode } from './types';

export const MAX_SCENES = 40;
export const MAX_MAP_TRACKS = 12;

const PRIMARY = ['portwatch_transits', 'portwatch_port_calls'] as const;
const SECONDARY = ['weather_wind_max_kmh', 'economic_eia_spot_brent'] as const;

export const DATASET_LABEL: Record<string, string> = {
  portwatch_transits: 'Transits', portwatch_transits_cargo: 'Cargo transits', portwatch_transit_capacity: 'Transit capacity',
  portwatch_port_calls: 'Port calls', portwatch_port_calls_cargo: 'Cargo port calls',
  weather_wind_max_kmh: 'Wind, daily max', weather_gust_max_kmh: 'Gust, daily max', weather_precip_mm: 'Precipitation', weather_wave_height_m: 'Wave height',
  economic_eia_spot_brent: 'Brent spot', economic_eia_spot_wti: 'WTI spot',
};
export const SOURCE_CHIP: Record<string, string> = {
  portwatch_transits: 'IMF PortWatch', portwatch_port_calls: 'IMF PortWatch', weather_wind_max_kmh: 'Open-Meteo', economic_eia_spot_brent: 'EIA',
};
export const datasetLabel = (id: string): string => DATASET_LABEL[id] ?? id.replace(/_/g, ' ');

export interface AssembleRequest {
  places: string[]; from: string; to: string; series: string[];
  events: { categories: string[]; source: 'episodes' };
  scenes: { enabled: true; sources: string[]; max_scenes: number };
  tracks: { enabled: boolean };
}

export function defaultPlan(ep: Episode): AssembleRequest {
  const primary = PRIMARY.find((d) => ep.series.includes(d));
  const series: string[] = [...(primary ? [primary] : []), ...SECONDARY.filter((d) => ep.series.includes(d))].sort();
  return {
    places: ep.places.slice(0, 2), from: ep.from, to: ep.to, series,
    events: { categories: ep.categories, source: 'episodes' },
    scenes: { enabled: true, sources: ['sentinel-2'], max_scenes: MAX_SCENES },
    tracks: { enabled: ep.hasTracks },
  };
}

/** Series keys of an assembly, primary dataset first. */
export function primaryKey(a: Assembly): string | null {
  for (const d of PRIMARY) { const s = a.series.find((x) => x.dataset === d && x.values.some((v) => v !== null)); if (s) return s.key; }
  return a.series.find((s) => s.values.some((v) => v !== null))?.key ?? null;
}
const pairKey = (a: Assembly): string | null => a.series.find((s) => s.dataset === 'weather_wind_max_kmh')?.key ?? a.series.find((s) => s.dataset === 'economic_eia_spot_brent')?.key ?? null;

export interface AnalysisCtx { series: string | null; x: string | null; eventId: string | null; runId: string | null }
export function defaultCtx(a: Assembly, ep: Episode): AnalysisCtx {
  const series = primaryKey(a);
  const first = ep.events.find((e) => a.events.some((x) => x.id === e.id)) ?? null;
  return { series, x: pairKey(a), eventId: first ? first.id : a.events[0]?.id ?? null, runId: ep.simRuns[0] ?? null };
}

export type AnalysisSpec = Record<string, unknown> & { kind: AnalysisKind };

/** The request body for one analysis, or null when its inputs are missing (the button stays off). */
export function analysisSpec(kind: AnalysisKind, c: AnalysisCtx): AnalysisSpec | null {
  const s = c.series;
  if (!s) return null;
  switch (kind) {
    case 'event_study': return c.eventId ? { kind, series: s, event_ids: [c.eventId] } : null;
    case 'before_after': return c.eventId ? { kind, series: s, split: 'event', event_id: c.eventId } : null;
    case 'lagged_correlation': return c.x && c.x !== s ? { kind, x: c.x, y: s, max_lag_days: 14, method: 'spearman' } : null;
    case 'change_points': return { kind, series: s, method: 'cusum' };
    case 'distribution': return { kind, series: s, by: 'month' };
    case 'seasonality': return { kind, series: s };
    case 'model_overlay': return c.runId ? { kind, run_or_scenario: c.runId, series: s } : null;
  }
}

/** Stable key of a spec, for the recorded copy's lookup. */
export function specKey(spec: AnalysisSpec): string {
  const o: Record<string, unknown> = {};
  for (const k of Object.keys(spec).sort()) o[k] = spec[k];
  return JSON.stringify(o);
}
export function requestKey(r: AssembleRequest): string {
  return JSON.stringify({ ...r, series: [...r.series].sort() });
}
