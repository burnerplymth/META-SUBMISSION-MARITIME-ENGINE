import React, { useEffect, useMemo, useState } from 'react';
import { useDeskStore, useSimStore } from '../../store';
import { nodeLabel } from '../../format';
import { Chip } from '../../shell/Chip';
import { historyApi, type Fetched } from './client';
import { OverlayChart, OVERLAY_BOX } from './charts';
import { ForkedFromChip } from './lineageChip';
import { countText, describeCard, lineageChipMeta, lineageEntry, overlayModel, placeRows, reasonLabel, realismBody, resultRows, seriesPlaces, REALISM_MAX_WINDOW_D } from './logic';
import { ErrChip, NsChip, ProvChip, Sec } from './parts';
import type { RealismCard, RealismResultsDoc, RealismRuleDoc } from './types';
import './history.css';
import { labelWord } from '../../copy';
import { Icon } from '../../ui/Icon';
import { useTabKeys } from '../../ui/Controls';

/** The method, in three short lines. The served label and caveats are not
 *  printed: they describe scoring, which this card does not do. */
const METHOD_LINES = [
  'A recorded outcome set beside a simulated outcome, place by place.',
  'Counts and directions only. Nothing is scored.',
  'Simulated traffic is modelled from lane structure. Separate from the live ledger.',
] as const;

/** The Descriptive chip: the card describes what was compared and scores
 *  nothing. Never LIVE, never a ledger word. */
const DescriptiveChip = () => <Chip kind="meta" word="Descriptive" title="Counts only" />;

/** Loaded runs (the sim slice's run and the desk's chain) as one-click
 *  choices. Kept in its own component so sim playback ticks re-render
 *  only this strip. */
function LoadedRuns({ onPick }: { onPick: (id: string) => void }) {
  const sim = useSimStore();
  const d = useDeskStore();
  const simId = sim.simRun?.sim_run_id ?? null;
  const chainId = d.chain?.run_id ?? null;
  return (
    <div className="hx-ex" role="group" aria-label="Loaded runs">
      {simId && <button type="button" className="hx-btn hx-btn--sim" onClick={() => onPick(simId)} title={simId}>Loaded sim</button>}
      {chainId && <button type="button" className="hx-btn" onClick={() => onPick(chainId)} title={chainId}>Loaded chain</button>}
      {!simId && !chainId && <Chip kind="notserved" word="No run" />}
    </div>
  );
}

export function RealismPanel({ onClose }: { onClose: () => void }) {
  const d = useDeskStore();
  const [results, setResults] = useState<Fetched<RealismResultsDoc> | null>(null);
  const [rule, setRule] = useState<Fetched<RealismRuleDoc> | null>(null);
  const [card, setCard] = useState<Fetched<RealismCard> | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [run, setRun] = useState('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [store, setStore] = useState(false);
  const [formErr, setFormErr] = useState<string | null>(null);
  const [place, setPlace] = useState<string | null>(null);

  const refresh = () => { void historyApi.realismResults().then(setResults); };
  useEffect(() => {
    let live = true;
    void historyApi.realismResults().then((r) => { if (live) setResults(r); });
    void historyApi.realismRule().then((r) => { if (live) setRule(r); });
    return () => { live = false; };
  }, []);

  const open = async (id: string) => {
    setBusy(id); setCard(null);
    const r = await historyApi.realismCard(id);
    setBusy(null); setCard(r);
    if (r.kind === 'ok') setPlace(seriesPlaces(r.body)[0] ?? null);
  };
  const grade = async () => {
    setFormErr(null);
    const b = realismBody({ run, from: from || undefined, to: to || undefined, store });
    if (!b.ok) { setFormErr(b.code); return; }
    setBusy('run'); setCard(null);
    const r = await historyApi.realismRun(b.body);
    setBusy(null); setCard(r);
    if (r.kind === 'ok') { setPlace(seriesPlaces(r.body)[0] ?? null); if (store) refresh(); }
  };

  const rows = results?.kind === 'ok' ? resultRows(results.body.results) : [];
  const c = card?.kind === 'ok' ? card.body : null;
  const ruleDoc = rule?.kind === 'ok' ? rule.body : null;

  return (
    <section className="panel tool-slot hx" data-panel="tool-realism">
      <header className="panel-head">
        <span className="code-chip">RLS</span>
        <span className="panel-title">Realism</span>
        <span className="panel-updated mono"><DescriptiveChip /></span>
        <span className="panel-controls"><button type="button" className="icon-btn" title="Close" aria-label="Close" onClick={onClose}><Icon name="close" size="sm" /></button></span>
      </header>
      <div className="panel-body"><div className="hx-body">

        <Sec title="Rule" id="rule" chips={<DescriptiveChip />}>
          {rule === null && <Chip kind="meta" word="Checking" />}
          {rule?.kind === 'notserved' && <NsChip reason={rule.reason} meta="Rule" />}
          {rule?.kind === 'error' && <ErrChip code={rule.code} />}
          {ruleDoc && (
            <div className="hx-chips">
              <Chip kind="meta" word="Rule" meta={ruleDoc.rule.id} />
              {ruleDoc.rule.frozen && <Chip kind="meta" word="Frozen" meta={ruleDoc.rule.frozen} />}
              <Chip kind="snapshot" word="Observed" />
              <Chip kind="sim" />
            </div>
          )}
        </Sec>

        <Sec title="Stored cards" id="stored" chips={results?.kind === 'ok' ? <Chip kind="meta" word={`${results.body.count}`} /> : null}>
          {results === null && <Chip kind="meta" word="Checking" />}
          {results?.kind === 'notserved' && <NsChip reason={results.reason} meta="Results" />}
          {results?.kind === 'error' && <ErrChip code={results.code} />}
          {rows.length > 0 && (
            <div className="hx-tbl-wrap">
              <table className="hx-tbl mono">
                <thead><tr><th>Event</th><th>Run</th><th>Window</th><th className="num">Graded entries</th></tr></thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.id} className={c?.card_id === r.id ? 'sel' : ''} data-card={r.id}>
                      <td><button type="button" className="hx-row-btn hx-row-btn--wrap" onClick={() => void open(r.id)}>{r.title}</button></td>
                      <td className="dim">{r.runKind}</td>
                      <td className="dim">{r.window}</td>
                      <td className="num">{r.graded}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {results?.kind === 'ok' && rows.length === 0 && <Chip kind="meta" word="No cards" />}
        </Sec>

        <Sec title="Run" id="run" chips={<Chip kind="sim" />}>
          <LoadedRuns onPick={setRun} />
          <div className="hx-ctl">
            <label>Run id<input type="text" value={run} onChange={(e) => setRun(e.target.value)} aria-label="Run id" placeholder="Run id" /></label>
            <label>From<input type="date" value={from} onChange={(e) => setFrom(e.target.value)} aria-label="Window from" /></label>
            <label>To<input type="date" value={to} onChange={(e) => setTo(e.target.value)} aria-label="Window to" /></label>
            <label className="hx-chk">Store<input type="checkbox" checked={store} onChange={(e) => setStore(e.target.checked)} aria-label="Store card" /></label>
            <button type="button" className="hx-btn hx-btn--go" disabled={busy === 'run'} onClick={() => void grade()}>Compare</button>
          </div>
          <div className="hx-chips"><Chip kind="meta" word={`Max ${REALISM_MAX_WINDOW_D} d`} />{busy === 'run' && <Chip kind="meta" word="Comparing" />}{formErr && <ErrChip code={formErr} />}</div>
        </Sec>

        {busy && busy !== 'run' && <Chip kind="meta" word="Loading" />}
        {card?.kind === 'notserved' && <div className="hx-state"><NsChip reason={card.reason} /></div>}
        {card?.kind === 'error' && <div className="hx-state"><ErrChip code={card.code} /></div>}
        {c && <CardView card={c} place={place} onPlace={(p) => { setPlace(p); d.selectNode(p); }} graph={d.graph} />}

      </div></div>
    </section>
  );
}

/** One realism card: the Descriptive chip, the rule, counts of graded
 *  entries, the per-place table of observed and simulated directions, the
 *  observed vs simulated overlay. The served label and caveats sit in a
 *  closed Method disclosure; nothing on the card is scored. */
export function CardView({ card, place, onPlace, graph }: { card: RealismCard; place: string | null; onPlace: (p: string) => void; graph?: Parameters<typeof nodeLabel>[1] }) {
  const counts = useMemo(() => describeCard(card), [card]);
  const prows = useMemo(() => placeRows(card), [card]);
  const sp = useMemo(() => seriesPlaces(card), [card]);
  const model = useMemo(() => (place ? overlayModel(card, place, OVERLAY_BOX) : null), [card, place]);
  const tk = useTabKeys(sp.length, sp.indexOf(place ?? ''), (i) => onPlace(sp[i]));
  const lineage = card.forked_from ? lineageChipMeta(lineageEntry({ forked_from: card.forked_from, chain_requests: [], sim_request: null, created_chain_runs: [] }, [], 0)) : null;
  return (
    <section className="hx-card" data-card-view={card.card_id ?? card.run.run_id}>
      <div className="hx-card-h">
        <DescriptiveChip />
        <Chip kind={card.source === 'STORED' ? 'snapshot' : 'model'} word={card.source === 'STORED' ? 'Stored' : 'Computed'} />
        <Chip kind="snapshot" word="Observed" />
        <Chip kind="sim" />
        <span className="spacer" />
        <Chip kind="meta" word="Rule" meta={String(card.rule?.id ?? '—')} />
      </div>
      <div className="hx-chips">
        <b className="mono">{card.title ?? card.run.run_id}</b>
        <Chip kind="meta" word={labelWord(card.run.kind)} meta={card.run.run_id} />
        <Chip kind="meta" word="Window" meta={`${card.window.from} → ${card.window.to}`} />
        {lineage && <ForkedFromChip meta={lineage} />}
      </div>

      <div className="hx-chips" role="group" aria-label="Graded entries">
        <span className="rk-lbl">Graded entries</span>
        {counts.map((c) => <Chip key={c.key} kind="meta" word={c.label} meta={countText(c)} />)}
      </div>

      <div className="hx-tbl-wrap">
        <table className="hx-tbl mono" aria-label="Places">
          <thead><tr><th>Place</th><th>Role</th><th>Observed</th><th>Simulated</th><th>Onset</th></tr></thead>
          <tbody>
            {prows.map((p) => (
              <tr key={p.place} className={place === p.place ? 'sel' : ''}>
                <td title={p.place}><button type="button" className="hx-row-btn" onClick={() => onPlace(p.place)}>{nodeLabel(p.place, graph)}</button></td>
                <td className="dim">{p.role}</td>
                <td>{p.observedDir} {p.observedPct}</td>
                <td>{p.modelDir} {p.modelPct}</td>
                <td>{p.onset}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {(card.not_compared ?? []).length > 0 && (
        <div className="hx-chips">{(card.not_compared ?? []).map((n) => <NsChip key={n.place} meta={`${nodeLabel(n.place, graph)} · ${reasonLabel(n.reason)}`} />)}</div>
      )}

      {sp.length > 0 && (
        <div className="hx-sec" data-sec="overlay">
          <div className="hx-sec-h"><h5>Observed vs simulated</h5><Chip kind="snapshot" word="Observed" /><Chip kind="sim" /></div>
          <div className="hx-ex" role="tablist" aria-label="Place series">
            {sp.map((p, i) => <button key={p} type="button" role="tab" {...tk(i)} aria-selected={p === place} className={`hx-btn${p === place ? ' on' : ''}`} onClick={() => onPlace(p)}>{nodeLabel(p, graph)}</button>)}
          </div>
          {model && <OverlayChart model={model} />}
          {!model && <Chip kind="notserved" word="No series" />}
        </div>
      )}
      {sp.length === 0 && <div className="hx-chips"><Chip kind="notserved" word="No series" /></div>}

      <details className="hx-method" data-caveat>
        <summary>Method</summary>
        {METHOD_LINES.map((t) => <p key={t} className="hx-caveat mono">{t}</p>)}
      </details>

      {(card.observed_provenance ?? []).length > 0 && (
        <details>
          <summary>Provenance {(card.observed_provenance ?? []).length}</summary>
          <table className="hx-tbl mono"><tbody>
            {(card.observed_provenance ?? []).map((o) => (
              <tr key={`${o.place}-${o.metric}`}><td>{nodeLabel(o.place, graph)}</td><td className="dim">{labelWord(o.metric)}</td><td><ProvChip prov={o} /></td><td className="dim">{(o.payload_sha256 ?? '').slice(0, 10)}</td></tr>
            ))}
          </tbody></table>
        </details>
      )}
    </section>
  );
}

