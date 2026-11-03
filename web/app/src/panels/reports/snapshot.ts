/* Snapshot view: the current chart set (and optionally the rows on
 * screen) as ONE print-friendly HTML page. Deterministic for a given
 * input: no clock inside; the as-of stamp is the data's. */
import { chartToSvg } from '../../charts/serialize';
import type { ChartSpec } from '../../charts/types';
import { escXml, type VarLookup } from '../../charts/vnode';
import { captionText } from '../../charts/serialize';
import { inlineSvg } from './figures';
import printCss from './printCss.json';

export interface SnapshotTable { title: string; columns: string[]; rows: Array<Array<string | number | null>>; shown: number; total: number }
export interface SnapshotInput {
  title: string;
  runId: string | null; simRunId: string | null; asOf: string | null;
  charts: ChartSpec[];
  missing: Array<{ id: string; reason: string }>;
  tables?: SnapshotTable[];
}

const CSS = printCss.snapshot;

export function snapshotHtml(inp: SnapshotInput, lookup: VarLookup, width = 760): string {
  const figs = inp.charts.map((c) => {
    const f = chartToSvg(c, { width, chartHeight: c.type === 'cascade' ? 280 : 240 }, lookup);
    return `<figure data-chart="${escXml(c.id)}">${inlineSvg(f.svg)}</figure>`;
  });
  const miss = inp.missing.map((m) => `<p class="ns">${escXml(m.id)} · NOT SERVED · ${escXml(m.reason)}</p>`);
  const tables = (inp.tables ?? []).map((t) => {
    const head = `<tr>${t.columns.map((c) => `<th>${escXml(c)}</th>`).join('')}</tr>`;
    const body = t.rows.map((r) => `<tr>${r.map((v) => (v == null ? '<td class="n">NOT SERVED</td>' : `<td${typeof v === 'number' ? ' class="n"' : ''}>${escXml(String(v))}</td>`)).join('')}</tr>`).join('');
    return `<h2>${escXml(t.title)} · ${t.shown} OF ${t.total} ROWS</h2><table>${head}${body}</table>`;
  });
  const chips = [...new Set(inp.charts.map((c) => captionText({ chip: c.provenance.chip })))];
  const meta = [inp.runId ? `RUN ${inp.runId}` : 'NO RUN', inp.simRunId ? `SIM ${inp.simRunId}` : null, inp.asOf ? `AS OF ${inp.asOf.slice(0, 16).replace('T', ' ')}Z` : null, ...chips].filter(Boolean) as string[];
  return `<!doctype html>\n<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${escXml(inp.title)}</title><style>${CSS}</style></head><body>`
    + `<h1>${escXml(inp.title)}</h1><p class="meta">${meta.map((m) => `<span>${escXml(m)}</span>`).join('')}</p>`
    + `${figs.join('')}${miss.join('')}${tables.join('')}`
    + '<footer>EVERY FIGURE CARRIES ITS PROVENANCE CHIP · NOT SERVED IS NEVER ZERO · NO TOTAL ACROSS CLASSES</footer></body></html>\n';
}
