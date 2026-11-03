/* Dynamic-import seams for the panels that are not on the default screen
 * (boot performance). Each export keeps the name and props of the
 * panel it wraps, so App.tsx renders them exactly as before; the panel
 * code lands in its own chunk, fetched on first use. */
import { lazyPanel } from '../components/LazyPanel';

/* The panels' stylesheets stay in the entry stylesheet, in the order the
 * static imports give them (reports, satellite desk, scenario,
 * after the base rules' import position in App.tsx). A stylesheet that
 * arrived with its lazy chunk would be appended AFTER chrome.css and
 * win equal-specificity ties it would otherwise lose (.scn-btn padding
 * in the SCN panel). Styles are cheap; the code is what is lazy. */
import './reports/reports.css';
import './satwatch2/satwatch2.css';
import './scenario/scenario.css';
import './porttwin/porttwin.css';

export const DesksPanel = lazyPanel<{ demoActive?: boolean }>(
  { code: 'DSK', title: 'ANALYSIS DESKS' },
  () => import('./desks').then((m) => ({ default: m.DesksPanel })),
);
export const LiveTwinPanel = lazyPanel<Record<string, never>>(
  { code: 'LTV', title: 'LIVE TWIN' },
  () => import('./livetwin').then((m) => ({ default: m.LiveTwinPanel })),
);
export const ReportCentrePanel = lazyPanel<Record<string, never>>(
  { code: 'RPT', title: 'REPORT CENTRE' },
  () => import('./reports/ReportCentrePanel').then((m) => ({ default: m.ReportCentrePanel })),
);
export const ExportCentrePanel = lazyPanel<Record<string, never>>(
  { code: 'EXP', title: 'EXPORT CENTRE' },
  () => import('./reports/ExportCentrePanel').then((m) => ({ default: m.ExportCentrePanel })),
);
export const SatelliteDeskPanel = lazyPanel<Record<string, never>>(
  { code: 'SAT', title: 'SATELLITE DESK' },
  () => import('./satwatch2/SatelliteDeskPanel').then((m) => ({ default: m.SatelliteDeskPanel })),
);
export const ScenarioLoopPanel = lazyPanel<{ onClose: () => void }>(
  { code: 'SCN', title: 'SCENARIO LOOP', className: 'tool-slot' },
  () => import('./scenario').then((m) => ({ default: m.ScenarioLoopPanel })),
);
export const PortTwinPanel = lazyPanel<Record<string, never>>(
  { code: 'PTW', title: 'PORT TWIN' },
  () => import('./porttwin/PortTwinPanel').then((m) => ({ default: m.PortTwinPanel })),
);
