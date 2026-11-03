/**
 * The tour state machine. Pure: no React, no DOM, no store. Timers, the clock
 * and the step applier are injected, so the machine runs with any
 * clock and applier.
 *
 *   idle -> playing <-> paused -> finished        (exit returns to idle from anywhere)
 *
 * Steps are idempotent by contract: `apply(step)` receives only the step and
 * must put the product in the step's complete target state, whatever ran
 * before. The machine therefore never replays history; jumping forward or
 * back is one `apply` call. Applies are serialised (a new one starts after the
 * previous one returned) and carry an `isStale()` probe so a superseded apply
 * stops waiting.
 */

export type TourStatus = 'idle' | 'playing' | 'paused' | 'finished';
export type TourMode = 'quick' | 'full';
export type TourSpeed = 1 | 1.5 | 2;
export const TOUR_SPEEDS: readonly TourSpeed[] = [1, 1.5, 2];
/** Dwell on a step when it declares none, in ms at 1x. */
export const DEFAULT_DWELL_MS = 8000;

export interface TourTimers {
  setTimeout(fn: () => void, ms: number): unknown;
  clearTimeout(handle: unknown): void;
  now(): number;
}
export const REAL_TIMERS: TourTimers = {
  setTimeout: (fn, ms) => setTimeout(fn, ms),
  clearTimeout: (h) => clearTimeout(h as ReturnType<typeof setTimeout>),
  now: () => Date.now(),
};

export interface TourStepLike { id: string; duration?: number }
export interface ApplyCtx { isStale(): boolean }

export interface TourState {
  status: TourStatus;
  mode: TourMode;
  /** Index into the steps of `mode`; meaningful unless idle. */
  index: number;
  total: number;
  speed: TourSpeed;
  /** True while the current step's target state is being applied. */
  applying: boolean;
}

export interface EngineDeps<S extends TourStepLike> {
  /** Steps per mode. `full` is the reference order: switching modes keeps the nearest step. */
  steps: Readonly<Record<TourMode, readonly S[]>>;
  apply(step: S, index: number, ctx: ApplyCtx): void | Promise<void>;
  /** Called once when the tour leaves for idle (exit); restores the user's session. */
  onExit?(): void | Promise<void>;
  /** Called once before the first step is applied (capture the user's session). */
  onStart?(): void | Promise<void>;
  onError?(e: unknown, step: S): void;
  timers?: TourTimers;
}

const IDLE: TourState = { status: 'idle', mode: 'quick', index: 0, total: 0, speed: 1, applying: false };

export class TourEngine<S extends TourStepLike> {
  private st: TourState;
  private listeners = new Set<() => void>();
  private timers: TourTimers;
  private token = 0;
  private queue: Promise<void> = Promise.resolve();
  private handle: unknown = null;
  /** Dwell left at 1x, and when/at which speed the current timer was armed. */
  private left = 0;
  private armedAt = 0;
  private armedSpeed: TourSpeed = 1;
  private started = false;

  constructor(private deps: EngineDeps<S>) {
    this.timers = deps.timers ?? REAL_TIMERS;
    this.st = { ...IDLE, total: deps.steps.quick.length };
  }

  /* ── reading ─────────────────────────────────────────────────────────── */
  subscribe = (l: () => void): (() => void) => { this.listeners.add(l); return () => { this.listeners.delete(l); }; };
  getState = (): TourState => this.st;
  steps(mode: TourMode = this.st.mode): readonly S[] { return this.deps.steps[mode]; }
  step(): S | null { return this.st.status === 'idle' ? null : this.deps.steps[this.st.mode][this.st.index] ?? null; }
  /** Dwell left on the current step at 1x (ms); the full dwell while none has started. */
  dwellLeft(): number { return this.left; }

  private set(p: Partial<TourState>): void {
    const next = { ...this.st, ...p };
    let same = true;
    for (const k of Object.keys(next) as Array<keyof TourState>) if (next[k] !== this.st[k]) same = false;
    if (same) return;
    this.st = next;
    this.listeners.forEach((l) => l());
  }

  /* ── commands ────────────────────────────────────────────────────────── */
  /** Begin the tour. `paused` opens on the step without auto-advance (a deep link to one step). */
  start(mode: TourMode = 'quick', index = 0, opts: { paused?: boolean } = {}): void {
    if (this.st.status !== 'idle') this.clearTimer();
    const list = this.deps.steps[mode];
    const i = clamp(index, 0, list.length - 1);
    if (!this.started) {
      this.started = true;
      this.queue = this.queue.then(() => this.deps.onStart?.()).catch(() => undefined);
    }
    this.set({ status: opts.paused ? 'paused' : 'playing', mode, index: i, total: list.length });
    this.enter();
  }

  play(): void {
    const s = this.st.status;
    if (s === 'idle') this.start('quick', 0);
    else if (s === 'finished') this.restart();
    else if (s === 'paused') this.resume();
  }
  pause(): void {
    if (this.st.status !== 'playing') return;
    this.freeze();
    this.set({ status: 'paused' });
  }
  resume(): void {
    if (this.st.status !== 'paused') return;
    this.set({ status: 'playing' });
    if (!this.st.applying) this.arm();
  }
  toggle(): void {
    if (this.st.status === 'playing') this.pause(); else this.play();
  }
  next(): void {
    const { status, index, total } = this.st;
    if (status === 'idle' || status === 'finished') return;
    if (index >= total - 1) { this.finish(); return; }
    this.go(index + 1);
  }
  previous(): void {
    const { status, index } = this.st;
    if (status === 'idle') return;
    this.go(Math.max(0, status === 'finished' ? index : index - 1));
  }
  goTo(i: number): void {
    if (this.st.status === 'idle') { this.start(this.st.mode, i, { paused: true }); return; }
    this.go(clamp(i, 0, this.st.total - 1));
  }
  restart(): void {
    if (this.st.status === 'idle') { this.start(this.st.mode, 0); return; }
    this.clearTimer();
    this.set({ status: 'playing', index: 0 });
    this.enter();
  }
  setSpeed(speed: TourSpeed): void {
    if (speed === this.st.speed) return;
    const running = this.st.status === 'playing' && this.handle !== null;
    if (running) this.freeze();
    this.set({ speed });
    if (running) this.arm();
  }
  /** Switch between the Quick and Full lists, keeping the same step or the last step before it. */
  setMode(mode: TourMode): void {
    if (mode === this.st.mode) return;
    if (this.st.status === 'idle') { this.set({ mode, total: this.deps.steps[mode].length }); return; }
    const cur = this.step();
    const full = this.deps.steps.full;
    const rank = (id: string) => full.findIndex((s) => s.id === id);
    const at = cur ? rank(cur.id) : 0;
    const list = this.deps.steps[mode];
    /* The same step when the other list has it, else the last step before it (in Full order). */
    let to = cur ? list.findIndex((s) => s.id === cur.id) : -1;
    if (to < 0) {
      let best = -1;
      list.forEach((s, i) => { const r = rank(s.id); if (r <= at && (best < 0 || r > rank(list[best].id))) best = i; });
      to = best < 0 ? 0 : best;
    }
    this.clearTimer();
    this.set({ mode, total: list.length, index: to, status: this.st.status === 'finished' ? 'paused' : this.st.status });
    this.enter();
  }
  exit(): void {
    if (this.st.status === 'idle') return;
    this.clearTimer();
    this.token += 1;
    this.left = 0;
    this.set({ status: 'idle', index: 0, applying: false });
    if (this.started) {
      this.started = false;
      this.queue = this.queue.then(() => this.deps.onExit?.()).catch(() => undefined);
    }
  }

  /** Resolves when every queued apply (and the exit restore) has settled. */
  idle(): Promise<void> { return this.queue; }

  /* ── internals ───────────────────────────────────────────────────────── */
  private go(i: number): void {
    this.clearTimer();
    this.set({ index: i, status: this.st.status === 'finished' ? 'paused' : this.st.status });
    this.enter();
  }
  private finish(): void {
    this.clearTimer();
    this.token += 1;
    this.set({ status: 'finished', applying: false });
  }
  private enter(): void {
    const step = this.step();
    if (!step) return;
    const mine = ++this.token;
    this.left = step.duration ?? DEFAULT_DWELL_MS;
    this.set({ applying: true });
    const ctx: ApplyCtx = { isStale: () => mine !== this.token };
    this.queue = this.queue.then(async () => {
      if (mine !== this.token) return;
      try { await this.deps.apply(step, this.st.index, ctx); } catch (e) { try { this.deps.onError?.(e, step); } catch { /* ignore */ } }
      if (mine !== this.token) return;
      this.set({ applying: false });
      if (this.st.status === 'playing') this.arm();
    });
  }
  private arm(): void {
    this.clearTimer();
    this.armedAt = this.timers.now();
    this.armedSpeed = this.st.speed;
    this.handle = this.timers.setTimeout(() => {
      this.handle = null;
      this.left = 0;
      if (this.st.status === 'playing') this.next();
    }, Math.max(0, this.left / this.st.speed));
  }
  /** Stop the dwell timer, keeping what is left of the dwell. */
  private freeze(): void {
    if (this.handle !== null) {
      const elapsed = (this.timers.now() - this.armedAt) * this.armedSpeed;
      this.left = Math.max(0, this.left - elapsed);
    }
    this.clearTimer();
  }
  private clearTimer(): void {
    if (this.handle !== null) { this.timers.clearTimeout(this.handle); this.handle = null; }
  }
}

const clamp = (v: number, lo: number, hi: number): number => Math.min(Math.max(Math.trunc(v) || 0, lo), Math.max(lo, hi));
