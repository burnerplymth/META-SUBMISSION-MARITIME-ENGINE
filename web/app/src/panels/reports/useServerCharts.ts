import { useEffect, useState } from 'react';
import { call } from './client';
import { catalogueQuery, parseChartCatalogue, type ServerCatalogue } from './serverCharts';

export type ServerChartsState =
  | { s: 'checking' }
  | { s: 'served'; cat: ServerCatalogue }
  | { s: 'notserved'; code: string }
  | { s: 'down'; code: string };

/** GET /exports/charts for the given run: the figure catalogue, or an
 *  honest NOT SERVED / DOWN. One request per run, kept for the session. */
const cache = new Map<string, ServerChartsState>();

export function useServerCharts(runId: string | null, simRunId: string | null): ServerChartsState {
  const key = `${runId ?? ''}|${simRunId ?? ''}`;
  const [state, setState] = useState<ServerChartsState>(() => cache.get(key) ?? { s: 'checking' });
  useEffect(() => {
    const hit = cache.get(key);
    if (hit) { setState(hit); return; }
    setState({ s: 'checking' });
    const ctrl = new AbortController();
    call(catalogueQuery(runId, simRunId), { signal: ctrl.signal, timeoutMs: 20000 })
      .then((res) => res.json())
      .then((doc) => {
        const cat = parseChartCatalogue(doc);
        const next: ServerChartsState = cat ? { s: 'served', cat } : { s: 'notserved', code: 'bad_catalogue' };
        cache.set(key, next); setState(next);
      })
      .catch((e: { code?: string }) => {
        if (e.code === 'aborted') return;
        const code = e.code ?? 'error';
        const next: ServerChartsState = code === 'network' || code === 'timeout' ? { s: 'down', code } : { s: 'notserved', code };
        if (next.s === 'notserved') cache.set(key, next);
        setState(next);
      });
    return () => ctrl.abort();
  }, [key, runId, simRunId]);
  return state;
}
