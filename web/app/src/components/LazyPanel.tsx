import React, { Suspense, lazy } from 'react';
import { PanelBoundary } from './PanelBoundary';
import { Status } from '../ui/Status';
import { useInWindow } from '../windows/context';

/* Boot performance: panels that are not part of the default
 * layout load through a dynamic import, so the desk shell and the
 * panels a first screen needs ship in the entry chunk and the rest lands
 * in chunks fetched on first use (Report Desk, Satellite Desk, Analysis
 * Desks, Live Twin, the HIS / RLS / EXP / SCN tools).
 *
 * Behaviour is unchanged: the wrapper is a plain function component with
 * the same props, so the panel registries (App.tsx, HISTORY_TOOL_PANELS)
 * keep their shape. While the chunk is fetched the slot shows the panel's
 * own code and title with a LOADING stamp; a failed fetch (offline after
 * a deploy) degrades THAT panel to the same boundary note every panel
 * has, never the whole app. */

export interface LazyLabel { code: string; title: string; className?: string }

function Loading({ code, title, className = '' }: LazyLabel) {
  /* Inside a dock window the window chrome carries the title; the fallback is
   * one Status word (no prose, no second header). */
  const inWindow = useInWindow();
  if (inWindow) return <div className="empty-state wm-loading" aria-busy="true"><Status state="meta" word="Loading" /></div>;
  return (
    <section className={`panel panel--loading ${className}`.trim()} aria-busy="true">
      <header className="panel-head">
        <span className="code-chip">{code}</span>
        <span className="panel-title">{title}</span>
        <span className="panel-updated mono">Loading</span>
      </header>
    </section>
  );
}

export function lazyPanel<P extends object>(label: LazyLabel, load: () => Promise<{ default: React.ComponentType<P> }>): React.ComponentType<P> {
  const Inner = lazy(load) as unknown as React.ComponentType<Record<string, unknown>>;
  function LazyPanel(props: P) {
    return (
      <PanelBoundary panelLabel={label.code}>
        <Suspense fallback={<Loading {...label} />}>
          {React.createElement(Inner, props as Record<string, unknown>)}
        </Suspense>
      </PanelBoundary>
    );
  }
  LazyPanel.displayName = `Lazy(${label.code})`;
  return LazyPanel;
}
