import React, { useContext, useEffect } from 'react';
import { ChainPanel, ChainTracePanel, DelayChart, MapContentPanel, MapPanel, MarketWatchPanel, NewsPanel } from '../panels/core';
import { SimPanel } from '../panels/sim';
import { AlertsPanel, EnsemblePanel, ExplainPanel, HealthPanel, LedgerPanel, MoneyPanel, SatWatchPanel, WhatIfPanel } from '../panels/desk';
import { ImpactPanel } from '../panels/impact';
/* Boot performance: panels outside the default screen load on first use. */
import { DesksPanel, LiveTwinPanel, PortTwinPanel, SatelliteDeskPanel, ScenarioLoopPanel } from '../panels/lazy';
import { LazyDataViewer, LazyExportCentre as ExportCentrePanel, LazyReportCentre as ReportCentrePanel } from '../panels/reports/lazy';
import { HISTORY_TOOL_PANELS } from '../panels/history';
import { LazyLanePlanner } from '../panels/laneplanner/lazy';
import { LazyReplay } from '../panels/replay/lazy';
import { LazyScenarioBuilder } from '../panels/scenarios/lazy';
import { LazyMonteCarlo } from '../panels/montecarlo/lazy';
import { LazyShipments } from '../panels/shipments/lazy';
import { LazyWatch } from '../panels/watch/lazy';
import { LazyResearch } from '../panels/research/lazy';
import { AiActivityPanel } from '../meta/AiActivityPanel';
import { LazyChatPanel } from '../chat/lazy';
import { LazyBriefPanel } from '../brief/lazy';
import { LazyExportWindow } from '../exports/lazy';

import { PortsLivePanels } from '../panels/ports/slot';
import { ToolSlot } from '../shell/ToolSlot';
import { PLANNED_TOOLS, type ToolId } from '../shell/tools';
import { Status } from '../ui/Status';
import { WorkspaceCtx } from './context';
import { TOOL_OF_WINDOW, type WindowId } from './registry';
import { wm } from './controller';

/** A rail tool shown as a window: the served panel when the route answers,
 *  else the generic state card (served, not served, down). */
function ToolWindow({ wid }: { wid: WindowId }) {
  const ws = useContext(WorkspaceCtx);
  const toolId = TOOL_OF_WINDOW[wid] as ToolId;
  const def = PLANNED_TOOLS.find((t) => t.id === toolId);
  const probe = ws.probes[toolId];
  useEffect(() => { if (def) ws.reprobe(def); /* probe once when the window opens */ // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  if (!def) return <div className="empty-state"><Status state="notserved" /></div>;
  const close = () => wm.closeWindow(wid);
  if (toolId === 'scenarios' && probe?.state === 'served') return <ScenarioLoopPanel onClose={close} />;
  const Custom = HISTORY_TOOL_PANELS[toolId];
  if (Custom) return React.createElement(Custom, { onClose: close });
  return <ToolSlot tool={def} probe={probe ?? { state: 'checking', doc: null }} onClose={close} />;
}

function ChartsWindow() {
  return <section className="panel panel--charts" data-panel="charts"><div className="panel-body chart-win"><DelayChart large /></div></section>;
}

const only = (id: 'livefeed' | 'livetape' | 'livectx' | 'ports') => <PortsLivePanels show={(p) => p === id} />;

/** The component each window shows. Every entry renders the same panel the
 *  old grid rendered; nothing here changes a panel's behaviour. */
export function WindowBody({ wid }: { wid: WindowId }) {
  const ws = useContext(WorkspaceCtx);
  switch (wid) {
    case 'map': return <MapPanel />;
    case 'news': return <NewsPanel />;
    case 'market': return <MarketWatchPanel />;
    case 'satwatch': return <SatWatchPanel />;
    case 'health': return <HealthPanel />;
    case 'mapcontent': return <MapContentPanel />;
    case 'chain': return <ChainPanel />;
    case 'chaintrace': return <ChainTracePanel />;
    case 'whatif': return <WhatIfPanel />;
    case 'ensemble': return <EnsemblePanel />;
    case 'sim': return <SimPanel />;
    case 'impact': return <ImpactPanel />;
    case 'desks': return <DesksPanel />;
    case 'proof': return <LazyReplay as="proof" />;
    case 'replay': return <LazyReplay />;
    case 'ledger': return <LedgerPanel />;
    case 'money': return <MoneyPanel />;
    case 'explain': return <ExplainPanel />;
    case 'alerts': return <AlertsPanel />;
    case 'reports': return <ReportCentrePanel />;
    case 'dataviewer': return <LazyDataViewer />;
    case 'laneplanner': return <LazyLanePlanner />;
    case 'scenariobuilder': return <LazyScenarioBuilder />;
    case 'montecarlo': return <LazyMonteCarlo />;
    case 'shipments': return <LazyShipments />;
    case 'watchlists': return <LazyWatch />;
    case 'exports': return <ExportCentrePanel />;
    case 'expt': return <LazyExportWindow />;
    case 'satdesk': return <SatelliteDeskPanel />;
    case 'porttwin': return <PortTwinPanel />;
    case 'ports': return only('ports');
    case 'livetwin': return <LiveTwinPanel />;
    case 'livefeed': return only('livefeed');
    case 'livetape': return only('livetape');
    case 'livectx': return only('livectx');
    case 'charts': return <ChartsWindow />;
    case 'aiactivity': return <AiActivityPanel />;
    case 'agent': return <LazyChatPanel />;
    case 'brief': return <LazyBriefPanel />;
    case 'research': return <LazyResearch />;
    case 'tool-history': case 'tool-realism': case 'tool-scenarios': case 'tool-exports': return <ToolWindow wid={wid} />;
    default: return null;
  }
}
