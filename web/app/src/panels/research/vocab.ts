/* Chip and row wording of the Research window. Short noun phrases; the API's chip ids map to sentence-case words. */
import type { AnalysisResult } from './types';

const CAVEAT: Record<string, string> = {
  insufficient_n: 'Insufficient n', short_window: 'Short window', gaps_in_series: 'Gaps in series', autocorrelated_daily_data: 'Autocorrelated daily data',
  block_bootstrap: 'Block bootstrap', multiple_lags_examined: 'Multiple lags examined', single_event: 'Single event', few_events: 'Few events',
  multiple_events: 'Multiple events', data_dependent_selection: 'Data-dependent selection', modelled_atmosphere: 'Modelled atmosphere',
  modelled_count: 'Modelled count', simulated: 'Simulated', estimate_not_observation: 'Estimate, not observation', cloud_unusable_excluded: 'Cloudy scenes excluded',
  recorded_not_live: 'Recorded', no_control_series: 'No control series', weekday_pattern_only: 'Weekday pattern only',
  change_points_are_segmentations: 'Segmentation', category_not_in_library: 'Category not in library', no_open_track_data: 'No open track data',
  no_series_for_place: 'No series for place', not_applicable: 'Not applicable', not_in_cache: 'Not in cache', no_data: 'No data', not_served: 'Not served',
  no_events_in_library: 'No events in library', no_scenes: 'No scenes', scenes_subsampled: 'Scenes subsampled',
};
export const caveatWord = (c: string): string => CAVEAT[c] ?? (c.replace(/[^A-Za-z0-9 _-]/g, '').replace(/_/g, ' ').trim().replace(/^./, (m) => m.toUpperCase()) || 'Note');

const STATE: Record<string, string> = { cached: 'Cached', live: 'Live', not_served: 'Not served' };
export const stateWord = (s: string): string => STATE[s] ?? 'Not served';

export const ERROR_WORD: Record<string, string> = {
  series_not_found: 'Series not in this assembly', series_not_served: 'Series not served', event_not_found: 'Event not in this assembly',
  split_outside_window: 'Split outside the window', insufficient_data: 'Insufficient data', run_not_found: 'Run not available', no_overlay_series: 'No simulated series for this place',
  invalid_request: 'Request refused', recorded_only: 'Not in the recorded copy',
};
export const errorWord = (code: string): string => ERROR_WORD[code] ?? 'Request refused';

export const SCENE_REASON: Record<string, string> = {
  no_recorded_reply: 'No recorded reply', s1_cfar_live_not_offered: 'Not offered', cloud: 'Cloud above limit', nodata: 'No-data above limit',
};

const f = (v: unknown, d = 2): string => (typeof v === 'number' && Number.isFinite(v) ? v.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d }) : 'Not served');
const sig = (v: unknown): string => (typeof v === 'number' && Number.isFinite(v) ? v.toLocaleString('en-US', { maximumFractionDigits: 1 }) : 'Not served');
const o = (v: unknown): Record<string, unknown> => (typeof v === 'object' && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : {});
const a = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);

export interface Row { label: string; value: string }

/** The headline numbers of a result as label/value rows (no prose). */
export function summaryRows(r: AnalysisResult): Row[] {
  const e = r.estimate;
  switch (r.kind) {
    case 'event_study': {
      const tr = o(e.trough);
      return [
        { label: 'Trough day', value: `${sig(tr.offset_day)} d` }, { label: 'Trough index', value: f(tr.index) },
        { label: 'Days below 0.90 index', value: `${sig(e.days_below_90pct)} d` },
        { label: 'Recovery day', value: e.recovery_offset_day === null || e.recovery_offset_day === undefined ? 'Not reached' : `${sig(e.recovery_offset_day)} d` },
      ];
    }
    case 'before_after': {
      const b = o(e.before), af = o(e.after);
      return [
        { label: 'Mean before', value: f(b.mean, 1) }, { label: 'Mean after', value: f(af.mean, 1) }, { label: 'Difference', value: f(e.diff_mean, 1) },
        { label: 'Change', value: `${f(e.pct_change, 1)} %` }, { label: 'Days before / after', value: `${sig(b.n)} / ${sig(af.n)}` },
      ];
    }
    case 'lagged_correlation': {
      const p = o(e.peak);
      return [{ label: 'Peak lag', value: `${sig(p.lag)} d` }, { label: 'Spearman rho at peak', value: f(p.rho, 2) }, { label: 'Pairs at peak', value: sig(p.n) }];
    }
    case 'change_points': {
      const pts = a(e.points).map(o);
      return [{ label: 'Change points', value: String(pts.length) }, ...pts.slice(0, 4).map((p, i) => ({ label: `Point ${i + 1}`, value: `${String(p.date ?? '')}: ${f(p.mean_before, 1)} to ${f(p.mean_after, 1)}` }))];
    }
    case 'distribution': {
      const g = a(e.groups).map(o);
      return [{ label: 'Groups', value: String(g.length) }, ...g.slice(0, 6).map((x) => ({ label: String(x.key ?? ''), value: `median ${f(x.median, 1)}, n ${sig(x.n)}` }))];
    }
    case 'seasonality': {
      const w = a(e.weekday).map(o).filter((x) => typeof x.index === 'number');
      if (!w.length) return [];
      const hi = w.reduce((m, x) => ((x.index as number) > (m.index as number) ? x : m)), lo = w.reduce((m, x) => ((x.index as number) < (m.index as number) ? x : m));
      const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
      return [{ label: 'Highest weekday', value: `${DAYS[Number(hi.dow)] ?? ''} ${f(hi.index, 2)}` }, { label: 'Lowest weekday', value: `${DAYS[Number(lo.dow)] ?? ''} ${f(lo.index, 2)}` }, { label: 'Month profile', value: e.month ? 'Shown' : 'Not served' }];
    }
    case 'model_overlay': {
      const m = o(e.metrics);
      return [
        { label: 'Overlapping days', value: sig(m.n_overlap) }, { label: 'Mean absolute difference', value: f(m.mean_abs_diff, 1) },
        { label: 'Rank correlation', value: f(m.rank_correlation, 2) }, { label: 'Observed mean', value: f(m.observed_mean, 1) }, { label: 'Simulated mean', value: f(m.simulated_mean, 1) },
      ];
    }
  }
}

/** The overlay label the API appends (Simulated beside observed); empty for other kinds. */
export function overlayLabel(r: AnalysisResult): string | null {
  const l = o(r.estimate).label;
  return r.kind === 'model_overlay' && typeof l === 'string' ? l : null;
}
