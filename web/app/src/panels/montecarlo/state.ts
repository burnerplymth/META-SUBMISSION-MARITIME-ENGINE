/* State of the Monte Carlo window: one pure reducer and one polling driver.
 * The driver takes its fetchers and its clock as arguments, so it runs
 * on any fetch with no timers of its own. A recorded fixture is its own source and
 * never shows as a served run. */
import { pollBackoffMs } from '../../api';
import { getAllSamples, getJob, startJob } from './client';
import type { Fetched, McFixture, McJob, McQuote, McRequest, McSample, McStart } from './types';

export type Phase = 'idle' | 'starting' | 'running' | 'done' | 'interrupted' | 'failed' | 'refused' | 'notserved' | 'down';
export type Source = 'server' | 'recorded';

export interface McState {
  phase: Phase;
  source: Source | null;
  jobId: string | null;
  job: McJob | null;
  quote: McQuote | null;
  samples: McSample[] | null;
  /** short machine code of a refusal, a failure or a down route */
  code: string | null;
  /** the recorded fixture's origin note, while source is recorded */
  recordedNote: string | null;
}

export const INITIAL: McState = { phase: 'idle', source: null, jobId: null, job: null, quote: null, samples: null, code: null, recordedNote: null };

export type McAction =
  | { t: 'starting' }
  | { t: 'started'; start: McStart }
  | { t: 'poll'; job: McJob }
  | { t: 'samples'; rows: McSample[] | null }
  | { t: 'refused'; code: string }
  | { t: 'notserved' }
  | { t: 'down'; code: string }
  | { t: 'recorded'; fixture: McFixture }
  | { t: 'attach'; id: string }
  | { t: 'cancelled' }
  | { t: 'reset' };

const phaseOf = (j: McJob): Phase => (j.status === 'done' ? 'done' : j.status === 'interrupted' ? 'interrupted' : j.status === 'failed' ? 'failed' : 'running');

export function reducer(s: McState, a: McAction): McState {
  switch (a.t) {
    case 'starting': return { ...INITIAL, phase: 'starting', source: 'server' };
    case 'started': return { ...s, phase: 'running', source: 'server', jobId: a.start.job_id, quote: a.start.quote, code: null };
    case 'poll': {
      if (s.jobId && a.job.job_id !== s.jobId) return s; // a late answer of a replaced job
      const phase = phaseOf(a.job);
      return { ...s, phase, job: a.job, code: phase === 'failed' ? a.job.error?.code ?? 'failed' : null };
    }
    case 'samples': return { ...s, samples: a.rows };
    case 'refused': return { ...s, phase: 'refused', code: a.code };
    case 'notserved': return { ...s, phase: 'notserved', code: null };
    case 'down': return { ...s, phase: 'down', code: a.code };
    case 'recorded': return {
      phase: 'done', source: 'recorded', jobId: a.fixture.job.job_id, job: a.fixture.job, quote: a.fixture.job.quote ?? null,
      samples: a.fixture.samples, code: null, recordedNote: a.fixture.recorded.note,
    };
    case 'attach': return { ...INITIAL, phase: 'running', source: 'server', jobId: a.id };
    case 'cancelled': case 'reset': return INITIAL;
  }
}

/** Is the job still being worked on (the window shows progress and a cancel action)? */
export const isBusy = (s: McState): boolean => s.phase === 'starting' || s.phase === 'running';

/** Share of the job done, 0..1; 0 when unknown. */
export function progressOf(s: McState): number {
  const p = s.job?.progress;
  if (!p || !(p.n > 0)) return 0;
  return Math.max(0, Math.min(1, p.done / p.n));
}

/* ── quote ────────────────────────────────────────────────────────────── */

export const CONFIRM_ABOVE_S = 120;
/** "about 60 s", "about 2 min": the cost quote as a chip word. */
export function quoteWord(q: McQuote | null | undefined): string | null {
  if (!q || !Number.isFinite(q.est_seconds) || q.est_seconds < 0) return null;
  const s = q.est_seconds;
  if (s < 90) return `about ${Math.max(1, Math.round(s / 5) * 5)} s`;
  return `about ${Math.round(s / 60)} min`;
}
/** A run quoted above two minutes needs an explicit confirmation before it starts. */
export const needsConfirm = (q: McQuote | null | undefined): boolean => !!q && q.est_seconds > CONFIRM_ABOVE_S;

/** The quote before the server answers: n times the per-run time it last measured (or the reference time). */
export function estimateQuote(n: number, sPerRun = 0.12): McQuote { return { n, est_seconds: n * sPerRun, s_per_run: sPerRun, basis: 'default' }; }

/* ── driver ───────────────────────────────────────────────────────────── */

export interface RunDeps {
  startJob: (body: McRequest, signal?: AbortSignal) => Promise<Fetched<McStart>>;
  getJob: (id: string, signal?: AbortSignal) => Promise<Fetched<McJob>>;
  getAllSamples: (id: string, signal?: AbortSignal) => Promise<McSample[] | null>;
  /** resolves after `ms`, or at once when the signal aborts */
  sleep: (ms: number, signal?: AbortSignal) => Promise<void>;
  pollMs: number;
}

export const sleepFor = (ms: number, signal?: AbortSignal): Promise<void> => new Promise((res) => {
  if (signal?.aborted) { res(); return; }
  const t = setTimeout(res, ms);
  signal?.addEventListener('abort', () => { clearTimeout(t); res(); }, { once: true });
});

export const LIVE_DEPS: RunDeps = { startJob, getJob, getAllSamples, sleep: sleepFor, pollMs: 1000 };

function report<T>(r: Fetched<T>, dispatch: (a: McAction) => void): r is Extract<Fetched<T>, { kind: 'ok' }> {
  if (r.kind === 'ok') return true;
  if (r.kind === 'notserved') dispatch({ t: 'notserved' });
  else if (r.kind === 'down') dispatch({ t: 'down', code: r.code });
  else dispatch({ t: 'refused', code: r.code });
  return false;
}

/** Start a job and follow it until it settles. Resolves with the job id (null when it never started).
 *  An aborted signal stops the loop without a state change; the caller owns what that means. */
export async function runJob(body: McRequest, dispatch: (a: McAction) => void, signal: AbortSignal, deps: RunDeps = LIVE_DEPS): Promise<string | null> {
  dispatch({ t: 'starting' });
  const started = await deps.startJob(body, signal);
  if (signal.aborted) return null;
  if (!report(started, dispatch)) return null;
  const id = started.doc.job_id;
  dispatch({ t: 'started', start: started.doc });
  return follow(id, dispatch, signal, deps);
}

/** Poll one job to its end. Failures of the poll itself back off and only give up after five in a row. */
export async function follow(id: string, dispatch: (a: McAction) => void, signal: AbortSignal, deps: RunDeps = LIVE_DEPS): Promise<string | null> {
  let failures = 0;
  for (;;) {
    const r = await deps.getJob(id, signal);
    if (signal.aborted) return null;
    if (r.kind === 'ok') {
      failures = 0;
      dispatch({ t: 'poll', job: r.doc });
      if (r.doc.status === 'done') {
        const rows = await deps.getAllSamples(id, signal);
        if (signal.aborted) return null;
        dispatch({ t: 'samples', rows });
        return id;
      }
      if (r.doc.status !== 'running') return id;
    } else if (r.kind === 'down') {
      failures += 1;
      if (failures >= 5) { dispatch({ t: 'down', code: r.code }); return id; }
    } else {
      report(r, dispatch);
      return id;
    }
    await deps.sleep(pollBackoffMs(failures, deps.pollMs, 8000), signal);
    if (signal.aborted) return null;
  }
}
