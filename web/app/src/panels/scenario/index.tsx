import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../../api';
import { Chip } from '../../shell/Chip';
import { useStore } from '../../store';
import type { Chain } from '../../types';
import './scenario.css';
import { routeCapability, scn } from './client';
import { ScnCtx, TAB_LABEL, TAB_TITLE, type BuildTrack, type CmpPair, type Loaded, type LoopCtx, type TabId } from './ctx';
import { CompareTab } from './CompareTab';
import { JournalTab } from './JournalTab';
import { RunsTab } from './RunsTab';
import { RunTab } from './RunTab';
import { ShockTab } from './ShockTab';
import { VerifyTab } from './VerifyTab';
import { engineLabel, errorLine, mapRefusal, placeNamer, placeOptions, shortId, type BookRow, type Capability, type Refusal } from './logic';
import type { CatalogueRun, ChainJournal, EngineRev, Loop, ShockRules, SimJournal } from './types';
import { RefusalLine, Seg, Tag } from './ui';
import { Icon } from '../../ui/Icon';
import { BriefButton } from '../../brief/hooks';

export type { TabId } from './ctx';

const TAB_KEY = 'supermap.scn.tab';
const ALL_TABS: TabId[] = ['RUN', 'SHOCK', 'JRNL', 'RUNS', 'CMP', 'VERIFY'];

function readTab(): TabId {
  try { const v = window.localStorage.getItem(TAB_KEY); if (v && (ALL_TABS as string[]).includes(v)) return v as TabId; } catch { /* storage blocked */ }
  return 'RUN';
}

type Probe = Capability | 'checking';
interface Caps { scenarios: Probe; rules: Probe; journal: Probe; compare: Probe; verify: Probe; sim: Probe }
const CHECKING: Caps = { scenarios: 'checking', rules: 'checking', journal: 'checking', compare: 'checking', verify: 'checking', sim: 'checking' };

/** GET-probe every route the panel can use, once. A route that 404s hides
 *  its tab; a feed that errors is DOWN and hides it too. */
function useCaps(): Caps {
  const [caps, setCaps] = useState<Caps>(CHECKING);
  useEffect(() => {
    let live = true;
    void Promise.all([
      routeCapability('/scenarios', '/scenarios'), routeCapability('/shocks/rules', '/shocks/rules'),
      routeCapability('/run/{run_id}/journal', '/run/probe/journal'), routeCapability('/compare/{a}/{b}', '/compare/probe/probe'),
      routeCapability('/run/{run_id}/verify', '/run/probe/verify'), routeCapability('/sim/runs/lineage', '/sim/runs/lineage'),
    ]).then(([scenarios, rules, journal, compare, verify, sim]) => { if (live) setCaps({ scenarios, rules, journal, compare, verify, sim }); });
    return () => { live = false; };
  }, []);
  return caps;
}

/** One fetched document keyed by run id (and a refresh nonce). */
function useLoaded<T>(id: string | null, enabled: boolean, load: (id: string) => Promise<T>, nonce: number): Loaded<T> {
  const [st, setSt] = useState<Loaded<T>>({ id: null, status: 'idle', doc: null, error: null });
  const loadRef = useRef(load); loadRef.current = load;
  useEffect(() => {
    if (!id || !enabled) { setSt({ id: null, status: 'idle', doc: null, error: null }); return; }
    let live = true;
    setSt((p) => ({ id, status: 'loading', doc: p.id === id ? p.doc : null, error: null }));
    loadRef.current(id).then((doc) => { if (live) setSt({ id, status: 'ready', doc, error: null }); })
      .catch((e) => { if (live) setSt({ id, status: 'error', doc: null, error: errorLine(e) }); });
    return () => { live = false; };
  }, [id, enabled, nonce]);
  return st;
}

export function ScenarioLoopPanel({ onClose }: { onClose: () => void }) {
  const s = useStore();
  const caps = useCaps();
  const [tab, setTabState] = useState<TabId>(readTab);
  const [loopPref, setLoopPref] = useState<Loop | null>(null);
  const [rev, setRev] = useState<EngineRev>(2);
  const [rules, setRules] = useState<ShockRules | null>(null);
  const [session, setSession] = useState<BookRow[]>([]);
  const [build, setBuild] = useState<BuildTrack | null>(null);
  const [cmp, setCmp] = useState<CmpPair>({ a: null, b: null });
  const [nonce, setNonce] = useState(0);
  const [openErr, setOpenErr] = useState<Refusal | null>(null);

  const setTab = useCallback((t: TabId) => {
    setTabState(t);
    try { window.localStorage.setItem(TAB_KEY, t); } catch { /* storage blocked */ }
  }, []);

  /* ── places and rules ───────────────────────────────────────────────── */
  const places = useMemo(() => placeOptions(s.graph), [s.graph]);
  const nameOf = useMemo(() => placeNamer(places), [places]);
  useEffect(() => {
    if (caps.rules !== 'served') return;
    let live = true;
    scn.rules().then((r) => { if (live) setRules(r); }).catch(() => { if (live) setRules(null); });
    return () => { live = false; };
  }, [caps.rules]);

  /* ── the loaded subject ─────────────────────────────────────────────── */
  const simServed = caps.sim === 'served';
  const chainId = s.chain?.run_id ?? null;
  const simId = s.simActive ? s.simRun?.sim_run_id ?? null : null;
  const loop: Loop = loopPref === 'sim' && simServed ? 'sim' : loopPref === 'chain' ? 'chain' : simServed && simId ? 'sim' : 'chain';
  const subjectId = loop === 'sim' ? simId : chainId;
  const chainJournal = useLoaded<ChainJournal>(chainId, caps.journal === 'served', scn.journal, nonce);
  const simJournal = useLoaded<SimJournal>(simId, simServed && loop === 'sim', scn.simJournal, nonce);
  const refreshJournal = useCallback(() => setNonce((n) => n + 1), []);

  /* ── the run catalogue ──────────────────────────────────────────────── */
  const [cat, setCat] = useState<{ runs: CatalogueRun[] | null; status: 'idle' | 'loading' | 'error'; error: string | null }>({ runs: null, status: 'idle', error: null });
  const refreshCatalogue = useCallback(() => {
    setCat((p) => ({ ...p, status: 'loading', error: null }));
    scn.runs().then((runs) => setCat({ runs, status: 'idle', error: null })).catch((e) => setCat((p) => ({ ...p, status: 'error', error: errorLine(e) })));
  }, []);
  const catSeeded = useRef(false);
  useEffect(() => {
    if (catSeeded.current) return;
    if (s.runsCatalogue) { catSeeded.current = true; setCat({ runs: s.runsCatalogue as CatalogueRun[], status: 'idle', error: null }); }
  }, [s.runsCatalogue]);
  useEffect(() => {
    const id = window.setTimeout(() => { if (!catSeeded.current) { catSeeded.current = true; refreshCatalogue(); } }, 1200);
    return () => window.clearTimeout(id);
  }, [refreshCatalogue]);

  /* ── opening runs ───────────────────────────────────────────────────── */
  const openChainRun = useCallback(async (id: string): Promise<Chain | null> => {
    setOpenErr(null);
    try {
      const ch = await api.chain(id);
      s.openVariantChain(ch, `Scenario loop · ${nameOf(ch.events[0]?.node_id)}`);
      setLoopPref('chain');
      return ch;
    } catch (e) {
      const x = e as { code?: string; message?: string };
      setOpenErr(mapRefusal(x.code ?? 'error', x.message ?? String(e)));
      return null;
    }
  }, [s, nameOf]);
  const openSimRunId = useCallback(async (id: string) => { setOpenErr(null); setLoopPref('sim'); await s.openSimRun(id); }, [s]);

  /* ── sim fork builds: honest BUILDING, then open ───────────────────── */
  const beginBuild = useCallback((b: BuildTrack) => { setBuild(b); s.followSimBuild(b.simRunId); }, [s]);
  const dismissBuild = useCallback(() => setBuild(null), []);
  const buildId = build?.state === 'building' ? build.simRunId : null;
  const buildParent = build?.parentId ?? null;
  const sRef = useRef(s); sRef.current = s;
  useEffect(() => {
    if (!buildId) return;
    let live = true;
    let failures = 0;
    let inflight = false; // a slow status call never stacks a second one
    const tick = async () => {
      if (inflight) return;
      inflight = true;
      try {
        const st = await api.simRunStatus(buildId);
        if (!live) return;
        failures = 0;
        if (st.status === 'ready') {
          setBuild((b) => (b && b.simRunId === buildId ? { ...b, state: 'ready' } : b));
          setCmp({ a: buildParent, b: buildId });
          void sRef.current.openSimRun(buildId);
          refreshCatalogue();
          setNonce((n) => n + 1);
        } else if (st.status === 'failed') {
          setBuild((b) => (b && b.simRunId === buildId ? { ...b, state: 'failed', error: `[${st.error.code}] ${st.error.message}` } : b));
        }
      } catch (e) {
        if (!live) return;
        failures += 1;
        if (failures >= 5) setBuild((b) => (b && b.simRunId === buildId ? { ...b, state: 'failed', error: errorLine(e) } : b));
      } finally { inflight = false; }
    };
    const id = window.setInterval(() => void tick(), 2000);
    return () => { live = false; window.clearInterval(id); };
  }, [buildId, buildParent, refreshCatalogue]);

  /* ── tabs: a tab whose route is not served is hidden ───────────────── */
  const served = (p: Probe) => p === 'served';
  const visible: TabId[] = useMemo(() => {
    const out: TabId[] = [];
    if (served(caps.scenarios)) out.push('RUN');
    if (served(caps.rules)) out.push('SHOCK');
    if (loop === 'chain' ? served(caps.journal) : simServed) out.push('JRNL');
    out.push('RUNS');
    if (loop === 'chain' ? served(caps.compare) : simServed) out.push('CMP');
    if (loop === 'chain' ? served(caps.verify) : simServed) out.push('VERIFY');
    return out;
  }, [caps, loop, simServed]);
  const active: TabId = visible.includes(tab) ? tab : visible[0];

  const ctx: LoopCtx = {
    loop, setLoop: setLoopPref, simServed, go: setTab, places, nameOf, rules, rev, setRev,
    chain: s.chain, chainId, simId, subjectId, chainJournal, simJournal, refreshJournal,
    catalogue: cat, refreshCatalogue, session, addSession: (r) => setSession((p) => [...p, r]),
    build, beginBuild, dismissBuild, cmp, setCmp, openChainRun, openSimRunId, noteRunsChanged: refreshCatalogue,
  };

  const loadedRev = chainJournal.doc?.engine_rev;
  const checking = Object.values(caps).includes('checking');
  const anyServed = visible.length > 1 || (visible.length === 1 && visible[0] !== 'RUNS');

  const onTabKey = (e: React.KeyboardEvent, i: number) => {
    if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
    e.preventDefault();
    const next = visible[(i + (e.key === 'ArrowRight' ? 1 : -1) + visible.length) % visible.length];
    setTab(next);
    window.requestAnimationFrame(() => document.getElementById(`scn-tab-${next}`)?.focus());
  };

  return (
    <section className="panel tool-slot scn-panel" data-panel="tool-scenarios" aria-label="Scenario loop">
      <header className="panel-head">
        <span className="code-chip">SCN</span>
        <span className="panel-title">Scenario loop</span>
        <span className="panel-updated mono">
          {checking ? <Tag tone="busy" word="Checking" /> : <Chip kind="meta" word="Local" title="Stored here" />}
        </span>
        <span className="panel-controls"><button type="button" className="icon-btn" title="Close" aria-label="Close scenario loop" onClick={onClose}><Icon name="close" size="sm" /></button></span>
      </header>
      <div className="scn-stamps">
        <Chip kind="sim" />
        <Chip kind="model" />
        {loop === 'chain' && <Chip kind="meta" word={engineLabel(chainId && loadedRev ? loadedRev : rev)} />}
        {simServed && (
          <Seg<Loop> label="Loop" value={loop} onChange={setLoopPref}
            options={[{ v: 'chain', label: 'Chain' }, { v: 'sim', label: 'Simulation' }]} />
        )}
        <span className="mono scn-subject" data-testid="subject">{subjectId ? shortId(subjectId) : 'No run'}</span>
        {loop === 'chain' && chainId && <BriefButton request={{ run_id: chainId }} />}
      </div>
      <div className="scn-tabs" role="tablist" aria-label="Scenario loop views">
        {visible.map((t, i) => (
          <button key={t} id={`scn-tab-${t}`} type="button" role="tab" aria-selected={t === active} tabIndex={t === active ? 0 : -1}
            title={TAB_TITLE[t]} className={t === active ? 'on' : undefined} onClick={() => setTab(t)} onKeyDown={(e) => onTabKey(e, i)}>{TAB_LABEL[t]}</button>
        ))}
      </div>
      <div className="panel-body scn-body" role="tabpanel" aria-labelledby={`scn-tab-${active}`}>
        <ScnCtx.Provider value={ctx}>
          {openErr && <RefusalLine r={openErr} />}
          {!checking && !anyServed && <div className="empty-state"><Chip kind="notserved" meta="/scenarios" /></div>}
          {active === 'RUN' && <RunTab />}
          {active === 'SHOCK' && <ShockTab />}
          {active === 'JRNL' && <JournalTab />}
          {active === 'RUNS' && <RunsTab />}
          {active === 'CMP' && <CompareTab />}
          {active === 'VERIFY' && <VerifyTab />}
        </ScnCtx.Provider>
      </div>
    </section>
  );
}
