/**
 * AGENT CONTROLLER: one object that owns the task loop. It creates a session
 * per task, posts the message, long-polls /steps (after_rev), performs the
 * client actions the server hands to the browser, resolves confirmations and
 * stops the task. React reads its immutable snapshot with useSyncExternalStore
 * (useAgent.ts); a closed Agent window therefore never loses a running task.
 *
 * Nothing runs without the person: a typed or reviewed-spoken message starts
 * a task; a state-changing action waits for Confirm; a model never confirms.
 * All I/O goes through the injected AgentApi (a mocked fetch can be passed).
 */
import { AgentApiError, LONG_POLL_S, type AgentApi, type ArrangeShock } from './api';
import { performClientAction, type ViewDeps } from './clientActions';
import { INITIAL_AGENT_STATE, agentReducer, finalAnswer, unhandledClientActions, type AgentAct, type AgentState } from './reducer';
import { isOpen, isTerminal, type AgentAction, type AgentMessage, type AgentStatusDoc, type AgentStep, type AgentTransport } from './types';

export interface AgentMeta {
  status: AgentStatusDoc | null;
  tasks: string[];
  /** Machine code when the agent routes could not be read (rendered as a state label). */
  error: string | null;
  loaded: boolean;
}
export interface AgentCheckpoint { state: AgentState; handled: Set<string>; logged: Set<string>; answered: Set<string> }
export interface AgentSnapshot { state: AgentState; meta: AgentMeta; origin: 'typed' | 'voice' | null }

export interface ControllerDeps {
  api: AgentApi;
  /** The live view deps (window manager, store); null while no shell is mounted. */
  view: () => ViewDeps | null;
  /** Called once per settled step (the AI activity log feed). */
  onStep?: (step: AgentStep, transport: AgentTransport) => void;
  /** Called once per final assistant message (spoken reply). */
  onAnswer?: (message: AgentMessage, origin: 'typed' | 'voice' | null) => void;
  sleep?: (ms: number) => Promise<void>;
}

const FINAL_STEP = new Set(['ok', 'error', 'rejected', 'cancelled']);
const MAX_POLL_FAILURES = 3;
export const MAX_TASK_CHARS = 2000;

export class AgentController {
  private snap: AgentSnapshot = {
    state: INITIAL_AGENT_STATE, meta: { status: null, tasks: [], error: null, loaded: false }, origin: null,
  };
  private listeners = new Set<() => void>();
  private token = 0;
  private abort: AbortController | null = null;
  private handled = new Set<string>();
  private logged = new Set<string>();
  private answered = new Set<string>();
  private sleep: (ms: number) => Promise<void>;
  /** Resolves when the current poll loop ends. */
  loopDone: Promise<void> = Promise.resolve();

  constructor(private deps: ControllerDeps) {
    this.sleep = deps.sleep ?? ((ms) => new Promise((r) => setTimeout(r, ms)));
  }

  /* ── store plumbing ─────────────────────────────────────────────────── */
  subscribe = (l: () => void): (() => void) => { this.listeners.add(l); return () => { this.listeners.delete(l); }; };
  getSnapshot = (): AgentSnapshot => this.snap;
  private emit(next: AgentSnapshot): void { this.snap = next; this.listeners.forEach((l) => l()); }
  private dispatch(act: AgentAct): void {
    const state = agentReducer(this.snap.state, act);
    if (state !== this.snap.state) this.emit({ ...this.snap, state });
  }
  setDeps(patch: Partial<ControllerDeps>): void { this.deps = { ...this.deps, ...patch }; }

  /* ── meta (status + demo tasks) ─────────────────────────────────────── */
  async loadMeta(): Promise<void> {
    try {
      const [status, tasks] = await Promise.all([this.deps.api.status(), this.deps.api.demoTasks()]);
      this.emit({ ...this.snap, meta: { status, tasks: Array.isArray(tasks.tasks) ? tasks.tasks.filter((t) => typeof t === 'string') : [], error: null, loaded: true } });
    } catch (e) {
      this.emit({ ...this.snap, meta: { status: null, tasks: [], error: codeOf(e), loaded: true } });
    }
  }

  /* ── the task ───────────────────────────────────────────────────────── */
  /** Start a task. Returns false when one is already running or the text is unusable. `arrange` starts the Scenario builder's one-tool task with the shocks on its timeline. */
  async send(text: string, origin: 'typed' | 'voice' = 'typed', arrange?: { timeline: ArrangeShock[] }): Promise<boolean> {
    const t = text.trim();
    const cur = this.snap.state;
    if (!t || t.length > MAX_TASK_CHARS || cur.busy) return false;
    if (cur.status === 'running' || cur.status === 'awaiting_confirmation' || cur.status === 'awaiting_client') return false;
    this.stopLoop();
    this.handled.clear(); this.logged.clear(); this.answered.clear();
    this.emit({ state: { ...INITIAL_AGENT_STATE, busy: true }, meta: this.snap.meta, origin });
    const token = this.token;
    try {
      const created = await this.deps.api.createSession(arrange ? 'scenario_arrange' : undefined);
      if (token !== this.token) return false;
      this.dispatch({ type: 'session', session: created });
      const posted = await this.deps.api.postMessage(created.id, t, arrange?.timeline);
      if (token !== this.token) return false;
      this.dispatch({ type: 'session', session: posted });
      this.dispatch({ type: 'request', on: false });
      this.loopDone = this.loop(created.id, token);
      return true;
    } catch (e) {
      if (token === this.token) {
        this.dispatch({ type: 'request', on: false });
        this.dispatch({ type: 'error', code: codeOf(e) });
      }
      return false;
    }
  }

  async confirm(actionId: string): Promise<void> { await this.resolve(actionId, 'confirm'); }
  async reject(actionId: string): Promise<void> { await this.resolve(actionId, 'reject'); }
  private async resolve(actionId: string, how: 'confirm' | 'reject'): Promise<void> {
    const sid = this.snap.state.sessionId;
    if (!sid || this.snap.state.busy) return;
    // The card goes away at once so a double click cannot resolve it twice.
    this.dispatch({ type: 'request', on: true });
    try {
      const v = how === 'confirm' ? await this.deps.api.confirm(sid, actionId) : await this.deps.api.reject(sid, actionId);
      this.dispatch({ type: 'session', session: v });
    } catch (e) {
      this.dispatch({ type: 'error', code: codeOf(e) });
    } finally {
      this.dispatch({ type: 'request', on: false });
    }
  }

  async stop(): Promise<void> {
    const sid = this.snap.state.sessionId;
    if (!sid) return;
    try { this.dispatch({ type: 'session', session: await this.deps.api.cancel(sid) }); } catch (e) { this.dispatch({ type: 'error', code: codeOf(e) }); }
  }

  /** Forget the task (a new, empty state). The server session is left to expire. */
  reset(): void {
    this.stopLoop();
    this.handled.clear(); this.logged.clear(); this.answered.clear();
    this.emit({ state: INITIAL_AGENT_STATE, meta: this.snap.meta, origin: null });
  }

  /** A copy of the task as it stands, for `restore`. The tour takes one on entry so leaving it hands the viewer their own session back. */
  checkpoint(): AgentCheckpoint {
    return { state: this.snap.state, handled: new Set(this.handled), logged: new Set(this.logged), answered: new Set(this.answered) };
  }
  /** Put a `checkpoint` back. A task that was still running resumes polling; a settled one is shown as it was. */
  restore(cp: AgentCheckpoint): void {
    this.stopLoop();
    this.handled = new Set(cp.handled); this.logged = new Set(cp.logged); this.answered = new Set(cp.answered);
    this.emit({ ...this.snap, state: cp.state });
    const st = cp.state;
    if (st.sessionId && !st.busy && (st.status === 'running' || st.status === 'awaiting_client')) this.loopDone = this.loop(st.sessionId, this.token);
  }

  /* ── the loop ───────────────────────────────────────────────────────── */
  private stopLoop(): void {
    this.token += 1;
    this.abort?.abort();
    this.abort = null;
  }

  private async loop(sid: string, token: number): Promise<void> {
    let failures = 0;
    while (token === this.token) {
      const ctrl = new AbortController();
      this.abort = ctrl;
      try {
        const doc = await this.deps.api.steps(sid, this.snap.state.rev, LONG_POLL_S, ctrl.signal);
        if (token !== this.token) return;
        failures = 0;
        this.dispatch({ type: 'steps', doc });
        this.feedLog(doc.steps, doc.transport);
        await this.performClient(sid, token);
        if (token !== this.token) return;
        // usage, caps and the messages live on the session view
        await this.refresh(sid, token);
        if (isTerminal(this.snap.state.status)) {
          // The session view can reach a terminal state before the poll that carries the
          // last step updates: drain once so no step is left half finished.
          try {
            const last = await this.deps.api.steps(sid, this.snap.state.rev, 0);
            if (token !== this.token) return;
            this.dispatch({ type: 'steps', doc: last });
            this.feedLog(last.steps, last.transport);
          } catch { /* the steps already shown stand */ }
          this.announce();
          return;
        }
      } catch (e) {
        if (token !== this.token) return;
        if (e instanceof AgentApiError && e.code === 'aborted') return;
        failures += 1;
        this.dispatch({ type: 'error', code: codeOf(e) });
        if (failures >= MAX_POLL_FAILURES || (e instanceof AgentApiError && e.status >= 400 && e.status < 500 && e.status !== 429)) return;
        await this.sleep(Math.min(4000, 500 * 2 ** failures));
      }
    }
  }

  private async refresh(sid: string, token: number): Promise<void> {
    try {
      const v = await this.deps.api.getSession(sid);
      if (token === this.token) this.dispatch({ type: 'session', session: v });
    } catch { /* the next poll refreshes it */ }
  }

  /** Perform every client action the server dispatched and report each result. */
  private async performClient(sid: string, token: number): Promise<void> {
    for (const a of unhandledClientActions(this.snap.state, this.handled)) {
      this.handled.add(a.id);
      const result = await this.runOne(a, sid);
      if (token !== this.token) return;
      try { await this.deps.api.clientResult(sid, a.id, result); } catch (e) {
        // 404 / 409: the task moved on (stopped, timed out); nothing to do
        if (!(e instanceof AgentApiError) || e.status < 400 || e.status >= 500) this.dispatch({ type: 'error', code: codeOf(e) });
      }
    }
  }
  private async runOne(a: AgentAction, sid: string) {
    const view = this.deps.view();
    if (!view) return { ok: false, code: 'view_unavailable', summary: 'Workspace not ready' };
    return performClientAction(a.tool, a.arguments, view, { sessionId: sid });
  }

  private feedLog(steps: readonly AgentStep[], transport: AgentTransport): void {
    for (const s of steps) {
      if (!FINAL_STEP.has(s.status) || this.logged.has(s.id)) continue;
      this.logged.add(s.id);
      try { this.deps.onStep?.(s, transport); } catch { /* the log is a convenience */ }
    }
  }

  private announce(): void {
    const m = finalAnswer(this.snap.state);
    if (!m || this.answered.has(m.id) || m.system) return;
    this.answered.add(m.id);
    try { this.deps.onAnswer?.(m, this.snap.origin); } catch { /* speech is a convenience */ }
  }

}

function codeOf(e: unknown): string {
  return e instanceof AgentApiError ? e.code : 'unreachable';
}
