/* Pure logic for the multi-source satellite desk. The source
 * registry, per-place availability, stored Sentinel-1 / NAIP / Landsat
 * records, and the chip views the panel prints. No DOM, no fetch, no React.
 *
 * Doctrine carried:
 *  - the desk speaks in chips and state labels, never explanatory prose;
 *  - a Sentinel-1 count is a number of bright candidate targets, MODELLED, never
 *    SOURCED and never a vessel count; it prints as `BRIGHT TARGETS · N`;
 *  - a source whose feed is down is NOT SERVED, an empty catalogue is
 *    NO SCENE, a US-only source elsewhere is OUT OF COVER; nothing is faked;
 *  - a stored snapshot stays viewable when the catalogue is down;
 *  - ship-detection grade is a property of the SOURCE (what it could
 *    support), printed beside every source and never upgraded. */
import type { OverlayDetection } from './model';
import { labelWord, sentenceCase } from '../../copy';

export const SOURCE_IDS = ['sentinel-2', 'sentinel-1', 'landsat', 'naip'] as const;
export type SourceId = (typeof SOURCE_IDS)[number];
export const isSourceId = (v: unknown): v is SourceId => typeof v === 'string' && (SOURCE_IDS as readonly string[]).includes(v);

const isObj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
const str = (v: unknown): v is string => typeof v === 'string' && v.length > 0;
const num = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);

/* ── registry ─────────────────────────────────────────────────────── */
export interface SourceSpec {
  id: SourceId; chip: string; label: string; kind: string;
  resolution_m: number; revisit_label: string; coverage: string; cloud_filter: boolean;
  ship_detection_grade: string; ship_detection_label: string;
  licence: string; attribution: string; attribution_chip: string;
}
export interface SourcesDoc { contract: 'satwatch-sources/1'; mode: string; counts_basis: string; sources: SourceSpec[] }

export function parseSources(body: unknown): SourcesDoc | null {
  if (!isObj(body) || body.contract !== 'satwatch-sources/1' || !Array.isArray(body.sources)) return null;
  const out: SourceSpec[] = [];
  for (const s of body.sources) {
    if (!isObj(s) || !isSourceId(s.id) || !str(s.chip) || !str(s.label) || !num(s.resolution_m) || !str(s.ship_detection_label)) continue;
    out.push({
      id: s.id, chip: s.chip, label: s.label, kind: String(s.kind ?? ''), resolution_m: s.resolution_m,
      revisit_label: String(s.revisit_label ?? ''), coverage: String(s.coverage ?? ''), cloud_filter: s.cloud_filter === true,
      ship_detection_grade: String(s.ship_detection_grade ?? ''), ship_detection_label: s.ship_detection_label,
      licence: String(s.licence ?? ''), attribution: String(s.attribution ?? ''), attribution_chip: String(s.attribution_chip ?? ''),
    });
  }
  if (out.length === 0) return null;
  return { contract: 'satwatch-sources/1', mode: String(body.mode ?? ''), counts_basis: String(body.counts_basis ?? ''), sources: out };
}

/* ── per-place availability ───────────────────────────────────────── */
export type AvailState = 'AVAILABLE' | 'NO SCENE' | 'OUT OF COVER' | 'NOT SERVED';
export interface StoredSummary { date: string | null; retrieved_at: string | null; targets_floor: number | null; detector_status: string | null }
export interface AvailabilityRow {
  id: SourceId; chip: string; label: string; state: AvailState; error: string | null;
  scene: { id: string; date: string; cloud_pct_tile?: number | null } | null;
  stored: StoredSummary | null; ship_detection_grade: string; ship_detection_label: string; attribution_chip: string;
}
/** `catalogue` false: the stored-only answer (no live lookup yet); a source without a stored product then reads Not scanned, never Loading. */
export interface AvailabilityDoc { place: string; name: string; mode: string; catalogue: boolean; sources: AvailabilityRow[] }

const STATES: readonly string[] = ['AVAILABLE', 'NO SCENE', 'OUT OF COVER', 'NOT SERVED'];

export function parseAvailability(body: unknown): AvailabilityDoc | null {
  if (!isObj(body) || body.contract !== 'satwatch-sources/1' || !str(body.place) || !Array.isArray(body.sources)) return null;
  const rows: AvailabilityRow[] = [];
  for (const r of body.sources) {
    if (!isObj(r) || !isSourceId(r.id)) continue;
    const state: AvailState = STATES.includes(String(r.state)) ? (r.state as AvailState) : 'NOT SERVED';
    const sc = isObj(r.scene) && str(r.scene.id) && str(r.scene.date) ? { id: r.scene.id, date: r.scene.date, cloud_pct_tile: num(r.scene.cloud_pct_tile) ? r.scene.cloud_pct_tile : null } : null;
    const st = isObj(r.stored) ? {
      date: str(r.stored.date) ? r.stored.date : null, retrieved_at: str(r.stored.retrieved_at) ? r.stored.retrieved_at : null,
      targets_floor: num(r.stored.targets_floor) ? r.stored.targets_floor : null, detector_status: str(r.stored.detector_status) ? r.stored.detector_status : null,
    } : null;
    rows.push({
      id: r.id, chip: String(r.chip ?? r.id), label: String(r.label ?? r.id), state, error: str(r.error) ? r.error : null, scene: sc, stored: st,
      ship_detection_grade: String(r.ship_detection_grade ?? ''), ship_detection_label: String(r.ship_detection_label ?? ''), attribution_chip: String(r.attribution_chip ?? ''),
    });
  }
  return { place: body.place, name: String(body.name ?? ''), mode: String(body.mode ?? ''), catalogue: body.catalogue !== false, sources: rows };
}

/* ── chip row ─────────────────────────────────────────────────────── */
/** `chip` is a key the shared Chip component understands. */
export interface StateChip { chip: 'SNAPSHOT' | 'NOT SERVED' | 'INFO' | 'MODELLED' | 'FLAG'; word: string; title?: string }
export interface SourceChipView { id: SourceId; label: string; state: StateChip; selectable: boolean; grade: StateChip; attribution: string }

const md = (iso: string | null | undefined): string => (iso && iso.length >= 10 ? iso.slice(5, 10) : '');

/** Neutral for sources that can support a count, hatched for those that cannot. */
export function gradeChip(grade: string, label: string): StateChip {
  const none = grade === 'none' || grade === 'not_a_ship_source';
  return { chip: none ? 'NOT SERVED' : 'INFO', word: label || labelWord(grade).replace(/_/g, ' ') };
}

/** The state label for one source on one place.
 *  `av` is the availability doc, 'loading' while it is in flight, or 'err'
 *  when the catalogue answer is not available. A stored snapshot always
 *  wins: it is still a valid stored scene when the live catalogue is down. */
export function sourceChips(reg: SourcesDoc, av: AvailabilityDoc | 'loading' | 'err' | null): SourceChipView[] {
  return reg.sources.map((s) => {
    const row = av && av !== 'loading' && av !== 'err' ? av.sources.find((r) => r.id === s.id) ?? null : null;
    const grade = gradeChip(s.ship_detection_grade, s.ship_detection_label);
    let state: StateChip; let selectable = true;
    if (row?.stored?.date) {
      state = { chip: 'SNAPSHOT', word: `Snapshot ${md(row.stored.date)}` };
    } else if (av === 'loading' || av === null) {
      state = { chip: 'INFO', word: 'Loading' }; selectable = s.id === 'sentinel-2';
    } else if (av !== 'err' && !av.catalogue) {
      state = { chip: 'NOT SERVED', word: 'Not scanned' }; selectable = s.id === 'sentinel-2';
    } else if (av === 'err' || !row) {
      state = { chip: 'NOT SERVED', word: 'Not served' }; selectable = s.id === 'sentinel-2';
    } else if (row.state === 'AVAILABLE') {
      state = { chip: 'INFO', word: `Available ${md(row.scene?.date)}`.trim() };
    } else {
      state = { chip: 'NOT SERVED', word: sentenceCase(row.state) }; selectable = s.id === 'sentinel-2';
    }
    return { id: s.id, label: sentenceCase(s.chip) === 'Landsat' ? 'Landsat' : s.chip, state, selectable, grade, attribution: s.attribution_chip };
  });
}

/** A source can be fetched (admin) when the catalogue says a scene exists. */
export const canFetch = (row: AvailabilityRow | null): boolean => !!row && row.state === 'AVAILABLE';

/* ── stored records ───────────────────────────────────────────────── */
export interface SourceRecord {
  kind: 'satwatch_multi'; contract: string; source: SourceId; place: string; name: string; retrieved_at: string;
  imagery_mode: string; mode: string;
  scene: { id: string; date: string; platform?: string | null; cloud_pct_tile?: number | null; year?: string | null };
  crop: { file: string; url: string; px: [number, number]; pixel_spacing_m: number; window_m?: number | null };
  detector: { id: string | null; label: string | null; status: string; stats?: { sea_pct?: number; valid_pct?: number; sea_mask_mode?: string } };
  counts: { targets_floor: number } | null; count_basis: string | null;
  detections: OverlayDetection[];
  ship_detection_grade: string;
  attribution: { chip: string; text: string; licence: string };
  cover_pct?: number | null;
}

export function parseSourceRecord(body: unknown): SourceRecord | null {
  if (!isObj(body) || body.kind !== 'satwatch_multi' || !isSourceId(body.source) || !str(body.place)) return null;
  const scene = body.scene; const crop = body.crop; const det = body.detector; const attr = body.attribution;
  if (!isObj(scene) || !str(scene.id) || !str(scene.date)) return null;
  if (!isObj(crop) || !str(crop.url) || !Array.isArray(crop.px) || !num(crop.px[0]) || !num(crop.px[1])) return null;
  if (!isObj(det) || !str(det.status) || !isObj(attr) || !Array.isArray(body.detections)) return null;
  const counts = isObj(body.counts) && num(body.counts.targets_floor) ? { targets_floor: body.counts.targets_floor } : null;
  return {
    kind: 'satwatch_multi', contract: String(body.contract ?? ''), source: body.source, place: body.place, name: String(body.name ?? ''),
    retrieved_at: String(body.retrieved_at ?? ''), imagery_mode: String(body.imagery_mode ?? ''), mode: String(body.mode ?? ''),
    scene: { id: scene.id, date: scene.date, platform: str(scene.platform) ? scene.platform : null, cloud_pct_tile: num(scene.cloud_pct_tile) ? scene.cloud_pct_tile : null, year: str(scene.year) ? scene.year : null },
    crop: { file: String(crop.file ?? ''), url: crop.url, px: [crop.px[0], crop.px[1]], pixel_spacing_m: num(crop.pixel_spacing_m) ? crop.pixel_spacing_m : 0, window_m: num(crop.window_m) ? crop.window_m : null },
    detector: { id: str(det.id) ? det.id : null, label: str(det.label) ? det.label : null, status: det.status, stats: isObj(det.stats) ? (det.stats as SourceRecord['detector']['stats']) : undefined },
    counts, count_basis: str(body.count_basis) ? body.count_basis : null,
    detections: (body.detections as unknown[]).filter((d): d is OverlayDetection => isObj(d) && typeof d.id === 'string'),
    ship_detection_grade: String(body.ship_detection_grade ?? ''),
    attribution: { chip: String(attr.chip ?? ''), text: String(attr.text ?? ''), licence: String(attr.licence ?? '') },
    cover_pct: num(body.cover_pct) ? body.cover_pct : null,
  };
}

export interface RecordChip extends StateChip { key: string }

/** Chips for a stored record: provenance, floor count, detector, resolution,
 *  attribution. Pure; every word is a state label, none is a sentence. */
export function recordChips(rec: SourceRecord, spec: SourceSpec | null): RecordChip[] {
  const out: RecordChip[] = [];
  out.push({ key: 'snap', chip: 'SNAPSHOT', word: `Snapshot ${rec.scene.date}`, title: `${spec?.label ?? rec.source} · Stored scene` });
  if (rec.source === 'sentinel-1') {
    out.push({ key: 'modelled', chip: 'MODELLED', word: 'Modelled', title: 'Detector output · Not sourced' });
    if (rec.detector.status !== 'OK' || rec.counts == null) {
      out.push({ key: 'count', chip: 'NOT SERVED', word: rec.detector.status !== 'OK' ? labelWord(rec.detector.status) : 'Count not served' });
    } else {
      out.push({ key: 'count', chip: 'MODELLED', word: `Bright targets · ${rec.counts.targets_floor}`, title: 'Bright targets; quays and clutter may be included' });
    }
    out.push({ key: 'unconf', chip: 'INFO', word: 'Unconfirmed' });
    out.push({ key: 'det', chip: 'INFO', word: 'CFAR · VV', title: rec.detector.label ?? undefined });
    if (rec.detector.stats?.sea_pct != null) out.push({ key: 'sea', chip: 'INFO', word: `Sea ${Math.round(rec.detector.stats.sea_pct)}%`, title: 'Open water share' });
  } else {
    out.push({ key: 'nodet', chip: 'NOT SERVED', word: 'No detection' });
  }
  if (rec.crop.pixel_spacing_m > 0) out.push({ key: 'res', chip: 'INFO', word: `${rec.crop.pixel_spacing_m.toFixed(rec.crop.pixel_spacing_m < 10 ? 1 : 0)} m/px` });
  if (rec.scene.cloud_pct_tile != null) out.push({ key: 'cloud', chip: 'INFO', word: `Scene cloud ${Math.round(rec.scene.cloud_pct_tile)}%` });
  if (rec.cover_pct != null) out.push({ key: 'cover', chip: 'INFO', word: `Cover ${Math.round(rec.cover_pct)}%` });
  /* the ship-detection grade is stated once, in the source strip above; repeating it here printed it twice */
  if (rec.attribution.chip) out.push({ key: 'attr', chip: 'INFO', word: rec.attribution.chip, title: `${rec.attribution.text} · ${rec.attribution.licence}` });
  return out;
}


/** Admin fetch routes per source; the body is {place}. */
export const FETCH_ROUTE: Record<Exclude<SourceId, 'sentinel-2'>, string> = {
  'sentinel-1': '/satwatch/s1/scan', naip: '/satwatch/naip/crop', landsat: '/satwatch/landsat/crop',
};
export const RECORD_ROUTE: Record<Exclude<SourceId, 'sentinel-2'>, string> = {
  'sentinel-1': '/satwatch/s1', naip: '/satwatch/naip', landsat: '/satwatch/landsat',
};
export const fetchWord = (src: SourceId): string => (src === 'sentinel-1' ? 'Scan' : 'Fetch');
