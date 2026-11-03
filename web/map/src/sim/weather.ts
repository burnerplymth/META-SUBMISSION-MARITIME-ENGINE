// Weather layer data.
// Pure projection of the pinned snapshot + state payload into map
// points at the SIM TIME IN VIEW:
//   - node points: the weather IN FORCE served inside GET /sim/state
//     nodes[].weather (wind/wave + valid_at + source travel with the
//     number — the client never re-derives node weather);
//   - lane-midpoint points: wave height sampled from the pinned
//     snapshot's lane_midpoints series at the hour containing sim_time
//     (the same hour-index rule the server uses; a display read of the
//     pinned snapshot, not a recomputation of the sim).
// When the run has no weather the layer's state is an honest
// UNAVAILABLE with the server's reason — never fabricated values.

import { channelColors, modeColors } from "../theme/tokens";
import type { LonLat } from "./geometry";
import type {
  SimLane,
  SimState,
  SimWeatherResponse,
  SimWeatherSnapshot,
} from "./types";

export interface WeatherPoint {
  kind: "node" | "lane-midpoint";
  id: string;
  position: LonLat;
  wind_kn: number | null;
  wind_dir: number | null;
  wave_m: number | null;
  valid_at: string | null;
}

export type WeatherLayerData =
  | { status: "ok"; points: WeatherPoint[]; retrieved_at: string; source: string }
  | { status: "unavailable"; reason: string };

/** Index of the snapshot hour containing `simTime` (ISO). Snapshot
 *  times are hourly ISO strings ("YYYY-MM-DDTHH:00") — compare on the
 *  hour prefix, mirroring server/sim/weather.py hour_index. */
export function hourIndex(times: string[], simTime: string): number | null {
  if (!times.length) return null;
  const target = simTime.slice(0, 13);
  if (target < times[0].slice(0, 13) || target > times[times.length - 1].slice(0, 13)) {
    return null;
  }
  for (let i = 0; i < times.length; i++) {
    if (times[i].slice(0, 13) === target) return i;
  }
  return null;
}

export function weatherLayerData(
  response: SimWeatherResponse | null,
  state: SimState,
  lanesByKey: Map<string, SimLane>,
  nodePos: Map<string, LonLat>,
): WeatherLayerData {
  if (!response) {
    return { status: "unavailable", reason: "weather not loaded for this run" };
  }
  if (response.weather === null) {
    return { status: "unavailable", reason: response.reason };
  }
  const snapshot: SimWeatherSnapshot = response.weather;
  const points: WeatherPoint[] = [];

  // Node points from the state payload's in-force weather (authoritative).
  for (const n of state.nodes) {
    if (!n.weather) continue;
    const pos = nodePos.get(n.id);
    if (!pos) continue;
    const series = snapshot.nodes[n.id];
    const idx = series ? hourIndex(series.times, state.sim_time) : null;
    points.push({
      kind: "node",
      id: n.id,
      position: pos,
      wind_kn: n.weather.wind_kn,
      wind_dir: idx !== null && series?.wind_dir ? series.wind_dir[idx] : null,
      wave_m: n.weather.wave_m,
      valid_at: n.weather.valid_at,
    });
  }

  // Lane-midpoint wave samples from the pinned snapshot series.
  for (const [key, series] of Object.entries(snapshot.lane_midpoints)) {
    const lane = lanesByKey.get(key);
    if (!lane?.midpoint) continue;
    const idx = hourIndex(series.times, state.sim_time);
    if (idx === null) continue;
    const wave = series.wave_m?.[idx] ?? null;
    if (wave === null) continue;
    points.push({
      kind: "lane-midpoint",
      id: key,
      position: [lane.midpoint[1], lane.midpoint[0]],
      wind_kn: null,
      wind_dir: null,
      wave_m: wave,
      valid_at: series.times[idx],
    });
  }
  points.sort((a, b) => a.id.localeCompare(b.id) || a.kind.localeCompare(b.kind));
  return {
    status: "ok",
    points,
    retrieved_at: snapshot.retrieved_at,
    source: snapshot.source,
  };
}

/** Graded colour for a wind speed (kn): calm -> breeze -> gale -> storm.
 *  Returns an RGB triple; alpha is the layer's choice. Thresholds are a
 *  display grading only (Beaufort-ish breaks), not a sim input.
 *  The grades are the desk's EXISTING palette families —
 *  sea (calm) · inventory (breeze) · reroute gold (gale) · queue red
 *  (storm) — never a new colour idiom. */
const GRADE_CALM = modeColors.sea;
const GRADE_BREEZE = channelColors.inventory;
const GRADE_GALE = channelColors.reroute;
const GRADE_STORM = channelColors.queue;

export function windGradeRgb(windKn: number): [number, number, number] {
  if (windKn < 11) return GRADE_CALM;
  if (windKn < 22) return GRADE_BREEZE;
  if (windKn < 34) return GRADE_GALE;
  return GRADE_STORM;
}

/** Graded colour for a wave height (m). */
export function waveGradeRgb(waveM: number): [number, number, number] {
  if (waveM < 1) return GRADE_CALM;
  if (waveM < 2.5) return GRADE_BREEZE;
  if (waveM < 4) return GRADE_GALE;
  return GRADE_STORM;
}

export const WEATHER_ATTRIBUTION = "Weather data by Open-Meteo.com";
/* Rendered on the map wherever weather shows — user-readable text, so
 * the vocabulary law applies (places / network, never the data
 * model's own words). */
export const WEATHER_SAMPLE_LABEL = "Sampled at network points";
/* Legend label for the modelled weather-cell field (state
 * payload weather_cells) — user-readable vocabulary only. */
export const WEATHER_CELLS_LABEL = "Weather field, modelled cells";
