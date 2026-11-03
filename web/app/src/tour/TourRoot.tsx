/**
 * The tour surface: a compact control bar (bottom centre, above the status
 * bar), a caption card beside the featured window and a thin ring around it.
 * The product stays visible and clickable throughout: there is no dimming
 * overlay and the ring and card ignore the pointer except for their own
 * controls. Controls are the shared Rail components (ui/).
 */
import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { useAgent } from '../agent/singleton';
import { useStore } from '../store';
import { Button, IconButton, SegmentedControl, Status, Toolbar, ToolbarSeparator, ToolbarSpacer, type SegmentOption } from '../ui';
import { wm } from '../windows/controller';
import { captureLoadLink, takeLoadLink } from './deepLink';
import { TOUR_SPEEDS, type TourMode, type TourSpeed } from './engine';
import { labelsFor, transportOf } from './labels';
import { notServed, subscribeNotes } from './notes';
import { bindStore, engine, startTour } from './runtime';
import { STEPS_BY_MODE } from './steps';
import './tour.css';

/* read the link at import, before the URL writer rewrites the hash to the keys it knows */
captureLoadLink();

const MODES: readonly SegmentOption<TourMode>[] = [{ value: 'quick', label: 'Quick' }, { value: 'full', label: 'Full' }];
const SPEEDS: readonly SegmentOption<`${TourSpeed}`>[] = TOUR_SPEEDS.map((v) => ({ value: `${v}` as `${TourSpeed}`, label: `${v}x`, name: `Speed ${v}x` }));

interface Box { left: number; top: number; width: number; height: number }

/** The featured element's box: a window id (its whole group, tab bar included) or a CSS selector. */
function targetBox(sp: { window?: string; selector?: string } | undefined): Box | null {
  if (!sp) return null;
  let el: Element | null = null;
  if (sp.window) {
    const w = document.querySelector(`.wm-win[data-wid="${sp.window}"]`);
    el = w?.closest('.dv-groupview, .dv-resize-container') ?? w ?? null;
  } else if (sp.selector) el = document.querySelector(sp.selector);
  if (!el) return null;
  const r = el.getBoundingClientRect();
  if (r.width < 4 || r.height < 4) return null;
  return { left: r.left, top: r.top, width: r.width, height: r.height };
}
const sameBox = (a: Box | null, b: Box | null) => (a === b) || (!!a && !!b && Math.abs(a.left - b.left) < 1 && Math.abs(a.top - b.top) < 1 && Math.abs(a.width - b.width) < 1 && Math.abs(a.height - b.height) < 1);

const isTextEntry = (t: EventTarget | null): boolean => {
  const el = t as HTMLElement | null;
  if (!el || !el.tagName) return false;
  return el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT' || el.isContentEditable;
};
const isControl = (t: EventTarget | null): boolean => {
  const el = t as HTMLElement | null;
  return !!el?.closest?.('button, a[href], [role="button"], [role="tab"], [role="menuitem"], [role="switch"], summary');
};

export function TourRoot() {
  const s = useStore();
  /* bound in a layout effect, not during render; the host reads the latest value when a step applies */
  useLayoutEffect(() => { bindStore(s); });
  const unserved = useSyncExternalStore(subscribeNotes, notServed, notServed);
  const st = useSyncExternalStore(engine.subscribe, engine.getState, engine.getState);
  const agentSnap = useAgent();
  const [menu, setMenu] = useState(false);
  const [box, setBox] = useState<Box | null>(null);
  const [bottom, setBottom] = useState(60);
  const barRef = useRef<HTMLDivElement | null>(null);
  const cardRef = useRef<HTMLElement | null>(null);
  const stRef = useRef(st); stRef.current = st;
  const active = st.status !== 'idle';
  const steps = STEPS_BY_MODE[st.mode];
  const step = active ? steps[Math.min(st.index, steps.length - 1)] : null;

  /* the deep link: once, after the first chain is on screen (or a short wait) */
  const linked = useRef(false);
  useEffect(() => {
    if (linked.current) return;
    const ready = s.chain !== null && wm.state.attached;
    const go = () => { if (linked.current) return; linked.current = true; const l = takeLoadLink(); if (l) startTour(l.mode, l.index); };
    if (ready) { const id = window.setTimeout(go, 400); return () => window.clearTimeout(id); }
    const fallback = window.setTimeout(go, 6000);
    return () => window.clearTimeout(fallback);
  }, [s.chain, wm.state.attached]); // eslint-disable-line react-hooks/exhaustive-deps

  /* keep the bar above the transport and status bars, whatever their height */
  useLayoutEffect(() => {
    if (!active) return;
    const measure = () => {
      const status = document.querySelector('footer.statusbar');
      const above = status?.previousElementSibling as HTMLElement | null;
      const top = (above ?? status)?.getBoundingClientRect().top;
      if (typeof top === 'number') setBottom(Math.max(0, Math.round(window.innerHeight - top)) + 8);
    };
    measure();
    window.addEventListener('resize', measure);
    const id = window.setInterval(measure, 1000);
    return () => { window.removeEventListener('resize', measure); window.clearInterval(id); };
  }, [active]);

  /* follow the featured window while layouts settle */
  useEffect(() => {
    if (!step) { setBox(null); return; }
    const update = () => setBox((prev) => { const next = targetBox(step.spotlight); return sameBox(prev, next) ? prev : next; });
    update();
    const id = window.setInterval(update, 250);
    window.addEventListener('resize', update);
    return () => { window.clearInterval(id); window.removeEventListener('resize', update); };
  }, [step]);

  /* focus: into the bar when the tour opens */
  const wasActive = useRef(false);
  useEffect(() => {
    if (active && !wasActive.current) window.setTimeout(() => barRef.current?.querySelector<HTMLElement>('[data-tour-primary]')?.focus(), 60);
    wasActive.current = active;
  }, [active]);

  /* keyboard: Space play or pause, Left and Right step, Esc exits */
  useEffect(() => {
    if (!active) return;
    const on = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey || e.isComposing) return;
      const t = e.target;
      if (e.key === 'Escape') {
        if ((t as HTMLElement | null)?.closest?.('[data-tour-menu]')) return;
        if (isTextEntry(t) || (t as HTMLElement | null)?.closest?.('.search-drop, .wm-menu, .lm-pop, .pop')) return;
        e.preventDefault(); engine.exit(); return;
      }
      if (isTextEntry(t)) return;
      const inBar = !!(t as HTMLElement | null)?.closest?.('.tour-bar');
      if (e.key === ' ' || e.code === 'Space') {
        if (isControl(t) && !inBar) return;
        if (inBar && isControl(t)) return; // the focused tour button handles its own key
        e.preventDefault(); engine.toggle(); return;
      }
      if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
        if ((t as HTMLElement | null)?.closest?.('.ui-seg, [data-tour-menu], [role="tablist"], .dv-tabs-container')) return;
        e.preventDefault();
        if (e.key === 'ArrowRight') engine.next(); else engine.previous();
      }
    };
    window.addEventListener('keydown', on);
    return () => window.removeEventListener('keydown', on);
  }, [active]);

  useEffect(() => { if (!active) setMenu(false); }, [active]);

  const agentTransport = agentSnap.state.transport ?? (agentSnap.meta.status?.transport_label ?? null);
  const labels = useMemo(() => labelsFor(step?.labels, {
    health: s.health, snapshotMode: s.snapshotMode, runMeta: s.runMeta, llm: transportOf(s.llmStatus), agent: agentTransport,
    ledger: s.ledgerSource === 'live' && s.ledger ? { locked: s.ledger.length, graded: s.ledger.filter((e) => e.grade).length } : null,
    now: Date.now(),
  }), [step, s.health, s.snapshotMode, s.runMeta, s.llmStatus, agentTransport, s.ledger, s.ledgerSource]);

  const shown = useMemo(() => (unserved ? [...labels, { kind: 'notserved' as const, word: 'Not served' }] : labels), [labels, unserved]);
  const go = useCallback((i: number) => { engine.goTo(i); setMenu(false); }, []);

  if (!active || !step) return null;
  const n = st.index + 1;
  const counter = `Step ${n} of ${st.total}`;
  const finished = st.status === 'finished';
  const playing = st.status === 'playing';

  /* the caption card: above the bar, centred on the featured window and kept on screen */
  const vw = typeof window === 'undefined' ? 1280 : window.innerWidth;
  const cardW = Math.min(380, vw - 24);
  const cx = box ? box.left + box.width / 2 : vw / 2;
  const left = Math.max(12, Math.min(vw - cardW - 12, cx - cardW / 2));

  return (
    <div className="tour-root" data-tour-status={st.status} data-tour-step={step.id}>
      {box && (
        <div className={`tour-ring${st.applying ? ' tour-ring--busy' : ''}`} aria-hidden="true"
          style={{ left: box.left - 2, top: box.top - 2, width: box.width + 4, height: box.height + 4 }} />
      )}
      <section ref={cardRef} className="tour-card" role="region" aria-label="Tour step" style={{ left, width: cardW, bottom: bottom + 48 }}>
        <div className="tour-card-head">
          <span className="tour-card-n">{counter}</span>
          {st.applying && <Status state="meta" word="Loading" className="tour-loading" />}
          <span className="tour-card-labels">
            {shown.map((l, i) => <Status key={`${l.word}${i}`} state={l.kind} word={l.word} meta={l.meta} />)}
          </span>
        </div>
        <h2 className="tour-card-t">{step.title}</h2>
        <p className="tour-card-c">{step.caption}</p>
      </section>
      <div className="ui-vh" role="status" aria-live="polite" aria-atomic="true">{`${counter}. ${step.title}. ${step.caption}`}</div>

      <div className="tour-bar" ref={barRef} style={{ bottom }}>
        <Toolbar label="Tour controls" edge="top" className="tour-toolbar">
          <IconButton icon="step-back" label="Previous step" keys="Left" disabled={st.index === 0 && !finished} onClick={() => engine.previous()} />
          <IconButton icon={finished ? 'rotate-ccw' : playing ? 'pause' : 'play'} label={finished ? 'Restart' : playing ? 'Pause' : 'Play'} keys="Space" variant="secondary"
            data-tour-primary="" onClick={() => engine.toggle()} />
          <IconButton icon="step-forward" label="Next step" keys="Right" disabled={finished} onClick={() => engine.next()} />
          <span className="tour-counter" aria-hidden="true">{counter}</span>
          <div className="tour-rail" role="group" aria-label="Steps">
            <span className="tour-rail-fill" style={{ width: `${(n / st.total) * 100}%` }} aria-hidden="true" />
            {steps.map((x, i) => (
              <Button key={x.id} variant="ghost" size="sm" className={`tour-tick${i < st.index ? ' done' : ''}${i === st.index ? ' now' : ''}`}
                aria-label={`Step ${i + 1}: ${x.title}`} aria-current={i === st.index ? 'step' : undefined} title={`${i + 1}. ${x.title}`} onClick={() => go(i)} />
            ))}
          </div>
          <span className="tour-menu-wrap" data-tour-menu="">
            <Button size="sm" icon="list" aria-haspopup="menu" aria-expanded={menu} onClick={() => setMenu((m) => !m)}>Steps</Button>
            {menu && (
              <StepsMenu steps={steps} index={st.index} onPick={go} onClose={() => { setMenu(false); barRef.current?.querySelector<HTMLElement>('[data-tour-menu] button')?.focus(); }} />
            )}
          </span>
          <ToolbarSeparator />
          <SegmentedControl<`${TourSpeed}`> label="Speed" size="sm" value={`${st.speed}`} options={SPEEDS} onChange={(v) => engine.setSpeed(Number(v) as TourSpeed)} />
          <SegmentedControl<TourMode> label="Tour length" size="sm" value={st.mode} options={MODES} onChange={(m) => engine.setMode(m)} />
          <ToolbarSpacer />
          <Button size="sm" icon="close" keys="Esc" onClick={() => engine.exit()}>Exit</Button>
        </Toolbar>
      </div>
    </div>
  );
}

function StepsMenu({ steps, index, onPick, onClose }: { steps: readonly { id: string; title: string }[]; index: number; onPick: (i: number) => void; onClose: () => void }) {
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => { ref.current?.querySelectorAll<HTMLElement>('button')[index]?.focus(); }, [index]);
  useEffect(() => {
    const onDown = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node) && !(e.target as HTMLElement).closest?.('[data-tour-menu]')) onClose(); };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [onClose]);
  const onKey = (e: React.KeyboardEvent) => {
    const items = Array.from(ref.current?.querySelectorAll<HTMLElement>('button') ?? []);
    const at = items.indexOf(document.activeElement as HTMLElement);
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); onClose(); }
    else if (e.key === 'ArrowDown') { e.preventDefault(); items[(at + 1) % items.length]?.focus(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); items[(at - 1 + items.length) % items.length]?.focus(); }
    else if (e.key === 'Home') { e.preventDefault(); items[0]?.focus(); }
    else if (e.key === 'End') { e.preventDefault(); items[items.length - 1]?.focus(); }
  };
  return (
    <div className="tour-menu" role="menu" aria-label="Tour steps" ref={ref} onKeyDown={onKey}>
      {steps.map((x, i) => (
        <Button key={x.id} variant="ghost" size="sm" role="menuitem" className={`tour-menu-item${i === index ? ' on' : ''}`} aria-current={i === index ? 'step' : undefined}
          onClick={() => onPick(i)}><span className="tour-menu-n">{i + 1}</span>{x.title}</Button>
      ))}
    </div>
  );
}
