/* Words and number formats of the Monte Carlo window. One decimal rule per
 * unit: days to one place, counts to none. A share is always "k of N". */
import { finite } from './math';
import type { McJob, McRequest } from './types';

/** The seed as the server stores it: only the magnitude counts, so -5 and 5 are one job. */
export const seedWord = (seed: number): string => String(Math.abs(seed));
export const MC_LABEL = 'Input-uncertainty simulation (seeded). Modelled, conditional on the stated input ranges; not a forecast.';

export const METRIC_LABELS: Readonly<Record<string, string>> = {
  worst_added_transit_d: 'Worst added transit', total_added_delay_d: 'Total added delay', places_affected_count: 'Places affected',
  chain_length_events: 'Chain length', queue_peak_delay_d: 'Queue peak delay',
  queue_peak_vessels: 'Queue peak', volume_at_risk_teu_day: 'Volume at risk',
};
export const metricLabel = (k: string): string => METRIC_LABELS[k] ?? k.replace(/_d$/, '').replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());

const NF0 = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });
const NF1 = new Intl.NumberFormat('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const NF2 = new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** Decimals of a unit: days and ratios read to one place, counts to none. */
export const decimalsOf = (unit: string): number => (unit === 'd' ? 1 : 0);
export const NONE = '—';

export function fmt(v: number | null | undefined, unit = 'd'): string {
  if (!finite(v)) return NONE;
  return decimalsOf(unit) === 1 ? NF1.format(v) : NF0.format(v);
}
export const fmt1 = (v: number | null | undefined): string => (finite(v) ? NF1.format(v) : NONE);
export const fmt2 = (v: number | null | undefined): string => (finite(v) ? NF2.format(v) : NONE);
export const fmtInt = (v: number | null | undefined): string => (finite(v) ? NF0.format(v) : NONE);
export function fmtSigned(v: number | null | undefined, unit = 'd'): string {
  if (!finite(v)) return NONE;
  const s = fmt(Math.abs(v), unit);
  return v > 0 && s !== fmt(0, unit) ? `+${s}` : v < 0 && s !== fmt(0, unit) ? `−${s}` : s;
}
/** "15.2 d" with its unit; counts carry their word. */
export const withUnit = (v: number | null | undefined, unit: string): string => (finite(v) ? `${fmt(v, unit)}${unit === 'd' ? ' d' : unit ? ` ${unit}` : ''}` : NONE);
/** "13.5 to 28.2 d" */
export function range(lo: number, hi: number, unit: string): string {
  return `${fmt(lo, unit)} to ${fmt(hi, unit)}${unit === 'd' ? ' d' : unit ? ` ${unit}` : ''}`;
}
export const fmtPct = (share: number | null | undefined): string => (finite(share) ? `${NF1.format(share * 100)}%` : NONE);
/** "134 of 200 simulations" */
export const kOfN = (k: number, n: number): string => `${NF0.format(k)} of ${NF0.format(n)} simulations`;

/** Short text of a digest: the first eight characters. */
export const digestShort = (d: string | null | undefined): string => (d ? d.slice(0, 8) : NONE);

/** "Suez Canal closure 14 d" for a request; `nameOf` maps a place id to its name. */
export function scenarioName(req: Pick<McRequest, 'disruption'>, nameOf: (id: string) => string): string {
  const d = req.disruption;
  const loss = d.kind !== 'closure' && d.capacity_loss !== undefined ? ` ${Math.round(d.capacity_loss * 100)}%` : '';
  return `${nameOf(d.node_id)} ${d.kind.replace(/_/g, ' ')}${loss} ${fmtDur(d.duration_d)} d`;
}
const fmtDur = (d: number): string => (Number.isInteger(d) ? NF0.format(d) : NF1.format(d));

/** Engine word of a job: "Rev 2", "Rev 2 c". */
export function engineWord(j: Pick<McJob, 'request'>): string {
  const r = j.request;
  return `Rev ${r.engine_rev}${r.profile ? ` ${r.profile}` : ''}`;
}

/** The request as one line for an export caption and a manifest. */
export function requestLine(j: Pick<McJob, 'request'>, nameOf: (id: string) => string): string {
  const r = j.request;
  return `${scenarioName(r, nameOf)} · ${engineWord(j)} · n ${r.n} · seed ${seedWord(r.seed)}`;
}
