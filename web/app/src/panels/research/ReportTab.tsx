import React, { useCallback, useEffect, useRef, useState } from 'react';
import { confirmCard, fmtCost, statusLabel } from '../../agent/timeline';
import { ModeMark, BadgeFace, PopButton, PopRows } from '../../meta/AiBadge';
import { NA } from '../../meta/provenance';
import { openProvenance } from '../../provenance/registry';
import { nodeName } from '../../seed';
import { SAVE_PDF_LABEL, SAVE_PDF_TIP, printHtmlAsPdf } from '../../exports/printPdf';
import { Chip, MetaChip, StateLine } from '../reports/chips';
import { isBusyPhase, hasAnalysis } from './reportController';
import {
  CONTEXT_CHIP, CONTEXT_TITLE, EXPORT_LABEL, evidenceById, evidenceKindWord, evidenceTarget, evidenceTitle, exportFormats, exportUrl, findingsCiting, liveTask, MODE_WORD,
  readable, recordedTaskFor, reportErrorWord, usd, type ContextLine, type EvidenceRow, type EvidenceTarget, type Mode, type ReportFinding, type ReportNote, type Verification,
} from './reportModel';
import { evidenceProvenance } from './reportProv';
import { getReport, useReport } from './reportSingleton';
import { datasetLabel } from './plan';
import { caveatWord } from './vocab';
import { KIND_LABEL, type AnalysisKind, type Episode } from './types';

/* RESEARCH REPORT (Report tab of the Research window). Meta AI writes a note from the evidence an agent session
 * recorded; code checks every number and date against that evidence. The tab shows the note, its findings with the
 * evidence each one cites, the check result as counts, and exports. Recorded by default: a Live run asks first. */

export interface ReportContext {
  episode: Episode | null;
  kind: AnalysisKind;
  series: string | null;
  eventId: string | null;
  /** The workbench is showing the recorded copy (the service did not answer). */
  copyRecorded: boolean;
}

const fmtNum = (v: number | string | null): string => (typeof v === 'number' ? v.toLocaleString('en-US', { maximumFractionDigits: 6 }) : v ?? NA);

export function VerifyChips({ v }: { v: Verification }) {
  return (
    <span className="rk-row rk-wrap rpt-verify" data-testid="report-verification" data-status={v.status}>
      {v.chips.map((c, i) => (i === 0
        ? (v.status === 'VERIFIED' ? <Chip key={c} chip="SOURCED" word="Verified" /> : <Chip key={c} chip="FLAG" word="Unverified" />)
        : <MetaChip key={c}>{c}</MetaChip>))}
    </span>
  );
}

function MetaAiBadge({ mode, model, prompt, cost, estimate }: { mode: Mode; model: string | null; prompt: string | null; cost: number | null; estimate: number | null }) {
  const m = mode === 'LIVE' ? 'live' : 'recorded';
  const rows = [
    { k: 'Model', v: model ?? NA }, { k: 'Mode', v: MODE_WORD[mode] }, { k: 'Prompt id', v: prompt ?? NA },
    { k: 'Cost', v: mode === 'LIVE' ? usd(cost) ?? NA : NA }, { k: 'Estimate', v: usd(estimate) ?? NA },
  ];
  return (
    <PopButton className={`ai-badge ai-badge--${m}`} ariaLabel={`Meta AI, ${m}`} head="Meta AI" data={{ 'data-transport': mode, 'data-ai-badge': m }} body={<PopRows rows={rows} />}>
      <BadgeFace mode={m} />
    </PopButton>
  );
}

function EvidenceChip({ id, rows, selected, onPick }: { id: string; rows: EvidenceRow[]; selected: boolean; onPick: (id: string) => void }) {
  const row = rows.find((r) => r.id === id) ?? null;
  return (
    <button type="button" className={`rpt-ev${selected ? ' on' : ''}`} data-evidence={id} aria-pressed={selected} onClick={() => onPick(id)}
      aria-label={`Evidence ${id}${row ? `, ${evidenceKindWord(row.kind)}` : ''}`}>
      <b>{id}</b>{row && <span>{evidenceKindWord(row.kind)}</span>}
    </button>
  );
}

function Finding({ f, rows, picked, onPick }: { f: ReportFinding; rows: EvidenceRow[]; picked: string | null; onPick: (id: string) => void }) {
  return (
    <li className={`rpt-finding rpt-finding--${f.state}`} data-finding={f.id} data-state={f.state}>
      <div className="rk-row rk-wrap">
        <MetaChip>{f.id}</MetaChip>
        {f.state === 'verified' ? <Chip chip="SOURCED" word="Verified" /> : <Chip chip="FLAG" word="Flagged" />}
        {f.evidence.map((id) => <EvidenceChip key={id} id={id} rows={rows} selected={picked === id} onPick={onPick} />)}
      </div>
      <p className="rpt-text">{readable(f.text)}</p>
      {f.numbers.length > 0 && (
        <details className="rpt-figs">
          <summary>{`Figures ${f.numbers.length}`}</summary>
          <dl className="rsh-kv">
            {f.numbers.map((n, i) => (
              <div key={`${n.name}-${i}`}><dt>{readable(n.name)}</dt><dd>{`${fmtNum(n.value)}${n.unit ? ` ${n.unit}` : ''}`}{n.evidence ? ` (${n.evidence})` : ''}</dd></div>
            ))}
          </dl>
        </details>
      )}
    </li>
  );
}

function ContextItem({ c, rows, picked, onPick }: { c: ContextLine; rows: EvidenceRow[]; picked: string | null; onPick: (id: string) => void }) {
  return (
    <li className={`rpt-finding rpt-finding--unchecked rpt-context--${c.state}`} data-context={c.id} data-state={c.state}>
      <div className="rk-row rk-wrap">
        <MetaChip>{c.id}</MetaChip>
        <MetaChip>{CONTEXT_CHIP}</MetaChip>
        {c.evidence.map((id) => <EvidenceChip key={id} id={id} rows={rows} selected={picked === id} onPick={onPick} />)}
      </div>
      <p className="rpt-text">{c.state === 'flagged' ? c.text : readable(c.text)}</p>
    </li>
  );
}

export function NoteView({ note, sessionId, pdf, onShow }: { note: ReportNote; sessionId: string; pdf: boolean; onShow: (t: EvidenceTarget) => void }) {
  const [picked, setPicked] = useState<string | null>(null);
  const [printState, setPrintState] = useState<'idle' | 'working' | 'error'>('idle');
  const savePdf = useCallback(async () => {
    setPrintState('working');
    try {
      const r = await fetch(exportUrl(sessionId, 'html', note.id), { headers: { Accept: 'text/html' } });
      if (!r.ok) throw new Error('http');
      setPrintState(printHtmlAsPdf(await r.text(), { label: note.id }) ? 'idle' : 'error');
    } catch { setPrintState('error'); }
  }, [sessionId, note.id]);
  const tableRef = useRef<HTMLTableElement | null>(null);
  const lookup = note.evidence;
  const pick = useCallback((id: string) => {
    setPicked(id);
    const e = evidenceById(note.evidence, id);
    if (e) openProvenance(evidenceProvenance(e, note));
    tableRef.current?.querySelector(`[data-evidence-row="${id}"]`)?.scrollIntoView?.({ block: 'nearest' });
  }, [note]);
  const mc = note.modelCall;
  const live = note.label.state === 'LIVE';
  return (
    <div className="rsh-sec" data-testid="report-note" data-note={note.id} data-mode={note.label.state}>
      <div className="rk-row rk-wrap">
        <h3 className="rsh-h rpt-title">{note.title}</h3>
        <MetaAiBadge mode={note.label.state} model={note.label.model} prompt={note.label.promptId} cost={mc.costUsd} estimate={mc.estimatedUsd} />
        <VerifyChips v={note.verification} />
      </div>
      {note.question && <p className="rpt-q" data-testid="report-question">{note.question}</p>}

      <section className="rsh-sec" aria-label="Findings">
        <h3 className="rsh-h">Findings <MetaChip>{`${note.findings.length}`}</MetaChip></h3>
        {note.findings.length === 0 ? <StateLine chip="NOT SERVED" word="No findings passed the check" />
          : <ol className="rpt-findings" data-testid="report-findings">{note.findings.map((f) => <Finding key={f.id} f={f} rows={lookup} picked={picked} onPick={pick} />)}</ol>}
      </section>

      {note.context.length > 0 && (
        <section className="rsh-sec" aria-label={CONTEXT_TITLE} data-testid="report-context">
          <h3 className="rsh-h">{CONTEXT_TITLE} <MetaChip>{`${note.context.length}`}</MetaChip></h3>
          <ul className="rpt-findings" data-testid="report-context-list">{note.context.map((c) => <ContextItem key={c.id} c={c} rows={lookup} picked={picked} onPick={pick} />)}</ul>
        </section>
      )}

      <section className="rsh-sec" aria-label="Data used" data-testid="report-data">
        <h3 className="rsh-h">Data used <MetaChip>{`${note.dataUsed.length}`}</MetaChip></h3>
        {note.dataUsed.length === 0 ? <StateLine chip="NOT SERVED" word="No series" /> : (
          <div className="rsh-scroll">
            <table className="rk-table">
              <thead><tr><th>Dataset</th><th>Places</th><th>Unit</th><th>Observed</th><th>State</th><th>Evidence</th></tr></thead>
              <tbody>
                {note.dataUsed.map((d) => (
                  <tr key={d.id}>
                    <td>{datasetLabel(d.id)}{d.source ? <span className="rk-count">{` ${d.source}`}</span> : null}</td>
                    <td>{d.places.map((p) => nodeName(p)).join(', ') || NA}</td>
                    <td>{d.unit ?? NA}</td>
                    <td className="rk-num">{d.firstObs && d.lastObs ? `${d.firstObs} to ${d.lastObs}` : NA}</td>
                    <td>{d.state === 'not_served' ? <Chip chip="NOT SERVED" word="Not served" /> : d.state === 'live' ? <Chip chip="LIVE" word="Live" /> : d.state === 'recorded' ? <Chip chip="SNAPSHOT" word="Recorded" /> : <Chip chip="SNAPSHOT" word="Cached" />}</td>
                    <td>{d.evidence.map((id) => <EvidenceChip key={id} id={id} rows={lookup} selected={picked === id} onPick={pick} />)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="rsh-sec" aria-label="Evidence" data-testid="report-evidence">
        <h3 className="rsh-h">Evidence <MetaChip>{`${note.evidence.length}`}</MetaChip></h3>
        <div className="rsh-scroll">
          <table className="rk-table" ref={tableRef}>
            <thead><tr><th>Id</th><th>Entry</th><th className="rk-r">n</th><th>Caveats</th><th>Cited by</th><th /></tr></thead>
            <tbody>
              {note.evidence.map((e) => (
                <tr key={e.id} data-evidence-row={e.id} className={picked === e.id ? 'rpt-row-on' : undefined} aria-selected={picked === e.id}>
                  <td className="rk-num"><b>{e.id}</b></td>
                  <td>{evidenceTitle(e)}</td>
                  <td className="rk-r rk-num">{e.n ?? NA}</td>
                  <td>{e.caveats.length ? <span className="rk-row rk-wrap">{e.caveats.map((c) => <MetaChip key={c}>{caveatWord(c)}</MetaChip>)}</span> : <span className="rk-count">None listed</span>}</td>
                  <td className="rk-num">{findingsCiting(note, e.id).join(', ') || NA}</td>
                  <td>
                    <span className="rk-row">
                      <button type="button" className="rk-btn" data-evidence-prov={e.id} onClick={() => pick(e.id)}>Where from</button>
                      <button type="button" className="rk-btn" data-evidence-show={e.id} onClick={() => { setPicked(e.id); onShow(evidenceTarget(e)); }}>Show in workbench</button>
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="rsh-sec" aria-label="Limitations" data-testid="report-limitations">
        <h3 className="rsh-h">Limitations <MetaChip>{`${note.limitations.length}`}</MetaChip></h3>
        <ul className="rpt-list">{note.limitations.map((l, i) => <li key={`${i}-${l}`}>{l}</li>)}</ul>
      </section>

      <details className="rpt-method" data-testid="report-methods">
        <summary>Methods</summary>
        <ul className="rpt-list">{note.methods.map((m, i) => <li key={`${i}-${m}`}>{m}</li>)}</ul>
      </details>

      <section className="rsh-sec" aria-label="Export report">
        <h3 className="rsh-h">Export</h3>
        <div className="rk-row rk-wrap">
          {exportFormats(pdf).map((f) => (
            <a key={f} className="rk-btn" href={exportUrl(sessionId, f, note.id)} download data-testid={`report-export-${f}`}>{EXPORT_LABEL[f]}</a>
          ))}
          {!pdf && <button type="button" className="rk-btn" title={SAVE_PDF_TIP} disabled={printState === 'working'} onClick={() => { void savePdf(); }} data-testid="report-export-print-pdf">{SAVE_PDF_LABEL}</button>}
          {printState === 'error' && <Chip chip="NOT SERVED" word="Print blocked" />}
        </div>
      </section>
      <p className="rsh-foot" data-testid="report-label">{note.label.statistics}</p>
      <p className="rsh-foot">{live ? note.label.text : 'Recorded script'}</p>
    </div>
  );
}

export function ReportTab({ ctx, onShow }: { ctx: ReportContext; onShow: (t: EvidenceTarget) => void }) {
  const { state: s, meta } = useReport();
  const report = getReport();
  const [confirmWrite, setConfirmWrite] = useState(false);
  useEffect(() => { if (!meta.loaded) void report.loadMeta(); }, [report, meta.loaded]);

  const ep = ctx.episode;
  const busy = isBusyPhase(s.phase);
  const transport: Mode | null = s.agent.transport ?? meta.transport;
  const noScript = !!ep && meta.transport !== 'LIVE' && recordedTaskFor(ep.id, meta.recordedTasks) === null;
  const agentOff = meta.enabled === false;
  const canStart = meta.loaded && !!ep && !busy && !agentOff && !noScript;

  const start = () => {
    if (!ep) return;
    void report.begin({
      episodeId: ep.id,
      task: (t, recorded) => (t === 'RECORDED' ? recordedTaskFor(ep.id, recorded) : liveTask({ episode: ep, kind: ctx.kind, series: ctx.series, eventId: ctx.eventId })),
    });
  };

  const label = statusLabel(s.agent.status, s.agent.stopReason);
  const limits = s.agent.limits;
  const pendingCards = s.agent.pending.map((a) => ({ card: confirmCard(a), est: a.est_cost_usd, spend: a.can_spend }));
  const live = transport === 'LIVE';
  const note = s.doc?.note ?? null;

  return (
    <div className="rsh-sec" data-testid="report-root" data-phase={s.phase} data-transport={transport ?? 'unknown'}>
      <div className="rk-row rk-wrap" data-testid="report-bar">
        {transport && <span className={`ai-badge ai-badge--${live ? 'live' : 'recorded'} rpt-mode`} data-ai-badge={live ? 'live' : 'recorded'}><ModeMark mode={live ? 'live' : 'recorded'} /> <span className="ai-badge-w">Meta AI</span> <b>{MODE_WORD[transport]}</b></span>}
        {ep && <MetaChip>{ep.title}</MetaChip>}
        {ep && <MetaChip>{KIND_LABEL[ctx.kind]}</MetaChip>}
        <button type="button" className="rk-btn rk-btn--primary" data-testid="report-write" disabled={!canStart} onClick={start}>{s.phase === 'ready' ? 'New report' : 'Write report'}</button>
        {busy && s.phase !== 'confirm_live' && <button type="button" className="rk-btn" data-testid="report-cancel" onClick={() => { void report.cancel(); }}>Stop</button>}
        {noScript && meta.loaded && <Chip chip="NOT SERVED" word="No recorded report for this episode" />}
        {agentOff && <Chip chip="NOT SERVED" word="Meta AI off" />}
        {ctx.copyRecorded && <MetaChip>Workbench: recorded copy</MetaChip>}
      </div>

      {s.phase === 'creating' && <StateLine chip="NOT SERVED" word="Starting" />}

      {s.phase === 'confirm_live' && (
        <div className="rpt-card" data-testid="report-confirm-live">
          <div className="rk-row rk-wrap">
            <Chip chip="LIVE" word="Live run" />
            <MetaChip>{s.agent.model ?? 'Meta AI'}</MetaChip>
            {limits && <MetaChip>{`Spend limit ${usd(limits.max_spend_usd)}`}</MetaChip>}
            {limits && <MetaChip>{`${limits.max_steps} steps`}</MetaChip>}
            <button type="button" className="rk-btn rk-btn--primary" data-testid="report-confirm-live-yes" onClick={() => { void report.confirmLive(); }}>Confirm live run</button>
            <button type="button" className="rk-btn" data-testid="report-confirm-live-no" onClick={() => { void report.cancel(); }}>Cancel</button>
          </div>
        </div>
      )}

      {(s.phase === 'gathering' || s.phase === 'awaiting_confirmation' || s.phase === 'writing') && (
        <div className="rk-row rk-wrap" role="status" data-testid="report-progress">
          <MetaChip>{s.phase === 'writing' ? 'Writing report' : label.word}</MetaChip>
          <MetaChip>{`${s.agent.steps.length} steps`}</MetaChip>
          {s.agent.usage && live && <MetaChip>{`Spent ${fmtCost(s.agent.usage.spent_usd)}`}</MetaChip>}
          {!s.own && <MetaChip>Session from the Meta AI window</MetaChip>}
        </div>
      )}

      {pendingCards.map(({ card, est, spend }) => (
        <div key={card.id} className="rpt-card" data-testid="report-pending" data-action={card.tool}>
          <div className="rk-row rk-wrap">
            <b>{card.title}</b>
            <span className="rpt-text">{card.summary}</span>
            {spend || est > 0 ? <MetaChip>{`Estimated ${usd(est) ?? NA}`}</MetaChip> : <MetaChip>No cost</MetaChip>}
            {limits && <MetaChip>{`Spend limit ${usd(limits.max_spend_usd)}`}</MetaChip>}
            <button type="button" className="rk-btn rk-btn--primary" data-testid="report-pending-yes" onClick={() => { void report.confirm(card.id); }}>Confirm</button>
            <button type="button" className="rk-btn" data-testid="report-pending-no" onClick={() => { void report.reject(card.id); }}>Reject</button>
          </div>
        </div>
      ))}

      {s.phase === 'gathered' && (
        <div className="rpt-card" data-testid="report-gathered">
          <div className="rk-row rk-wrap">
            <MetaChip>{`${s.evidence.length} evidence entries`}</MetaChip>
            <button type="button" className="rk-btn rk-btn--primary" data-testid="report-write-note" disabled={!hasAnalysis(s.evidence)}
              onClick={() => { if (live) setConfirmWrite(true); else void report.writeNote(); }}>Write note</button>
            {confirmWrite && live && (
              <>
                <MetaChip>{`Spend limit ${usd(limits?.max_spend_usd)}`}</MetaChip>
                <button type="button" className="rk-btn rk-btn--primary" data-testid="report-write-note-yes" onClick={() => { setConfirmWrite(false); void report.writeNote(); }}>Confirm live call</button>
                <button type="button" className="rk-btn" onClick={() => setConfirmWrite(false)}>Cancel</button>
              </>
            )}
          </div>
        </div>
      )}

      {s.error && s.phase !== 'idle' && <StateLine chip={s.phase === 'failed' ? 'DOWN' : 'NOT SERVED'} word={reportErrorWord(s.error)} code={s.error} />}

      {s.phase === 'idle' && !note && <StateLine chip="NOT SERVED" word="No report" />}

      {note && s.sessionId && <NoteView note={note} sessionId={s.sessionId} pdf={meta.pdf === true} onShow={onShow} />}
    </div>
  );
}
