/* The standard header block: what, when, where from,
 * label, hashes. One builder; every format places it its own way (CSV
 * comment lines, XLSX Provenance sheet, JSON object, HTML table, SVG desc).
 * Descriptive wording only: no accuracy or forecast claims. */
import { sha256Hex } from '../panels/reports/packVerify';
import { WINDOWS, type WindowId } from '../windows/registry';
import { sourceLineFor } from '../sources/credits';
import type { ExportKind, ExportProvenance } from './types';

export const GENERATOR = 'SupermAP Export 1';

/** The clock used for the `Exported` field. Data rows never carry it. */
export const exportClock = { now: (): string => new Date().toISOString() };

const WORDS: Record<string, string> = { nodes: 'places', node: 'place', edges: 'legs', edge: 'leg', graph: 'map' };
/** House vocabulary: node, edge, graph become place, leg, map (same rule as server/reports/vocab.py). */
export function plainVocab(text: string): string {
  return text.replace(/\b(nodes|node|edges|edge|graph)\b/gi, (m) => {
    const w = WORDS[m.toLowerCase()];
    if (m.length > 1 && m === m.toUpperCase()) return w.toUpperCase();
    return m[0] === m[0].toUpperCase() ? w[0].toUpperCase() + w.slice(1) : w;
  });
}

/** "1 row", "2 rows": count with thousands separators and the right noun form. */
export const count = (n: number, one: string, many = `${one}s`): string => `${n.toLocaleString('en-US')} ${n === 1 ? one : many}`;

export const windowTitle = (windowId: string): string => (WINDOWS as Record<string, { title: string } | undefined>)[windowId]?.title ?? windowId;
export const isWindow = (id: string): id is WindowId => Object.prototype.hasOwnProperty.call(WINDOWS, id);

export const KIND_WORD: Readonly<Record<ExportKind, string>> = {
  chart: 'Chart', table: 'Table', run: 'Run', plan: 'Plan', document: 'Document', dataset: 'Dataset', media: 'Media', report: 'Run report',
};

export interface HeaderInput {
  title: string;
  kind: ExportKind;
  windowId: string;
  prov: ExportProvenance;
  rows?: number | null;
  columns?: number | null;
  /** sha256 of the canonical row content (see `dataSha256`) */
  dataHash?: string | null;
  note?: string | null;
}
export type HeaderEntry = [string, string];

const one = (v: string): string => plainVocab(v.replace(/[\r\n\t]+/g, ' ').trim());

/** Ordered key/value pairs of the header block; empty values are left out. */
export function headerEntries(h: HeaderInput): HeaderEntry[] {
  const p = h.prov;
  const e: Array<[string, string | null | undefined]> = [
    ['What', h.title],
    ['Kind', KIND_WORD[h.kind]],
    ['Window', windowTitle(h.windowId)],
    ['Label', p.label],
    ['Source', p.source],
    ['Credits', sourceLineFor(p.source, h.title, h.note)],
    ['Run', p.run_id],
    ['Simulation run', p.sim_run_id],
    ['As of', p.as_of],
    ['Exported', exportClock.now()],
    ['Rows', h.rows != null ? String(h.rows) : null],
    ['Columns', h.columns != null ? String(h.columns) : null],
    ['Unit', p.unit],
    ['Source hash', p.hash],
    ['Data sha256', h.dataHash],
    ['Note', h.note],
    ['Generator', GENERATOR],
  ];
  return e.filter((x): x is [string, string] => typeof x[1] === 'string' && x[1] !== '').map(([k, v]) => [k, one(v)]);
}

/** `# Key: value` lines for the top of a CSV file. */
export function csvHeaderLines(entries: readonly HeaderEntry[]): string {
  return entries.map(([k, v]) => `# ${k}: ${v}`).join('\n') + '\n';
}

export const headerObject = (entries: readonly HeaderEntry[]): Record<string, string> => Object.fromEntries(entries);

/** sha256 of the canonical content: the JSON of [columns, rows]. Equal data, equal hash. */
export async function dataSha256(columns: readonly string[], rows: ReadonlyArray<ReadonlyArray<unknown>>): Promise<string> {
  return sha256Hex(new TextEncoder().encode(JSON.stringify([columns, rows])));
}

/** One line for an SVG <desc> and a caption band. */
export const headerLine = (entries: readonly HeaderEntry[], keys: readonly string[] = ['Label', 'Source', 'Run', 'As of']): string =>
  entries.filter(([k]) => keys.includes(k)).map(([k, v]) => `${k} ${v}`).join(' · ');

export const escHtml = (s: string): string => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
export const escXml = (s: string): string =>
  // eslint-disable-next-line no-control-regex
  s.replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g, '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

/** The credit entry alone. Every export carries it, even when the header block is switched off. */
export const creditOnly = (entries: readonly HeaderEntry[]): HeaderEntry[] => entries.filter(([k]) => k === 'Credits');
/** The whole header block when `on`, else the credit entry alone. */
export const shownEntries = (on: boolean, entries: readonly HeaderEntry[]): HeaderEntry[] => (on ? [...entries] : creditOnly(entries));
