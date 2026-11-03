import { useEffect, useState } from 'react';
import { fetchDataset } from '../dataviewer/dataClient';
import { PREVIEW_ROWS, previewFromDataset, type PreviewInfo } from './exportPlan';
import { hasData, type ExportCatalogue } from './model';

/* Mini-previews for the catalogue: the first rows of every dataset that
 * has data, fetched one after another and kept for the session. A preview
 * is a sample (its row count says so); the real size is measured on
 * download. */
const cache = new Map<string, PreviewInfo>();
const failed = new Set<string>();

export function usePreviews(cat: ExportCatalogue | null, runId: string | null, simRunId: string | null): Record<string, PreviewInfo | undefined> {
  const [, tick] = useState(0);
  useEffect(() => {
    if (!cat) return;
    const ctrl = new AbortController();
    (async () => {
      for (const d of cat.datasets) {
        if (!hasData(d) || ctrl.signal.aborted) continue;
        const key = `${d.id}|${runId ?? ''}|${simRunId ?? ''}`;
        if (cache.has(key) || failed.has(key)) continue;
        try {
          const r = await fetchDataset(d, { runId, simRunId, rowsCap: PREVIEW_ROWS }, ctrl.signal);
          cache.set(key, previewFromDataset(r.ds, r.text));
        } catch { if (!ctrl.signal.aborted) failed.add(key); }
        if (!ctrl.signal.aborted) tick((n) => n + 1);
      }
    })();
    return () => ctrl.abort();
  }, [cat, runId, simRunId]);
  const out: Record<string, PreviewInfo | undefined> = {};
  if (cat) for (const d of cat.datasets) out[d.id] = cache.get(`${d.id}|${runId ?? ''}|${simRunId ?? ''}`);
  return out;
}
