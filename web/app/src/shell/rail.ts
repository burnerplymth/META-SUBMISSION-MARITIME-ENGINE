import type { PanelId } from '../types';
import { LIVE_TWIN_NAME, type ShellMode } from './mode';
import { toolsFor, type ProbeState, type ToolId } from './tools';

/* The left tool rail (34 px, codes) per mode. LIVE offers the live
 * terminal panels (the LIVE TWIN entry only while its server flag is on:
 * flag off means no rail entry, no layout entry, no section); SIMULATION
 * offers run control, chains and what-if. The tools whose routes are
 * provided separately follow the panel entries and carry an honest
 * state mark; a tool whose feed is DOWN hides. */

export type RailFunc = 'MAP' | 'CHAINS' | 'WHAT-IF' | 'REPLAY' | 'MARKETS' | 'LEDGER' | 'PROOF';
export type RailAction =
  | { t: 'func'; func: RailFunc }
  | { t: 'layout'; layout: string; focus?: PanelId }
  | { t: 'tool'; tool: ToolId };
export interface RailEntry { code: string; title: string; action: RailAction; mark?: 'notserved'; sep?: boolean }

const F = (code: string, title: string, func: RailFunc): RailEntry => ({ code, title, action: { t: 'func', func } });

export function railEntries(mode: ShellMode, liveTwinEnabled: boolean, probes: Partial<Record<ToolId, ProbeState>>): RailEntry[] {
  const panels: RailEntry[] = mode === 'sim'
    ? [
      F('MAP', 'Map', 'MAP'),
      { code: 'RUN', title: 'Run control', action: { t: 'layout', layout: 'Simulation', focus: 'sim' } },
      F('CHN', 'Chains', 'CHAINS'),
      F('WIF', 'What-if', 'WHAT-IF'),
    ]
    : [
      F('MAP', 'Map', 'MAP'),
      F('MKT', 'Markets', 'MARKETS'),
      F('RPL', 'Replay', 'REPLAY'),
      F('LDG', 'Ledger', 'LEDGER'),
      F('PRF', 'Proof', 'PROOF'),
    ];
  if (mode === 'live' && liveTwinEnabled) panels.push({ code: 'LTV', title: LIVE_TWIN_NAME, action: { t: 'layout', layout: LIVE_TWIN_NAME } });
  const tools: RailEntry[] = [];
  for (const t of toolsFor(mode)) {
    const st = probes[t.id] ?? 'checking';
    if (st === 'down' || (st === 'notserved' && t.hideWhenNotServed)) continue;
    tools.push({ code: t.code, title: t.title, action: { t: 'tool', tool: t.id }, mark: st === 'notserved' ? 'notserved' : undefined, sep: tools.length === 0 });
  }
  return [...panels, ...tools];
}
