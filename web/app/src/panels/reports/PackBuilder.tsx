import { SAVE_PDF_LABEL, SAVE_PDF_TIP, printHtmlAsPdf } from '../../exports/printPdf';
import React, { useEffect, useMemo, useState } from 'react';
import { formatBytes } from '../../charts/format';
import { Chip, MetaChip, StateLine } from './chips';
import { CentreError, buildPack, fetchTemplates, saveBlob, type PackDownload } from './client';
import { DatasetList, type DatasetActions } from './DatasetList';
import { ManifestView } from './ManifestView';
import { estimateLabel, estimatePack, packStages, type PackPhase, type PreviewInfo } from './exportPlan';
import {
  defaultPackSelection, failureState, hasData, packBody, packCounter, packIncludeAll, packLeaveAllOut, togglePack,
  type ExportCatalogue, type PackManifest, type ReportTemplatesDoc,
} from './model';
import type { Done } from './exportActions';
import { listZipEntries, readZipEntry, readZipJson } from './zip';
import type { RouteState } from '../dataviewer/dataClient';
import { StateMark } from '../../ui/Status';

/* DATA PACK builder: a checklist of datasets (each with its own size
 * estimate), a live ESTIMATE for the whole zip, build progress in
 * stages, then the manifest with every file, hash and cap. */

export interface Bounds { cap: number; from: string | null; to: string | null; bad: boolean }

/** The report HTML inside a pack (top-level .html entry), if the pack has one. */
function reportEntry(bytes: Uint8Array): string | null {
  try { return listZipEntries(bytes).find((e) => /\.html?$/i.test(e.name) && !e.name.includes('/'))?.name ?? null; } catch { return null; }
}
async function savePackPdf(bytes: Uint8Array): Promise<void> {
  const name = reportEntry(bytes);
  const raw = name ? await readZipEntry(bytes, name) : null;
  if (raw) printHtmlAsPdf(new TextDecoder().decode(raw), { label: name ?? undefined });
}

export function PackBuilder({ cat, runId, simRunId, previews, bounds, onDone, onOpen, actions }: {
  cat: ExportCatalogue; runId: string | null; simRunId: string | null; previews: Record<string, PreviewInfo | undefined>;
  bounds: Bounds; onDone: (d: Done) => void; onOpen: (datasetId: string) => void; actions: DatasetActions;
}) {
  const [sel, setSel] = useState<string[]>(() => defaultPackSelection(cat));
  const [includeReport, setIncludeReport] = useState(true);
  const [tpl, setTpl] = useState('full');
  const [tpls, setTpls] = useState<ReportTemplatesDoc | null>(null);
  const [phase, setPhase] = useState<PackPhase>('idle');
  const [err, setErr] = useState<string | null>(null);
  const [res, setRes] = useState<{ dl: PackDownload; manifest: PackManifest | null; manifestErr: string | null } | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [incCharts, setIncCharts] = useState(true);
  const [incXlsx, setIncXlsx] = useState(true);
  const [incPng, setIncPng] = useState(false);

  useEffect(() => { setSel(defaultPackSelection(cat)); setRes(null); setPhase('idle'); }, [cat]);
  useEffect(() => {
    if (!runId) { setTpls(null); return; }
    const c = new AbortController();
    fetchTemplates(runId, c.signal).then(setTpls).catch(() => setTpls(null));
    return () => c.abort();
  }, [runId]);
  useEffect(() => {
    if (phase !== 'building' && phase !== 'reading') return;
    const t0 = Date.now();
    const id = setInterval(() => setElapsed(Date.now() - t0), 200);
    return () => clearInterval(id);
  }, [phase]);

  const tplDoc = tpls?.templates.find((t) => t.id === tpl) ?? tpls?.templates[0] ?? null;
  const est = useMemo(() => estimatePack(cat, sel, previews, bounds.cap, { include: includeReport && !!runId, bytes: tplDoc?.estimate_bytes_default ?? null }), [cat, sel, previews, bounds.cap, includeReport, runId, tplDoc]);
  const busy = phase === 'building' || phase === 'reading';

  const build = async () => {
    if (bounds.bad || sel.length === 0) return;
    setErr(null); setRes(null); setPhase('building'); setElapsed(0);
    try {
      const body = packBody({ runId, simRunId, datasets: sel, rowsCap: bounds.cap, includeReport, template: tplDoc?.id ?? tpl, from: bounds.from, to: bounds.to, includeCharts: incCharts, includeXlsx: incXlsx, includePng: incCharts && incPng });
      const dl = await buildPack(body);
      setPhase('reading');
      let manifest: PackManifest | null = null; let manifestErr: string | null = null;
      try { manifest = await readZipJson<PackManifest>(dl.bytes, 'manifest.json'); if (!manifest) manifestErr = 'manifest_missing'; } catch (e) { manifestErr = 'manifest_unreadable'; }
      setRes({ dl, manifest, manifestErr }); setPhase('done');
      // pin as_of so the stored request rebuilds the same bytes
      const pinned = dl.asOf ? { ...body, as_of: dl.asOf } : body;
      onDone({ label: `DATA PACK · ${sel.length} DATASETS`, file: dl.filename, at: new Date().toISOString(), runId, bytes: dl.blob.size, rows: null, req: { t: 'pack', body: pinned } });
    } catch (e) { setErr((e as CentreError).code ?? 'error'); setPhase('failed'); }
  };

  const fail = err ? failureState(err) : null;
  const stages = packStages(phase);
  const needRun = cat.datasets.filter((d) => !hasData(d) && d.needs);
  return (
    <div className="xc-pack">
      <div className="xc-packbar">
        <span className="rk-lbl">Datasets</span><span className="rk-count mono" aria-live="polite">{packCounter(cat, sel)}</span>
        <button type="button" className="rk-btn" onClick={() => setSel(packIncludeAll(cat))}>Include all</button>
        <button type="button" className="rk-btn" onClick={() => setSel(packLeaveAllOut())}>Leave all out</button>
        <span className="rk-spacer" />
        <Chip chip="ESTIMATE" word={`Estimate ${estimateLabel(est)} raw`} title="Cap × bytes" />
        {est.unknown.length > 0 && <MetaChip>{est.unknown.length} not sampled</MetaChip>}
      </div>
      <DatasetList items={cat.datasets} previews={previews} rowsCap={bounds.cap} xlsx="checking" busy={null} actions={actions} pick picked={sel}
        onPick={(id) => setSel((cur) => togglePack(cat, cur, id))} showUpdated={cat.datasets.some((d) => d.updated_at)} />
      {needRun.length > 0 && <div className="rk-row rk-wrap"><span className="rk-lbl">Needs a run</span>{needRun.map((d) => <Chip key={d.id} chip="NOT SERVED" word={`${d.id} · ${d.state}`} />)}</div>}

      <div className="xc-opts">
        <label className="rk-check rk-row">
          <input type="checkbox" checked={includeReport && !!runId} disabled={!runId} onChange={(e) => setIncludeReport(e.target.checked)} />
          <span>Include report (HTML)</span>
          {!runId && <Chip chip="NOT SERVED" word="No run" />}
        </label>
        {includeReport && runId && tpls && (
          <label className="rk-row" htmlFor="xc-tpl">
            <span className="rk-lbl">Template</span>
            <select id="xc-tpl" className="rk-input" value={tplDoc?.id ?? tpl} onChange={(e) => setTpl(e.target.value)}>
              {tpls.templates.map((t) => <option key={t.id} value={t.id}>{t.title}</option>)}
            </select>
            {est.reportBytes != null && <MetaChip>Report ≈ {formatBytes(est.reportBytes)}</MetaChip>}
          </label>
        )}
        <div className="rk-row rk-wrap" role="group" aria-label="Pack contents">
          <label className="rk-check"><input type="checkbox" checked={incCharts} onChange={(e) => setIncCharts(e.target.checked)} /><span>Figures SVG · CSV · JSON</span></label>
          <label className="rk-check"><input type="checkbox" checked={incXlsx} onChange={(e) => setIncXlsx(e.target.checked)} /><span>Workbook XLSX</span></label>
          <label className="rk-check"><input type="checkbox" checked={incCharts && incPng} disabled={!incCharts} onChange={(e) => setIncPng(e.target.checked)} /><span>Figure PNGS</span></label>
          {incPng && <MetaChip>Slower</MetaChip>}
        </div>
        <div className="rk-row">
          <button type="button" className="rk-btn rk-btn--primary" disabled={busy || sel.length === 0 || bounds.bad} onClick={build}>{busy ? 'Building…' : 'Build data pack'}</button>
          {res && <button type="button" className="rk-btn" onClick={() => saveBlob(res.dl.filename, res.dl.blob)}>Download ZIP</button>}
          {res && reportEntry(res.dl.bytes) && <button type="button" className="rk-btn" title={SAVE_PDF_TIP} data-testid="pack-save-pdf" onClick={() => { void savePackPdf(res.dl.bytes); }}>{SAVE_PDF_LABEL}</button>}
          {res && <MetaChip>ZIP {formatBytes(res.dl.blob.size)}</MetaChip>}
          {bounds.bad && <Chip chip="DOWN" word="Bad time bound" />}
        </div>
        {phase !== 'idle' && (
          <ol className="xc-stages" aria-label="Build progress" aria-live="polite">
            {stages.map((s) => (
              <li key={s.id} className={`xc-stage ${s.state}`}>
                <StateMark name={s.state === 'done' ? 'dot' : s.state === 'now' ? 'half' : s.state === 'failed' ? 'cross' : 'ring'} />{s.label}
                {s.state === 'now' && <span className="mono rk-num"> {(elapsed / 1000).toFixed(1)}s</span>}
              </li>
            ))}
          </ol>
        )}
        {fail && <StateLine chip={fail.chip} word={fail.word} code={fail.code} />}
      </div>

      {res?.manifestErr && <StateLine chip="NOT SERVED" word="Manifest not readable" code={res.manifestErr} />}
      {res?.manifest && <ManifestView manifest={res.manifest} zip={res.dl.bytes} onOpen={onOpen} />}
    </div>
  );
}
