import { shortRunId } from '../../exports/columnLabels';
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Chip, MetaChip, StateLine } from './chips';
import { SAVE_PDF_LABEL, SAVE_PDF_TIP, printHtmlAsPdf } from '../../exports/printPdf';
import { CentreError, buildPdf, buildReport, fetchCatalogue, fetchTemplates, saveBlob } from './client';
import {
  FORMAT_TABS, buildBody, buildSummary, defaultSelection, estimateBytes, failureState, formatBytes, includeAll, leaveAllOut,
  hasData, normaliseSelection, pdfAvailability, reportFilename, selectionCounter, toggleSection,
  type BuildEnvelope, type ReportFormat, type ReportTemplatesDoc,
} from './model';
import { FIGURE_DATASETS, figureSpecs, figuresHtml, injectFigures, printLookup, type FigureHtml } from './figures';
import type { RecentItem } from './recent';
import { useServerCharts } from './useServerCharts';
import { fetchDataset } from '../dataviewer/dataClient';
import type { Dataset } from '../dataviewer/columns';
import { Icon } from '../../ui/Icon';

/* REPORT BUILDER: template picker, section checklist (the limits and
 * caveats section is locked in), HTML / MD / PDF print view, size
 * ESTIMATE, a live preview that rebuilds as the checklist changes, and
 * FIGURES: the chain, its legs and impact per place drawn as inline SVG
 * at the end of the HTML report, each with its provenance caption. The
 * server report itself is never altered: figures are appended here. */

type Load = { s: 'loading' } | { s: 'ok'; doc: ReportTemplatesDoc } | { s: 'err'; code: string };
type Built = Partial<Record<ReportFormat, BuildEnvelope>>;
type FigState = { s: 'idle' } | { s: 'loading' } | { s: 'ok'; fig: FigureHtml } | { s: 'err'; code: string };

/** Screen-only skin for the preview sheet: links in the product's accent instead of browser blue; print is untouched. */
const PREVIEW_SKIN = '<style data-preview-skin>@media screen{a{color:#07607f;text-decoration:none}a:hover{text-decoration:underline}}</style>';
const withPreviewSkin = (html: string): string => (html.includes('</head>') ? html.replace('</head>', `${PREVIEW_SKIN}</head>`) : html);

export function ReportBuilder({ runId, onExported }: { runId: string | null; onExported?: (r: Omit<RecentItem, 'key'>) => void }) {
  const [load, setLoad] = useState<Load>({ s: 'loading' });
  const [templateId, setTemplateId] = useState('full');
  const [selection, setSelection] = useState<string[]>([]);
  const [format, setFormat] = useState<ReportFormat>('html');
  const [built, setBuilt] = useState<Built>({});
  const [building, setBuilding] = useState(false);
  const [buildErr, setBuildErr] = useState<string | null>(null);
  const [pdfBusy, setPdfBusy] = useState(false);
  const [pdfErr, setPdfErr] = useState<string | null>(null);
  const [live, setLive] = useState(true);
  const [withFigs, setWithFigs] = useState(true);
  const [fig, setFig] = useState<FigState>({ s: 'idle' });
  const frame = useRef<HTMLIFrameElement>(null);
  const sc = useServerCharts(runId, null);
  const serverFigs = sc.s === 'served';
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);

  const reload = useCallback(() => {
    const ctrl = new AbortController();
    setLoad({ s: 'loading' });
    fetchTemplates(runId, ctrl.signal)
      .then((doc) => {
        setLoad({ s: 'ok', doc });
        setTemplateId((cur) => (doc.templates.some((t) => t.id === cur) ? cur : doc.templates[0].id));
      })
      .catch((e: unknown) => { if ((e as CentreError).code !== 'aborted') setLoad({ s: 'err', code: (e as CentreError).code ?? 'error' }); });
    return () => ctrl.abort();
  }, [runId]);
  useEffect(() => reload(), [reload]);

  const template = load.s === 'ok' ? (load.doc.templates.find((t) => t.id === templateId) ?? load.doc.templates[0]) : null;
  const tplKey = template?.id ?? '';
  useEffect(() => {
    if (!template) return;
    setSelection(defaultSelection(template));
    setBuilt({}); setBuildErr(null); setPdfErr(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tplKey, runId]);

  const changeSelection = (next: string[]) => { setSelection(next); setBuilt({}); setBuildErr(null); setPdfErr(null); };
  const counter = template ? selectionCounter(template, selection) : null;
  const estimate = template ? formatBytes(estimateBytes(template, selection)) : null;
  const pdf = load.s === 'ok' ? pdfAvailability(load.doc.pdf) : { available: false, reason: null };
  const env = built[format] ?? null;
  const printable = format === 'html' || format === 'pdf-print-view';
  /* the server report carries its own figures; the ones drawn here are the fallback while it does not */
  const clientFigs = withFigs && printable && sc.s !== 'checking' && !serverFigs;

  const build = useCallback(async () => {
    if (!template || !runId) return;
    setBuilding(true); setBuildErr(null);
    try {
      const e = await buildReport(buildBody(runId, template, selection, format));
      setBuilt((b) => ({ ...b, [format]: e }));
    } catch (e) { setBuildErr((e as CentreError).code ?? 'error'); } finally { setBuilding(false); }
  }, [template, runId, selection, format]);

  /* live preview: rebuild shortly after the checklist, template or tab changes */
  const buildRef = useRef(build); buildRef.current = build;
  useEffect(() => {
    if (!live || !template || !runId || built[format]) return;
    const t = setTimeout(() => { void buildRef.current(); }, 700);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [live, template, runId, selection, format, built]);

  /* figures: the run's datasets, drawn once per run */
  const figRun = useRef<string | null>(null);
  useEffect(() => {
    if (!clientFigs || !runId) return;
    if (figRun.current === runId && fig.s !== 'err') return;
    figRun.current = runId;
    const ctrl = new AbortController();
    setFig({ s: 'loading' });
    (async () => {
      const cat = await fetchCatalogue(runId, null, ctrl.signal);
      const sets: Record<string, Dataset> = {};
      let asOf = '';
      for (const id of FIGURE_DATASETS) {
        const meta = cat.datasets.find((d) => d.id === id);
        if (!meta || !hasData(meta)) continue;
        const r = await fetchDataset(meta, { runId, simRunId: null, rowsCap: 500 }, ctrl.signal);
        sets[id] = r.ds; asOf = r.asOf;
      }
      const results = figureSpecs(sets, (id) => ({ datasetProvenance: cat.datasets.find((d) => d.id === id)?.provenance ?? null, runId, asOf, source: `EXPORT ${id.toUpperCase()}`, scope: null }));
      setFig({ s: 'ok', fig: figuresHtml(results, printLookup) });
    })().catch((e: unknown) => { if ((e as CentreError).code !== 'aborted') setFig({ s: 'err', code: (e as CentreError).code ?? 'error' }); });
    return () => ctrl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clientFigs, runId]);

  const content = useMemo(() => {
    if (!env) return null;
    return clientFigs && fig.s === 'ok' ? injectFigures(env.content, fig.fig.html) : env.content;
  }, [env, clientFigs, fig]);

  const record = (file: string, bytes: number | null, ext: 'html' | 'md', figures: boolean) => {
    if (!template || !runId) return;
    onExported?.({ label: `REPORT ${template.id.toUpperCase()} ${format === 'md' ? 'MD' : 'HTML'}`, file, at: new Date().toISOString(), runId, bytes, rows: null,
      req: { t: 'report', body: { ...buildBody(runId, template, selection, format) } as Record<string, unknown>, ext, figures } });
  };
  const download = () => {
    if (!env || !template || !runId || content == null) return;
    const tab = FORMAT_TABS.find((f) => f.id === format) ?? FORMAT_TABS[0];
    const figured = content !== env.content;
    const base = reportFilename(env, runId, template, format);
    const name = figured ? base.replace(/(\.[a-z]+)$/, '-figures$1') : base;
    const blob = new Blob([content], { type: `${tab.mime}; charset=utf-8` });
    saveBlob(name, blob);
    record(name, blob.size, format === 'md' ? 'md' : 'html', figured);
  };
  const [printBlocked, setPrintBlocked] = useState(false);
  const savePdf = () => { if (content != null) setPrintBlocked(!printHtmlAsPdf(content, { label: runId && template ? `${runId} ${template.id}` : undefined })); };
  const print = () => { const w = frame.current?.contentWindow; if (w) { w.focus(); w.print(); } };
  const downloadPdf = async () => {
    if (!template || !runId) return;
    setPdfBusy(true); setPdfErr(null);
    try {
      const blob = await buildPdf({ run_id: runId, template: template.id, sections: normaliseSelection(template, selection) });
      saveBlob(`${runId}-${template.id}.pdf`, blob);
    } catch (e) { setPdfErr((e as CentreError).code ?? 'error'); } finally { setPdfBusy(false); }
  };
  const onTabKey = (e: React.KeyboardEvent, i: number) => {
    const n = FORMAT_TABS.length;
    const j = e.key === 'ArrowRight' ? (i + 1) % n : e.key === 'ArrowLeft' ? (i - 1 + n) % n : e.key === 'Home' ? 0 : e.key === 'End' ? n - 1 : -1;
    if (j < 0) return;
    e.preventDefault(); setFormat(FORMAT_TABS[j].id); tabRefs.current[j]?.focus();
  };

  const summary = env ? buildSummary(env) : null;
  const pdfFail = pdfErr ? failureState(pdfErr) : null;
  const buildFail = buildErr ? failureState(buildErr) : null;

  if (load.s === 'loading') return <StateLine chip="INFO" word="Loading" />;
  if (load.s === 'err') return <StateLine chip={failureState(load.code).chip} word={failureState(load.code).word} code={load.code} onRetry={reload} />;
  if (!template) return null;
  return (
    <div className="rk-root">
      <div className="rk-controls">
        <div className="rk-row">
          <span className="rk-lbl">Run</span>
          {runId ? <MetaChip title={runId}>{shortRunId(runId) ?? runId}</MetaChip> : <Chip chip="NOT SERVED" word="No run" />}
        </div>
        <label className="rk-row" htmlFor="rk-template">
          <span className="rk-lbl">Template</span>
          <select id="rk-template" className="rk-input" value={template.id} onChange={(e) => setTemplateId(e.target.value)}>
            {load.doc.templates.map((t) => <option key={t.id} value={t.id}>{t.title}</option>)}
          </select>
        </label>
        <div className="rk-row rk-row--between">
          <span className="rk-lbl" id="rk-sections-lbl">Sections</span>
          <span className="rk-count mono" aria-live="polite">{counter?.label}</span>
        </div>
        <div className="rk-row">
          <button type="button" className="rk-btn" onClick={() => changeSelection(includeAll(template))}>Include all</button>
          <button type="button" className="rk-btn" onClick={() => changeSelection(leaveAllOut(template))}>Leave all out</button>
        </div>
        <ul className="rk-list" aria-labelledby="rk-sections-lbl">
          {template.sections.map((sec) => {
            const on = normaliseSelection(template, selection).includes(sec.id);
            return (
              <li key={sec.id} className="rk-item">
                <label className="rk-check">
                  <input type="checkbox" checked={on} disabled={sec.locked} onChange={() => changeSelection(toggleSection(template, selection, sec.id))} />
                  <span className="rk-item-title">{sec.title}</span>
                </label>
                <Chip chip={sec.chip} />
                {sec.locked && <Chip chip="LOCKED" tone="meta" />}
                <span className="rk-size mono">{formatBytes(sec.estimate_bytes)}</span>
              </li>
            );
          })}
        </ul>
        <label className="rk-check rk-row">
          <input type="checkbox" checked={serverFigs || (withFigs && printable)} disabled={!printable || serverFigs} onChange={(e) => setWithFigs(e.target.checked)} />
          <span>Figures</span>
          {serverFigs && <Chip chip="LIVE" word="From server" />}
          {!printable && !serverFigs && <MetaChip>HTML only</MetaChip>}
          {clientFigs && fig.s === 'loading' && <MetaChip>Drawing</MetaChip>}
          {clientFigs && fig.s === 'ok' && <MetaChip>{fig.fig.drawn} drawn here</MetaChip>}
          {clientFigs && fig.s === 'ok' && fig.fig.missing.length > 0 && <Chip chip="NOT SERVED" word={`${fig.fig.missing.length} not served`} />}
          {clientFigs && fig.s === 'err' && <Chip chip={failureState(fig.code).chip} word="Figures down" title={fig.code} />}
        </label>
        <label className="rk-check rk-row">
          <input type="checkbox" checked={live} onChange={(e) => setLive(e.target.checked)} />
          <span>Live preview</span>
        </label>
        <div className="rk-tabs" role="tablist" aria-label="Report format">
          {FORMAT_TABS.map((f, i) => (
            <button key={f.id} type="button" role="tab" id={`rk-tab-${f.id}`} aria-selected={format === f.id} tabIndex={format === f.id ? 0 : -1}
              ref={(el) => { tabRefs.current[i] = el; }} className={`rk-tab${format === f.id ? ' on' : ''}`} onClick={() => setFormat(f.id)} onKeyDown={(e) => onTabKey(e, i)}>
              {f.label}
            </button>
          ))}
        </div>
        <div className="rk-row">
          <Chip chip="ESTIMATE" word={`Estimate ${estimate ?? ''}`} title="HTML size estimate" />
          <MetaChip>HTML basis</MetaChip>
        </div>
        <div className="rk-row">
          <button type="button" className="rk-btn rk-btn--primary" disabled={!runId || building} onClick={build}>{building ? 'Building…' : 'Build report'}</button>
          {!runId && <Chip chip="NOT SERVED" word="No run" />}
        </div>
        {buildFail && <StateLine chip={buildFail.chip} word={buildFail.word} code={buildFail.code} />}
      </div>

      <div className="rk-preview" role="tabpanel" aria-labelledby={`rk-tab-${format}`}>
        <div className="rk-toolbar">
          {summary && env ? (
            <>
              <span className="rk-lbl">Actual</span><MetaChip>{summary.bytes}</MetaChip>
              {summary.estimate && <><span className="rk-lbl">Estimate was</span><MetaChip title="HTML size basis">{summary.estimate} HTML</MetaChip></>}
              <MetaChip title={env.meta.sha256}>SHA256 {summary.sha}</MetaChip>
              <MetaChip>{summary.sections} sections</MetaChip>
              {summary.lockedAdded && <Chip chip="LOCKED" word="Caveats added" tone="meta" />}
              {content != null && content !== env.content && <MetaChip>+ Figures here</MetaChip>}
            </>
          ) : <MetaChip>Not built</MetaChip>}
          <span className="rk-spacer" />
          {content != null && env && printable && (serverFigs || content !== env.content) && (
            <button type="button" className="rk-btn" onClick={() => { const d = frame.current?.contentDocument; (d?.getElementById('sm-figures') ?? d?.querySelector('figure'))?.scrollIntoView(); }}>Figures <Icon name="arrow-down" size="xs" /></button>
          )}
          <button type="button" className="rk-btn" disabled={!env || !printable} onClick={print}>Print</button>
          <button type="button" className="rk-btn" disabled={!env} onClick={download}>Download {format === 'md' ? 'MD' : 'HTML'}</button>
          {printable && !pdf.available && (
            <>
              <button type="button" className="rk-btn" title={SAVE_PDF_TIP} disabled={!env || content == null} onClick={savePdf} data-testid="report-save-pdf">{SAVE_PDF_LABEL}</button>
              {printBlocked && <Chip chip="NOT SERVED" word="Print blocked" />}
            </>
          )}
          {format === 'pdf-print-view' && pdf.available && (
            <>
              <button type="button" className="rk-btn" disabled={!runId || !pdf.available || pdfBusy} onClick={downloadPdf}>{pdfBusy ? 'Rendering…' : 'Download PDF'}</button>
              {pdfFail && <Chip chip={pdfFail.chip} word={pdfFail.word} title={pdfFail.code} />}
            </>
          )}
        </div>
        <div className="rk-pane">
          {!env && <div className="rk-empty"><Chip chip="NOT SERVED" word={building ? 'Building' : 'No report built'} /></div>}
          {env && format === 'md' && <pre className="rk-md mono" tabIndex={0} role="region" aria-label="Markdown preview">{env.content}</pre>}
          {env && format !== 'md' && content != null && (
            <iframe ref={frame} className="rk-frame" title={`Report preview, ${format}`} srcDoc={withPreviewSkin(content)} sandbox="allow-same-origin allow-modals" referrerPolicy="no-referrer" />
          )}
        </div>
      </div>
    </div>
  );
}
