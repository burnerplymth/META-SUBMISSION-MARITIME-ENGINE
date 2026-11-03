import React, { useCallback, useEffect, useRef } from 'react';
import { DockviewReact, type DockviewReadyEvent, type DockviewTheme, type IDockviewPanelProps } from 'dockview-react';
import 'dockview-react/dist/styles/dockview.css';
import './dock.css';
import { PanelBoundary } from '../components/PanelBoundary';
import { agentView } from '../agent/singleton';
import { makeViewDeps } from '../agent/viewDeps';
import { safeStorage, useDeskStore } from '../store';
import { readLocationUrlState } from '../urlSync';
import { WindowBody } from './content';
import { WinFrame } from './WinFrame';
import { InWindowCtx } from './context';
import { GroupActions, WindowTab, useWmState } from './chrome';
import { wm } from './controller';
import { LinkScope } from './LinkScope';
import { installFlyGate } from './linkGate';
import { WINDOWS, isWindowId, type WindowId } from './registry';
import { snapFloat } from './snap';

/** One dock panel: the window's panel inside its link scope. */
function WindowPanel(props: IDockviewPanelProps) {
  const id = props.api.id;
  if (!isWindowId(id)) return null;
  return (
    <InWindowCtx.Provider value={{ id }}>
      <LinkScope wid={id}>
        <WinFrame id={id}>
          <PanelBoundary panelLabel={WINDOWS[id].code}><WindowBody wid={id} /></PanelBoundary>
        </WinFrame>
      </LinkScope>
    </InWindowCtx.Provider>
  );
}

/* The theme class goes on dockview's shell element, which also wraps the floating
 * overlay host; a className on the inner element left floating windows on the
 * built-in abyss colours (navy bars in the light theme). */
const SUPERMAP_THEME: DockviewTheme = { name: 'supermap', className: 'dockview-theme-supermap' };
const COMPONENTS = { window: WindowPanel };
const TABS = { window: WindowTab };
const KV = { get: (k: string) => safeStorage.get(k), set: (k: string, v: string) => safeStorage.set(k, v), remove: (k: string) => safeStorage.remove(k) };

const ARROW_DIR: Record<string, 'left' | 'right' | 'up' | 'down'> = { ArrowLeft: 'left', ArrowRight: 'right', ArrowUp: 'up', ArrowDown: 'down' };

/** Keyboard: Alt+Shift+F float or dock, Alt+Shift+M
 *  maximize, Alt+Shift+X close, Alt+Shift+Arrows resize a docked window, on the active window. Tab cycling and group
 *  focus are implemented here (dockview's keyboard module is enterprise). */
function useWindowKeys(getActive: () => WindowId | null) {
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      /* dockview's own keyboard navigation is an enterprise module (not used);
       * the same keys are implemented here: F6 / Shift+F6 next and previous
       * window, Ctrl+] and Ctrl+[ next and previous tab. */
      if (e.key === 'F6') { e.preventDefault(); wm.cycleGroup(e.shiftKey ? -1 : 1); return; }
      if (e.ctrlKey && !e.altKey && !e.metaKey && (e.key === ']' || e.key === '[')) { e.preventDefault(); wm.cycleTab(e.key === ']' ? 1 : -1); return; }
      if (!e.altKey || !e.shiftKey || e.ctrlKey || e.metaKey) return;
      const id = getActive();
      if (!id) return;
      if (e.code === 'KeyF') { e.preventDefault(); wm.toggleFloat(id); }
      else if (e.code === 'KeyM') { e.preventDefault(); wm.toggleMaximize(id); }
      else if (e.code === 'KeyX') { e.preventDefault(); wm.closeWindow(id); }
      else if (ARROW_DIR[e.code]) { if (wm.resizeWindow(id, ARROW_DIR[e.code])) e.preventDefault(); }
    };
    window.addEventListener('keydown', on);
    return () => window.removeEventListener('keydown', on);
  }, [getActive]);
}

export function DockWorkspace() {
  const s = useDeskStore();
  const st = useWmState();
  const apiRef = useRef<DockviewReadyEvent['api'] | null>(null);
  const layoutRef = useRef(s.layout); layoutRef.current = s.layout;

  const onReady = useCallback((e: DockviewReadyEvent) => {
    apiRef.current = e.api;
    wm.attach(e.api, KV, { liveTwin: s.liveTwinEnabled });
    const u = readLocationUrlState();
    wm.restoreCurrent(layoutRef.current, u.layout === null && u.preset === null);
    if (u.preset) void wm.applyCode(u.preset);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => () => wm.detach(), []);
  useEffect(() => installFlyGate(), []);
  // Agent UI control: the agent acts on the live store through this factory.
  const sRef = useRef(s); sRef.current = s;
  useEffect(() => { agentView.set(() => makeViewDeps(sRef.current)); return () => agentView.set(null); }, []);

  // The store's layout name drives the manager (setLayout, URL, search, demo).
  useEffect(() => {
    if (!st.attached) return;
    if (s.layout !== wm.state.legacy) wm.applyLegacy(s.layout);
  }, [s.layout, st.attached]);
  // A user preset or a link maps back onto its legacy layout name.
  useEffect(() => {
    if (st.attached && st.legacy !== s.layout) s.setLayout(st.legacy);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [st.legacy, st.epoch]);
  // LIVE TWIN law.
  useEffect(() => { wm.setGate({ liveTwin: s.liveTwinEnabled }); }, [s.liveTwinEnabled]);
  // focus: open and raise the window the store names (URL focus=, search, desk jumps).
  useEffect(() => {
    if (!st.attached || !s.focusPanel || !isWindowId(s.focusPanel)) return;
    wm.openWindow(s.focusPanel);
  }, [s.focusPanel, st.attached]);

  useWindowKeys(useCallback(() => {
    const p = apiRef.current?.activePanel?.id;
    return p && isWindowId(p) ? p : null;
  }, []));

  return (
    <div className="wm-dock">
      <DockviewReact
        theme={SUPERMAP_THEME}
        components={COMPONENTS}
        tabComponents={TABS}
        defaultTabComponent={WindowTab}
        rightHeaderActionsComponent={GroupActions}
        onReady={onReady}
        defaultRenderer="always"
        floatingGroupBounds="boundedWithinViewport"
        floatingGroupDragHandle="tabbar"
        transformFloatingGroupDrag={(c) => snapFloat({ proposed: c.proposed, container: c.container, others: c.others, alt: c.modifiers.altKey })}
        singleTabMode="default"
        getTabContextMenuItems={({ panel }) => (panel.id === 'map' ? ['float', 'maximize'] : ['float', 'maximize', 'close'])}
      />
    </div>
  );
}
