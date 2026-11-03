import React, { useEffect, useMemo, useState } from 'react';
import { labelWord } from '../../copy';
import type { DisruptionSpec } from '../../types';
import { Button, IconButton, SegmentedControl } from '../../ui';
import { mcSlot, pickNote, type PickedDisruption } from '../../handoffs/pick';
import { Chip, MetaChip } from '../reports/chips';
import type { PlaceChoice } from '../laneplanner/logic';
import type { Scenario } from '../laneplanner/types';
import { fmtInt } from './fmt';
import {
  buildRequest, checkDraft, clampN, clampSeed, codeWord, DEFAULT_N, DEFAULT_SEED, defaultInputs, DIST_OPTIONS, disruptionFromScenario, disruptionFromSpec, draftsFrom,
  draftsValid, ENGINE_CHOICES, inputLabel, isClosure, isDistKind, N_STEPS, overridesFrom, reseedFrom, validateDisruption, type RangeDraft,
} from './inputs';
import { estimateQuote, needsConfirm, progressOf, quoteWord, isBusy, type McState } from './state';
import type { McRequest } from './types';
import type { RunRec } from './runs';
import { runSub, runTitle } from './runs';

/* Run setup: the disruption (a preset or a place, kind, duration and capacity loss), the engine, the number of
 * simulations, the seed, the input ranges and, optionally, a run whose random draws the new run shares.
 * Chips and labels only; a run quoted above two minutes asks for a second press. */

const KINDS = ['closure', 'capacity_cut', 'strike', 'congestion'];
const CHAIN = '@chain';
const CUSTOM = '';

export interface SetupProps {
  places: readonly PlaceChoice[];
  scenarios: readonly Scenario[] | null;
  chainSpec: DisruptionSpec | null;
  runs: readonly RunRec[];
  nameOf: (id: string) => string;
  state: McState;
  /** per-run seconds the server last measured, for the quote before the server answers */
  sPerRun: number | null;
  onRun: (req: McRequest) => void;
  onCancel: () => void;
}

const isSuezFirst = (a: PlaceChoice, b: PlaceChoice): number => (a.id === 'chk_suez' ? -1 : b.id === 'chk_suez' ? 1 : 0);

export function Setup({ places, scenarios, chainSpec, runs, nameOf, state, sPerRun, onRun, onCancel }: SetupProps) {
  const core = useMemo(() => places.filter((p) => p.tier === 'core' && p.mode === 'sea').sort((a, b) => isSuezFirst(a, b) || (a.kind === b.kind ? a.name.localeCompare(b.name) : a.kind === 'chokepoint' ? -1 : 1)), [places]);
  const [preset, setPreset] = useState<string>(scenarios?.some((s) => s.id === 'suez-closure') ? 'suez-closure' : CUSTOM);
  const [placeId, setPlaceId] = useState<string>('chk_suez');
  const [kind, setKind] = useState<string>('closure');
  const [duration, setDuration] = useState<string>('14');
  const [lossPct, setLossPct] = useState<string>('50');
  const [engine, setEngine] = useState<string>('r2');
  const [n, setN] = useState<number>(DEFAULT_N);
  const [seed, setSeed] = useState<string>(String(DEFAULT_SEED));
  const [pairKey, setPairKey] = useState<string>('');
  const [touched, setTouched] = useState<Record<string, RangeDraft>>({});
  const [open, setOpen] = useState(false);
  const [armed, setArmed] = useState(false);
  /* a scenario handed over by the Scenario builder fills the disruption and the engine; it is read on mount and on every later hand-off */
  const [fromBuilder, setFromBuilder] = useState<PickedDisruption | null>(null);
  useEffect(() => {
    const apply = () => {
      const p = mcSlot.take();
      if (!p) return;
      setPreset(CUSTOM); setPlaceId(p.placeId); setKind(p.kind); setDuration(String(p.durationD)); setLossPct(String(Math.round(p.capacityLoss * 100)));
      setEngine(p.engineRev === 1 ? 'r1' : 'r2'); setTouched({}); setArmed(false); setFromBuilder(p);
    };
    apply();
    return mcSlot.subscribe(apply);
  }, []);

  /* a preset fills the place, kind, duration and capacity loss once, when it is chosen or when the library arrives */
  const fill = (id: string) => {
    setPreset(id);
    setFromBuilder(null);
    setArmed(false);
    if (id === CHAIN && chainSpec) {
      setPlaceId(chainSpec.node_id); setKind(chainSpec.kind); setDuration(String(chainSpec.duration_d)); setLossPct(String(Math.round((chainSpec.capacity_loss ?? 1) * 100)));
      return;
    }
    const s = scenarios?.find((x) => x.id === id);
    if (s) {
      setPlaceId(s.place_id); setKind(s.kind); setDuration(String(s.default_duration_d));
      setLossPct(String(Math.round((s.default_capacity_loss ?? 0.5) * 100)));
    }
  };
  useEffect(() => { if (preset && scenarios) fill(preset); /* once the library is known */ }, [scenarios]); // eslint-disable-line react-hooks/exhaustive-deps

  const partner = runs.find((r) => r.key === pairKey) ?? null;
  const dur = Number(duration);
  const loss = Number(lossPct) / 100;
  const closure = isClosure(kind);
  const disruption = useMemo(() => {
    const base = { node_id: placeId, kind, duration_d: dur, capacity_loss: closure ? 1 : loss };
    const sc = scenarios?.find((s) => s.id === preset);
    return sc && sc.place_id === placeId && sc.kind === kind ? disruptionFromScenario(sc, dur, closure ? null : loss) : disruptionFromSpec(base);
  }, [placeId, kind, dur, loss, closure, scenarios, preset]);
  const disruptionError = validateDisruption(disruption);

  const defaults = useMemo(() => draftsFrom(defaultInputs(disruption)), [disruption]);
  const rows = useMemo(() => defaults.map((d) => touched[d.name] ?? d), [defaults, touched]);
  const rowsOk = draftsValid(rows);
  const setRow = (name: string, patch: Partial<RangeDraft>) => {
    setArmed(false);
    setTouched((t) => ({ ...t, [name]: { ...(t[name] ?? defaults.find((d) => d.name === name)!), ...patch } }));
  };

  const eng = ENGINE_CHOICES.find((c) => c.value === engine) ?? ENGINE_CHOICES[1];
  const effectiveN = partner ? partner.job.request.n : n;
  const effectiveSeed = partner ? Math.abs(partner.job.request.seed) : clampSeed(Number(seed));
  const request = (): McRequest => {
    const body = buildRequest({
      disruption, engineRev: eng.rev, profile: eng.profile, n: effectiveN, seed: effectiveSeed,
      distributions: overridesFrom(rows, defaults, closure),
    });
    if (partner) body.pair_with = partner.job.job_id;
    return body;
  };
  const quote = estimateQuote(effectiveN, sPerRun ?? undefined);
  const askConfirm = needsConfirm(quote);
  const busy = isBusy(state);
  const seedBad = !partner && !/^-?\d+$/.test(seed.trim());
  const canRun = !busy && !disruptionError && rowsOk && !seedBad && !!placeId;

  const run = () => {
    if (!canRun) return;
    if (askConfirm && !armed) { setArmed(true); return; }
    setArmed(false);
    onRun(request());
  };

  const status = (() => {
    switch (state.phase) {
      case 'refused': return <Chip chip="FLAG" word={codeWord(state.code)} title={state.code ?? undefined} />;
      case 'notserved': return <Chip chip="NOT SERVED" word="Not served" />;
      case 'down': return <Chip chip="DOWN" word="Down" title={state.code ?? undefined} />;
      case 'failed': return <Chip chip="FLAG" word="Failed" title={state.code ?? undefined} />;
      case 'interrupted': return <Chip chip="FLAG" word="Interrupted" />;
      default: return null;
    }
  })();
  const p = progressOf(state);
  const done = state.job?.progress.done ?? 0;
  const total = state.job?.progress.n ?? state.quote?.n ?? effectiveN;

  return (
    <section className="mc-setup" aria-label="Run setup" data-testid="mc-setup">
      <div className="mc-fields">
        <div className="mc-field mc-field--wide">
          <label htmlFor="mc-preset" className="mc-lab">Preset</label>
          <select id="mc-preset" className="mc-input" value={preset} onChange={(e) => fill(e.target.value)}>
            <option value={CUSTOM}>Custom</option>
            {chainSpec && <option value={CHAIN}>{`Loaded chain · ${nameOf(chainSpec.node_id)} ${labelWord(chainSpec.kind).toLowerCase()}`}</option>}
            {(scenarios ?? []).map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
          </select>
        </div>
        <div className="mc-field mc-field--wide">
          <label htmlFor="mc-place" className="mc-lab">Place</label>
          <select id="mc-place" className="mc-input" value={placeId} onChange={(e) => { setPlaceId(e.target.value); setPreset(CUSTOM); setArmed(false); setFromBuilder(null); }}>
            {!core.some((c) => c.id === placeId) && <option value={placeId}>{nameOf(placeId)}</option>}
            {core.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </div>
        <div className="mc-field">
          <label htmlFor="mc-kind" className="mc-lab">Kind</label>
          <select id="mc-kind" className="mc-input" value={kind} onChange={(e) => { setKind(e.target.value); setPreset(CUSTOM); setArmed(false); setFromBuilder(null); }}>
            {(KINDS.includes(kind) ? KINDS : [kind, ...KINDS]).map((k) => <option key={k} value={k}>{labelWord(k)}</option>)}
          </select>
        </div>
        <div className="mc-field mc-field--num">
          <label htmlFor="mc-dur" className="mc-lab">Duration</label>
          <span className="mc-unit"><input id="mc-dur" className="mc-input" type="number" min={1} max={3650} step={1} value={duration} aria-invalid={!(dur > 0 && dur <= 3650)}
            onChange={(e) => { setDuration(e.target.value); setArmed(false); setFromBuilder(null); }} /><i>d</i></span>
        </div>
        {!closure && (
          <div className="mc-field mc-field--num">
            <label htmlFor="mc-loss" className="mc-lab">Capacity loss</label>
            <span className="mc-unit"><input id="mc-loss" className="mc-input" type="number" min={5} max={100} step={5} value={lossPct} aria-invalid={!(loss > 0 && loss <= 1)}
              onChange={(e) => { setLossPct(e.target.value); setArmed(false); setFromBuilder(null); }} /><i>%</i></span>
          </div>
        )}
      </div>
      <div className="mc-fields">
        <div className="mc-field mc-field--seg">
          <span className="mc-lab" id="mc-eng-l">Engine</span>
          <SegmentedControl label="Engine" size="sm" value={engine} onChange={(v) => { setEngine(v); setArmed(false); }} options={ENGINE_CHOICES.map((c) => ({ value: c.value, label: c.label }))} />
        </div>
        <div className="mc-field mc-field--seg">
          <span className="mc-lab">Simulations</span>
          <SegmentedControl label="Simulations" size="sm" value={String(partner ? effectiveN : n)} onChange={(v) => { if (!partner) { setN(clampN(Number(v))); setArmed(false); } }}
            options={N_STEPS.map((x) => ({ value: String(x), label: String(x) }))} />
        </div>
        <div className="mc-field mc-field--num">
          <label htmlFor="mc-seed" className="mc-lab">Seed</label>
          <span className="mc-unit">
            <input id="mc-seed" className="mc-input" inputMode="numeric" value={partner ? String(effectiveSeed) : seed} disabled={!!partner} aria-invalid={seedBad}
              onChange={(e) => { setSeed(e.target.value); setArmed(false); }} />
            <IconButton icon="refresh" label="Reseed" size="sm" disabled={!!partner} onClick={() => { setSeed(String(reseedFrom(Math.random()))); setArmed(false); }} />
          </span>
        </div>
        {runs.some((r) => r.source === 'server') && (
          <div className="mc-field mc-field--wide">
            <label htmlFor="mc-pair" className="mc-lab">Share draws with</label>
            <select id="mc-pair" className="mc-input" value={pairKey} onChange={(e) => { setPairKey(e.target.value); setArmed(false); }}>
              <option value="">Not paired</option>
              {runs.filter((r) => r.source === 'server').map((r) => <option key={r.key} value={r.key}>{`${runTitle(r, nameOf)} · ${runSub(r)}`}</option>)}
            </select>
          </div>
        )}
      </div>
      <div className="mc-ranges">
        <button type="button" className="mc-disc" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
          <span>Input ranges</span><span className="mc-disc-n">{Object.keys(touched).length ? `${Object.keys(touched).length} changed` : 'Default'}</span>
        </button>
        {open && (
          <div className="mc-tablewrap">
            <table className="mc-table mc-table--ranges" data-testid="mc-ranges">
              <thead><tr><th scope="col">Input</th><th scope="col">Shape</th><th scope="col" className="num">Low</th><th scope="col" className="num">Mode</th><th scope="col" className="num">High</th><th scope="col">Source</th></tr></thead>
              <tbody>
                {rows.map((r) => {
                  const chk = checkDraft(r);
                  const fixedRow = closure && r.name === 'capacity_loss';
                  const cell = (k: 'low' | 'mode' | 'high', used: boolean) => (used && !fixedRow
                    ? <input className="mc-input mc-input--cell" value={r[k]} aria-label={`${inputLabel(r.name)} ${k}`} aria-invalid={!!chk[k]} title={chk[k]} onChange={(e) => setRow(r.name, { [k]: e.target.value })} />
                    : <span className="mc-dim">{used ? r[k] : '—'}</span>);
                  return (
                    <tr key={r.name}>
                      <th scope="row">{inputLabel(r.name)}</th>
                      <td>{fixedRow ? <span className="mc-dim">Fixed</span> : (
                        <select className="mc-input mc-input--cell" aria-label={`${inputLabel(r.name)} shape`} value={r.dist} onChange={(e) => { if (isDistKind(e.target.value)) setRow(r.name, { dist: e.target.value }); }}>
                          {DIST_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                        </select>)}</td>
                      <td className="num">{cell('low', r.dist !== 'fixed')}</td>
                      <td className="num">{cell('mode', r.dist !== 'uniform')}</td>
                      <td className="num">{cell('high', r.dist !== 'fixed')}</td>
                      <td>{touched[r.name] ? 'Request' : 'Default'}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {Object.keys(touched).length > 0 && <Button size="sm" variant="ghost" icon="reset" onClick={() => { setTouched({}); setArmed(false); }}>Reset ranges</Button>}
          </div>
        )}
      </div>
      <div className="mc-actions">
        <Button variant="primary" icon="play" onClick={run} disabled={!canRun} data-testid="mc-run">{armed ? 'Confirm run' : 'Run'}</Button>
        {busy && <Button variant="secondary" icon="square" onClick={onCancel} data-testid="mc-cancel">Cancel</Button>}
        <MetaChip title="Estimated run time">{quoteWord(state.quote ?? quote) ?? ''}</MetaChip>
        {fromBuilder && <MetaChip>Scenario timeline</MetaChip>}
        {fromBuilder && pickNote(fromBuilder) && <Chip chip="INFO" word={pickNote(fromBuilder)!} />}
        {disruptionError && <Chip chip="FLAG" word={disruptionError} />}
        {!rowsOk && <Chip chip="FLAG" word="Range out of bounds" />}
        {status}
        {busy && (
          <span className="mc-progress" role="progressbar" aria-label="Simulations done" aria-valuemin={0} aria-valuemax={total} aria-valuenow={done}>
            <span className="mc-progress-bar"><i style={{ width: `${Math.round(p * 100)}%` }} /></span>
            <span className="mc-progress-n">{state.phase === 'starting' ? 'Starting' : `${fmtInt(done)} of ${fmtInt(total)}`}</span>
          </span>
        )}
      </div>
    </section>
  );
}
