import type { ShellMode } from './mode';

/* Tool panels the shell offers beside the existing desk panels, whose
 * backend routes are provided separately. The shell never fabricates
 * their content: each tool probes its own GET route and renders the
 * honest state: SERVED (summary of the real answer), NOT SERVED (the
 * route does not exist yet) or DOWN (the feed is erroring: the entry
 * hides, the status bar keeps the chip). */

export type ToolId = 'ports' | 'history' | 'realism' | 'scenarios' | 'exports';
export interface ToolDef { id: ToolId; code: string; title: string; route: string; modes: readonly ShellMode[]; hideWhenNotServed?: boolean;
  /** Health-board adapters behind the route, when it fronts a feed; the header chip reads their state. */
  adapters?: readonly string[] }

export const PLANNED_TOOLS: readonly ToolDef[] = [
  { id: 'ports', code: 'PRT', title: 'Ports', route: '/ports', modes: ['live'] },
  { id: 'history', code: 'HIS', title: 'History', route: '/history/sources', modes: ['live'] },
  { id: 'realism', code: 'RLS', title: 'Realism', route: '/realism/results', modes: ['live', 'sim'] },
  { id: 'scenarios', code: 'SCN', title: 'Scenario loop', route: '/scenarios', modes: ['sim'], hideWhenNotServed: true },
  { id: 'exports', code: 'EXP', title: 'Exports', route: '/exports/datasets', modes: ['live', 'sim'] },
];

export const toolsFor = (mode: ShellMode): ToolDef[] => PLANNED_TOOLS.filter((t) => t.modes.includes(mode));

export type ProbeState = 'checking' | 'served' | 'notserved' | 'down';

/** Classify one GET probe. A 2xx that is not JSON is the SPA fallback
 *  (a dev/preview server answering index.html for an unknown path) and
 *  counts as NOT SERVED; 404/405/501 are NOT SERVED; any other failure
 *  (network error, 5xx, 401/403) is DOWN. */
export function classifyProbe(status: number | null, contentType: string | null): ProbeState {
  if (status === null) return 'down';
  if (status >= 200 && status < 300) return /json/i.test(contentType ?? '') ? 'served' : 'notserved';
  if (status === 404 || status === 405 || status === 501) return 'notserved';
  return 'down';
}

export interface ProbeSummaryRow { key: string; value: string }
/** A summary of a served answer from its real fields only: scalars
 *  verbatim (strings clipped), arrays and objects as counts. At most
 *  `max` rows. Never invents a field. */
export function summariseDoc(doc: unknown, max = 8): ProbeSummaryRow[] {
  if (Array.isArray(doc)) return [{ key: 'items', value: String(doc.length) }];
  if (!doc || typeof doc !== 'object') return [];
  const rows: ProbeSummaryRow[] = [];
  for (const [k, v] of Object.entries(doc as Record<string, unknown>)) {
    if (rows.length >= max) break;
    if (Array.isArray(v)) rows.push({ key: k, value: `${v.length} items` });
    else if (v && typeof v === 'object') rows.push({ key: k, value: `${Object.keys(v as object).length} fields` });
    else if (typeof v === 'string') rows.push({ key: k, value: v.length > 48 ? `${v.slice(0, 47)}…` : v });
    else if (typeof v === 'number' || typeof v === 'boolean') rows.push({ key: k, value: String(v) });
  }
  return rows;
}

export interface ProbeResult { state: ProbeState; doc: unknown }
export type FetchLike = (url: string, init?: { headers?: Record<string, string> }) => Promise<{
  status: number; headers: { get(k: string): string | null }; json(): Promise<unknown>;
}>;
/** GET-only probe. Never throws. */
export async function probeTool(base: string, route: string, fetchFn: FetchLike): Promise<ProbeResult> {
  try {
    const res = await fetchFn(`${base}${route}`, { headers: { accept: 'application/json' } });
    const state = classifyProbe(res.status, res.headers.get('content-type'));
    if (state !== 'served') return { state, doc: null };
    try { return { state, doc: await res.json() }; } catch { return { state: 'down', doc: null }; }
  } catch { return { state: 'down', doc: null }; }
}
