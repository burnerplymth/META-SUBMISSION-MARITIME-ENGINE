/**
 * NATURAL-LANGUAGE QUERY — pure logic for the META
 * edge of the command bar. Doctrine (binding): the model may PARSE language into the CLOSED filter vocabulary;
 * it may never rank, invent an entity or answer in prose. The lexical
 * engine stays the only ranker. So this module:
 *
 *   1. ROUTES: the structured resolveQuery path always runs first and is
 *      unchanged; the model is only a candidate when that path found
 *      nothing AND the input reads as language, not a code or qualifier.
 *   2. VALIDATES every reply client-side against the closed schema (the
 *      server already does; a second, independent check is the doctrine,
 *      so a faulty or hostile server reply cannot widen the vocabulary).
 *      Anything outside the schema is REFUSED with a machine code, shown
 *      as a chip, and the lexical resolution stands.
 *   3. PLANS the ranked text: the words the model returned are matched
 *      against the real catalogue by the lexical engine itself; words
 *      that match no served entity are shown as UNMATCHED chips, never
 *      dropped silently and never replaced by a guess.
 *   4. BUILDS the chips (parsed filters, removable) and applies removals.
 */
import { CATEGORY_LABELS, CATEGORY_ORDER, parseQualifier, type SearchCategory, type SearchFilters } from '../search/engine';
import { parseProvenance, type LlmProvenance } from './provenance';

/* ── 1. routing ───────────────────────────────────────────────────── */
export const NL_MAX_CHARS = 300;
const wordsOf = (s: string): string[] => s.toLowerCase().match(/[a-z0-9]+/g) ?? [];

/** Is this raw input a candidate for the model at all? Never for a typed
 *  qualifier (port: …), a single token (a code or a name), blank input,
 *  or input longer than the API accepts. */
export function isNlCandidate(raw: string): boolean {
  const t = raw.trim();
  if (!t || t.length > NL_MAX_CHARS) return false;
  if (parseQualifier(t).category) return false;      // port: / event: / run: …
  return wordsOf(t).length >= 2;
}

export type QueryRoute = 'structured' | 'nl';
/** The structured path's hits always win; the model is asked only when
 *  the structured search is empty and the input reads as language. */
export function routeQuery(raw: string, structuredTotal: number): QueryRoute {
  return structuredTotal === 0 && isNlCandidate(raw) ? 'nl' : 'structured';
}

/** Free (RECORDED) transport may parse as you type; a LIVE (paid) call
 *  waits for an explicit Enter / click. Unknown status never auto-runs. */
export function shouldAutoParse(statusLive: boolean | null | undefined): boolean {
  return statusLive === false;
}

/* ── 2. validation / refusal ──────────────────────────────────────── */
export const MODES = ['sea', 'air', 'ground'] as const;
export const FILTER_KEYS = ['categories', 'modes', 'severities', 'dateFloor'] as const;
const ENTITY_KEYS = new Set(['entities', 'entity', 'place', 'places', 'place_ids', 'placeids', 'node_ids', 'nodes',
  'ids', 'results', 'result', 'ranking', 'rank', 'answer', 'answers', 'summary', 'explanation', 'matches']);
const REPLY_KEYS = new Set(['text', 'filters', 'source', 'rejected', 'provenance']);
const DAY_MS = 86_400_000;

export interface NlParsed {
  text: string;
  filters: SearchFilters;
  provenance: LlmProvenance | null;
}

export type NlOutcome =
  | { status: 'parsed'; parsed: NlParsed }
  /** The server fell back to plain lexical resolution (model reply rejected or no model). */
  | { status: 'fallback'; code: string; provenance: LlmProvenance | null }
  /** The reply broke the closed schema (client-side check): refused with a code. */
  | { status: 'refused'; code: string }
  | { status: 'timeout' }
  | { status: 'unreachable' };

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);
const refuse = (code: string): NlOutcome => ({ status: 'refused', code });

/** Machine code for a refusal/fallback -> the chip word (no prose). */
export function refusalWord(code: string): string {
  return code.replace(/[^a-z0-9]+/gi, '_').toUpperCase();
}

/**
 * Validate one decoded POST /llm/query reply against the closed schema.
 * `typed` is what the user typed (the text field may only reuse its
 * words); `nowMs` anchors the date-floor window check.
 */
export function validateNlReply(raw: unknown, typed: string, nowMs: number): NlOutcome {
  if (!isObj(raw)) return refuse('reply_not_an_object');
  for (const k of Object.keys(raw)) {
    if (ENTITY_KEYS.has(k.toLowerCase())) return refuse('entity_not_allowed');
    if (!REPLY_KEYS.has(k)) return refuse('reply_field_not_in_schema');
  }
  const source = raw.source;
  if (source !== 'nl' && source !== 'lexical_fallback') return refuse('source_not_in_enum');
  const parsedProv = parseProvenance(raw.provenance);
  // A NONE block means no model ran: it is the absence of provenance.
  const provenance = parsedProv && parsedProv.transport !== 'NONE' ? parsedProv : null;

  const filtersRaw = raw.filters;
  if (!isObj(filtersRaw)) return refuse('filters_not_an_object');
  for (const k of Object.keys(filtersRaw)) {
    if (ENTITY_KEYS.has(k.toLowerCase())) return refuse('entity_not_allowed');
    if (!(FILTER_KEYS as readonly string[]).includes(k)) return refuse('filter_not_in_schema');
  }

  if (source === 'lexical_fallback') {
    if (Object.keys(filtersRaw).length) return refuse('fallback_carries_filters');
    const code = typeof raw.rejected === 'string' && raw.rejected ? raw.rejected : 'lexical_fallback';
    return { status: 'fallback', code, provenance };
  }

  const filters: SearchFilters = {};
  if (filtersRaw.categories !== undefined) {
    const c = filtersRaw.categories;
    if (!Array.isArray(c) || !c.length || c.some((x) => typeof x !== 'string' || !(CATEGORY_ORDER as string[]).includes(x))) return refuse('categories_not_in_enum');
    filters.categories = [...new Set(c as SearchCategory[])];
  }
  if (filtersRaw.modes !== undefined) {
    const m = filtersRaw.modes;
    if (!Array.isArray(m) || !m.length || m.some((x) => typeof x !== 'string' || !(MODES as readonly string[]).includes(x))) return refuse('modes_not_in_enum');
    filters.modes = [...new Set(m as Array<'sea' | 'air' | 'ground'>)];
  }
  if (filtersRaw.severities !== undefined) {
    const s = filtersRaw.severities;
    if (!Array.isArray(s) || !s.length || s.some((x) => typeof x !== 'number' || !Number.isInteger(x) || x < 1 || x > 5)) return refuse('severity_invalid');
    filters.severities = [...new Set(s as number[])].sort((a, b) => a - b);
  }
  if (filtersRaw.dateFloor !== undefined) {
    const d = filtersRaw.dateFloor;
    const t = typeof d === 'string' ? Date.parse(d) : NaN;
    // The server's windows are 1 / 7 / 30 / 90 / 365 days back from now.
    if (!Number.isFinite(t) || t > nowMs + DAY_MS || t < nowMs - 366 * DAY_MS) return refuse('date_floor_invalid');
    filters.dateFloor = d as string;
  }

  const text = raw.text;
  if (typeof text !== 'string') return refuse('text_not_string');
  if (text.length > typed.length) return refuse('text_longer_than_input');
  const typedWords = new Set(wordsOf(typed));
  if (wordsOf(text).some((w) => !typedWords.has(w))) return refuse('text_introduces_words_not_typed');
  if (!text.trim() && !Object.keys(filters).length) return refuse('nothing_resolved');
  // A model-parsed reply must say which model parsed it: without a
  // usable provenance block the chip cannot be built, so the reply is
  // not accepted as a model output.
  if (!provenance) return refuse('provenance_missing');
  return { status: 'parsed', parsed: { text: text.trim(), filters, provenance } };
}

/* ── 3. text planning against the real catalogue ──────────────────── */
/** Function words the lexical engine should not have to match. A plain
 *  deterministic list: it removes words, it never adds or rewrites any. */
const STOP = new Set(['a', 'an', 'the', 'in', 'at', 'of', 'to', 'on', 'for', 'from', 'by', 'with', 'near', 'nearby', 'around',
  'and', 'or', 'is', 'are', 'was', 'were', 'be', 'been', 'did', 'do', 'does', 'what', 'which', 'who', 'where', 'when', 'how',
  'hit', 'hits', 'affected', 'show', 'me', 'find', 'list', 'all', 'any', 'there', 'that', 'this', 'it', 'its', 'my', 'about']);

export const nlTerms = (text: string): string[] => [...new Set(wordsOf(text).filter((w) => !STOP.has(w)))];

export interface TextPlan {
  /** What the lexical engine ranks. */
  applied: string;
  /** Words that matched at least one served entity. */
  matched: string[];
  /** Words that matched nothing served: shown as chips, never guessed at. */
  unmatched: string[];
}

/** Choose the ranked text from the model's returned words using only the
 *  lexical engine's own hit counts (`countHits`). */
export function planNlText(text: string, countHits: (q: string) => number): TextPlan {
  const terms = nlTerms(text);
  if (!terms.length) return { applied: '', matched: [], unmatched: [] };
  const whole = terms.join(' ');
  if (countHits(whole) > 0) return { applied: whole, matched: terms, unmatched: [] };
  const matched = terms.filter((t) => countHits(t) > 0);
  const unmatched = terms.filter((t) => !matched.includes(t));
  if (!matched.length) return { applied: whole, matched: [], unmatched: terms };
  const joined = matched.join(' ');
  if (countHits(joined) > 0) return { applied: joined, matched, unmatched };
  // Each word matches alone but not together: rank the first, mark the rest unmatched.
  return { applied: matched[0], matched: [matched[0]], unmatched: [...matched.slice(1), ...unmatched] };
}

/** A returned category the catalogue holds nothing for is NOT SERVED,
 *  not "no results" (a down feed must not read as an empty one). */
export function unservedCategories(filters: SearchFilters, counts: Partial<Record<SearchCategory, number>>): SearchCategory[] {
  return (filters.categories ?? []).filter((c) => !(counts[c] && counts[c]! > 0));
}

/* ── 4. chips + removal ───────────────────────────────────────────── */
export type NlChipKind = 'filter' | 'term' | 'unmatched' | 'overridden';
export interface NlChip {
  id: string;
  kind: NlChipKind;
  label: string;
  removable: boolean;
}

const MONS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
export function sinceWord(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? 'Since ?' : `Since ${String(d.getUTCDate()).padStart(2, '0')} ${MONS[d.getUTCMonth()]}`;
}
export function severityWord(sev: number[]): string {
  const s = [...sev].sort((a, b) => a - b);
  const contiguousToFive = s.every((v, i) => v === s[0] + i) && s[s.length - 1] === 5;
  return contiguousToFive ? `Sev ${s[0]}+` : `Sev ${s.join(',')}`;
}

/** The facet state the user set by hand wins over a parsed filter (the
 *  resolveQuery rule: explicit selections beat inference). */
export interface FacetPresence { categories: boolean; modes: boolean; severities: boolean; dateFloor: boolean }

export function nlChips(parsedFilters: SearchFilters, plan: TextPlan, facets: FacetPresence): NlChip[] {
  const out: NlChip[] = [];
  const kindFor = (k: keyof FacetPresence): NlChipKind => (facets[k] ? 'overridden' : 'filter');
  for (const c of parsedFilters.categories ?? []) out.push({ id: `cat:${c}`, kind: kindFor('categories'), label: CATEGORY_LABELS[c], removable: true });
  for (const m of parsedFilters.modes ?? []) out.push({ id: `mode:${m}`, kind: kindFor('modes'), label: m.charAt(0).toUpperCase() + m.slice(1), removable: true });
  if (parsedFilters.severities?.length) out.push({ id: 'sev', kind: kindFor('severities'), label: severityWord(parsedFilters.severities), removable: true });
  if (parsedFilters.dateFloor) out.push({ id: 'date', kind: kindFor('dateFloor'), label: sinceWord(parsedFilters.dateFloor), removable: true });
  for (const t of plan.matched) out.push({ id: `term:${t}`, kind: 'term', label: t, removable: true });
  for (const t of plan.unmatched) out.push({ id: `unm:${t}`, kind: 'unmatched', label: t, removable: false });
  return out;
}

/** Drop the filters / terms the user removed from the chip row. */
export function applyRemovals(parsed: NlParsed, plan: TextPlan, removed: ReadonlySet<string>): { text: string; filters: SearchFilters } {
  const f: SearchFilters = {};
  const cats = (parsed.filters.categories ?? []).filter((c) => !removed.has(`cat:${c}`));
  if (cats.length) f.categories = cats;
  const modes = (parsed.filters.modes ?? []).filter((m) => !removed.has(`mode:${m}`));
  if (modes.length) f.modes = modes;
  if (parsed.filters.severities?.length && !removed.has('sev')) f.severities = parsed.filters.severities;
  if (parsed.filters.dateFloor && !removed.has('date')) f.dateFloor = parsed.filters.dateFloor;
  const text = plan.applied.split(/\s+/).filter((w) => w && !removed.has(`term:${w}`)).join(' ');
  return { text, filters: f };
}

/** Merge the parsed filters under the hand-set facet filters (resolveQuery
 *  already encodes the facets; a facet the user set wins). */
export function mergeUnderFacets(facetFilters: SearchFilters, parsed: SearchFilters): SearchFilters {
  const out: SearchFilters = { ...parsed };
  for (const k of FILTER_KEYS) if (facetFilters[k] !== undefined) (out as Record<string, unknown>)[k] = facetFilters[k];
  return out;
}

export const facetPresence = (f: SearchFilters): FacetPresence => ({
  categories: !!f.categories?.length, modes: !!f.modes?.length, severities: !!f.severities?.length, dateFloor: !!f.dateFloor,
});
