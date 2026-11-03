import { fmtDateLong } from '../../format';
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Chip } from '../../shell/Chip';
import { useStore } from '../../store';
import { TICK_SPECS, clampPlayhead, floorTickOf, simClockReadout } from '../../../../map/src/sim/phaseD';
import type { SimRunRequest, SimTickSize } from '../../../../map/src/sim/types';
import { scn } from './client';
import { useScn } from './ctx';
import {
  ENGINE_REVS, SHAPES, buildRunBody, chainPreview, plainLine, chainReadout, chainStep, defaultStartDate, engineLabel, errorLine, kindWord,
  mapRefusal, parseScenarios, scenarioDraft, shortHash, shortId, simPreview, startedAtIso, type Refusal, type StepAction,
} from './logic';
import type { EngineRev, RunReceipt, ScenarioEntry, ShockShape } from './types';
import { Building, ChoiceChips, CopyButton, Empty, Field, RefusalLine, Seg, Tag } from './ui';
import { labelWord } from '../../copy';
import { Icon } from '../../ui/Icon';

const pctWord = (v: number) => `${Math.round(v * 100)}%`;

/** `[code] message` (the store's createSimRun result) back to a refusal. */
export function refusalFromLine(line: string): Refusal {
  const m = /^\[([^\]]+)\]\s*(.*)$/s.exec(line);
  return mapRefusal(m ? m[1] : 'error', m ? m[2] : line);
}

export function RunTab() {
  const c = useScn();
  const s = useStore();
  const [scenarios, setScenarios] = useState<ScenarioEntry[] | null>(null);
  const [scenErr, setScenErr] = useState<string | null>(null);
  const [selId, setSelId] = useState('');
  const [durationD, setDurationD] = useState<number | null>(null);
  const [size, setSize] = useState<number | null>(null);
  const [shape, setShape] = useState<ShockShape>('step');
  const [startsOn, setStartsOn] = useState(() => defaultStartDate(new Date()));
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<Refusal | null>(null);
  const [receipt, setReceipt] = useState<RunReceipt | null>(null);
  // sim run parameters
  const [tick, setTick] = useState<SimTickSize>('day');
  const [ticks, setTicks] = useState(10);
  const [seed, setSeed] = useState(42);
  const [atTick, setAtTick] = useState(2);

  const rev = c.rev;
  useEffect(() => {
    let live = true;
    setScenarios(null); setScenErr(null);
    scn.scenarios(rev).then((d) => { if (live) setScenarios(parseScenarios(d)); }).catch((e) => { if (live) setScenErr(errorLine(e)); });
    return () => { live = false; };
  }, [rev]);
  useEffect(() => {
    if (scenarios && scenarios.length && !scenarios.some((x) => x.id === selId)) setSelId(scenarios[0].id);
  }, [scenarios, selId]);

  const sel = useMemo(() => scenarios?.find((x) => x.id === selId) ?? null, [scenarios, selId]);
  useEffect(() => { setDurationD(sel?.default_duration_d ?? null); setSize(sel?.default_capacity_loss ?? null); }, [sel]);

  const draft = sel ? scenarioDraft(sel, { durationD: durationD ?? undefined, size: size ?? undefined, shape, startsOn }) : null;
  const preview = !draft ? null
    : c.loop === 'sim'
      ? simPreview({ type: 'disruption', placeId: draft.placeId, toPlaceId: '', kind: draft.kind, size: draft.size, durationD: draft.durationD, radiusKm: 0, atTick }, c.nameOf, c.rules)
      : chainPreview(draft, c.nameOf, { t: 'date' }, c.rules);

  const createChain = useCallback(async () => {
    if (!draft) return;
    setBusy(true); setRefusal(null); setReceipt(null);
    try {
      const r = await scn.run(buildRunBody(draft, rev, c.rules));
      setReceipt(r);
      await c.openChainRun(r.run_id);
      c.noteRunsChanged();
    } catch (e) {
      const x = e as { code?: string; message?: string };
      setRefusal(mapRefusal(x.code ?? 'error', x.message ?? String(e)));
    } finally { setBusy(false); }
  }, [draft, rev, c]);

  const createSim = useCallback(async () => {
    if (!sel || !draft) return;
    setBusy(true); setRefusal(null);
    const startAt = `${startsOn}T00:00:00Z`;
    const tickSeconds = TICK_SPECS[tick].seconds;
    const startedAt = new Date(Date.parse(startAt) + atTick * tickSeconds * 1000).toISOString().replace(/\.\d{3}Z$/, 'Z');
    const req: SimRunRequest = {
      disruptions: [{
        node_id: draft.placeId, kind: draft.kind, capacity_loss: draft.kind === 'closure' ? 1 : draft.size,
        duration_d: draft.durationD, started_at: startedAt, shape: 'step',
      }],
      tick, ticks, seed, start_at: startAt, weather: false, lane_geometry: true,
    };
    const line = await s.createSimRun(req);
    setBusy(false);
    if (line) setRefusal(refusalFromLine(line));
    else c.noteRunsChanged();
  }, [sel, draft, startsOn, tick, ticks, seed, atTick, s, c]);

  const simBuilding = c.loop === 'sim' ? s.simBuilding : null;
  const maxTicks = TICK_SPECS[tick].maxTicks;

  return (
    <div className="scn-tab" data-tab="RUN">
      <div className="scn-row scn-row--wrap">
        <Field label="Scenario" wide>
          <select value={selId} onChange={(e) => setSelId(e.target.value)} disabled={!scenarios?.length} aria-label="Scenario">
            {(scenarios ?? []).map((x) => <option key={x.id} value={x.id}>{x.label}</option>)}
          </select>
        </Field>
        {c.loop === 'chain' && (
          <div className="scn-field">
            <span>Engine revision</span>
            <Seg<EngineRev> label="Engine revision" value={rev} onChange={c.setRev}
              options={ENGINE_REVS.map((r) => ({ v: r, label: `Rev ${r}`, title: engineLabel(r) }))} />
          </div>
        )}
      </div>
      {scenarios === null && !scenErr && <Tag tone="busy" word="Loading" meta="Scenarios" />}
      {scenErr && <div className="scn-refusal mono" role="alert"><Tag tone="bad" word="Down" meta="Scenarios" /><span className="scn-reason">{plainLine(scenErr)}</span></div>}
      {scenarios && scenarios.length === 0 && <Empty word="No scenarios" />}

      {sel && draft && (
        <>
          <div className="scn-row scn-row--wrap">
            <div className="scn-field"><span>Place</span><b className="mono scn-static">{sel.place_name}</b></div>
            <div className="scn-field"><span>Kind</span><b className="mono scn-static">{kindWord(sel.kind)}</b></div>
            <div className="scn-field"><span>Duration (d)</span>
              <ChoiceChips label="Duration" value={durationD ?? sel.default_duration_d} options={sel.duration_options_d} onChange={setDurationD} />
            </div>
            {sel.capacity_loss_options && (
              <div className="scn-field"><span>Capacity loss</span>
                <ChoiceChips label="Capacity loss" value={size ?? sel.default_capacity_loss ?? 0.5} options={sel.capacity_loss_options} onChange={setSize} fmt={pctWord} />
              </div>
            )}
            {c.loop === 'chain' && (
              <div className="scn-field"><span>Shape</span>
                <Seg<ShockShape> label="Shape" value={shape} onChange={setShape}
                  options={SHAPES.filter((x) => !sel.shapes || sel.shapes.includes(x)).map((x) => ({ v: x, label: labelWord(x) }))} />
              </div>
            )}
            <Field label="Starts on (UTC)"><input type="date" className="mono" value={startsOn} onChange={(e) => setStartsOn(e.target.value)} aria-label="Starts on" /><span className="scb-datefmt mono">{fmtDateLong(startsOn)}</span></Field>
          </div>

          {c.loop === 'sim' && (
            <div className="scn-row scn-row--wrap">
              <div className="scn-field"><span>Tick</span>
                <Seg<SimTickSize> label="Tick size" value={tick} onChange={(t) => { setTick(t); setTicks((n) => Math.min(n, TICK_SPECS[t].maxTicks)); }}
                  options={(['hour', 'day', 'week'] as SimTickSize[]).map((t) => ({ v: t, label: TICK_SPECS[t].label }))} />
              </div>
              <Field label={`Ticks (1 to ${maxTicks})`}><input type="number" min={1} max={maxTicks} className="mono" value={ticks} onChange={(e) => setTicks(Math.max(1, Math.min(maxTicks, Number(e.target.value) || 1)))} aria-label="Ticks" /></Field>
              <Field label="Shock at tick"><input type="number" min={0} max={Math.max(0, ticks - 1)} className="mono" value={atTick} onChange={(e) => setAtTick(Math.max(0, Math.min(ticks - 1, Number(e.target.value) || 0)))} aria-label="Shock at tick" /></Field>
              <Field label="Seed"><input type="number" className="mono" value={seed} onChange={(e) => setSeed(Number(e.target.value) || 0)} aria-label="Seed" /></Field>
            </div>
          )}

          <div className="scn-preview mono" aria-live="polite" data-testid="preview">
            <span className="scn-preview-k">Preview</span>
            <span>{preview ?? '—'}</span>
          </div>

          <div className="scn-row">
            <button type="button" className="btn btn--amber scn-btn" disabled={busy || !!simBuilding || !startedAtIso(startsOn)}
              onClick={() => void (c.loop === 'chain' ? createChain() : createSim())}>
              {busy || simBuilding ? (c.loop === 'sim' ? 'Building' : 'Running') : 'Create run'}
            </button>
            {c.loop === 'chain' && <Chip kind="meta" word={engineLabel(rev)} />}
            <Chip kind="sim" />
            {c.loop === 'sim' && <Chip kind="model" word="Lane geometry" />}
            {simBuilding && <Building since={simBuilding.since} meta={shortId(simBuilding.simRunId)} />}
          </div>
        </>
      )}
      {refusal && <RefusalLine r={refusal} />}
      {receipt && c.loop === 'chain' && (
        <div className="scn-receipt mono">
          <Tag tone="ok" word="Created" meta={shortId(receipt.run_id)} />
          <Chip kind="meta" word={engineLabel(receipt.engine_rev)} />
          <Chip kind="meta" word={`${receipt.event_count} steps`} />
          <Chip kind="meta" word="SHA-256" meta={shortHash(receipt.stream_hash)} />
          <CopyButton text={receipt.stream_hash} />
        </div>
      )}
      <hr className="rule" />
      {c.loop === 'chain' ? <ChainTransport /> : <SimTransport />}
    </div>
  );
}

/* ── transport: step through the causal chain ─────────────────────────── */
function ChainTransport() {
  const c = useScn();
  const s = useStore();
  const events = s.chain?.events ?? [];
  const [playing, setPlaying] = useState(false);
  const cur = useRef<{ seq: number | null }>({ seq: null });
  cur.current.seq = s.selectedSeq;
  const go = useCallback((a: StepAction) => {
    const seq = chainStep(events, cur.current.seq, a);
    if (seq === null) return;
    const ev = events.find((e) => e.seq === seq);
    if (ev) s.selectNode(ev.node_id, ev.seq);
  }, [events, s]);
  useEffect(() => {
    if (!playing) return;
    const id = window.setInterval(() => {
      const at = cur.current.seq;
      if (at !== null && events.length && at === events[events.length - 1].seq) { setPlaying(false); return; }
      go('next');
    }, 1000);
    return () => window.clearInterval(id);
  }, [playing, events, go]);
  useEffect(() => { setPlaying(false); }, [s.chain?.run_id]);
  if (!s.chain) return <Empty word="No run" />;
  const sel = events.find((e) => e.seq === s.selectedSeq) ?? null;
  const atEnd = sel !== null && sel.seq === events[events.length - 1]?.seq;
  return (
    <div className="scn-transport" role="group" aria-label="Chain transport">
      <div className="scn-row">
        <button type="button" className="btn scn-btn" onClick={() => go('first')} aria-label="First step" title="First step"><Icon name="skip-back" /></button>
        <button type="button" className="btn scn-btn" onClick={() => go('prev')} aria-label="Previous step" title="Previous step"><Icon name="step-back" /></button>
        <button type="button" className="btn scn-btn scn-play" onClick={() => { if (!playing && (sel === null || atEnd)) go('first'); setPlaying((p) => !p); }}
          aria-pressed={playing} aria-label={playing ? 'Pause' : 'Play'}><Icon name={playing ? 'pause' : 'play'} /></button>
        <button type="button" className="btn scn-btn" onClick={() => go('next')} aria-label="Next step" title="Next step"><Icon name="step-forward" /></button>
        <button type="button" className="btn scn-btn" onClick={() => go('last')} aria-label="Last step" title="Last step"><Icon name="skip-forward" /></button>
        <span className="mono scn-readout" data-testid="chain-readout">{chainReadout(events, s.selectedSeq)}</span>
        <Chip kind="meta" word={s.chain.run_id.slice(0, 18)} />
      </div>
      {sel && (
        <div className="scn-step mono">
          <b>{c.nameOf(sel.node_id)}</b><span>{sel.channel}</span><span>+{sel.delay_d.toFixed(1)} d</span><span>{sel.at.slice(0, 10)}</span>
        </div>
      )}
    </div>
  );
}

/* ── transport: the existing sim playhead ─────────────────────────────── */
const SPEEDS = [0.5, 1, 2, 4];
function SimTransport() {
  const s = useStore();
  const run = s.simRun;
  if (!s.simActive || !run) return <Empty word="No run" />;
  const clock = simClockReadout(run, s.simPlayhead);
  const last = run.ticks - 1;
  return (
    <div className="scn-transport" role="group" aria-label="Sim transport">
      <div className="scn-row">
        <button type="button" className="btn scn-btn" onClick={() => s.setSimPlayhead(0)} aria-label="Start" title="Start"><Icon name="skip-back" /></button>
        <button type="button" className="btn scn-btn" onClick={() => s.simStepGranularity(-1)} aria-label="Step back" title="Step back"><Icon name="step-back" /></button>
        <button type="button" className="btn scn-btn scn-play" onClick={s.simPlaying ? s.simPause : s.simPlay} aria-pressed={s.simPlaying}
          aria-label={s.simPlaying ? 'Pause' : 'Play'}><Icon name={s.simPlaying ? 'pause' : 'play'} /></button>
        <button type="button" className="btn scn-btn" onClick={() => s.simStepGranularity(1)} aria-label="Step forward" title="Step forward"><Icon name="step-forward" /></button>
        <button type="button" className="btn scn-btn" onClick={() => s.setSimPlayhead(clampPlayhead(last, run.ticks))} aria-label="End" title="End"><Icon name="skip-forward" /></button>
        <span className="mono scn-readout" data-testid="sim-readout">{`Tick ${floorTickOf(s.simPlayhead)} / ${last}`}</span>
      </div>
      <div className="scn-row scn-row--wrap">
        <span className="mono scn-clock">{clock.dateTime}</span>
        <Chip kind="meta" word={clock.tickLabel} />
        <Seg<number> label="Speed" value={s.simSpeed} onChange={s.setSimSpeed} options={SPEEDS.map((v) => ({ v, label: `${v}×` }))} />
      </div>
    </div>
  );
}
