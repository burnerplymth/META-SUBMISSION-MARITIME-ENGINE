/**
 * ADVANCED SEARCH — engine core. Pure TypeScript:
 * no React, no store, no fetching. Builds one index over ONLY what the
 * product serves or already loads (places from the served world payload,
 * the disruption feed, the money layer's exposures, Ledger entries,
 * session-known runs, commodity lenses, satellite scan areas, and the
 * static feature registry in ./registry.ts) and answers grouped,
 * relevancy-ranked type-ahead queries over it.
 *
 * Ranking law (fixed order): exact code match > exact name > prefix >
 * token match > substring > typo-tolerant fuzzy. Fuzzy has a hard
 * confidence floor — below it the honest answer is "no results", never
 * an invented match. One refinement: a prefix match on a
 * GOODS class label scores at the label band (90, just under exact
 * name) — goods labels are canonical phrases from a closed taxonomy,
 * so a goods-label query must always surface its goods row.
 *
 * Display discipline: every string
 * this module emits for the screen names places by name and kind
 * ("Port · Egypt"), actions in plain words ("Open", "View on map"), and
 * never renders a raw identifier. Identifiers live only inside action
 * payloads, where the store's actions consume them. Caller-built titles
 * (runs, Ledger entries) are required from the caller for the same
 * reason: if the app cannot name it in human words, it is not indexed.
 *
 * The cmdbar dispatches each result's `action` through the
 * store's existing actions / workspace plumbing — one code path, no
 * parallel navigation.
 */
import { DESK_FEATURES, LAYER_FEATURES, LAYOUT_FEATURES, PANEL_FEATURES, TOOL_FEATURES } from './registry';

// ── Categories ────────────────────────────────────────────────────────
export type SearchCategory = 'places' | 'panels' | 'events' | 'companies' | 'ledger' | 'runs' | 'watchlists' | 'goods';
export const CATEGORY_ORDER: SearchCategory[] = ['places', 'panels', 'events', 'companies', 'ledger', 'runs', 'watchlists', 'goods'];
export const CATEGORY_LABELS: Record<SearchCategory, string> = {
  places: 'Places', panels: 'Panels & features', events: 'Events',
  companies: 'Companies', ledger: 'Chain ledger', runs: 'Runs',
  watchlists: 'Watchlists', goods: 'Goods',
};

// ── Source records (exactly what the app already holds) ──────────────
/** A place, from the served world payload (GET /graph records as-is). */
export interface PlaceInput {
  id: string; name: string;
  kind: string;            // 'port' | 'air_hub' | 'ground_node' | 'chokepoint'
  mode: string;            // 'sea' | 'air' | 'ground'
  tier?: string;           // 'core' | 'shell' (absent reads as core)
  aliases?: string[];
  /** Served on every record (capacity + its unit, verbatim). */
  capacity?: number; capacity_unit?: string; capacityUnit?: string;
  /** Carried by the build censuses; the served payload does not include
   *  it yet — read when present, never invented when absent. */
  country?: string;
}
export interface EventInput {
  id: string; headline: string; placeId?: string;
  kind?: string; source?: string; severity?: number; status?: string; detectedAt?: string;
}
export interface CompanyInput {
  ticker: string; company: string; placeId: string; exposureKind?: string;
  /** The mapping's filing, as served (form + filed date). */
  filingForm?: string; filedDate?: string;
}
export interface LedgerInput {
  entryId: string;
  /** Human title built by the caller from served data (the locked
   *  disruption's headline / place). Never an identifier. */
  title: string;
  lockedAt?: string; status?: string; placeId?: string;
  /** Locked steps in the locked chain, when the caller knows it. */
  stepCount?: number;
}
export interface RunInput {
  runId: string;
  /** Human label built by the caller (place + day, the runRef idiom). */
  title: string;
  subtitle?: string;
}
export interface LensInput { lensId: string; label: string; placeCount?: number }
export interface ScanInput { placeId: string; name: string; nowDate?: string; baselineDate?: string }
/** One stored run from the served run catalogue (GET /runs): the
 *  server-built human title is rendered verbatim; `facts` is the
 *  server-served fact bag (disruption kind, seed place name, event /
 *  variant counts, tick info) — primitive values are displayed as
 *  served, absent ones simply do not appear. The id is matching-only:
 *  it enters the code/fragment haystacks, never a display string. */
export interface CatalogueRunInput {
  runId: string; kind: string; title: string;
  createdAt?: string; status?: string;
  facts?: Record<string, unknown>;
}
export interface ExplorationInput {
  id: string; name: string; members?: number; expansions?: number; updatedAt?: string;
}
export interface WatchlistInput { id: string; name: string; placeCount?: number }
export interface GoodsInput { code: string; label: string }
export interface ReplayInput { eventId: string; title: string; observedSteps?: number }

export interface SearchSources {
  places?: PlaceInput[];
  events?: EventInput[];
  companies?: CompanyInput[];
  ledger?: LedgerInput[];
  runs?: RunInput[];
  catalogueRuns?: CatalogueRunInput[];
  explorations?: ExplorationInput[];
  watchlists?: WatchlistInput[];
  goods?: GoodsInput[];
  replays?: ReplayInput[];
  lenses?: LensInput[];
  scans?: ScanInput[];
  /** Include the static panel/layout/layer registry (default true). */
  features?: boolean;
}

// ── Actions (dispatched through the store) ────────────────
export type SearchAction =
  | { type: 'select-place'; placeId: string }
  | { type: 'view-on-map'; placeId: string }
  | { type: 'open-panel'; panelId: string }
  | { type: 'open-layout'; layout: string }
  | { type: 'open-tool'; tool: string; modes: Array<'live' | 'sim'> }
  | { type: 'toggle-layer'; layer: string }
  | { type: 'set-basemap'; basemap: 'dark' | 'light' | 'sat' }
  | { type: 'apply-lens'; lensId: string }
  | { type: 'open-event'; eventId: string; placeId?: string }
  | { type: 'open-company'; ticker: string; placeId: string }
  | { type: 'open-ledger'; entryId: string }
  | { type: 'open-run'; runId: string }
  | { type: 'open-sim-run'; runId: string }
  | { type: 'open-scan'; placeId: string }
  | { type: 'open-desk'; desk: string }
  | { type: 'open-exploration'; id: string }
  | { type: 'open-watchlist'; id: string }
  | { type: 'open-goods'; code: string }
  | { type: 'open-replay'; eventId: string };

export interface SearchResult {
  category: SearchCategory;
  /** Row flavour within the category ('place', 'scan', 'panel', …). */
  kind: string;
  title: string;
  subtitle: string;
  /** Display code chip where the terminal idiom has one (desk code,
   *  ticker, or a place's own registry code) — never an internal id. */
  code?: string;
  actionLabel: string;
  action: SearchAction;
  score: number;
  /** Preview-card facts for the highlighted row (the LSEG summary-card
   *  idiom): served values only, in display order. Absent facts are
   *  omitted, never filled with a placeholder figure. */
  facts?: Array<{ label: string; value: string }>;
  /** 'Shell place' marks a shell-tier place in the result, per the
   *  tier-honesty rule; core places carry 'Core place'. */
  tierLabel?: 'Core place' | 'Shell place';
  /** True for the "View on map" companion row of a place result. */
  companion?: boolean;
}

export interface SearchGroup { category: SearchCategory; label: string; total: number; results: SearchResult[] }
export interface SearchOutcome { groups: SearchGroup[]; totalResults: number }
export interface SearchFilters {
  categories?: SearchCategory[];
  modes?: Array<'sea' | 'air' | 'ground'>;
  tiers?: Array<'core' | 'shell'>;
  /** Event severities to keep (exact served values, e.g. [4, 5] for
   *  "SEV 4+"). Applies to events only; other categories unaffected. */
  severities?: number[];
  /** ISO floor: events detected before this instant are dropped.
   *  Applies to events only; other categories unaffected. */
  dateFloor?: string;
  limitPerGroup?: number;
}

// ── Normalisation & matching ─────────────────────────────────────────
/** Lowercase, diacritic-stripped, punctuation-collapsed form. */
export const norm = (s: string): string =>
  s.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase()
    .replace(/[^a-z0-9]+/g, ' ').trim().replace(/\s+/g, ' ');
const tokens = (s: string): string[] => { const n = norm(s); return n ? n.split(' ') : []; };

/** Damerau–Levenshtein (optimal string alignment) distance. */
export function editDistance(a: string, b: string): number {
  const m = a.length, n = b.length;
  if (!m) return n; if (!n) return m;
  const d: number[][] = Array.from({ length: m + 1 }, (_, i) => [i, ...new Array<number>(n).fill(0)]);
  for (let j = 1; j <= n; j++) d[0][j] = j;
  for (let i = 1; i <= m; i++) for (let j = 1; j <= n; j++) {
    const cost = a[i - 1] === b[j - 1] ? 0 : 1;
    d[i][j] = Math.min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost);
    if (i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1])
      d[i][j] = Math.min(d[i][j], d[i - 2][j - 2] + 1);
  }
  return d[m][n];
}
/** Typo budget by token length; short tokens get none (too ambiguous). */
const maxTypos = (len: number): number => (len <= 3 ? 0 : len <= 5 ? 1 : len <= 9 ? 2 : 3);

// Score bands — the ranking law, highest first.
const S_CODE = 100, S_NAME = 95, S_PREFIX = 80, S_TOKEN_PREFIX = 75,
  S_TOKEN = 60, S_SUBSTRING = 50, S_FUZZY = 40;
/** Goods-label band: the goods classes are a CLOSED
 *  taxonomy (8 classes) whose labels are canonical query phrases
 *  ("mineral fuels & oils (HS 27)"). A query that prefix-matches a
 *  goods label scores here — above the generic prefix band, below
 *  exact name — so a goods-label query always surfaces its goods row
 *  at the top of the cut instead of being crowded out by token/fuzzy
 *  place-name matches, while an exact place name (S_NAME) still
 *  outranks it: names outrank, the qualifier category still wins. */
const S_LABEL = 90;

interface IndexedEntry {
  category: SearchCategory; kind: string;
  title: string; subtitle: string; code?: string;
  actionLabel: string; action: SearchAction; tierLabel?: SearchResult['tierLabel'];
  mode?: string; tier?: 'core' | 'shell';
  /** For kind === 'place': the place's own kind ('port', 'air_hub', …). */
  placeKind?: string;
  nameNorm: string; nameTokens: string[];
  codeNorms: string[];            // exact-code haystack (aliases, tickers, desk codes)
  extraTokens: string[];          // keywords, country, kind words, source words…
  companionFor?: string;          // place id when this row is its map companion template
  facts?: Array<{ label: string; value: string }>;
  /** Events only: the served severity + detection instant, carried
   *  so the severity / date-floor facets can filter on served facts. */
  severity?: number; dateIso?: string;
  /** Matching-only haystack: the entry's identifier with every
   *  separator stripped, so a run is findable by any fragment of its
   *  id. NEVER rendered — titles/subtitles/facts stay human. */
  idNorm?: string;
}

/** Score one entry against a normalised query; 0 = no match. */
function scoreEntry(e: IndexedEntry, q: string, qTokens: string[]): number {
  if (!q) return 0;
  if (e.codeNorms.includes(q)) return S_CODE;
  if (e.nameNorm === q) return S_NAME;
  if (e.category === 'goods' && e.nameNorm.startsWith(q)) return S_LABEL;
  if (e.nameNorm.startsWith(q)) return S_PREFIX;
  if (qTokens.length === 1 && e.nameTokens.some((t) => t.startsWith(q))) return S_TOKEN_PREFIX;
  // Token match: every query token is a name/extra token or a prefix of one.
  const hay = [...e.nameTokens, ...e.extraTokens];
  if (qTokens.every((qt) => hay.some((t) => t === qt || t.startsWith(qt)))) return S_TOKEN;
  if (q.length >= 3 && e.nameNorm.includes(q)) return S_SUBSTRING;
  // Id fragment: any run of the stripped identifier (a hash fragment
  // typed or pasted) matches at the substring band — below every
  // human-name band, so a name always beats an id fragment.
  const qAlnum = q.replace(/ /g, '');
  if (e.idNorm && qAlnum.length >= 3 && e.idNorm.includes(qAlnum)) return S_SUBSTRING;
  // Fuzzy: every query token must land within its typo budget of some
  // name token (headline/keyword tokens do not fuzzy-match — too noisy).
  // The band is distance-weighted: a one-typo match outranks a two-typo
  // match, so 'savanah' finds Savannah ahead of Salalah.
  let totalDist = 0;
  const fuzzyOk = qTokens.every((qt) => {
    const budget = maxTypos(qt.length);
    if (!budget) return false;
    let best = Infinity;
    for (const t of e.nameTokens) {
      if (Math.abs(t.length - qt.length) > budget) continue;
      const d = editDistance(qt, t);
      if (d <= budget && d / Math.max(qt.length, t.length) <= 0.35) best = Math.min(best, d);
    }
    if (best === Infinity) return false;
    totalDist += best;
    return true;
  });
  if (fuzzyOk && totalDist > 0) return S_FUZZY - totalDist;
  return 0;
}

// ── Display helpers (this module's own; they align with the shared
//    human-text formatters in src/format.ts where they overlap) ──────
const KIND_LABELS: Record<string, string> = {
  port: 'Port', air_hub: 'Airport', ground_node: 'Ground hub', chokepoint: 'Chokepoint',
};
export const kindLabel = (kind: string): string => KIND_LABELS[kind] ?? 'Place';
const MONS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
export const fmtDay = (iso?: string): string | null => {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null
    : `${String(d.getUTCDate()).padStart(2, '0')} ${MONS[d.getUTCMonth()]} ${String(d.getUTCFullYear()).slice(2)}`;
};
const prettyWords = (k?: string): string | null => {
  if (!k) return null;
  const w = k.replace(/_/g, ' ');
  return w.charAt(0).toUpperCase() + w.slice(1);
};
/** A place's own short registry code (USSAV, ADD, DKAAL…) when it has one. */
const placeCode = (aliases: string[]): string | undefined =>
  aliases.map((a) => a.trim().toUpperCase()).find((a) => /^[A-Z0-9]{3,6}$/.test(a));
const MODE_WORDS: Record<string, string> = { sea: 'Sea', air: 'Air', ground: 'Ground' };
/* One formatter for the whole index: constructing an Intl.NumberFormat per place cost 0.2 s of a boot profile at 4x slowdown. */
const NUM_FORMAT = new Intl.NumberFormat('en-US', { maximumFractionDigits: 1 });
const num = (n: number): string => NUM_FORMAT.format(n);
type Fact = { label: string; value: string };
const fact = (label: string, value: string | null | undefined): Fact[] =>
  (value ? [{ label, value }] : []);

// ── Index build ──────────────────────────────────────────────────────
export interface SearchIndex { entries: IndexedEntry[]; counts: Record<SearchCategory | 'scans', number> }

export function buildSearchIndex(src: SearchSources): SearchIndex {
  const entries: IndexedEntry[] = [];
  const counts: Record<SearchCategory | 'scans', number> =
    { places: 0, panels: 0, events: 0, companies: 0, ledger: 0, runs: 0, watchlists: 0, goods: 0, scans: 0 };
  const placeName = new Map<string, string>();
  for (const p of src.places ?? []) placeName.set(p.id, p.name);
  const at = (placeId?: string): string | null => (placeId ? placeName.get(placeId) ?? null : null);

  // Places — plus a "View on map" companion template per place.
  for (const p of src.places ?? []) {
    const tier: 'core' | 'shell' = p.tier === 'shell' ? 'shell' : 'core';
    const aliases = p.aliases ?? [];
    const subtitle = [kindLabel(p.kind), p.country, tier === 'shell' ? 'Shell place' : null]
      .filter(Boolean).join(' · ');
    const unit = p.capacityUnit ?? p.capacity_unit;
    const base: IndexedEntry = {
      category: 'places', kind: 'place', title: p.name, subtitle,
      code: placeCode(aliases), actionLabel: 'Open',
      action: { type: 'select-place', placeId: p.id },
      tierLabel: tier === 'shell' ? 'Shell place' : 'Core place',
      facts: [
        { label: 'Kind', value: kindLabel(p.kind) },
        ...fact('Mode', MODE_WORDS[p.mode]),
        ...fact('Country', p.country),
        ...fact('Capacity', typeof p.capacity === 'number' ? `${num(p.capacity)}${unit ? ` ${unit}` : ''}` : null),
        { label: 'Tier', value: tier === 'shell' ? 'Shell place' : 'Core place' },
      ],
      mode: p.mode, tier, placeKind: p.kind,
      nameNorm: norm(p.name), nameTokens: tokens(p.name),
      codeNorms: aliases.map(norm).filter((a) => a && !a.includes(' ')),
      extraTokens: [...aliases.flatMap(tokens), ...tokens(kindLabel(p.kind)),
        ...(p.country ? tokens(p.country) : []), ...tokens(p.mode), tier],
    };
    entries.push(base);
    entries.push({ ...base, kind: 'view-on-map', actionLabel: 'View on map',
      action: { type: 'view-on-map', placeId: p.id }, companionFor: p.id });
    counts.places++;
  }

  // Satellite scan areas — a row of their own inside Places.
  for (const sc of src.scans ?? []) {
    const dates = [sc.nowDate, sc.baselineDate].filter(Boolean).join(' vs ');
    entries.push({
      category: 'places', kind: 'scan', title: sc.name,
      subtitle: ['Satellite imagery', dates || null].filter(Boolean).join(' · '),
      facts: [...fact('Latest scene', sc.nowDate), ...fact('Baseline scene', sc.baselineDate)],
      actionLabel: 'View imagery', action: { type: 'open-scan', placeId: sc.placeId },
      nameNorm: norm(sc.name), nameTokens: tokens(sc.name), codeNorms: [],
      extraTokens: ['satellite', 'imagery', 'scan'],
    });
    counts.scans++;
  }

  // Features — static registry (skipped when features === false).
  if (src.features !== false) {
    for (const f of PANEL_FEATURES) {
      entries.push({
        category: 'panels', kind: 'panel', title: f.title,
        subtitle: f.layouts.length ? `Panel · ${f.layouts[0]}` : 'Panel', code: f.code, actionLabel: 'Open',
        facts: f.layouts.length ? [{ label: 'Appears in', value: f.layouts.join(', ') }] : [],
        action: { type: 'open-panel', panelId: f.panelId },
        nameNorm: norm(f.title), nameTokens: tokens(f.title),
        codeNorms: [norm(f.code)], extraTokens: f.keywords.flatMap(tokens),
      });
      counts.panels++;
    }
    for (const t of TOOL_FEATURES) {
      const where = t.modes.length === 2 ? 'Live and Simulation' : t.modes[0] === 'sim' ? 'Simulation' : 'Live';
      entries.push({
        category: 'panels', kind: 'tool', title: t.title,
        subtitle: `Tool · ${where}`, code: t.code, actionLabel: 'Open',
        facts: [{ label: 'Available in', value: where }],
        action: { type: 'open-tool', tool: t.toolId, modes: t.modes },
        nameNorm: norm(t.title), nameTokens: tokens(t.title),
        codeNorms: [norm(t.code)], extraTokens: t.keywords.flatMap(tokens),
      });
      counts.panels++;
    }
    for (const d of DESK_FEATURES) {
      entries.push({
        category: 'panels', kind: 'desk', title: d.title,
        subtitle: 'Analysis desk · Analysis Desks', code: d.code, actionLabel: 'Open',
        facts: [{ label: 'Appears in', value: 'Analysis Desks' }],
        action: { type: 'open-desk', desk: d.desk },
        nameNorm: norm(d.title), nameTokens: tokens(d.title),
        codeNorms: [norm(d.code)], extraTokens: d.keywords.flatMap(tokens),
      });
      counts.panels++;
    }
    for (const l of LAYOUT_FEATURES) {
      entries.push({
        category: 'panels', kind: 'layout', title: l.label ?? l.name, subtitle: 'Layout',
        code: l.code, actionLabel: 'Open layout', action: { type: 'open-layout', layout: l.name },
        nameNorm: norm(l.label ?? l.name), nameTokens: tokens(l.label ?? l.name),
        codeNorms: [norm(l.code)], extraTokens: [...l.keywords.flatMap(tokens), ...(l.label ? tokens(l.name) : [])],
      });
      counts.panels++;
    }
    for (const l of LAYER_FEATURES) {
      const isBasemap = l.key.startsWith('basemap:');
      entries.push({
        category: 'panels', kind: isBasemap ? 'basemap' : 'layer', title: l.title,
        subtitle: isBasemap ? 'Map style' : 'Map layer', actionLabel: isBasemap ? 'Show' : 'Show layer',
        action: isBasemap
          ? { type: 'set-basemap', basemap: l.key.slice('basemap:'.length) as 'dark' | 'light' | 'sat' }
          : { type: 'toggle-layer', layer: l.key },
        nameNorm: norm(l.title), nameTokens: tokens(l.title),
        codeNorms: [], extraTokens: l.keywords.flatMap(tokens),
      });
      counts.panels++;
    }
  }

  // Lenses — dynamic features, served by the app at boot.
  for (const lens of src.lenses ?? []) {
    entries.push({
      category: 'panels', kind: 'lens', title: lens.label,
      subtitle: ['Commodity lens', lens.placeCount ? `${lens.placeCount} places` : null].filter(Boolean).join(' · '),
      facts: [...fact('Places covered', lens.placeCount ? String(lens.placeCount) : null)],
      actionLabel: 'Apply', action: { type: 'apply-lens', lensId: lens.lensId },
      nameNorm: norm(lens.label), nameTokens: tokens(lens.label),
      codeNorms: [], extraTokens: ['lens', 'commodity'],
    });
    counts.panels++;
  }

  // Events — the disruption feed, as served.
  for (const ev of src.events ?? []) {
    const place = at(ev.placeId);
    entries.push({
      category: 'events', kind: 'event', title: ev.headline,
      subtitle: [place, prettyWords(ev.kind), fmtDay(ev.detectedAt)].filter(Boolean).join(' · '),
      facts: [
        ...fact('Severity', typeof ev.severity === 'number' ? `SEV ${ev.severity}` : null),
        ...fact('Status', ev.status ? prettyWords(ev.status.toLowerCase()) : null),
        ...fact('At', place),
        ...fact('Detected', fmtDay(ev.detectedAt)),
      ],
      actionLabel: 'Open', action: { type: 'open-event', eventId: ev.id, placeId: ev.placeId },
      severity: ev.severity, dateIso: ev.detectedAt,
      nameNorm: norm(ev.headline), nameTokens: tokens(ev.headline),
      codeNorms: [], extraTokens: [...(place ? tokens(place) : []),
        ...(ev.source ? tokens(ev.source) : []), ...(ev.kind ? tokens(ev.kind) : [])],
    });
    counts.events++;
  }

  // Companies — the money layer's served exposures.
  for (const c of src.companies ?? []) {
    const place = at(c.placeId);
    entries.push({
      category: 'companies', kind: 'company', title: c.company,
      subtitle: [c.ticker.toUpperCase(), place ? `Exposure at ${place}` : null].filter(Boolean).join(' · '),
      code: c.ticker.toUpperCase(), actionLabel: 'Open',
      facts: [
        { label: 'Ticker', value: c.ticker.toUpperCase() },
        ...fact('Exposure', c.exposureKind ? prettyWords(c.exposureKind) : null),
        ...fact('Exposed place', place),
        ...fact('Filing', c.filingForm ? `${c.filingForm}${c.filedDate ? ` · ${fmtDay(c.filedDate) ?? c.filedDate}` : ''}` : null),
      ],
      action: { type: 'open-company', ticker: c.ticker, placeId: c.placeId },
      nameNorm: norm(c.company), nameTokens: tokens(c.company),
      codeNorms: [norm(c.ticker)], extraTokens: place ? tokens(place) : [],
    });
    counts.companies++;
  }

  // Ledger entries — caller-titled, locked-date subtitled.
  for (const l of src.ledger ?? []) {
    entries.push({
      category: 'ledger', kind: 'ledger', title: l.title,
      subtitle: [l.lockedAt ? `Locked ${fmtDay(l.lockedAt) ?? ''}` : null,
        l.status ? prettyWords(l.status.toLowerCase()) : null].filter(Boolean).join(' · '),
      facts: [
        ...fact('Locked', fmtDay(l.lockedAt)),
        ...fact('Status', l.status ? prettyWords(l.status.toLowerCase()) : null),
        ...fact('Locked steps', typeof l.stepCount === 'number' ? String(l.stepCount) : null),
      ],
      actionLabel: 'Open', action: { type: 'open-ledger', entryId: l.entryId },
      nameNorm: norm(l.title), nameTokens: tokens(l.title),
      codeNorms: [], extraTokens: at(l.placeId) ? tokens(at(l.placeId)!) : [],
    });
    counts.ledger++;
  }

  // Runs — caller-titled (human place + day labels only). The id is
  // matching-only (code + fragment haystacks), never a display string.
  const idHaystack = (id: string) => id.replace(/[^a-z0-9]/gi, '').toLowerCase();
  for (const r of src.runs ?? []) {
    entries.push({
      category: 'runs', kind: 'run', title: r.title, subtitle: r.subtitle ?? 'Stored run',
      actionLabel: 'Open', action: { type: 'open-run', runId: r.runId },
      nameNorm: norm(r.title), nameTokens: tokens(r.title),
      codeNorms: [norm(r.runId)], extraTokens: tokens(r.runId), idNorm: idHaystack(r.runId),
    });
    counts.runs++;
  }

  // Run catalogue — every stored run the server names (GET /runs).
  // Kinds are told apart in the subtitle (kind word + day) and the
  // preview facts (the served fact bag, primitives only); the title
  // is the server's human title verbatim and no code chip is shown —
  // the raw id stays in the action payload and the matching haystack.
  const RUN_KIND_WORDS: Record<string, string> = {
    chain: 'Chain run', ensemble: 'Ensemble', 'ensemble-tree': 'Ensemble tree', sim: 'Simulation run',
  };
  const RUN_KIND_KINDS: Record<string, string> = {
    chain: 'chain-run', ensemble: 'ensemble', 'ensemble-tree': 'ensemble-tree', sim: 'sim-run',
  };
  for (const r of src.catalogueRuns ?? []) {
    const kindWord = RUN_KIND_WORDS[r.kind] ?? 'Stored run';
    const bag = r.facts ?? {};
    const countBit = (keys: string[], word: string): string | null => {
      for (const k of keys) if (typeof bag[k] === 'number') return `${bag[k]} ${word}`;
      return null;
    };
    const factRows: Fact[] = [
      { label: 'Kind', value: kindWord },
      ...fact('Created', fmtDay(r.createdAt)),
      ...fact('Status', r.status ? prettyWords(r.status.toLowerCase()) : null),
      ...Object.entries(bag)
        .filter(([, v]) => typeof v === 'string' || typeof v === 'number')
        .map(([k, v]) => ({ label: prettyWords(k) ?? k, value: String(v) })),
    ];
    entries.push({
      category: 'runs', kind: RUN_KIND_KINDS[r.kind] ?? 'run', title: r.title,
      subtitle: [kindWord, fmtDay(r.createdAt),
        countBit(['event_count', 'events'], 'events'),
        countBit(['variant_count', 'variants'], 'variants'),
      ].filter(Boolean).join(' · '),
      facts: factRows,
      actionLabel: 'Open',
      action: r.kind === 'sim'
        ? { type: 'open-sim-run', runId: r.runId }
        : (r.kind === 'ensemble' || r.kind === 'ensemble-tree')
          ? { type: 'open-panel', panelId: 'ensemble' }
          : { type: 'open-run', runId: r.runId },
      nameNorm: norm(r.title), nameTokens: tokens(r.title),
      codeNorms: [norm(r.runId)],
      extraTokens: [...tokens(r.runId), ...tokens(kindWord),
        ...Object.values(bag).filter((v) => typeof v === 'string').flatMap((v) => tokens(v as string))],
      idNorm: idHaystack(r.runId),
    });
    counts.runs++;
  }

  // Explorations — saved ensemble explorations, opened in the ENS panel.
  for (const x of src.explorations ?? []) {
    entries.push({
      category: 'runs', kind: 'exploration', title: x.name,
      subtitle: ['Exploration', typeof x.members === 'number' ? `${x.members} members` : null,
        fmtDay(x.updatedAt)].filter(Boolean).join(' · '),
      facts: [
        ...fact('Members', typeof x.members === 'number' ? String(x.members) : null),
        ...fact('Deepenings', typeof x.expansions === 'number' ? String(x.expansions) : null),
        ...fact('Updated', fmtDay(x.updatedAt)),
      ],
      actionLabel: 'Open', action: { type: 'open-exploration', id: x.id },
      nameNorm: norm(x.name), nameTokens: tokens(x.name),
      codeNorms: [norm(x.id)], extraTokens: [...tokens(x.id), 'exploration'], idNorm: idHaystack(x.id),
    });
    counts.runs++;
  }

  // Replays — the graded historical replays, opened in the REPLAY desk.
  for (const rp of src.replays ?? []) {
    entries.push({
      category: 'runs', kind: 'replay', title: rp.title,
      subtitle: ['Replay', typeof rp.observedSteps === 'number' ? `${rp.observedSteps} observed steps` : null]
        .filter(Boolean).join(' · '),
      facts: [...fact('Observed steps', typeof rp.observedSteps === 'number' ? String(rp.observedSteps) : null)],
      actionLabel: 'Open', action: { type: 'open-replay', eventId: rp.eventId },
      nameNorm: norm(rp.title), nameTokens: tokens(rp.title),
      codeNorms: [], extraTokens: ['replay'],
    });
    counts.runs++;
  }

  // Watchlists — their own category; the subtitle is the place count.
  for (const w of src.watchlists ?? []) {
    entries.push({
      category: 'watchlists', kind: 'watchlist', title: w.name,
      subtitle: typeof w.placeCount === 'number' ? `${w.placeCount} place${w.placeCount === 1 ? '' : 's'}` : 'Watchlist',
      facts: [...fact('Places', typeof w.placeCount === 'number' ? String(w.placeCount) : null)],
      actionLabel: 'Open', action: { type: 'open-watchlist', id: w.id },
      nameNorm: norm(w.name), nameTokens: tokens(w.name),
      codeNorms: [norm(w.id)], extraTokens: [...tokens(w.id), 'watchlist'], idNorm: idHaystack(w.id),
    });
    counts.watchlists++;
  }

  // Goods classes — the IMPACT taxonomy, code chip = the HS code.
  for (const g of src.goods ?? []) {
    entries.push({
      category: 'goods', kind: 'goods', title: g.label, subtitle: 'Goods class',
      code: g.code, actionLabel: 'Open',
      facts: [{ label: 'Class', value: g.code }],
      action: { type: 'open-goods', code: g.code },
      nameNorm: norm(g.label), nameTokens: tokens(g.label),
      codeNorms: [norm(g.code)], extraTokens: ['goods', 'class'],
    });
    counts.goods++;
  }

  return { entries, counts };
}

// ── Qualifiers ───────────────────────────────────────────────────────
interface ParsedQuery { text: string; category?: SearchCategory; mode?: string; kind?: string }
const QUALIFIERS: Record<string, Partial<ParsedQuery>> = {
  place: { category: 'places' }, places: { category: 'places' },
  port: { category: 'places', kind: 'port' }, ports: { category: 'places', kind: 'port' },
  airport: { category: 'places', kind: 'air_hub' }, airports: { category: 'places', kind: 'air_hub' },
  hub: { category: 'places', kind: 'ground_node' },
  chokepoint: { category: 'places', kind: 'chokepoint' }, chokepoints: { category: 'places', kind: 'chokepoint' },
  sea: { category: 'places', mode: 'sea' }, air: { category: 'places', mode: 'air' }, ground: { category: 'places', mode: 'ground' },
  panel: { category: 'panels' }, panels: { category: 'panels' }, feature: { category: 'panels' }, layout: { category: 'panels' },
  event: { category: 'events' }, events: { category: 'events' }, news: { category: 'events' },
  company: { category: 'companies' }, companies: { category: 'companies' },
  ticker: { category: 'companies' }, stock: { category: 'companies' },
  ledger: { category: 'ledger' }, prediction: { category: 'ledger' },
  run: { category: 'runs' }, runs: { category: 'runs' },
  desk: { category: 'panels' }, desks: { category: 'panels' },
  watchlist: { category: 'watchlists' }, watchlists: { category: 'watchlists' },
  goods: { category: 'goods' }, good: { category: 'goods' },
};
export function parseQualifier(raw: string): ParsedQuery {
  const m = raw.trim().match(/^([a-z]+):\s*(.*)$/i);
  if (m && QUALIFIERS[m[1].toLowerCase()]) return { text: m[2], ...QUALIFIERS[m[1].toLowerCase()] };
  return { text: raw };
}

// ── Search ───────────────────────────────────────────────────────────
const DEFAULT_LIMIT = 6;

export function search(index: SearchIndex, rawQuery: string, filters: SearchFilters = {}): SearchOutcome {
  const parsed = parseQualifier(rawQuery);
  const q = norm(parsed.text);
  const qTokens = tokens(parsed.text);
  const limit = filters.limitPerGroup ?? DEFAULT_LIMIT;
  // A typed qualifier's category wins over a facet category (the
  // facet merge in query.ts already encodes this; the engine keeps
  // the same law so a direct caller cannot invert it).
  const cats = parsed.category ? [parsed.category] : (filters.categories ?? CATEGORY_ORDER);
  // Empty text browses ONLY under an explicit restriction (a qualifier
  // or a category facet) — a bare empty query is no query at all.
  const browse = !q && (parsed.category !== undefined || filters.categories !== undefined);
  if (!q && !browse) return { groups: [], totalResults: 0 };

  const byCat = new Map<SearchCategory, { total: number; rows: SearchResult[] }>();
  for (const cat of cats) {
    const scored: Array<{ e: IndexedEntry; score: number }> = [];
    for (const e of index.entries) {
      if (e.category !== cat || e.companionFor) continue;
      if (parsed.mode && e.mode !== parsed.mode) continue;
      // Kind qualifier (port:/airport:/hub:/chokepoint:) narrows place rows.
      if (parsed.kind && e.kind === 'place' && e.placeKind !== parsed.kind) continue;
      if (filters.modes && e.mode && !filters.modes.includes(e.mode as 'sea')) continue;
      if (filters.tiers && e.tier && !filters.tiers.includes(e.tier)) continue;
      // Severity / date-floor facets apply to events only — every
      // other category is unaffected by them, by construction.
      if (e.category === 'events') {
        if (filters.severities?.length && (e.severity == null || !filters.severities.includes(e.severity))) continue;
        if (filters.dateFloor && (!e.dateIso || e.dateIso < filters.dateFloor)) continue;
      }
      const score = q ? scoreEntry(e, q, qTokens) : 1; // browse mode: every in-scope row scores flat
      if (score > 0) scored.push({ e, score });
    }
    const narrowed = scored;
    narrowed.sort((a, b) => b.score - a.score
      || tierRank(a.e) - tierRank(b.e)
      || a.e.title.length - b.e.title.length
      || a.e.title.localeCompare(b.e.title));
    const rows: SearchResult[] = [];
    for (const { e, score } of narrowed.slice(0, limit)) {
      rows.push(toResult(e, score));
      if (e.kind === 'place') {
        const comp = index.entries.find((x) => x.companionFor === placeIdOf(e));
        if (comp) rows.push({ ...toResult(comp, score), companion: true });
      }
    }
    if (narrowed.length) byCat.set(cat, { total: narrowed.length, rows });
  }

  const groups: SearchGroup[] = [...byCat.entries()]
    .sort((a, b) => bestScore(b[1].rows) - bestScore(a[1].rows)
      || CATEGORY_ORDER.indexOf(a[0]) - CATEGORY_ORDER.indexOf(b[0]))
    .map(([category, g]) => ({ category, label: CATEGORY_LABELS[category], total: g.total, results: g.rows }));
  return { groups, totalResults: groups.reduce((n, g) => n + g.total, 0) };
}

const tierRank = (e: IndexedEntry): number => (e.tier === 'shell' ? 1 : 0);
const bestScore = (rows: SearchResult[]): number => rows.reduce((m, r) => Math.max(m, r.score), 0);
const placeIdOf = (e: IndexedEntry): string =>
  e.action.type === 'select-place' ? e.action.placeId : '';

function toResult(e: IndexedEntry, score: number): SearchResult {
  return {
    category: e.category, kind: e.kind, title: e.title, subtitle: e.subtitle,
    code: e.code, actionLabel: e.actionLabel, action: e.action, score,
    tierLabel: e.tierLabel, facts: e.facts,
  };
}

/** Flat row order for keyboard navigation. */
export const flattenResults = (outcome: SearchOutcome): SearchResult[] =>
  outcome.groups.flatMap((g) => g.results);
