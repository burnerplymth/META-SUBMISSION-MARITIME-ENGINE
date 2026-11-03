import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { LiveStatus } from '../../ui/LiveStatus';
import { useTabKeys } from '../../ui/Controls';
import { Panel } from '../../components/Panel';
import { safeStorage, useStore } from '../../store';
import { Button, IconButton } from '../../ui';
import { fetchScenarios } from '../laneplanner/client';
import { PlacePicker } from '../laneplanner/PlacePicker';
import { placeChoices } from '../laneplanner/logic';
import type { Scenario } from '../laneplanner/types';
import { Chip, MetaChip, StateLine } from '../reports/chips';
import { loadList as loadShipments } from '../shipments/storage';
import { fetchPlace, fetchTransit } from './client';
import { applyRun, placeStateWord, BASIS_CHIP, BASIS_WORD, evaluate, fmtD, fmtRange, fmtStamp, LEVEL_WORD, placeIds, RULE_WORD, ruleName, transitGroups, transitKey } from './logic';
import { clean, clearState, exportDoc, withSample, importDoc, listIdFor, loadState, MAX_FILE_BYTES, MAX_ITEMS, MAX_LISTS, MAX_RULES, nextId, saveState } from './storage';
import type { Observations, PlaceResult, RuleState, RuleType, TransitObs, WatchItem, WatchList, WatchRule, WatchState } from './types';
import '../laneplanner/lane.css';
import './watch.css';

/* WATCHLISTS (WCH). Lists of places, legs and shipment lanes kept in this
 * browser only, simple rules read against feeds the API already serves, and
 * the entries recorded when a rule's condition is met. Evaluation runs here,
 * on request and every few minutes while the window is open. Added transit is
 * MODELLED from named variants. Nothing is sent to anyone. */

const POLL_MS = 5 * 60 * 1000;
const CONCURRENCY = 4;
type Reading = { at: string; places: Record<string, PlaceResult>; transit: Record<string, TransitObs>; transitFailed: string[]; snapshotMode: boolean };
type Notice = Array<{ chip: string; word: string }> | null;

function download(name: string, text: string) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([text], { type: 'application/json' })); a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 4000);
}

async function pool<T, R>(xs: readonly T[], n: number, fn: (x: T) => Promise<R>): Promise<R[]> {
  const out: R[] = new Array(xs.length); let i = 0;
  await Promise.all(Array.from({ length: Math.min(n, xs.length) }, async () => { while (i < xs.length) { const k = i++; out[k] = await fn(xs[k]); } }));
  return out;
}

export function WatchPanel() {
  const st = useStore();
  const [state, setState] = useState<WatchState>(() => loadState(safeStorage));
  const [scenarios, setScenarios] = useState<Scenario[] | null | undefined>(undefined);
  const [reading, setReading] = useState<Reading | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);
  const [states, setStates] = useState<Record<string, RuleState>>({});
  const fileRef = useRef<HTMLInputElement>(null);
  const stateRef = useRef(state); stateRef.current = state;
  const abort = useRef<AbortController | null>(null);
  const places = useMemo(() => placeChoices(st.graph.nodes), [st.graph.nodes]);
  const snapshotMode = !!st.snapshotMode;
  const library = scenarios ?? [];
  const scnLabel = useCallback((id: string) => library.find((s) => s.id === id)?.label ?? id, [library]);

  useEffect(() => { saveState(safeStorage, state); }, [state]);
  useEffect(() => {
    const ctrl = new AbortController();
    void fetchScenarios(ctrl.signal).then((s) => setScenarios(s));
    return () => ctrl.abort();
  }, []);

  /* one reading of every watched place and every leg or lane with an added-transit rule, then the rules */
  const run = useCallback(async () => {
    abort.current?.abort();
    const ctrl = new AbortController(); abort.current = ctrl;
    const s = stateRef.current;
    const ids = placeIds(s.lists).slice(0, MAX_LISTS * MAX_ITEMS);
    const groups = transitGroups(s);
    if (ids.length === 0 && groups.length === 0) { setReading(null); setStates({}); setBusy(false); return; }
    setBusy(true);
    const results = await pool(ids, CONCURRENCY, (id) => fetchPlace(id, ctrl.signal));
    const placeRes: Record<string, PlaceResult> = {}; ids.forEach((id, i) => { placeRes[id] = results[i]; });
    const transit: Record<string, TransitObs> = {}; const failed: string[] = [];
    for (const g of groups) {
      const r = await fetchTransit(g, ctrl.signal);
      if (r.kind === 'ok') Object.assign(transit, r.byKey); else failed.push(g.scenarioId);
    }
    if (ctrl.signal.aborted) return;
    const now = new Date().toISOString();
    const obs: Observations = {
      places: Object.fromEntries(ids.map((id) => [id, placeRes[id].kind === 'ok' ? (placeRes[id] as Extract<PlaceResult, { kind: 'ok' }>).obs : undefined])),
      transit, snapshotMode,
    };
    const out = evaluate(stateRef.current, obs, now, scnLabel);
    setStates(out.states);
    setState((cur) => applyRun(cur, out));
    setReading({ at: now, places: placeRes, transit, transitFailed: failed, snapshotMode });
    setBusy(false);
  }, [snapshotMode, scnLabel]);

  /* read when the window opens (once the scenario library has answered) and every few minutes */
  const rulesKey = JSON.stringify([state.lists, state.rules]);
  useEffect(() => {
    if (scenarios === undefined) return undefined;
    const t = setTimeout(() => { void run(); }, 400);
    return () => clearTimeout(t);
  }, [rulesKey, scenarios, run]);
  useEffect(() => {
    const t = setInterval(() => { if (typeof document === 'undefined' || document.visibilityState !== 'hidden') void run(); }, POLL_MS);
    return () => { clearInterval(t); abort.current?.abort(); };
  }, [run]);

  /* edits */
  const active = state.lists.find((l) => l.id === state.active) ?? state.lists[0] ?? null;
  const edit = (fn: (s: WatchState) => WatchState) => setState((s) => fn(s));
  const addList = (name: string) => edit((s) => {
    const n = clean(name); if (!n || s.lists.length >= MAX_LISTS) return s;
    const id = listIdFor(s.lists);
    return { ...s, lists: [...s.lists, { id, name: n, items: [] }], active: id };
  });
  const renameList = (name: string) => edit((s) => { const n = clean(name); return !n || !active ? s : { ...s, lists: s.lists.map((l) => (l.id === active.id ? { ...l, name: n } : l)) }; });
  const removeList = () => edit((s) => {
    if (!active) return s;
    const lists = s.lists.filter((l) => l.id !== active.id);
    return { ...s, lists, rules: s.rules.filter((r) => r.listId !== active.id), active: lists[0]?.id ?? null };
  });
  const addItems = (items: Array<Omit<WatchItem, 'id'>>) => edit((s) => {
    if (!active) return s;
    const used = s.lists.flatMap((l) => l.items.map((i) => i.id));
    const lists = s.lists.map((l) => {
      if (l.id !== active.id) return l;
      const next = [...l.items];
      for (const it of items) {
        if (next.length >= MAX_ITEMS) break;
        if (next.some((x) => x.kind === it.kind && x.a === it.a && (x.b ?? '') === (it.b ?? ''))) continue;
        const id = nextId('I', used); used.push(id);
        next.push({ ...it, id });
      }
      return { ...l, items: next };
    });
    return { ...s, lists };
  });
  const addSample = () => edit((s) => withSample(s, places));
  const removeItem = (id: string) => edit((s) => ({ ...s, lists: s.lists.map((l) => ({ ...l, items: l.items.filter((i) => i.id !== id) })) }));
  const addRule = (r: Omit<WatchRule, 'id' | 'listId' | 'enabled'>) => edit((s) => {
    if (!active || s.rules.length >= MAX_RULES) return s;
    return { ...s, rules: [...s.rules, { ...r, id: nextId('R', s.rules.map((x) => x.id)), listId: active.id, enabled: true }] };
  });
  const toggleRule = (id: string) => edit((s) => ({ ...s, rules: s.rules.map((r) => (r.id === id ? { ...r, enabled: !r.enabled } : r)) }));
  const removeRule = (id: string) => edit((s) => ({ ...s, rules: s.rules.filter((r) => r.id !== id) }));
  const clearTriggered = () => edit((s) => ({ ...s, triggered: [] }));
  const resetAll = () => { clearState(safeStorage); setState(loadState({ get: () => null, set: () => undefined, remove: () => undefined })); setReading(null); setStates({}); setNotice(null); };

  const fromShipments = () => {
    const lanes = new Map<string, Omit<WatchItem, 'id'>>();
    for (const r of loadShipments(safeStorage).rows) {
      if (r.mode !== 'sea') continue;
      const k = `${r.origin}|${r.destination}`;
      if (!lanes.has(k)) lanes.set(k, { kind: 'lane', label: `${r.origin} to ${r.destination}`, a: r.origin, b: r.destination, aName: r.origin, bName: r.destination });
    }
    if (lanes.size === 0) { setNotice([{ chip: 'NO DATA', word: 'No shipments' }]); return; }
    addItems([...lanes.values()]);
    setNotice([{ chip: 'INFO', word: `${Math.min(lanes.size, MAX_ITEMS)} lanes read` }]);
  };

  const exportJson = () => download('watchlists.json', JSON.stringify(exportDoc(state, new Date()), null, 2));
  const onFile = async (f: File | undefined) => {
    if (!f) return;
    if (f.size > MAX_FILE_BYTES) { setNotice([{ chip: 'FLAG', word: 'File too large' }]); return; }
    const text = await f.text().catch(() => '');
    const res = importDoc(text, stateRef.current);
    if (!res.ok) setNotice([{ chip: 'FLAG', word: res.reason === 'wrong_schema' ? 'Not a watchlists file' : res.reason === 'too_large' ? 'File too large' : 'File not read' }]);
    else {
      setState(res.state);
      setNotice([{ chip: 'INFO', word: `${res.counts.lists} lists` }, { chip: 'INFO', word: `${res.counts.rules} rules` }, { chip: 'INFO', word: `${res.counts.triggered} triggered` }]);
    }
    if (fileRef.current) fileRef.current.value = '';
  };

  const activeRules = state.rules.filter((r) => r.listId === active?.id);
  const stamp = reading ? `Read ${fmtStamp(reading.at)}` : busy ? 'READING' : state.lists.length ? 'NOT READ' : 'EMPTY';

  return (
    <Panel id="watchlists" code="WCH" title="Watchlists" updated={stamp} className="wc-panel"
      exportSpec={{ json: () => exportDoc(state, new Date()), csv: state.triggered.length ? () => state.triggered.map((t) => ({ Time: t.at, State: BASIS_WORD[t.basis], Rule: t.rule, Item: t.itemLabel, List: t.listName, Detail: t.detail })) : undefined }}>
      <div className="wc-root">
        <LiveStatus text={busy ? 'Checking' : notice?.map((c) => c.word).join(', ') ?? ''} />
        <div className="wc-controls" role="group" aria-label="Watchlists">
          <span className="wc-bar-title">Lists<b className="wc-n">{state.lists.length}</b></span>
          <ListBar lists={state.lists} active={active} onPick={(id) => edit((s) => ({ ...s, active: id }))} onAdd={addList} onRename={renameList} onRemove={removeList} />
          <span className="wc-spacer" />
          <div className="wc-actions">
            <Button icon="refresh-cw" onClick={() => void run()} disabled={busy || state.lists.length === 0}>Read now</Button>
            <Button icon="download" onClick={exportJson} disabled={state.lists.length === 0}>Export JSON</Button>
            <Button icon="upload" onClick={() => fileRef.current?.click()}>Import JSON</Button>
            <input ref={fileRef} type="file" accept=".json,application/json" className="wc-file" aria-label="Import JSON file" data-testid="wc-file" onChange={(e) => void onFile(e.target.files?.[0])} />
            <IconButton icon="trash-2" label="Clear all" onClick={resetAll} disabled={state.lists.length === 0 && state.triggered.length === 0} />
          </div>
        </div>
        <div className="wc-chips" role="group" aria-label="State">
          <MetaChip>Stored in this browser</MetaChip>
          {snapshotMode && <Chip chip="SNAPSHOT" word="Recorded" />}
          {notice?.map((c, i) => <Chip key={i} chip={c.chip} word={c.word} />)}
          {busy && <Chip chip="INFO" word="Reading" />}
          {reading && reading.transitFailed.length > 0 && <Chip chip="NOT SERVED" word="Added transit" />}
        </div>

        {state.lists.length === 0 || !active ? (
          <>
          <div className="rk-state wc-empty" role="status" data-testid="wc-empty">
            <Chip chip="NO DATA" word="No watchlists" />
            <NewList onAdd={addList} cta="Create watchlist" />
            <Button icon="bell" onClick={addSample}>Sample watchlist</Button>
          </div>
          {/* the three sections the workbench will hold, each with its own state label, so the empty panel reads as a structure */}
          <div className="wc-idle" aria-hidden="true">
            <Section title="Places and legs" count={0} testid="wc-items-idle"><StateLine chip="NO DATA" word="No items" /></Section>
            <Section title="Rules" count={0} testid="wc-rules-idle"><StateLine chip="NO DATA" word="No rules" /></Section>
            <Section title="Triggered" count={0} testid="wc-triggered-idle"><StateLine chip="NO DATA" word="None triggered" /></Section>
          </div>
          </>
        ) : (
          <>
            <Section title="Watchlists" count={active.items.length} testid="wc-items">
              <AddItem places={places} onAdd={addItems} onFromShipments={fromShipments} full={active.items.length >= MAX_ITEMS} />
              <ItemsTable list={active} rules={activeRules} reading={reading} scnLabel={scnLabel} onRemove={removeItem} />
            </Section>
            <Section title="Rules" count={activeRules.length} testid="wc-rules">
              <AddRule library={library} libraryState={scenarios} onAdd={addRule} full={state.rules.length >= MAX_RULES} />
              <RulesTable rules={activeRules} states={states} scnLabel={scnLabel} onToggle={toggleRule} onRemove={removeRule} />
            </Section>
          </>
        )}
        {/* with no list and nothing recorded the empty state above is the whole panel: no second empty box */}
        {(state.lists.length > 0 || state.triggered.length > 0) && <Section title="Triggered" count={state.triggered.length} testid="wc-triggered"
          action={<Button icon="x" onClick={clearTriggered} disabled={state.triggered.length === 0}>Clear triggered</Button>}>
          {state.triggered.length === 0 ? <StateLine chip="NO DATA" word="None triggered" /> : (
            <div className="wc-tablewrap">
              <table className="wc-table" data-testid="wc-triggered-table">
                <thead><tr><th scope="col">Time</th><th scope="col">State</th><th scope="col">Rule</th><th scope="col">Item</th><th scope="col">List</th><th scope="col">Detail</th></tr></thead>
                <tbody>
                  {state.triggered.map((t) => (
                    <tr key={t.id} data-basis={t.basis}>
                      <td className="mono">{fmtStamp(t.at)}</td>
                      <td><Chip chip={BASIS_CHIP[t.basis]} word={BASIS_WORD[t.basis]} /></td>
                      <td>{t.rule}</td><td>{t.itemLabel}</td><td>{t.listName}</td><td className="wc-detail">{t.detail}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Section>}
        <details className="wc-method">
          <summary>Method</summary>
          <ul>
            <li>Rules read values the API serves now.</li>
            <li>An entry is recorded when a condition becomes met.</li>
            <li>Added transit is modelled from named variants.</li>
          </ul>
        </details>
      </div>
    </Panel>
  );
}

function Section({ title, count, testid, action, children }: { title: string; count: number; testid: string; action?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="wc-sec" aria-label={title} data-testid={testid}>
      <div className="wc-sech"><span className="wc-st">{title}</span><b className="wc-n">{count}</b><span className="wc-spacer" />{action}</div>
      {children}
    </section>
  );
}

function NewList({ onAdd, cta }: { onAdd: (name: string) => void; cta: string }) {
  const [name, setName] = useState('');
  const go = () => { if (clean(name)) { onAdd(name); setName(''); } };
  return (
    <span className="wc-inline">
      <input className="wc-input" aria-label="Watchlist name" placeholder="Name" value={name} maxLength={60} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') go(); }} />
      <Button icon="plus" onClick={go} disabled={!clean(name)}>{cta}</Button>
    </span>
  );
}

function ListBar({ lists, active, onPick, onAdd, onRename, onRemove }: {
  lists: WatchList[]; active: WatchList | null; onPick: (id: string) => void; onAdd: (n: string) => void; onRename: (n: string) => void; onRemove: () => void;
}) {
  const [renaming, setRenaming] = useState<string | null>(null);
  const tk = useTabKeys(lists.length, lists.findIndex((x) => x.id === active?.id), (i) => onPick(lists[i].id));
  return (
    <div className="wc-listbar">
      {lists.length > 0 && (
        <div className="wc-tabs" role="tablist" aria-label="Watchlists">
          {lists.map((l, i) => (
            <button key={l.id} type="button" role="tab" {...tk(i)} aria-selected={active?.id === l.id} className={`wc-tab${active?.id === l.id ? ' on' : ''}`} onClick={() => onPick(l.id)}>{l.name}<b className="wc-n">{l.items.length}</b></button>
          ))}
        </div>
      )}
      {active && renaming === null && <IconButton icon="pencil" label="Rename watchlist" size="sm" onClick={() => setRenaming(active.name)} />}
      {active && renaming !== null && (
        <span className="wc-inline">
          <input className="wc-input" aria-label="Rename watchlist" value={renaming} maxLength={60} autoFocus onChange={(e) => setRenaming(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') { onRename(renaming); setRenaming(null); } else if (e.key === 'Escape') setRenaming(null); }} />
          <Button onClick={() => { onRename(renaming); setRenaming(null); }}>Save</Button>
        </span>
      )}
      {active && <IconButton icon="trash-2" label="Delete watchlist" size="sm" onClick={onRemove} />}
      {lists.length > 0 && lists.length < MAX_LISTS && <NewList onAdd={onAdd} cta="New watchlist" />}
    </div>
  );
}

function AddItem({ places, onAdd, onFromShipments, full }: {
  places: ReturnType<typeof placeChoices>; onAdd: (items: Array<Omit<WatchItem, 'id'>>) => void; onFromShipments: () => void; full: boolean;
}) {
  const [kind, setKind] = useState<'place' | 'leg'>('place');
  const [a, setA] = useState<string | null>(null);
  const [b, setB] = useState<string | null>(null);
  const name = (id: string | null) => places.find((p) => p.id === id)?.name ?? id ?? '';
  const ready = kind === 'place' ? !!a : !!a && !!b && a !== b;
  const add = () => {
    if (!ready || !a) return;
    onAdd([kind === 'place' ? { kind, label: name(a), a, aName: name(a) } : { kind, label: `${name(a)} to ${name(b)}`, a, b: b ?? '', aName: name(a), bName: name(b) }]);
    setA(null); setB(null);
  };
  return (
    <div className="wc-form lp-panel" role="group" aria-label="Add to watchlist">
      <div className="wc-seg" role="group" aria-label="Kind">
        <button type="button" className={`wc-segb${kind === 'place' ? ' on' : ''}`} aria-pressed={kind === 'place'} onClick={() => setKind('place')}>Place</button>
        <button type="button" className={`wc-segb${kind === 'leg' ? ' on' : ''}`} aria-pressed={kind === 'leg'} onClick={() => setKind('leg')}>Leg</button>
      </div>
      <PlacePicker label={kind === 'place' ? 'Place' : 'From'} value={a} choices={places} onChange={setA} />
      {kind === 'leg' && <PlacePicker label="To" value={b} choices={places} onChange={setB} />}
      <div className="wc-form-actions">
        <Button icon="plus" onClick={add} disabled={!ready || full}>Add</Button>
        <Button icon="package" onClick={onFromShipments} disabled={full}>Lanes from My shipments</Button>
      </div>
    </div>
  );
}

/** Live is worn only when the cell's feed state is LIVE, its as-of is inside that feed's window and the host
 *  is not serving a captured copy (the shared truth label); anything else reads Recorded or Snapshot. */
function stateChip(r: PlaceResult | undefined, snapshotMode: boolean, at: string | undefined): React.ReactNode {
  if (!r) return <MetaChip>Not read</MetaChip>;
  if (r.kind === 'notserved') return <Chip chip="NOT SERVED" />;
  if (r.kind === 'down') return <Chip chip="DOWN" />;
  const f = r.obs.feed;
  if (f === 'notserved') return <Chip chip="NOT SERVED" />;
  const word = placeStateWord(r.obs, snapshotMode, at);
  return word === 'LIVE' ? <Chip chip="LIVE" /> : <Chip chip="SNAPSHOT" word={word === 'RECORDED' ? 'Recorded' : 'Snapshot'} />;
}
function devChip(r: PlaceResult | undefined): React.ReactNode {
  if (!r || r.kind !== 'ok') return <span className="wc-dim">—</span>;
  const d = r.obs.deviation;
  if (d == null) return <Chip chip="NO DATA" word="No data" />;
  return d === 0 ? <MetaChip>{LEVEL_WORD[0]}</MetaChip> : <Chip chip="FLAG" word={LEVEL_WORD[d]} />;
}

function ItemsTable({ list, rules, reading, scnLabel, onRemove }: {
  list: WatchList; rules: WatchRule[]; reading: Reading | null; scnLabel: (id: string) => string; onRemove: (id: string) => void;
}) {
  if (list.items.length === 0) return <StateLine chip="NO DATA" word="No items" />;
  const transitRules = rules.filter((r) => r.type === 'added_transit' && r.enabled);
  return (
    <div className="wc-tablewrap">
      <table className="wc-table" data-testid="wc-items-table">
        <thead><tr><th scope="col">Item</th><th scope="col">Kind</th><th scope="col">Feed state</th><th scope="col">Vessel activity</th><th scope="col" className="num">Added transit d</th><th scope="col"><span className="wc-sr">Remove</span></th></tr></thead>
        <tbody>
          {list.items.map((i) => {
            const pr = i.kind === 'place' ? reading?.places[i.a] : undefined;
            return (
              <tr key={i.id} data-kind={i.kind}>
                <th scope="row" className="wc-item">{i.label}</th>
                <td>{i.kind === 'place' ? 'Place' : i.kind === 'leg' ? 'Leg' : 'Lane'}</td>
                <td>{i.kind === 'place' ? stateChip(pr, !!reading?.snapshotMode, reading?.at) : <span className="wc-dim">—</span>}</td>
                <td>{i.kind === 'place' ? devChip(pr) : <span className="wc-dim">—</span>}</td>
                <td className="num">
                  {i.kind === 'place' ? <span className="wc-dim">—</span> : transitRules.length === 0 ? <span className="wc-dim">—</span> : transitRules.map((r) => {
                    const o = reading?.transit[transitKey(r, i)];
                    return (
                      <span key={r.id} className="wc-tv" title={scnLabel(r.scenarioId ?? '')}>
                        {o && o.median != null ? <>{fmtD(o.median)} <i>{fmtRange(o)}</i> <Chip chip="MODELLED" /></> : reading ? <Chip chip="NOT SERVED" /> : <MetaChip>Not read</MetaChip>}
                      </span>
                    );
                  })}
                </td>
                <td><IconButton icon="x" label={`Remove ${i.label}`} size="sm" onClick={() => onRemove(i.id)} /></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function AddRule({ library, libraryState, onAdd, full }: {
  library: Scenario[]; libraryState: Scenario[] | null | undefined; onAdd: (r: Omit<WatchRule, 'id' | 'listId' | 'enabled'>) => void; full: boolean;
}) {
  const [type, setType] = useState<RuleType>('feed_state');
  const [level, setLevel] = useState<1 | 2 | 3>(1);
  const [scn, setScn] = useState('');
  const [dur, setDur] = useState(14);
  const [thr, setThr] = useState(3);
  const sid = scn || library[0]?.id || '';
  const needsScenario = type === 'added_transit';
  const ok = !full && (!needsScenario || !!sid);
  const add = () => {
    if (!ok) return;
    if (type === 'feed_state') onAdd({ type });
    else if (type === 'deviation') onAdd({ type, level });
    else onAdd({ type, scenarioId: sid, durationD: Math.min(3650, Math.max(1, Math.round(dur) || 1)), thresholdD: Math.min(3650, Math.max(0, thr || 0)) });
  };
  return (
    <div className="wc-form" role="group" aria-label="Add rule">
      <label className="wc-fl"><span className="wc-lab">Rule</span>
        <select className="wc-input" aria-label="Rule type" value={type} onChange={(e) => setType(e.target.value as RuleType)}>
          {(['feed_state', 'deviation', 'added_transit'] as const).map((t) => <option key={t} value={t}>{RULE_WORD[t]}</option>)}
        </select>
      </label>
      {type === 'deviation' && (
        <label className="wc-fl"><span className="wc-lab">At or above</span>
          <select className="wc-input" aria-label="Deviation level" value={level} onChange={(e) => setLevel(Number(e.target.value) as 1 | 2 | 3)}>
            {[1, 2, 3].map((l) => <option key={l} value={l}>{LEVEL_WORD[l]}</option>)}
          </select>
        </label>
      )}
      {needsScenario && (
        <>
          <label className="wc-fl wc-fl--wide"><span className="wc-lab">Scenario</span>
            <select className="wc-input" aria-label="Scenario" value={sid} onChange={(e) => setScn(e.target.value)} disabled={library.length === 0}>
              {library.length === 0 && <option value="">{libraryState === undefined ? 'Loading' : 'Not served'}</option>}
              {library.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
            </select>
          </label>
          <label className="wc-fl wc-fl--num"><span className="wc-lab">Duration</span>
            <span className="wc-unit"><input className="wc-input" type="number" aria-label="Duration" min={1} max={3650} value={dur} onChange={(e) => setDur(Number(e.target.value))} /><i>d</i></span>
          </label>
          <label className="wc-fl wc-fl--num"><span className="wc-lab">Above</span>
            <span className="wc-unit"><input className="wc-input" type="number" aria-label="Threshold" min={0} max={3650} step={0.5} value={thr} onChange={(e) => setThr(Number(e.target.value))} /><i>d</i></span>
          </label>
        </>
      )}
      <div className="wc-form-actions"><Button icon="plus" onClick={add} disabled={!ok}>Add rule</Button>{needsScenario && <Chip chip="MODELLED" />}</div>
    </div>
  );
}

const STATUS_VIEW: Record<RuleState['status'], { chip: string; word: string }> = {
  met: { chip: 'FLAG', word: 'Met' }, not_met: { chip: 'NOT MET', word: 'Not met' }, not_evaluated: { chip: 'NO DATA', word: 'Not read' },
};
function RulesTable({ rules, states, scnLabel, onToggle, onRemove }: {
  rules: WatchRule[]; states: Record<string, RuleState>; scnLabel: (id: string) => string; onToggle: (id: string) => void; onRemove: (id: string) => void;
}) {
  if (rules.length === 0) return <StateLine chip="NO DATA" word="No rules" />;
  return (
    <div className="wc-tablewrap">
      <table className="wc-table" data-testid="wc-rules-table">
        <thead><tr><th scope="col">On</th><th scope="col">Rule</th><th scope="col" className="num">Items</th><th scope="col">State</th><th scope="col"><span className="wc-sr">Remove</span></th></tr></thead>
        <tbody>
          {rules.map((r) => {
            const s = states[r.id];
            const v = !r.enabled ? { chip: 'OFF', word: 'Off' } : STATUS_VIEW[s?.status ?? 'not_evaluated'];
            return (
              <tr key={r.id} data-rule={r.type} data-state={r.enabled ? s?.status ?? 'not_evaluated' : 'off'}>
                <td><input type="checkbox" checked={r.enabled} aria-label={`${ruleName(r, scnLabel)} on`} onChange={() => onToggle(r.id)} /></td>
                <th scope="row" className="wc-item">{ruleName(r, scnLabel)}{r.type === 'added_transit' && <> <Chip chip="MODELLED" /></>}</th>
                <td className="num">{s ? `${s.met} of ${s.items}` : '—'}</td>
                <td><Chip chip={v.chip} word={v.word} /></td>
                <td><IconButton icon="x" label={`Remove ${ruleName(r, scnLabel)}`} size="sm" onClick={() => onRemove(r.id)} /></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

