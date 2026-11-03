/* Adapts the existing Panel `exportSpec` (csv, json, pngSvgId, pack) into
 * registry entries, so every panel that already exports shows up in the
 * Export window and the per-panel menu with no change to the panel
 * code. A panel that wants richer entries registers its own
 * under the same ids (`<window>:table`, `<window>:data`, `<window>:view`,
 * `<window>:pack`) and replaces these. */
import { useMemo } from 'react';
import type { ExportSpec } from '../components/Panel';
import { inlineSvgComputedStyles, institutionalThemeFor } from '../exporter';
import { svgToPng } from '../charts/exportDom';
import { objectsToTable, jsonFile, mkFile, rowsFile, type ItemMeta } from './builders';
import { count, headerEntries, windowTitle } from './header';
import { useExportable } from './registry';
import type { ExportContext, ExportDescriptor, ExportLabel, ExportableDef } from './types';
import { ExportError } from './types';

/** Standing label per window when the panel does not state one. Conservative: the
 *  engine's causal chain, impact and planning views are Modelled; the simulator is
 *  Simulated; feeds and boards are a Snapshot of what was served. */
const MODELLED = ['chain', 'chaintrace', 'whatif', 'impact', 'ensemble', 'laneplanner', 'money', 'proof', 'desks', 'explain'];
const SIMULATED = ['sim', 'porttwin', 'tool-scenarios'];
const RECORDED = ['ledger', 'tool-history', 'tool-realism'];
export function defaultLabel(windowId: string): ExportLabel {
  if (MODELLED.includes(windowId)) return 'Modelled';
  if (SIMULATED.includes(windowId)) return 'Simulated';
  if (RECORDED.includes(windowId)) return 'Recorded';
  return 'Snapshot';
}

const cache = new WeakMap<object, { at: number; rows: Array<Record<string, unknown>> }>();
/** The csv getter can be heavy (a whole grid); describe() runs on every render of a list, so keep it for a second. */
function rowsOf(spec: ExportSpec): Array<Record<string, unknown>> {
  if (!spec.csv) return [];
  const hit = cache.get(spec);
  const now = Date.now();
  if (hit && now - hit.at < 1000) return hit.rows;
  const rows = spec.csv();
  cache.set(spec, { at: now, rows });
  return rows;
}

export function liftExportSpec(windowId: string, spec: ExportSpec | undefined, ctx: ExportContext): ExportableDef[] {
  if (!spec) return [];
  const sp: ExportSpec = spec;
  const wt = windowTitle(windowId);
  const prov = (source?: string) => ({ label: sp.label ?? defaultLabel(windowId), source: source ?? sp.source ?? `${wt} window`, run_id: ctx.runId, sim_run_id: ctx.simRunId, as_of: ctx.asOf });
  const out: ExportableDef[] = [];
  const meta = (local: string, title: string, kind: ItemMeta['kind']): ItemMeta => ({ id: `${windowId}:${local}`, windowId, title, kind, prov: prov() });

  if (spec.csv) {
    const m = meta('table', `${wt} table`, 'table');
    out.push({
      ...m, formats: ['csv', 'xlsx', 'html', 'json'],
      describe: (): ExportDescriptor => {
        const r = rowsOf(spec);
        const t = r.length ? Object.keys(r[0]) : [];
        return { summary: r.length ? `${count(r.length, 'row')}, ${count(t.length, 'column')}` : 'No rows', rows: r.length, columns: t, provenance: m.prov, state: r.length ? 'ready' : 'empty' };
      },
      preview: () => {
        const t = objectsToTable(rowsOf(spec).slice(0, 8));
        return { kind: 'table', columns: t.columns, rows: t.rows, total: rowsOf(spec).length };
      },
      build: async (f, o) => {
        const rows = rowsOf(spec);
        if (!rows.length) throw new ExportError('empty');
        if (f === 'json' && spec.json) return jsonFile(m, spec.json(), o);
        const t = objectsToTable(spec.csv!());
        return rowsFile(m, f, t.columns, t.rows, o);
      },
    });
  } else if (spec.json) {
    const m = meta('data', `${wt} data`, 'dataset');
    out.push({
      ...m, formats: ['json'],
      describe: () => { const v = spec.json!(); return { summary: v == null ? 'No data' : 'Structured data', provenance: m.prov, state: v == null ? 'empty' : 'ready' }; },
      build: async (_f, o) => { const v = spec.json!(); if (v == null) throw new ExportError('empty'); return jsonFile(m, v, o); },
    });
  }

  if (spec.pngSvgId) {
    const m = meta('view', `${wt} view`, 'chart');
    const el = (): SVGSVGElement | null => (typeof document === 'undefined' ? null : (document.getElementById(spec.pngSvgId!) as unknown as SVGSVGElement | null));
    out.push({
      ...m, formats: ['svg', 'png'],
      describe: () => ({ summary: 'Current view', provenance: m.prov, state: el() ? 'ready' : 'empty' }),
      build: async (f, o) => {
        const svg = el();
        if (!svg) throw new ExportError('empty');
        const root = document.documentElement; const prev = root.dataset.theme;
        const retro = prev === 'retro-dark' || prev === 'retro-light';
        if (retro) root.dataset.theme = institutionalThemeFor(prev);
        let xml = '';
        try { xml = new XMLSerializer().serializeToString(inlineSvgComputedStyles(svg)); } finally { if (retro && prev) root.dataset.theme = prev; }
        const entries = headerEntries({ title: m.title, kind: 'chart', windowId, prov: m.prov });
        const withDesc = xml.replace(/(<svg\b[^>]*>)/, `$1<desc>${entries.map(([k, v]) => `${k}: ${v}`).join('; ').replace(/[<>&]/g, '')}</desc>`);
        if (f === 'svg') return mkFile(m, 'svg', o, withDesc);
        const r = svg.getBoundingClientRect();
        return mkFile(m, 'png', o, await svgToPng(withDesc, Math.max(1, r.width || 960), Math.max(1, r.height || 540), 2));
      },
    });
  }

  if (spec.pack) {
    const m = meta('pack', `${wt} data pack`, 'dataset');
    out.push({
      ...m, formats: ['json'],
      describe: () => ({ summary: 'Data with a provenance manifest', provenance: m.prov }),
      build: async (_f, o) => {
        const p = spec.pack!();
        return jsonFile(m, { provenance_manifest: p.provenance, data: p.data }, o);
      },
    });
  }
  return out;
}

/** Hook used by Panel: registers the lifted entries for the panel's window. */
export function usePanelExportables(windowId: string, spec: ExportSpec | undefined, ctx: ExportContext): void {
  const defs = useMemo(() => liftExportSpec(windowId, spec, ctx), [windowId, spec, ctx.runId, ctx.simRunId, ctx.asOf]); // eslint-disable-line react-hooks/exhaustive-deps
  // at most four entries per panel; fixed slots keep the hook order stable
  useExportable(defs[0]); useExportable(defs[1]); useExportable(defs[2]); useExportable(defs[3]);
}
