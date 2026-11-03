/**
 * RESEARCH REPORT CONTROLLER. One object owns the Report tab's job: create an agent session, give it the task for the
 * open episode and analysis, follow its steps, answer the browser-side view change it asks for, hold the person's
 * confirmations, then read the evidence list and the note, or queue the note and poll the 202 job.
 *
 * Rules it keeps:
 *  - Recorded by default. A session the server makes Live waits for an explicit Confirm before any message is sent,
 *    and shows the model, the session spend limit and every action's estimate. The server's own gates (demo link,
 *    daily cap, kill switch) stay the authority; their codes are shown as state labels.
 *  - Nothing runs without the person: a model never confirms. A pending action is resolved only by Confirm / Reject.
 *  - The documents are untrusted and parsed (reportModel.ts); the text the code check removed is never available.
 * All I/O goes through the injected AgentApi. A page singleton (reportSingleton.ts)
 * keeps a running job alive when the window is closed.
 */
import { AgentApiError, LONG_POLL_S, type AgentApi } from '../../agent/api';
import { INITIAL_AGENT_STATE, agentReducer, unhandledClientActions, type AgentState } from '../../agent/reducer';
import { isTerminal, type ClientResult } from '../../agent/types';
import {
  parseEvidenceDoc, parseReportDoc, recordedTaskFor, type EvidenceRow, type Mode, type ReportDoc,
} from './reportModel';

export type ReportPhase =
  | 'idle' | 'creating' | 'confirm_live' | 'gathering' | 'awaiting_confirmation' | 'gathered' | 'writing' | 'ready' | 'failed';

export interface ReportState {
  phase: ReportPhase;
  sessionId: string | null;
  /** The session this controller created (and drives), or one attached from the Agent window. */
  own: boolean;
  episodeId: string | null;
  agent: AgentState;
  evidence: EvidenceRow[];
  doc: ReportDoc | null;
  /** Machine code of the last failure; shown through reportErrorWord. */
  error: string | null;
}
export interface ReportMeta {
  loaded: boolean;
  /** Transport the server reports for a new session (before the demo gate), null when unknown. */
  transport: Mode | null;
  enabled: boolean | null;
  /** Recorded research task texts the server lists. */
  recordedTasks: string[];
  /** PDF renderer state from the report service; null until read. */
  pdf: boolean | null;
}
export interface ReportSnapshot { state: ReportState; meta: ReportMeta }

export interface BeginRequest {
  episodeId: string;
  /** The task for the session's transport; null = the transport has none for this episode. */
  task: (transport: Mode, recorded: readonly string[]) => string | null;
}

export interface ReportDeps {
  api: AgentApi;
  /** Reads the PDF renderer state (GET /reports/templates); null/throw = not served. */
  pdf?: () => Promise<boolean>;
  sleep?: (ms: number) => Promise<void>;
  /** Poll interval of the note job, ms. */
  pollMs?: number;
}

export const INITIAL_REPORT: ReportState = {
  phase: 'idle', sessionId: null, own: true, episodeId: null, agent: INITIAL_AGENT_STATE, evidence: [], doc: null, error: null,
};

const BUSY: readonly ReportPhase[] = ['creating', 'confirm_live', 'gathering', 'awaiting_confirmation', 'writing'];
export const isBusyPhase = (p: ReportPhase): boolean => BUSY.includes(p);
/** Evidence kinds that are not an analysis: a note needs at least one that is. */
const READS = new Set(['catalog', 'episodes']);
export const hasAnalysis = (rows: readonly EvidenceRow[]): boolean => rows.some((r) => !READS.has(r.kind));

const MAX_POLL_FAILURES = 3;
const MAX_NOTE_POLLS = 200;
const MAX_WATCH_POLLS = 100;

export class ReportController {
  private snap: ReportSnapshot = {
    state: INITIAL_REPORT, meta: { loaded: false, transport: null, enabled: null, recordedTasks: [], pdf: null },
  };
  private listeners = new Set<() => void>();
  private token = 0;
  private abort: AbortController | null = null;
  private handled = new Set<string>();
  private liveTask: string | null = null;
  private sleep: (ms: number) => Promise<void>;
  private pollMs: number;
  /** Resolves when the running job ends. */
  jobDone: Promise<void> = Promise.resolve();

  constructor(private deps: ReportDeps) {
    this.sleep = deps.sleep ?? ((ms) => new Promise((r) => setTimeout(r, ms)));
    this.pollMs = deps.pollMs ?? 700;
  }

  /* ── store plumbing ─────────────────────────────────────────────────── */
  subscribe = (l: () => void): (() => void) => { this.listeners.add(l); return () => { this.listeners.delete(l); }; };
  getSnapshot = (): ReportSnapshot => this.snap;
  private emit(next: ReportSnapshot): void { this.snap = next; this.listeners.forEach((l) => l()); }
  private set(patch: Partial<ReportState>): void { this.emit({ ...this.snap, state: { ...this.snap.state, ...patch } }); }
  private agent(act: Parameters<typeof agentReducer>[1]): void {
    const agent = agentReducer(this.snap.state.agent, act);
    if (agent !== this.snap.state.agent) this.set({ agent });
  }

  /* ── meta ───────────────────────────────────────────────────────────── */
  async loadMeta(): Promise<void> {
    const [status, tasks, pdf] = await Promise.allSettled([
      this.deps.api.status(), this.deps.api.demoTasks(), this.deps.pdf ? this.deps.pdf() : Promise.resolve(false),
    ]);
    const st = status.status === 'fulfilled' ? status.value : null;
    const rt = tasks.status === 'fulfilled' && Array.isArray(tasks.value.research_tasks)
      ? tasks.value.research_tasks.filter((t): t is string => typeof t === 'string') : [];
    this.emit({
      ...this.snap,
      meta: {
        loaded: true, transport: st?.transport_label === 'LIVE' ? 'LIVE' : st ? 'RECORDED' : null,
        enabled: st ? st.enabled !== false : null, recordedTasks: rt, pdf: pdf.status === 'fulfilled' ? pdf.value === true : false,
      },
    });
  }

  /* ── start ──────────────────────────────────────────────────────────── */
  /** Create a session for the open episode. A Recorded session starts at once; a Live one waits for confirmLive(). */
  async begin(req: BeginRequest): Promise<boolean> {
    if (isBusyPhase(this.snap.state.phase)) return false;
    this.stopLoop();
    this.handled.clear();
    this.liveTask = null;
    this.emit({ ...this.snap, state: { ...INITIAL_REPORT, phase: 'creating', episodeId: req.episodeId } });
    const token = this.token;
    try {
      const created = await this.deps.api.createSession();
      if (token !== this.token) return false;
      this.agent({ type: 'session', session: created });
      this.set({ sessionId: created.id });
      const task = req.task(created.transport, this.snap.meta.recordedTasks);
      if (!task) {
        this.set({ phase: 'failed', error: created.transport === 'RECORDED' ? 'no_recorded_report' : 'invalid_request' });
        return false;
      }
      if (created.transport === 'LIVE') {
        this.liveTask = task;
        this.set({ phase: 'confirm_live' });
        return true;
      }
      return await this.post(created.id, task, token);
    } catch (e) {
      if (token === this.token) this.set({ phase: 'failed', error: codeOf(e) });
      return false;
    }
  }

  /** The person confirms a Live run: only now is a message sent to Meta AI. */
  async confirmLive(): Promise<boolean> {
    const st = this.snap.state;
    if (st.phase !== 'confirm_live' || !st.sessionId || !this.liveTask) return false;
    const task = this.liveTask;
    this.liveTask = null;
    this.set({ phase: 'creating' });
    try { return await this.post(st.sessionId, task, this.token); } catch (e) {
      this.set({ phase: 'failed', error: codeOf(e) });
      return false;
    }
  }

  private async post(sid: string, task: string, token: number): Promise<boolean> {
    const posted = await this.deps.api.postMessage(sid, task);
    if (token !== this.token) return false;
    this.agent({ type: 'session', session: posted });
    this.set({ phase: 'gathering', error: null });
    this.jobDone = this.gather(sid, token);
    return true;
  }

  /* ── confirmations ──────────────────────────────────────────────────── */
  async confirm(actionId: string): Promise<void> { await this.resolve(actionId, 'confirm'); }
  async reject(actionId: string): Promise<void> { await this.resolve(actionId, 'reject'); }
  private async resolve(actionId: string, how: 'confirm' | 'reject'): Promise<void> {
    const st = this.snap.state;
    if (!st.sessionId || !st.own || !st.agent.pending.some((p) => p.id === actionId)) return;
    // The card goes away at once so a double click cannot resolve it twice.
    this.set({ agent: { ...st.agent, pending: st.agent.pending.filter((p) => p.id !== actionId) } });
    try {
      const v = how === 'confirm' ? await this.deps.api.confirm(st.sessionId, actionId) : await this.deps.api.reject(st.sessionId, actionId);
      this.agent({ type: 'session', session: v });
      this.set({ phase: 'gathering' });
    } catch (e) {
      this.set({ error: codeOf(e) });
    }
  }

  /** Stop the session and forget it. */
  async cancel(): Promise<void> {
    const sid = this.snap.state.sessionId;
    const own = this.snap.state.own;
    this.reset();
    if (sid && own) { try { await this.deps.api.cancel(sid); } catch { /* the session expires on its own */ } }
  }

  reset(): void {
    this.stopLoop();
    this.handled.clear();
    this.liveTask = null;
    this.emit({ ...this.snap, state: INITIAL_REPORT });
  }

  /* ── the job ────────────────────────────────────────────────────────── */
  private stopLoop(): void { this.token += 1; this.abort?.abort(); this.abort = null; }

  private async gather(sid: string, token: number): Promise<void> {
    let failures = 0;
    while (token === this.token) {
      const ctrl = new AbortController();
      this.abort = ctrl;
      try {
        const doc = await this.deps.api.steps(sid, this.snap.state.agent.rev, LONG_POLL_S, ctrl.signal);
        if (token !== this.token) return;
        failures = 0;
        this.agent({ type: 'steps', doc });
        await this.answerClient(sid, token);
        if (token !== this.token) return;
        try { this.agent({ type: 'session', session: await this.deps.api.getSession(sid) }); } catch { /* the next poll refreshes it */ }
        if (token !== this.token) return;
        const a = this.snap.state.agent;
        if (isTerminal(a.status)) break;
        this.set({ phase: a.status === 'awaiting_confirmation' ? 'awaiting_confirmation' : 'gathering' });
      } catch (e) {
        if (token !== this.token) return;
        if (e instanceof AgentApiError && e.code === 'aborted') return;
        failures += 1;
        if (failures >= MAX_POLL_FAILURES || (e instanceof AgentApiError && e.status >= 400 && e.status < 500 && e.status !== 429)) {
          this.set({ phase: 'failed', error: codeOf(e) });
          return;
        }
        await this.sleep(Math.min(4000, 500 * 2 ** failures));
      }
    }
    if (token === this.token) await this.collect(sid, token);
  }

  /** The browser-side view change the recorded script ends with: the Research window is already open here. */
  private async answerClient(sid: string, token: number): Promise<void> {
    for (const a of unhandledClientActions(this.snap.state.agent, this.handled)) {
      this.handled.add(a.id);
      const r: ClientResult = a.tool === 'open_research'
        ? { ok: true, summary: 'Research window already open' }
        : { ok: false, code: 'unavailable', summary: 'Not available here' };
      if (token !== this.token) return;
      try { await this.deps.api.clientResult(sid, a.id, r); } catch { /* the task moved on */ }
    }
  }

  /** Watch a session another window runs (the Agent window) until it settles, then read it. */
  private async watch(sid: string, token: number): Promise<void> {
    for (let i = 0; i < MAX_WATCH_POLLS && token === this.token; i += 1) {
      try { this.agent({ type: 'session', session: await this.deps.api.getSession(sid) }); } catch (e) {
        if (token === this.token) this.set({ phase: 'failed', error: codeOf(e) });
        return;
      }
      const s = this.snap.state.agent.status;
      if (s === 'none' || isTerminal(s) || s === 'created') break;
      await this.sleep(1200);
    }
    if (token === this.token) await this.collect(sid, token);
  }

  /** Read the evidence list and the note; decide what the tab can offer next. */
  private async collect(sid: string, token: number): Promise<void> {
    try {
      const [ev, rep] = await Promise.all([
        this.deps.api.evidence(sid).then(parseEvidenceDoc, () => null),
        this.deps.api.researchReport(sid).then(parseReportDoc),
      ]);
      if (token !== this.token) return;
      const evidence = ev ?? [];
      if (!rep) { this.set({ evidence, phase: 'failed', error: 'bad_response' }); return; }
      if (rep.state === 'ready' && rep.note) { this.set({ evidence, doc: rep, phase: 'ready', error: null }); return; }
      if (rep.state === 'writing') { this.set({ evidence, doc: rep, phase: 'writing' }); await this.pollNote(sid, token); return; }
      if (hasAnalysis(evidence)) { this.set({ evidence, doc: rep, phase: 'gathered', error: rep.state === 'failed' ? rep.error : null }); return; }
      this.set({ evidence, doc: rep, phase: 'failed', error: 'no_evidence' });
    } catch (e) {
      if (token === this.token) this.set({ phase: 'failed', error: codeOf(e) });
    }
  }

  /** Use the session of another window (open_research from the Agent window). */
  async attach(sessionId: string): Promise<void> {
    const st = this.snap.state;
    if (st.sessionId === sessionId && (st.phase === 'ready' || isBusyPhase(st.phase))) return;
    if (isBusyPhase(st.phase) && st.own) return;
    this.stopLoop();
    this.handled.clear();
    this.emit({ ...this.snap, state: { ...INITIAL_REPORT, phase: 'gathering', sessionId, own: false } });
    const token = this.token;
    this.jobDone = this.watch(sessionId, token);
    await this.jobDone;
  }

  /** The person asks for the note (the click is the confirmation; a Live session shows its estimate first). */
  async writeNote(): Promise<boolean> {
    const st = this.snap.state;
    if (!st.sessionId || !(st.phase === 'gathered' || st.phase === 'failed' || st.phase === 'ready')) return false;
    if (!hasAnalysis(st.evidence)) return false;
    const sid = st.sessionId;
    const token = this.token;
    this.set({ phase: 'writing', error: null });
    try {
      const doc = parseReportDoc(await this.deps.api.writeResearchReport(sid));
      if (token !== this.token) return false;
      if (doc) this.set({ doc });
    } catch (e) {
      if (token === this.token) this.set({ phase: 'gathered', error: codeOf(e) });
      return false;
    }
    this.jobDone = this.pollNote(sid, token);
    await this.jobDone;
    return this.snap.state.phase === 'ready';
  }

  private async pollNote(sid: string, token: number): Promise<void> {
    let none = 0;
    for (let i = 0; i < MAX_NOTE_POLLS && token === this.token; i += 1) {
      try {
        const doc = parseReportDoc(await this.deps.api.researchReport(sid));
        if (token !== this.token) return;
        if (doc) {
          if (doc.state === 'ready' && doc.note) { this.set({ doc, phase: 'ready', error: null }); await this.refreshEvidence(sid, token); return; }
          if (doc.state === 'failed') { this.set({ doc, phase: 'gathered', error: doc.error ?? 'model_error' }); return; }
          if (doc.state === 'none') none += 1;
          if (none > 5) { this.set({ doc, phase: 'gathered', error: 'bad_response' }); return; }
        }
      } catch (e) {
        if (token !== this.token) return;
        this.set({ phase: 'gathered', error: codeOf(e) });
        return;
      }
      await this.sleep(this.pollMs);
    }
    if (token === this.token) this.set({ phase: 'gathered', error: 'timeout' });
  }

  private async refreshEvidence(sid: string, token: number): Promise<void> {
    try {
      const ev = parseEvidenceDoc(await this.deps.api.evidence(sid));
      if (ev && token === this.token) this.set({ evidence: ev });
    } catch { /* the note carries its own evidence table */ }
  }

  /** Recorded script text the server lists for an episode (for the tab). */
  recordedFor(episodeId: string): string | null { return recordedTaskFor(episodeId, this.snap.meta.recordedTasks); }
}

function codeOf(e: unknown): string { return e instanceof AgentApiError ? e.code : 'unreachable'; }
