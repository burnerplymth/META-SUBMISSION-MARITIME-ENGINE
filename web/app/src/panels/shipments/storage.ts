/* The shipment list lives in this browser only (localStorage, one key).
 * Reads are validated field by field; a blocked or corrupt store reads as an
 * empty list. Nothing here is sent anywhere: the list leaves the browser only
 * inside an evaluate or export request the person triggers. */
import { cleanText, MAX_ID, MAX_ROWS, MAX_TEXT, MAX_UNIT, readDate } from './csv';
import { SHIP_MODES, type ScenarioChoice, type Shipment } from './types';

export const STORE_KEY = 'supermap.shipments.v1';
export type ListOrigin = 'user' | 'sample';
export interface StoredList { rows: Shipment[]; origin: ListOrigin; scenario: ScenarioChoice }
export interface KV { get(key: string): string | null; set(key: string, value: string): void; remove(key: string): void }

const num = (v: unknown): number | undefined => (typeof v === 'number' && Number.isFinite(v) && v >= 0 && v <= 1e12 ? v : undefined);

export function sanitiseRow(r: unknown): Shipment | null {
  if (!r || typeof r !== 'object') return null;
  const o = r as Record<string, unknown>;
  const id = cleanText(o.id).slice(0, MAX_ID);
  const origin = cleanText(o.origin).slice(0, MAX_TEXT);
  const destination = cleanText(o.destination).slice(0, MAX_TEXT);
  if (!id || !origin || !destination) return null;
  const mode = SHIP_MODES.find((m) => m === o.mode) ?? 'sea';
  const s: Shipment = { id, origin, destination, mode };
  const etd = typeof o.etd === 'string' ? readDate(o.etd) : null; if (etd) s.etd = etd;
  const eta = typeof o.eta === 'string' ? readDate(o.eta) : null; if (eta) s.eta = eta;
  const v = num(o.value_usd); if (v !== undefined) s.value_usd = v;
  const q = num(o.quantity); if (q !== undefined) s.quantity = q;
  const u = cleanText(o.unit).slice(0, MAX_UNIT); if (u) s.unit = u;
  const via = cleanText(o.via).slice(0, MAX_TEXT); if (via) s.via = via;
  return s;
}

export function sanitiseList(rows: unknown): Shipment[] {
  if (!Array.isArray(rows)) return [];
  const seen = new Set<string>(); const out: Shipment[] = [];
  for (const r of rows) {
    const s = sanitiseRow(r);
    if (!s || seen.has(s.id)) continue;
    seen.add(s.id); out.push(s);
    if (out.length >= MAX_ROWS) break;
  }
  return out;
}

export function sanitiseScenario(v: unknown): ScenarioChoice {
  if (!v || typeof v !== 'object') return { kind: 'none' };
  const o = v as Record<string, unknown>;
  const dur = typeof o.duration === 'number' && o.duration >= 1 && o.duration <= 3650 ? o.duration : 14;
  const loss = typeof o.loss === 'number' && o.loss > 0 && o.loss <= 1 ? o.loss : null;
  if (o.kind === 'library' && typeof o.id === 'string' && /^[a-z0-9-]{1,64}$/.test(o.id)) return { kind: 'library', id: o.id, duration: dur, loss };
  if (o.kind === 'chain') return { kind: 'chain', duration: dur, loss };
  return { kind: 'none' };
}

export function loadList(kv: KV): StoredList {
  const empty: StoredList = { rows: [], origin: 'user', scenario: { kind: 'none' } };
  try {
    const raw = kv.get(STORE_KEY);
    if (!raw) return empty;
    const j = JSON.parse(raw) as Record<string, unknown>;
    if (!j || j.v !== 1) return empty;
    return { rows: sanitiseList(j.rows), origin: j.origin === 'sample' ? 'sample' : 'user', scenario: sanitiseScenario(j.scenario) };
  } catch { return empty; }
}

export function saveList(kv: KV, list: StoredList): void {
  try { kv.set(STORE_KEY, JSON.stringify({ v: 1, origin: list.origin, rows: list.rows, scenario: list.scenario })); } catch { /* storage full or blocked: the list stays in memory */ }
}

export function clearList(kv: KV): void { try { kv.remove(STORE_KEY); } catch { /* nothing to clear */ } }

/** The next free id of the form S-001 for a hand-entered row. */
export function nextId(rows: readonly Shipment[]): string {
  const used = new Set(rows.map((r) => r.id));
  let n = rows.length + 1;
  while (used.has(`S-${String(n).padStart(3, '0')}`)) n += 1;
  return `S-${String(n).padStart(3, '0')}`;
}
