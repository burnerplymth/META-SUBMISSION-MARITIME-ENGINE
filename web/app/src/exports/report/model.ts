/* The Run report document model. One plain-data document, two renderers: HTML for the browser's own print path
 * (html.ts) and a vector PDF written in the browser (pdf.ts). Builders in each window produce a ReportDoc; nothing here
 * touches the DOM, the network or React. Figures are standalone SVG strings drawn by the shared chart kit. */
import { captionSource } from '../../sources/credits';
import type { ExportLabel } from '../types';

export type ChipTone =
  | 'modelled' | 'simulated' | 'recorded' | 'snapshot' | 'live' | 'sourced' | 'info' | 'flag' | 'notserved' | 'approx' | 'descriptive' | 'detected';

export interface ReportChip { word: string; tone: ChipTone }

export type TableCell = string | number | null | { text: string; chip?: ChipTone; bold?: boolean };
export interface TableColumn { label: string; align?: 'l' | 'r'; /** relative width weight */ w?: number; mono?: boolean }

export interface FigureBlock {
  t: 'figure';
  title: string;
  chips?: ReportChip[];
  /** standalone SVG (no scripts), drawn in the light skin */
  svg: string;
  width: number;
  height: number;
  caption?: string;
}

export type Block =
  | { t: 'heading'; level: 1 | 2 | 3; text: string; chips?: ReportChip[] }
  | { t: 'para'; text: string; muted?: boolean }
  | { t: 'chips'; chips: ReportChip[] }
  | { t: 'kv'; rows: Array<[string, string]>; mono?: boolean }
  | { t: 'tiles'; tiles: Array<{ label: string; value: string; sub?: string; chip?: ReportChip }> }
  | { t: 'table'; title?: string; chips?: ReportChip[]; columns: TableColumn[]; rows: TableCell[][]; note?: string }
  | FigureBlock
  | { t: 'figures'; items: FigureBlock[] }
  | { t: 'note'; text: string; tone?: ChipTone }
  | { t: 'pagebreak' };

export interface ReportDoc {
  /** stable id, for example "montecarlo:run" */
  id: string;
  /** document title (cover) */
  title: string;
  /** window the report belongs to, shown in the page header */
  windowTitle: string;
  orientation: 'portrait' | 'landscape';
  /** standing label of the whole document */
  label: ExportLabel;
  /** chips under the title: Modelled, Simulated, Recorded, Snapshot, Live ... */
  chips: ReportChip[];
  /** one line naming where the numbers come from */
  source: string;
  runId?: string | null;
  simRunId?: string | null;
  hash?: string | null;
  /** time of the data; never the export time */
  asOf?: string | null;
  /** the mandatory label sentence, when the window has one */
  labelText?: string | null;
  /** key/value pairs under the chips: inputs, seed, digest */
  meta: Array<[string, string]>;
  blocks: Block[];
}

export const CHIP_FOR_LABEL: Readonly<Record<ExportLabel, ChipTone>> = {
  Modelled: 'modelled', Simulated: 'simulated', Recorded: 'recorded', Sourced: 'sourced', Snapshot: 'snapshot', Live: 'live', 'Not served': 'notserved',
};
export const chipOf = (word: string, tone: ChipTone): ReportChip => ({ word, tone });
export const labelChip = (label: ExportLabel): ReportChip => ({ word: label, tone: CHIP_FOR_LABEL[label] });

/** Chip from a chip-kit word such as MODELLED, SIMULATED, SNAPSHOT, NOT SERVED (copy rules: sentence case). */
export function chipFromWord(word: string, shown?: string): ReportChip {
  const u = word.trim().toUpperCase();
  const tone: ChipTone = u === 'MODELLED' || u === 'MODELED' ? 'modelled' : u === 'SIMULATED' ? 'simulated' : u === 'RECORDED' ? 'recorded'
    : u === 'SNAPSHOT' ? 'snapshot' : u === 'LIVE' ? 'live' : u === 'SOURCED' ? 'sourced' : u === 'NOT SERVED' || u === 'NO DATA' || u === 'DOWN' ? 'notserved'
    : u === 'FLAG' ? 'flag' : 'info';
  const sentence = (s: string) => (s ? s.charAt(0).toUpperCase() + s.slice(1).toLowerCase() : s);
  return { word: shown ?? sentence(word.trim()), tone };
}

/** Plain text of a table cell. */
export const cellText = (c: TableCell): string => (c == null ? '' : typeof c === 'object' ? c.text : typeof c === 'number' ? (Number.isFinite(c) ? String(c) : '') : c);

/** Every string a document would print (for vocabulary and label checks). */
export function docText(d: ReportDoc): string {
  const out: string[] = [d.title, d.windowTitle, d.source, d.labelText ?? '', d.runId ?? '', d.simRunId ?? '', d.hash ?? '', d.asOf ?? '', d.label];
  d.chips.forEach((c) => out.push(c.word));
  d.meta.forEach(([k, v]) => out.push(k, v));
  const fig = (f: FigureBlock) => { out.push(f.title, f.caption ?? '', ...(f.chips ?? []).map((c) => c.word), f.svg.replace(/<[^>]*>/g, ' ')); };
  for (const b of d.blocks) {
    switch (b.t) {
      case 'heading': out.push(b.text, ...(b.chips ?? []).map((c) => c.word)); break;
      case 'para': case 'note': out.push(b.text); break;
      case 'chips': out.push(...b.chips.map((c) => c.word)); break;
      case 'kv': b.rows.forEach(([k, v]) => out.push(k, v)); break;
      case 'tiles': b.tiles.forEach((t) => out.push(t.label, t.value, t.sub ?? '', t.chip?.word ?? '')); break;
      case 'table': out.push(b.title ?? '', b.note ?? '', ...(b.chips ?? []).map((c) => c.word), ...b.columns.map((c) => c.label), ...b.rows.flat().map(cellText)); break;
      case 'figure': fig(b); break;
      case 'figures': b.items.forEach(fig); break;
      default: break;
    }
  }
  return out.filter(Boolean).join('\n');
}

export const PRODUCT = 'SupermAP';
export const REPORT_KIND = 'Run report';

/** Page footer text, shared by both renderers: label, source, run id, hash and as-of. The page number is added by the renderer. */
export function footerParts(d: ReportDoc): string[] {
  const short = (s: string, n: number) => (s.length > n ? `${s.slice(0, n)}` : s);
  return [
    d.label,
    captionSource(d.source) ?? d.source,
    d.runId ? `Run ${d.runId}` : null,
    d.simRunId ? `Simulation ${d.simRunId}` : null,
    d.hash ? `Hash ${short(d.hash, 16)}` : null,
    d.asOf ? `As of ${d.asOf}` : null,
  ].filter((x): x is string => !!x);
}

/** Content width in CSS pixels at 1:1 for the A4 page (14 mm side margins): figures are drawn at this width so chart text keeps its size. */
export const CONTENT_PX: Readonly<Record<ReportDoc['orientation'], number>> = { portrait: 688, landscape: 1016 };
