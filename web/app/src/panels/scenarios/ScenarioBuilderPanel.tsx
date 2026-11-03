import React, { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { ApiError } from '../../api';
import { useAgent, getAgent } from '../../agent/singleton';
import { Panel } from '../../components/Panel';
import { focusPlaceOnMap } from '../../mapFlyTo';
import { safeStorage, useStore } from '../../store';
import { Status } from '../../ui/Status';
import { wm } from '../../windows/controller';
import { placeChoices, type PlaceChoice } from '../laneplanner/logic';
import { handoffSnapshot, hasHandoff, proposeArrangement, sendHandoff, subscribeHandoffs } from './bridge';
import { scenarioApi, probeScenarios } from './client';
import { useScenarioReport } from './exportReport';
import {
  addShock, addShocksForPlaces, bulkUpdate, duplicateShock, fromWire, moveRow, removeShock, templateFor, toWire, updateShock,
  type ShockPatch, type ShockTemplate,
} from './doc';
import { refusalText, stampFor, targetsFor } from './logic';
import { runArrange } from './arrange';
import { RunSection, compareCombined, type CmpRow, type RunState } from './run';
import { ArrangeSection, PlacesSection, SavedSection, Section, ShocksSection, TimelineBar } from './sections';
import { forgetToken, loadTokens, rememberToken } from './tokens';
import { Timeline } from './Timeline';
import { DEFAULT_PX_PER_DAY, DEFAULT_SNAP, fitPxPerDay, overlapSegments, spanOf, viewDays, zoomIn, zoomOut, type Snap } from './timeline';
import { getBuilder } from './store';
import type { BuilderDoc, ChainRunDoc, OrganiseRule, RulesDoc, SavedRow, ShockVerdictRow, ValidateDoc } from './types';
import './scenarios.css';

/* SCENARIO BUILDER (SCB). Pick one or many places, give each a shock (kind,
 * start, duration, capacity loss), keep adding shocks to the SAME scenario,
 * arrange them on a timeline by dragging or typing, and run the scenario on the
 * causal chain (one exact run per shock, with a labelled combined table) or on
 * the simulator (one run, every shock a window). Meta AI can arrange the
 * timeline through one structured rule; the server applies it and the change
 * shows as a diff to accept or discard. */

type ValState = { state: 'idle' | 'checking' | 'ok' | 'error' | 'notserved'; doc: ValidateDoc | null; line: string | null };

function useValidation(doc: BuilderDoc, rev: 1 | 2, enabled: boolean): ValState {
  const [v, setV] = useState<ValState>({ state: 'idle', doc: null, line: null });
  useEffect(() => {
    if (!enabled) return;
    if (doc.shocks.length === 0) { setV({ state: 'idle', doc: null, line: null }); return; }
    const ctrl = new AbortController();
    setV((p) => ({ ...p, state: 'checking' }));
    const t = setTimeout(() => {
      scenarioApi.validate(doc, rev, ctrl.signal)
        .then((d) => { if (!ctrl.signal.aborted) setV({ state: 'ok', doc: d, line: null }); })
        .catch((e) => { if (!ctrl.signal.aborted && !(e instanceof ApiError && e.code === 'aborted')) setV({ state: 'error', doc: null, line: errorLine(e) }); });
    }, 250);
    return () => { clearTimeout(t); ctrl.abort(); };
  }, [doc, rev, enabled]);
  return v;
}

/** One factual line for a refusal: a title and a sentence, no ids, no raw text. */
export function errorLine(e: unknown): string {
  const x = e as { code?: string; message?: string };
  const code = typeof x?.code === 'string' ? x.code : '';
  if (code === 'network') return 'Service unreachable. The request was not sent.';
  if (code === 'timeout') return 'No answer. The request timed out.';
  if (code === 'scenario_refused') return 'Run refused. The engine rejected a shock.';
  if (code === 'rate_limited') return 'Request refused. The call limit is reached.';
  if (code === 'unknown_scenario') return 'Request refused. The saved scenario is not found.';
  return 'Request refused. The scenario is not accepted.';
}

export function ScenarioBuilderPanel() {
  const st = useStore();
  const b = getBuilder();
  const bs = useSyncExternalStore(b.subscribe, b.getState, b.getState);
  const agent = useAgent();
  useSyncExternalStore(subscribeHandoffs, handoffSnapshot, handoffSnapshot);
  const doc = bs.doc;

  const choices = useMemo(() => placeChoices(st.graph.nodes), [st.graph.nodes]);
  const byId = useMemo(() => new Map<string, PlaceChoice>(choices.map((c) => [c.id, c])), [choices]);
  const nameOf = useCallback((id: string) => byId.get(id)?.name ?? id, [byId]);

  const [probe, setProbe] = useState<'checking' | 'served' | 'notserved' | 'down'>('checking');
  const [rules, setRules] = useState<RulesDoc | null>(null);
  const [rev, setRev] = useState<1 | 2>(1);
  const served = probe === 'served';
  useEffect(() => {
    let live = true;
    void probeScenarios().then((p) => { if (live) setProbe(p); });
    return () => { live = false; };
  }, []);
  useEffect(() => {
    if (!served) return;
    const c = new AbortController();
    scenarioApi.rules(c.signal).then(setRules).catch(() => setRules(null));
    return () => c.abort();
  }, [served]);
  const val = useValidation(doc, rev, served);
  const verdicts = useMemo(() => new Map<string, ShockVerdictRow>((val.doc?.shocks ?? []).map((r) => [r.id, r])), [val.doc]);

  /* ── places ─────────────────────────────────────────────────────── */
  const [follow, setFollow] = useState(false);
  const [template, setTemplate] = useState<ShockTemplate>(templateFor('closure'));
  const mapSel = st.selectedNode;
  const addPlace = useCallback((id: string) => { b.addPlace(id); b.noteRecent([id]); }, [b]);
  useEffect(() => { if (follow && mapSel && byId.has(mapSel)) addPlace(mapSel); }, [follow, mapSel, byId, addPlace]);
  const room = Math.max(0, 12 - doc.shocks.length);

  const defaultPlace = useCallback((): string | null => {
    const cand = [bs.places[bs.places.length - 1], mapSel && byId.has(mapSel) ? mapSel : null, bs.recents[0]].find((x) => x && byId.has(x));
    if (cand) return cand;
    return choices.find((c) => c.kind === 'chokepoint' && c.tier === 'core')?.id ?? choices[0]?.id ?? null;
  }, [bs.places, bs.recents, mapSel, byId, choices]);

  /* ── edits ─────────────────────────────────────────────────────── */
  const onAddShock = () => {
    const p = defaultPlace();
    if (!p) return;
    b.commit(addShock(doc, p, template));
    b.noteRecent([p]);
  };
  const onAddShocks = () => {
    if (bs.places.length === 0) return;
    b.commit(addShocksForPlaces(doc, bs.places, template));
    b.noteRecent(bs.places);
  };
  const [focusId, setFocusId] = useState<string | null>(null);
  const patch = (id: string, p: ShockPatch, key: string) => b.commit(updateShock(doc, id, p), key);
  const [axis, setAxis] = useState<'days' | 'dates'>('days');
  const [snap, setSnap] = useState<Snap>(DEFAULT_SNAP);
  const [px, setPx] = useState(DEFAULT_PX_PER_DAY);
  const tlWrap = useRef<HTMLDivElement>(null);
  const fit = () => {
    const w = (tlWrap.current?.clientWidth ?? 640) - 156 - 24;
    setPx(fitPxPerDay(viewDays(doc.shocks, 30), w));
  };
  const span = spanOf(doc.shocks);
  const overlaps = overlapSegments(doc.shocks);
  const placeIds = useMemo(() => [...new Set([...bs.places, ...doc.shocks.map((s) => s.placeId)])], [bs.places, doc.shocks]);
  const refusedAll = useMemo(() => new Set((val.doc?.shocks ?? []).filter((r) => !r.accepted).map((r) => r.id)), [val.doc]);

  const onKeyDown = (e: React.KeyboardEvent) => {
    const t = e.target as HTMLElement;
    if (/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || t.isContentEditable) return;
    if (!(e.ctrlKey || e.metaKey)) return;
    const k = e.key.toLowerCase();
    if (k === 'z' && !e.shiftKey) { e.preventDefault(); b.undo(); } else if ((k === 'z' && e.shiftKey) || k === 'y') { e.preventDefault(); b.redo(); }
  };

  /* ── arrange ───────────────────────────────────────────────────── */
  const [askStatus, setAskStatus] = useState('');
  const [askReason, setAskReason] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);
  const [applied, setApplied] = useState(false);
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false; }, []);
  const agentBusy = agent.state.busy || ['running', 'awaiting_confirmation', 'awaiting_client'].includes(agent.state.status);
  useEffect(() => { if (!agent.meta.loaded) void getAgent().loadMeta(); }, [agent.meta.loaded]);
  const agentOff = agent.meta.loaded && (agent.meta.error !== null || agent.meta.status?.enabled === false);
  const mode = agent.meta.status?.transport_label ? (agent.meta.status.transport_label === 'LIVE' ? 'Live' : 'Recorded') as 'Live' | 'Recorded' : null;
  const say = (status: string, reason: string | null = null) => { setAskStatus(status); setAskReason(reason); };
  const onAsk = async (text: string) => {
    if (doc.shocks.length === 0) { say('Add shocks first.'); return; }
    setApplied(false);
    say('Working');
    setAsking(true);
    const out = await runArrange(getAgent(), b, text);
    if (!alive.current) return;
    setAsking(false);
    say(out.status, out.reason);
  };
  const onRule = async (r: { kind: 'stagger' | 'cluster' | 'spread_evenly' | 'align_start'; value?: number }) => {
    const rule: OrganiseRule = r.kind === 'stagger' ? { kind: 'stagger', gap_d: r.value ?? 0 }
      : r.kind === 'cluster' ? { kind: 'cluster', within_d: r.value ?? 7 }
      : r.kind === 'spread_evenly' ? { kind: 'spread_evenly', window_d: r.value ?? 30 } : { kind: 'align_start', day: 0 };
    const res = await proposeArrangement(rule, b, 'rule', null);
    setApplied(false);
    if (res.ok && !b.getState().pending) say('No change proposed.', 'Starts already match the rule');
    else say(res.ok ? `${res.summary}. Accept or discard it below.` : `Request refused. ${res.summary}`);
  };

  /* ── run ───────────────────────────────────────────────────────── */
  const [run, setRun] = useState<RunState>({ kind: 'idle' });
  const [cmp, setCmp] = useState<{ name: string; rows: CmpRow[] } | null>(null);
  const okFor = val.doc && val.state !== 'checking' ? targetsFor(val.doc.accepted, val.doc.runnable) : [];
  const can = { chain: okFor.includes('chain'), sim: okFor.includes('sim') };
  const why = useMemo(() => {
    if (!val.doc || doc.shocks.length === 0) return [];
    const out: string[] = [];
    for (const t of ['chain', 'sim'] as const) {
      if (val.doc.accepted[t]) continue;
      const r = val.doc.shocks.find((x) => !x.verdicts[t].ok);
      if (r) out.push(`${t === 'chain' ? 'Chain' : 'Simulation'}: ${refusalText(r.verdicts[t], rules, t)}`);
    }
    return out;
  }, [val.doc, rules, doc.shocks.length]);
  useScenarioReport({ doc, nameOf, validation: val.doc, rules, run: run.kind === 'chain' || run.kind === 'sim' ? run.doc : null, engineRev: rev });
  const noteScenarioPlaces = () => b.noteRecent(doc.shocks.map((s) => s.placeId));
  const onRun = async (target: 'chain' | 'sim') => {
    setRun({ kind: 'running', target });
    setCmp(null);
    try {
      const out = await scenarioApi.run(doc, { target, engineRev: rev });
      noteScenarioPlaces();
      if (out.target === 'chain') setRun({ kind: 'chain', doc: out });
      else { setRun({ kind: 'sim', doc: out, status: out.status === 'ready' ? 'ready' : 'building' }); st.followSimBuild(out.sim_run_id); }
    } catch (e) { setRun({ kind: 'error', line: errorLine(e) }); }
  };
  const simId = run.kind === 'sim' && run.status === 'building' ? run.doc.sim_run_id : null;
  useEffect(() => {
    if (!simId) return;
    let live = true;
    let fails = 0;
    const tick = async () => {
      try {
        const r = await scenarioApi.simStatus(simId);
        if (!live) return;
        fails = 0;
        if (r.status === 'ready') setRun((p) => (p.kind === 'sim' && p.doc.sim_run_id === simId ? { ...p, status: 'ready' } : p));
        else if (r.status === 'failed') setRun((p) => (p.kind === 'sim' && p.doc.sim_run_id === simId ? { ...p, status: 'failed' } : p));
      } catch { fails += 1; if (live && fails >= 5) setRun((p) => (p.kind === 'sim' && p.doc.sim_run_id === simId ? { ...p, status: 'failed' } : p)); }
    };
    const id = window.setInterval(() => void tick(), 2000);
    return () => { live = false; window.clearInterval(id); };
  }, [simId]);

  const openWin = (id: 'chain' | 'sim') => { try { if (!wm.isOpen(id)) wm.openWindow(id, 'dock'); } catch { /* no window manager: the panel stays */ } };
  const onOpenRun = async (runId: string) => { const ok = await st.openChain(runId); if (ok) openWin('chain'); };
  const onFocusPlace = (id: string) => { st.restoreMap(); st.selectNode(id); focusPlaceOnMap(id); };
  const onOpenSim = async (id: string) => { await st.openSimRun(id); openWin('sim'); };
  const onHandoff = (kind: 'monte-carlo' | 'shipments') => {
    const w = toWire(doc);
    sendHandoff(kind, { scenarioId: null, name: w.name, startAt: w.start_at, shocks: w.shocks, engineRev: rev });
  };

  /* ── saved ─────────────────────────────────────────────────────── */
  const [saved, setSaved] = useState<SavedRow[] | null>(null);
  const [savedState, setSavedState] = useState<'idle' | 'loading' | 'error'>('idle');
  const [saving, setSaving] = useState(false);
  const [saveNote, setSaveNote] = useState<string | null>(null);
  const [comparing, setComparing] = useState<string | null>(null);
  const [tokens, setTokens] = useState<Record<string, string>>(() => loadTokens(safeStorage));
  const refreshSaved = useCallback(() => {
    setSavedState('loading');
    scenarioApi.saved().then((r) => { setSaved(r.scenarios); setSavedState('idle'); }).catch(() => setSavedState('error'));
  }, []);
  useEffect(() => { if (served) refreshSaved(); }, [served, refreshSaved]);
  const onSave = async () => {
    setSaving(true);
    try {
      const r = await scenarioApi.save(doc);
      setSaveNote(r.created ? 'Saved' : 'Already saved');
      if (r.created && r.owner_token) { rememberToken(safeStorage, r.id, r.owner_token); setTokens(loadTokens(safeStorage)); }
      refreshSaved();
    } catch (e) { setSaveNote(null); setRun({ kind: 'error', line: errorLine(e) }); } finally { setSaving(false); }
  };
  const onLoad = async (id: string) => {
    try {
      const r = await scenarioApi.get(id);
      const d = fromWire(r.scenario);
      b.load(d);
      b.setPlaces([...new Set(d.shocks.map((s) => s.placeId))]);
      setRun({ kind: 'idle' });
      setCmp(null);
    } catch (e) { setRun({ kind: 'error', line: errorLine(e) }); }
  };
  const onDelete = async (id: string) => {
    const token = tokens[id];
    if (!token) return;
    try { await scenarioApi.remove(id, token); forgetToken(safeStorage, id); setTokens(loadTokens(safeStorage)); }
    catch (e) { setRun({ kind: 'error', line: errorLine(e) }); }
    finally { refreshSaved(); }
  };
  const onCompare = async (id: string) => {
    setComparing(id);
    try {
      const [a, c] = await Promise.all([scenarioApi.run(doc, { target: 'chain', engineRev: rev }), scenarioApi.runSaved(id, { target: 'chain', engineRev: rev })]);
      setCmp({ name: saved?.find((r) => r.id === id)?.name ?? '', rows: compareCombined(a as ChainRunDoc, c as ChainRunDoc) });
    } catch (e) { setRun({ kind: 'error', line: errorLine(e) }); } finally { setComparing(null); }
  };

  const stamp = stampFor({ shocks: doc.shocks.length, span: span.totalSpanD, state: probe === 'notserved' || probe === 'down' ? 'notserved' : val.state });

  return (
    <Panel id="scenariobuilder" code="SCB" title="Scenario builder" updated={stamp} className="scb-panel">
      <div className="scb-root" onKeyDown={onKeyDown} onBlurCapture={() => b.endGesture()} data-scb-state={val.state}>
        {(probe === 'notserved' || probe === 'down') && <div className="scb-state" role="status"><Status state={probe === 'down' ? 'down' : 'notserved'} meta="/scenarios" /></div>}
        <div className="scb-stamps"><Status state="model" /><Status state="sim" /></div>
        <PlacesSection choices={choices} nameOf={nameOf} places={bs.places} recents={bs.recents} mapSelected={mapSel} follow={follow} onFollow={setFollow}
          onAdd={addPlace} onRemove={(id) => b.removePlace(id)} onClear={() => b.setPlaces([])} template={template} onTemplate={setTemplate}
          onAddShocks={onAddShocks} room={room} />
        <ShocksSection shocks={doc.shocks} selected={bs.selected} focusId={focusId} nameOf={nameOf} placeIds={placeIds} verdicts={verdicts} rules={rules}
          checking={val.state === 'checking'} canUndo={bs.canUndo} canRedo={bs.canRedo} onAdd={onAddShock} onUndo={() => b.undo()} onRedo={() => b.redo()}
          onToggle={(id) => b.toggleSelected(id)} onToggleAll={() => b.setSelected(bs.selected.length === doc.shocks.length ? [] : doc.shocks.map((s) => s.id))}
          onFocus={setFocusId} onPatch={patch} onRemove={(id) => b.commit(removeShock(doc, id))} onDuplicate={(id) => b.commit(duplicateShock(doc, id))}
          onMove={(id, dir) => b.commit(moveRow(doc, id, dir))} onBulk={(p) => { b.commit(bulkUpdate(doc, bs.selected, p)); b.endGesture(); }} />
        {val.state === 'error' && <div className="scb-err" role="alert">{val.line}</div>}
        <Section title="Timeline" id="timeline">
          <TimelineBar axis={axis} onAxis={setAxis} snap={snap} onSnap={setSnap} onZoomIn={() => setPx(zoomIn(px))} onZoomOut={() => setPx(zoomOut(px))} onFit={fit}
            startDate={doc.startDate} onStartDate={(d) => b.commit({ ...doc, startDate: d }, 'start-date')} span={span.totalSpanD} overlaps={overlaps.length}
            sameSite={overlaps.filter((o) => o.samePlace).length} lastEnd={span.lastEndD} empty={doc.shocks.length === 0} />
          <div ref={tlWrap}>
            <Timeline shocks={doc.shocks} names={Object.fromEntries(placeIds.map((id) => [id, nameOf(id)]))} pending={bs.pending ? bs.pending.starts : null}
              selectedId={focusId} refused={refusedAll} snap={snap} axis={axis} startDate={doc.startDate} pxPerDay={px}
              onSelect={setFocusId} onEdit={(id, p, key) => b.commit(updateShock(doc, id, { ...(p.startD !== undefined ? { startD: p.startD } : {}), ...(p.durationD !== undefined ? { durationD: p.durationD } : {}) }), key)}
              onGestureEnd={() => b.endGesture()} />
          </div>
        </Section>
        <ArrangeSection busy={asking || agentBusy} mode={mode} status={askStatus} disabled={doc.shocks.length === 0} aiOff={agentOff} onAsk={(t) => void onAsk(t)} onRule={(r) => void onRule(r)}
          pending={bs.pending} nameOf={(sid) => nameOf(doc.shocks.find((s) => s.id === sid)?.placeId ?? sid)}
          reason={askReason} applied={applied && bs.canUndo} onUndo={() => { b.undo(); setApplied(false); say(''); }}
          onAccept={() => { if (b.acceptPending()) { setApplied(true); say('Arrangement applied.'); } }} onDiscard={() => { b.discardPending(); say(''); }} />
        <RunSection state={run} rules={rules} nameOf={nameOf} can={can} why={why} busy={run.kind === 'running'} rev={rev} onRev={setRev} revs={rules?.engine_revs ?? [1]}
          onRun={(t) => void onRun(t)} onOpenRun={(id) => void onOpenRun(id)} onFocusPlace={onFocusPlace} onOpenSim={(id) => void onOpenSim(id)}
          handoffs={{ monteCarlo: hasHandoff('monte-carlo'), shipments: hasHandoff('shipments') }} onHandoff={onHandoff} cmp={cmp} onCloseCmp={() => setCmp(null)} />
        <SavedSection name={doc.name} onName={(n) => b.commit({ ...doc, name: n.slice(0, 60) }, 'name')} onSave={() => void onSave()} saving={saving} canSave={val.doc?.runnable === true}
          saveNote={saveNote} saved={saved} savedState={savedState} onLoad={(id) => void onLoad(id)} onDelete={(id) => void onDelete(id)} owned={tokens} onCompare={(id) => void onCompare(id)} comparing={comparing} />
      </div>
    </Panel>
  );
}
