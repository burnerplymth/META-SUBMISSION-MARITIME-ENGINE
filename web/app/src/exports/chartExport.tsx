import React, { useCallback, useMemo, useRef, useState } from 'react';
import { ContextMenu, type ContextMenuItem } from '../components/ContextMenu';
import { captionText } from '../charts/serialize';
import type { ChartSpec } from '../charts/types';
import { wm } from '../windows/controller';
import { useInWindow } from '../windows/context';
import { chartFile, chartSvg, provOfChart, type ItemMeta } from './builders';
import { useExportContext } from './context';
import { count } from './header';
import { managerStore, quickExport } from './managerStore';
import { useExportable } from './registry';
import { DEFAULT_OPTIONS, FORMAT_LABEL, type ExportDescriptor, type ExportableDef, type ExportFormat } from './types';

/* Chart-level export for the shared chart kit: every
 * ChartFrame registers its chart with the export registry and offers a
 * context menu (right click, ContextMenu key, Shift+F10): SVG, PNG, CSV of
 * its data, add to the Export selection. */

export const CHART_FORMATS: readonly ExportFormat[] = ['svg', 'png', 'csv', 'json', 'xlsx', 'html'];

export function chartExportable(spec: ChartSpec, windowId: string, frame: { width: number; height: number }, rowCount: () => number): ExportableDef {
  const prov = provOfChart(spec.provenance);
  const meta: ItemMeta = { id: `${windowId}:chart-${spec.id}`, windowId, title: spec.title, kind: 'chart', prov: { ...prov, unit: spec.yUnit ?? null } };
  return {
    id: meta.id, windowId, title: spec.title, kind: 'chart', formats: CHART_FORMATS,
    describe: (): ExportDescriptor => {
      const n = rowCount();
      return { summary: n ? `${count(n, 'plotted row')}${spec.subtitle ? `, ${spec.subtitle}` : ''}` : 'Chart', rows: n || undefined, provenance: meta.prov, state: n === 0 ? 'empty' : 'ready' };
    },
    preview: () => {
      try { return { kind: 'svg', svg: chartSvg(spec, DEFAULT_OPTIONS, frame).svg }; } catch { return null; }
    },
    build: (f, o) => chartFile(meta, f, spec, o, frame),
  };
}

export function useChartExport(spec: ChartSpec, frame: { width: number; height: number }, rowCount: () => number) {
  const win = useInWindow();
  const windowId = win?.id ?? 'charts';
  const ctx = useExportContext();
  const rows = useRef(rowCount); rows.current = rowCount;
  const def = useMemo(() => chartExportable(spec, windowId, frame, () => rows.current()), [spec, windowId, frame.width, frame.height]); // eslint-disable-line react-hooks/exhaustive-deps
  useExportable(def);
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);
  const [word, setWord] = useState<{ ok: boolean; text: string } | null>(null);
  const flash = useCallback((ok: boolean, text: string) => { setWord({ ok, text }); setTimeout(() => setWord(null), 3200); }, []);
  const run = useCallback(async (f: ExportFormat) => {
    const r = await quickExport([def], { [def.id]: [f] }, false, ctx);
    flash(r.ok, r.word);
  }, [def, ctx, flash]);
  const items: ContextMenuItem[] = [
    ...(['svg', 'png', 'csv'] as const).map((f) => ({ key: f, label: `Export ${FORMAT_LABEL[f]}`, icon: 'download' as const, onSelect: () => void run(f) })),
    { key: 'select', label: 'Add to Export selection', icon: 'plus', onSelect: () => { managerStore.dispatch({ t: 'selectMany', defs: [def], on: true }); flash(true, 'Added to Export'); } },
    { key: 'open', label: 'Open Export', icon: 'file-down', onSelect: () => wm.openWindow('expt', 'dock') },
  ];
  const onContextMenu = (e: React.MouseEvent) => { e.preventDefault(); setMenu({ x: e.clientX, y: e.clientY }); };
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ContextMenu' || (e.shiftKey && e.key === 'F10')) {
      e.preventDefault();
      const r = (e.currentTarget as Element).getBoundingClientRect();
      setMenu({ x: r.left + 24, y: r.top + 24 });
    }
  };
  const node = menu ? <ContextMenu x={menu.x} y={menu.y} items={items} label={`Export ${spec.title}`} onClose={() => setMenu(null)} /> : null;
  return { def, onContextMenu, onKeyDown, menu: node, word, caption: captionText(spec.provenance) };
}
