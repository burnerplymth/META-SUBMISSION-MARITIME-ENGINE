/* Export manager: selection state as a pure reducer, the
 * pack manifest builder, and the runner. No React, no DOM; the browser
 * download is injected so the runner is testable in node. */
import { sha256Hex } from '../panels/reports/packVerify';
import { exportClock } from './header';
import { fileNameFor, uniqueNames, sanitisePattern } from './filename';
import { writeZip } from './zipWrite';
import { escHtml, windowTitle } from './header';
import { htmlPage } from './builders';
import { creditText, creditsFor, sourceLineFor } from '../sources/credits';
import {
  DEFAULT_OPTIONS, ERROR_WORD, ExportError,
  type ExportContext, type ExportErrorCode, type ExportFile, type ExportFormat, type ExportKind, type ExportOptions, type ExportResultRow, type ExportableDef,
} from './types';

/* ── state ──────────────────────────────────────────────────────────── */
export interface ExportRunProgress {
  total: number; done: number; current: string | null; errors: ExportFailure[];
}
export interface ExportFailure { id: string; title: string; format: ExportFormat; code: ExportErrorCode }

export interface ExportManagerState {
  /** ids in insertion order -> chosen formats */
  selection: Record<string, ExportFormat[]>;
  options: ExportOptions;
  /** combine everything into one ZIP with manifest.json and sha256 */
  asPack: boolean;
  focus: string | null;
  query: string;
}

export const initialManagerState = (): ExportManagerState => ({
  selection: {}, options: { ...DEFAULT_OPTIONS }, asPack: false, focus: null, query: '',
});

/** The format a newly ticked item starts with. */
export function defaultFormat(kind: ExportKind, formats: readonly ExportFormat[]): ExportFormat | null {
  const pref: Record<ExportKind, ExportFormat[]> = {
    chart: ['svg', 'png', 'csv'], table: ['csv', 'xlsx', 'json'], run: ['csv', 'json', 'xlsx'], plan: ['csv', 'json', 'xlsx'],
    document: ['html', 'pdf', 'md', 'json'], dataset: ['csv', 'xlsx', 'json'], media: ['png', 'svg', 'json'], report: ['pdf', 'html'],
  };
  return pref[kind].find((f) => formats.includes(f)) ?? formats[0] ?? null;
}

export type ManagerAction =
  | { t: 'toggle'; def: Pick<ExportableDef, 'id' | 'kind' | 'formats'> }
  | { t: 'setFormats'; id: string; formats: ExportFormat[] }
  | { t: 'toggleFormat'; id: string; format: ExportFormat; allowed: readonly ExportFormat[] }
  | { t: 'selectMany'; defs: Array<Pick<ExportableDef, 'id' | 'kind' | 'formats'>>; on: boolean }
  | { t: 'prune'; ids: readonly string[] }
  | { t: 'clear' }
  | { t: 'focus'; id: string | null }
  | { t: 'query'; q: string }
  | { t: 'option'; patch: Partial<ExportOptions> }
  | { t: 'pack'; on: boolean };

export function reduceManager(s: ExportManagerState, a: ManagerAction): ExportManagerState {
  switch (a.t) {
    case 'toggle': {
      if (s.selection[a.def.id]) { const { [a.def.id]: _drop, ...rest } = s.selection; return { ...s, selection: rest }; }
      const f = defaultFormat(a.def.kind, a.def.formats);
      return f ? { ...s, selection: { ...s.selection, [a.def.id]: [f] }, focus: a.def.id } : s;
    }
    case 'setFormats': {
      if (!s.selection[a.id]) return s;
      if (a.formats.length === 0) { const { [a.id]: _drop, ...rest } = s.selection; return { ...s, selection: rest }; }
      return { ...s, selection: { ...s.selection, [a.id]: dedupe(a.formats) } };
    }
    case 'toggleFormat': {
      if (!a.allowed.includes(a.format)) return s;
      const cur = s.selection[a.id] ?? [];
      const next = cur.includes(a.format) ? cur.filter((f) => f !== a.format) : [...cur, a.format];
      // ticking a format selects the item; clearing the last format deselects it
      if (next.length === 0) { const { [a.id]: _drop, ...rest } = s.selection; return { ...s, selection: rest }; }
      return { ...s, selection: { ...s.selection, [a.id]: next } };
    }
    case 'selectMany': {
      const sel = { ...s.selection };
      for (const d of a.defs) {
        if (a.on) { if (!sel[d.id]) { const f = defaultFormat(d.kind, d.formats); if (f) sel[d.id] = [f]; } } else delete sel[d.id];
      }
      return { ...s, selection: sel };
    }
    case 'prune': {
      const keep = new Set(a.ids);
      const entries = Object.entries(s.selection).filter(([id]) => keep.has(id));
      return entries.length === Object.keys(s.selection).length ? s : { ...s, selection: Object.fromEntries(entries) };
    }
    case 'clear': return { ...s, selection: {} };
    case 'focus': return s.focus === a.id ? s : { ...s, focus: a.id };
    case 'query': return { ...s, query: a.q };
    case 'option': {
      const o = { ...s.options, ...a.patch };
      if (typeof a.patch.filenamePattern === 'string') o.filenamePattern = a.patch.filenamePattern;
      return { ...s, options: o };
    }
    case 'pack': return { ...s, asPack: a.on };
    default: return s;
  }
}
const dedupe = <T,>(a: readonly T[]): T[] => [...new Set(a)];

export const selectedCount = (s: ExportManagerState): number => Object.keys(s.selection).length;
export const fileCount = (s: ExportManagerState): number => Object.values(s.selection).reduce((n, f) => n + f.length, 0);

/** Case-insensitive match on title, window title and format names. */
export function matchesQuery(q: string, hay: ReadonlyArray<string>): boolean {
  const terms = q.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (!terms.length) return true;
  const text = hay.join(' ').toLowerCase();
  return terms.every((t) => text.includes(t));
}

/* ── manifest ───────────────────────────────────────────────────────── */
export const PACK_SCHEMA = 'exports-bundle/1';

export interface ProducedFile {
  item: Pick<ExportableDef, 'id' | 'title' | 'windowId' | 'kind'>;
  label: string; source: string; run_id?: string | null; as_of?: string | null;
  file: ExportFile; bytes: Uint8Array; sha256: string;
}

const DIR: Record<ExportKind, string> = { chart: 'figures', table: 'tables', run: 'runs', plan: 'runs', document: 'documents', media: 'media', dataset: 'data', report: 'reports' };

export interface PackManifest {
  pack: 'supermap-export-bundle'; schema: typeof PACK_SCHEMA; generated_at: string;
  options: Omit<ExportOptions, 'filenamePattern'> & { filenamePattern: string };
  context: ExportContext;
  items: Array<{ id: string; title: string; kind: ExportKind; window: string; label: string; source: string; run_id: string | null; as_of: string | null; files: string[] }>;
  files: Record<string, { bytes: number; sha256: string }>;
  /** The credit lines of every source named by an item (never empty). */
  credits: string[];
  errors: ExportFailure[];
}

/** Distinct credit lines for the items of a pack; the stored-data line when none names a registered source. */
export function packCredits(items: ReadonlyArray<{ title: string; source: string }>): string[] {
  const out = new Set<string>();
  for (const it of items) for (const c of creditsFor(it.source, it.title)) out.add(creditText(c));
  return out.size ? [...out] : [sourceLineFor(...items.map((i) => i.source))];
}

export function buildManifest(produced: ReadonlyArray<ProducedFile & { path: string }>, o: ExportOptions, ctx: ExportContext, errors: ExportFailure[]): PackManifest {
  const items = new Map<string, PackManifest['items'][number]>();
  const files: PackManifest['files'] = {};
  for (const p of produced) {
    files[p.path] = { bytes: p.bytes.length, sha256: p.sha256 };
    const it = items.get(p.item.id) ?? { id: p.item.id, title: p.item.title, kind: p.item.kind, window: p.item.windowId, label: p.label, source: p.source, run_id: p.run_id ?? null, as_of: p.as_of ?? null, files: [] };
    it.files.push(p.path);
    items.set(p.item.id, it);
  }
  return {
    pack: 'supermap-export-bundle', schema: PACK_SCHEMA, generated_at: exportClock.now(),
    options: { ...o, filenamePattern: sanitisePattern(o.filenamePattern) }, context: ctx,
    items: [...items.values()], files: Object.fromEntries(Object.entries(files).sort(([a], [b]) => (a < b ? -1 : 1))),
    credits: packCredits([...items.values()]), errors,
  };
}

/** index.html of a pack: every item with its label, source and run, charts inline as SVG, the member files with their sha256.
 *  Uses only what is in the pack; the page carries no clock apart from the manifest's generated_at. */
export function packIndexHtml(produced: ReadonlyArray<ProducedFile & { path: string }>, o: ExportOptions, ctx: ExportContext): string {
  const byItem = new Map<string, Array<ProducedFile & { path: string }>>();
  for (const p of produced) byItem.set(p.item.id, [...(byItem.get(p.item.id) ?? []), p]);
  const sections = [...byItem.values()].map((files) => {
    const it = files[0];
    const svg = o.figures ? files.find((f) => f.file.format === 'svg') : undefined;
    const meta = [['Label', it.label], ['Source', it.source], ['Credits', sourceLineFor(it.source, it.item.title)], ['Run', it.run_id ?? ''], ['As of', it.as_of ?? ''], ['Window', windowTitle(it.item.windowId)]]
      .filter(([, v]) => v).map(([k, v]) => `<tr><th scope="row">${escHtml(k)}</th><td>${escHtml(v)}</td></tr>`).join('');
    const fig = svg ? `<figure>${new TextDecoder().decode(svg.bytes).replace(/^<\?xml[^>]*\?>\s*/, '')}</figure>` : '';
    const list = files.map((f) => `<li><a href="${escHtml(f.path)}">${escHtml(f.path)}</a> <span class="cap">${f.bytes.length.toLocaleString('en-US')} bytes, sha256 ${f.sha256}</span></li>`).join('');
    return `<section><h2>${escHtml(it.item.title)}</h2><table class="hdr" aria-label="Item information">${meta}</table>${fig}<ul>${list}</ul></section>`;
  });
  const head = `<h1>Export pack</h1><table class="hdr" aria-label="Pack information">${[['Run', ctx.runId ?? ''], ['Simulation run', ctx.simRunId ?? ''], ['Items', String(byItem.size)], ['Files', String(produced.length)]]
    .filter(([, v]) => v).map(([k, v]) => `<tr><th scope="row">${escHtml(k)}</th><td>${escHtml(v)}</td></tr>`).join('')}</table><p class="cap">File hashes and generation time are in manifest.json.</p>`;
  return htmlPage('Export pack', head + sections.join(''));
}

/* ── run ────────────────────────────────────────────────────────────── */
export interface RunInput {
  items: readonly ExportableDef[];
  selection: Record<string, ExportFormat[]>;
  options: ExportOptions;
  asPack: boolean;
  context: ExportContext;
  onProgress?: (p: ExportRunProgress) => void;
  /** Called once per delivered file (browser download). */
  deliver: (file: { filename: string; blob: Blob }) => void;
}
export interface RunOutput {
  rows: ExportResultRow[];
  /** delivered blobs by filename, for "download again" links */
  blobs: Map<string, Blob>;
  errors: ExportFailure[];
  manifest: PackManifest | null;
}

async function bytesOf(b: Blob): Promise<Uint8Array> { return new Uint8Array(await b.arrayBuffer()); }

export async function runExport(inp: RunInput): Promise<RunOutput> {
  const byId = new Map(inp.items.map((d) => [d.id, d]));
  const jobs: Array<{ def: ExportableDef; format: ExportFormat }> = [];
  for (const [id, formats] of Object.entries(inp.selection)) {
    const def = byId.get(id);
    if (!def) continue;
    if ((def.describe().state ?? 'ready') !== 'ready') continue;
    for (const f of formats) if (def.formats.includes(f)) jobs.push({ def, format: f });
  }
  const errors: ExportFailure[] = [];
  const produced: ProducedFile[] = [];
  const progress = (done: number, current: string | null): void => inp.onProgress?.({ total: jobs.length, done, current, errors: [...errors] });
  progress(0, jobs[0]?.def.title ?? null);
  let done = 0;
  for (const { def, format } of jobs) {
    progress(done, def.title);
    try {
      const d = def.describe();
      const file = await def.build(format, inp.options);
      const bytes = await bytesOf(file.blob);
      produced.push({ item: def, label: d.provenance.label, source: d.provenance.source, run_id: d.provenance.run_id ?? d.provenance.sim_run_id ?? null, as_of: d.provenance.as_of ?? null, file, bytes, sha256: await sha256Hex(bytes) });
    } catch (e) {
      const code = e instanceof ExportError ? e.code : 'failed';
      errors.push({ id: def.id, title: def.title, format, code });
    }
    done += 1;
  }
  progress(done, null);

  const at = exportClock.now();
  const blobs = new Map<string, Blob>();
  const rows: ExportResultRow[] = [];
  let manifest: PackManifest | null = null;

  if (inp.asPack && produced.length > 0) {
    const names = uniqueNames(produced.map((p) => `${DIR[p.item.kind]}/${p.file.filename}`));
    const withPath = produced.map((p, i) => ({ ...p, path: names[i] }));
    const indexBytes = new TextEncoder().encode(packIndexHtml(withPath, inp.options, inp.context));
    const index = { item: { id: 'pack:index', title: 'Pack index', windowId: 'expt', kind: 'document' as const }, label: 'Snapshot', source: 'Export pack index', file: { blob: new Blob([indexBytes]), filename: 'index.html', mime: 'text/html', format: 'html' as const }, bytes: indexBytes, sha256: await sha256Hex(indexBytes), path: 'index.html' };
    manifest = buildManifest([...withPath, index], inp.options, inp.context, errors);
    const manifestBytes = new TextEncoder().encode(`${JSON.stringify(manifest, null, 2)}\n`);
    const zip = writeZip([...withPath.map((p) => ({ name: p.path, bytes: p.bytes })), { name: 'index.html', bytes: indexBytes }, { name: 'manifest.json', bytes: manifestBytes }]);
    const blob = new Blob([zip as BlobPart], { type: 'application/zip' });
    const filename = fileNameFor(inp.options.filenamePattern, { title: 'export-pack', kind: 'pack', window: '', run: inp.context.runId ?? inp.context.simRunId, date: inp.context.asOf }, 'zip');
    inp.deliver({ filename, blob });
    blobs.set(filename, blob);
    rows.push({
      id: 'pack', title: `Export pack, ${withPath.length} ${withPath.length === 1 ? 'file' : 'files'}`, format: 'pack', filename, bytes: zip.length, sha256: await sha256Hex(zip), mime: 'application/zip', at,
      members: [...withPath, index].map((p) => ({ name: p.path, bytes: p.bytes.length, sha256: p.sha256 })),
    });
  } else {
    const names = uniqueNames(produced.map((p) => p.file.filename));
    produced.forEach((p, i) => {
      inp.deliver({ filename: names[i], blob: p.file.blob });
      blobs.set(names[i], p.file.blob);
      rows.push({ id: p.item.id, title: p.item.title, format: p.file.format, filename: names[i], bytes: p.bytes.length, sha256: p.sha256, mime: p.file.mime, at });
    });
  }
  return { rows, blobs, errors, manifest };
}

export const failureWord = (f: ExportFailure): string => ERROR_WORD[f.code];
