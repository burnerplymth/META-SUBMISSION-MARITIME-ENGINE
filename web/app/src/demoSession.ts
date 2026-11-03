/**
 * Demo-session isolation helpers.
 *
 * The AUTO DEMO director dispatches the same actions a human does,
 * so without isolation it rewrites the live session (chain, runMeta,
 * compare, layout, focus, selection) and the localStorage keys those
 * actions persist. The store snapshots that slice before the demo
 * starts and restores it on ANY exit; the localStorage half of the
 * snapshot lives here as pure functions over a Storage-like object so
 * the byte-identical contract is testable without a DOM.
 */

/** Every localStorage key a tour action can change:
 *  layout (showMoney forces Market Desk), theme and the SIM layer
 *  record (persisted by store effects alongside layout). */
export const DEMO_LOCAL_KEYS = [
  'supermap.layout',
  'supermap.theme',
  'supermap.sim.layers',
  /* The director forces canonical geometry for the run, which
   * rewrites the persisted widths — the snapshot carries the key so
   * the presenter's widths restore byte-identically too. */
  'supermap.colw',
  /* The window manager autosaves the current dock and keeps the
   * user presets in these two keys; the demo forces the canonical layout
   * and restores both byte-identically. */
  'supermap.layout.current',
  'supermap.presets',
  /* The tour opens the Scenario loop window on its compare tab, which the panel remembers. */
  'supermap.scn.tab',
] as const;

export interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

export type LocalSnapshot = Record<string, string | null>;

/** Capture the demo-touchable keys verbatim (null = key absent). */
export function captureLocalSnapshot(storage: StorageLike): LocalSnapshot {
  const snap: LocalSnapshot = {};
  for (const k of DEMO_LOCAL_KEYS) snap[k] = storage.getItem(k);
  return snap;
}

/** Restore byte-identically: a key present before the demo gets its
 *  exact string back; a key absent before the demo is removed, never
 *  left holding a demo-written value. */
export function restoreLocalSnapshot(storage: StorageLike, snap: LocalSnapshot): void {
  for (const k of DEMO_LOCAL_KEYS) {
    const v = snap[k];
    if (v === null || v === undefined) storage.removeItem(k);
    else storage.setItem(k, v);
  }
}

/* ── Item 4: DOM scroll half of the snapshot ──────────────────────
 * Scroll offsets are DOM state, not store state, so the store slice
 * + localStorage snapshot cannot carry them. The demo moves them
 * two ways: Panel.tsx scrollIntoView()s a panel when the director
 * focuses it (explain / whatif / proof / money beats), which scrolls
 * the panel's column; and the demo's layout switches (showMoney
 * forces Market Desk, showProof/showMoney un-close panels) re-stack
 * the columns and clamp/reset their offsets. The scrollables those
 * paths can move, enumerated from the app's actual DOM structure:
 * the three layout columns in App.tsx (.col--left / .col--centre /
 * .col--right — the centre never scrolls in the desktop frame but is
 * captured for the stacked layout), the document scrolling element,
 * and every mounted panel's .panel-body (styles.css: overflow auto),
 * keyed by the panel's stable data-panel id (Panel.tsx). */

export interface ScrollPos { top: number; left: number }
export type ScrollSnapshot = Record<string, ScrollPos>;
export interface ScrollElementLike { scrollTop: number; scrollLeft: number }
export interface ScrollTarget { key: string; el: ScrollElementLike }

interface ScrollNodeLike {
  scrollTop: number; scrollLeft: number;
  getAttribute?: (name: string) => string | null;
  querySelector?: (sel: string) => ScrollNodeLike | null;
}
interface ScrollRootLike {
  querySelector(sel: string): ScrollNodeLike | null;
  querySelectorAll(sel: string): ArrayLike<ScrollNodeLike>;
  scrollingElement?: ScrollNodeLike | null;
}

/** Enumerate the demo-movable scrollables under a root (the live
 *  document in the app; a structural fake elsewhere), each keyed by a
 *  stable identifier that survives the demo's re-renders. */
export function collectScrollTargets(root: ScrollRootLike): ScrollTarget[] {
  const out: ScrollTarget[] = [];
  const cols: Array<[string, string]> = [
    ['col:left', '.col--left'], ['col:right', '.col--right'], ['col:centre', '.col--centre'],
  ];
  for (const [key, sel] of cols) {
    const n = root.querySelector(sel);
    if (n) out.push({ key, el: n });
  }
  if (root.scrollingElement) out.push({ key: 'document', el: root.scrollingElement });
  for (const panel of Array.from(root.querySelectorAll('[data-panel]'))) {
    const id = panel.getAttribute?.('data-panel');
    const body = panel.querySelector?.('.panel-body');
    if (id && body) out.push({ key: `panel:${id}:body`, el: body });
  }
  return out;
}

/** Capture the targets' offsets verbatim, keyed as collected. */
export function captureScrollSnapshot(targets: Iterable<ScrollTarget>): ScrollSnapshot {
  const snap: ScrollSnapshot = {};
  for (const t of targets) snap[t.key] = { top: t.el.scrollTop, left: t.el.scrollLeft };
  return snap;
}

/** Restore the captured offsets. A key whose element is not mounted
 *  at restore time (a panel the pre-demo layout had closed) is
 *  skipped — nothing is invented for it; a target absent from the
 *  snapshot (mounted only by the demo) is left as its fresh mount
 *  rendered it. */
export function restoreScrollSnapshot(targets: Iterable<ScrollTarget>, snap: ScrollSnapshot): void {
  for (const t of targets) {
    const p = snap[t.key];
    if (!p) continue;
    t.el.scrollTop = p.top;
    t.el.scrollLeft = p.left;
  }
}

/* ── The restore / scrollIntoView race ────────────────────────────
 * The rAF passes below are not the last word: a demo beat's focus
 * change can commit its Panel effect AFTER they have drained (the
 * beat loop's last setFocus, an in-flight forkAt's follow-on), and
 * that effect's smooth scrollIntoView then moves a column off the
 * offset the restore just put back — measured in-package as exactly
 * +136 px on one right-column scroller after a Beat-4 abort, with
 * every other scroller restored to the pixel. The restore wins the
 * race two ways: (1) a settle pass re-applies the snapshot once the
 * exit has settled, after any smooth scroll a stale beat set in
 * motion has finished; (2) while a restore is settling,
 * scrollRestoreSettling() reads true and Panel.tsx checks it at
 * call time and skips its focus scroll. A generation counter keeps
 * a superseded restore's settle pass from touching a newer demo's
 * session.
 *
 * The abort-only residual: a COMPLETED demo restores
 * both columns to the pixel, but an abort at Beat 2 left the right
 * column at 427 against a 300 snapshot. The difference is
 * timing, not path — both exits funnel through the same restore.
 * A completion happens minutes in, when every panel's content has
 * long been rendered at its final height; an early abort happens
 * while the app is still filling in (boot slices and panel content
 * landing, heights still growing). After the settle pass, content
 * still growing ABOVE the visible region makes the browser's
 * scroll anchoring move the column's scrollTop to keep its anchor
 * stable — +127 px of late growth lands as +127 px of scroll, with
 * nothing left to re-assert the snapshot. So the settle pass is
 * not the end: a settle WATCH keeps re-asserting the snapshot on a
 * short tick until the offsets have held stable for consecutive
 * ticks (hard-capped), and the Panel gate stays armed for the
 * whole watch. */
let scrollSettleGen = 0;
let scrollSettling = false;

/** True from the moment a scroll restore is scheduled until its
 *  settle pass lands. Checked at call time by the one programmatic
 *  scroller a demo beat can still fire late (Panel.tsx's focus
 *  scrollIntoView). */
export function scrollRestoreSettling(): boolean { return scrollSettling; }

/** How long after the restore the settle pass waits: long enough
 *  for any smooth scrollIntoView a stale beat set in motion to
 *  have run to completion. */
export const SCROLL_SETTLE_MS = 650;

/** Settle-watch cadence and bounds: after the settle
 *  pass, the snapshot is re-asserted every SCROLL_WATCH_MS until
 *  the offsets have held stable for SCROLL_WATCH_CLEAN_TICKS
 *  consecutive ticks, or SCROLL_WATCH_MAX_MS of scheduled watch
 *  time has elapsed (a snapshot offset the restored content can
 *  never reach — clamped by a shorter column — must not pin the
 *  gate forever). */
export const SCROLL_WATCH_MS = 200;
export const SCROLL_WATCH_CLEAN_TICKS = 2;
export const SCROLL_WATCH_MAX_MS = 4000;

/**
 * Restore scroll offsets AFTER the session restore has set the
 * layout state: applying offsets against the demo layout's content
 * height would clamp them, so this applies once immediately (for
 * elements already mounted at their restored size) and again over
 * the next two animation frames, once the restored layout has
 * rendered and the content height is the pre-demo one. The collect
 * callback re-enumerates the DOM on every pass, so panels the
 * layout restore re-mounts are found by the later passes. A settle
 * pass (see the race note above) closes the stale-beat window, and
 * a settle watch then keeps re-asserting the snapshot until the
 * offsets are stable, closing the late-growth anchoring window an
 * early abort leaves open.
 */
export function scheduleScrollRestore(
  snap: ScrollSnapshot,
  collect: () => ScrollTarget[],
  raf?: (cb: () => void) => number,
  later?: (cb: () => void, ms: number) => unknown,
): void {
  const gen = ++scrollSettleGen;
  scrollSettling = true;
  const apply = () => restoreScrollSnapshot(collect(), snap);
  apply();
  const r = raf ?? (typeof requestAnimationFrame !== 'undefined' ? requestAnimationFrame : null);
  if (r) r(() => { apply(); r(apply); });
  const t = later ?? ((cb: () => void, ms: number) => setTimeout(cb, ms));
  t(() => {
    if (gen !== scrollSettleGen) return; // a newer restore owns the settle now
    apply();
    /* Settle watch: drift = any collected target whose
     * offset no longer equals the snapshot. A drifted tick is
     * corrected and restarts the stability count; the watch ends
     * after consecutive clean ticks or the hard cap, and only then
     * does the Panel gate disarm. */
    let cleanTicks = 0;
    let watchElapsed = 0;
    const tick = () => {
      if (gen !== scrollSettleGen) return; // superseded — the newer restore owns the gate
      watchElapsed += SCROLL_WATCH_MS;
      const drifted = collect().some(({ key, el: e }) => {
        const p = snap[key];
        return p !== undefined && (e.scrollTop !== p.top || e.scrollLeft !== p.left);
      });
      if (drifted) { apply(); cleanTicks = 0; } else cleanTicks++;
      if (cleanTicks >= SCROLL_WATCH_CLEAN_TICKS || watchElapsed >= SCROLL_WATCH_MAX_MS) {
        scrollSettling = false;
        return;
      }
      t(tick, SCROLL_WATCH_MS);
    };
    t(tick, SCROLL_WATCH_MS);
  }, SCROLL_SETTLE_MS);
}

/**
 * The director's abort gate. `dead` is checked after EVERY await in
 * the beat loop (see demo.tsx playDemoScript): once killed, no
 * forkAt / setFocus / chain write may land, however late the
 * in-flight promise resolves.
 */
export interface AbortGate {
  readonly dead: boolean;
  kill(): void;
  /** True while the director may still act. */
  check(): boolean;
}

export function createAbortGate(): AbortGate {
  let dead = false;
  return {
    get dead() { return dead; },
    kill() { dead = true; },
    check() { return !dead; },
  };
}
