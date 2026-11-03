/**
 * useNlSearch: the command bar's Meta AI edge as one hook. It owns
 * only the model call and its bookkeeping; the ranking is still the
 * lexical engine's (`search` from ../search/engine), run here on the
 * validated filters. SearchBox keeps its structured path unchanged and
 * swaps in `outcome` only when this hook has a validated parse.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { search, type SearchIndex, type SearchOutcome, type SearchFilters, type SearchCategory } from '../search/engine';
import { queryNl } from './client';
import { noteMetaCall } from './aiStatus';
import { recordAiCall } from './aiCalls';
import {
  applyRemovals, facetPresence, mergeUnderFacets, nlChips, planNlText, routeQuery, shouldAutoParse, unservedCategories,
  type NlChip, type NlOutcome, type TextPlan,
} from './nlQuery';
import type { LlmProvenance } from './provenance';

export type NlPhase = 'off' | 'ready' | 'loading' | 'done';

export interface NlView {
  phase: NlPhase;
  outcome: NlOutcome | null;
  /** Chips of the parsed query that are still applied. */
  chips: NlChip[];
  /** True when the user removed a parsed filter/term. */
  hasRemovals: boolean;
  /** The lexical engine's result over the validated parse; null unless parsed. */
  searchOutcome: SearchOutcome | null;
  unserved: SearchCategory[];
  provenance: LlmProvenance | null;
  /** Enter may (re)run the parse: not yet asked, or the last try timed out / was unreachable. */
  canRun: boolean;
  run: () => void;
  remove: (id: string) => void;
  removeLast: () => boolean;
  reset: () => void;
}

const TRANSIENT = new Set(['timeout', 'unreachable']);
export const AUTO_DEBOUNCE_MS = 500;
export const NL_DID = 'Parsed 1 query';

export function useNlSearch({ query, index, structOutcome, facetFilters, statusLive }: {
  query: string;
  index: SearchIndex;
  structOutcome: SearchOutcome;
  /** resolveQuery(...).filters: what the hand-set facets contribute. */
  facetFilters: SearchFilters;
  statusLive: boolean | null | undefined;
}): NlView {
  const raw = query.trim();
  const route = routeQuery(raw, structOutcome.totalResults);
  const [result, setResult] = useState<{ raw: string; outcome: NlOutcome } | null>(null);
  const [loading, setLoading] = useState<string | null>(null);
  const [removed, setRemoved] = useState<ReadonlySet<string>>(new Set());
  const cache = useRef(new Map<string, NlOutcome>());
  const abortRef = useRef<AbortController | null>(null);

  // A new query clears the removals and any in-flight call.
  useEffect(() => { setRemoved(new Set()); }, [raw]);
  useEffect(() => () => abortRef.current?.abort(), []);

  const run = useCallback(() => {
    if (route !== 'nl') return;
    const cached = cache.current.get(raw);
    if (cached) { setResult({ raw, outcome: cached }); return; }
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setLoading(raw);
    void queryNl(raw, ctrl.signal).then((outcome) => {
      if (ctrl.signal.aborted) return;
      if (!TRANSIENT.has(outcome.status)) cache.current.set(raw, outcome);
      setResult({ raw, outcome });
      setLoading((l) => (l === raw ? null : l));
      const prov = outcome.status === 'parsed' ? outcome.parsed.provenance : outcome.status === 'fallback' ? outcome.provenance : null;
      if (prov) recordAiCall({ surface: 'query', did: NL_DID, provenance: prov });
      noteMetaCall();
    });
  }, [route, raw]);

  // Free (RECORDED) parses fire as you type; a LIVE one waits for Enter.
  const auto = shouldAutoParse(statusLive);
  useEffect(() => {
    if (route !== 'nl' || !auto) return;
    if (result?.raw === raw || loading === raw) return;
    const t = setTimeout(run, AUTO_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [route, auto, raw, run, result, loading]);

  const outcome = route === 'nl' && result?.raw === raw ? result.outcome : null;
  const parsed = outcome?.status === 'parsed' ? outcome.parsed : null;

  const derived = useMemo(() => {
    if (!parsed) return null;
    const count = (q: string) => search(index, q, mergeUnderFacets(facetFilters, parsed.filters)).totalResults;
    const plan: TextPlan = planNlText(parsed.text, count);
    const eff = applyRemovals(parsed, plan, removed);
    const filters = mergeUnderFacets(facetFilters, eff.filters);
    const searchOutcome = search(index, eff.text, filters);
    const chips = nlChips(parsed.filters, plan, facetPresence(facetFilters)).filter((c) => !removed.has(c.id));
    return { searchOutcome, chips, unserved: unservedCategories(filters, index.counts), plan };
  }, [parsed, index, facetFilters, removed]);

  const remove = useCallback((id: string) => setRemoved((p) => new Set(p).add(id)), []);
  const removeLast = useCallback((): boolean => {
    const last = [...(derived?.chips ?? [])].reverse().find((c) => c.removable);
    if (!last) return false;
    setRemoved((p) => new Set(p).add(last.id));
    return true;
  }, [derived]);
  const reset = useCallback(() => setRemoved(new Set()), []);

  const phase: NlPhase = route !== 'nl' ? 'off' : loading === raw ? 'loading' : outcome ? 'done' : 'ready';
  const provenance = outcome?.status === 'parsed' ? outcome.parsed.provenance
    : outcome?.status === 'fallback' ? outcome.provenance : null;
  const canRun = phase === 'ready' || (phase === 'done' && !!outcome && TRANSIENT.has(outcome.status));
  return {
    phase, canRun, outcome, chips: derived?.chips ?? [], hasRemovals: removed.size > 0,
    searchOutcome: derived?.searchOutcome ?? null, unserved: derived?.unserved ?? [],
    provenance, run, remove, removeLast, reset,
  };
}
