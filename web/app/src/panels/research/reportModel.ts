/* Research report: the shapes of /agent/sessions/{id}/research-report and /evidence (server/agent/research_routes.py),
 * the validators for what the server sends, and the pure view model of the Report tab. Every document is untrusted: a
 * parser returns a clean typed value or null, never throws. Nothing here echoes text the code check removed (the
 * server never sends it; only counts arrive). */
import { API_BASE } from '../../api';
import { humaniseIds } from '../../format';
import { nodeName } from '../../seed';
import { datasetLabel } from './plan';
import { isObj, KIND_LABEL, num, str, type AnalysisKind, type Episode } from './types';
import { ANALYSIS_KINDS } from './types';

const arr = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);
const strs = (v: unknown, max = 40, len = 400): string[] => arr(v).filter((x): x is string => typeof x === 'string').slice(0, max).map((s) => s.slice(0, len));
const EV_ID = /^E\d{1,3}$/;
const FID = /^F\d{1,3}$/;

export type ReportState = 'none' | 'writing' | 'ready' | 'failed';
export type Mode = 'LIVE' | 'RECORDED';
export type FindingState = 'verified' | 'flagged';
export type VerifyStatus = 'VERIFIED' | 'UNVERIFIED';

export interface ReportNumber { name: string; value: number | string | null; unit: string; evidence: string | null }
export interface ReportFinding { id: string; text: string; state: FindingState; evidence: string[]; numbers: ReportNumber[] }
export interface DataUsedRow {
  id: string; title: string; source: string | null; licence: string | null; unit: string | null; places: string[];
  firstObs: string | null; lastObs: string | null; state: string | null; evidence: string[];
}
export interface EvidenceRow {
  id: string; tool: string; kind: string; analysisId: string | null; n: number | null; label: string | null; summary: string;
  caveats: string[]; hasFigure: boolean; argsSha: string | null; resultSha: string | null;
}
export interface Verification {
  status: VerifyStatus; proposed: number; verified: number; flagged: number; removed: number; withoutEvidence: number; unchecked: number; chips: string[];
}
export type ContextKind = 'finding' | 'context' | 'limitation';
/** Model wording the code did not check: screened, never counted as verified. A flagged line carries the server's withheld wording. */
export interface ContextLine { id: string; kind: ContextKind; text: string; state: 'unchecked' | 'flagged'; evidence: string[] }
export const CONTEXT_TITLE = 'Context (unchecked)';
export const CONTEXT_CHIP = 'Unchecked wording';
export interface NoteLabel { state: Mode; text: string; statistics: string; model: string | null; promptId: string | null }
export interface ModelCall { transport: string | null; model: string | null; costUsd: number | null; estimatedUsd: number | null; structured: boolean }
export interface ReportNote {
  id: string; title: string; question: string; label: NoteLabel; dataUsed: DataUsedRow[]; findings: ReportFinding[]; context: ContextLine[];
  limitations: string[]; methods: string[]; evidenceIds: string[]; verification: Verification; modelCall: ModelCall; evidence: EvidenceRow[];
}
export interface ReportDoc { sessionId: string; transport: Mode; state: ReportState; error: string | null; note: ReportNote | null }

/* ── parsers ─────────────────────────────────────────────────────────── */

const count = (v: unknown): number => { const n = num(v); return n !== null && n >= 0 ? Math.floor(n) : 0; };

const CHIP_OK = /^(Verified|Unverified|\d{1,3} of \d{1,3} findings checked|\d{1,3} (flagged|removed|without evidence|unchecked))$/;

/** The four counts as chips, the way the server words them. Used when the served chips are absent or off-pattern. */
export function verificationChips(v: Pick<Verification, 'status' | 'proposed' | 'verified' | 'flagged' | 'removed' | 'withoutEvidence'> & { unchecked?: number }): string[] {
  const out = [v.status === 'VERIFIED' ? 'Verified' : 'Unverified', `${v.verified} of ${v.proposed} findings checked`];
  if (v.flagged) out.push(`${v.flagged} flagged`);
  if (v.removed) out.push(`${v.removed} removed`);
  if (v.withoutEvidence) out.push(`${v.withoutEvidence} without evidence`);
  if (v.unchecked) out.push(`${v.unchecked} unchecked`);
  return out;
}

export function parseVerification(v: unknown): Verification | null {
  if (!isObj(v)) return null;
  const counts = {
    proposed: count(v.proposed), verified: count(v.verified), flagged: count(v.flagged), removed: count(v.rejected),
    withoutEvidence: count(v.dropped_no_evidence), unchecked: count(v.unchecked),
  };
  /* Only a server that says VERIFIED, with every proposed finding passed unchanged, reads Verified here. */
  const clean = counts.proposed > 0 && counts.verified === counts.proposed && !counts.flagged && !counts.removed && !counts.withoutEvidence;
  const status: VerifyStatus = v.status === 'VERIFIED' && clean ? 'VERIFIED' : 'UNVERIFIED';
  const base = { status, ...counts };
  const served = strs(v.chips, 8, 60);
  /* The served chips are used only when they agree with the served counts; otherwise the counts are worded here. */
  const own = verificationChips(base);
  const chips = served.length && served.every((c) => CHIP_OK.test(c)) && served.length === own.length && served.every((c, i) => c === own[i]) ? served : own;
  return { ...base, chips };
}

function parseNumbers(v: unknown): ReportNumber[] {
  const out: ReportNumber[] = [];
  for (const n of arr(v).slice(0, 12)) {
    if (!isObj(n)) continue;
    const name = str(n.name, 120);
    if (!name) continue;
    const val = typeof n.value === 'number' && Number.isFinite(n.value) ? n.value : typeof n.value === 'string' ? n.value.slice(0, 60) : null;
    out.push({ name, value: val, unit: typeof n.unit === 'string' ? n.unit.slice(0, 30) : '', evidence: typeof n.evidence === 'string' && EV_ID.test(n.evidence) ? n.evidence : null });
  }
  return out;
}

export function parseFindings(v: unknown): ReportFinding[] {
  const out: ReportFinding[] = [];
  const seen = new Set<string>();
  for (const f of arr(v).slice(0, 12)) {
    if (!isObj(f)) continue;
    const id = str(f.id, 12), text = str(f.text, 900);
    if (!id || !FID.test(id) || !text || seen.has(id)) continue;
    seen.add(id);
    out.push({ id, text, state: f.state === 'verified' ? 'verified' : 'flagged', evidence: strs(f.evidence, 8, 8).filter((e) => EV_ID.test(e)), numbers: parseNumbers(f.numbers) });
  }
  return out;
}

export function parseContext(v: unknown): ContextLine[] {
  const out: ContextLine[] = [];
  const seen = new Set<string>();
  for (const c of arr(v).slice(0, 24)) {
    if (!isObj(c)) continue;
    const id = str(c.id, 8), text = str(c.text, 900);
    if (!id || !/^[CL]\d{1,2}$/.test(id) || !text || seen.has(id)) continue;
    seen.add(id);
    const kind: ContextKind = c.kind === 'finding' || c.kind === 'limitation' ? c.kind : 'context';
    out.push({ id, kind, text, state: c.state === 'flagged' ? 'flagged' : 'unchecked', evidence: strs(c.evidence, 8, 8).filter((e) => EV_ID.test(e)) });
  }
  return out;
}

export function parseEvidence(v: unknown): EvidenceRow[] {
  const out: EvidenceRow[] = [];
  for (const e of arr(v).slice(0, 60)) {
    if (!isObj(e)) continue;
    const id = str(e.id, 8);
    if (!id || !EV_ID.test(id)) continue;
    out.push({
      id, tool: str(e.tool, 40) ?? '', kind: str(e.kind, 40) ?? '', analysisId: str(e.analysis_id, 80), n: num(e.n), label: str(e.label, 240),
      summary: str(e.summary, 200) ?? '', caveats: strs(e.caveat_chips, 12, 60), hasFigure: e.has_figure === true,
      argsSha: str(e.args_sha256, 20), resultSha: str(e.result_sha256, 20),
    });
  }
  return out;
}

function parseDataUsed(v: unknown): DataUsedRow[] {
  const out: DataUsedRow[] = [];
  for (const r of arr(v).slice(0, 24)) {
    if (!isObj(r)) continue;
    const id = str(r.id, 80);
    if (!id) continue;
    out.push({
      id, title: str(r.title, 160) ?? id, source: str(r.source, 200), licence: str(r.licence, 80), unit: str(r.unit, 40),
      places: strs(r.places, 5, 64), firstObs: str(r.first_obs, 10), lastObs: str(r.last_obs, 10), state: str(r.state, 20), evidence: strs(r.evidence, 8, 8).filter((e) => EV_ID.test(e)),
    });
  }
  return out;
}

function parseLabel(v: unknown): NoteLabel | null {
  if (!isObj(v)) return null;
  const state: Mode = v.state === 'LIVE' ? 'LIVE' : 'RECORDED';
  return { state, text: str(v.text, 200) ?? '', statistics: str(v.statistics, 200) ?? '', model: str(v.model, 80), promptId: str(v.prompt_id, 80) };
}

export function parseNote(v: unknown): ReportNote | null {
  if (!isObj(v)) return null;
  const id = str(v.id, 24), label = parseLabel(v.label), verification = parseVerification(v.verification);
  if (!id || !/^rn_[0-9a-f]{6,16}$/.test(id) || !label || !verification) return null;
  const mc = isObj(v.model_call) ? v.model_call : {};
  return {
    id, title: str(v.title, 200) ?? 'Research note', question: str(v.question, 600) ?? '', label, dataUsed: parseDataUsed(v.data_used),
    findings: parseFindings(v.findings), context: parseContext(v.context_unchecked), limitations: strs(v.limitations, 16), methods: strs(v.methods, 16), evidenceIds: strs(v.evidence_ids, 60, 8).filter((e) => EV_ID.test(e)),
    verification, evidence: parseEvidence(v.evidence),
    modelCall: { transport: str(mc.transport, 20), model: str(mc.model, 80), costUsd: num(mc.cost_usd), estimatedUsd: num(mc.estimated_cost_usd), structured: mc.structured_output === true },
  };
}

export function parseReportDoc(v: unknown): ReportDoc | null {
  if (!isObj(v)) return null;
  const sid = str(v.session_id, 60);
  if (!sid) return null;
  const st = v.state;
  const state: ReportState = st === 'writing' || st === 'ready' || st === 'failed' ? st : 'none';
  const note = parseNote(v.note);
  return { sessionId: sid, transport: v.transport === 'LIVE' ? 'LIVE' : 'RECORDED', state: state === 'ready' && !note ? 'none' : state, error: str(v.error, 60), note };
}

export function parseEvidenceDoc(v: unknown): EvidenceRow[] | null {
  return isObj(v) && Array.isArray(v.evidence) ? parseEvidence(v.evidence) : null;
}

/* ── wording ─────────────────────────────────────────────────────────── */

export const MODE_WORD: Record<Mode, string> = { LIVE: 'Live', RECORDED: 'Recorded' };

const KIND_WORD: Record<string, string> = {
  catalog: 'Catalogue', episodes: 'Episodes', assemble: 'Assembly', scene_scan: 'Scene scan',
  ...KIND_LABEL,
};
export const evidenceKindWord = (kind: string): string => KIND_WORD[kind] ?? (kind.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase()) || 'Evidence');

const ERROR_WORD: Record<string, string> = {
  no_evidence: 'No analysis evidence', no_note: 'No report', note_busy: 'Report in progress', session_busy: 'Session busy',
  max_spend: 'Spend limit reached', daily_cap: 'Daily limit reached', llm_disabled: 'Meta AI off', spend_store_unreadable: 'Spend store down',
  call_too_large: 'Evidence too large', model_error: 'Model error', model_reply_not_json: 'Reply not readable', unknown_session: 'Session gone',
  agent_disabled: 'Meta AI off', unreachable: 'Not served', timeout: 'Timed out', rate_limited: 'Rate limited', demo_locked: 'Live needs the demo link',
  renderer_unavailable: 'PDF not served', no_recorded_report: 'No recorded report for this episode', client_timeout: 'Browser did not answer',
  invalid_request: 'Request refused', bad_response: 'Bad reply',
};
export const reportErrorWord = (code: string | null | undefined): string =>
  (code && ERROR_WORD[code]) || (code && code.startsWith('http_') ? `Error ${code.slice(5)}` : 'Request refused');

/** Place ids and `dataset:place` keys in served text read as names. The text itself is not changed in the export. */
export function readable(text: string): string {
  const keyed = text.replace(/\b((?:portwatch|weather|economic|events|scenes|tracks)_[a-z0-9_]*):([a-z][a-z0-9_]*)\b/g, (_m, ds: string, pl: string) =>
    `${datasetLabel(ds)}, ${pl === 'global' ? 'Global' : nodeName(pl)}`);
  return humaniseIds(keyed);
}

/* ── evidence → workbench ────────────────────────────────────────────── */

export type WorkbenchSection = 'series' | 'analysis' | 'scenes' | 'events';
export interface EvidenceTarget { kind: AnalysisKind | null; series: string | null; section: WorkbenchSection }

const isKind = (k: string): k is AnalysisKind => (ANALYSIS_KINDS as readonly string[]).includes(k);

/** Where the workbench shows what an evidence entry cites: the analysis row (kind and series) or the aligned series. */
export function evidenceTarget(e: EvidenceRow): EvidenceTarget {
  const m = /\bon ([a-z0-9_.-]+:[a-z0-9_.-]+)/i.exec(e.summary);
  const series = m ? m[1] : null;
  if (isKind(e.kind)) return { kind: e.kind, series, section: 'analysis' };
  if (e.kind === 'scene_scan') return { kind: null, series: null, section: 'scenes' };
  if (e.kind === 'episodes') return { kind: null, series: null, section: 'events' };
  return { kind: null, series, section: 'series' };
}

export function evidenceById(rows: readonly EvidenceRow[], id: string): EvidenceRow | null {
  return rows.find((r) => r.id === id) ?? null;
}

/** Row title: `Event study, n 1`. */
export function evidenceTitle(e: EvidenceRow): string {
  return `${evidenceKindWord(e.kind)}${e.n !== null ? `, n ${e.n.toLocaleString('en-US')}` : ''}`;
}

/** Findings that cite an evidence id (for the highlight in the other direction). */
export function findingsCiting(note: ReportNote, id: string): string[] {
  return note.findings.filter((f) => f.evidence.includes(id)).map((f) => f.id);
}

/* ── which episode a request names ───────────────────────────────────── */

export interface OpenRequest { episodeId?: string | null; places?: string[]; from?: string | null; to?: string | null }

/** The episode an open_research request points at: an id, else the one whose window and first place match. */
export function resolveEpisode(list: readonly Episode[], r: OpenRequest): Episode | null {
  if (r.episodeId) return list.find((e) => e.id === r.episodeId) ?? null;
  const first = r.places?.[0];
  const exact = list.find((e) => (!r.from || e.from === r.from) && (!r.to || e.to === r.to) && (!first || e.places.includes(first)) && (r.from || r.to || first));
  if (exact) return exact;
  return first ? list.find((e) => e.places.includes(first)) ?? null : null;
}

/* ── the task text ───────────────────────────────────────────────────── */

/** Recorded scripts exist for three episodes (server/agent/fixture_research.py); the text is the one the server lists. */
const RECORDED_BY_EPISODE: Array<[RegExp, RegExp]> = [
  [/^ever_given/, /ever given/i], [/^la_lb/, /los angeles/i], [/^panama/, /panama/i],
];
export function recordedTaskFor(episodeId: string, tasks: readonly string[]): string | null {
  for (const [ep, task] of RECORDED_BY_EPISODE) if (ep.test(episodeId)) return tasks.find((t) => task.test(t)) ?? null;
  return null;
}

export interface TaskContext { episode: Episode; kind: AnalysisKind; series: string | null; eventId: string | null }
export const MAX_TASK = 1200;

/** The task a live session receives: the open episode and analysis, in the user's own terms. Short, no prose. */
export function liveTask(c: TaskContext): string {
  const cats = c.episode.categories.length ? ` with ${c.episode.categories.map((x) => x.replace(/_/g, ' ')).join(' and ')} events` : '';
  const on = c.series ? ` on ${c.series}` : '';
  const around = c.eventId ? ` around ${c.eventId}` : '';
  return `Assemble historical data for ${c.episode.places.slice(0, 2).join(' and ')} from ${c.episode.from} to ${c.episode.to}${cats}, `
    + `run a ${KIND_LABEL[c.kind].toLowerCase()} analysis${on}${around}, and write the research note.`.slice(0, MAX_TASK);
}

/* ── export ──────────────────────────────────────────────────────────── */

export type ExportFormat = 'html' | 'json' | 'pdf';
export const EXPORT_LABEL: Record<ExportFormat, string> = { html: 'HTML', json: 'JSON', pdf: 'PDF' };

export function exportUrl(sessionId: string, fmt: ExportFormat, noteId: string | null, base: string = API_BASE): string {
  const q = `format=${fmt}${noteId ? `&note_id=${encodeURIComponent(noteId)}` : ''}`;
  return `${base}/agent/sessions/${encodeURIComponent(sessionId)}/research-report/export?${q}`;
}

/** Formats offered for a note: HTML and JSON always; PDF only when the report service says its renderer is available. */
export function exportFormats(pdfAvailable: boolean): ExportFormat[] {
  return pdfAvailable ? ['html', 'json', 'pdf'] : ['html', 'json'];
}

/** `GET /reports/templates` → whether the PDF renderer is available (strictly true). */
export function pdfAvailableFrom(doc: unknown): boolean {
  return isObj(doc) && isObj(doc.pdf) && doc.pdf.available === true;
}

/* ── estimates ───────────────────────────────────────────────────────── */

export function usd(v: number | null | undefined): string | null {
  if (v === null || v === undefined || !Number.isFinite(v)) return null;
  if (v === 0) return 'USD 0.00';
  return v >= 1 ? `USD ${v.toFixed(2)}` : `USD ${v.toFixed(4)}`;
}
