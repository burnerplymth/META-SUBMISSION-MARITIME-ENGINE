import React, { useSyncExternalStore } from 'react';
import { SearchBox } from '../search/SearchBox';
import { nodeName } from '../seed';
import { useDeskStore } from '../store';
import { canBack, canForward } from '../urlState';
import { Icon } from '../ui/Icon';
import { LayoutsMenu } from './LayoutsMenu';
import { LinkMark, useWmState } from './chrome';
import { wm } from './controller';
import { linkStore } from './linkGroups';
import { WINDOWS, type WindowId } from './registry';
import { layoutCodeFor } from './layoutCode';
import './bar.css';

/** The function-code bar: one button per window code. A lit code is an open
 *  window; click opens or raises it, Shift+click opens it as a float. */
const BAR: readonly WindowId[] = ['map', 'chain', 'news', 'ports', 'sim', 'impact', 'tool-history', 'tool-scenarios', 'tool-exports', 'ledger'];

export function FunctionCodeBar() {
  const s = useDeskStore();
  const st = useWmState();
  const items = BAR.filter((id) => {
    const m = WINDOWS[id].mode;
    return !m || m === s.mode;
  });
  return (
    <nav className="fcb" aria-label="Function codes">
      {items.map((id) => {
        const d = WINDOWS[id];
        const on = st.open.includes(id) && !(id === 'map' && st.mapHidden);
        return (
          <button key={id} type="button" className={`fcb-b${on ? ' on' : ''}`} aria-pressed={on} title={`${d.title}  ${d.code}`}
            onClick={(e) => { if (e.shiftKey) wm.openWindow(id, 'float'); else wm.openWindow(id, 'dock'); }}>
            <span className="fcb-code">{d.code}</span><span className="fcb-title">{d.title}</span>
          </button>
        );
      })}
    </nav>
  );
}

/** Link-group legend: each group and the place it holds. */
export function LinkLegend() {
  const s = useDeskStore();
  const ls = useSyncExternalStore(linkStore.subscribe, linkStore.get, linkStore.get);
  const b = ls.cells.B?.place ?? null;
  const members = (g: 'A' | 'B') => Object.entries(ls.links).filter(([, v]) => v === g).length;
  const place = (id: string | null) => (id ? nodeName(id) : 'none');
  return (
    <span className="lnk-legend" role="group" aria-label="Link groups">
      <span className="lnk-legend-h">Link groups</span>
      <span className="lnk-legend-i"><LinkMark group="A" /><span>{place(s.selectedNode)}</span></span>
      {(b !== null || members('B') > 0) && <span className="lnk-legend-i"><LinkMark group="B" /><span>{place(b)}</span></span>}
    </span>
  );
}

/** Row B in manager mode: history, function codes, the command bar (centre),
 *  link groups and the Layouts menu. */
export function WorkspaceBar() {
  const s = useDeskStore();
  return (
    <header className="subbar cmdbar wsbar">
      <span className="nav-glyphs">
        <button type="button" title="Back" disabled={!canBack(s.historyPos)} aria-label="Back" onClick={s.goBack}><Icon name="back" /></button>
        <button type="button" title="Forward" disabled={!canForward(s.historyPos)} aria-label="Forward" onClick={s.goForward}><Icon name="forward" /></button>
        <button type="button" title="Home" aria-label="Home" onClick={() => { s.setLayout(s.mode === 'sim' ? 'Simulation' : 'SupermAP'); wm.reset(); }}><Icon name="home" /></button>
      </span>
      <FunctionCodeBar />
      <span className="wsbar-centre"><SearchBox fnChip={layoutCodeFor(s.layout)} /></span>
      <LinkLegend />
      <LayoutsMenu />
    </header>
  );
}
