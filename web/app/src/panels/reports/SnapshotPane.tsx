import React, { useEffect, useMemo, useRef, useState } from 'react';
import { ChartFrame } from '../../charts/ChartFrame';
import { liveLookup } from '../../charts/exportDom';
import { chartToSvg, exportFilename } from '../../charts/serialize';
import type { ChartSpec } from '../../charts/types';
import { Chip, MetaChip, StateLine } from './chips';
import { CentreError, saveBlob } from './client';
import { SAVE_PDF_LABEL, SAVE_PDF_TIP, printHtmlAsPdf } from '../../exports/printPdf';
import { openPrintWindow, stackedPng, type Done } from './exportActions';
import { figureSpecs, type FigureResult } from './figures';
import { snapshotHtml } from './snapshot';
import { hasData, type ExportCatalogue } from './model';
import type { Dataset } from '../dataviewer/columns';
import { fetchDataset } from '../dataviewer/dataClient';

/* SNAPSHOT VIEW: the run's chart set, drawn live, exportable as one
 * print-friendly page, as a stacked PNG, or chart by chart. The page and
 * the PNG carry the light print skin; every chart keeps its caption. */

const CANDIDATES = ['chain_events', 'legs', 'impact_per_place', 'ports_board', 'sim_series', 'scorecards', 'health_board'] as const;
const DEFAULT_ON = ['chain_events', 'legs', 'impact_per_place'];

export function SnapshotPane({ cat, runId, simRunId, onDone }: {
  cat: ExportCatalogue; runId: string | null; simRunId: string | null; onDone: (d: Done) => void;
}) {
  const [on, setOn] = useState<string[]>(DEFAULT_ON);
  const [sets, setSets] = useState<Record<string, Dataset>>({});
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [asOf, setAsOf] = useState<string | null>(null);
  const [word, setWord] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const loaded = useRef<string>('');

  const avail = CANDIDATES.filter((id) => cat.datasets.some((d) => d.id === id));
  useEffect(() => {
    const key = `${runId}|${simRunId}|${on.join(',')}|${cat.datasets.map((d) => d.state).join('')}`;
    if (loaded.current === key) return;
    loaded.current = key;
    const ctrl = new AbortController();
    setLoading(true); setErr(null);
    (async () => {
      const out: Record<string, Dataset> = {}; let stamp = '';
      for (const id of on) {
        const meta = cat.datasets.find((d) => d.id === id);
        if (!meta || !hasData(meta)) continue;
        const r = await fetchDataset(meta, { runId, simRunId, rowsCap: 500 }, ctrl.signal);
        out[id] = r.ds; stamp = r.asOf;
      }
      setSets(out); setAsOf(stamp || null); setLoading(false);
    })().catch((e: unknown) => { if ((e as CentreError).code !== 'aborted') { setErr((e as CentreError).code ?? 'error'); setLoading(false); } });
    return () => ctrl.abort();
  }, [cat, runId, simRunId, on]);

  const results: FigureResult[] = useMemo(() => {
    const ids = on;
    return ids.map((id) => {
      const ds = sets[id];
      if (!ds) {
        const meta = cat.datasets.find((d) => d.id === id);
        return { id, spec: null, reason: meta && !hasData(meta) ? meta.state : 'NOT SERVED' } as FigureResult;
      }
      const prov = { datasetProvenance: cat.datasets.find((d) => d.id === id)?.provenance ?? null, runId, asOf, source: `EXPORT ${id.toUpperCase()}`, scope: null };
      return figureSpecs({ [id]: ds }, () => prov, [id])[0];
    });
  }, [on, sets, cat, runId, asOf]);
  const specs = results.flatMap((r) => (r.spec ? [r.spec] : []));
  const missing = results.filter((r) => !r.spec).map((r) => ({ id: r.id, reason: r.reason ?? 'NOT SERVED' }));

  const flash = (w: string) => { setWord(w); setTimeout(() => setWord(null), 3000); };
  const title = `Snapshot${runId ? ` ${runId}` : ''}`;
  const html = () => {
    const { lookup, done } = liveLookup('print');
    try { return snapshotHtml({ title, runId, simRunId, asOf, charts: specs, missing }, lookup); } finally { done(); }
  };
  const record = (file: string, bytes: number) => onDone({ label: 'SNAPSHOT', file, at: new Date().toISOString(), runId, bytes, rows: null, req: { t: 'snapshot', runId, simRunId } });
  const fileBase = exportFilename({ title: 'snapshot', type: 'view', runId, asOf, ext: 'html' });

  const printView = () => { if (!openPrintWindow(html())) flash('POPUP BLOCKED'); else flash('PRINT VIEW OPEN'); };
  const savePdf = () => { flash(printHtmlAsPdf(html(), { label: title }) ? 'PRINT DIALOG OPEN' : 'PRINT BLOCKED'); };
  const downloadHtml = () => { const b = new Blob([html()], { type: 'text/html;charset=utf-8' }); saveBlob(fileBase, b); record(fileBase, b.size); flash('SAVED HTML'); };
  const downloadPng = async () => {
    setBusy(true);
    try {
      const { lookup, done } = liveLookup('print');
      let frames: Array<{ svg: string; width: number; height: number }>;
      try { frames = specs.map((s: ChartSpec) => { const f = chartToSvg(s, { width: 900, chartHeight: s.type === 'cascade' ? 280 : 240 }, lookup); return { svg: f.svg, width: f.width, height: f.height }; }); } finally { done(); }
      const png = await stackedPng(frames, 2);
      const file = fileBase.replace(/\.html$/, '@2x.png');
      saveBlob(file, png); record(file, png.size); flash('SAVED PNG 2X');
    } catch { flash('EXPORT FAILED'); } finally { setBusy(false); }
  };

  return (
    <div className="xc-snap">
      <div className="xc-snapbar">
        <span className="rk-lbl">Charts</span>
        {avail.map((id) => (
          <label key={id} className="rk-check">
            <input type="checkbox" checked={on.includes(id)} onChange={() => setOn((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]))} />
            <span className="mono">{id}</span>
          </label>
        ))}
      </div>
      <div className="xc-snapbar">
        <button type="button" className="rk-btn rk-btn--primary" disabled={specs.length === 0 || loading} onClick={printView}>Print view</button>
        <button type="button" className="rk-btn" title={SAVE_PDF_TIP} disabled={specs.length === 0 || loading} onClick={savePdf} data-testid="snapshot-save-pdf">{SAVE_PDF_LABEL}</button>
        <button type="button" className="rk-btn" disabled={specs.length === 0 || loading} onClick={downloadHtml}>Download HTML</button>
        <button type="button" className="rk-btn" disabled={specs.length === 0 || loading || busy} onClick={downloadPng}>{busy ? 'Rendering…' : 'Export PNG 2X'}</button>
        {word && <span className="cx-status ok" role="status">{word}</span>}
        <span className="rk-spacer" />
        {loading && <MetaChip>Loading</MetaChip>}
        {runId ? <MetaChip title={runId}>Run {runId}</MetaChip> : <Chip chip="NOT SERVED" word="No run" />}
        <MetaChip>{specs.length} drawn</MetaChip>
      </div>
      {err && <StateLine chip="DOWN" word="Charts down" code={err} />}
      <div className="xc-charts">
        {specs.map((s) => <ChartFrame key={s.id} spec={s} height={s.type === 'cascade' ? 280 : 220} exportWidth={900} />)}
        {missing.map((m) => <div key={m.id} className="rk-state"><Chip chip="NOT SERVED" word={`${m.id} · ${m.reason}`} /></div>)}
      </div>
    </div>
  );
}
