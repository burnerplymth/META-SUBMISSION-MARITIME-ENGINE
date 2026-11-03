/* Pure logic for the aerial SAM desk (sub-metre survey scenes of ports).
 * No DOM, no fetch, no React. The server is /satwatch/aerial/* (server/satwatch/aerial_routes.py).
 *
 * Doctrine carried: results are MODELLED detections of ONE dated scene, "SAM found N", never a count of
 * ships ("Detections, not a census"); a recorded reply is never labelled live; a length that stops at the
 * scene border is a floor (printed with a "≥"); the picture shown is a reduced preview unless the native
 * crop is stored, and it says which. Chips and state words only. */

import { fmtUsdSmall } from '../../../format';

const isObj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
const str = (v: unknown): v is string => typeof v === 'string' && v.length > 0;
const num = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);

export const FRAMING_FALLBACK = 'Detections, not a census';
/** Every count wears this until a human has reviewed that place (nothing here is verified or minted). */
export const VERIFICATION_WORD = 'Detected, not verified';
export interface Attribution { chip: string; text: string; licence: string }

export interface AerialScene {
  scene_id: string; name: string; source: string; badge: string; acquisition_label: string | null; gsd_m: number;
  size_px: [number, number] | null; state: 'SNAPSHOT' | 'NOT FETCHED'; recorded_phrases: string[];
  image: 'native' | 'preview' | null; attribution: Attribution | null;
  /** The satellite desk anchorage this scene belongs to; null for a port the desk does not offer. */
  desk_place: string | null;
}
export interface PhraseSpec { phrase: string; label: string }
export interface ScenesDoc { framing: string; phrases: PhraseSpec[]; default_phrases: string[]; scenes: AerialScene[] }

const parseAttr = (v: unknown): Attribution | null =>
  isObj(v) && str(v.chip) && str(v.text) ? { chip: v.chip, text: v.text, licence: String(v.licence ?? '') } : null;

export function parseScenes(body: unknown): ScenesDoc | null {
  if (!isObj(body) || body.contract !== 'satwatch-aerial-scenes/1' || !Array.isArray(body.scenes)) return null;
  const scenes: AerialScene[] = [];
  for (const s of body.scenes) {
    if (!isObj(s) || !str(s.scene_id) || !str(s.name) || !str(s.source) || !str(s.badge) || !num(s.gsd_m)) continue;
    const sz = Array.isArray(s.size_px) && num(s.size_px[0]) && num(s.size_px[1]) ? ([s.size_px[0], s.size_px[1]] as [number, number]) : null;
    scenes.push({
      scene_id: s.scene_id, name: s.name, source: s.source, badge: s.badge, acquisition_label: str(s.acquisition_label) ? s.acquisition_label : null,
      gsd_m: s.gsd_m, size_px: sz, state: s.state === 'SNAPSHOT' ? 'SNAPSHOT' : 'NOT FETCHED',
      recorded_phrases: Array.isArray(s.recorded_phrases) ? s.recorded_phrases.filter(str) : [],
      image: s.image === 'native' || s.image === 'preview' ? s.image : null, attribution: parseAttr(s.attribution),
      desk_place: str(s.desk_place) ? s.desk_place : null,
    });
  }
  const phrases = Array.isArray(body.phrases) ? body.phrases.filter((p): p is Record<string, unknown> => isObj(p) && str(p.phrase) && str(p.label))
    .map((p) => ({ phrase: String(p.phrase), label: String(p.label) })) : [];
  return {
    framing: str(body.framing) ? body.framing : FRAMING_FALLBACK, phrases,
    default_phrases: Array.isArray(body.default_phrases) ? body.default_phrases.filter(str) : [], scenes,
  };
}

/** The scene a desk anchorage owns: the first one that lists the place and has at least one recorded reply. */
export const sceneForPlace = (scenes: readonly AerialScene[], place: string | null): AerialScene | null =>
  place ? scenes.find((s) => s.desk_place === place && s.recorded_phrases.length > 0) ?? null : null;
/** Default anchorage: the first one in desk order that owns an aerial scene (the recorded SAM boxes are then visible at once), else the first. */
export const preferredPlace = (placeIds: readonly string[], scenes: readonly AerialScene[]): string | null =>
  placeIds.find((id) => sceneForPlace(scenes, id) !== null) ?? placeIds[0] ?? null;
/** What the aerial section shows for the selected anchorage. 'scene' = its scene and recorded boxes; 'none' = nothing scanned there (a state chip, never a spinner). */
export const aerialPlaceState = (scenes: readonly AerialScene[], place: string | null): 'scene' | 'none' => (sceneForPlace(scenes, place) ? 'scene' : 'none');

/* ── the aerial source registry (GET /satwatch/aerial/sources) ───── */
export interface AerialSource { id: string; chip: string; label: string; gsd_m: number; coverage: string; year: number | null; licence: string }
export function parseSources(body: unknown): AerialSource[] | null {
  if (!isObj(body) || body.contract !== 'satwatch-aerial-sources/1' || !Array.isArray(body.sources)) return null;
  const out: AerialSource[] = [];
  for (const s of body.sources) {
    if (!isObj(s) || !str(s.id) || !str(s.chip) || !num(s.gsd_m)) continue;
    out.push({ id: s.id, chip: s.chip, label: str(s.label) ? s.label : s.chip, gsd_m: s.gsd_m, coverage: String(s.coverage ?? ''),
      year: num(s.year) ? s.year : null, licence: String(s.licence ?? '') });
  }
  return out;
}
/** "PDOK 25 CM · 25 cm": the chip plus its sampling distance. */
export const sourceWord = (s: Pick<AerialSource, 'chip' | 'gsd_m'>): string => `${s.chip} · ${Math.round(s.gsd_m * 100)} cm`;
/** Registry sources that have at least one scene, registry order; a scene whose source the registry does not list is still reachable under "All". */
export const sourcesWithScenes = (sources: readonly AerialSource[], scenes: readonly Pick<AerialScene, 'source'>[]): AerialSource[] =>
  sources.filter((s) => scenes.some((x) => x.source === s.id));

export interface AerialObject {
  id: string; phrase: string; cls: string; score: number; box: [number, number, number, number]; rings: number[][][] | null;
  length_m: number; width_m: number; floor: boolean; stitched: boolean; basis: string; lonlat: [number, number] | null;
}
export interface AerialResult {
  scene_id: string; name: string; badge: string; acquisition_label: string | null; gsd_m: number; size_px: [number, number];
  mode: 'RECORDED' | 'LIVE'; phrases: string[]; found: Record<string, number>; objects: AerialObject[]; framing: string;
  cost_usd: number; live: boolean; model: string | null; images: number; response_ids: string[]; tile_count: number; skipped_tiles: number; missing: string[]; attribution: Attribution | null;
}

export function parseResult(body: unknown): AerialResult | null {
  if (!isObj(body) || body.contract !== 'satwatch-aerial/1' || !str(body.scene_id) || !isObj(body.found) || !Array.isArray(body.objects)) return null;
  const sz = body.size_px;
  if (!Array.isArray(sz) || !num(sz[0]) || !num(sz[1])) return null;
  const found: Record<string, number> = {};
  for (const [k, v] of Object.entries(body.found)) if (num(v)) found[k] = v;
  const objects: AerialObject[] = [];
  for (const o of body.objects) {
    if (!isObj(o) || !str(o.id) || !str(o.phrase) || !num(o.score) || !Array.isArray(o.box_norm1000) || o.box_norm1000.length !== 4 || !o.box_norm1000.every(num)) continue;
    const ll = Array.isArray(o.centre_lonlat) && num(o.centre_lonlat[0]) && num(o.centre_lonlat[1]) ? ([o.centre_lonlat[0], o.centre_lonlat[1]] as [number, number]) : null;
    objects.push({
      id: o.id, phrase: o.phrase, cls: String(o.class ?? o.phrase), score: o.score, box: o.box_norm1000 as [number, number, number, number],
      rings: Array.isArray(o.rings) ? (o.rings as number[][][]) : null, length_m: num(o.length_m) ? o.length_m : 0, width_m: num(o.width_m) ? o.width_m : 0,
      floor: o.length_is_floor === true, stitched: o.stitched === true, basis: String(o.length_basis ?? ''), lonlat: ll,
    });
  }
  const prov = isObj(body.provenance) ? body.provenance : {};
  const tiles = Array.isArray(body.tiles) ? body.tiles : [];
  return {
    scene_id: body.scene_id, name: String(body.name ?? ''), badge: String(body.badge ?? ''), acquisition_label: str(body.acquisition_label) ? body.acquisition_label : null,
    gsd_m: num(body.gsd_m) ? body.gsd_m : 0, size_px: [sz[0], sz[1]], mode: body.mode === 'LIVE' ? 'LIVE' : 'RECORDED',
    phrases: Array.isArray(body.phrases) ? body.phrases.filter(str) : [], found, objects, framing: str(body.framing) ? body.framing : FRAMING_FALLBACK,
    cost_usd: num(prov.cost_usd) ? prov.cost_usd : 0, live: prov.live === true && body.mode === 'LIVE',
    model: body.mode === 'LIVE' && str(prov.model) ? prov.model : null, images: num(prov.images_processed) ? prov.images_processed : 0,
    response_ids: Array.isArray(prov.response_ids) ? prov.response_ids.filter(str) : [], tile_count: tiles.length,
    skipped_tiles: tiles.filter((t) => isObj(t) && t.skipped === true).length,
    missing: Array.isArray(body.missing_phrases) ? body.missing_phrases.filter(str) : [], attribution: parseAttr(body.attribution),
  };
}

export interface AerialPlan {
  scene_id: string; est_cost_usd: number; calls: number; tiles: number; live_state: string; crop_stored: boolean;
  recorded_phrases: string[]; missing_phrases: string[]; remaining_calls: number | null; cap_reached: boolean; disabled: boolean;
}
export function parsePlan(body: unknown): AerialPlan | null {
  if (!isObj(body) || body.contract !== 'satwatch-aerial/1' || !str(body.scene_id) || !num(body.est_cost_usd) || !isObj(body.live) || !isObj(body.recorded)) return null;
  const u = isObj(body.usage) ? body.usage : {};
  return {
    scene_id: body.scene_id, est_cost_usd: body.est_cost_usd, calls: num(body.live.calls) ? body.live.calls : 0,
    tiles: Array.isArray(body.tiles) ? body.tiles.filter((t) => isObj(t) && t.skipped !== true).length : 0,
    live_state: String(body.live.state ?? 'NOT FETCHED'), crop_stored: body.live.crop_stored === true,
    recorded_phrases: Array.isArray(body.recorded.phrases) ? body.recorded.phrases.filter(str) : [],
    missing_phrases: Array.isArray(body.recorded.missing) ? body.recorded.missing.filter(str) : [],
    remaining_calls: num(u.remaining_calls_today) ? u.remaining_calls_today : null, cap_reached: u.cap_reached === true, disabled: u.disabled === true,
  };
}

/* ── words ───────────────────────────────────────────────────────── */
export const lengthWord = (o: Pick<AerialObject, 'length_m' | 'floor'>): string => `${o.floor ? '≥ ' : ''}${Math.round(o.length_m)} m`;
export const scoreWord = (s: number): string => s.toFixed(2);
export const foundWord = (n: number): string => (n === 0 ? 'SAM found none' : `SAM found ${n}`);
export const usd = (n: number): string => fmtUsdSmall(n);
export const confirmWord = (p: AerialPlan): string => `Est. ${usd(p.est_cost_usd)} · ${p.calls} ${p.calls === 1 ? 'call' : 'calls'}`;

/** The badge phrase for the first phrase: "found none" only for a served count of 0. */
export function foundDid(r: AerialResult): string {
  const n = r.found[r.phrases[0]];
  return n === undefined ? 'count not served' : n === 0 ? 'found none' : `found ${n}`;
}

export interface FoundChip { phrase: string; word: string }
/** One "SAM found N" chip per phrase the result carries (never a sum across phrases). */
export function foundChips(r: AerialResult, label: (p: string) => string): FoundChip[] {
  return r.phrases.map((p) => ({ phrase: p, word: `${r.found[p] === undefined ? 'SAM count not served' : foundWord(r.found[p])} · ${label(p).toLowerCase()}` }));
}

/** Table rows: phrase order of the result, then confidence; `index` is the tag number drawn on the picture. */
export function objectRows(r: AerialResult, shown: ReadonlySet<string>): (AerialObject & { index: number })[] {
  const order = new Map(r.phrases.map((p, i) => [p, i]));
  return r.objects.filter((o) => shown.has(o.phrase))
    .sort((a, b) => (order.get(a.phrase) ?? 9) - (order.get(b.phrase) ?? 9) || b.score - a.score)
    .map((o, i) => ({ ...o, index: i + 1 }));
}

/** What the picture is: a reduced preview or the native crop (the server says which). */
export function imageChip(kind: AerialScene['image']): { chip: 'SNAPSHOT' | 'INFO' | 'NOT SERVED'; word: string; title: string } {
  if (kind === 'native') return { chip: 'INFO', word: 'Native pixels', title: 'Stored crop' };
  if (kind === 'preview') return { chip: 'INFO', word: 'Reduced preview', title: 'Display only' };
  return { chip: 'NOT SERVED', word: 'No picture', title: '' };
}

export const exportHref = (base: string, scene: string, phrases: readonly string[], fmt: 'geojson' | 'csv'): string => {
  const q = new URLSearchParams({ scene, fmt });
  if (phrases.length) q.set('phrases', phrases.join(','));
  return `${base}/satwatch/aerial/export?${q.toString()}`;
};

/** Failure words for the error codes the routes serve (chips, never a stack trace). */
export function failureWord(code: string): { chip: 'NOT SERVED' | 'FLAG' | 'INFO'; word: string } {
  switch (code) {
    case 'unauthorized': return { chip: 'FLAG', word: 'Operator token needed' };
    case 'not_recorded': return { chip: 'NOT SERVED', word: 'No recorded reply' };
    case 'crop_not_stored': return { chip: 'NOT SERVED', word: 'Native crop not fetched' };
    case 'feed_down': return { chip: 'NOT SERVED', word: 'Imagery feed not served' };
    case 'no_scene': return { chip: 'NOT SERVED', word: 'No scene' };
    case 'sam_cap': return { chip: 'FLAG', word: 'Daily SAM cap reached' };
    case 'awaiting_meta_key': return { chip: 'NOT SERVED', word: 'Awaiting key' };
    case 'cost_not_confirmed': return { chip: 'FLAG', word: 'Cost not confirmed' };
    case 'scan_busy': return { chip: 'INFO', word: 'Busy' };
    case 'sam_call_failed': case 'sam_bad_reply': return { chip: 'NOT SERVED', word: 'SAM call failed' };
    case 'no_data': return { chip: 'NOT SERVED', word: 'No data in scene' };
    default: return { chip: 'NOT SERVED', word: 'Not served' };
  }
}

/* ── result precedence and call accounting ───────────────────────── */
export type ResState = { s: 'idle' } | { s: 'loading' } | { s: 'ok'; v: AerialResult } | { s: 'err'; code: string };

/** A recorded replay never replaces a live result for the same scene: the live reply is the newer, spent-for answer. */
export function acceptRecorded(cur: ResState, incoming: AerialResult): ResState {
  if (cur.s === 'ok' && cur.v.mode === 'LIVE' && cur.v.scene_id === incoming.scene_id) return cur;
  return { s: 'ok', v: incoming };
}

export interface AerialCallEntry { did: string; key: string; provenance: Record<string, unknown> }
/** One session-log entry per SAM image call of a live result (the ledger counts images); none for a recorded one. */
export function liveCallEntries(r: AerialResult, runId: string): AerialCallEntry[] {
  if (r.mode !== 'LIVE' || !r.live || r.images <= 0) return [];
  const per = r.cost_usd / r.images;
  const did = r.phrases.length === 1 ? foundWord(r.found[r.phrases[0]] ?? 0) : `SAM, ${r.phrases.length} phrases`;
  return Array.from({ length: r.images }, (_, i) => ({
    did, key: `aerial:${runId}:${i}`,
    provenance: { model: r.model ?? 'sam-3.1', transport: 'LIVE', transport_raw: 'http', live: true, cost_usd: Number.isFinite(per) ? per : 0, cached: false },
  }));
}

/** The state chip beside Run SAM, in priority order; the crop prerequisite is named, never a silent disabled button. */
export function runStateChip(o: { operator: boolean; cropStored: boolean; capReached: boolean; disabled: boolean }): { chip: 'FLAG' | 'INFO' | 'NOT SERVED'; word: string } {
  if (!o.operator) return { chip: 'FLAG', word: 'Operator token needed' };
  if (o.capReached) return { chip: 'NOT SERVED', word: 'Daily cap reached' };
  if (o.disabled) return { chip: 'NOT SERVED', word: 'SAM disabled' };
  if (!o.cropStored) return { chip: 'NOT SERVED', word: 'Fetch the crop first' };
  return { chip: 'INFO', word: 'Live ready' };
}
