/**
 * DISRUPTION BRIEF window (code BRF): a one-page institutional note written
 * from a run. Every figure is a fact read from the engine (click it for its
 * source); the text is machine-checked against those facts and the check is
 * shown as a count. Exports: HTML, PDF, JSON.
 *
 * No explanatory prose: chips and state labels only. The user-visible name is
 * Meta AI; a recorded brief is labelled Recorded and is never shown as live.
 */
import React, { useEffect, useId, useMemo, useRef, useState } from 'react';
import { ModeMark } from '../meta/AiBadge';
import { Panel } from '../components/Panel';
import { SafeLink } from '../components/SafeLink';
import { EmptyState } from '../shell/Chip';
import { useDeskStore } from '../store';
import { Button, Status, type StateKind } from '../ui';
import { errorWord, reasonWord, safeSvg } from './client';
import { SAVE_PDF_LABEL, SAVE_PDF_TIP } from '../exports/printPdf';
import { getBrief, useBrief } from './controller';
import type { BriefBlock, BriefDoc, BriefFact, BriefSection, Seg, TableBlock } from './types';
import './brief.css';

const CHIP_STATE: Record<string, { state: StateKind; word: string }> = {
  MODELLED: { state: 'model', word: 'Modelled' }, SIMULATED: { state: 'sim', word: 'Simulated' }, SOURCED: { state: 'meta', word: 'Sourced' },
  INPUT: { state: 'meta', word: 'Input' }, SNAPSHOT: { state: 'snapshot', word: 'Snapshot' }, LIVE: { state: 'live', word: 'Live' },
  'NOT SERVED': { state: 'notserved', word: 'Not served' }, RECORDED: { state: 'snapshot', word: 'Recorded' },
};
export function Chip({ chip }: { chip: string }) {
  const c = CHIP_STATE[chip] ?? { state: 'meta' as StateKind, word: chip };
  return <Status state={c.state} word={c.word} />;
}

export const nbsp = (s: string): string => s.replace(/ /g, ' ');

/* ── fact chips ───────────────────────────────────────────────────── */
interface FactCtx { facts: Map<string, BriefFact>; briefId: string; open: (id: string, el: HTMLElement) => void; active: string | null }

function FactSpan({ seg, ctx }: { seg: Seg; ctx: FactCtx }) {
  const id = seg.fact as string;
  return (
    <button type="button" className={`bf-fact${ctx.active === id ? ' bf-fact--on' : ''}`} data-fact={id} data-prov={`brief:${ctx.briefId}:${id}`}
      aria-label={`${ctx.facts.get(id)?.label ?? 'Fact'}: ${seg.t}`} onClick={(e) => ctx.open(id, e.currentTarget)}>
      {seg.t}<sup className="bf-fid">{id}</sup>
    </button>
  );
}
function Segments({ segs, ctx }: { segs: Seg[]; ctx: FactCtx }) {
  return <>{segs.map((s, i) => (s.fact ? <FactSpan key={i} seg={s} ctx={ctx} /> : <React.Fragment key={i}>{s.t}</React.Fragment>))}</>;
}

/** The source popover of one fact. Until the shared provenance drawer exists
 *  this is the whole answer to "where does this number come from". */
export function FactPopover({ fact, anchor, onClose }: { fact: BriefFact; anchor: DOMRect; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    const onDown = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node) && !(e.target as HTMLElement).closest('.bf-fact')) onClose(); };
    document.addEventListener('keydown', onKey); document.addEventListener('mousedown', onDown);
    ref.current?.focus();
    return () => { document.removeEventListener('keydown', onKey); document.removeEventListener('mousedown', onDown); };
  }, [onClose]);
  const s = fact.source;
  const vh = typeof window === 'undefined' ? 900 : window.innerHeight;
  const vw = typeof window === 'undefined' ? 1400 : window.innerWidth;
  const top = Math.min(anchor.bottom + 6, vh - 190);
  const left = Math.max(8, Math.min(anchor.left, vw - 320));
  const rows: Array<[string, React.ReactNode]> = [['Fact', `${fact.id}, ${fact.label}`], ['Value', fact.display], ['Source', s.label]];
  if (s.type === 'citation') {
    if (s.publisher) rows.push(['Publisher', s.publisher]);
    if (s.title) rows.push(['Title', s.title]);
    if (s.date) rows.push(['Date', String(s.date)]);
    if (s.url) rows.push(['Link', <SafeLink key="l" href={s.url}>{String(s.url).replace(/^https:\/\//, '').slice(0, 48)}</SafeLink>]);
  }
  if (s.retrieved_at) rows.push(['Retrieved', String(s.retrieved_at)]);
  if (typeof s.run_id === 'string') rows.push(['Run', s.run_id]);
  if (typeof s.seq === 'number') rows.push(['Chain event', String(s.seq)]);
  if (typeof s.plan_id === 'string') rows.push(['Plan', s.plan_id]);
  if (typeof s.job_id === 'string') rows.push(['Job', s.job_id]);
  return (
    <div ref={ref} className="bf-pop" role="dialog" aria-label={`Source of ${fact.id}`} tabIndex={-1} style={{ top, left }} data-fact-pop={fact.id}>
      <div className="bf-pop-head"><Chip chip={fact.chip} /><button type="button" className="bf-pop-x" aria-label="Close" onClick={onClose}>Close</button></div>
      <dl className="bf-pop-rows">{rows.map(([k, v]) => (<React.Fragment key={k}><dt>{k}</dt><dd>{v}</dd></React.Fragment>))}</dl>
    </div>
  );
}

/* ── blocks ───────────────────────────────────────────────────────── */
function TableView({ b, ctx }: { b: TableBlock; ctx: FactCtx }) {
  return (
    <div className="bf-tablewrap">
      <table className="bf-table">
        <caption>{b.title}{b.chip && <> <Chip chip={b.chip} /></>}</caption>
        <thead><tr>{b.columns.map((c) => <th key={c.label} className={c.num ? 'num' : ''}>{c.label}{c.unit && <span className="bf-u"> [{c.unit}]</span>}</th>)}</tr></thead>
        <tbody>
          {b.rows.map((r, i) => (
            <tr key={i} data-fact={r.fact_id ?? undefined}>
              {r.cells.map((c, j) => {
                const col = b.columns[j];
                if (r.state && col?.label === 'State') return <td key={j}><Chip chip={r.state} /></td>;
                return (
                  <td key={j} className={col?.num ? 'num' : ''}>
                    {j === 0 && r.fact_id ? <button type="button" className="bf-fact bf-fact--cell" data-prov={`brief:${ctx.briefId}:${r.fact_id}`} aria-label={`Source of ${c}`} onClick={(e) => ctx.open(r.fact_id as string, e.currentTarget)}>{c}<sup className="bf-fid">{r.fact_id}</sup></button> : c}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      {b.note && <div className="bf-note">{b.note}</div>}
    </div>
  );
}

function BlockView({ b, doc, ctx, figNo }: { b: BriefBlock; doc: BriefDoc; ctx: FactCtx; figNo: Map<string, number> }) {
  switch (b.kind) {
    case 'paragraph': return <p className="bf-p" data-source={b.source}><Segments segs={b.segments} ctx={ctx} /></p>;
    case 'bullets': return <ul className="bf-ul" data-source={b.source}>{b.items.map((i, k) => <li key={k}><Segments segs={i.segments} ctx={ctx} /></li>)}</ul>;
    case 'table': return <TableView b={b} ctx={ctx} />;
    case 'figure': {
      const f = doc.figures.find((x) => x.id === b.figure_id);
      const svg = f ? safeSvg(f.svg) : null;
      if (!f || !svg) return null;
      return (
        <figure className="bf-fig" data-figure={f.id}>
          <img className="bf-svg" alt={f.title} src={`data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`} />
          <figcaption>Figure {figNo.get(f.id)}. {f.title} <Chip chip={f.chip} /></figcaption>
        </figure>
      );
    }
    default: return null;
  }
}

function SectionView({ s, n, doc, ctx, figNo }: { s: BriefSection; n: number; doc: BriefDoc; ctx: FactCtx; figNo: Map<string, number> }) {
  return (
    <section className="bf-sec" data-section={s.id} data-state={s.state}>
      <h3 className="bf-h"><span className="bf-n">{n}</span><span>{s.title}</span><span className="bf-chips">{s.chips.map((c) => <Chip key={c} chip={c} />)}</span></h3>
      {s.state !== 'SERVED'
        ? <div className="bf-ns"><Chip chip="NOT SERVED" /><span>{s.reason}</span></div>
        : s.blocks.map((b) => <BlockView key={b.id} b={b} doc={doc} ctx={ctx} figNo={figNo} />)}
    </section>
  );
}

const fmtStamp = (iso: string): string => {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '-';
  const m = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][d.getUTCMonth()];
  return `${d.getUTCDate()} ${m} ${d.getUTCFullYear()} ${String(d.getUTCHours()).padStart(2, '0')}:${String(d.getUTCMinutes()).padStart(2, '0')} UTC`;
};

/** The rendered note. Pure over the document (no store needed). */
export function BriefNote({ doc }: { doc: BriefDoc }) {
  const facts = useMemo(() => new Map(doc.facts.map((f) => [f.id, f])), [doc]);
  const [pop, setPop] = useState<{ id: string; rect: DOMRect } | null>(null);
  const ctx: FactCtx = { facts, briefId: doc.brief_id, active: pop?.id ?? null, open: (id, el) => setPop((p) => (p?.id === id ? null : { id, rect: el.getBoundingClientRect() })) };
  const figNo = useMemo(() => {
    const m = new Map<string, number>();
    for (const s of doc.sections) for (const b of s.blocks) if (b.kind === 'figure' && !m.has(b.figure_id)) m.set(b.figure_id, m.size + 1);
    return m;
  }, [doc]);
  const live = doc.transport === 'LIVE';
  return (
    <article className="bf-note" data-brief={doc.brief_id} data-transport={doc.transport}>
      <header className="bf-mast">
        <div className="bf-kicker"><span>Disruption brief</span><span className="bf-sp" /><Chip chip="MODELLED" /><Chip chip={live ? 'LIVE' : 'RECORDED'} /></div>
        <h2 className="bf-title">{doc.subject}</h2>
        <p className="bf-deck"><Segments segs={doc.summary.segments} ctx={ctx} /></p>
        <dl className="bf-meta">
          <div><dt>Generated</dt><dd>{fmtStamp(doc.generated_at)}</dd></div>
          <div><dt>Run</dt><dd>{doc.run.run_id}</dd></div>
          <div><dt>Chain hash</dt><dd>{(doc.run.stream_hash ?? '').slice(0, 12) || '-'}</dd></div>
          <div><dt>Brief</dt><dd>{doc.brief_id}</dd></div>
        </dl>
      </header>
      <div className="bf-strip">
        {doc.key_figures.map((k) => (
          <button type="button" key={k.fact_id} className="bf-kf" data-fact={k.fact_id} data-prov={`brief:${doc.brief_id}:${k.fact_id}`} onClick={(e) => ctx.open(k.fact_id, e.currentTarget)}>
            <span className="bf-kf-l">{k.label}</span><span className="bf-kf-v">{k.value}</span><Chip chip={k.chip} />
          </button>
        ))}
      </div>
      {doc.sections.map((s, i) => <SectionView key={s.id} s={s} n={i + 1} doc={doc} ctx={ctx} figNo={figNo} />)}
      <section className="bf-sec" data-section="sources">
        <h3 className="bf-h"><span className="bf-n">{doc.sections.length + 1}</span><span>Sources</span></h3>
        <ol className="bf-sources">
          {doc.sources.map((s) => (
            <li key={s.id} id={`bf-${s.id}`}>
              <span className="bf-sid">{s.id}</span>
              <span>
                {s.kind === 'citation' ? <><SafeLink href={s.url}>{s.publisher}</SafeLink>{s.title ? `, ${s.title}` : ''}{s.date ? `, ${s.date}` : ''} <Chip chip="SOURCED" /></>
                  : <>{s.label}{s.state && <> <Chip chip={s.state} /></>}{s.retrieved_at ? `, retrieved ${fmtStamp(s.retrieved_at)}` : ''}</>}
                <span className="bf-refs"> Facts {s.fact_ids.slice(0, 10).join(', ')}{s.fact_ids.length > 10 ? ` and ${s.fact_ids.length - 10} more` : ''}</span>
              </span>
            </li>
          ))}
        </ol>
      </section>
      <footer className="bf-foot"><div>{doc.label}</div><div className="bf-hash">Content sha256 {doc.sha256}</div></footer>
      {pop && facts.get(pop.id) && <FactPopover fact={facts.get(pop.id) as BriefFact} anchor={pop.rect} onClose={() => setPop(null)} />}
    </article>
  );
}

/* ── header bar ───────────────────────────────────────────────────── */
function VerifyChip({ doc }: { doc: BriefDoc }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const v = doc.verifier;
  const clean = v.flagged.length === 0 && v.dropped === 0;
  return (
    <span className="bf-verify">
      <button type="button" className="bf-vbtn" aria-expanded={open} aria-controls={id} onClick={() => setOpen((o) => !o)} data-verified={`${v.passed}/${v.checked}`}>
        <Status state={clean ? 'live' : 'snapshot'} word={`Verified ${v.passed} of ${v.checked} statements`} />
      </button>
      {open && (
        <span id={id} className="bf-vlist" role="status">
          {v.flagged.length === 0 && v.dropped === 0 ? <Status state="meta" word="No block replaced" /> : null}
          {v.flagged.map((f, i) => <Status key={i} state="snapshot" word={`${f.block.replace(/\./g, ' ')}: ${reasonWord(f.reason)}`} />)}
          {v.dropped > 0 && <Status state="snapshot" word={`${v.dropped} blocks dropped`} />}
        </span>
      )}
    </span>
  );
}

export function BriefPanel() {
  const s = useBrief();
  const desk = useDeskStore();
  const brief = getBrief();
  const doc = s.doc;
  const runId = desk.chain?.run_id ?? null;
  useEffect(() => { void brief.refreshStatus(); }, [brief]);
  const live = (doc?.transport ?? s.status?.transport) === 'LIVE';
  const est = s.estimate;
  const busy = s.phase === 'loading';
  const stamp = doc ? (doc.transport === 'LIVE' ? 'LIVE' : 'RECORDED') : undefined;
  return (
    <Panel id="brief" code="BRF" title="Disruption brief" updated={stamp}
      exportSpec={doc ? { json: () => doc } : undefined}>
      <div className="bf" data-brief-phase={s.phase}>
        <div className="bf-bar" role="toolbar" aria-label="Brief">
          <span className={`bf-mode bf-mode--${live ? 'live' : 'recorded'}`} data-transport={live ? 'LIVE' : 'RECORDED'}><ModeMark mode={live ? 'live' : 'recorded'} /> {live ? 'Live' : 'Recorded'}</span>
          {doc && <VerifyChip doc={doc} />}
          {doc && live && doc.cost.usd > 0 && <Status state="meta" word="Cost" meta={`USD ${doc.cost.usd.toFixed(4)}`} />}
          <span className="bf-sp" />
          {est && est.transport === 'LIVE' && !est.cached && <Status state="meta" word="Est." meta={`USD ${est.estimated_cost_usd.toFixed(4)}`} />}
          <Button size="sm" disabled={!s.req || busy} onClick={() => { void brief.regenerate(); }}>Regenerate</Button>
          {(['html', 'pdf', 'json'] as const).map((k) => (k === 'pdf' && s.status?.pdf.available === false
            ? <Button key={k} size="sm" icon="download" title={SAVE_PDF_TIP} disabled={!doc || s.exporting?.state === 'working'} data-export="print-pdf" onClick={() => { void brief.printPdf(); }}>{SAVE_PDF_LABEL}</Button>
            : <Button key={k} size="sm" icon="download" disabled={!doc || s.exporting?.state === 'working'} data-export={k} onClick={() => { void brief.export(k); }}>{k === 'html' ? 'HTML' : k === 'pdf' ? 'PDF' : 'JSON'}</Button>
          ))}
          {s.exporting?.state === 'error' && <Status state="notserved" word={`${s.exporting.kind.toUpperCase()} ${errorWord(s.exporting.code ?? '').toLowerCase()}`} />}
        </div>
        <div className="bf-scroll">
          {s.phase === 'loading' && !doc && <div className="bf-state" role="status" aria-busy="true"><Status state="meta" word="Writing" /></div>}
          {s.phase === 'notserved' && <div className="bf-state" role="status"><Status state="notserved" word="Not served" /></div>}
          {(s.phase === 'refused' || s.phase === 'down') && <div className="bf-state" role="status"><Status state={s.phase === 'down' ? 'down' : 'notserved'} word={errorWord(s.error ?? '')} /></div>}
          {s.phase === 'idle' && (
            runId
              ? <div className="bf-state"><Button variant="primary" onClick={() => { void brief.request({ run_id: runId }); }}>Write brief</Button><Status state="meta" word="Run" meta={runId} /></div>
              : <EmptyState word="No chain" />
          )}
          {doc && <BriefNote doc={doc} />}
        </div>
      </div>
    </Panel>
  );
}
