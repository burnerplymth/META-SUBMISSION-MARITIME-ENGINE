/* Pure logic for Meta AI on one NATIVE satellite crop: SAM 3.1
 * segmentation and the Muse Spark vision read. No DOM, no fetch, no React.
 *
 * Doctrine carried:
 *  - a SAM result reads "SAM found N" (never "there are N"); zero is "SAM
 *    found none". The count is MODELLED, display-only, and the server says so
 *    (`counts_feed_signals: false`);
 *  - RECORDED is a stored real reply for this exact crop; LIVE only when the
 *    result says mode LIVE AND its provenance agrees (any disagreement
 *    downgrades to Recorded, the safe direction);
 *  - a live run needs the live state READY, an operator token and the
 *    caller's confirmation of the shown estimate; nothing here sends it;
 *  - detector states are shown as the server states them: Awaiting key /
 *    Ready / Not served. Ready is never worded or toned as Live. */
import type { OverlayDetection } from './model';
import { fmtUsdSmall } from '../../format';

export type AiSource = 'sentinel-2' | 'naip' | 'landsat' | 'sentinel-1';
export type LiveState = 'AWAITING_META_KEY' | 'READY' | 'NOT_SERVED' | string;

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);
const fin = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);

/* ── plans ────────────────────────────────────────────────────────── */
export interface CropRef {
  source: string; place: string; role: 'now' | 'baseline' | null; name: string | null;
  crop_id: string; crop_url: string; crop_px: [number, number]; scene_date: string | null; resolution_m: number | null;
}
export interface LiveGate { state: LiveState; reason: string; admin_required: boolean }
export interface RecordedGate { available: boolean; recorded_at: string | null; model?: string | null }
export interface SamPlan extends CropRef {
  contract: 'satwatch-sam/1'; prompt: string; prompts: string[]; recorded: RecordedGate; live: LiveGate;
  images: number; est_cost_usd: number; price_per_1000_images_usd: number;
  usage: { calls_today: number; cap_calls_per_day: number; remaining_calls_today: number | null; cost_usd_today: number; cap_reached: boolean; disabled: boolean; ledger_ok: boolean };
}
export interface VisionPlan extends CropRef {
  contract: 'satwatch-vision/1'; recorded: RecordedGate; live: LiveGate; model: string;
  est_cost_usd: number; estimated_tokens_in: number; estimated_tokens_out: number;
  usage: { spent_usd: number; cap_usd: number | null; remaining_usd: number | null; calls_today: number; cap_reached: boolean; disabled: boolean };
}

function cropRef(b: Record<string, unknown>): boolean {
  return typeof b.source === 'string' && typeof b.place === 'string' && typeof b.crop_id === 'string' && typeof b.crop_url === 'string'
    && Array.isArray(b.crop_px) && b.crop_px.length === 2 && b.crop_px.every(fin);
}
const gate = (g: unknown): boolean => isObj(g) && typeof g.state === 'string' && typeof g.reason === 'string';
const rec = (g: unknown): boolean => isObj(g) && typeof g.available === 'boolean';

export function parseSamPlan(body: unknown): SamPlan | null {
  if (!isObj(body) || body.contract !== 'satwatch-sam/1' || !cropRef(body)) return null;
  if (!gate(body.live) || !rec(body.recorded) || !fin(body.est_cost_usd) || !isObj(body.usage) || typeof body.prompt !== 'string' || !Array.isArray(body.prompts)) return null;
  return body as unknown as SamPlan;
}
export function parseVisionPlan(body: unknown): VisionPlan | null {
  if (!isObj(body) || body.contract !== 'satwatch-vision/1' || !cropRef(body)) return null;
  if (!gate(body.live) || !rec(body.recorded) || !fin(body.est_cost_usd) || !isObj(body.usage)) return null;
  return body as unknown as VisionPlan;
}

/* ── results ──────────────────────────────────────────────────────── */
export interface SamObject extends OverlayDetection { long_side_px?: number; below_floor?: boolean | null }
export interface Provenance { model: string | null; transport: string; transport_raw?: string; live?: boolean; prompt_id?: string | null; cost_usd?: number; cached?: boolean; note?: string; [k: string]: unknown }
export interface SamResult extends CropRef {
  contract: 'satwatch-sam/1'; prompt: string; mode: 'RECORDED' | 'LIVE'; found: number; below_floor: number | null;
  objects: SamObject[]; label: string; image_basis: string; provenance: Provenance; counts_feed_signals: false;
}
export interface VisionFeature { label: string; detail: string }
export interface VisionRead {
  scene: string; image_quality: string; visible_features: VisionFeature[]; congestion_cues: string[]; congestion_level: string;
  confidence: number; cannot_see: string[]; limits: string[]; post_check: string[];
}
export interface VisionResult extends CropRef {
  contract: 'satwatch-vision/1'; mode: 'RECORDED' | 'LIVE'; read: VisionRead; label: string; image_basis: string; provenance: Provenance; counts_feed_signals: false;
}

export function parseSamResult(body: unknown): SamResult | null {
  if (!isObj(body) || body.contract !== 'satwatch-sam/1' || !cropRef(body)) return null;
  if ((body.mode !== 'RECORDED' && body.mode !== 'LIVE') || !fin(body.found) || !Array.isArray(body.objects) || !isObj(body.provenance)) return null;
  if (body.found !== body.objects.length) return null;
  if (body.counts_feed_signals !== false) return null;
  return body as unknown as SamResult;
}
export function parseVisionResult(body: unknown): VisionResult | null {
  if (!isObj(body) || body.contract !== 'satwatch-vision/1' || !cropRef(body)) return null;
  if ((body.mode !== 'RECORDED' && body.mode !== 'LIVE') || !isObj(body.read) || !isObj(body.provenance)) return null;
  const r = body.read;
  if (typeof r.scene !== 'string' || !Array.isArray(r.visible_features) || !Array.isArray(r.cannot_see) || !Array.isArray(r.limits) || !fin(r.confidence)) return null;
  if (body.counts_feed_signals !== false) return null;
  return body as unknown as VisionResult;
}

/* ── words ────────────────────────────────────────────────────────── */
export const usd = (n: number | null | undefined): string | null =>
  n == null || !Number.isFinite(n) ? null : fmtUsdSmall(n);
export const scoreWord = (s: number | null | undefined): string => (s == null || !Number.isFinite(s) ? '–' : s.toFixed(2));

/** "SAM found 15" / "SAM found none". Never "there are N". */
export const foundWord = (n: number): string => (n === 0 ? 'SAM found none' : `SAM found ${n}`);

export interface ModeChip { chip: 'LIVE' | 'SNAPSHOT'; word: 'Live' | 'Recorded' }
/** Live only when every signal agrees; the safe direction is Recorded. */
export function modeChip(r: { mode: string; provenance: Provenance }): ModeChip {
  const live = r.mode === 'LIVE' && r.provenance.transport === 'LIVE' && r.provenance.live === true && r.provenance.transport_raw !== 'fixture';
  return live ? { chip: 'LIVE', word: 'Live' } : { chip: 'SNAPSHOT', word: 'Recorded' };
}

export interface ObjectChip { id: string; index: number; word: string; score: number | null; belowFloor: boolean }
/** One chip per object, strongest first; the index is what the crop tag shows. */
export function objectChips(objects: readonly SamObject[]): ObjectChip[] {
  return objects
    .map((o, i) => ({ o, i }))
    .sort((a, b) => (b.o.score ?? -1) - (a.o.score ?? -1) || a.i - b.i)
    .map(({ o, i }) => ({ id: o.id, index: i + 1, word: scoreWord(o.score), score: o.score, belowFloor: o.below_floor === true }));
}

export interface GeomCensus { masks: number; boxes: number }
export function geomCensus(objects: readonly SamObject[]): GeomCensus {
  let masks = 0; let boxes = 0;
  for (const o of objects) {
    if (o.mask && o.mask.format === 'polygon' && o.mask.rings.length > 0) masks++;
    if (Array.isArray(o.bbox) && o.bbox.length === 4) boxes++;
  }
  return { masks, boxes };
}

export interface SamChips { found: string; label: string; mode: ModeChip; prompt: string; below: string | null; basis: string }
export function samChips(r: SamResult): SamChips {
  return {
    found: foundWord(r.found), label: 'Modelled', mode: modeChip(r), prompt: r.prompt,
    below: r.below_floor != null && r.below_floor > 0 ? `${r.below_floor} below size floor` : null,
    basis: 'Native pixels',
  };
}

/* ── the run gate (confirm before spending) ───────────────────────── */
export interface Gate {
  /** The run button is offered and enabled. */
  canRun: boolean;
  /** State chip beside the button, never prose. */
  chip: { chip: string; word: string; title?: string };
}
export function liveGate(live: LiveGate, opts: { operator: boolean; capReached?: boolean }): Gate {
  if (live.state === 'AWAITING_META_KEY') return { canRun: false, chip: { chip: 'NOT SERVED', word: 'Awaiting key', title: live.reason } };
  if (live.state === 'NOT_SERVED' || opts.capReached) return { canRun: false, chip: { chip: 'NOT SERVED', word: 'Not served', title: live.reason } };
  if (live.state !== 'READY') return { canRun: false, chip: { chip: 'NOT SERVED', word: 'Not served', title: live.reason } };
  if (!opts.operator) return { canRun: false, chip: { chip: 'INFO', word: 'Operator only', title: 'a live call needs the operator token' } };
  return { canRun: true, chip: { chip: 'INFO', word: 'Ready', title: live.reason } };
}

export interface ConfirmView { est: string; price: string | null; remaining: string | null; estNumber: number }
export function samConfirm(plan: SamPlan): ConfirmView {
  const rem = plan.usage.remaining_calls_today;
  return {
    est: usd(plan.est_cost_usd) ?? '–', estNumber: plan.est_cost_usd,
    price: `$${plan.price_per_1000_images_usd.toFixed(2)} / 1,000 images`,
    remaining: rem == null ? null : `${rem} of ${plan.usage.cap_calls_per_day} calls left today`,
  };
}
export function visionConfirm(plan: VisionPlan): ConfirmView {
  const u = plan.usage;
  return {
    est: usd(plan.est_cost_usd) ?? '–', estNumber: plan.est_cost_usd, price: null,
    remaining: u.remaining_usd == null ? null : `${usd(u.remaining_usd)} of ${usd(u.cap_usd)} left today`,
  };
}

/* ── detector rows (the selector) ─────────────────────────────────── */
export interface DetectorDocProvider {
  id: string; label: string; selected: boolean; state: string; note?: string; counts_are_real: boolean; offline_test: boolean;
  usage?: { calls_today: number; cap_calls_per_day: number; remaining_calls_today?: number; cost_usd_today: number; price_per_1000_images_usd: number; cap_reached: boolean };
}
export interface DetectorRow {
  id: string; label: string; selected: boolean; test: boolean;
  state: { chip: string; word: string; tone?: 'test' };
  extras: string[]; title: string | undefined;
}
const STATE_VIEW: Record<string, { chip: string; word: string; tone?: 'test' }> = {
  AWAITING_META_KEY: { chip: 'NOT SERVED', word: 'Awaiting key' },
  READY: { chip: 'INFO', word: 'Ready' },
  NOT_SERVED: { chip: 'NOT SERVED', word: 'Not served' },
  NOT_CONFIGURED: { chip: 'NOT SERVED', word: 'Not configured' },
  NOT_IMPLEMENTED: { chip: 'NOT SERVED', word: 'Not implemented' },
  OFFLINE_TEST: { chip: 'INFO', word: 'Offline test', tone: 'test' },
};
export const RECORDED_VIEW = { chip: 'SNAPSHOT', word: 'Recorded' } as const;
const LABEL: Record<string, string> = {
  none: 'No detector', 'reference-counts': 'Offline detector', 'meta-sam': 'SAM 3.1', 'open-weights-sam': 'Open-weights SAM',
};
export function detectorRows(providers: readonly DetectorDocProvider[]): DetectorRow[] {
  return providers.map((p) => {
    /* Without a key the desk replays stored SAM replies: the detector reads Recorded. Awaiting key belongs to the Run live action (liveGate). */
    const st = p.id === 'meta-sam' && p.state === 'AWAITING_META_KEY' ? RECORDED_VIEW : STATE_VIEW[p.state] ?? { chip: 'NOT SERVED', word: p.state.replace(/_/g, ' ').toLowerCase().replace(/^./, (c) => c.toUpperCase()) };
    const extras: string[] = [];
    if (p.usage) {
      extras.push(`Cap ${p.usage.cap_calls_per_day} / day`, `Calls ${p.usage.calls_today}`, `Est. ${usd(p.usage.cost_usd_today) ?? '–'}`,
        `$${p.usage.price_per_1000_images_usd.toFixed(2)} / 1,000 images`);
    }
    return { id: p.id, label: LABEL[p.id] ?? p.label, selected: p.selected, test: p.offline_test === true, state: st, extras, title: st === RECORDED_VIEW ? 'Replaying stored SAM replies' : p.note };
  });
}

/* ── failures ─────────────────────────────────────────────────────── */
export interface AiFailure { chip: 'NOT SERVED' | 'DOWN'; word: string }
/** A served error code as a state chip, never prose. */
export function aiFailure(code: string): AiFailure {
  switch (code) {
    case 'not_recorded': return { chip: 'NOT SERVED', word: 'No recorded reply' };
    case 'awaiting_meta_key': return { chip: 'NOT SERVED', word: 'Awaiting key' };
    case 'unauthorized': return { chip: 'NOT SERVED', word: 'Operator only' };
    case 'live_not_configured': return { chip: 'NOT SERVED', word: 'Live not enabled' };
    case 'sam_cap': case 'llm_cap': return { chip: 'NOT SERVED', word: 'Cap reached' };
    case 'llm_disabled': return { chip: 'NOT SERVED', word: 'Disabled' };
    case 'sam_not_offered': case 'bad_source': return { chip: 'NOT SERVED', word: 'Not offered' };
    case 'crop_not_found': return { chip: 'NOT SERVED', word: 'Crop not served' };
    case 'sam_call_failed': case 'vision_call_failed': case 'network': case 'timeout': return { chip: 'DOWN', word: 'Call failed' };
    case 'sam_bad_reply': case 'vision_bad_reply': case 'recorded_unreadable': return { chip: 'DOWN', word: 'Reply refused' };
    case 'cost_not_confirmed': return { chip: 'DOWN', word: 'Cost not confirmed' };
    default: return { chip: 'DOWN', word: 'Error' };
  }
}

export const qualityWord = (q: string): string => q.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());

/* ── the vision read as chips ─────────────────────────────────────── */
/** Longest chip text, characters, before it is cut; the full text is the chip title. */
export const CHIP_MAX = 34;
/** Chips shown per field; the rest are counted. */
export const FIELD_MAX = 6;
export interface ReadChip { text: string; full: string | null }
export interface ChipField { key: 'features' | 'cues' | 'cannot' | 'limits'; label: string; chips: ReadChip[]; more: number }

const cap1 = (t: string): string => t.charAt(0).toUpperCase() + t.slice(1);
/** The model's phrase as a short chip: first clause, capitalised, cut at a word. */
export function shortRead(t: string, full?: string): ReadChip {
  const clause = t.split(/[,;]| - /)[0].trim().replace(/[.:]+$/, '');
  let text = cap1(clause);
  if (text.length > CHIP_MAX) text = `${text.slice(0, CHIP_MAX - 1).replace(/\s+\S*$/, '')}…`;
  const whole = (full ?? t).trim();
  return { text, full: whole === text ? null : whole };
}

/** The Muse Spark read as labelled fields of short chips. The model's text is
 *  data: it is kept whole in the chip titles and in the Read disclosure. */
export function visionFields(r: VisionRead): ChipField[] {
  const mk = (key: ChipField['key'], label: string, chips: ReadChip[]): ChipField => ({ key, label, chips: chips.slice(0, FIELD_MAX), more: Math.max(0, chips.length - FIELD_MAX) });
  return [
    mk('features', 'Visible features', r.visible_features.map((f) => shortRead(f.label, f.detail ? `${cap1(f.label)}: ${f.detail}` : f.label))),
    mk('cues', 'Congestion cues', r.congestion_cues.map((c) => shortRead(c))),
    mk('cannot', 'Cannot see', r.cannot_see.map((c) => shortRead(c))),
    mk('limits', 'Limits', r.limits.map((c) => shortRead(c))),
  ];
}
