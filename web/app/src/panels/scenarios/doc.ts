/* Scenario builder document operations. Pure and immutable: every function
 * returns a new document and never touches storage, the clock or the DOM. */
import { clamp, DURATION_MAX_D, OFFSET_MAX_D, r4 } from './timeline';
import { KINDS, SHAPES, type BuilderDoc, type OrganiseDoc, type PendingArrangement, type ShockKind, type UiShock, type WireScenario, type WireShock } from './types';

export const MAX_SHOCKS = 12;
export const ID_RE = /^[A-Za-z0-9_-]{1,24}$/;
export const NAME_MAX = 60;
export const NOTE_MAX = 140;
export const DEFAULT_START_DATE = '2026-11-02';

export const KIND_LABEL: Record<ShockKind, string> = {
  closure: 'Closure', capacity_cut: 'Capacity cut', congestion: 'Congestion', strike: 'Strike', weather: 'Weather',
  grounding: 'Grounding', accident: 'Accident', policy: 'Policy', other: 'Other',
};

/** What a new shock of a kind starts with: a closure runs at full loss. */
export const KIND_DEFAULTS: Record<ShockKind, { loss: number; durationD: number }> = {
  closure: { loss: 1, durationD: 14 }, capacity_cut: { loss: 0.5, durationD: 30 }, congestion: { loss: 0.5, durationD: 14 },
  strike: { loss: 0.5, durationD: 7 }, weather: { loss: 0.5, durationD: 5 }, grounding: { loss: 1, durationD: 5 },
  accident: { loss: 0.5, durationD: 3 }, policy: { loss: 0.3, durationD: 30 }, other: { loss: 0.5, durationD: 7 },
};

export const emptyDoc = (startDate = DEFAULT_START_DATE): BuilderDoc => ({ name: '', startDate, shocks: [] });
export const isKind = (v: unknown): v is ShockKind => typeof v === 'string' && (KINDS as readonly string[]).includes(v);

/** The loss a shock really runs at: a closure is always 1. */
export const effectiveLoss = (s: Pick<UiShock, 'kind' | 'loss'>): number => (s.kind === 'closure' ? 1 : s.loss);

/** The smallest unused id of the form s1, s2, ... */
export function nextId(shocks: readonly Pick<UiShock, 'id'>[]): string {
  const used = new Set(shocks.map((s) => s.id));
  for (let n = 1; ; n++) if (!used.has(`s${n}`)) return `s${n}`;
}

export interface ShockTemplate { kind: ShockKind; startD: number; durationD: number; loss: number }
export const templateFor = (kind: ShockKind, startD = 0): ShockTemplate => ({ kind, startD, ...KIND_DEFAULTS[kind] });

export function makeShock(shocks: readonly UiShock[], placeId: string, t: ShockTemplate): UiShock {
  return { id: nextId(shocks), placeId, kind: t.kind, startD: r4(clamp(t.startD, 0, OFFSET_MAX_D)), durationD: r4(clamp(t.durationD, 0.01, DURATION_MAX_D)), loss: clamp(t.loss, 0.01, 1), shape: 'step', note: '' };
}

/** Add one shock; refused (unchanged) at the cap. */
export function addShock(doc: BuilderDoc, placeId: string, t: ShockTemplate): BuilderDoc {
  if (doc.shocks.length >= MAX_SHOCKS) return doc;
  return { ...doc, shocks: [...doc.shocks, makeShock(doc.shocks, placeId, t)] };
}
/** One shock per place, same template, up to the cap. Places that already have a row are skipped when `skipExisting`. */
export function addShocksForPlaces(doc: BuilderDoc, placeIds: readonly string[], t: ShockTemplate, skipExisting = false): BuilderDoc {
  let cur = doc;
  const have = new Set(doc.shocks.map((s) => s.placeId));
  for (const p of placeIds) {
    if (skipExisting && have.has(p)) continue;
    cur = addShock(cur, p, t);
  }
  return cur;
}
export const removeShock = (doc: BuilderDoc, id: string): BuilderDoc => ({ ...doc, shocks: doc.shocks.filter((s) => s.id !== id) });
export function duplicateShock(doc: BuilderDoc, id: string): BuilderDoc {
  const i = doc.shocks.findIndex((s) => s.id === id);
  if (i < 0 || doc.shocks.length >= MAX_SHOCKS) return doc;
  const copy: UiShock = { ...doc.shocks[i], id: nextId(doc.shocks) };
  return { ...doc, shocks: [...doc.shocks.slice(0, i + 1), copy, ...doc.shocks.slice(i + 1)] };
}
export function moveRow(doc: BuilderDoc, id: string, dir: -1 | 1): BuilderDoc {
  const i = doc.shocks.findIndex((s) => s.id === id);
  const j = i + dir;
  if (i < 0 || j < 0 || j >= doc.shocks.length) return doc;
  const next = doc.shocks.slice();
  [next[i], next[j]] = [next[j], next[i]];
  return { ...doc, shocks: next };
}

export type ShockPatch = Partial<Pick<UiShock, 'placeId' | 'kind' | 'startD' | 'durationD' | 'loss' | 'shape' | 'note'>>;
/** Patch one shock. Kind changes keep the user's numbers; a closure shows loss 1. */
export function updateShock(doc: BuilderDoc, id: string, patch: ShockPatch): BuilderDoc {
  let changed = false;
  const shocks = doc.shocks.map((s) => {
    if (s.id !== id) return s;
    const next = { ...s, ...patch };
    if (next.kind === 'closure') next.loss = 1;
    if (JSON.stringify(next) !== JSON.stringify(s)) changed = true;
    return next;
  });
  return changed ? { ...doc, shocks } : doc;
}
/** The bulk edit: the same patch on every listed shock. A per-row edit afterwards overrides it. */
export function bulkUpdate(doc: BuilderDoc, ids: readonly string[], patch: ShockPatch): BuilderDoc {
  const set = new Set(ids);
  let cur = doc;
  for (const s of doc.shocks) if (set.has(s.id)) cur = updateShock(cur, s.id, patch);
  return cur;
}
export const setStarts = (doc: BuilderDoc, starts: Readonly<Record<string, number>>): BuilderDoc => ({
  ...doc, shocks: doc.shocks.map((s) => (s.id in starts ? { ...s, startD: r4(starts[s.id]) } : s)),
});

/* ── wire ───────────────────────────────────────────────────────────── */
export function toWire(doc: BuilderDoc): WireScenario {
  return {
    name: doc.name.trim().slice(0, NAME_MAX), start_at: `${doc.startDate}T00:00:00Z`, notes: '',
    shocks: doc.shocks.map((s): WireShock => ({
      id: s.id, place_id: s.placeId, kind: s.kind, start_offset_d: r4(s.startD), duration_d: r4(s.durationD),
      capacity_loss: effectiveLoss(s), shape: s.shape, note: s.note,
    })),
  };
}
/** The scenario body POST /scenarios/* takes (name and notes are optional there). */
export const wireBody = (doc: BuilderDoc): Record<string, unknown> => {
  const w = toWire(doc);
  return { name: w.name, start_at: w.start_at, shocks: w.shocks };
};
export function fromWire(w: Pick<WireScenario, 'name' | 'start_at' | 'shocks'>): BuilderDoc {
  const shocks: UiShock[] = [];
  for (const s of (w.shocks ?? []).slice(0, MAX_SHOCKS)) {
    if (!s || typeof s.id !== 'string' || !ID_RE.test(s.id) || typeof s.place_id !== 'string' || !isKind(s.kind)) continue;
    const num = (v: unknown, d: number) => (typeof v === 'number' && Number.isFinite(v) ? v : d);
    shocks.push({
      id: s.id, placeId: s.place_id, kind: s.kind, startD: r4(clamp(num(s.start_offset_d, 0), 0, OFFSET_MAX_D)),
      durationD: r4(clamp(num(s.duration_d, 7), 0.01, DURATION_MAX_D)), loss: clamp(num(s.capacity_loss, 0.5), 0.01, 1),
      shape: (SHAPES as readonly string[]).includes(s.shape) ? (s.shape as UiShock['shape']) : 'step',
      note: typeof s.note === 'string' ? s.note.slice(0, NOTE_MAX) : '',
    });
  }
  return { name: typeof w.name === 'string' ? w.name.slice(0, NAME_MAX) : '', startDate: typeof w.start_at === 'string' ? w.start_at.slice(0, 10) : DEFAULT_START_DATE, shocks };
}

/** Build the pending arrangement from the server's organise answer. */
export function pendingFrom(doc: OrganiseDoc, source: PendingArrangement['source'], text: string | null): PendingArrangement {
  return {
    rule: doc.rule, source, text,
    starts: Object.fromEntries(doc.shocks.map((s) => [s.id, s.start_offset_d])),
    diff: doc.diff, moved: doc.moved,
    overlapsBefore: doc.overlaps_before.length, overlapsAfter: doc.overlaps_after.length,
    spanBefore: doc.span_before.total_span_d, spanAfter: doc.span_after.total_span_d,
  };
}

/** Validate a stored draft: anything unusable becomes null. */
export function parseDraft(text: string | null): BuilderDoc | null {
  if (!text) return null;
  try {
    const o = JSON.parse(text) as Partial<BuilderDoc> & { shocks?: unknown };
    if (!o || typeof o !== 'object' || !Array.isArray(o.shocks)) return null;
    const wire = (o.shocks as UiShock[]).map((s) => ({
      id: s.id, place_id: s.placeId, kind: s.kind, start_offset_d: s.startD, duration_d: s.durationD,
      capacity_loss: s.loss, shape: s.shape, note: s.note,
    }));
    const doc = fromWire({ name: typeof o.name === 'string' ? o.name : '', start_at: `${typeof o.startDate === 'string' ? o.startDate : DEFAULT_START_DATE}T00:00:00Z`, shocks: wire as WireShock[] });
    return /^\d{4}-\d{2}-\d{2}$/.test(doc.startDate) ? doc : { ...doc, startDate: DEFAULT_START_DATE };
  } catch { return null; }
}
