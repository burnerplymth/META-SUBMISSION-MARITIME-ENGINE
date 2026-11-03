import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useDeskStore } from '../store';
import { SearchBox } from '../search/SearchBox';
import { canBack, canForward } from '../urlState';
import { MODE_LABEL, menuLayouts } from './mode';
import { layoutLabel } from '../windows/layoutCode';
import { ToolsMenu } from './ToolsMenu';
import { Icon } from '../ui/Icon';
import { useMenuKeys } from '../ui/menuKeys';
import type { RailEntry } from './rail';

export const FUNCS = ['HOME', 'MAP', 'CHAINS', 'WHAT-IF', 'REPLAY', 'MARKETS', 'LEDGER', 'PROOF'] as const;
export type Func = (typeof FUNCS)[number];
/** The words the function tabs show (the codes above stay the identifiers). */
export const FUNC_LABEL: Record<Func, string> = { HOME: 'Home', MAP: 'Map', CHAINS: 'Chains', 'WHAT-IF': 'What-if', REPLAY: 'Replay', MARKETS: 'Markets', LEDGER: 'Ledger', PROOF: 'Proof' };

/** LAYOUTS menu: the current mode's family first, every other layout
 *  beneath a hairline. Nothing is removed by the mode. */
export function LayoutsMenu({ savedAt }: { savedAt: string | null }) {
  const s = useDeskStore();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);
  const btn = useRef<HTMLButtonElement>(null);
  const pop = useRef<HTMLSpanElement>(null);
  const close = useCallback(() => setOpen(false), []);
  const onMenuKey = useMenuKeys(open, pop, btn, close);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { setOpen(false); btn.current?.focus(); } };
    document.addEventListener('mousedown', onDown); document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('mousedown', onDown); document.removeEventListener('keydown', onKey); };
  }, [open]);
  const { primary, other } = menuLayouts(s.mode, s.layoutNames, s.liveTwinEnabled);
  const item = (n: string) => (
    <button key={n} type="button" role="menuitem" className={s.layout === n ? 'on' : ''}
      onClick={() => { s.setLayout(n); setOpen(false); }}>{layoutLabel(n)}</button>
  );
  return (
    <span className="layouts-wrap" ref={ref}>
      <button ref={btn} type="button" className="layouts-btn mono" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}>Layouts <Icon name="chevron-down" size="xs" /></button>
      {open && (
        <span ref={pop} className="pop layouts-menu mono" role="menu" aria-label="Layouts" onKeyDown={onMenuKey}>
          <span className="pop-h">{MODE_LABEL[s.mode]}</span>
          {primary.map(item)}
          {other.length > 0 && <span className="pop-h pop-sep">Other</span>}
          {other.map(item)}
          <button type="button" className="layouts-reset pop-sep" role="menuitem"
            onClick={() => { s.setLayout(s.mode === 'sim' ? 'Simulation' : 'SupermAP'); s.forceCanonicalGeometry(); setOpen(false); }}>Reset layout</button>
        </span>
      )}
      <span className="saved-stamp mono" title="Saved layout">Saved {savedAt ?? '—'}</span>
    </span>
  );
}

/** Row B, the command bar (32 px): history, function tabs, search,
 *  layouts. The function tabs become the bottom tab bar on a phone. */
export function SubBar({ activeFunc, onFunc, layoutCode, savedAt, tools = [], activeTool = null, onTool }:
  { activeFunc: Func | null; onFunc: (f: Func) => void; layoutCode: string; savedAt: string | null;
    tools?: readonly RailEntry[]; activeTool?: string | null; onTool?: (e: RailEntry) => void }) {
  const s = useDeskStore();
  return (
    <header className="subbar cmdbar">
      <span className="nav-glyphs">
        <button type="button" title="Back" disabled={!canBack(s.historyPos)} aria-label="Back" onClick={s.goBack}><Icon name="back" /></button>
        <button type="button" title="Forward" disabled={!canForward(s.historyPos)} aria-label="Forward" onClick={s.goForward}><Icon name="forward" /></button>
        <button type="button" title="Home layout" aria-label="Home" onClick={() => onFunc('HOME')}><Icon name="home" /></button>
      </span>
      <nav className="func-menu func-tabs" aria-label="Functions">
        {FUNCS.map((f) => <button key={f} type="button" className={activeFunc === f ? 'on' : ''} aria-current={activeFunc === f ? 'page' : undefined}
          onClick={() => onFunc(f)}>{FUNC_LABEL[f]}</button>)}
      </nav>
      {onTool && <ToolsMenu entries={tools} activeTool={activeTool} onPick={onTool} />}
      <SearchBox fnChip={layoutCode} />
      <span className="spacer" />
      <LayoutsMenu savedAt={savedAt} />
    </header>
  );
}
