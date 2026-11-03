/**
 * SEARCH QUERY SEAM — the one place a raw
 * search-box string becomes a resolved query for the lexical engine.
 *
 * CONTRACT: resolveQuery(raw, facets) → { text, filters }. Today the
 * resolution is exactly parseQualifier + the facet merge: a typed
 * qualifier's category wins over the facet chips, a qualifier's text
 * is what the engine ranks, and every facet the UI offers (category,
 * mode, severity, date floor) lands in SearchFilters.
 *
 * An NL front-end (post-Oct-26 key) slots in HERE and nowhere else:
 * it produces the same resolved shape — a text the lexical engine
 * ranks plus validated filters — and the lexical engine remains the
 * only ranker. The NL layer may parse language; it may never rank,
 * invent an entity, or answer in prose. A query it cannot resolve
 * to served entities falls back to this exact lexical resolution.
 */
import { parseQualifier, type SearchCategory, type SearchFilters } from './engine';

/** The facet state the SearchBox chips carry (UI-side shape). */
export interface FacetState {
  categories?: SearchCategory[];
  modes?: Array<'sea' | 'air' | 'ground'>;
  /** "SEV 4+" toggle → the served severities it keeps, [4, 5]. */
  severities?: number[];
  /** "Last 7 days" toggle → ISO instant, events only. */
  dateFloor?: string;
}

export interface ResolvedQuery { text: string; filters: SearchFilters }

export function resolveQuery(raw: string, facets: FacetState = {}): ResolvedQuery {
  const parsed = parseQualifier(raw);
  const filters: SearchFilters = {};
  // Qualifier category wins over the chip selection (including 'All').
  if (parsed.category) filters.categories = [parsed.category];
  else if (facets.categories?.length) filters.categories = facets.categories;
  if (facets.modes?.length) filters.modes = facets.modes;
  if (facets.severities?.length) filters.severities = facets.severities;
  if (facets.dateFloor) filters.dateFloor = facets.dateFloor;
  return { text: raw, filters };
}
