import { fmtDateLong } from '../../format';
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Chip } from '../../shell/Chip';
import { useStore } from '../../store';
import { scn } from './client';
import { useScn } from './ctx';
import {
  SHAPES, SIM_TYPE_WORD, buildForkBody, buildNewCheckBody, buildRunBody, buildSimForkBody, buildSimIntervention, chainBook,
  chainPreview, plainLine, chainShocksInForce, defaultStartDate, effectiveSize, forkOverrides, inForceChip, kindWord, mapRefusal, refusalOfCheck, resolvePlace, shortId,
  simBook, simShocksInForce, simPreview, validateSimDraft, type BookRow, type ChainShockDraft, type Refusal, type SimShockDraft,
} from './logic';
import type { ShockCheck, ShockShape, SimInterventionKind } from './types';
import { Building, Empty, Field, RefusalLine, Seg, Tag } from './ui';
import { labelWord } from '../../copy';

type ChainMode = 'NEW' | 'FORK';
type CheckState =
  | { s: 'idle' }
  | { s: 'checking' }
  | { s: 'accepted'; diverges: number | null; local: boolean }
  | { s: 'refused'; r: Refusal };

const errRefusal = (e: unknown): Refusal => {
  const x = e as { code?: string; message?: string };
  return mapRefusal(x.code ?? 'error', x.message ?? String(e));
};

export function ShockTab() {
  const c = useScn();
  return (
    <div className="scn-tab" data-tab="SHOCK">
      {c.loop === 'chain' ? <ChainDesk /> : <SimDesk />}
      <ShockBook />
    </div>
  );
}

/** Place input with the served places as options; only an exact place validates. */
function PlaceInput({ label, value, onChange, bad, id }: { label: string; value: string; onChange: (v: string) => void; bad?: boolean; id: string }) {
  const c = useScn();
  const ok = resolvePlace(value, c.places) !== null;
  return (
    <Field label={label} bad={bad || (value !== '' && !ok)} wide>
      <input className="mono" list={id} value={value} onChange={(e) => onChange(e.target.value)} placeholder="Suez Canal" autoComplete="off"
        aria-label={label} aria-invalid={value !== '' && !ok} />
      <datalist id={id}>{c.places.map((p) => <option key={p.id} value={p.name} label={`${p.id} · ${p.kind}`} />)}</datalist>
      {value !== '' && !ok && <Tag tone="bad" word="Unknown place" />}
    </Field>
  );
}

const SIZE_STEP = 0.05;

/* ── chain loop: new run on any place, or a fork shock at a step ──────── */
function ChainDesk() {
  const c = useScn();
  const s = useStore();
  const events = s.chain?.events ?? [];
  const [mode, setMode] = useState<ChainMode>('NEW');
  const [placeText, setPlaceText] = useState('Suez Canal');
  const [kind, setKind] = useState('closure');
  const [size, setSize] = useState(0.5);
  const [durationD, setDurationD] = useState(14);
  const [shape, setShape] = useState<ShockShape>('step');
  const [startsOn, setStartsOn] = useState(() => defaultStartDate(new Date()));
  const [seq, setSeq] = useState<number | null>(null);
  const [check, setCheck] = useState<CheckState>({ s: 'idle' });
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<{ runId: string; parent: string | null } | null>(null);
  const [fail, setFail] = useState<Refusal | null>(null);
  const checkTicket = useRef(0);

  useEffect(() => {
    if (!events.length) { setSeq(null); return; }
    setSeq((cur) => (cur !== null && events.some((e) => e.seq === cur) ? cur : events[Math.min(2, events.length - 1)].seq));
  }, [s.chain?.run_id]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (s.selectedSeq !== null && events.some((e) => e.seq === s.selectedSeq)) setSeq(s.selectedSeq); }, [s.selectedSeq]); // eslint-disable-line react-hooks/exhaustive-deps

  const place = resolvePlace(placeText, c.places);
  const forkEvent = events.find((e) => e.seq === seq) ?? null;
  const kinds = c.rules?.kinds ?? ['closure'];
  const draft: ChainShockDraft = {
    placeId: mode === 'FORK' ? forkEvent?.node_id ?? '' : place?.id ?? '',
    kind, size: mode === 'FORK' ? size : effectiveSize(kind, size, c.rules), durationD, shape, startsOn,
  };
  const preview = chainPreview(draft, c.nameOf, mode === 'FORK' ? { t: 'step', seq: seq ?? 0 } : { t: 'date' }, c.rules);

  const ready = mode === 'NEW' ? place !== null && durationD > 0 : !!s.chain && seq !== null && durationD > 0;
  const body = useMemo(() => {
    if (!ready) return null;
    return mode === 'NEW' ? buildNewCheckBody(draft, c.rev, c.rules) : { run_id: s.chain!.run_id, at_seq: seq!, overrides: forkOverrides(draft) };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, mode, draft.placeId, draft.kind, draft.size, draft.durationD, draft.shape, draft.startsOn, c.rev, seq, s.chain?.run_id, c.rules]);

  const runCheck = useCallback(async (b: unknown) => {
    const t = ++checkTicket.current;
    setCheck({ s: 'checking' });
    try {
      const r: ShockCheck = await scn.check(b);
      if (t !== checkTicket.current) return;
      const ref = refusalOfCheck(r);
      setCheck(ref ? { s: 'refused', r: ref } : { s: 'accepted', diverges: r.diverges_from_seq ?? null, local: false });
    } catch (e) {
      if (t === checkTicket.current) setCheck({ s: 'refused', r: errRefusal(e) });
    }
  }, []);
  useEffect(() => {
    setDone(null); setFail(null);
    if (!body) { checkTicket.current++; setCheck({ s: 'idle' }); return; }
    const id = window.setTimeout(() => void runCheck(body), 450);
    return () => window.clearTimeout(id);
  }, [body, runCheck]);

  const inject = async () => {
    if (!body) return;
    setBusy(true); setFail(null);
    try {
      let runId: string; let parent: string | null = null;
      if (mode === 'NEW') {
        runId = (await scn.run(buildRunBody(draft, c.rev, c.rules))).run_id;
      } else {
        parent = s.chain!.run_id;
        runId = (await scn.fork(buildForkBody(parent, seq!, draft))).run_id;
      }
      await c.openChainRun(runId);
      c.noteRunsChanged();
      setDone({ runId, parent });
      if (parent) c.setCmp({ a: parent, b: runId });
    } catch (e) {
      const r = errRefusal(e);
      setFail(r);
      c.addSession({ key: `s${Date.now()}`, n: '+', at: mode === 'FORK' ? `Step ${seq}` : 'Start', kind: mode === 'FORK' ? 'FORK SHOCK' : 'SHOCK', place: c.nameOf(draft.placeId), what: preview ?? '', status: 'REFUSED', code: r.code, session: true });
    } finally { setBusy(false); }
  };

  const refused = check.s === 'refused' ? check.r : null;
  const field = (f: Refusal['field']) => refused?.field === f;
  /* D-21: a fork shock on a place the parent's journal already holds a shock on. */
  const journal = c.chainJournal.id === s.chain?.run_id ? c.chainJournal.doc : null;
  const inForce = mode === 'FORK' ? inForceChip(chainShocksInForce(journal, forkEvent?.node_id ?? '', seq)) : null;

  return (
    <div className="scn-desk" data-desk="chain">
      <div className="scn-row">
        <Seg<ChainMode> label="Shock target" value={mode} onChange={setMode}
          options={[{ v: 'NEW', label: 'New run' }, { v: 'FORK', label: 'Fork at step', disabled: !s.chain }]} />
        <Chip kind="meta" word={mode === 'NEW' ? c.rev === 1 ? 'Engine rev 1' : 'Engine rev 2' : (c.chain ? `Parent ${shortId(c.chain.run_id)}` : 'No run')} />
      </div>
      <div className="scn-grid">
        {mode === 'NEW' ? (
          <>
            <PlaceInput id="scn-places-new" label="Lands on" value={placeText} onChange={setPlaceText} bad={field('place')} />
            <Field label="Type" bad={field('kind')}>
              <select value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Shock type">
                {kinds.map((k) => <option key={k} value={k}>{kindWord(k)}</option>)}
              </select>
            </Field>
            <Field label="Starts on (UTC)"><input type="date" className="mono" value={startsOn} onChange={(e) => setStartsOn(e.target.value)} aria-label="Starts on" /><span className="scb-datefmt mono">{fmtDateLong(startsOn)}</span></Field>
          </>
        ) : (
          <Field label="Lands on step" bad={field('step')} wide>
            <select value={seq ?? ''} onChange={(e) => { const v = Number(e.target.value); setSeq(v); const ev = events.find((x) => x.seq === v); if (ev) s.selectNode(ev.node_id, ev.seq); }} aria-label="Lands on step">
              {events.map((e) => <option key={e.seq} value={e.seq}>{`${e.seq} · ${c.nameOf(e.node_id)} · ${e.channel}`}</option>)}
            </select>
          </Field>
        )}
        <Field label={mode === 'FORK' || kind !== 'closure' ? 'Size (capacity loss)' : 'Size'} bad={field('size')}>
          <input type="number" className="mono" min={SIZE_STEP} max={1} step={SIZE_STEP}
            value={mode === 'NEW' && kind === 'closure' ? 1 : size} disabled={mode === 'NEW' && kind === 'closure'}
            onChange={(e) => setSize(Number(e.target.value))} aria-label="Size" />
        </Field>
        <Field label="Duration (d)" bad={field('duration')}>
          <input type="number" className="mono" min={1} max={c.rules?.duration_d.max ?? 3650} step={1} value={durationD}
            onChange={(e) => setDurationD(Number(e.target.value))} aria-label="Duration" />
        </Field>
        <div className={`scn-field${field('shape') ? ' scn-field--bad' : ''}`}><span>Shape</span>
          <Seg<ShockShape> label="Shape" value={shape} onChange={setShape}
            options={SHAPES.filter((x) => !c.rules || c.rules.shapes.includes(x)).map((x) => ({ v: x, label: labelWord(x) }))} />
        </div>
      </div>
      <div className="scn-preview mono" aria-live="polite" data-testid="preview">
        <span className="scn-preview-k">Preview</span><span>{preview ?? '—'}</span>
      </div>
      <div className="scn-row scn-row--wrap">
        {check.s === 'idle' && <Tag tone="idle" word="Not checked" />}
        {check.s === 'checking' && <Tag tone="busy" word="Checking" />}
        {check.s === 'accepted' && <Tag tone="ok" word="Accepted" meta={check.diverges !== null ? `Diverges from step ${check.diverges}` : undefined} />}
        {inForce && <Tag tone="warn" word={inForce.word} meta={inForce.meta} title="Held by parent" />}
        <button type="button" className="btn scn-btn" disabled={!body || check.s === 'checking'} onClick={() => body && void runCheck(body)}>Validate</button>
        <button type="button" className="btn btn--amber scn-btn" disabled={!body || busy || check.s === 'refused' || check.s === 'checking'} onClick={() => void inject()}>
          {busy ? 'Running' : mode === 'FORK' ? 'Fork shock' : 'Create run'}
        </button>
        <Chip kind="sim" />
      </div>
      {refused && <RefusalLine r={refused} />}
      {fail && <RefusalLine r={fail} />}
      {done && (
        <div className="scn-row scn-row--wrap mono">
          <Tag tone="ok" word={done.parent ? 'Forked' : 'Created'} meta={shortId(done.runId)} />
          {done.parent && <button type="button" className="btn scn-btn" onClick={() => c.go('CMP')}>Compare</button>}
          <button type="button" className="btn scn-btn" onClick={() => c.go('JRNL')}>Journal</button>
        </div>
      )}
    </div>
  );
}

/* ── sim loop: a shock dated at a tick, injected as a fork ────────────── */
const SIM_TYPES: SimInterventionKind[] = ['disruption', 'airspace', 'storm_cell'];

function SimDesk() {
  const c = useScn();
  const s = useStore();
  const run = s.simActive ? s.simRun : null;
  const ticks = run?.ticks ?? 0;
  const [type, setType] = useState<SimInterventionKind>('disruption');
  const [placeText, setPlaceText] = useState('Suez Canal');
  const [toText, setToText] = useState('');
  const [kind, setKind] = useState('closure');
  const [size, setSize] = useState(1);
  const [durationD, setDurationD] = useState(3);
  const [radiusKm, setRadiusKm] = useState(300);
  const [atTick, setAtTick] = useState(Math.min(4, Math.max(0, ticks - 1)));
  const [busy, setBusy] = useState(false);
  const [fail, setFail] = useState<Refusal | null>(null);
  const [note, setNote] = useState<{ tone: 'ok' | 'idle'; word: string; meta?: string } | null>(null);

  useEffect(() => { setAtTick((t) => Math.max(0, Math.min(t, Math.max(0, ticks - 1)))); }, [ticks]);
  useEffect(() => { setFail(null); setNote(null); }, [run?.sim_run_id]);

  const place = resolvePlace(placeText, c.places);
  const toPlace = resolvePlace(toText, c.places);
  const kinds = c.rules?.kinds ?? ['closure'];
  const draft: SimShockDraft = {
    type, placeId: place?.id ?? '', toPlaceId: toPlace?.id ?? '', kind, size: kind === 'closure' ? 1 : size, durationD, radiusKm, atTick,
  };
  const placeIds = useMemo(() => new Set(c.places.map((p) => p.id)), [c.places]);
  const local = run ? validateSimDraft(draft, { ticks, places: placeIds, rules: c.rules }) : null;
  const preview = simPreview(draft, c.nameOf, c.rules);
  const building = c.build?.state === 'building';
  const field = (f: Refusal['field']) => (fail ?? local)?.field === f;
  /* D-21: the run's journal already holds this shock at this place and tick. */
  const journal = c.simJournal.id === run?.sim_run_id ? c.simJournal.doc : null;
  const ticksPerDay = run && typeof run.tick_seconds === 'number' && run.tick_seconds > 0 ? 86400 / run.tick_seconds : 1;
  const inForce = inForceChip(simShocksInForce(journal, draft, ticksPerDay));

  const inject = async () => {
    if (!run || local) return;
    setBusy(true); setFail(null); setNote(null);
    const body = buildSimForkBody(atTick, [buildSimIntervention(draft, c.rules)]);
    try {
      const { status, body: r } = await scn.simFork(run.sim_run_id, body);
      if (r.unchanged) { setNote({ tone: 'idle', word: 'Unchanged', meta: shortId(r.sim_run_id) }); return; }
      if (status === 202 || r.status === 'building') {
        c.beginBuild({ simRunId: r.sim_run_id, parentId: run.sim_run_id, atTick, since: Date.now(), state: 'building', error: null });
      } else {
        c.setCmp({ a: run.sim_run_id, b: r.sim_run_id });
        await c.openSimRunId(r.sim_run_id);
        c.noteRunsChanged();
        setNote({ tone: 'ok', word: 'Forked', meta: shortId(r.sim_run_id) });
      }
    } catch (e) {
      const r = errRefusal(e);
      setFail(r);
      c.addSession({ key: `s${Date.now()}`, n: '+', at: `Tick ${atTick}`, kind: 'SHOCK', place: c.nameOf(draft.placeId), what: preview ?? '', status: 'REFUSED', code: r.code, session: true });
    } finally { setBusy(false); }
  };

  if (!run) return <Empty word="No run" />;
  return (
    <div className="scn-desk" data-desk="sim">
      <div className="scn-row scn-row--wrap">
        <Seg<SimInterventionKind> label="Shock type" value={type} onChange={setType} options={SIM_TYPES.map((t) => ({ v: t, label: SIM_TYPE_WORD[t] }))} />
        <Chip kind="meta" word={`Parent ${shortId(run.sim_run_id)}`} />
      </div>
      <div className="scn-grid">
        <PlaceInput id="scn-places-sim" label={type === 'storm_cell' ? 'From place' : 'Lands on'} value={placeText} onChange={setPlaceText} bad={field('place') && !toText} />
        {type === 'storm_cell' && <PlaceInput id="scn-places-sim-to" label="To place" value={toText} onChange={setToText} bad={field('place') && !!toText} />}
        {type === 'disruption' && (
          <>
            <Field label="Type" bad={field('kind')}>
              <select value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Shock kind">
                {kinds.map((k) => <option key={k} value={k}>{kindWord(k)}</option>)}
              </select>
            </Field>
            <Field label="Size (capacity loss)" bad={field('size')}>
              <input type="number" className="mono" min={SIZE_STEP} max={1} step={SIZE_STEP} value={kind === 'closure' ? 1 : size} disabled={kind === 'closure'}
                onChange={(e) => setSize(Number(e.target.value))} aria-label="Size" />
            </Field>
          </>
        )}
        {type !== 'disruption' && (
          <Field label="Radius (km)" bad={field('radius')}>
            <input type="number" className="mono" min={1} max={20000} value={radiusKm} onChange={(e) => setRadiusKm(Number(e.target.value))} aria-label="Radius" />
          </Field>
        )}
        <Field label="Duration (d)" bad={field('duration')}>
          <input type="number" className="mono" min={1} max={c.rules?.duration_d.max ?? 3650} value={durationD} onChange={(e) => setDurationD(Number(e.target.value))} aria-label="Duration" />
        </Field>
        <Field label={`Starts at tick (0 to ${Math.max(0, ticks - 1)})`} bad={field('tick')}>
          <input type="number" className="mono" min={0} max={Math.max(0, ticks - 1)} value={atTick} onChange={(e) => setAtTick(Number(e.target.value))} aria-label="Starts at tick" />
        </Field>
        <div className="scn-field"><span>Shape</span><Seg<'step'> label="Shape" value="step" onChange={() => undefined} options={[{ v: 'step', label: 'Step' }]} /></div>
      </div>
      <div className="scn-preview mono" aria-live="polite" data-testid="preview">
        <span className="scn-preview-k">Preview</span><span>{preview ?? '—'}</span>
      </div>
      <div className="scn-row scn-row--wrap">
        {local ? <Tag tone="bad" word="Refused" meta={local.code} /> : <Tag tone="ok" word="Local check" />}
        {inForce && <Tag tone="warn" word={inForce.word} meta={inForce.meta} title="Held by run" />}
        <button type="button" className="btn btn--amber scn-btn" disabled={!!local || busy || building || !preview} onClick={() => void inject()}>
          {busy ? 'Sending' : building ? 'Building' : 'Fork shock'}
        </button>
        <Chip kind="sim" />
        {c.build?.state === 'building' && <Building since={c.build.since} meta={shortId(c.build.simRunId)} />}
      </div>
      {local && <RefusalLine r={local} />}
      {fail && <RefusalLine r={fail} />}
      {c.build?.state === 'failed' && c.build.error && (
        <div className="scn-refusal mono" role="alert"><Tag tone="bad" word="Failed" meta={shortId(c.build.simRunId)} /><span className="scn-reason">{plainLine(c.build.error)}</span></div>
      )}
      {c.build?.state === 'ready' && (
        <div className="scn-row scn-row--wrap mono">
          <Tag tone="ok" word="Built" meta={shortId(c.build.simRunId)} />
          <button type="button" className="btn scn-btn" onClick={() => c.go('CMP')}>Compare</button>
        </div>
      )}
      {note && <div className="scn-row mono"><Tag tone={note.tone} word={note.word} meta={note.meta} /></div>}
    </div>
  );
}

/* ── the shock book: journaled shocks and this browser session's attempts ─────── */
const STATUS_TONE: Record<BookRow['status'], 'ok' | 'bad' | 'busy' | 'idle' | 'warn'> = {
  APPLIED: 'ok', FIRED: 'ok', PENDING: 'warn', ACCEPTED: 'ok', REFUSED: 'bad', INJECTED: 'ok', BUILDING: 'busy', FAILED: 'bad',
};

export function ShockBook() {
  const c = useScn();
  const s = useStore();
  const journalRows = useMemo<BookRow[]>(() => (c.loop === 'chain'
    ? chainBook(c.chainJournal.doc, c.nameOf)
    : simBook(c.simJournal.doc, c.nameOf, Math.floor(s.simPlayhead))), [c.loop, c.chainJournal.doc, c.simJournal.doc, c.nameOf, s.simPlayhead]);
  const building: BookRow[] = c.loop === 'sim' && c.build && c.build.state !== 'ready'
    ? [{ key: `b${c.build.simRunId}`, n: '+', at: `Tick ${c.build.atTick}`, kind: 'SHOCK', place: '—', what: shortId(c.build.simRunId), status: c.build.state === 'building' ? 'BUILDING' : 'FAILED', session: true }]
    : [];
  const rows = [...journalRows, ...building, ...c.session];
  return (
    <section className="scn-book" aria-label="Shock book">
      <div className="scn-subhead mono">Shock book<span className="scn-count">{rows.length}</span></div>
      {rows.length === 0 ? <Empty word="No shocks" /> : (
        <table className="data-table scn-table">
          <thead><tr><th className="num">#</th><th>At</th><th>Kind</th><th>Place</th><th>What</th><th>Status</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.key} className={r.session ? 'scn-session' : undefined}>
                <td className="num mono">{r.n}</td><td className="mono">{r.at}</td><td className="mono">{labelWord(r.kind)}</td>
                <td>{r.place}</td><td className="mono">{r.what}</td>
                <td><Tag tone={STATUS_TONE[r.status]} word={r.status} meta={r.code} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
