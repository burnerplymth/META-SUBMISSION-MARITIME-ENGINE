import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Chip } from '../../shell/Chip';
import { useStore } from '../../store';
import { scn } from './client';
import { useScn } from './ctx';
import {
  catalogueRows, plainLine, chainDivergenceChip, chainJumpTarget, chainLineageRows, errorLine, lineageTree, shapeChainCompare, shapeSimCompare,
  countWord, shortId, simDivergenceChip, simJumpTarget, type ChainCompareView, type SimCompareView,
} from './logic';
import type { ChainCompare, SimCompare, SimLineage } from './types';
import { Building, Empty, Seg, Tag } from './ui';
import { labelWord } from '../../copy';

interface Async<T> { status: 'idle' | 'loading' | 'ready' | 'error'; doc: T | null; error: string | null; since: number }
const IDLE: Async<never> = { status: 'idle', doc: null, error: null, since: 0 };

function useFetched<T>(key: string | null, load: () => Promise<T>): Async<T> {
  const [st, setSt] = useState<Async<T>>(IDLE);
  const loadRef = useRef(load); loadRef.current = load;
  useEffect(() => {
    if (key === null) { setSt(IDLE); return; }
    let live = true;
    setSt({ status: 'loading', doc: null, error: null, since: Date.now() });
    loadRef.current().then((doc) => { if (live) setSt({ status: 'ready', doc, error: null, since: 0 }); })
      .catch((e) => { if (live) setSt({ status: 'error', doc: null, error: errorLine(e), since: 0 }); });
    return () => { live = false; };
  }, [key]);
  return st;
}

export function CompareTab() {
  const c = useScn();
  const s = useStore();
  const kind = c.loop;
  const options = useMemo(() => {
    const rows = catalogueRows(c.catalogue.runs, { kind, nameOf: c.nameOf });
    const ids = new Set(rows.map((r) => r.id));
    const extra = [c.cmp.a, c.cmp.b, kind === 'chain' ? c.chainId : c.simId].filter((x): x is string => !!x && !ids.has(x));
    return [...new Set(extra)].map((id) => ({ id, label: shortId(id) })).concat(rows.map((r) => ({ id: r.id, label: `${r.title} · ${shortId(r.id)}` })));
  }, [c.catalogue.runs, kind, c.nameOf, c.cmp.a, c.cmp.b, c.chainId, c.simId]);

  const { a, b } = c.cmp;
  const ready = !!a && !!b && a !== b;
  const key = ready ? `${kind}:${a}:${b}` : null;
  const chainCmp = useFetched<ChainCompare>(kind === 'chain' ? key : null, () => scn.compare(a!, b!));
  const simCmp = useFetched<SimCompare>(kind === 'sim' ? key : null, () => scn.simCompare(a!, b!));
  const cur = kind === 'chain' ? chainCmp : simCmp;

  const setSide = (side: 'a' | 'b', id: string) => c.setCmp({ ...c.cmp, [side]: id || null });
  const useLoaded = (side: 'a' | 'b') => { const id = kind === 'chain' ? c.chainId : c.simId; if (id) setSide(side, id); };

  return (
    <div className="scn-tab" data-tab="CMP">
      <div className="scn-grid">
        {(['a', 'b'] as const).map((side) => (
          <label key={side} className="scn-field scn-field--wide">
            <span>{`Run ${side.toUpperCase()}`}</span>
            <span className="scn-pick">
              <select value={c.cmp[side] ?? ''} onChange={(e) => setSide(side, e.target.value)} aria-label={`Run ${side.toUpperCase()}`}>
                <option value="">Pick a run</option>
                {options.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
              </select>
              <button type="button" className="btn scn-btn" onClick={() => useLoaded(side)} disabled={!(kind === 'chain' ? c.chainId : c.simId)}>Loaded</button>
            </span>
          </label>
        ))}
      </div>
      <div className="scn-row">
        <button type="button" className="btn scn-btn" onClick={() => c.setCmp({ a: c.cmp.b, b: c.cmp.a })}>Swap</button>
        <Chip kind="sim" />
        {kind === 'sim' && <Chip kind="model" />}
      </div>
      {!ready && <Empty word={a && b ? 'Pick two runs' : 'Pick a second run'} />}
      {ready && cur.status === 'loading' && <div className="empty-state"><Building since={cur.since} word="Comparing" /></div>}
      {ready && cur.status === 'error' && <div className="scn-refusal mono" role="alert"><Tag tone="bad" word="Down" /><span className="scn-reason">{plainLine(cur.error ?? '')}</span></div>}
      {ready && kind === 'chain' && chainCmp.doc && <ChainResult doc={chainCmp.doc} />}
      {ready && kind === 'sim' && simCmp.doc && <SimResult doc={simCmp.doc} />}
      {kind === 'sim' && <SimLineageList root={c.cmp.a ?? c.simId} />}
      {kind === 'chain' && <ChainLineageList runId={c.cmp.b ?? c.chainId} />}
    </div>
  );
}

/* ── chain result ─────────────────────────────────────────────────────── */
function ChainResult({ doc }: { doc: ChainCompare }) {
  const c = useScn();
  const s = useStore();
  const v: ChainCompareView = useMemo(() => shapeChainCompare(doc, c.nameOf), [doc, c.nameOf]);
  const [changedOnly, setChangedOnly] = useState(true);
  const rows = changedOnly ? v.places.filter((p) => p.changed) : v.places;

  const jump = async () => {
    let events = s.chain?.run_id === doc.b.run_id ? s.chain.events : null;
    if (!events) { const ch = await c.openChainRun(doc.b.run_id); events = ch?.events ?? null; }
    const t = chainJumpTarget(doc, events);
    if (t) s.selectNode(t.placeId, t.seq);
  };

  return (
    <div className="scn-result" data-result="chain">
      <div className="scn-row scn-row--wrap mono">
        <Tag tone={v.identical ? 'ok' : 'warn'} word={chainDivergenceChip(v)} />
        {v.sharedPrefix > 0 && <Chip kind="meta" word={`Shared ${v.sharedPrefix}`} />}
        <Chip kind="meta" word={`A ${v.revA}`} />
        <Chip kind="meta" word={`B ${v.revB}`} />
        {!v.sameRev && <Tag tone="warn" word="Revisions differ" />}
        <Chip kind="meta" word={`${v.eventsA} / ${v.eventsB} steps`} />
      </div>
      <div className="scn-row">
        <button type="button" className="btn btn--amber scn-btn" disabled={v.divergesFromSeq === null} onClick={() => void jump()}>Jump to first difference</button>
        <label className="scn-check mono"><input type="checkbox" checked={changedOnly} onChange={(e) => setChangedOnly(e.target.checked)} /> Changed only ({v.changedCount})</label>
      </div>
      <div className="scn-scroll">
        <table className="data-table scn-table scn-wide" aria-label="Per-place delta">
          <thead><tr><th>Place</th><th>In</th><th>Channel A / B</th><th className="num">Step A / B</th><th className="num">Delay A</th><th className="num">Delay B</th><th className="num">Δ Delay</th><th className="num">Δ Cost</th></tr></thead>
          <tbody>
            {rows.map((p) => (
              <tr key={p.placeId} onClick={() => s.selectNode(p.placeId, null)} className={s.selectedNode === p.placeId ? 'row--sel' : undefined}>
                <td>{p.name}</td><td className="mono">{labelWord(p.presence)}</td>
                <td className="mono">{p.channelA} / {p.channelB}</td>
                <td className="num mono">{p.firstSeqA} / {p.firstSeqB}</td>
                <td className="num mono">{p.delayA}</td><td className="num mono">{p.delayB}</td>
                <td className={`num mono scn-d${p.deltaSign}`}>{p.delayDelta}</td>
                <td className="num mono">{p.costDelta}</td>
              </tr>
            ))}
            {rows.length === 0 && <tr><td colSpan={8}><Empty word="No changes" /></td></tr>}
          </tbody>
        </table>
      </div>
      <div className="scn-subhead mono">Channels</div>
      <table className="data-table scn-table" aria-label="Channel counts">
        <thead><tr><th>Channel</th><th className="num">A</th><th className="num">B</th><th className="num">Δ</th></tr></thead>
        <tbody>
          {v.channels.map((ch) => (
            <tr key={ch.channel}><td className="mono">{ch.channel}</td><td className="num mono">{ch.a}</td><td className="num mono">{ch.b}</td>
              <td className={`num mono scn-d${Math.sign(ch.delta)}`}>{ch.delta > 0 ? `+${ch.delta}` : ch.delta === 0 ? '0' : `−${Math.abs(ch.delta)}`}</td></tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ChainLineageList({ runId }: { runId: string | null }) {
  const c = useScn();
  const j = useFetched(runId, () => scn.journal(runId!));
  if (!runId || !j.doc) return null;
  const rows = chainLineageRows(j.doc);
  return (
    <div className="scn-result">
      <div className="scn-subhead mono">Branches</div>
      <table className="data-table scn-table">
        <thead><tr><th>Run</th><th>Parent</th><th>Forked at</th><th /></tr></thead>
        <tbody>
          {rows.map((l) => (
            <tr key={l.id} className={l.current ? 'row--sel' : undefined}>
              <td className="mono">{shortId(l.id)}</td><td className="mono">{l.parent ? shortId(l.parent) : '—'}</td><td className="mono">{l.at}</td>
              <td className="scn-actions">
                <button type="button" className="btn scn-btn" onClick={() => c.setCmp({ ...c.cmp, a: l.id })}>A</button>
                <button type="button" className="btn scn-btn" onClick={() => c.setCmp({ ...c.cmp, b: l.id })}>B</button>
                <button type="button" className="btn scn-btn" onClick={() => void c.openChainRun(l.id)}>Open</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ── sim result ───────────────────────────────────────────────────────── */
function SimResult({ doc }: { doc: SimCompare }) {
  const c = useScn();
  const s = useStore();
  const v: SimCompareView = useMemo(() => shapeSimCompare(doc), [doc]);
  const [pending, setPending] = useState<{ runId: string; tick: number } | null>(null);
  useEffect(() => {
    if (pending && s.simActive && s.simRun?.sim_run_id === pending.runId) { s.setSimPlayhead(pending.tick); setPending(null); }
  }, [pending, s.simActive, s.simRun?.sim_run_id]); // eslint-disable-line react-hooks/exhaustive-deps

  const jump = async () => {
    const t = simJumpTarget(doc);
    if (!t) return;
    if (t.placeId) s.selectNode(t.placeId, null);
    if (s.simActive && s.simRun?.sim_run_id === t.runId) s.setSimPlayhead(t.tick);
    else { setPending({ runId: t.runId, tick: t.tick }); await c.openSimRunId(t.runId); }
  };

  return (
    <div className="scn-result" data-result="sim">
      <div className="scn-row scn-row--wrap mono">
        <Tag tone={v.identical ? 'ok' : 'warn'} word={simDivergenceChip(v)} />
        {v.relation && <Chip kind="meta" word={labelWord(v.relation)} meta={v.forkedAtTick !== null ? `Tick ${v.forkedAtTick}` : undefined} />}
        <Chip kind="meta" word={`${v.differingEntities} entities differ`} />
        <Chip kind="meta" word={`${v.placesChanged} places changed`} />
      </div>
      <div className="scn-row">
        <button type="button" className="btn btn--amber scn-btn" disabled={v.divergesFromTick === null} onClick={() => void jump()}>Jump to first difference</button>
      </div>
      {v.firstDifference && (
        <div className="scn-step mono">
          <b>{`Tick ${v.firstDifference.tick}`}</b><span>{v.firstDifference.entityId}</span><span>{v.firstDifference.mode}</span>
          <span>{c.nameOf(v.firstDifference.placeId)}</span><span>{`${v.firstDifference.aKind} → ${v.firstDifference.bKind}`}</span>
        </div>
      )}
      <div className="scn-scroll">
        <table className="data-table scn-table scn-measures" aria-label="Measures">
          <thead><tr><th>Measure</th><th className="num">A</th><th className="num">B</th><th className="num">Difference</th><th>First differs</th></tr></thead>
          <tbody>
            {v.rows.map((r) => (
              <tr key={r.id}>
                <td>{r.label} <Chip kind={r.basis === 'MODELLED' ? 'model' : 'sim'} word={labelWord(r.basis)} /> <span className="mono scn-unit">{r.unit}</span></td>
                <td className="num mono">{r.a}</td><td className="num mono">{r.b}</td>
                <td className={`num mono scn-d${r.deltaSign}`}>{r.difference}</td><td className="mono">{r.firstDiffers}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {v.omitted.length > 0 && (
        <div className="scn-row scn-row--wrap">{v.omitted.map((o) => <Chip key={o} kind="notserved" word="Omitted" meta={o} />)}</div>
      )}
      {v.firstChanged.length > 0 && (
        <div className="scn-row scn-row--wrap">
          {v.firstChanged.map((p) => (
            <button key={p.placeId} type="button" className="scn-place mono" onClick={() => s.selectNode(p.placeId, null)}>{c.nameOf(p.placeId)} · Tick {p.tick}</button>
          ))}
        </div>
      )}
    </div>
  );
}

function SimLineageList({ root }: { root: string | null }) {
  const c = useScn();
  const lin = useFetched<SimLineage>(root, () => scn.simLineage(root!));
  if (!root) return null;
  if (lin.status === 'error') return <div className="scn-refusal mono" role="alert"><Tag tone="bad" word="Down" meta="Lineage" /><span className="scn-reason">{plainLine(lin.error ?? '')}</span></div>;
  if (!lin.doc) return null;
  const rows = lineageTree(lin.doc.runs);
  return (
    <div className="scn-result">
      <div className="scn-subhead mono">Branches<span className="scn-count">{rows.length}</span></div>
      <table className="data-table scn-table">
        <thead><tr><th>Run</th><th>State</th><th>Forked at</th><th /></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.sim_run_id} className={r.sim_run_id === c.simId ? 'row--sel' : undefined}>
              <td className="mono" style={{ paddingLeft: 6 + r.depth * 12 }}>{shortId(r.sim_run_id)}</td>
              <td><Tag tone={r.status === 'ready' ? 'ok' : r.status === 'building' ? 'busy' : 'bad'} word={labelWord(r.status)} /></td>
              <td className="mono">{r.forked_at_tick === null ? '—' : `Tick ${r.forked_at_tick}`}{r.n_interventions ? ` · ${countWord(r.n_interventions, 'shock')}` : ''}</td>
              <td className="scn-actions">
                <button type="button" className="btn scn-btn" onClick={() => c.setCmp({ ...c.cmp, a: r.sim_run_id })}>A</button>
                <button type="button" className="btn scn-btn" onClick={() => c.setCmp({ ...c.cmp, b: r.sim_run_id })}>B</button>
                <button type="button" className="btn scn-btn" disabled={r.status !== 'ready'} onClick={() => void c.openSimRunId(r.sim_run_id)}>Open</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
