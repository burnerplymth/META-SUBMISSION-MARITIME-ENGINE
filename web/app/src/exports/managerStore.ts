/* The export manager as a module-level store: the selection survives
 * closing and reopening the window, and the per-panel menu and the chart
 * context menu can add to it. Run results live here too. */
import { useSyncExternalStore } from 'react';
import { safeStorage } from '../store';
import { saveBlob } from '../panels/reports/client';
import { sha256Hex, verifyPack, type VerifyRow } from '../panels/reports/packVerify';
import { exportClock } from './header';
import type { PackManifest as ReportsManifest } from '../panels/reports/model';
import { loadHistory, pushHistory, saveHistory, type HistoryEntry } from './history';
import { failureWord, initialManagerState, reduceManager, runExport, type ExportFailure, type ExportManagerState, type ExportRunProgress, type ManagerAction, type PackManifest } from './manager';
import { sanitisePattern } from './filename';
import type { ExportContext, ExportFormat, ExportResultRow, ExportableDef } from './types';
import { DEFAULT_OPTIONS } from './types';

const OPTIONS_KEY = 'supermap.export.options';

function loadOptions(): Partial<ExportManagerState['options']> & { asPack?: boolean } {
  try {
    const raw = safeStorage.get(OPTIONS_KEY);
    if (!raw) return {};
    const v = JSON.parse(raw) as Record<string, unknown>;
    const out: Partial<ExportManagerState['options']> & { asPack?: boolean } = {};
    for (const k of ['figures', 'provenanceSheet', 'csvHeader'] as const) if (typeof v[k] === 'boolean') out[k] = v[k] as boolean;
    if (v.theme === 'light' || v.theme === 'dark') out.theme = v.theme;
    if (typeof v.filenamePattern === 'string') out.filenamePattern = sanitisePattern(v.filenamePattern);
    if (typeof v.asPack === 'boolean') out.asPack = v.asPack;
    return out;
  } catch { return {}; }
}

export interface RunView {
  busy: boolean; progress: ExportRunProgress | null; rows: ExportResultRow[]; blobs: Map<string, Blob>; errors: ExportFailure[]; manifest: PackManifest | null; verify: VerifyRow[] | null;
}
const idleRun = (): RunView => ({ busy: false, progress: null, rows: [], blobs: new Map(), errors: [], manifest: null, verify: null });

let state: ExportManagerState = (() => { const o = loadOptions(); const s = initialManagerState(); return { ...s, options: { ...DEFAULT_OPTIONS, ...o }, asPack: o.asPack ?? false }; })();
let run: RunView = idleRun();
let history: HistoryEntry[] | null = null;
const subs = new Set<() => void>();
const emit = (): void => subs.forEach((f) => f());

export const managerStore = {
  get: (): ExportManagerState => state,
  getRun: (): RunView => run,
  history: (): HistoryEntry[] => (history ??= loadHistory(safeStorage)),
  subscribe: (f: () => void): (() => void) => { subs.add(f); return () => { subs.delete(f); }; },
  dispatch(a: ManagerAction): void {
    const next = reduceManager(state, a);
    if (next === state) return;
    state = next;
    if (a.t === 'option' || a.t === 'pack') {
      try { safeStorage.set(OPTIONS_KEY, JSON.stringify({ ...state.options, asPack: state.asPack })); } catch { /* storage is a convenience */ }
    }
    emit();
  },
  clearHistory(): void { history = []; saveHistory(safeStorage, history); emit(); },
  /** Reset. */
  reset(): void { state = initialManagerState(); run = idleRun(); history = null; emit(); },
};

/* snapshots must be referentially stable between emits */
const stateSnap = (): ExportManagerState => state;
const runSnap = (): RunView => run;
const histSnap = (): HistoryEntry[] => managerStore.history();
export const useManagerState = (): ExportManagerState => useSyncExternalStore(managerStore.subscribe, stateSnap, stateSnap);
export const useRunView = (): RunView => useSyncExternalStore(managerStore.subscribe, runSnap, runSnap);
export const useExportHistory = (): HistoryEntry[] => useSyncExternalStore(managerStore.subscribe, histSnap, histSnap);

/** Export the current selection. One failed item never stops the rest. */
export async function startExport(items: readonly ExportableDef[], ctx: ExportContext): Promise<RunView> {
  if (run.busy) return run;
  run = { ...idleRun(), busy: true };
  emit();
  try {
    const out = await runExport({
      items, selection: state.selection, options: state.options, asPack: state.asPack, context: ctx,
      onProgress: (p) => { run = { ...run, progress: p }; emit(); },
      deliver: (f) => saveBlob(f.filename, f.blob),
    });
    let verify: VerifyRow[] | null = null;
    if (out.manifest) {
      const zip = [...out.blobs.values()].find((b) => b.type === 'application/zip');
      if (zip) { try { verify = await verifyPack(new Uint8Array(await zip.arrayBuffer()), { files: out.manifest.files, datasets: [] } as unknown as ReportsManifest); } catch { verify = null; } }
    }
    run = { busy: false, progress: run.progress ? { ...run.progress, current: null } : null, rows: out.rows, blobs: out.blobs, errors: out.errors, manifest: out.manifest, verify };
    if (out.rows.length) { history = pushHistory(managerStore.history(), out.rows); saveHistory(safeStorage, history); }
  } catch {
    run = { ...run, busy: false };
  }
  emit();
  return run;
}

export interface QuickResult { ok: boolean; word: string; file?: string }
/** Export a few items now (the per-panel menu, the chart menu) with the manager's options,
 *  without touching the Export window's selection or run view. */
export async function quickExport(defs: readonly ExportableDef[], selection: Record<string, ExportFormat[]>, asPack: boolean, ctx: ExportContext): Promise<QuickResult> {
  try {
    const out = await runExport({ items: defs, selection, options: state.options, asPack, context: ctx, deliver: (f) => saveBlob(f.filename, f.blob) });
    if (out.rows.length) { history = pushHistory(managerStore.history(), out.rows); saveHistory(safeStorage, history); emit(); }
    if (out.rows.length === 0) return { ok: false, word: out.errors[0] ? failureWord(out.errors[0]) : 'Nothing to export' };
    const file = out.rows[0].filename;
    return { ok: out.errors.length === 0, word: out.errors.length ? `${out.rows.length} saved, ${out.errors.length} not exported` : out.rows.length > 1 ? `Saved ${out.rows.length} files` : `Saved ${file}`, file };
  } catch { return { ok: false, word: 'Export failed' }; }
}

export interface PackPdfResult extends QuickResult { pages?: number; reports?: number }
/** One PDF with a contents page that joins the run reports of `defs`. Delivered as a download and recorded in the history. */
export async function packPdf(defs: readonly ExportableDef[], ctx: ExportContext): Promise<PackPdfResult> {
  try {
    const { buildPackPdf, packFileName } = await import('./report');
    const r = await buildPackPdf(defs, state.options);
    const name = packFileName(ctx.runId ?? ctx.simRunId, ctx.asOf);
    const bytes = new Uint8Array(await r.blob.arrayBuffer());
    saveBlob(name, r.blob);
    const row = { id: 'pack-pdf', title: `Run pack PDF, ${r.reports} ${r.reports === 1 ? 'report' : 'reports'}`, format: 'pack' as const, filename: name, bytes: bytes.length, sha256: await sha256Hex(bytes), mime: 'application/pdf', at: exportClock.now() };
    history = pushHistory(managerStore.history(), [row]); saveHistory(safeStorage, history);
    runPack = { name, blob: r.blob, row, pages: r.pages, reports: r.reports };
    emit();
    return { ok: true, word: `Saved ${name}`, file: name, pages: r.pages, reports: r.reports };
  } catch (e) {
    return { ok: false, word: (e as { code?: string })?.code === 'empty' ? 'No reports to join' : 'Export failed' };
  }
}
export interface PackPdfView { name: string; blob: Blob; row: ExportResultRow; pages: number; reports: number }
let runPack: PackPdfView | null = null;
export const packPdfView = (): PackPdfView | null => runPack;
export const usePackPdfView = (): PackPdfView | null => useSyncExternalStore(managerStore.subscribe, packPdfView, packPdfView);
