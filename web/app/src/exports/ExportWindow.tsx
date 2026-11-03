import React, { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { Panel } from '../components/Panel';
import { Icon } from '../ui/Icon';
import { Button, SegmentedControl, Toggle } from '../ui';
import { Chip, MetaChip } from '../panels/reports/chips';
import { formatBytes, shortHash } from '../panels/reports/model';
import { columnLabel, shortRunId } from './columnLabels';
import { saveBlob } from '../panels/reports/client';
import { useExportContext } from './context';
import { windowTitle, KIND_WORD } from './header';
import {
  fileCount, matchesQuery, selectedCount, failureWord, type ExportFailure,
} from './manager';
import { managerStore, packPdf, startExport, useExportHistory, useManagerState, usePackPdfView, useRunView } from './managerStore';
import { SAVE_PDF_LABEL, SAVE_PDF_TIP } from './printPdf';
import { providerVersion, resolveProviders, subscribeRegistry, useExportables } from './registry';
import { ensureServerProvider } from './serverProvider';
import { sanitisePattern } from './filename';
import {
  FORMAT_LABEL, STATE_WORD, type ExportDescriptor, type ExportFormat, type ExportPreview, type ExportableDef,
} from './types';
import './export.css';

/* The server catalogue (run datasets, figures, reports) is a provider: it needs no open panel. */
ensureServerProvider();

/* EXPORT (function code EXPT): one window to select what to export from
 * everything the app can currently produce, preview it, choose formats per
 * item and export the selection, as separate files or one ZIP pack with a
 * manifest and a sha256 per file. */

const windowRank = (id: string): number => {
  const order = ['chain', 'chaintrace', 'whatif', 'impact', 'sim', 'laneplanner', 'ports', 'porttwin', 'satdesk', 'dataviewer', 'reports', 'exports'];
  const i = order.indexOf(id);
  return i < 0 ? order.length : i;
};

interface Row { def: ExportableDef; desc: ExportDescriptor }
interface Group { key: string; title: string; rows: Row[] }

export function groupRows(items: readonly ExportableDef[], query: string): Group[] {
  const groups = new Map<string, Group & { rank: number }>();
  for (const def of items) {
    const desc = def.describe();
    const gt = def.group ?? windowTitle(def.windowId);
    if (!matchesQuery(query, [def.title, gt, KIND_WORD[def.kind], def.formats.map((f) => FORMAT_LABEL[f]).join(' '), desc.provenance.label, desc.summary])) continue;
    const g = groups.get(gt) ?? { key: gt, title: gt, rows: [], rank: windowRank(def.windowId) };
    g.rows.push({ def, desc });
    groups.set(gt, g);
  }
  return [...groups.values()].sort((a, b) => a.rank - b.rank || a.title.localeCompare(b.title));
}

function useItems(): { items: ExportableDef[]; loading: boolean } {
  const ctx = useExportContext();
  const mounted = useExportables();
  const [prov, setProv] = useState<ExportableDef[]>([]);
  const [loading, setLoading] = useState(true);
  const tick = useSyncExternalStore(subscribeRegistry, providerVersion, providerVersion);
  useEffect(() => {
    const sig = { aborted: false };
    setLoading(true);
    void resolveProviders(ctx, sig).then((list) => { if (!sig.aborted) { setProv(list); setLoading(false); } });
    return () => { sig.aborted = true; };
  }, [ctx, tick]);
  const items = useMemo(() => {
    const seen = new Map<string, ExportableDef>();
    for (const d of [...prov, ...mounted]) seen.set(d.id, d);
    return [...seen.values()];
  }, [prov, mounted]);
  return { items, loading };
}

/* ── preview ────────────────────────────────────────────────────────── */
function svgSrc(svg: string): string { return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`; }

/** Preview figures read like the screen: days to one decimal, other fractions to two. The files keep full precision. */
export function previewCell(v: unknown, column: string): string {
  if (typeof v !== 'number' || !Number.isFinite(v) || Number.isInteger(v)) return String(v);
  return v.toFixed(/(^|_)d$|\(d\)|days?$/i.test(column) ? 1 : 2);
}

export function PreviewBody({ p }: { p: ExportPreview | null }) {
  if (!p) return <div className="xp-prev-empty"><Chip chip="NOT SERVED" word="No preview" /></div>;
  if (p.kind === 'svg') return <img className="xp-prev-img" src={svgSrc(p.svg)} alt="Chart preview" />;
  if (p.kind === 'image') return <img className="xp-prev-img" src={p.src} alt={p.alt} />;
  if (p.kind === 'text') return <ul className="xp-prev-text">{p.lines.map((l, i) => <li key={i}>{l}</li>)}</ul>;
  return (
    <div className="xp-prev-table" tabIndex={0} role="region" aria-label="Table preview">
      <table>
        <thead><tr>{p.columns.map((c) => <th key={c} scope="col" title={columnLabel(c) === c ? undefined : c}>{columnLabel(c)}</th>)}</tr></thead>
        <tbody>{p.rows.map((r, i) => <tr key={i}>{p.columns.map((_, j) => { const v = r[j]; return <td key={j} className={typeof v === 'number' ? 'num' : undefined}>{v == null ? <span className="xp-ns">Not served</span> : previewCell(v, p.columns[j])}</td>; })}</tr>)}</tbody>
      </table>
      <div className="xp-prev-foot mono">{`First ${p.rows.length} of ${p.total.toLocaleString('en-US')} rows`}</div>
    </div>
  );
}

function PrintButton({ def }: { def: ExportableDef }) {
  const [state, setState] = useState<'idle' | 'working' | 'blocked'>('idle');
  return (
    <div className="xp-printrow">
      <Button size="sm" variant="ghost" icon="file-down" title={SAVE_PDF_TIP} disabled={state === 'working'} data-testid="xp-print-pdf"
        onClick={() => { setState('working'); void def.print?.({ ...managerStore.get().options }).then((ok) => setState(ok ? 'idle' : 'blocked')).catch(() => setState('blocked')); }}>{SAVE_PDF_LABEL}</Button>
      {state === 'blocked' && <Chip chip="NOT SERVED" word="Print blocked" />}
    </div>
  );
}

function PreviewPane({ row }: { row: Row | null }) {
  const [rich, setRich] = useState<{ id: string; p: ExportPreview | null } | null>(null);
  const id = row?.def.id ?? null;
  useEffect(() => {
    if (!row?.def.loadPreview) { setRich(null); return; }
    let stop = false;
    void row.def.loadPreview().then((p) => { if (!stop) setRich({ id: row.def.id, p }); }).catch(() => { if (!stop) setRich({ id: row.def.id, p: null }); });
    return () => { stop = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);
  if (!row) return <div className="xp-prev xp-prev--none"><Chip chip="NOT SERVED" word="Nothing selected for preview" /></div>;
  const { def, desc } = row;
  const p = def.preview?.() ?? (rich && rich.id === def.id ? rich.p : null);
  const pv = desc.provenance;
  return (
    <div className="xp-prev" aria-live="polite">
      <div className="xp-prev-head">
        <h3 className="xp-prev-title">{def.title}</h3>
        <div className="xp-chips">
          <Chip chip={pv.label} />
          <MetaChip>{KIND_WORD[def.kind]}</MetaChip>
          {desc.state && desc.state !== 'ready' && <Chip chip="NOT SERVED" word={STATE_WORD[desc.state]} />}
        </div>
      </div>
      <PreviewBody p={p ?? (rich && rich.id === def.id ? rich.p : null)} />
      {def.print && <PrintButton def={def} />}
      <dl className="xp-meta">
        <dt>Contents</dt><dd>{desc.summary}</dd>
        {desc.columns && desc.columns.length > 0 && <><dt>Columns</dt><dd className="xp-cols">{desc.columns.slice(0, 14).map(columnLabel).join(', ')}{desc.columns.length > 14 ? `, ${desc.columns.length - 14} more` : ''}</dd></>}
        <dt>Source</dt><dd>{pv.source}</dd>
        {pv.run_id && <><dt>Run</dt><dd className="mono" title={pv.run_id}>{shortRunId(pv.run_id)}</dd></>}
        {pv.sim_run_id && <><dt>Simulation run</dt><dd className="mono" title={pv.sim_run_id}>{shortRunId(pv.sim_run_id)}</dd></>}
        {pv.as_of && <><dt>As of</dt><dd className="mono">{pv.as_of}</dd></>}
        {pv.unit && <><dt>Unit</dt><dd>{pv.unit}</dd></>}
        {pv.hash && <><dt>Hash</dt><dd className="mono" title={pv.hash}>{shortHash(pv.hash, 16)}</dd></>}
        <dt>Window</dt><dd>{def.group ?? windowTitle(def.windowId)}</dd>
      </dl>
    </div>
  );
}

/* ── list ───────────────────────────────────────────────────────────── */
export function FormatChips({ def, formats, onToggle }: { def: Pick<ExportableDef, 'id' | 'title' | 'formats'>; formats: readonly ExportFormat[]; onToggle: (f: ExportFormat) => void }) {
  return (
    <div className="xp-formats" role="group" aria-label={`Formats for ${def.title}`}>
      {def.formats.map((f) => (
        <button key={f} type="button" className={`xp-fmt${formats.includes(f) ? ' on' : ''}`} aria-pressed={formats.includes(f)} onClick={() => onToggle(f)}>{FORMAT_LABEL[f]}</button>
      ))}
    </div>
  );
}

function ItemList({ groups, focusId }: { groups: Group[]; focusId: string | null }) {
  const st = useManagerState();
  const listRef = useRef<HTMLDivElement>(null);
  const move = (e: React.KeyboardEvent) => {
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
    const names = Array.from(listRef.current?.querySelectorAll<HTMLElement>('.xp-name') ?? []);
    const i = names.indexOf(document.activeElement as HTMLElement);
    if (i < 0) return;
    e.preventDefault();
    names[Math.min(names.length - 1, Math.max(0, i + (e.key === 'ArrowDown' ? 1 : -1)))]?.focus();
  };
  return (
    <div className="xp-list" ref={listRef} onKeyDown={move}>
      {groups.map((g) => {
        const ready = g.rows.filter((r) => (r.desc.state ?? 'ready') === 'ready');
        const allOn = ready.length > 0 && ready.every((r) => !!st.selection[r.def.id]);
        return (
          <section key={g.key} className="xp-group" aria-label={g.title}>
            <header className="xp-ghead">
              <label className="xp-check">
                <input type="checkbox" checked={allOn} disabled={ready.length === 0} aria-label={`Select all in ${g.title}`}
                  onChange={() => managerStore.dispatch({ t: 'selectMany', defs: ready.map((r) => r.def), on: !allOn })} />
              </label>
              <h3 className="xp-gtitle">{g.title}</h3>
              <span className="xp-count mono">{g.rows.length}</span>
            </header>
            <ul className="xp-rows">
              {g.rows.map(({ def, desc }) => {
                const on = st.selection[def.id];
                const ok = (desc.state ?? 'ready') === 'ready';
                return (
                  <li key={def.id} className={`xp-row${on ? ' on' : ''}${focusId === def.id ? ' focus' : ''}${ok ? '' : ' off'}`}>
                    <label className="xp-check">
                      <input type="checkbox" checked={!!on} disabled={!ok} aria-label={`Select ${def.title}`} onChange={() => managerStore.dispatch({ t: 'toggle', def })} />
                    </label>
                    <button type="button" className="xp-name" aria-current={focusId === def.id ? 'true' : undefined} onClick={() => managerStore.dispatch({ t: 'focus', id: def.id })}
                      onFocus={() => managerStore.dispatch({ t: 'focus', id: def.id })}>
                      <span className="xp-title">{def.title}</span>
                      <span className="xp-sub">{desc.summary}</span>
                    </button>
                    <span className="xp-state">{ok ? <Chip chip={desc.provenance.label} /> : <Chip chip="NOT SERVED" word={STATE_WORD[desc.state ?? 'not-served']} />}</span>
                    {on && ok && <FormatChips def={def} formats={on} onToggle={(f) => managerStore.dispatch({ t: 'toggleFormat', id: def.id, format: f, allowed: def.formats })} />}
                  </li>
                );
              })}
            </ul>
          </section>
        );
      })}
    </div>
  );
}

/* ── options and results ────────────────────────────────────────────── */
function Options() {
  const st = useManagerState();
  const [pattern, setPattern] = useState(st.options.filenamePattern);
  const o = st.options;
  return (
    <div className="xp-opts" role="group" aria-label="Export options">
      <Toggle label="Single ZIP pack" checked={st.asPack} onChange={(v) => managerStore.dispatch({ t: 'pack', on: v })} />
      <Toggle label="Include figures" checked={o.figures} onChange={(v) => managerStore.dispatch({ t: 'option', patch: { figures: v } })} />
      <Toggle label="Provenance sheet" checked={o.provenanceSheet} onChange={(v) => managerStore.dispatch({ t: 'option', patch: { provenanceSheet: v } })} />
      <Toggle label="Header block in CSV" checked={o.csvHeader} onChange={(v) => managerStore.dispatch({ t: 'option', patch: { csvHeader: v } })} />
      <div className="xp-opt-row">
        <span className="xp-lbl">Figure theme</span>
        <SegmentedControl label="Figure theme" size="sm" value={o.theme} onChange={(v) => managerStore.dispatch({ t: 'option', patch: { theme: v } })}
          options={[{ value: 'light', label: 'Light' }, { value: 'dark', label: 'Dark' }]} />
      </div>
      <label className="xp-opt-row" htmlFor="xp-pattern">
        <span className="xp-lbl">File name</span>
        <input id="xp-pattern" className="xp-input mono" value={pattern} spellCheck={false} aria-describedby="xp-pattern-help"
          onChange={(e) => setPattern(e.target.value)} onBlur={() => { const p = sanitisePattern(pattern); setPattern(p); managerStore.dispatch({ t: 'option', patch: { filenamePattern: p } }); }} />
      </label>
      <span id="xp-pattern-help" className="xp-hint mono">{'{title} {kind} {window} {run} {date} {label}'}</span>
    </div>
  );
}

function CopyHash({ hash }: { hash: string }) {
  const [done, setDone] = useState(false);
  return (
    <button type="button" className="xp-hash mono" title={hash} aria-label={`Copy sha256 ${hash}`}
      onClick={() => { void navigator.clipboard?.writeText(hash).then(() => { setDone(true); setTimeout(() => setDone(false), 1500); }).catch(() => undefined); }}>
      {done ? 'Copied' : shortHash(hash, 16)}
    </button>
  );
}

/** One PDF that joins the run reports: the selected report items, or every available report when none is selected. */
function PackPdf({ items, ctx }: { items: readonly ExportableDef[]; ctx: ReturnType<typeof useExportContext> }) {
  const st = useManagerState();
  const view = usePackPdfView();
  const [busy, setBusy] = useState(false);
  const [word, setWord] = useState<{ ok: boolean; text: string } | null>(null);
  const reports = items.filter((d) => d.report && (d.describe().state ?? 'ready') === 'ready');
  const chosen = reports.filter((d) => !!st.selection[d.id]);
  const use = chosen.length ? chosen : reports;
  const go = async () => {
    setBusy(true); setWord(null);
    const r = await packPdf(use, ctx);
    setWord({ ok: r.ok, text: r.ok ? `${r.reports} ${r.reports === 1 ? 'report' : 'reports'}, ${r.pages} pages` : r.word });
    setBusy(false);
  };
  if (reports.length === 0) return null;
  return (
    <div className="xp-packpdf" role="group" aria-label="Run pack PDF">
      <Button variant="secondary" size="sm" icon="file-down" disabled={busy || use.length === 0} onClick={() => void go()} data-testid="xp-pack-pdf">
        {busy ? 'Building' : `Full run pack PDF, ${use.length} ${use.length === 1 ? 'report' : 'reports'}`}
      </Button>
      {word && <span className={`xp-note mono ${word.ok ? 'ok' : 'bad'}`} role="status">{word.text}</span>}
      {view && <button type="button" className="xp-dl" onClick={() => saveBlob(view.name, view.blob)}><Icon name="download" size="xs" /> <span className="mono">{view.name}</span></button>}
    </div>
  );
}

function Results() {
  const rv = useRunView();
  const hist = useExportHistory();
  const [showHist, setShowHist] = useState(false);
  const matched = rv.verify ? rv.verify.filter((r) => r.state === 'MATCH').length : 0;
  return (
    <div className="xp-results" aria-live="polite">
      {rv.progress && rv.busy && (
        <div className="xp-progress" role="progressbar" aria-valuemin={0} aria-valuemax={rv.progress.total} aria-valuenow={rv.progress.done} aria-label="Export progress">
          <i style={{ width: `${rv.progress.total ? Math.round((rv.progress.done / rv.progress.total) * 100) : 0}%` }} />
          <span className="mono">{`${rv.progress.done} of ${rv.progress.total}`}{rv.progress.current ? `, ${rv.progress.current}` : ''}</span>
        </div>
      )}
      {rv.rows.length > 0 && (
        <ul className="xp-res" aria-label="Exported files">
          {rv.rows.map((r) => (
            <li key={r.filename} className="xp-res-row">
              <button type="button" className="xp-dl" onClick={() => { const b = rv.blobs.get(r.filename); if (b) saveBlob(r.filename, b); }}>
                <Icon name="download" size="xs" /> <span className="mono">{r.filename}</span>
              </button>
              <span className="xp-size mono">{formatBytes(r.bytes)}</span>
              <CopyHash hash={r.sha256} />
              {r.members && (
                <details className="xp-members">
                  <summary>{`${r.members.length} files`}</summary>
                  <ul>{r.members.map((m) => <li key={m.name}><span className="mono">{m.name}</span><span className="xp-size mono">{formatBytes(m.bytes)}</span><CopyHash hash={m.sha256} /></li>)}</ul>
                </details>
              )}
            </li>
          ))}
        </ul>
      )}
      {rv.verify && (
        <div className="xp-verify">
          <Chip chip={matched === rv.verify.length ? 'MATCH' : 'DOWN'} word={matched === rv.verify.length ? 'Hashes match' : 'Hash mismatch'} />
          <span className="mono">{`${matched} of ${rv.verify.length} files checked against the manifest`}</span>
        </div>
      )}
      {rv.errors.length > 0 && (
        <ul className="xp-errs" aria-label="Items not exported">
          {rv.errors.map((e: ExportFailure, i) => <li key={`${e.id}${e.format}${i}`}><Chip chip="DOWN" word={failureWord(e)} /> <span>{`${e.title}, ${FORMAT_LABEL[e.format]}`}</span></li>)}
        </ul>
      )}
      <div className="xp-hist">
        <button type="button" className="xp-link" aria-expanded={showHist} onClick={() => setShowHist((v) => !v)}>{`Recent exports ${hist.length}`}</button>
        {showHist && (
          <>
            <ul className="xp-hist-list">
              {hist.length === 0 && <li><Chip chip="NOT SERVED" word="None yet" /></li>}
              {hist.map((h, i) => (
                <li key={`${h.sha256}${i}`}><span className="mono xp-hist-at">{h.at.slice(0, 16).replace('T', ' ')}Z</span><span className="mono">{h.filename}</span><span className="xp-size mono">{formatBytes(h.bytes)}</span><CopyHash hash={h.sha256} /></li>
              ))}
            </ul>
            {hist.length > 0 && <button type="button" className="xp-link" onClick={() => managerStore.clearHistory()}>Clear history</button>}
          </>
        )}
      </div>
    </div>
  );
}

/* ── window ─────────────────────────────────────────────────────────── */
export function ExportWindow() {
  const ctx = useExportContext();
  const st = useManagerState();
  const rv = useRunView();
  const { items, loading } = useItems();
  const groups = useMemo(() => groupRows(items, st.query), [items, st.query, st.selection]); // eslint-disable-line react-hooks/exhaustive-deps
  const flat = useMemo(() => groups.flatMap((g) => g.rows), [groups]);
  const focusRow = flat.find((r) => r.def.id === st.focus) ?? flat[0] ?? null;
  useEffect(() => { if (!loading) managerStore.dispatch({ t: 'prune', ids: items.map((d) => d.id) }); }, [items, loading]);

  const n = selectedCount(st); const files = fileCount(st);
  const run = useCallback(() => { void startExport(items, ctx); }, [items, ctx]);
  const stamp = n ? `${n} selected` : `${items.length} available`;

  return (
    <Panel id="expt" code="EXPT" title="Export" updated={stamp} className="xp-panel">
      <div className="xp-root">
        <div className="xp-top">
          <label className="xp-search" htmlFor="xp-q">
            <Icon name="search" size="sm" />
            <input id="xp-q" type="search" className="xp-input" value={st.query} placeholder="Search items" spellCheck={false}
              onChange={(e) => managerStore.dispatch({ t: 'query', q: e.target.value })} aria-label="Search items" />
          </label>
          <div className="xp-ctx">
            {ctx.runId ? <MetaChip title={ctx.runId}>{`Run ${shortRunId(ctx.runId)}`}</MetaChip> : <Chip chip="NOT SERVED" word="No run" />}
            {ctx.simRunId ? <MetaChip title={ctx.simRunId}>{`Simulation ${shortRunId(ctx.simRunId)}`}</MetaChip> : <Chip chip="NOT SERVED" word="No simulation run" />}
          </div>
          <div className="xp-bulk">
            <Button size="sm" variant="ghost" onClick={() => managerStore.dispatch({ t: 'selectMany', defs: flat.filter((r) => (r.desc.state ?? 'ready') === 'ready').map((r) => r.def), on: true })}>Select shown</Button>
            <Button size="sm" variant="ghost" disabled={n === 0} onClick={() => managerStore.dispatch({ t: 'clear' })}>Clear</Button>
          </div>
        </div>
        <div className="xp-main">
          <div className="xp-left" role="region" aria-label="Available items">
            {loading && items.length === 0 && <div className="xp-note"><Chip chip="INFO" word="Loading" /></div>}
            {!loading && groups.length === 0 && <div className="xp-note"><Chip chip="NOT SERVED" word={st.query ? 'No match' : 'Nothing to export'} /></div>}
            <ItemList groups={groups} focusId={focusRow?.def.id ?? null} />
          </div>
          <div className="xp-right">
            <PreviewPane row={focusRow} />
          </div>
        </div>
        <div className="xp-foot">
          <Options />
          <PackPdf items={items} ctx={ctx} />
          <div className="xp-go">
            <Button variant="primary" disabled={n === 0 || rv.busy} onClick={run} icon="file-down">
              {rv.busy ? 'Exporting' : n ? `Export selected, ${files} ${files === 1 ? 'file' : 'files'}${st.asPack ? ' in one pack' : ''}` : 'Export selected'}
            </Button>
          </div>
          <Results />
        </div>
      </div>
    </Panel>
  );
}
