/* No-data share of a stored crop: pure logic.
 *
 * A Sentinel-2 crop cut at the swath edge carries a wedge of pure black (value
 * 0,0,0) or transparent pixels. The desk measures that share in the browser,
 * hatches the region and states the percentage, and withholds the change flag
 * when a crop is mostly blank. This is display only: the measurement reads a
 * private copy of the pixels, nothing is written back, and no count or
 * detection reads it. No DOM, no fetch, no React. */

/** Share of no-data pixels (percent) at which the chip appears. */
export const NODATA_SHOW_PCT = 0.5;
/** Share (percent) above which the change flag and delta are withheld. */
export const NODATA_HIGH_PCT = 5;
/** Target number of hatch cells along the longer side. */
const GRID = 150;
/** A cell is hatched when at least this share of its pixels is no data. */
const CELL_SHARE = 0.9;

export interface NoDataStats {
  w: number; h: number;
  /** Percent of pixels that are pure black or fully transparent, 0 to 100. */
  pct: number;
  cellPx: number; cols: number; rows: number;
  /** Row-major cols x rows; 1 = hatched. */
  cells: Uint8Array;
}
export type NoDataState = { s: 'loading' } | { s: 'ok'; stats: NoDataStats } | { s: 'err' };
export interface CropNoData { now: NoDataState; baseline: NoDataState }

/** Measure a w x h RGBA buffer. A pixel is no data when it is fully
 *  transparent or exactly 0,0,0. Dark water is never exactly zero. */
export function analyseNoData(rgba: ArrayLike<number>, w: number, h: number): NoDataStats {
  const cellPx = Math.max(1, Math.ceil(Math.max(w, h) / GRID));
  const cols = Math.ceil(w / cellPx); const rows = Math.ceil(h / cellPx);
  const per = new Uint32Array(cols * rows);
  const area = new Uint32Array(cols * rows);
  let blank = 0;
  for (let y = 0; y < h; y++) {
    const rowBase = Math.floor(y / cellPx) * cols;
    for (let x = 0; x < w; x++) {
      const o = (y * w + x) * 4;
      const c = rowBase + Math.floor(x / cellPx);
      area[c]++;
      if (rgba[o + 3] === 0 || (rgba[o] === 0 && rgba[o + 1] === 0 && rgba[o + 2] === 0)) { blank++; per[c]++; }
    }
  }
  const cells = new Uint8Array(cols * rows);
  for (let i = 0; i < cells.length; i++) cells[i] = area[i] > 0 && per[i] / area[i] >= CELL_SHARE ? 1 : 0;
  return { w, h, pct: w * h > 0 ? (blank / (w * h)) * 100 : 0, cellPx, cols, rows, cells };
}

/** The chip word: whole percent from 10, one decimal below. */
export const noDataChipWord = (pct: number): string => `No data ${pct >= 10 ? Math.round(pct) : pct.toFixed(1)}%`;
/** The chip is drawn only for a measurable share. */
export const showNoData = (s: NoDataState): s is { s: 'ok'; stats: NoDataStats } => s.s === 'ok' && s.stats.pct >= NODATA_SHOW_PCT;

/** Whether the change flag and delta may be drawn. `pending` while a crop is
 *  still being measured; an unreadable crop leaves the served delta as it was. */
export function changeGate(now: NoDataState, baseline: NoDataState): 'show' | 'pending' | 'withheld' {
  for (const s of [now, baseline]) if (s.s === 'ok' && s.stats.pct > NODATA_HIGH_PCT) return 'withheld';
  if (now.s === 'loading' || baseline.s === 'loading') return 'pending';
  return 'show';
}
