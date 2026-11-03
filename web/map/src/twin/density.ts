// TWIN density heat (pure): at very low zoom thousands of dots merge
// into noise, so the fleet is also binned into a coarse lon/lat grid
// and drawn as soft glow discs whose strength follows the (log) count.
// Binning runs over the already-filtered DrawSets, reuses its buffers,
// and costs O(entities).

import type { DrawSet } from "./fleet";

/** Cells with fewer entities than this draw no glow (the dots carry them). */
export const HEAT_MIN_COUNT = 3;

export class HeatBuffer {
  n = 0;
  pos = new Float64Array(0);
  /** cell counts, same order as pos */
  count = new Uint16Array(0);
  /** RGBA per cell (alpha = strength) */
  color = new Uint8ClampedArray(0);
  /** glow radius in px per cell (grows with the cell's count) */
  radius = new Float32Array(0);
  maxCount = 0;
  private grid = new Uint16Array(0);
  private gridKey = "";
  ensureGrid(w: number, h: number): Uint16Array {
    const key = `${w}x${h}`;
    if (key !== this.gridKey) {
      this.grid = new Uint16Array(w * h);
      this.gridKey = key;
      this.pos = new Float64Array(w * h * 2);
      this.count = new Uint16Array(w * h);
      this.color = new Uint8ClampedArray(w * h * 4);
      this.radius = new Float32Array(w * h);
    } else {
      this.grid.fill(0);
    }
    return this.grid;
  }
}

/** Bin DrawSet positions into `cellDeg` cells; fills heat.pos/count for
 *  the non-empty cells and returns their number. */
export function binDensity(
  sets: ReadonlyArray<DrawSet>,
  cellDeg: number,
  heat: HeatBuffer,
  rgb: readonly [number, number, number] = [255, 255, 255],
): number {
  const w = Math.ceil(360 / cellDeg);
  const h = Math.ceil(170 / cellDeg);
  const grid = heat.ensureGrid(w, h);
  for (const set of sets) {
    for (let k = 0; k < set.n; k++) {
      const lon = set.pos[k * 2];
      const lat = set.pos[k * 2 + 1];
      if (lat < -85 || lat > 85) continue;
      const cx = Math.min(w - 1, Math.max(0, Math.floor((lon + 180) / cellDeg)));
      const cy = Math.min(h - 1, Math.max(0, Math.floor((lat + 85) / cellDeg)));
      const i = cy * w + cx;
      if (grid[i] < 65535) grid[i]++;
    }
  }
  let n = 0;
  let max = 0;
  for (let cy = 0; cy < h; cy++) {
    for (let cx = 0; cx < w; cx++) {
      const c = grid[cy * w + cx];
      if (c < HEAT_MIN_COUNT) continue; // singles are drawn as dots anyway
      heat.pos[n * 2] = -180 + (cx + 0.5) * cellDeg;
      heat.pos[n * 2 + 1] = -85 + (cy + 0.5) * cellDeg;
      heat.count[n] = c;
      if (c > max) max = c;
      n++;
    }
  }
  heat.n = n;
  heat.maxCount = max;
  for (let i = 0; i < n; i++) {
    heat.color[i * 4] = rgb[0];
    heat.color[i * 4 + 1] = rgb[1];
    heat.color[i * 4 + 2] = rgb[2];
    const st = heatStrength(heat.count[i], max);
    heat.color[i * 4 + 3] = Math.round(10 + 80 * st * st);
    heat.radius[i] = 5 + 9 * st;
  }
  return n;
}

/** Glow alpha (0..1) for a cell: log scale against the busiest cell. */
export function heatStrength(count: number, max: number): number {
  if (count <= 0 || max <= 0) return 0;
  return Math.log1p(count) / Math.log1p(max);
}
