/* PORT TWIN run orchestration: one scenario run and everything the panel
 * shows from it. Pure async code over an injected client. Each feed is independent: a failed frames, KPI,
 * compare or variants call leaves its own `down` entry and the section that
 * reads it is hidden; the others still show. */
import * as api from './client';
import { HttpError } from '../ports/http';
import { framePlan } from './scenario';
import { failureSentence } from '../failureText';
import type { VariantSet } from './kpi';
import type { CompareDoc, FramesDoc, KpisDoc, SimRequest, SimRun } from './types';

export type FeedKey = 'frames' | 'kpis' | 'compare' | 'baseline' | 'variants';
export interface SimBundle {
  status: 'idle' | 'running' | 'done' | 'error';
  /** A refusal or failure: short word + the server's reason. */
  error: { word: string; reason: string } | null;
  req: SimRequest | null;
  run: SimRun | null;
  frames: FramesDoc | null;
  kpis: KpisDoc | null;
  compare: CompareDoc | null;
  baseKpis: KpisDoc | null;
  baseFrames: FramesDoc | null;
  variants: VariantSet;
  down: Partial<Record<FeedKey, string>>;
}
export const EMPTY_BUNDLE: SimBundle = {
  status: 'idle', error: null, req: null, run: null, frames: null, kpis: null, compare: null, baseKpis: null, baseFrames: null, variants: {}, down: {},
};

export interface SimClient {
  runSim: typeof api.runSim;
  fetchFrames: typeof api.fetchFrames;
  fetchKpis: typeof api.fetchKpis;
  fetchCompare: typeof api.fetchCompare;
}
export const realClient: SimClient = { runSim: api.runSim, fetchFrames: api.fetchFrames, fetchKpis: api.fetchKpis, fetchCompare: api.fetchCompare };

export const feedWord = (e: unknown): string => {
  if (e instanceof HttpError) return e.status === 404 ? 'NOT SERVED' : e.status === 0 ? 'DOWN' : e.status === 422 || e.status === 400 ? 'REFUSED' : e.status === 429 ? 'RATE LIMITED' : 'DOWN';
  return 'DOWN';
};
const reasonOf = (e: unknown): string => (e instanceof HttpError ? failureSentence(e.code, e.status) : failureSentence(null));

export interface RunOpts { withVariants: boolean; client?: SimClient; signal?: AbortSignal }

/** Run, then fan out. `emit` receives a partial patch each time a feed lands. */
export async function runBundle(placeId: string, req: SimRequest, emit: (patch: Partial<SimBundle>) => void, opts: RunOpts): Promise<void> {
  const c = opts.client ?? realClient;
  const { signal } = opts;
  emit({ ...EMPTY_BUNDLE, status: 'running', req });
  let run: SimRun;
  try { run = await c.runSim(placeId, req, signal); }
  catch (e) {
    if (signal?.aborted) return;
    emit({ status: 'error', error: { word: feedWord(e), reason: reasonOf(e) } });
    return;
  }
  if (signal?.aborted) return;
  emit({ status: 'done', run, variants: { base: run.summary } });
  const plan = framePlan(run.journal.days);
  const down: Partial<Record<FeedKey, string>> = {};
  const mark = (k: FeedKey, e: unknown) => { if (signal?.aborted) return; down[k] = feedWord(e); emit({ down: { ...down } }); };
  const tasks: Array<Promise<void>> = [];
  tasks.push(c.fetchFrames(placeId, run.sim_id, plan.fromH, plan.toH, plan.stepMin, signal).then((frames) => { if (!signal?.aborted) emit({ frames }); }, (e) => mark('frames', e)));
  tasks.push(c.fetchKpis(placeId, run.sim_id, signal).then((kpis) => { if (!signal?.aborted) emit({ kpis }); }, (e) => mark('kpis', e)));
  const scenarioRun = run.journal.scenario.length > 0;
  if (scenarioRun) {
    tasks.push(c.fetchCompare(placeId, run.sim_id, signal).then(async (compare) => {
      if (signal?.aborted) return;
      emit({ compare });
      try {
        const baseKpis = await c.fetchKpis(placeId, compare.baseline_id, signal);
        if (!signal?.aborted) emit({ baseKpis });
      } catch (e) { mark('baseline', e); }
    }, (e) => mark('compare', e)));
  }
  if (opts.withVariants) {
    const one = async (v: 'low' | 'high') => {
      try { return (await c.runSim(placeId, { ...req, variant: v }, signal)).summary; }
      catch (e) { mark('variants', e); return null; }
    };
    tasks.push(Promise.all([one('low'), one('high')]).then(([low, high]) => {
      if (signal?.aborted) return;
      emit({ variants: { ...(low ? { low } : {}), base: run.summary, ...(high ? { high } : {}) } });
    }));
  }
  await Promise.all(tasks);
}

/** Frames of the baseline run (the map's Baseline view), fetched on demand. */
export async function loadBaselineFrames(placeId: string, baselineId: string, days: number, client: SimClient = realClient, signal?: AbortSignal): Promise<FramesDoc> {
  const p = framePlan(days);
  return client.fetchFrames(placeId, baselineId, p.fromH, p.toH, p.stepMin, signal);
}

/** Merge a patch into a bundle (patches replace top-level keys; `down` merges). */
export function mergeBundle(b: SimBundle, patch: Partial<SimBundle>): SimBundle {
  if ('status' in patch && patch.status === 'running' && 'req' in patch) return { ...EMPTY_BUNDLE, ...patch } as SimBundle;
  return { ...b, ...patch, down: patch.down ? { ...b.down, ...patch.down } : b.down };
}
