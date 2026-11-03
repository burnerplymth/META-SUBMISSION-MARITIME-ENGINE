import { labelWord, sentenceCase } from '../../copy';
/* Pure logic for SATELLITE DESK. Overlay geometry per the
 * satwatch-overlay/1 contract,
 * count/delta views, provider and detector chips, scan-job progress,
 * the scan-capability probe, place rows and the LLM provenance chip.
 * No DOM, no fetch, no React.
 *
 * Doctrine carried:
 *  - counts that the API serves as null stay null: the panel prints
 *    NOT SERVED / AWAITING_META_KEY, never 0;
 *  - a delta exists only when the server served one (both crops
 *    counted); it is never recomputed from a single count;
 *  - the reference-counts provider is always REFERENCE COUNTS ·
 *    OFFLINE and is never presented as SAM or as live;
 *  - geometry a detector did not produce is never drawn: a baseline
 *    crop with counts but no localisations is COUNT ONLY. */

/* ── contract types (satwatch-overlay/1) ──────────────────────────── */
export interface VisibilityBasis { cloud_pct_aoi?: number | null; cloud_pct_tile?: number | null; method?: string; note?: string | null }
export interface MaskPolygon { format: 'polygon'; rings: number[][][] }
export interface MaskRle { format: 'rle_coco'; size: [number, number]; counts: string | number[] }
export interface OverlayDetection {
  id: string; label?: string | null; zone?: string | null; score: number | null;
  localization: 'mask' | 'bbox' | 'point' | string;
  bbox: number[] | null; point: number[] | null; mask: MaskPolygon | MaskRle | null;
  coord_space?: string; crop_px?: [number, number];
  detector_id?: string; crop_id?: string; role: 'now' | 'baseline' | string; scene_date?: string;
  visibility_basis?: VisibilityBasis; note?: string | null;
}
export interface OverlayCrop { file: string; url: string; scene_date: string; scene_id?: string; cloud_pct_aoi?: number | null; cloud_pct_tile?: number | null }
export interface PerCrop {
  role: string; crop_id?: string; scene_date?: string; status: string; provider?: string;
  counted: boolean; n_localised: number; visibility?: string | null; note?: string | null;
}
export interface OverlayCounts {
  vessels_now?: number | null; vessels_baseline?: number | null; vessels_delta?: number | null;
  frame_vessels_now?: number | null; frame_vessels_baseline?: number | null;
  anchorage_now?: number | null; berth_now?: number | null; transit_now?: number | null;
  yard_now?: number | string | null; ambiguous_excluded_now?: number | null;
  count_basis?: string | null; counted_by?: string | null;
}
export interface Overlay {
  contract: string; coord_space: string; node_id: string; name: string;
  served: boolean; not_served_reason: string | null;
  status: string; provider: string | null; provider_label: string | null;
  counts: OverlayCounts | null; delta: number | null;
  imagery_dates: { now: string; baseline: string };
  usable_pair: boolean;
  crops: { now: OverlayCrop; baseline: OverlayCrop } | null;
  detections: OverlayDetection[];
  per_crop: { now?: PerCrop; baseline?: PerCrop } | null;
  countable_class?: string | null; note?: string | null;
}

/** Contract check: an overlay envelope with the right contract id and
 *  array detections. Anything else is a contract failure (NOT SERVED),
 *  never an empty overlay. */
export function parseOverlay(body: unknown): Overlay | null {
  const b = body as Overlay | null;
  if (!b || typeof b !== 'object' || b.contract !== 'satwatch-overlay/1') return null;
  if (typeof b.node_id !== 'string' || !Array.isArray(b.detections)) return null;
  return b;
}

/* ── geometry ─────────────────────────────────────────────────────── */
export type Geometry =
  | { kind: 'polygon'; rings: number[][][] }
  | { kind: 'rle'; size: [number, number]; counts: string | number[] }
  | { kind: 'bbox'; box: [number, number, number, number] }
  | { kind: 'point'; x: number; y: number }
  | { kind: 'none' };

const num = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);

/** Best geometry present on a detection: mask > bbox > point, verified
 *  field by field. A detection whose named localization is missing its
 *  field falls down the chain; with nothing usable it is `none` and is
 *  not drawn (never invented). */
export function detectionGeometry(d: OverlayDetection): Geometry {
  const m = d.mask;
  if (m && m.format === 'polygon' && Array.isArray(m.rings) && m.rings.some((r) => Array.isArray(r) && r.length >= 3 && r.every((p) => Array.isArray(p) && num(p[0]) && num(p[1])))) {
    return { kind: 'polygon', rings: m.rings.filter((r) => r.length >= 3) };
  }
  if (m && m.format === 'rle_coco' && Array.isArray(m.size) && num(m.size[0]) && num(m.size[1]) && m.size[0] > 0 && m.size[1] > 0 && (typeof m.counts === 'string' || Array.isArray(m.counts))) {
    return { kind: 'rle', size: [m.size[0], m.size[1]], counts: m.counts };
  }
  if (Array.isArray(d.bbox) && d.bbox.length === 4 && d.bbox.every(num)) {
    const [x1, y1, x2, y2] = d.bbox;
    return { kind: 'bbox', box: [Math.min(x1, x2), Math.min(y1, y2), Math.max(x1, x2), Math.max(y1, y2)] };
  }
  if (Array.isArray(d.point) && d.point.length === 2 && d.point.every(num)) {
    return { kind: 'point', x: d.point[0], y: d.point[1] };
  }
  return { kind: 'none' };
}

/** norm1000 to a clamped 0..100 percentage. */
export const normToPct = (v: number): number => Math.min(100, Math.max(0, v / 10));
/** norm1000 to crop pixels. */
export const normToPx = (v: number, cropPx: number): number => (v / 1000) * cropPx;

/** SVG path for polygon rings in norm1000 (the SVG viewBox is
 *  0 0 1000 1000, stretched to the image box). */
export function ringsToPath(rings: number[][][]): string {
  return rings.map((r) => `${r.map((p, i) => `${i === 0 ? 'M' : 'L'}${round2(p[0])} ${round2(p[1])}`).join(' ')} Z`).join(' ');
}
const round2 = (n: number): number => Math.round(n * 100) / 100;

/** Rect of a bbox geometry for an SVG <rect> in norm1000. */
export function bboxRect(box: [number, number, number, number]): { x: number; y: number; w: number; h: number } {
  return { x: box[0], y: box[1], w: box[2] - box[0], h: box[3] - box[1] };
}

export interface LayerSummary { total: number; point: number; bbox: number; polygon: number; rle: number; none: number; drawable: number }
/** Geometry census of one crop's detections. `drawable` excludes
 *  detections with no usable geometry. */
export function layerSummary(dets: readonly OverlayDetection[], role: 'now' | 'baseline'): LayerSummary {
  const out: LayerSummary = { total: 0, point: 0, bbox: 0, polygon: 0, rle: 0, none: 0, drawable: 0 };
  for (const d of dets) {
    if (d.role !== role) continue;
    out.total++;
    const g = detectionGeometry(d);
    if (g.kind === 'point') out.point++;
    else if (g.kind === 'bbox') out.bbox++;
    else if (g.kind === 'polygon') out.polygon++;
    else if (g.kind === 'rle') out.rle++;
    else out.none++;
  }
  out.drawable = out.total - out.none;
  return out;
}

export type LayerState =
  | { state: 'NOT SERVED'; reason: string }
  | { state: 'COUNT ONLY'; counted: boolean }
  | { state: 'DRAWN'; summary: LayerSummary };

/** What the overlay layer over one crop is allowed to say. Not served
 *  -> hide the mask layer (NOT SERVED); served but this crop has no
 *  localisations -> COUNT ONLY (never an empty layer read as zero
 *  vessels); otherwise DRAWN with the geometry census. */
export function layerState(ov: Overlay | null, role: 'now' | 'baseline'): LayerState {
  if (!ov) return { state: 'NOT SERVED', reason: 'overlay not served' };
  if (!ov.served) return { state: 'NOT SERVED', reason: ov.not_served_reason ?? 'detection not served' };
  const summary = layerSummary(ov.detections, role);
  if (summary.drawable === 0) return { state: 'COUNT ONLY', counted: ov.per_crop?.[role]?.counted === true };
  return { state: 'DRAWN', summary };
}

/* ── COCO RLE ─────────────────────────────────────────────────────── */
/** Decode a COCO compressed-RLE `counts` string into run lengths
 *  (pycocotools rleFrString). */
export function rleCountsFromString(s: string): number[] {
  const cnts: number[] = [];
  let p = 0;
  while (p < s.length) {
    let x = 0; let k = 0; let more = true;
    while (more) {
      if (p >= s.length) throw new Error('rle_truncated');
      const c = s.charCodeAt(p) - 48;
      if (c < 0 || c > 63) throw new Error('rle_bad_char');
      x |= (c & 0x1f) << (5 * k);
      more = (c & 0x20) !== 0;
      p++; k++;
      if (!more && (c & 0x10) !== 0) x |= -1 << (5 * k);
    }
    if (cnts.length > 2) x += cnts[cnts.length - 2];
    cnts.push(x);
  }
  return cnts;
}

/** Decode COCO RLE into a row-major h*w Uint8Array (1 = mask). COCO
 *  runs are column-major (Fortran order) and start with background. */
export function decodeRle(counts: string | number[], size: [number, number]): Uint8Array {
  const [h, w] = size;
  const runs = typeof counts === 'string' ? rleCountsFromString(counts) : counts;
  const out = new Uint8Array(h * w);
  let pos = 0; let val = 0;
  for (const r of runs) {
    if (val === 1) {
      for (let i = 0; i < r && pos + i < h * w; i++) {
        const idx = pos + i;
        const col = Math.floor(idx / h); const row = idx - col * h;
        out[row * w + col] = 1;
      }
    }
    pos += r; val ^= 1;
  }
  return out;
}

/** Paint a decoded mask into an RGBA buffer of (dw x dh), nearest
 *  neighbour when the mask size differs from the destination. */
export function paintMask(dst: Uint8ClampedArray, dw: number, dh: number, mask: Uint8Array, size: [number, number], rgba: [number, number, number, number]): void {
  const [h, w] = size;
  for (let y = 0; y < dh; y++) {
    const sy = Math.min(h - 1, Math.floor((y / dh) * h));
    for (let x = 0; x < dw; x++) {
      const sx = Math.min(w - 1, Math.floor((x / dw) * w));
      if (mask[sy * w + sx] !== 1) continue;
      const o = (y * dw + x) * 4;
      dst[o] = rgba[0]; dst[o + 1] = rgba[1]; dst[o + 2] = rgba[2]; dst[o + 3] = rgba[3];
    }
  }
}

/* ── counts, delta, cloud basis ───────────────────────────────────── */
export const pctLabel = (n: number | null | undefined, digits = 1): string | null =>
  n == null || !Number.isFinite(n) ? null : `${n.toFixed(digits)}%`;

export interface CountsView {
  /** Detection status word (OK / PARTIAL / AWAITING_META_KEY / ...). */
  status: string;
  /** null = not served: the panel prints NOT SERVED, never 0. */
  now: number | null; baseline: number | null; delta: number | null; deltaFrac: number | null;
  frameNow: number | null; frameBaseline: number | null;
  basis: string | null; basisLabel: string | null;
  countable: string | null;
}
const numOrNull = (v: unknown): number | null => (num(v) ? v : null);

/** The counts as served. `delta` is the server's `vessels_delta`
 *  (null unless both crops were counted); deltaFrac is delta over the
 *  baseline count, null for a zero or missing baseline. */
export function countsView(ov: Overlay | null): CountsView {
  const c = ov?.counts ?? null;
  const baseline = numOrNull(c?.vessels_baseline);
  const delta = numOrNull(c?.vessels_delta);
  const basis = c?.count_basis ?? null;
  return {
    status: ov?.status ?? 'NOT SERVED',
    now: numOrNull(c?.vessels_now), baseline, delta,
    deltaFrac: delta != null && baseline != null && baseline > 0 ? delta / baseline : null,
    frameNow: numOrNull(c?.frame_vessels_now), frameBaseline: numOrNull(c?.frame_vessels_baseline),
    basis,
    basisLabel: basis === 'anchorage_zone' ? 'Anchorage zone' : basis === 'frame' ? 'Whole frame' : basis ? labelWord(basis) : null,
    countable: ov?.countable_class ?? null,
  };
}

export interface DeltaChip { label: string; direction: 'up' | 'down' | 'flat'; pct: string | null; flagged: boolean }
/** Δ chip text: signed integer, the percentage versus baseline, and a
 *  >=10% change flag (the server's own signal threshold band). Null
 *  when no delta was served. */
export function deltaChip(cv: CountsView): DeltaChip | null {
  if (cv.delta == null) return null;
  const sign = cv.delta > 0 ? '+' : cv.delta < 0 ? '−' : '±';
  const pctN = cv.deltaFrac == null ? 0 : Math.round(Math.abs(cv.deltaFrac) * 100);
  const pct = cv.deltaFrac == null ? null : pctN === 0 ? '0%' : `${cv.deltaFrac >= 0 ? '+' : '−'}${pctN}%`;
  return {
    label: `Δ ${sign}${Math.abs(cv.delta)}`,
    direction: cv.delta > 0 ? 'up' : cv.delta < 0 ? 'down' : 'flat',
    pct, flagged: cv.deltaFrac != null && Math.abs(cv.deltaFrac) >= 0.1,
  };
}

export interface CloudBasis { aoi: string | null; tile: string | null; visibility: string | null; visibilityFull: string | null }
/** Cloud / visibility basis for one crop: AOI and tile cloud percent
 *  from the crop, plus the detector's visibility word (the segment of
 *  its note before the first " - ", kept short; full text in title). */
export function cloudBasis(crop: OverlayCrop | null | undefined, pc: PerCrop | null | undefined): CloudBasis {
  const full = pc?.visibility ?? null;
  const word = full ? full.split(/\s+[-–—]\s+/)[0].trim() : null;
  return { aoi: pctLabel(crop?.cloud_pct_aoi), tile: pctLabel(crop?.cloud_pct_tile), visibility: word, visibilityFull: full };
}

/* ── provider + detector chips ────────────────────────────────────── */
export const REFERENCE_COUNTS_ID = 'reference-counts';
/** Chip text for the counting provider: the served label with " - "
 *  shown as " · " (REFERENCE COUNTS · OFFLINE). null when no provider
 *  counted. The reference-counts provider is always the test chip. */
export function providerChip(provider: string | null | undefined, label: string | null | undefined): { text: string; test: boolean } | null {
  if (provider === REFERENCE_COUNTS_ID) return { text: 'Offline detector', test: true };
  if (!label) return null;
  return { text: displayLabel(label.replace(/\s+-\s+/g, ' · ')), test: false };
}

export interface DetectorProvider { id: string; label: string; selected: boolean; state: string; note?: string; counts_are_real: boolean; offline_test: boolean; requires_env?: string[] }
export interface DetectorsDoc { selector_env?: string; selected: string; config_error: string | null; providers: DetectorProvider[] }
export function parseDetectors(body: unknown): DetectorsDoc | null {
  const b = body as DetectorsDoc | null;
  if (!b || typeof b !== 'object' || !Array.isArray(b.providers)) return null;
  return b;
}
const STATE_WORD: Record<string, string> = {
  NOT_SERVED: 'Not served', OFFLINE_TEST: 'Offline test', AWAITING_META_KEY: 'Awaiting key',
  NOT_IMPLEMENTED: 'Not implemented', NOT_CONFIGURED: 'Not configured', READY: 'Ready',
};
export const detectorStateWord = (s: string): string => {
  const w = STATE_WORD[s];
  if (w) return w;
  const t = s.replace(/_/g, ' ').toLowerCase();
  return t.charAt(0).toUpperCase() + t.slice(1);
};
/** A served provider label as the screen words it: the vendor name keeps its capital only. */
export const displayLabel = (label: string): string => {
  const t = label.replace(/\bREFERENCE COUNTS(\s*(·|-)\s*OFFLINE)?/i, 'Offline detector').replace(/\bAWAITING META KEY\b/i, 'Awaiting key').replace(/\bMETA\b/g, 'Meta');
  return sentenceCase(t);
};

export interface DetectorChip { text: string; state: string; chip: 'NOT SERVED' | 'INFO' | 'DOWN' | 'LIVE'; test: boolean; real: boolean }
/** The status chip for the selected detector: its served label and
 *  state word. READY with real counts is the only LIVE-style state;
 *  a config error is DOWN; none / awaiting key is NOT SERVED. */
export function detectorChip(doc: DetectorsDoc | null): DetectorChip | null {
  if (!doc) return null;
  if (doc.config_error) return { text: 'Detector config error', state: 'Down', chip: 'DOWN', test: false, real: false };
  const p = doc.providers.find((x) => x.selected) ?? doc.providers.find((x) => x.id === doc.selected);
  if (!p) return { text: 'No detector', state: 'Not served', chip: 'NOT SERVED', test: false, real: false };
  /* READY says a key is present and the transport exists; it is not a call
   * made, so it never earns the LIVE style (a LIVE chip is for a result). */
  const chip = p.state === 'NOT_SERVED' || p.state === 'AWAITING_META_KEY' || p.state === 'NOT_CONFIGURED' || p.state === 'NOT_IMPLEMENTED' ? 'NOT SERVED' : 'INFO';
  return {
    text: p.id === REFERENCE_COUNTS_ID ? 'Offline detector' : displayLabel(p.label.replace(/\s+-\s+/g, ' · ')),
    state: detectorStateWord(p.state), chip, test: p.offline_test === true, real: p.counts_are_real === true,
  };
}

/* ── places ───────────────────────────────────────────────────────── */
export interface PlaceScan { retrieved_at: string; imagery_dates: { now: string; baseline: string }; usable_pair: boolean; detection_status: string | null }
export interface SatPlace {
  node_id: string; name: string; placement: string; anchorage_verified: boolean;
  core_bbox?: number[]; capacity_rank: number | null; last_scan: PlaceScan | null;
}
export interface PlacesDoc { places: SatPlace[]; note?: string }
export function parsePlaces(body: unknown): PlacesDoc | null {
  const b = body as PlacesDoc | null;
  if (!b || typeof b !== 'object' || !Array.isArray(b.places)) return null;
  return b;
}
export interface PlaceRow {
  id: string; name: string; curated: boolean; verified: boolean;
  scan: 'NOT SCANNED' | 'PAIR USABLE' | 'PAIR NOT USABLE'; detection: string | null; now: string | null; baseline: string | null;
}
/** Sorted rows: curated before port-point-centred, scanned before
 *  unscanned, then by capacity rank, then name. */
export function placeRows(places: readonly SatPlace[]): PlaceRow[] {
  const rows = places.map((p) => ({
    p,
    row: {
      id: p.node_id, name: p.name, curated: p.placement === 'CURATED', verified: p.anchorage_verified === true,
      scan: p.last_scan ? (p.last_scan.usable_pair ? 'PAIR USABLE' : 'PAIR NOT USABLE') : 'NOT SCANNED',
      detection: p.last_scan?.detection_status ?? null,
      now: p.last_scan?.imagery_dates?.now ?? null, baseline: p.last_scan?.imagery_dates?.baseline ?? null,
    } as PlaceRow,
  }));
  rows.sort((a, b) => {
    if (a.row.curated !== b.row.curated) return a.row.curated ? -1 : 1;
    const sa = a.p.last_scan ? 0 : 1; const sb = b.p.last_scan ? 0 : 1;
    if (sa !== sb) return sa - sb;
    const ra = a.p.capacity_rank ?? Number.MAX_SAFE_INTEGER; const rb = b.p.capacity_rank ?? Number.MAX_SAFE_INTEGER;
    if (ra !== rb) return ra - rb;
    return a.p.name < b.p.name ? -1 : a.p.name > b.p.name ? 1 : 0;
  });
  return rows.map((r) => r.row);
}
/** Detection status of a scan, as the word the chip prints. */
export function detectionWord(status: string | null | undefined): string {
  if (!status) return 'Not served';
  /* No key means the live detector did not run on this scan; the key state belongs to the Run action only. */
  return status === 'AWAITING_META_KEY' ? 'Not run' : labelWord(status);
}

/* ── scan control + job ───────────────────────────────────────────── */
export interface ScanItem {
  node_id: string; name?: string; status: string; usable_pair?: boolean | null;
  imagery_dates?: { now: string; baseline: string } | null; detection_status?: string | null;
  counts?: unknown; signal?: unknown; error?: string | null; notes?: string[];
}
export interface ScanJob {
  job_id: string; status: string; created_at?: string; started_at?: string | null; finished_at?: string | null;
  request?: { places: string[] | string; days?: number; mint?: boolean; detector?: string | null };
  detector?: { id: string; label: string };
  progress: { total: number; finished: number };
  items: ScanItem[];
}
export function parseJob(body: unknown): ScanJob | null {
  const b = body as ScanJob | null;
  if (!b || typeof b !== 'object' || typeof b.job_id !== 'string' || !b.progress || !Array.isArray(b.items)) return null;
  return b;
}
export const JOB_POLL_MS = 4000;
export const jobActive = (j: Pick<ScanJob, 'status'> | null): boolean => !!j && (j.status === 'queued' || j.status === 'running');
export interface JobProgress { finished: number; total: number; pct: number; label: string; active: boolean }
export function jobProgress(j: ScanJob | null): JobProgress {
  if (!j) return { finished: 0, total: 0, pct: 0, label: '', active: false };
  const { finished, total } = j.progress;
  const pct = total > 0 ? Math.round((Math.min(finished, total) / total) * 100) : 0;
  return { finished, total, pct, label: `${finished} of ${total} places · ${labelWord(j.status)}`, active: jobActive(j) };
}
/** Capability probe for the admin-only scan route. There is no
 *  capability endpoint; the probe is `POST /satwatch/scan {}`: the
 *  route authenticates before it validates, so 401 means the caller
 *  may not, 422 (places required) means the caller is admin and
 *  nothing was started, 429 (busy) also implies auth passed. Any
 *  other status leaves the control hidden. */
export function scanCapability(status: number): 'allowed' | 'denied' | 'unknown' {
  if (status === 401 || status === 403) return 'denied';
  if (status === 422 || status === 429 || status === 202 || status === 200) return 'allowed';
  return 'unknown';
}
export function scanBody(target: string | 'all', days = 90): { places: string[] | 'all'; days: number; mint: false } {
  return { places: target === 'all' ? 'all' : [target], days, mint: false };
}

/* ── brief + signals + LLM provenance ─────────────────────────────── */
export interface BriefDoc {
  node_id: string;
  intel: {
    node_id: string; vessels_now: number | null; vessels_baseline: number | null; delta: number | null; status: string;
    imagery_dates: { now: string; baseline: string }; provider: string | null; provider_label: string | null;
  };
  text: string;
  provenance?: LlmProvenance | null;
}
export function parseBrief(body: unknown): BriefDoc | null {
  const b = body as BriefDoc | null;
  if (!b || typeof b !== 'object' || typeof b.text !== 'string' || !b.intel) return null;
  return b;
}
/** Brief body lines for the collapsed text block (blank lines
 *  dropped). The text is the server's deterministic template. */
export const briefLines = (b: BriefDoc): string[] => b.text.split('\n').map((l) => l.trimEnd()).filter((l) => l.length > 0);

export interface LlmProvenance { model: string | null; transport: 'LIVE' | 'RECORDED' | 'NONE' | string; live?: boolean; prompt_id?: string | null; cost_usd?: number; cached?: boolean }
export interface MetaChip { text: string; tone: 'meta' | 'live' }
/** Model chip from a provenance block alone : Live
 *  shows the model id; Recorded shows Recorded and never a model name
 *  as if live; none shows no model chip. A satellite brief carries no
 *  provenance block (deterministic template, no LLM): its chip is the
 *  honest Template one. */
export function metaChip(p: LlmProvenance | null | undefined): MetaChip {
  if (!p) return { text: 'Template \u00b7 no model', tone: 'meta' };
  if (p.transport === 'LIVE') return { text: `Meta AI \u00b7 ${p.model ?? 'model'} \u00b7 Live`, tone: 'live' };
  if (p.transport === 'RECORDED') return { text: 'Meta AI \u00b7 Recorded', tone: 'meta' };
  return { text: 'No model', tone: 'meta' };
}

export interface SignalRow { nodeId: string; wouldMint: boolean; reasons: string[]; providerLabel: string | null; severity: number | null }
export interface SignalsDoc { signals: Array<{ node_id: string; would_mint: boolean; reasons?: string[]; provider_label?: string | null; candidate?: { severity?: number } | null }> }
export function parseSignals(body: unknown): SignalsDoc | null {
  const b = body as SignalsDoc | null;
  if (!b || typeof b !== 'object' || !Array.isArray(b.signals)) return null;
  return b;
}
export const reasonWord = (code: string): string => labelWord(code.replace(/_/g, ' '));
export function signalRows(doc: SignalsDoc): SignalRow[] {
  return doc.signals.map((s) => ({
    nodeId: s.node_id, wouldMint: s.would_mint === true, reasons: (s.reasons ?? []).map(reasonWord),
    providerLabel: s.provider_label ? s.provider_label.replace(/\s+-\s+/g, ' · ') : null,
    severity: s.candidate && num(s.candidate.severity) ? s.candidate.severity : null,
  }));
}

/* ── compare slider ───────────────────────────────────────────────── */
export const clampSlider = (v: number): number => (Number.isFinite(v) ? Math.min(100, Math.max(0, Math.round(v))) : 50);
/** clip-path for the NOW layer: it shows to the right of the divider. */
export const nowClip = (pct: number): string => `inset(0 0 0 ${clampSlider(pct)}%)`;
