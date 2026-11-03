/* Fetch layer for the viewer and the export page: one dataset as CSV
 * text (with the X-Rows-* / X-Provenance headers), and a probe for the
 * optional routes (xlsx, server charts). Reuses the reports
 * client, so errors carry the served code and nothing throws a bare
 * TypeError into a panel. */
import { CentreError, call, getJson } from '../reports/client';
import { csvPath, csvResultFromHeaders, type CsvResult, type ExportDataset } from '../reports/model';
import { loadDataset, type Dataset } from './columns';
import { neutralText } from '../../contentGuard';

export interface DatasetQuery { runId: string | null; simRunId: string | null; from?: string | null; to?: string | null; rowsCap: number | null }
export interface LoadedDataset { ds: Dataset; result: CsvResult; bytes: number; filename: string; asOf: string; text: string }

export async function fetchDataset(meta: ExportDataset, q: DatasetQuery, signal?: AbortSignal): Promise<LoadedDataset> {
  const path = csvPath(meta, { runId: q.runId, simRunId: q.simRunId, from: q.from ?? null, to: q.to ?? null, rowsCap: q.rowsCap });
  const res = await call(path, { signal, timeoutMs: 60000 });
  const text = neutralText(await res.text());
  const cd = res.headers.get('content-disposition') ?? '';
  const m = /filename="?([^";]+)"?/.exec(cd);
  return {
    ds: loadDataset(meta.id, text, meta.columns), result: csvResultFromHeaders((n) => res.headers.get(n)),
    bytes: text.length, filename: m?.[1] ?? `${meta.id}.csv`, asOf: new Date().toISOString(), text,
  };
}

/** Mean bytes per CSV row in a sample; null when the sample has none. */
export function bytesPerRow(text: string, rows: number): number | null {
  if (rows <= 0) return null;
  const head = text.indexOf('\n') + 1;
  return Math.max(1, (text.length - head) / rows);
}

export type RouteState = 'served' | 'notserved' | 'down' | 'checking';

/** GET a route and say whether it exists. 404/405/501 = NOT SERVED. */
export async function probeRoute(path: string, signal?: AbortSignal): Promise<{ state: RouteState; doc: unknown }> {
  try {
    const res = await call(path, { signal, timeoutMs: 15000 });
    const ct = res.headers.get('content-type') ?? '';
    if (/json/i.test(ct)) return { state: 'served', doc: await res.json().catch(() => null) };
    return { state: 'served', doc: null };
  } catch (e) {
    const c = (e as CentreError).code;
    if (c === 'aborted') return { state: 'checking', doc: null };
    if (c === 'network' || c === 'timeout') return { state: 'down', doc: null };
    if (c === 'http_404' || c === 'http_405' || c === 'http_501' || c === 'not_found') return { state: 'notserved', doc: null };
    return { state: 'notserved', doc: null };
  }
}

export { getJson };
