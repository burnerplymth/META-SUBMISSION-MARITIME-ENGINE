import React from 'react';
import { useDeskStore } from '../store';
import type { PanelId } from '../types';
import type { ShellMode } from './mode';

/** Inspector tab strip: jump to the existing
 *  right-column panels that are visible in the current layout. No new
 *  panel is created; a tab whose panel is closed or absent is not shown. */
export const INSPECTOR_TABS: Record<ShellMode, ReadonlyArray<[string, PanelId]>> = {
  live: [['Detail', 'mapcontent'], ['Chain', 'chain'], ['Exposures', 'money'], ['Impact', 'impact']],
  sim: [['Run', 'sim'], ['Chain', 'chain'], ['What-if', 'whatif'], ['Impact', 'impact'], ['Detail', 'mapcontent']],
};

export function inspectorTabsFor(mode: ShellMode, visible: readonly PanelId[]): Array<[string, PanelId]> {
  return INSPECTOR_TABS[mode].filter(([, id]) => visible.includes(id)) as Array<[string, PanelId]>;
}

export function InspectorTabs() {
  const s = useDeskStore();
  const tabs = inspectorTabsFor(s.mode, s.visible);
  if (tabs.length < 2) return null;
  return (
    <nav className="inspector-tabs mono" aria-label="Inspector">
      {tabs.map(([label, id]) => (
        <button key={id} type="button" className={s.focusPanel === id ? 'on' : ''} aria-pressed={s.focusPanel === id}
          onClick={() => s.setFocus(id)}>{label}</button>
      ))}
    </nav>
  );
}
