/**
 * ADVANCED SEARCH — static copy + row chips.
 *
 * Every fixed string the search surface renders lives here, in one
 * place, so the wording can be checked in one file
 * against the vocabulary law (a place is a place, a connection is a route, the
 * whole is the network — never the engine-room words) and the
 * raw-identifier ban (no served id, no internal key, ever reaches the
 * screen). Result titles/subtitles/facts are NOT copy — they come
 * from the engine, built only from served values.
 */
import type { SearchResult } from './engine';

export const SEARCH_COPY = {
  placeholder: 'Search places, panels, events, companies, runs',
  ariaLabel: 'Command search',
  resultsLabel: 'Search results',
  commandsLabel: 'Commands',
  /** Empty-query state (the summoned dropdown before any typing): scope and qualifiers as chip rows. */
  hintTitle: 'Search scope',
  hintScope: ['Places', 'Panels', 'Events', 'Companies', 'Chain ledger', 'Runs'],
  hintQualifiersTitle: 'Qualifiers',
  hintQualifiers: ['port:', 'airport:', 'chokepoint:', 'ticker:', 'ledger:', 'run:'],
  /** Honest floor: below the matcher's confidence there is no result. */
  noResults: 'No results',
  /** Facet chip row. */
  facetAll: 'All',
  facetSev: 'Severity 4+',
  facetLast7: 'Last 7 days',
  /** Footer furniture (the keys themselves are drawn as key caps). */
  footerSelect: 'Select',
  footerOpen: 'Open',
  footerClose: 'Close',
  narrowNote: 'Refine query',
} as const;

export const footerShowing = (shown: number, total: number): string =>
  `${shown} of ${total} result${total === 1 ? '' : 's'}`;

/** The card footer: the action under the Enter key. */
export const cardEnter = (actionLabel: string): string => `Enter · ${actionLabel}`;

/** Short type chip for a result row — the desk idiom (mono code chip).
 *  Panels and layouts carry their own desk/function code as the chip;
 *  a place's chip comes from its served Kind fact (Port / Airport /
 *  Ground hub / Chokepoint), never from its internal key. */
const PLACE_KIND_CHIPS: Record<string, string> = {
  Port: 'PORT', Airport: 'AIR', 'Ground hub': 'GND', Chokepoint: 'CHK',
};

export function chipFor(r: SearchResult): string {
  switch (r.kind) {
    case 'place': {
      const kind = r.facts?.find((f) => f.label === 'Kind')?.value;
      return (kind && PLACE_KIND_CHIPS[kind]) || 'PLC';
    }
    case 'view-on-map': return 'MAP';
    case 'scan': return 'SAT';
    case 'panel':
    case 'layout': return r.code ?? 'PNL';
    case 'layer': return 'LYR';
    case 'basemap': return 'MAP';
    case 'lens': return 'LENS';
    case 'event': return 'NWS';
    case 'company': return 'CO';
    case 'ledger': return 'LDG';
    case 'run': return 'RUN';
    case 'desk': return r.code ?? 'DSK';
    case 'tool': return r.code ?? 'TOL';
    case 'chain-run': return 'RUN';
    case 'ensemble': return 'ENS';
    case 'ensemble-tree': return 'ENS';
    case 'sim-run': return 'SIM';
    case 'exploration': return 'EXP';
    case 'replay': return 'RPL';
    case 'watchlist': return 'WATCH';
    case 'goods': return r.code ?? 'GDS';
    default: return '··';
  }
}
