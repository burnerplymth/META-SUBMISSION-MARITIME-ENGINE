/* The runs held by the window: every finished job (served or recorded) the person has opened in this browser session.
 * Pure helpers; the panel keeps the array. A recorded run never shares a key with a served one. */
import { scenarioName, seedWord } from './fmt';
import type { McJob, McSample } from './types';
import type { Source } from './state';

export interface RunRec { key: string; job: McJob; samples: McSample[] | null; source: Source }

export const MAX_RUNS = 8;
export const runKey = (source: Source, id: string): string => `${source === 'recorded' ? 'rec' : 'srv'}:${id}`;

/** Add or replace a run; the oldest run goes first when the list is full. */
export function addRun(runs: readonly RunRec[], rec: RunRec): RunRec[] {
  const next = runs.filter((r) => r.key !== rec.key);
  next.push(rec);
  return next.slice(-MAX_RUNS);
}
export const removeRun = (runs: readonly RunRec[], key: string): RunRec[] => runs.filter((r) => r.key !== key);

/** "Suez Canal closure 14 d" */
export const runTitle = (r: RunRec, nameOf: (id: string) => string): string => scenarioName(r.job.request, nameOf);
/** "Rev 2 · n 200 · seed 7" */
export function runSub(r: RunRec): string {
  const q = r.job.request;
  return `Rev ${q.engine_rev}${q.profile ? ` ${q.profile}` : ''} · n ${q.n} · seed ${seedWord(q.seed)}`;
}

/** Which two runs to compare when none is chosen: the last two, the older one as A. */
export function defaultPair(runs: readonly RunRec[]): { a: string | null; b: string | null } {
  if (runs.length === 0) return { a: null, b: null };
  if (runs.length === 1) return { a: runs[0].key, b: null };
  return { a: runs[runs.length - 2].key, b: runs[runs.length - 1].key };
}

/** A pair that still names runs on the list; otherwise the default pair. */
export function validPair(runs: readonly RunRec[], a: string | null, b: string | null): { a: string | null; b: string | null } {
  const has = (k: string | null) => !!k && runs.some((r) => r.key === k);
  if (has(a) && has(b) && a !== b) return { a, b };
  return defaultPair(runs);
}

export const finishedWithResults = (j: McJob | null | undefined): j is McJob & { results: NonNullable<McJob['results']> } => !!j && j.status === 'done' && !!j.results;
