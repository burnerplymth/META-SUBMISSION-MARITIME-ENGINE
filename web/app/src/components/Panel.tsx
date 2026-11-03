import React, { useEffect, useRef } from 'react';
import { Icon } from '../ui/Icon';
import { scrollBehavior } from '../ui/motion';
import { useStore } from '../store';
import { scrollRestoreSettling } from '../demoSession';
import { PanelBoundary } from './PanelBoundary';
import { useInWindow, windowMeta } from '../windows/context';
import type { PanelId } from '../types';
import { useExportContext } from '../exports/context';
import { usePanelExportables } from '../exports/panelLift';
import { ExportButton } from '../exports/PanelExportMenu';

export interface ExportSpec {
  csv?: () => Array<Record<string, unknown>>;
  json?: () => unknown;
  pngSvgId?: string;
  /* retrieved_at is OPTIONAL — a pack whose data
   * carries no served retrieval stamp omits the field rather than
   * stamping export time as retrieval time. */
  pack?: () => { data: unknown; provenance: Array<{ file: string; source: string; retrieved_at?: string; mode: string }> };
  /** Standing label written into the lifted export entries; the window default applies when omitted. */
  label?: 'Modelled' | 'Simulated' | 'Recorded' | 'Sourced' | 'Snapshot' | 'Live';
  /** Source line written into the lifted export entries. */
  source?: string;
}

/** Run one export of a panel's spec (the header menu and the window menu share it). */
export async function runPanelExport(id: string, kind: string, exportSpec: ExportSpec | undefined): Promise<void> {
  const ex = await import('../exporter');
  if (kind === 'csv' && exportSpec?.csv) ex.exportCSV(`${id}-export`, exportSpec.csv());
  if (kind === 'json' && exportSpec?.json) ex.exportJSON(`${id}-export`, exportSpec.json());
  if (kind === 'pack' && exportSpec?.pack) { const p = exportSpec.pack(); ex.exportDataPack(`${id}-pack`, p.data, p.provenance); }
  if (kind === 'png' && exportSpec?.pngSvgId) ex.exportPNG(`${id}-export`, document.getElementById(exportSpec.pngSvgId) as unknown as SVGSVGElement);
}

/** A stamp string read as WORD · source: the leading state word never
 *  truncates (it is the part that matters), the source after it does, and the
 *  full text is the tooltip. A stamp with no separator renders as is. */
export function StampText({ text }: { text: string }) {
  const i = text.indexOf(' · ');
  if (i <= 0) return <>{text}</>;
  return <><b className="stamp-w">{text.slice(0, i)}</b><span className="stamp-rest">{text.slice(i)}</span></>;
}

/** ONE panel-header component (shared by every panel): code chip + title + updated
 *  stamp + window controls (⋯ export menu · ⤢ maximize · ✕ close). */
export function Panel({ id, code, title, updated, stamp, exportSpec, action, children, className = '' }:
  { id: PanelId; code: string; title: string; updated?: string;
    /** A header action (a stable component that reads its own state), e.g. the Brief button. */
    action?: React.ReactNode;
    /** A header stamp that is a node (a basis chip with its popover); `updated` stays its text twin. */
    stamp?: React.ReactNode;
    exportSpec?: ExportSpec; children: React.ReactNode; className?: string }) {
  const s = useStore();
  const ref = useRef<HTMLDivElement>(null);
  /* Inside a dock window the window chrome owns the title, the stamp and the
   * export menu: this panel reports them and renders no header of its own. */
  const win = useInWindow();
  useEffect(() => {
    if (!win) return;
    windowMeta.set(id, { updated, stamp, exportSpec, code, action });
  });
  useEffect(() => { if (!win) return; return () => windowMeta.set(id, null); }, [win, id]);
  useEffect(() => {
    if (win) return;
    if (s.focusPanel !== id) return;
    /* Demo-restore race (demoSession.ts): while a demo exit's
     * scroll restore is still settling, a stale beat's focus change
     * can commit this effect late — scrolling now would move the
     * column off the offset the restore is putting back, and win.
     * Skip the scroll; the settle pass has the final word. */
    if (scrollRestoreSettling()) return;
    ref.current?.scrollIntoView({ block: 'nearest', behavior: scrollBehavior() });
  }, [s.focusPanel, id, win]);
  /* Every panel's exportSpec is lifted into the export registry (exports/panelLift.ts). */
  const exportCtx = useExportContext();
  usePanelExportables(id, exportSpec, exportCtx);
  return (
    <section ref={ref} className={`panel ${s.focusPanel === id ? 'panel--focus' : ''} ${className}`} data-panel={id}>
      {!win && <header className="panel-head">
        <span className="code-chip">{code}</span>
        <span className="panel-title" title={title}>{title}</span>
        {stamp ? <span className="panel-updated panel-updated--node mono" title={updated}>{stamp}</span>
          : updated && <span className="panel-updated mono" title={updated}><StampText text={updated} /></span>}
        <span className="panel-controls">
          {action}
          <ExportButton windowId={id} variant="icon" />
          <button type="button" className="icon-btn" title="Maximize" aria-label="Maximize" onClick={() => s.toggleMax(id)}><Icon name="maximize-2" /></button>
          <button type="button" className="icon-btn" title="Close" aria-label="Close" onClick={() => s.closePanel(id)}><Icon name="close" /></button>
        </span>
      </header>}
      <div className="panel-body">
        <PanelBoundary panelLabel={code} resetKeys={[updated ?? '']}>{children}</PanelBoundary>
      </div>
    </section>
  );
}
