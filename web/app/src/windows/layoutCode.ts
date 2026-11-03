/** Function code shown in the command bar chip and the workspace tab, per
 *  active layout (moved out of App.tsx so the window manager shares it). */
export const LAYOUT_CODE: Record<string, string> = {
  SupermAP: 'SM001', 'Domino Room': 'CHN', 'Place Desk': 'PLD', 'Market Desk': 'MKT', Replay: 'RPL', History: 'HIS', Proof: 'PRF', 'Analysis Desks': 'DSK', 'Live Twin': 'LTV', Simulation: 'SIM', 'Report Desk': 'RPT', 'Data Desk': 'DAT', 'Satellite Desk': 'SAT', 'Ports Board': 'PRT', 'Lane Desk': 'LNE', 'Shipments Desk': 'SHP', 'Watch Desk': 'WCH', 'Port Twin': 'PTW', Agent: 'AGT', Demo: 'DMO', Research: 'RSH', Builder: 'SCB',
};
/** The name a layout wears on screen when it differs from its stored key. The
 *  key (`Agent`) stays so `#layout=Agent`, `layout agent` and stored layouts keep working. */
export const LAYOUT_LABEL: Record<string, string> = { Agent: 'Meta AI' };
export const layoutLabel = (layout: string): string => LAYOUT_LABEL[layout] ?? layout;
export const layoutCodeFor = (layout: string): string => LAYOUT_CODE[layout] ?? 'SM001';
