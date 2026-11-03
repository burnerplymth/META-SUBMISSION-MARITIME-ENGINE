/**
 * Chart geometry for the event strip.
 *
 * The strip's grammar: when a series is
 * sparse, bars sit at a capped width and the series is centred in the
 * plot; when it is dense, bars aggregate into openly-stated bins. The
 * background grid and axis always stay full-width — only the data
 * block moves. Both helpers are pure.
 */

/** Bar layout for a sequence series in a plot of the given width:
 *  slot width capped at 48px; the used block is centred between the
 *  plot margins (20px left, W-20 right — the strip's existing frame). */
export function barLayout(count: number, width: number): { bw: number; x0: number } {
  if (count <= 0) return { bw: 0, x0: 20 };
  const bw = Math.min(48, (width - 40) / count);
  const x0 = 20 + ((width - 40) - bw * count) / 2;
  return { bw, x0 };
}

export interface QueueBins {
  /** One count per bin, at most maxBins entries. */
  counts: number[];
  /** Days per bin — 1 unless the span forces wider bins. */
  binDays: number;
  peak: number;
  /** First day of the peak bin (relative to the first event). */
  peakDay: number;
  spanDays: number;
}

/** Bucket queue/congestion events (chain `at` timestamps) into at most
 *  maxBins fixed time bins. The bin width widens openly with the span
 *  (1 day per bin up to maxBins days; wider spans aggregate into
 *  multi-day bins) — events are never folded silently into an end bin:
 *  the caller prints the bin width in the caption whenever it exceeds
 *  one day. */
export function queueBins(ats: string[], maxBins = 15): QueueBins | null {
  if (!ats.length) return null;
  const times = ats.map((a) => Date.parse(a)).filter((t) => !Number.isNaN(t));
  if (!times.length) return null;
  const t0 = Math.min(...times);
  const t1 = Math.max(...times);
  const spanDays = Math.floor((t1 - t0) / 86400000) + 1;
  const binDays = Math.max(1, Math.ceil(spanDays / maxBins));
  const nBins = Math.ceil(spanDays / binDays);
  const counts = new Array(nBins).fill(0) as number[];
  let peak = 0; let peakBin = 0;
  for (const t of times) {
    const b = Math.min(nBins - 1, Math.floor((t - t0) / 86400000 / binDays));
    counts[b] += 1;
    if (counts[b] > peak) { peak = counts[b]; peakBin = b; }
  }
  return { counts, binDays, peak, peakDay: peakBin * binDays, spanDays };
}
