import React, { useEffect, useMemo, useRef, useState } from 'react';
import { PanelBoundary } from '../components/PanelBoundary';
import { useDeskStore } from '../store';
import { Icon } from '../ui/Icon';
import { MAIN_ID } from '../shell/SkipLink';
import { centerInScroller } from './centerInScroller';
import { WindowBody } from './content';
import { WinFrame } from './WinFrame';
import { InWindowCtx } from './context';
import { LinkScope } from './LinkScope';
import { installFlyGate } from './linkGate';
import { builtinFor, gatedPreset } from './presets';
import { WINDOWS, windowAvailable, type WindowId } from './registry';

/** Phone (max 860 px): the dock is not mounted. One window at a time, in the
 *  preset's phone order, map first; a bottom switcher and a sheet; no float.
 *  The map stays mounted once shown (hidden, never removed). */
export function PhoneWorkspace() {
  const s = useDeskStore();
  const gate = { liveTwin: s.liveTwinEnabled };
  const order: WindowId[] = useMemo(() => {
    const p = gatedPreset(builtinFor(s.layout) ?? builtinFor('SupermAP')!, gate);
    return p.phone.filter((id) => windowAvailable(id, gate));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [s.layout, s.liveTwinEnabled]);
  useEffect(() => installFlyGate(), []);
  const [active, setActive] = useState<WindowId>('map');
  const [sheet, setSheet] = useState(false);
  const [visited, setVisited] = useState<WindowId[]>(['map']);
  const cur = order.includes(active) ? active : 'map';
  useEffect(() => { if (!order.includes(active)) setActive('map'); }, [order, active]);
  useEffect(() => { setVisited((v) => (v.includes(cur) ? v : [...v, cur])); }, [cur]);
  useEffect(() => { const f = s.focusPanel; if (f && (order as string[]).includes(f)) setActive(f as WindowId); }, [s.focusPanel, order]);
  const tabsRef = useRef<HTMLElement>(null);
  /* the switcher scrolls sideways: the active window's tab is brought into view when the window changes */
  /* scrollLeft, not scrollIntoView: scrollIntoView on mount makes the tab the focus navigation starting point, so the first Tab skipped the skip link and the top bar */
  useEffect(() => { const nav = tabsRef.current; const on = nav?.querySelector<HTMLElement>('button.on'); if (nav && on) centerInScroller(nav, on); }, [cur, sheet]);
  const sheetRef = useRef<HTMLDivElement>(null);
  const moreRef = useRef<HTMLButtonElement>(null);
  /* the sheet: focus moves in, Esc and an outside press close it, focus returns to the opener */
  useEffect(() => {
    if (!sheet) return;
    sheetRef.current?.querySelector<HTMLElement>('button')?.focus();
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); setSheet(false); moreRef.current?.focus(); } };
    const down = (e: PointerEvent) => {
      const t = e.target as Node;
      if (sheetRef.current?.contains(t) || moreRef.current?.contains(t)) return;
      setSheet(false);
    };
    window.addEventListener('keydown', key, true);
    window.addEventListener('pointerdown', down);
    return () => { window.removeEventListener('keydown', key, true); window.removeEventListener('pointerdown', down); };
  }, [sheet]);
  const go = (id: WindowId) => { setActive(id); setSheet(false); };
  return (
    <main id={MAIN_ID} tabIndex={-1} className="ph-main" data-phone="1">
      {order.filter((id) => id === 'map' || visited.includes(id)).map((id) => (
        <div key={id} className="ph-win" hidden={id !== cur}>
          <PhoneWindow id={id} />
        </div>
      ))}
      <nav ref={tabsRef} className="ph-tabs" aria-label="Windows">
        {order.map((id) => (
          <button key={id} type="button" className={id === cur ? 'on' : ''} aria-current={id === cur ? 'page' : undefined} onClick={() => go(id)}>
            <Icon name={WINDOWS[id].icon} size="sm" /><span>{WINDOWS[id].title}</span>
          </button>
        ))}
        <button ref={moreRef} type="button" className="ph-more" aria-label="All windows" aria-haspopup="dialog" aria-controls="ph-sheet" aria-expanded={sheet} onClick={() => setSheet((v) => !v)}><Icon name="more" size="sm" /></button>
      </nav>
      {sheet && (
        <div ref={sheetRef} id="ph-sheet" className="ph-sheet" role="dialog" aria-label="Windows">
          {order.map((id) => (
            <button key={id} type="button" className={id === cur ? 'on' : ''} onClick={() => go(id)}>
              <Icon name={WINDOWS[id].icon} size="md" /><span>{WINDOWS[id].title}</span><span className="ph-code">{WINDOWS[id].code}</span>
            </button>
          ))}
        </div>
      )}
    </main>
  );
}

function PhoneWindow({ id }: { id: WindowId }) {
  return (
    <InWindowCtx.Provider value={{ id }}>
      <LinkScope wid={id}>
        <WinFrame id={id}><PanelBoundary panelLabel={WINDOWS[id].code}><WindowBody wid={id} /></PanelBoundary></WinFrame>
      </LinkScope>
    </InWindowCtx.Provider>
  );
}

/** True while the viewport is phone width. */
export function usePhone(): boolean {
  const q = '(max-width: 860px)';
  const [m, setM] = useState(() => typeof window !== 'undefined' && !!window.matchMedia?.(q).matches);
  useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return;
    const mq = window.matchMedia(q);
    const on = () => setM(mq.matches);
    on(); mq.addEventListener?.('change', on);
    return () => mq.removeEventListener?.('change', on);
  }, []);
  return m;
}
