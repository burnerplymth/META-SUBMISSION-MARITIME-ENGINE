/* LIVE terminal — pure model (feeds, entities, counts). Hook-free.
 * Doctrine: absent data is NOT SERVED (null counts, never zero); a feed state is one of LIVE /
 * SNAPSHOT / DOWN / NOT SERVED and is never inferred; simulated
 * entities never appear here (these are served live records only). */

import type { ChipKind } from '../ports/model';
import { liveChipKind } from '../../shell/truthLabel';
import { labelWord } from '../../copy';

export interface Provenance { source: string; retrieved_at: string; mode: string }

export interface HealthFeed {
  id: string; label: string; topic?: string; state: string; mode?: string | null; source?: string;
  retrieved_at?: string | null; age_s?: number | null; max_age_s?: number | null; fresh?: boolean; records?: number | null;
  note?: string | null; adapter_state?: string; rejected?: number;
}
export interface HealthDoc {
  feeds: HealthFeed[];
  hub?: { clients?: number; dropped_total?: number; held?: Record<string, number>; memory_estimate_bytes?: number };
  poller?: { polls: number; interval_s: number; last_error: string | null };
}

export interface Columns {
  id: string[]; lat: number[]; lon: number[]; spd_kn?: Array<number | null>; hdg?: Array<number | null>;
  t?: number[]; age_s?: number[]; name?: Array<string | null>; alt_m?: Array<number | null>; p?: number[];
}
export interface EntitiesDoc {
  kind: string; generated_at: string; served: boolean; status: string; reason?: string | null;
  feed?: { id: string; state: string; retrieved_at?: string | null; age_s?: number | null; note?: string | null };
  held?: number; matched?: number; count?: number; truncated?: boolean;
  columns: Partial<Columns>; provenance?: Provenance[];
}
export interface HubItem { node_id: string; aircraft_in_bbox: number; observed_at: string; age_s: number; recent: number[]; p?: number }
export interface SnapshotDoc {
  generated_at: string; epoch: string; seq: number; last_event_id: string; topics: string[];
  health?: HealthDoc;
  vessels?: { served: boolean; status: string; reason?: string | null; count?: number; total?: number; truncated?: boolean; columns?: Partial<Columns>; provenance?: Provenance[] };
  aircraft?: { served: boolean; status: string; reason?: string | null; total?: number; columns?: Partial<Columns>; hubs?: { served: boolean; status: string; items: HubItem[]; provenance?: Provenance[] }; provenance?: Provenance[] };
  ports?: { served: boolean; status: string; total?: number; reason?: string | null; provenance?: Provenance[] };
  borders?: { served?: boolean; status?: string; total?: number; items?: unknown[]; reason?: string | null; provenance?: Provenance[] };
  events?: { served: boolean; status: string; total?: number; count?: number; reason?: string | null; items?: SnapshotEvent[]; provenance?: Provenance[] };
}
export interface SnapshotEvent {
  id: string; node_id: string; kind: string; severity: number; status: string; title: string;
  detected_at: string; started_at?: string | null; expires_at?: string | null; evidence_url?: string | null; citation?: unknown; p?: number;
}

/* ── feed strip ─────────────────────────────────────────────────────── */

export type FeedStripState = 'LIVE' | 'SNAPSHOT' | 'DOWN' | 'NOT SERVED';
export interface FeedStripEntry {
  id: string; label: string; state: FeedStripState; chip: ChipKind; retrievedAt: string | null; records: number | null; title: string;
}

/** The feeds the strip names, in the order they are shown. */
export const STRIP_ORDER = ['opensky', 'aisstream', 'eia', 'comtrade', 'gdacs', 'noaa', 'cbp'] as const;
const STRIP_LABEL: Record<string, string> = {
  opensky: 'OpenSky', aisstream: 'AIS', eia: 'EIA', comtrade: 'Comtrade', gdacs: 'GDACS', noaa: 'NOAA', cbp: 'CBP', portwatch: 'PortWatch',
};

/** Registry credit ids for the feeds the strip names. */
export const STRIP_CREDIT: Readonly<Record<string, string>> = {
  opensky: 'opensky', aisstream: 'aisstream', eia: 'eia', comtrade: 'un-comtrade', gdacs: 'gdacs', noaa: 'noaa-nws', cbp: 'cbp', portwatch: 'imf-portwatch',
};

/* LIVE here is the served state, not a client decision: the server is the single authority
 * (live/hub.py demotes by age, adapters/registry.py applies a read-time freshness rule). */
export function normState(raw: string | null | undefined): FeedStripState {
  const s = String(raw ?? '').toUpperCase();
  return s === 'LIVE' || s === 'SNAPSHOT' || s === 'NOT SERVED' ? (s as FeedStripState) : 'DOWN';
}

/** Age text: seconds under a minute, minutes under 2 h, else hours/days. */
export function ageText(sec: number | null | undefined): string {
  if (sec === null || sec === undefined || !Number.isFinite(sec) || sec < 0) return '';
  if (sec < 60) return `${Math.round(sec)}s`;
  if (sec < 7200) return `${Math.round(sec / 60)}m`;
  if (sec < 172800) return `${Math.round(sec / 3600)}h`;
  return `${Math.round(sec / 86400)}d`;
}

export interface ContextIndexFeed { feed: string; label?: string; state: string; mode?: string | null; last_success_at?: string | null; note?: string | null; count?: number; provenance?: Provenance | null }

/** Merge GET /live/health feeds with GET /feeds (EIA, Comtrade) into one
 *  strip. A feed that neither answered is simply not in the strip (it is
 *  not invented as DOWN here). PortWatch is shown after the named seven. */
export function mergeFeedStrip(health: HealthFeed[] | null | undefined, context: ContextIndexFeed[] | null | undefined): FeedStripEntry[] {
  const out = new Map<string, FeedStripEntry>();
  for (const f of health ?? []) {
    const state = normState(f.state);
    out.set(f.id, {
      id: f.id, label: STRIP_LABEL[f.id] ?? labelWord(f.label), state, chip: stateChip(state),
      retrievedAt: f.retrieved_at ?? null, records: typeof f.records === 'number' ? f.records : null,
      title: [f.label, f.retrieved_at ? `retrieved ${f.retrieved_at}` : null, f.age_s != null ? `age ${ageText(f.age_s)}` : null,
        typeof f.records === 'number' ? `${f.records} records` : null, f.note].filter(Boolean).join(' · '),
    });
  }
  for (const f of context ?? []) {
    const state = normState(f.state);
    out.set(f.feed, {
      id: f.feed, label: STRIP_LABEL[f.feed] ?? labelWord(f.feed), state, chip: stateChip(state),
      retrievedAt: f.provenance?.retrieved_at ?? f.last_success_at ?? null, records: typeof f.count === 'number' ? f.count : null,
      title: [f.label, f.provenance?.retrieved_at ? `retrieved ${f.provenance.retrieved_at}` : null, typeof f.count === 'number' ? `${f.count} records` : null, f.note].filter(Boolean).join(' · '),
    });
  }
  const named = STRIP_ORDER.filter((k) => out.has(k)).map((k) => out.get(k)!);
  const rest = [...out.values()].filter((e) => !(STRIP_ORDER as readonly string[]).includes(e.id)).sort((a, b) => a.id.localeCompare(b.id));
  return [...named, ...rest];
}

export function stateChip(s: FeedStripState): ChipKind { return s === 'LIVE' ? 'LIVE' : s === 'SNAPSHOT' ? 'SNAPSHOT' : s === 'NOT SERVED' ? 'NOT SERVED' : 'DOWN'; }

/** A feed is "down" for hiding purposes when it is DOWN or NOT SERVED. */
export const feedIsDown = (e: FeedStripEntry | undefined): boolean => !e || e.state === 'DOWN' || e.state === 'NOT SERVED';

/* ── counts by kind ─────────────────────────────────────────────────── */

export interface KindCount {
  kind: string; label: string; count: number | null; chip: ChipKind; unit: string; title: string;
  /** True for tracked-entity counts: reports received, not a census. */
  sample: boolean;
}

function chipOfStatus(served: boolean | undefined, status: string | undefined): ChipKind {
  if (!served) return 'NOT SERVED';
  const s = String(status ?? '').toUpperCase();
  return s === 'LIVE' ? 'LIVE' : s === 'SNAPSHOT' ? 'SNAPSHOT' : 'NOT SERVED';
}

/** One count per kind, each its own unit — never summed with another.
 *  A kind that is not served has a null count (rendered NOT SERVED). */
export function countsByKind(s: SnapshotDoc | null | undefined): KindCount[] {
  if (!s) return [];
  const out: KindCount[] = [];
  const v = s.vessels;
  out.push({ kind: 'vessel', label: 'Vessels', count: v?.served && typeof v.total === 'number' ? v.total : null, chip: chipOfStatus(v?.served, v?.status), unit: 'held', title: labelWord(v?.reason ?? ''), sample: true });
  const a = s.aircraft;
  const hubs = a?.hubs;
  out.push({ kind: 'aircraft', label: 'Aircraft', count: a?.served && typeof a.total === 'number' ? a.total : null, chip: chipOfStatus(a?.served, a?.status), unit: 'held', title: labelWord(a?.reason ?? ''), sample: true });
  out.push({ kind: 'air_hub', label: 'Air hubs', count: hubs?.served ? hubs.items.length : null, chip: chipOfStatus(hubs?.served, hubs?.status), unit: 'hubs', title: '', sample: false });
  const e = s.events;
  out.push({ kind: 'event', label: 'Events', count: e?.served && typeof e.total === 'number' ? e.total : null, chip: chipOfStatus(e?.served, e?.status), unit: 'held', title: labelWord(e?.reason ?? ''), sample: false });
  const b = s.borders;
  const bn = b?.served === false ? null : typeof b?.total === 'number' ? b.total : Array.isArray(b?.items) ? b!.items!.length : null;
  out.push({ kind: 'border', label: 'Border lanes', count: bn, chip: chipOfStatus(b?.served ?? bn !== null, b?.status ?? (bn !== null ? 'LIVE' : undefined)), unit: 'lanes', title: labelWord(b?.reason ?? ''), sample: false });
  const p = s.ports;
  out.push({ kind: 'port', label: 'Ports', count: p?.served && typeof p.total === 'number' ? p.total : null, chip: chipOfStatus(p?.served, p?.status), unit: 'places', title: labelWord(p?.reason ?? ''), sample: false });
  return out;
}

/* ── entity rows ────────────────────────────────────────────────────── */

export interface EntityRow {
  id: string; name: string; lat: number; lon: number; spd: number | null; hdg: number | null; alt: number | null;
  t: number | null; ageS: number | null; mode: string; source: string; retrievedAt: string | null;
}
export type EntitySort = 'age' | 'name' | 'id' | 'spd' | 'lat' | 'lon';

/** Columnar payload to rows. Arrays of unequal length are cut to the
 *  shortest identity/position column so a malformed payload cannot make
 *  a row with a missing coordinate. */
export function entityRows(doc: { columns?: Partial<Columns>; provenance?: Provenance[] } | null | undefined): EntityRow[] {
  const c = doc?.columns;
  if (!c || !Array.isArray(c.id) || !Array.isArray(c.lat) || !Array.isArray(c.lon)) return [];
  const n = Math.min(c.id.length, c.lat.length, c.lon.length);
  const prov = doc?.provenance ?? [];
  const rows: EntityRow[] = [];
  for (let i = 0; i < n; i++) {
    const pr = prov[c.p?.[i] ?? 0];
    rows.push({
      id: String(c.id[i]), name: c.name?.[i] ? String(c.name[i]) : '', lat: c.lat[i], lon: c.lon[i],
      spd: c.spd_kn?.[i] ?? null, hdg: c.hdg?.[i] ?? null, alt: c.alt_m?.[i] ?? null,
      t: c.t?.[i] ?? null, ageS: c.age_s?.[i] ?? null, mode: pr?.mode ?? '', source: pr?.source ?? '', retrievedAt: pr?.retrieved_at ?? null,
    });
  }
  return rows;
}

/** LIVE only for a LIVE record inside its freshness window at `now` (truth-label law). */
export function entityChip(r: EntityRow, now: number): ChipKind {
  return liveChipKind(r.mode, r.retrievedAt, r.source, now);
}

export function filterEntities(rows: EntityRow[], q: string): EntityRow[] {
  const t = q.trim().toLowerCase();
  return t ? rows.filter((r) => r.id.toLowerCase().includes(t) || r.name.toLowerCase().includes(t)) : rows;
}

/** Sort a copy; rows with no value for the key always sort last, ties by id. */
export function sortEntities(rows: EntityRow[], key: EntitySort, order: 'asc' | 'desc'): EntityRow[] {
  const val = (r: EntityRow): number | string | null =>
    key === 'age' ? r.ageS : key === 'name' ? (r.name || null) : key === 'id' ? r.id : key === 'spd' ? r.spd : key === 'lat' ? r.lat : r.lon;
  const present = rows.filter((r) => val(r) !== null);
  const absent = rows.filter((r) => val(r) === null).sort((a, b) => a.id.localeCompare(b.id));
  const dir = order === 'asc' ? 1 : -1;
  present.sort((a, b) => {
    const x = val(a)!, y = val(b)!;
    const c = typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y));
    return c !== 0 ? c * dir : a.id.localeCompare(b.id);
  });
  return [...present, ...absent];
}

export function fmtCoord(v: number, axis: 'lat' | 'lon'): string {
  const h = axis === 'lat' ? (v >= 0 ? 'N' : 'S') : (v >= 0 ? 'E' : 'W');
  return `${Math.abs(v).toFixed(3)}${h}`;
}
export const fmtSpeed = (v: number | null): string => (v === null ? '' : v.toFixed(1));
export const fmtHeading = (v: number | null): string => (v === null ? '' : `${String(Math.round(v)).padStart(3, '0')}°`);

export function nextEntitySort(cur: { key: EntitySort; order: 'asc' | 'desc' }, key: EntitySort): { key: EntitySort; order: 'asc' | 'desc' } {
  if (cur.key === key) return { key, order: cur.order === 'asc' ? 'desc' : 'asc' };
  return { key, order: key === 'name' || key === 'id' || key === 'age' ? 'asc' : 'desc' };
}

/** GET /live/entities query: names always, bounded limit. */
export function entitiesQuery(kind: 'vessel' | 'aircraft', limit = 200): string {
  return `kind=${kind}&limit=${Math.max(1, Math.min(20000, limit))}&names=1`;
}

/** GET /live/snapshot for the counts: counts only (no entity columns). */
export const SNAPSHOT_COUNTS_QUERY = 'topics=events,vessels,aircraft,ports,borders,health&entity_limit=0&events_limit=0&ports_limit=0&borders_limit=0';

/* ── flag gate ──────────────────────────────────────────────────────── */

export type LiveGate = 'checking' | 'on' | 'off' | 'down';

/** The LIVE TWIN flag law at the panel boundary. `off` (nothing rendered,
 *  nothing requested) when the store has no flag, or /live/health answered
 *  the flag-off 404; `on` once health answered; `down` for any other
 *  failure (the panels then show DOWN chips rather than disappear). */
export function gateOf(o: { storeEnabled: boolean; healthStatus: number | null; haveHealth: boolean }): LiveGate {
  if (!o.storeEnabled) return 'off';
  if (o.healthStatus === 404) return 'off';
  if (o.haveHealth) return 'on';
  if (o.healthStatus !== null) return 'down';
  return 'checking';
}
