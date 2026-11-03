/* The agent's two browser steps for the Scenario builder, and the hand-off
 * hooks other windows use.
 *
 *   scenario_open      fills the builder with shocks; nothing is run.
 *   scenario_organise  sends the builder's shocks and ONE structured rule to
 *                      POST /scenarios/organise (the server arranges, no
 *                      model) and shows the result as a diff the person
 *                      accepts or discards.
 *
 * The arguments came from a model, so they are checked again here. */
import type { ClientResult } from '../../agent/types';
import { scenarioApi } from './client';
import { MAX_SHOCKS, emptyDoc, fromWire, isKind, pendingFrom, templateFor } from './doc';
import { getBuilder, type BuilderStore } from './store';
import { isDate } from './timeline';
import type { OrganiseRule, WireShock } from './types';

const ok = (summary: string): ClientResult => ({ ok: true, summary });
const fail = (code: string, summary: string): ClientResult => ({ ok: false, code, summary });
const isObj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);
const PLACE_RE = /^[A-Za-z0-9_-]{1,64}$/;
const RULE_KINDS = ['stagger', 'sequence', 'cluster', 'align_start', 'spread_evenly', 'shift'];

export interface ScenarioViewDeps {
  hasPlace(id: string): boolean;
  /** Make the builder window visible. */
  showBuilder(): void;
}

/** scenario_open: validate the model's shocks and load them as a new draft. */
export function scenarioOpen(raw: unknown, d: ScenarioViewDeps, store: BuilderStore = getBuilder()): ClientResult {
  const a = isObj(raw) ? raw : {};
  const shocks = a.shocks;
  if (!Array.isArray(shocks) || shocks.length === 0 || shocks.length > MAX_SHOCKS) return fail('invalid_shocks', `shocks must be 1 to ${MAX_SHOCKS} items`);
  let doc = emptyDoc(store.getState().doc.startDate);
  if (typeof a.name === 'string') doc = { ...doc, name: a.name.slice(0, 60) };
  if (typeof a.start_at === 'string' && isDate(a.start_at.slice(0, 10))) doc = { ...doc, startDate: a.start_at.slice(0, 10) };
  const wire: WireShock[] = [];
  for (const s of shocks) {
    if (!isObj(s) || typeof s.place_id !== 'string' || !PLACE_RE.test(s.place_id) || !d.hasPlace(s.place_id)) return fail('unknown_place', 'unknown place in a shock');
    if (!isKind(s.kind)) return fail('invalid_kind', 'a shock has an unknown kind');
    const t = templateFor(s.kind, typeof s.start_offset_d === 'number' ? s.start_offset_d : 0);
    wire.push({
      id: typeof s.id === 'string' ? s.id : `s${wire.length + 1}`, place_id: s.place_id, kind: s.kind, start_offset_d: t.startD,
      duration_d: typeof s.duration_d === 'number' ? s.duration_d : t.durationD, capacity_loss: typeof s.capacity_loss === 'number' ? s.capacity_loss : t.loss,
      shape: typeof s.shape === 'string' ? s.shape : 'step', note: typeof s.note === 'string' ? s.note : '',
    });
  }
  const built = fromWire({ name: doc.name, start_at: `${doc.startDate}T00:00:00Z`, shocks: wire });
  if (built.shocks.length !== wire.length) return fail('invalid_shocks', 'a shock could not be read');
  doc = built;
  d.showBuilder();
  store.load(doc);
  store.setPlaces([...new Set(doc.shocks.map((s) => s.placeId))]);
  store.noteRecent(doc.shocks.map((s) => s.placeId));
  return ok(`Builder filled with ${doc.shocks.length} shock${doc.shocks.length === 1 ? '' : 's'}`);
}

/** Read and bound the rule the model sent (the server validates it in full). */
export function readRule(raw: unknown): OrganiseRule | null {
  if (!isObj(raw) || typeof raw.kind !== 'string' || !RULE_KINDS.includes(raw.kind)) return null;
  return raw as unknown as OrganiseRule;
}

/** Ask the server to arrange the builder's shocks by `rule`; the answer becomes the pending diff. */
export async function proposeArrangement(rule: OrganiseRule, store: BuilderStore, source: 'meta-ai' | 'rule', text: string | null, signal?: AbortSignal): Promise<ClientResult> {
  const doc = store.getState().doc;
  if (doc.shocks.length === 0) return fail('empty_builder', 'No shocks on the timeline.');
  try {
    const res = await scenarioApi.organise(doc.shocks.map((s) => ({ id: s.id, place_id: s.placeId, start_offset_d: s.startD, duration_d: s.durationD })), rule, signal);
    const pending = pendingFrom(res, source, text);
    if (pending.moved === 0) { store.setPending(null); return ok('Arrangement leaves every start unchanged'); }
    store.setPending(pending);
    return ok(`Arrangement proposed: ${pending.moved} move${pending.moved === 1 ? '' : 's'}`);
  } catch (e) {
    const x = e as { code?: string; message?: string };
    return fail(typeof x.code === 'string' && /^[a-z0-9_]{1,40}$/.test(x.code) ? x.code : 'organise_failed', (x.message ?? 'The arrangement was refused').slice(0, 160));
  }
}

export async function scenarioOrganise(raw: unknown, d: ScenarioViewDeps, store: BuilderStore = getBuilder()): Promise<ClientResult> {
  const a = isObj(raw) ? raw : {};
  const rule = readRule(a.rule);
  if (!rule) return fail('invalid_rule', 'Rule not recognised.');
  d.showBuilder();
  return proposeArrangement(rule, store, 'meta-ai', null);
}

/* ── hand-off hooks for sibling windows ─────────────────────────────────
 * Monte Carlo and Shipments read the active scenario through these. A window
 * registers a handler for its kind; the builder shows the matching control
 * only while a handler exists, so no dead control is ever drawn.
 *
 *   registerHandoff('monte-carlo', (s) => prefill the Monte Carlo form from s)
 *   registerHandoff('shipments',   (s) => evaluate shipments against s)
 */
export type HandoffKind = 'monte-carlo' | 'shipments';
export interface HandoffScenario { scenarioId: string | null; name: string; startAt: string; shocks: WireShock[]; /** the chain engine revision chosen in the builder */ engineRev?: 1 | 2 }
type Handler = (s: HandoffScenario) => void;
const handlers = new Map<HandoffKind, Set<Handler>>();
const listeners = new Set<() => void>();
export function registerHandoff(kind: HandoffKind, h: Handler): () => void {
  const set = handlers.get(kind) ?? new Set<Handler>();
  set.add(h); handlers.set(kind, set); listeners.forEach((l) => l());
  return () => { set.delete(h); listeners.forEach((l) => l()); };
}
export const hasHandoff = (kind: HandoffKind): boolean => (handlers.get(kind)?.size ?? 0) > 0;
export function subscribeHandoffs(l: () => void): () => void { listeners.add(l); return () => { listeners.delete(l); }; }
export function handoffSnapshot(): string { return `${hasHandoff('monte-carlo') ? 1 : 0}${hasHandoff('shipments') ? 1 : 0}`; }
export function sendHandoff(kind: HandoffKind, s: HandoffScenario): number {
  const set = handlers.get(kind);
  set?.forEach((h) => h(s));
  return set?.size ?? 0;
}
