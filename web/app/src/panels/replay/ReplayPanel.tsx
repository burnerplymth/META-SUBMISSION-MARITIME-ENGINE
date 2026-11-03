import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { LiveStatus } from '../../ui/LiveStatus';
import { ExportMenu } from '../../charts/ExportMenu';
import type { ExportKind, ExportResult, ExportSkin } from '../../charts/exportDom';
import { Panel } from '../../components/Panel';
import { focusPlaceOnMap } from '../../mapFlyTo';
import { bumpTicket, isCurrentTicket, issueTicket, newTicketBox } from '../../requestTicket';
import { nodeName } from '../../seed';
import { useStore } from '../../store';
import { Button, SegmentedControl, Toggle } from '../../ui';
import { Chip, MetaChip, StateLine } from '../reports/chips';
import { ModelColumn, ObservedColumn } from './Columns';
import { Picker } from './Picker';
import { ComparisonSection, MissesSection } from './Tables';
import { buildModel, buildObserved, buildSim } from './chart';
import { fetchEpisode, fetchEpisodes, type Fetched } from './client';
import { runReplayExport, runXlsxExport, type ExportInput } from './exportCompare';
import { useReplayReport } from './exportReport';
import {
  OPEN_IN_RESEARCH, allPairs, axisOf, chainSteps, classifyAll, fmtRange, hashChips, methodLines, missesOf, safeText, splitWord, startOf, summarize, toStoreChain,
  type ResearchRequest,
} from './logic';
import type { EpisodeDoc, EpisodeList } from './types';
import './replay.css';

/* REPLAY (RPL). A curated past event, side by side: what was reported and measured (left) and what the causal-chain
 * engine produced on the preregistered scenario (right), on one time axis with one cursor. The misses are published
 * first-class below. Everything is read from stored files (state: Recorded); nothing here claims a skill, an
 * accuracy or a forecast, and the counts are per relation beside a Descriptive chip.
 *
 * Hook for the Research workbench: the "Open in Research" button dispatches a window event named OPEN_IN_RESEARCH
 * ('supermap:research-open') with detail { episode, placeId, start, end, engine }. A listener that takes the request
 * sets detail.handled = true; with no listener the button reports Not served. */

type Phase<T> = { s: 'loading' } | { s: 'done'; r: Fetched<T> };
const EXPORT_W = 760;

export function ReplayPanel({ as = 'replay' }: { as?: 'replay' | 'proof' } = {}) {
  const st = useStore();
  const [restricted, setRestricted] = useState(false);
  const [list, setList] = useState<Phase<EpisodeList>>({ s: 'loading' });
  const [selected, setSelected] = useState<string | null>(null);
  const [engineId, setEngineId] = useState('v1');
  const [phase, setPhase] = useState<Phase<EpisodeDoc>>({ s: 'loading' });
  const [cursor, setCursor] = useState<number | null>(null);
  const [mapOn, setMapOn] = useState(true);
  const [research, setResearch] = useState<string | null>(null);
  const [xlsx, setXlsx] = useState<ExportResult | null>(null);
  const [reload, setReload] = useState(0);

  /* place names from the served graph, with the curated names as the fallback */
  const names = useMemo(() => {
    const m = new Map<string, string>();
    for (const n of st.graph.nodes as Array<Record<string, unknown>>) if (typeof n?.id === 'string' && typeof n.name === 'string') m.set(n.id, n.name);
    return m;
  }, [st.graph.nodes]);
  const nameOf = useCallback((id: string) => names.get(id) ?? nodeName(id), [names]);

  useEffect(() => {
    const ctrl = new AbortController();
    setList({ s: 'loading' });
    void fetchEpisodes(restricted, ctrl.signal).then((r) => { if (!ctrl.signal.aborted) setList({ s: 'done', r }); });
    return () => ctrl.abort();
  }, [restricted, reload]);

  const cards = list.s === 'done' && list.r.kind === 'ok' ? list.r.doc.episodes : null;
  useEffect(() => {
    if (!cards) return;
    if (!selected || !cards.some((c) => c.id === selected)) setSelected(cards[0]?.id ?? null);
  }, [cards, selected]);

  useEffect(() => {
    if (!selected) return;
    const ctrl = new AbortController();
    setPhase({ s: 'loading' }); setCursor(null); setResearch(null); setXlsx(null);
    void fetchEpisode(selected, ctrl.signal).then((r) => { if (!ctrl.signal.aborted) setPhase({ s: 'done', r }); });
    return () => ctrl.abort();
  }, [selected, reload]);

  const doc = phase.s === 'done' && phase.r.kind === 'ok' ? phase.r.doc : null;
  const engine = doc?.engines.find((e) => e.id === engineId) ?? doc?.engines[0] ?? null;
  const steps = useMemo(() => chainSteps(engine?.chain), [engine]);
  const axis = useMemo(() => (doc ? axisOf(doc.window) : null), [doc]);
  const rows = useMemo(() => (doc ? classifyAll(allPairs(doc, engine), { startMs: startOf(doc), name: nameOf }) : []), [doc, engine, nameOf]);
  const counts = useMemo(() => summarize(rows), [rows]);
  const misses = useMemo(() => missesOf(rows), [rows]);
  const chips = hashChips(engine);

  /* map: fly to the episode place and draw the recorded chain (the existing chain and map code) */
  useEffect(() => {
    if (!doc || !mapOn) return;
    st.restoreMap();
    if (engine?.available && engine.chain) st.openVariantChain(toStoreChain(engine.chain), `Replay · ${doc.title} · ${engine.label}`);
    else st.selectNode(doc.place_id);
    focusPlaceOnMap(doc.place_id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [doc?.id, engine?.id, mapOn]);

  const exportInput = useCallback((): ExportInput | null => {
    if (!doc || !axis) return null;
    const series = doc.observed.series[0];
    if (!series) return null;
    const observed = buildObserved(doc, series, axis, EXPORT_W);
    const model = engine?.available ? buildModel(doc, steps, nameOf, axis, EXPORT_W) : null;
    const sim = doc.simulation?.series[0] ? buildSim(doc, doc.simulation.series[0], axis, EXPORT_W) : null;
    return { doc, engine: engine ?? null, rows, steps, name: nameOf, observed, model, sim };
  }, [doc, axis, engine, steps, rows, nameOf]);

  const reportInput = useMemo(() => exportInput(), [exportInput]);
  useReplayReport(reportInput, as);
  const onExport = useCallback(async (kind: ExportKind, skin: ExportSkin): Promise<ExportResult> => {
    const input = exportInput();
    return input ? runReplayExport(kind, skin, input) : { ok: false, word: 'NO DATA' };
  }, [exportInput]);
  const xlsxTicket = useRef(newTicketBox());
  useEffect(() => { const box = xlsxTicket.current; return () => bumpTicket(box); }, []);
  const onXlsx = useCallback(async () => {
    const t = issueTicket(xlsxTicket.current);
    const input = exportInput();
    const r: ExportResult = input ? await runXlsxExport(input) : { ok: false, word: 'NO DATA' };
    if (isCurrentTicket(xlsxTicket.current, t)) setXlsx(r);
  }, [exportInput]);
  const onResearch = () => {
    if (!doc) return;
    const detail: ResearchRequest = { episode: doc.id, placeId: doc.place_id, start: doc.window.start, end: doc.window.end, engine: engine?.id ?? null };
    window.dispatchEvent(new CustomEvent<ResearchRequest>(OPEN_IN_RESEARCH, { detail }));
    setResearch(detail.handled ? 'Opened' : 'Not served');
  };
  useEffect(() => { if (!research) return; const t = setTimeout(() => setResearch(null), 3200); return () => clearTimeout(t); }, [research]);

  const stamp = doc ? `RECORDED · ${safeText(doc.title)}` : 'RECORDED';

  return (
    <Panel id={as} code={as === 'proof' ? 'PRF' : 'RPL'} title="Replay" updated={stamp} className="rp-panel"
      exportSpec={{ csv: doc ? () => rows.map((r) => ({ ...r, difference: r.difference ? `${r.difference.value} ${r.difference.unit}` : null })) : undefined, json: doc ? () => doc : undefined }}>
      <div className="rp-root" data-testid="rp-root">
        <LiveStatus text={phase.s === 'loading' ? 'Loading' : phase.r.kind === 'notserved' ? 'Not served' : phase.r.kind === 'down' ? 'Down' : (xlsx?.word ?? 'Episode ready')} />
        <ListState phase={list} onRetry={() => setReload((n) => n + 1)} />
        {cards && cards.length > 0 && (
          <Picker cards={cards} selected={selected} onSelect={setSelected} restricted={restricted} onRestricted={setRestricted} />
        )}
        {cards && cards.length === 0 && <StateLine chip="NO DATA" word="No episodes" />}
        {selected && phase.s === 'loading' && <StateLine chip="INFO" word="Loading" />}
        {phase.s === 'done' && phase.r.kind === 'notserved' && <StateLine chip="NOT SERVED" word="Not served" />}
        {phase.s === 'done' && phase.r.kind === 'down' && <StateLine chip="DOWN" word="Down" code={phase.r.code} onRetry={() => setReload((n) => n + 1)} />}
        {doc && axis && (
          <div className="rp-episode" data-testid="rp-episode" data-episode={doc.id}>
            <header className="rp-head">
              <h2 className="rp-title">{safeText(doc.title)}</h2>
              <span className="rp-dates mono">{fmtRange(doc.date_range.start, doc.date_range.end)}</span>
              <Chip chip="SNAPSHOT" word="Recorded" />
              {doc.status.split && <MetaChip>{splitWord(doc.status.split)}</MetaChip>}
              {doc.status.not_blind && <Chip chip="FLAG" word="Not blind" />}
              {doc.disruption?.basis === 'MODELED' && <Chip chip="MODELLED" word="Modelled input" />}
              {doc.disruption && <MetaChip>{`Input ${doc.disruption.kind.replace(/_/g, ' ')}, ${Math.round(doc.disruption.capacity_loss * 100)}% for ${doc.disruption.duration_d} d`}</MetaChip>}
            </header>
            <div className="rp-controls" role="group" aria-label="Engine and export">
              <SegmentedControl label="Engine" size="sm" value={engine?.id ?? 'v1'} onChange={setEngineId}
                options={doc.engines.map((e) => ({ value: e.id, label: e.label, className: e.available ? '' : 'rp-dim' }))} />
              <span className="rp-hashes">
                {chips.map((c) => <MetaChip key={c.key} title={c.full}>{`${c.label} ${c.short}`}</MetaChip>)}
                {engine && !engine.available && <Chip chip="NOT SERVED" word={engine.reason === 'outside_core' ? 'Outside core places' : 'Not run'} />}
              </span>
              <details className="rp-method">
                <summary>Method</summary>
                <ul>{methodLines(doc, engine).map((l) => <li key={l}>{l}</li>)}</ul>
              </details>
              <span className="rp-spacer" />
              <Toggle checked={mapOn} onChange={setMapOn} label="Map" />
              <ExportMenu onRun={onExport} hasLink={false} />
              <Button size="sm" variant="secondary" onClick={() => void onXlsx()} data-testid="rp-xlsx">XLSX</Button>
              {xlsx && <span className={`cx-status ${xlsx.ok ? 'ok' : 'bad'}`} role="status">{xlsx.word}</span>}
              <Button size="sm" variant="ghost" icon="history" onClick={onResearch}>Open in Research</Button>
              {research && <span className="cx-status bad" role="status">{research}</span>}
            </div>
            <div className="rp-cols">
              <ObservedColumn doc={doc} axis={axis} cursor={cursor} onCursor={setCursor} />
              <ModelColumn doc={doc} engine={engine} steps={steps} axis={axis} cursor={cursor} onCursor={setCursor} name={nameOf} />
            </div>
            <MissesSection misses={misses} counts={counts} ready={rows.length > 0} />
            <ComparisonSection rows={rows} />
          </div>
        )}
      </div>
    </Panel>
  );
}

function ListState({ phase, onRetry }: { phase: Phase<EpisodeList>; onRetry: () => void }) {
  if (phase.s === 'loading') return <StateLine chip="INFO" word="Loading" />;
  if (phase.r.kind === 'notserved') return <StateLine chip="NOT SERVED" word="Not served" />;
  if (phase.r.kind === 'down') return <StateLine chip="DOWN" word="Down" code={phase.r.code} onRetry={onRetry} />;
  return null;
}

