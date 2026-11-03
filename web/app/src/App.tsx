import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { CANNED_LABEL, CANNED_SPEC } from './seed';
import { layoutPanelsFor, useDeskStore } from './store';
import { gridTemplateColumns, leftColumnEmpty, mapSectionVisible } from './layoutGeometry';
import { SplitDivider } from './components/SplitDivider';
import { registerMapRestore } from './mapFlyTo';
import { consumeTool, registerToolConsumer } from './search/navBridge';
import { TOOL_NONE } from './search/toolOpen';
import { funcForLayout } from './shell/funcTab';
import { ChainPanel, ChainTracePanel, DelayChart, MapContentPanel, MapPanel, MarketWatchPanel, NewsPanel } from './panels/core';
import { SimPanel } from './panels/sim';
import { AlertsPanel, EnsemblePanel, ExplainPanel, HealthPanel, LedgerPanel, MoneyPanel, ProofPanel, SatWatchPanel, WhatIfPanel } from './panels/desk';
import { ImpactPanel } from './panels/impact';
/* Boot performance: panels outside the default screen load on first use. */
import { DesksPanel, LiveTwinPanel, PortTwinPanel, SatelliteDeskPanel, ScenarioLoopPanel } from './panels/lazy';
import { LazyDataViewer, LazyExportCentre as ExportCentrePanel, LazyReportCentre as ReportCentrePanel } from './panels/reports/lazy';
import { LazyLanePlanner } from './panels/laneplanner/lazy';
import { LazyReplay } from './panels/replay/lazy';
import { LazyMonteCarlo } from './panels/montecarlo/lazy';
import { LazyShipments } from './panels/shipments/lazy';
import { LazyWatch } from './panels/watch/lazy';
import { LazyResearch } from './panels/research/lazy';
import { LazyChatPanel } from './chat/lazy';
import { LazyScenarioBuilder } from './panels/scenarios/lazy';
import { LazyBriefPanel } from './brief/lazy';
import { LazyExportWindow } from './exports/lazy';
import { HISTORY_TOOL_PANELS } from './panels/history';
import { PortsLivePanels } from './panels/ports/slot';
import { InspectorTabs } from './shell/InspectorTabs';
import { StatusBar } from './shell/StatusBar';
import { withNow } from './shell/clock';
import { SubBar, type Func } from './shell/SubBar';
import { Tiles } from './shell/Tiles';
import { ToolSlot, useToolProbes } from './shell/ToolSlot';
import { Button } from './ui/Controls';
import { Icon } from './ui/Icon';
import { TopBar } from './shell/TopBar';
import { MAIN_ID, SkipLink } from './shell/SkipLink';
import { TourRoot } from './tour/TourRoot';
import { Transport } from './shell/Transport';
import { railEntries, type RailEntry } from './shell/rail';
import { toolMenuEntries } from './shell/ToolsMenu';
import { PLANNED_TOOLS, toolsFor, type ProbeState, type ToolId } from './shell/tools';
import { useAppearance } from './shell/useAppearance';
import type { PanelId } from './types';
import { DockWorkspace } from './windows/DockWorkspace';
import { WorkspaceBar } from './windows/WorkspaceBar';
import { PhoneWorkspace, usePhone } from './windows/PhoneWorkspace';
import { WorkspaceCtx } from './windows/context';
import { readWorkspaceChoice } from './windows/flag';
import { wmBridge } from './windows/bridge';
import { TOOL_WINDOW, type WindowId } from './windows/registry';
import { runTitle, runWord } from './shell/truthLabel';
import { installHandoffs } from './handoffs/install';
import { ProvenanceDrawer } from './provenance/ProvenanceDrawer';
import { SourcesDialog } from './sources/SourcesDialog';

/** Function code shown in the command-bar search chip + workspace tab,
 *  per active layout (as served by the layout state). */
const LAYOUT_CODE: Record<string, string> = {
  SupermAP: 'SM001', 'Domino Room': 'CHN', 'Place Desk': 'PLD', 'Market Desk': 'MKT', Replay: 'RPL', History: 'HIS', Proof: 'PRF', 'Analysis Desks': 'DSK', 'Live Twin': 'LTV', Simulation: 'SIM', 'Report Desk': 'RPT', 'Data Desk': 'DAT', 'Satellite Desk': 'SAT', 'Ports Board': 'PRT', 'Lane Desk': 'LNE', 'Shipments Desk': 'SHP', 'Watch Desk': 'WCH', 'Port Twin': 'PTW', Agent: 'AGT', Demo: 'DMO', Research: 'RSH', Builder: 'SCB',
};

/* the clock ticks inside these three, not in App (a 1 s App render redrew the whole shell) */
const TopBarT = withNow(TopBar);
const TransportT = withNow(Transport);
const StatusBarT = withNow(StatusBar);

export default function App() {
  const s = useDeskStore(); // Desk-slice consumer — playback ticks do not re-render the shell
  const [dragging, setDragging] = useState(false);
  const [activeFunc, setActiveFunc] = useState<Func | null>('MAP');
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const [tool, setTool] = useState<ToolId | null>(null);
  /* Staged migration: the window manager is the default; ?wm=0 keeps the old grid. */
  const [useWm] = useState(() => readWorkspaceChoice() === 'manager');
  const phone = usePhone();
  const appearance = useAppearance(s.theme, s.setTheme);
  const { probes, reprobe } = useToolProbes();
  /* The command palette opens a rail tool through the nav bridge
   * (the slot is App's state). A target for a tool this build lacks is
   * ignored quietly, as the bridge's contract says. */
  const reprobeRef = useRef(reprobe); reprobeRef.current = reprobe;
  useEffect(() => {
    registerToolConsumer(() => {
      const id = consumeTool();
      if (id === TOOL_NONE) { setTool(null); return; }
      const def = id ? PLANNED_TOOLS.find((t) => t.id === id) : undefined;
      if (!def) return;
      if (useWm) { wmBridge.handlers?.openWindow(TOOL_WINDOW[def.id] as WindowId); reprobeRef.current(def); return; }
      setTool(def.id);
      reprobeRef.current(def);
    });
    return () => registerToolConsumer(null);
  }, []);
  useEffect(() => { void s.boot(); /* eslint-disable-next-line */ }, []);
  useEffect(() => installHandoffs(), []);
  /* Layouts persist to localStorage on change (store effect) — the
   * SAVED {time} stamp is that persistence moment, client-side. */
  useEffect(() => {
    setSavedAt(new Date().toISOString().slice(11, 16));
    /* Layouts that are none of the function tabs light no tab. */
    setActiveFunc((f) => funcForLayout(s.layout, f));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [s.layout]);
  /* A tool belongs to a mode: leaving the mode closes its slot. */
  useEffect(() => {
    setTool((t) => (t && toolsFor(s.mode).some((d) => d.id === t) ? t : null));
  }, [s.mode]);

  /* Phone: the tool slot sits below the stacked map, so opening a tool from
   * the TOOLS menu brings it into view. No-op on a desktop. */
  useEffect(() => {
    if (!tool || typeof window === 'undefined' || !window.matchMedia?.('(max-width: 860px)').matches) return;
    const id = window.setTimeout(() => document.querySelector('[data-panel^="tool-"]')?.scrollIntoView({ block: 'start' }), 60);
    return () => window.clearTimeout(id);
  }, [tool]);

  const worst = useMemo(() => (s.chain?.events ?? []).reduce((m, e) => Math.max(m, e.delay_d), 0), [s.chain]);

  const homeLayout = s.mode === 'sim' ? 'Simulation' : 'SupermAP';
  const railActive = (code: string): boolean => {
    if (tool) return false;
    if (code === 'MAP') return s.layout === 'SupermAP' || s.layout === 'Simulation';
    if (code === 'CHN') return s.layout === 'Domino Room' || s.layout === 'Place Desk';
    if (code === 'WIF') return s.focusPanel === 'whatif';
    if (code === 'RPL') return s.layout === 'Replay' && s.focusPanel !== 'ledger';
    if (code === 'MKT') return s.layout === 'Market Desk';
    if (code === 'LDG') return s.focusPanel === 'ledger';
    if (code === 'PRF') return s.layout === 'Proof';
    if (code === 'RUN') return s.focusPanel === 'sim';
    if (code === 'LTV') return s.layout === 'Live Twin';
    return false;
  };

  const onFunc = (f: Func) => {
    setActiveFunc(f);
    setTool(null);
    if (f === 'HOME' || f === 'MAP') s.setLayout(homeLayout);
    if (f === 'CHAINS') { s.setLayout('Domino Room'); if (s.chain) void s.openChain(s.chain.run_id); }
    if (f === 'WHAT-IF') { s.setLayout('Domino Room'); s.setFocus('whatif'); }
    if (f === 'REPLAY') void s.runWhatIf(CANNED_SPEC, CANNED_LABEL);
    if (f === 'MARKETS') s.showMoney(s.selectedNode ?? 'chk_suez');
    if (f === 'LEDGER') { s.setLayout('Replay'); s.setFocus('ledger'); }
    if (f === 'PROOF') { s.setLayout('Proof'); s.showProof(); }
  };
  const onRail = (e: RailEntry) => {
    if (e.action.t === 'func') onFunc(e.action.func);
    else if (e.action.t === 'layout') {
      setTool(null); s.setLayout(e.action.layout);
      if (e.action.focus) s.setFocus(e.action.focus);
    } else {
      const id = e.action.tool;
      /* PRT opens the Ports Board LAYOUT: PortsPanel is a full Panel
       * (own header/close, layout membership), not a tool-slot body; the
       * generic slot would render a raw dump of the /ports response. */
      if (id === 'ports') { setTool(null); s.setLayout('Ports Board'); return; }
      if (useWm) {
        const def = PLANNED_TOOLS.find((t) => t.id === id);
        wmBridge.handlers?.openWindow(TOOL_WINDOW[id] as WindowId);
        if (def) reprobe(def);
        return;
      }
      setTool((cur) => (cur === id ? null : id));
      const def = PLANNED_TOOLS.find((t) => t.id === id);
      if (def) reprobe(def);
    }
  };
  const gotoExport = () => {
    if (useWm) { wmBridge.handlers?.openWindow('expt'); return; }
    if (!s.visible.includes('expt')) s.setLayout('Report Desk');
    s.setFocus('expt');
  };
  const gotoAlerts = () => {
    if (!s.visible.includes('alerts')) s.setLayout('SupermAP');
    s.setFocus('alerts');
  };
  const show = (p: PanelId) => s.visible.includes(p) && (s.maximized === null || s.maximized === p);
  /* The map is a window with its own state (layoutGeometry) —
   * it is rendered below UNCONDITIONALLY and never passes through
   * show(); geometry only re-templates the grid and toggles its
   * cell's display, so maximize / close / layout switches (Proof
   * included) preserve the single MapLibre instance. */
  const mapInLayout = layoutPanelsFor(s.layout, s.liveTwinEnabled).includes('map');
  const mapVisible = mapSectionVisible({ colW: s.colW, mapHidden: s.mapHidden }, mapInLayout, s.maximized);
  const leftEmpty = leftColumnEmpty(s.visible);
  const gridCols = gridTemplateColumns({ colW: s.colW, mapHidden: s.mapHidden }, mapInLayout, s.maximized, leftEmpty);
  const restoreMapAction = s.restoreMap;
  const layoutNow = s.layout;
  const liveTwinEnabledNow = s.liveTwinEnabled;
  useEffect(() => {
    registerMapRestore(() => {
      if (!layoutPanelsFor(layoutNow, liveTwinEnabledNow).includes('map')) s.setLayout('SupermAP');
      restoreMapAction();
    });
    return () => registerMapRestore(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layoutNow, liveTwinEnabledNow, restoreMapAction]);
  const layoutCode = LAYOUT_CODE[s.layout] ?? 'SM001';

  /* Keyboard:
   * '/' search, Alt+1 LIVE, Alt+2 SIMULATION, Shift+Alt+L toggles LIGHT/DARK. */
  const keys = useRef({ s, appearance }); keys.current = { s, appearance };
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      /* '/' focuses the command search when nothing editable has focus. */
      if (e.key === '/' && !e.altKey && !e.ctrlKey && !e.metaKey) {
        const t = e.target as HTMLElement | null;
        const tag = t?.tagName;
        if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || t?.isContentEditable) return;
        e.preventDefault();
        document.querySelector<HTMLInputElement>('.cmd-input input')?.focus();
        return;
      }
      if (!e.altKey || e.ctrlKey || e.metaKey) return;
      const { s: st, appearance: ap } = keys.current;
      if (!e.shiftKey && e.code === 'Digit1') { e.preventDefault(); st.setMode('live'); }
      else if (!e.shiftKey && e.code === 'Digit2') { e.preventDefault(); st.setMode('sim'); }
      else if (e.shiftKey && e.code === 'KeyL') { e.preventDefault(); ap.setScheme(ap.scheme === 'light' ? 'dark' : 'light'); }
    };
    window.addEventListener('keydown', on);
    return () => window.removeEventListener('keydown', on);
  }, []);

  const probeStates = useMemo(() => {
    const m: Partial<Record<ToolId, ProbeState>> = {};
    for (const t of PLANNED_TOOLS) m[t.id] = probes[t.id].state;
    return m;
  }, [probes]);
  const rail = railEntries(s.mode, s.liveTwinEnabled, probeStates);
  const openTool = tool ? PLANNED_TOOLS.find((t) => t.id === tool) ?? null : null;

  const reprobeStable = useCallback((t: Parameters<typeof reprobe>[0]) => reprobeRef.current(t), []);
  const wsValue = useMemo(() => ({ probes: Object.fromEntries(PLANNED_TOOLS.map((t) => [t.id, probes[t.id]])) as never, reprobe: reprobeStable, appearance }), [probes, reprobeStable, appearance]);

  return (
    <WorkspaceCtx.Provider value={wsValue}>
    <div className={`shell shell--${s.mode}`}>
      <SkipLink />
      <TopBarT layoutCode={layoutCode} a={appearance} onAlerts={gotoAlerts} onExport={gotoExport} />
      {useWm ? <WorkspaceBar /> : (
        <SubBar activeFunc={activeFunc} onFunc={onFunc} layoutCode={layoutCode} savedAt={savedAt}
          tools={toolMenuEntries(rail)} activeTool={tool} onTool={onRail} />
      )}
      <Tiles />
      {s.error && <div className="errorbar mono" role="alert">{s.error} <Button size="sm" onClick={() => window.location.reload()}>Reload</Button></div>}
      {!useWm ? (
        <>
      {/* ── Workspace grid (rail + 282 + centre + 424) ─────── */}
      <main id={MAIN_ID} tabIndex={-1} className={`grid ${s.maximized ? 'grid--max' : ''} ${dragging ? 'grid--dragging' : ''}`} style={{ gridTemplateColumns: gridCols }}>
        <div className="fn-rail" role="group" aria-label="Function rail">
          {mapInLayout && s.mapHidden && (
            <button className="rail-map-restore" title="Restore map" onClick={() => s.restoreMap()}>Map <Icon name="chevron-right" size="xs" /></button>
          )}
          {rail.map((r) => (
            <React.Fragment key={r.code}>
              {r.sep && <span className="rail-sep" aria-hidden="true" />}
              <button type="button" title={r.mark === 'notserved' ? `${r.title}: not served` : r.title}
                className={`${r.action.t === 'tool' ? (tool === r.action.tool ? 'on' : '') : railActive(r.code) ? 'on' : ''}${r.mark ? ` rail-mark rail-mark--${r.mark}` : ''}`.trim()}
                onClick={() => onRail(r)}>{r.code}{r.mark === 'notserved' && <Icon name="ban" size="xs" />}</button>
            </React.Fragment>
          ))}
        </div>
        <div className="col col--left" style={leftEmpty && s.maximized === null ? { display: 'none' } : undefined}>{show('news') && <NewsPanel />}{show('market') && <MarketWatchPanel />}{show('satwatch') && <SatWatchPanel />}{show('health') && <HealthPanel />}</div>
        {/* MapPanel is ALWAYS mounted (the rule, extended
            to the whole shell) — hidden states are display:none on its
            column, never an unmount, so the MapLibre instance and its
            camera survive maximize, close and layout switches. */}
        <div className="col col--centre" style={{ display: mapVisible ? undefined : 'none' }}><MapPanel /></div>
        {!s.maximized && mapVisible && (
          <>
            {!leftEmpty && <SplitDivider side="left" value={s.colW.left} onChange={s.setColW} onDragState={setDragging} />}
            <SplitDivider side="right" value={s.colW.right} onChange={s.setColW} onDragState={setDragging} />
          </>
        )}
        {/* The causal chain leads the right column — Chain
            Trace + Chain render above Map Content in the DOM, so the
            chain starts above the fold in the home layout. Panel focus
            scrolling (Panel.tsx scrollIntoView on focusPanel) is DOM-order
            independent. The inspector tab strip and an open tool slot
            precede them. */}
        <div className="col col--right">
          {s.maximized === null && <InspectorTabs />}
          {s.maximized === null && openTool && (openTool.id === 'scenarios' && probes.scenarios.state === 'served'
            ? <ScenarioLoopPanel onClose={() => setTool(null)} />
            : HISTORY_TOOL_PANELS[openTool.id]
            ? React.createElement(HISTORY_TOOL_PANELS[openTool.id]!, { onClose: () => setTool(null) })
            : <ToolSlot tool={openTool} probe={probes[openTool.id]} onClose={() => setTool(null)} />)}
          {/* SIMULATION: run control leads the right column. */}
          {s.mode === 'sim' && show('sim') && <SimPanel />}
          {/* The Live Twin layout opens on its own panel, not below Map Content. */}
          {show('livetwin') && <LiveTwinPanel />}
          <PortsLivePanels show={show} />
          {show('chaintrace') && <ChainTracePanel />}{show('chain') && <ChainPanel />}
          {show('mapcontent') && <MapContentPanel />}{show('whatif') && <WhatIfPanel />}{show('ensemble') && <EnsemblePanel />}{show('alerts') && <AlertsPanel />}{show('explain') && <ExplainPanel />}
          {show('proof') && <ProofPanel />}{show('replay') && <LazyReplay />}{show('ledger') && <LedgerPanel />}{show('money') && <MoneyPanel />}{show('impact') && <ImpactPanel />}{show('sim') && s.mode !== 'sim' && <SimPanel />}{show('desks') && <DesksPanel />}
          {show('dataviewer') && <LazyDataViewer />}{show('laneplanner') && <LazyLanePlanner />}{show('montecarlo') && <LazyMonteCarlo />}{show('shipments') && <LazyShipments />}{show('watchlists') && <LazyWatch />}{show('research') && <LazyResearch />}{show('reports') && <ReportCentrePanel />}{show('exports') && <ExportCentrePanel />}{show('expt') && <LazyExportWindow />}{show('satdesk') && <SatelliteDeskPanel />}{show('porttwin') && <PortTwinPanel />}{show('agent') && <LazyChatPanel />}{show('brief') && <LazyBriefPanel />}{show('scenariobuilder') && <LazyScenarioBuilder />}</div>
      </main>
        </>
      ) : (
        phone ? <PhoneWorkspace /> : <main id={MAIN_ID} tabIndex={-1} className="wm-main"><DockWorkspace /></main>
      )}
      {!useWm && (
        <>
      {/* ── Chart dock (full width, above the transport bar) ───────────
          A sparse chain (< 3 events) collapses the 150px strip
          to a ~34px summary line. */}
      {(s.chain?.events.length ?? 0) < 3 ? (
        <div className="chart-strip chart-dock chart-strip--sparse">
          <div className="chart-summary mono">
            <span className="code-chip">CHT</span><b>{s.runMeta ? runTitle(s.runMeta, s.health, s.snapshotMode, Date.now()) : 'Added delay (d)'}</b>
            <span>{s.runMeta ? runWord(s.runMeta, s.health, s.snapshotMode, Date.now()) : ''} · Chain</span>
            <span className="spacer" />
            <span>{s.chain ? `Max +${worst.toFixed(1)} d · ${s.chain.events.length} event${s.chain.events.length === 1 ? '' : 's'}` : 'No chain'}</span>
          </div>
        </div>
      ) : (
        <div className="chart-strip chart-dock"><DelayChart h={100} w={1600} /></div>
      )}
        </>
      )}
      <TransportT history={probes.history.state} onNow={() => setTool(null)}
        onHistory={() => { const d = PLANNED_TOOLS.find((t) => t.id === 'history'); setTool('history'); if (d) reprobe(d); }} />
      <StatusBarT probes={probeStates} />
      <TourRoot />
      <ProvenanceDrawer />
      <SourcesDialog />
    </div>
    </WorkspaceCtx.Provider>
  );
}
