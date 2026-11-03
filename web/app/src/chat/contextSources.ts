/**
 * CONTEXT CHIPS: what the person can attach to a message. Each chip is an id
 * and a few key numbers (never a dump); the server re-validates the shape and
 * hands it to the model as a data block. The panel attaches the current
 * candidates by default and shows each as a removable chip.
 *
 * Built-in sources read the store (selected place, loaded run). Other windows
 * add theirs with registerContextSource (scenario, Monte Carlo job, shipment
 * list summary, research assembly): one function returning chips.
 */
import { CONTEXT_KINDS, type ContextChip, type ContextKind } from './types';

export type ContextSource = () => ContextChip[];
const sources = new Map<string, ContextSource>();

/** Register (or replace) a source; returns the unregister function. */
export function registerContextSource(id: string, fn: ContextSource): () => void {
  sources.set(id, fn);
  return () => { if (sources.get(id) === fn) sources.delete(id); };
}

export const chipKey = (c: Pick<ContextChip, 'kind' | 'id' | 'label'>): string => `${c.kind}:${c.id ?? c.label}`;

const KEY_RE = /^[a-z0-9_]{1,40}$/;
const ID_RE = /^[A-Za-z0-9_\-:.]{1,96}$/;

/** A chip the server will accept, or null (bad kind, id or empty label). Strings are clipped. */
export function cleanChip(c: ContextChip): ContextChip | null {
  if (!c || !(CONTEXT_KINDS as readonly string[]).includes(c.kind)) return null;
  const label = String(c.label ?? '').replace(/\s+/g, ' ').trim().slice(0, 80);
  if (!label) return null;
  const id = c.id == null ? null : String(c.id);
  if (id !== null && !ID_RE.test(id)) return null;
  const summary: ContextChip['summary'] = {};
  for (const [k, v] of Object.entries(c.summary ?? {}).slice(0, 12)) {
    if (!KEY_RE.test(k)) continue;
    if (typeof v === 'number' && Number.isFinite(v)) summary[k] = v;
    else if (typeof v === 'boolean') summary[k] = v;
    else if (typeof v === 'string') summary[k] = v.replace(/\s+/g, ' ').trim().slice(0, 160);
  }
  return { kind: c.kind as ContextKind, id, label, summary };
}

export interface StoreContextInput {
  selectedNode: string | null;
  graph: { nodes: ReadonlyArray<{ id?: unknown; name?: unknown }> } | null;
  chain: { run_id: string; status?: string; event_count?: number; stream_hash?: string } | null;
  runMeta: { label?: string } | null;
}

/** The built-in candidates from the store: the selected place and the loaded run. */
export function storeContext(s: StoreContextInput): ContextChip[] {
  const out: ContextChip[] = [];
  if (s.selectedNode) {
    const node = s.graph?.nodes.find((n) => n.id === s.selectedNode);
    out.push({ kind: 'place', id: s.selectedNode, label: typeof node?.name === 'string' && node.name ? node.name : s.selectedNode, summary: {} });
  }
  if (s.chain) {
    const summary: ContextChip['summary'] = {};
    if (typeof s.chain.event_count === 'number') summary.events = s.chain.event_count;
    if (s.chain.status) summary.status = s.chain.status;
    if (s.chain.stream_hash) summary.stream_hash = s.chain.stream_hash.slice(0, 16);
    out.push({ kind: 'run', id: s.chain.run_id, label: s.runMeta?.label ?? 'Loaded run', summary });
  }
  return out;
}

/** Built-ins plus every registered source; a source that throws is skipped. */
export function collectContext(builtin: ContextChip[]): ContextChip[] {
  const all = [...builtin];
  for (const fn of sources.values()) {
    try { all.push(...fn()); } catch { /* a window's summary is a convenience */ }
  }
  const seen = new Set<string>();
  const out: ContextChip[] = [];
  for (const c of all) {
    const cc = cleanChip(c);
    if (!cc || seen.has(chipKey(cc))) continue;
    seen.add(chipKey(cc));
    out.push(cc);
  }
  return out.slice(0, 6);
}

/** The chips that go with a message: the candidates minus the ones the person removed. */
export function attachedFrom(candidates: readonly ContextChip[], dismissed: readonly string[]): ContextChip[] {
  return candidates.filter((c) => !dismissed.includes(chipKey(c)));
}

/** Dismissals only count for chips that are still candidates (a place selected again re-attaches). */
export function pruneDismissed(candidates: readonly ContextChip[], dismissed: readonly string[]): string[] {
  const live = new Set(candidates.map(chipKey));
  return dismissed.filter((k) => live.has(k));
}
