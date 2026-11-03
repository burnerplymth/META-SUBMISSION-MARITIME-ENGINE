/* Export serialisation: CSV of the plotted data, a standalone SVG
 * document with the title and provenance caption, and the filename
 * rules. Pure: the DOM-facing parts (PNG, clipboard, download) live in
 * exportDom.ts. */
import { captionSource, sourceLineFor } from '../sources/credits';
import { neutraliseCsvCell } from '../exporter';
import { INK, INK2, SURFACE, legendRow, text } from './draw';
import { CHART_FS, clipLabel, textWidth } from './format';
import { buildAny } from './build';
import type { BuildOpts, ChartModel, ChartSpec, ExportTable, Provenance } from './types';
import { h, toSvgString, translate, type VarLookup, type VNode } from './vnode';

/** The caption mark per chip word in a standalone exported SVG or PNG. An export is a static
 *  document with no component to draw a mark, so it carries a text glyph: it is not UI chrome
 *  (the screen uses the drawn `Status` marks). */
const CHIP_GLYPH: Record<string, string> = {
  LIVE: '\u25cf', SNAPSHOT: '\u25f7', SIMULATED: '\u25c7', MODELLED: '\u2248', DOWN: '\u2715', 'NOT SERVED': '\u2298',
  SOURCED: '\u25c6', BACKTEST: '\u25a4', LEDGER: '\u25a3', META: '', 'HAS DATA': '\u25cf', ESTIMATE: '\u2248', LOCKED: '\u25a0',
};

/* ── CSV ────────────────────────────────────────────────────────────── */
export function csvEscape(v: string | number | null | undefined): string {
  if (v == null) return '';
  const s = typeof v === 'number' ? (Number.isFinite(v) ? String(v) : '') : neutraliseCsvCell(String(v));
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/** The plotted table plus a provenance column, so a row never travels
 *  without its chip. An absent value is an empty cell (never 0). */
export function tableToCsv(table: ExportTable, prov: Provenance): string {
  const cols = [...table.columns, 'provenance'];
  const lines = [cols.map(csvEscape).join(',')];
  for (const r of table.rows) lines.push([...r, prov.chip].map(csvEscape).join(','));
  return `${lines.join('\n')}\n`;
}

/** The CSV with its source line first (`# Source: ...`), for a file that leaves the app. */
export const tableToCsvFile = (table: ExportTable, prov: Provenance): string => `# ${sourceLineFor(prov.source)}\n${tableToCsv(table, prov)}`;

/** Tab-separated, for pasting into a spreadsheet; formula cells are quoted like the CSV's. */
export function tableToTsv(table: ExportTable, prov: Provenance): string {
  const clean = (v: string | number | null) => (v == null ? '' : neutraliseCsvCell(String(v).replace(/[\t\r\n]+/g, ' ')));
  return `${[[...table.columns, 'provenance'].map(clean).join('\t'), ...table.rows.map((r) => [...r, prov.chip].map(clean).join('\t'))].join('\n')}\n`;
}

/* ── filenames ──────────────────────────────────────────────────────── */
export function slug(s: string, max = 40): string {
  const out = s.normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
  return out.slice(0, max).replace(/-+$/g, '') || 'chart';
}
export type ExportExt = 'svg' | 'png' | 'csv' | 'json' | 'html';
/** supermap-<title>-<type>[-<run>][-<YYYYMMDD>][@2x].<ext>. No clock: the
 *  date is the provenance as-of, so equal charts get equal names. */
export function exportFilename(o: { title: string; type: string; runId?: string | null; asOf?: string | null; ext: ExportExt; scale?: number }): string {
  const parts = ['supermap', slug(o.title), slug(o.type, 12)];
  if (o.runId) parts.push(slug(o.runId.replace(/^run_/, ''), 12));
  const d = o.asOf && /^(\d{4})-(\d{2})-(\d{2})/.exec(o.asOf);
  if (d) parts.push(`${d[1]}${d[2]}${d[3]}`);
  return `${parts.join('-')}${o.scale && o.scale > 1 ? `@${o.scale}x` : ''}.${o.ext}`;
}

/* ── caption ────────────────────────────────────────────────────────── */
export function captionParts(p: Provenance): string[] {
  const glyph = CHIP_GLYPH[p.chip.toUpperCase()];
  const out = [`${glyph ? `${glyph} ` : ''}${p.chip.toUpperCase()}`];
  if (p.source) out.push(captionSource(p.source) ?? p.source);
  if (p.asOf) out.push(`As of ${p.asOf.replace('T', ' ').replace(/:\d{2}(\.\d+)?Z$/, 'Z')}`);
  if (p.runId) out.push(`Run ${p.runId}`);
  if (p.scope) out.push(p.scope);
  return out;
}
export const captionText = (p: Provenance): string => captionParts(p).join(' · ');

/** Break a caption into lines no wider than `maxW` px, on the dots. */
export function wrapCaption(p: Provenance, maxW: number, size = CHART_FS): string[] {
  const lines: string[] = []; let cur = '';
  for (const part of captionParts(p)) {
    const next = cur ? `${cur} · ${part}` : part;
    if (cur && textWidth(next, size) > maxW) { lines.push(cur); cur = part; } else cur = next;
  }
  if (cur) lines.push(cur);
  return lines.map((l) => clipLabel(l, maxW, size));
}

/* ── standalone SVG ─────────────────────────────────────────────────── */
export interface FrameOpts { width: number; chartHeight: number; domain?: BuildOpts['domain']; hidden?: BuildOpts['hidden'] }
export interface FramedSvg { svg: string; width: number; height: number; model: ChartModel }

const PAD = 12;
export function composeNodes(spec: ChartSpec, o: FrameOpts): { nodes: VNode[]; width: number; height: number; model: ChartModel } {
  const inner = o.width - PAD * 2;
  const model = buildAny(spec, { width: inner, height: o.chartHeight, domain: o.domain ?? null, hidden: o.hidden });
  const nodes: VNode[] = [h('rect', { x: 0, y: 0, width: o.width, height: 1, fill: 'none' })];
  let y = PAD + 10;
  nodes.push(text(PAD, y, clipLabel(spec.title.toUpperCase(), inner, 11), { size: 11, fill: INK, weight: 600, tracking: 0.5 }));
  if (spec.subtitle) { y += 14; nodes.push(text(PAD, y, clipLabel(spec.subtitle, inner), { fill: INK2 })); }
  y += 10;
  const showLegend = spec.type !== 'spark' && spec.type !== 'multiples' && model.legend.length > 1;
  if (showLegend) {
    const lg = legendRow(model.legend, PAD, y + 8, inner);
    nodes.push(...lg.nodes); y += lg.height + 2;
  }
  nodes.push(translate(PAD, y, model.nodes));
  y += model.height + 6;
  const lines = wrapCaption(spec.provenance, inner);
  nodes.push(h('line', { x1: PAD, x2: o.width - PAD, y1: y, y2: y, stroke: 'var(--line)', strokeWidth: 1 }));
  lines.forEach((l, i) => nodes.push(text(PAD, y + 13 + i * 12, l, { fill: INK2 })));
  const height = y + 13 + lines.length * 12 + PAD - 4;
  nodes.unshift(h('rect', { x: 0, y: 0, width: o.width, height, fill: SURFACE }));
  return { nodes, width: o.width, height, model };
}

export function chartToSvg(spec: ChartSpec, o: FrameOpts, lookup?: VarLookup): FramedSvg {
  const c = composeNodes(spec, o);
  return { svg: toSvgString(c.nodes, { width: c.width, height: c.height }, lookup, { role: 'img', 'aria-label': spec.title }), width: c.width, height: c.height, model: c.model };
}

/** Static token table for headless renders (report figures): the
 *  dark and light institutional values from shell/tokens.css and
 *  charts.css. The browser path reads the live cascade instead. */
export const STATIC_TOKENS: Record<'dark' | 'light', Record<string, string>> = {
  dark: {
    '--bg-panel': '#0b0b0b', '--text': '#e2e2e2', '--text-2': '#9a9a9a', '--text-3': '#7d8085', '--line': '#222222', '--line-strong': '#3a3a3a',
    '--accent-text': '#6b9bff', '--bar-queue': '#c65a55', '--bar-congestion': '#c65a55', '--bar-reroute': '#bfa04e', '--bar-inventory': '#5da271',
    '--bar-cost': '#6fc3df', '--bar-mode-shift': '#8f83b8',
    '--cs-1': '#3987e5', '--cs-2': '#d95926', '--cs-3': '#199e70', '--cs-4': '#c98500', '--cs-5': '#d55181', '--cs-6': '#008300', '--cs-7': '#9085e9', '--cs-8': '#e66767',
  },
  light: {
    '--bg-panel': '#ffffff', '--text': '#1d1d1f', '--text-2': '#55636f', '--text-3': '#5b6b7a', '--line': '#d2d2d7', '--line-strong': '#8e8e93',
    '--accent-text': '#0a58e6', '--bar-queue': '#c65a55', '--bar-congestion': '#c65a55', '--bar-reroute': '#bfa04e', '--bar-inventory': '#5da271',
    '--bar-cost': '#6fc3df', '--bar-mode-shift': '#8f83b8',
    '--cs-1': '#2a78d6', '--cs-2': '#eb6834', '--cs-3': '#1baf7a', '--cs-4': '#eda100', '--cs-5': '#e87ba4', '--cs-6': '#008300', '--cs-7': '#6250d6', '--cs-8': '#e34948',
  },
};
export const staticLookup = (skin: 'dark' | 'light'): VarLookup => (name) => STATIC_TOKENS[skin][name];
