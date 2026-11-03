/**
 * CHAT CONTROLLER: one object that owns the conversation loop. It lists and
 * opens threads, creates one on the first send, posts messages with the
 * attached context chips, long-polls /steps (after_rev) so tool cards appear as
 * they happen, performs the client actions the server hands to the browser,
 * resolves confirmations and stops a turn. React reads its immutable snapshot
 * with useSyncExternalStore (singleton.ts); a closed Chat window therefore
 * never loses a running turn.
 *
 * Nothing runs without the person: a typed or reviewed-spoken message starts a
 * turn; a state-changing action waits for Confirm; a model never confirms. All
 * I/O goes through the injected ChatApi (a fake can be passed).
 */
import { performClientAction, type ViewDeps } from '../agent/clientActions';
import type { AgentMessage, AgentStatusDoc, AgentStep, AgentTransport } from '../agent/types';
import { AgentApiError, LONG_POLL_S, type ChatApi, type ExportFile } from './api';
import { chatReducer, INITIAL_CHAT_STATE, statusOf, working, type ChatAct, type ChatState } from './reducer';
import type { ChatStatusDoc, ContextChip, ExportFormat, ThreadView } from './types';

export interface ChatMeta {
  status: ChatStatusDoc | null;
  tasks: string[];
  suggestions: string[];
  /** Machine code when the agent routes could not be read. */
  error: string | null;
  loaded: boolean;
}
export interface ChatSnapshot { state: ChatState; meta: ChatMeta; origin: 'typed' | 'voice' | null }

export interface ChatDeps {
  api: ChatApi;
  view: () => ViewDeps | null;
  onStep?: (step: AgentStep, transport: AgentTransport) => void;
  onAnswer?: (message: AgentMessage, origin: 'typed' | 'voice' | null) => void;
  /** Hand an exported file to the person (browser download). */
  save?: (file: ExportFile) => void;
  sleep?: (ms: number) => Promise<void>;
}

export const MAX_MESSAGE_CHARS = 2000;
const FINAL_STEP = new Set(['ok', 'error', 'rejected', 'cancelled']);
const MAX_POLL_FAILURES = 3;

export class ChatController {
  private snap: ChatSnapshot = {
    state: INITIAL_CHAT_STATE, meta: { status: null, tasks: [], suggestions: [], error: null, loaded: false }, origin: null,
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

  constructor(private deps: ChatDeps) {
    this.sleep = deps.sleep ?? ((ms) => new Promise((r) => setTimeout(r, ms)));
  }

  /* ── store plumbing ─────────────────────────────────────────────────── */
  subscribe = (l: () => void): (() => void) => { this.listeners.add(l); return () => { this.listeners.delete(l); }; };
  getSnapshot = (): ChatSnapshot => this.snap;
  private emit(next: ChatSnapshot): void { this.snap = next; this.listeners.forEach((l) => l()); }
  private dispatch(act: ChatAct): void {
    const state = chatReducer(this.snap.state, act);
    if (state !== this.snap.state) this.emit({ ...this.snap, state });
  }
  setDeps(patch: Partial<ChatDeps>): void { this.deps = { ...this.deps, ...patch }; }

  /* ── meta and list ──────────────────────────────────────────────────── */
  async loadMeta(): Promise<void> {
    try {
      const [status, tasks] = await Promise.all([this.deps.api.status(), this.deps.api.demoTasks()]);
      const sugg = status.chat?.suggestions;
      this.emit({ ...this.snap, meta: {
        status, error: null, loaded: true,
        tasks: Array.isArray(tasks.tasks) ? tasks.tasks.filter((t) => typeof t === 'string') : [],
        suggestions: Array.isArray(sugg) ? sugg.filter((t) => typeof t === 'string') : [],
      } });
    } catch (e) {
      this.emit({ ...this.snap, meta: { status: null, tasks: [], suggestions: [], error: codeOf(e), loaded: true } });
    }
    await this.refreshList();
  }

  async refreshList(): Promise<void> {
    try {
      const doc = await this.deps.api.list();
      this.dispatch({ type: 'list', threads: Array.isArray(doc.threads) ? doc.threads : [] });
    } catch (e) {
      this.dispatch({ type: 'list_error', code: codeOf(e) });
    }
  }

  /* ── opening, creating, leaving ─────────────────────────────────────── */
  newChat(): void {
    this.stopLoop();
    this.resetMarks();
    this.dispatch({ type: 'draft' });
  }

  async open(id: string): Promise<void> {
    if (this.snap.state.activeId === id && this.snap.state.thread) return;
    this.stopLoop();
    this.resetMarks();
    this.dispatch({ type: 'request', on: true });
    const token = this.token;
    try {
      const v = await this.deps.api.get(id);
      if (token !== this.token) return;
      this.dispatch({ type: 'open', thread: v });
      // a chat that was mid-turn when the page loaded: follow it
      for (const m of v.messages) this.answered.add(m.id);
      if (working(this.snap.state)) this.loopDone = this.loop(id, this.token);
    } catch (e) {
      if (token !== this.token) return;
      this.dispatch({ type: 'error', code: codeOf(e) });
      if (e instanceof AgentApiError && e.status === 404) this.dispatch({ type: 'removed', id });
    } finally {
      if (token === this.token) this.dispatch({ type: 'request', on: false });
    }
  }

  private resetMarks(): void { this.handled.clear(); this.logged.clear(); this.answered.clear(); }

  /* ── the turn ───────────────────────────────────────────────────────── */
  /** Send a message. Returns false when a turn is running or the text is unusable. */
  async send(text: string, opts: { origin?: 'typed' | 'voice'; context?: ContextChip[] } = {}): Promise<boolean> {
    const t = text.trim();
    const st = this.snap.state;
    if (!t || t.length > MAX_MESSAGE_CHARS || st.busy || working(st)) return false;
    this.stopLoop();
    this.dispatch({ type: 'request', on: true });
    this.emit({ ...this.snap, origin: opts.origin ?? 'typed' });
    const token = this.token;
    try {
      let id = this.snap.state.activeId;
      if (!id) {
        const created = await this.deps.api.create(st.draftWeb === null ? {} : { web_search: st.draftWeb });
        if (token !== this.token) return false;
        this.dispatch({ type: 'open', thread: created });
        id = created.id;
      }
      const ctx = (opts.context ?? []).length ? opts.context : undefined;
      const posted = await this.deps.api.post(id, ctx ? { text: t, context: ctx } : { text: t });
      if (token !== this.token) return false;
      this.dispatch({ type: 'view', thread: posted });
      for (const m of posted.messages) if (m.role === 'assistant') this.answered.add(m.id);
      this.loopDone = this.loop(id, token);
      return true;
    } catch (e) {
      if (token === this.token) this.dispatch({ type: 'error', code: codeOf(e) });
      return false;
    } finally {
      if (token === this.token) this.dispatch({ type: 'request', on: false });
    }
  }

  async regenerate(): Promise<void> {
    const id = this.snap.state.activeId;
    if (!id || this.snap.state.busy || working(this.snap.state)) return;
    this.stopLoop();
    this.dispatch({ type: 'request', on: true });
    const token = this.token;
    try {
      const v = await this.deps.api.regenerate(id);
      if (token !== this.token) return;
      this.dispatch({ type: 'view', thread: v });
      for (const m of v.messages) if (m.role === 'assistant') this.answered.add(m.id);
      this.loopDone = this.loop(id, token);
    } catch (e) {
      if (token === this.token) this.dispatch({ type: 'error', code: codeOf(e) });
    } finally {
      if (token === this.token) this.dispatch({ type: 'request', on: false });
    }
  }

  async confirm(actionId: string): Promise<void> { await this.resolve(actionId, 'confirm'); }
  async reject(actionId: string): Promise<void> { await this.resolve(actionId, 'reject'); }
  private async resolve(actionId: string, how: 'confirm' | 'reject'): Promise<void> {
    const id = this.snap.state.activeId;
    if (!id || this.snap.state.busy) return;
    this.dispatch({ type: 'request', on: true });   // the card goes away at once: no double click
    try {
      const v = how === 'confirm' ? await this.deps.api.confirm(id, actionId) : await this.deps.api.reject(id, actionId);
      this.dispatch({ type: 'view', thread: v });
    } catch (e) {
      this.dispatch({ type: 'error', code: codeOf(e) });
    } finally {
      this.dispatch({ type: 'request', on: false });
    }
  }

  /** Stop this message's turn; the chat stays open. */
  async stop(): Promise<void> {
    const id = this.snap.state.activeId;
    if (!id) return;
    try { this.dispatch({ type: 'view', thread: await this.deps.api.cancel(id) }); } catch (e) { this.dispatch({ type: 'error', code: codeOf(e) }); }
  }

  /* ── thread edits ───────────────────────────────────────────────────── */
  async setWeb(on: boolean): Promise<void> {
    const id = this.snap.state.activeId;
    if (!id) { this.dispatch({ type: 'draft_web', on }); return; }
    try { this.dispatch({ type: 'view', thread: await this.deps.api.update(id, { web_search: on }) }); } catch (e) { this.dispatch({ type: 'error', code: codeOf(e) }); }
  }

  async rename(id: string, title: string): Promise<void> {
    const t = title.trim().slice(0, 80);
    if (!t) return;
    try {
      const v = await this.deps.api.update(id, { title: t });
      if (this.snap.state.activeId === id) this.dispatch({ type: 'view', thread: v });
      else await this.refreshList();
    } catch (e) { this.dispatch({ type: 'error', code: codeOf(e) }); }
  }

  async remove(id: string): Promise<void> {
    try {
      if (this.snap.state.activeId === id) this.stopLoop();
      await this.deps.api.remove(id);
      this.dispatch({ type: 'removed', id });
    } catch (e) {
      if (e instanceof AgentApiError && e.status === 404) this.dispatch({ type: 'removed', id });
      else this.dispatch({ type: 'error', code: codeOf(e) });
    }
  }

  dismissContext(keys: string[]): void { this.dispatch({ type: 'dismissed', keys }); }

  /* ── export ─────────────────────────────────────────────────────────── */
  async exportChat(format: ExportFormat, messageId?: string): Promise<boolean> {
    const id = this.snap.state.activeId;
    if (!id) return false;
    try {
      const file = await this.deps.api.exportThread(id, format, messageId);
      this.deps.save?.(file);
      return true;
    } catch (e) {
      this.dispatch({ type: 'error', code: codeOf(e) });
      return false;
    }
  }

  /* ── the loop ───────────────────────────────────────────────────────── */
  private stopLoop(): void {
    this.token += 1;
    this.abort?.abort();
    this.abort = null;
  }

  private async loop(id: string, token: number): Promise<void> {
    let failures = 0;
    while (token === this.token) {
      const ctrl = new AbortController();
      this.abort = ctrl;
      try {
        const doc = await this.deps.api.steps(id, this.snap.state.rev, LONG_POLL_S, ctrl.signal);
        if (token !== this.token) return;
        failures = 0;
        this.dispatch({ type: 'steps', doc });
        this.feedLog(doc.steps as AgentStep[], doc.transport);
        await this.performClient(id, token);
        if (token !== this.token) return;
        await this.refresh(id, token);
        if (!working(this.snap.state)) {
          try {                                  // drain once: no step is left half finished
            const last = await this.deps.api.steps(id, this.snap.state.rev, 0);
            if (token !== this.token) return;
            this.dispatch({ type: 'steps', doc: last });
            this.feedLog(last.steps as AgentStep[], last.transport);
          } catch { /* the steps already shown stand */ }
          this.announce();
          void this.refreshList();
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

  private async refresh(id: string, token: number): Promise<void> {
    try {
      const v = await this.deps.api.get(id);
      if (token === this.token) this.dispatch({ type: 'view', thread: v });
    } catch { /* the next poll refreshes it */ }
  }

  private async performClient(id: string, token: number): Promise<void> {
    const th = this.snap.state.thread;
    for (const a of (th?.client_actions ?? []).filter((x) => x.state === 'dispatched' && !this.handled.has(x.id))) {
      this.handled.add(a.id);
      const view = this.deps.view();
      const result = view ? await performClientAction(a.tool, a.arguments, view, { sessionId: id })
        : { ok: false, code: 'view_unavailable', summary: 'the workspace is not ready' };
      if (token !== this.token) return;
      try { await this.deps.api.clientResult(id, a.id, result); } catch (e) {
        if (!(e instanceof AgentApiError) || e.status < 400 || e.status >= 500) this.dispatch({ type: 'error', code: codeOf(e) });
      }
    }
  }

  private feedLog(steps: readonly AgentStep[], transport: AgentTransport): void {
    for (const s of steps) {
      if (!FINAL_STEP.has(s.status) || this.logged.has(s.id)) continue;
      this.logged.add(s.id);
      try { this.deps.onStep?.(s, transport); } catch { /* the log is a convenience */ }
    }
  }

  private announce(): void {
    const th: ThreadView | null = this.snap.state.thread;
    const m = th?.messages[th.messages.length - 1];
    if (!m || m.role !== 'assistant' || m.system || this.answered.has(m.id)) return;
    this.answered.add(m.id);
    try { this.deps.onAnswer?.(m as unknown as AgentMessage, this.snap.origin); } catch { /* speech is a convenience */ }
  }

  get status() { return statusOf(this.snap.state); }
}

export function codeOf(e: unknown): string {
  return e instanceof AgentApiError ? e.code : 'unreachable';
}

/** Browser download of an exported file (object URL, revoked after the click). */
export function downloadFile(file: ExportFile): void {
  const url = URL.createObjectURL(file.blob);
  try {
    const a = document.createElement('a');
    a.href = url;
    a.download = file.filename;
    a.rel = 'noopener';
    document.body.appendChild(a);
    a.click();
    a.remove();
  } finally {
    setTimeout(() => URL.revokeObjectURL(url), 4000);
  }
}

export type { AgentStatusDoc };
