// ── Display formatters ─────────────────────────────────────────────────────
// The ONLY place raw served identifiers are turned into the human text the
// interface shows. Node ids (sea_savannah), run ids (run_…), endpoint
// paths, contract codes and hashes belong to the API and to exports —
// never to the screen. Panels import from here rather than formatting
// one-off, so every surface (and the SIM / impact surfaces after it)
// renders names, dates and sources the same way.
import { NODE_NAMES, nodeName } from './seed';
import type { Chain, GraphData } from './types';

const MONS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** "2026-10-21T01:04:57Z" → "21 Oct 26" (UTC). Unparseable input degrades
 *  to its date part rather than leaking the raw timestamp. */
export const fmtDay = (iso: string): string => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? String(iso).slice(0, 10)
    : `${String(d.getUTCDate()).padStart(2, '0')} ${MONS[d.getUTCMonth()]} ${String(d.getUTCFullYear()).slice(2)}`;
};

/** "2026-10-21T01:04:57Z" → "21 Oct · 01:04 UTC". */
export const fmtDayTime = (iso: string): string => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? String(iso).slice(0, 16).replace('T', ' ')
    : `${fmtDay(iso)} · ${String(d.getUTCHours()).padStart(2, '0')}:${String(d.getUTCMinutes()).padStart(2, '0')} UTC`;
};

interface NamedNode { id: string; name?: string }
const graphNodes = (g?: GraphData | null): NamedNode[] =>
  (g?.nodes as unknown as NamedNode[] | undefined) ?? [];

/** Human label for a node id: the served graph node's own name first,
 *  then the curated name table, then a prettified id (never the raw id). */
export function nodeLabel(id: string, graph?: GraphData | null): string {
  if (!id) return '—';
  const n = graphNodes(graph).find((x) => x.id === id);
  if (n?.name) return n.name;
  return nodeName(id);
}

/** The chain's root event (the disruption itself). */
export const rootEvent = (chain: Chain) =>
  chain.events.find((e) => e.parent_id === null) ?? chain.events[0];

/** Human reference for a run: "Run · Suez Canal · 23 Mar 23" — root node
 *  + the day the disruption lands, both from the served chain. The raw
 *  run id stays in the API and in exports, where it belongs. */
export function runRef(chain: Chain | null | undefined, graph?: GraphData | null): string {
  if (!chain || !chain.events.length) return '—';
  const root = rootEvent(chain);
  return `Run · ${nodeLabel(root.node_id, graph)} · ${fmtDay(root.at)}`;
}

/** Provenance stamp in the terminal idiom: state word + the source's
 *  human name ("LIVE · Disruption feed", "SNAPSHOT · SEC EDGAR"). */
export const srcStamp = (word: string, source: string) => `${word} · ${source}`;

/** Disruption kinds and other served enums → sentence words
 *  ("capacity_cut" → "Capacity cut"). */
export const prettyKind = (k: string): string => {
  const w = String(k).replace(/_/g, ' ');
  return w.charAt(0).toUpperCase() + w.slice(1);
};

/** Scorecard methodology keys → readable labels. */
const KEY_LABELS: Record<string, string> = {
  delay_accuracy: 'Graded steps', node_coverage: 'Places observed',
  false_positives: 'Modelled, not observed', evidence_rule: 'Evidence rule',
  effective_spec: 'Effective scenario', determinism: 'Determinism',
};
export const prettyKey = (k: string): string =>
  KEY_LABELS[k] ?? prettyKind(k).replace(/ d$/, ' (d)');

/** Served status enums → human states ("AWAITING_META_KEY" →
 *  "Awaiting key"). Unknown statuses degrade to spaced,
 *  capitalised words rather than the raw enum. */
export function statusLabel(status: string): string {
  if (/AWAITING.*KEY/i.test(status)) return 'Awaiting key';
  const w = String(status).replace(/_/g, ' ').toLowerCase();
  return w.charAt(0).toUpperCase() + w.slice(1);
}

/** Source (adapter) names for the screen: brand spellings for the known
 *  sources, spaced title case otherwise — never the raw registry key
 *  ("portwatch_daily" → "PortWatch Daily"). */
const ADAPTER_LABELS: Record<string, string> = {
  portwatch: 'PortWatch', portwatch_daily: 'PortWatch Daily', gdelt: 'GDELT',
  gdacs: 'GDACS', noaa: 'NOAA', cbp: 'CBP', aisstream: 'AISStream',
  opensky: 'OpenSky', ntad: 'NTAD',
};
export const adapterLabel = (a: string): string =>
  ADAPTER_LABELS[a.toLowerCase()] ?? a.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());

/** Alert-rule source fields accept the label the user sees ("PortWatch
 *  Daily") or the registry key; resolve to the key the API matches on. */
export function resolveAdapterInput(input: string, known: string[]): string {
  const v = input.trim();
  if (!v) return v;
  if (known.includes(v)) return v;
  const low = v.toLowerCase();
  return known.find((k) => adapterLabel(k).toLowerCase() === low) ?? v;
}

/** Satellite detection counts as served. Fixed shape: vessels_now /
 *  vessels_baseline are the ANCHORAGE counts (the congestion quantity);
 *  frame_* are whole-frame large-vessel totals. Returns null for
 *  anything that is not a counted detection block, so callers fall back
 *  to showing the served value verbatim. */
export interface SatCounts {
  vesselsNow: number; vesselsBaseline: number; delta: number;
  deltaFrac: number | null; berthNow?: number; berthBaseline?: number;
  frameNow?: number; frameBaseline?: number; yardNow?: string;
}
export function satCounts(counts: unknown): SatCounts | null {
  if (!counts || typeof counts !== 'object') return null;
  const c = counts as Record<string, unknown>;
  const num = (v: unknown): number | null =>
    typeof v === 'number' && Number.isFinite(v) ? v : null;
  const now = num(c.vessels_now), base = num(c.vessels_baseline);
  if (now === null || base === null) return null;
  const delta = now - base;
  return {
    vesselsNow: now, vesselsBaseline: base, delta,
    deltaFrac: base > 0 ? delta / base : (delta > 0 ? 1 : 0),
    berthNow: num(c.berth_now) ?? undefined,
    berthBaseline: num(c.berth_baseline) ?? undefined,
    frameNow: num(c.frame_vessels_now) ?? undefined,
    frameBaseline: num(c.frame_vessels_baseline) ?? undefined,
    yardNow: typeof c.yard_now === 'string' ? c.yard_now : undefined,
  };
}

/** LLM transport → what the user is actually looking at. */
export const transportLabel = (t?: string | null): string => {
  if (!t) return '';
  if (t.toLowerCase().includes('fixture')) return 'RECORDED';
  if (t.toLowerCase().includes('http')) return 'LIVE';
  return t.toUpperCase();
};

/** The forecast negation is allowed only inside the MODELLED label (the
 *  mandatory Monte Carlo label is kept verbatim). In any other served prose
 *  it reads "Conditional on stated inputs". */
const NEG_KEEP = 'Modelled, conditional on the stated input ranges;' + ' not a forecast.';
export function softenNegation(text: string): string {
  if (!/forecast/i.test(text)) return text;
  const keep = text.includes(NEG_KEEP);
  const masked = keep ? text.split(NEG_KEEP).join('\u0000KEEP\u0000') : text;
  const out = masked
    .replace(/(^|[.!?\u0000]\s+)Not (?:an? )?forecasts?/g, '$1Conditional on stated inputs')
    .replace(/\s*[,;]?\s*(?:and\s+)?not (?:an? )?forecasts?/gi, '; conditional on stated inputs')
    .replace(/^\s*;\s*/, '');
  return keep ? out.split('\u0000KEEP\u0000').join(NEG_KEEP) : out;
}

/** A small dollar estimate: zero reads $0.00 (never $0.0000); a positive amount
 *  under a hundredth of a cent reads "<$0.0001"; up to ten cents four places,
 *  above that two. */
export function fmtUsdSmall(n: number): string {
  if (n === 0) return '$0.00';
  if (n > 0 && n < 0.0001) return '<$0.0001';
  return `$${n < 0.1 ? n.toFixed(4) : n.toFixed(2)}`;
}

/** "2026-11-02" -> "2 Nov 2026" (UTC): an unambiguous date for a field whose native control prints 11/02/2026. */
export const fmtDateLong = (iso: string): string => {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso ?? ''));
  if (!m) return '';
  const mon = MONS[Number(m[2]) - 1];
  return mon ? `${Number(m[3])} ${mon} ${m[1]}` : '';
};

/** A delay in days, one decimal, never an engine float (6.666667 -> "6.7"). */
export const fmtDelayD = (d: number): string => (Number.isFinite(d) ? d.toFixed(1) : '—');

/** A dimensionless magnitude: at most two decimals, no trailing zeros (1.0000 -> "1", 0.5000 -> "0.5"). */
export function fmtMagnitude(m: number): string {
  if (!Number.isFinite(m)) return '—';
  return String(Number(m.toFixed(2)));
}

/** The engine-room vocabulary is banned from the interface: a place is a
 *  place (or a port / airport / hub / chokepoint), a connection is a
 *  leg, the whole is the network. Applied to every served prose
 *  string that reaches the screen. */
export function vocab(text: string): string {
  return softenNegation(text)
    .replace(/\bnodes\b/gi, 'places')
    .replace(/\bnode\b/gi, 'place')
    .replace(/\bedges\b/gi, 'legs')
    .replace(/\bedge\b/gi, 'leg')
    .replace(/\bgraph\b/gi, 'network');
}

/** Replace every known node id in a prose string with its human label
 *  (longest ids first so prefixes can't shadow), then the vocabulary
 *  pass. Used on served prose, whose facts stay intact — only the
 *  identifiers and engine-room words change. */
export function humaniseIds(text: string, graph?: GraphData | null): string {
  let out = text;
  /* A pair whose id is not in the text changes nothing (split then join returns the same string), so it is skipped with a
   * substring test; the sorted pair list is built once per node list instead of on every call (measured: 1.5 s of a 16 s
   * CPU profile of the boot at 4x slowdown, the largest single function, mostly sorting about 1,000 pairs per headline). */
  for (const [id, label] of idLabelPairs(graph)) if (out.indexOf(id) !== -1) out = out.split(id).join(label);
  return vocab(out);
}

const NO_NODES: NamedNode[] = [];
const pairCache = new WeakMap<object, Array<[string, string]>>();
/** [id, label] pairs, longest id first so a prefix can never shadow a longer id; same order as before the cache. */
function idLabelPairs(graph?: GraphData | null): Array<[string, string]> {
  const nodes = (graph?.nodes as unknown as NamedNode[] | undefined) ?? NO_NODES;
  const hit = pairCache.get(nodes);
  if (hit) return hit;
  const pairs: Array<[string, string]> = [
    ...Object.entries(NODE_NAMES),
    ...nodes.filter((n) => n.name).map((n) => [n.id, n.name as string] as [string, string]),
  ];
  pairs.sort((a, b) => b[0].length - a[0].length);
  pairCache.set(nodes, pairs);
  return pairs;
}

/** Agent text for display: run ids dropped, ranges as "13 to 28 d", engine floats rounded to one decimal, place ids to names. */
export function humaniseAgentText(text: string, graph?: GraphData | null): string {
  const out = String(text ?? '')
    .replace(/\s*\(\s*run\s+run_[a-z0-9]+\s*\)/gi, '')
    .replace(/\brun_[a-z0-9]{8,}\s*:\s*/gi, '')
    .replace(/\brun_[a-z0-9]{8,}\b/gi, 'the run')
    .replace(/(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)(\s*d\b)/g, (_m, a: string, b: string, u: string) => `${a} to ${b}${u}`)
    .replace(/\b(\d+)\.(\d{2,})\b/g, (_m, i: string, f: string) => Number(`${i}.${f}`).toFixed(1))
    .replace(/\s*\((?:sea|air|gnd|chk)_[a-z0-9_]+\)/g, '');
  return humaniseIds(out, graph);
}

/** The explain narrative arrives with its own machine prefix in recorded
 *  mode ("FIXTURE EXPLAIN (transport=fixture — …). Run run_…: …"). The
 *  panel states the recorded/live fact itself, in plain words, so the
 *  prefix and the run id come off here; the narrative's content — steps,
 *  channels, delays — is untouched apart from id → name substitution. */
export function cleanNarrative(text: string, graph?: GraphData | null): string {
  let out = text
    .replace(/^\s*FIXTURE EXPLAIN\s*\([^)]*\)\s*\.?\s*/i, '')
    .replace(/^\s*Run\s+run_[a-z0-9]+\s*:\s*/i, '')
    .replace(/\brun_[a-z0-9]{8,}\b/g, 'the run')
    // Step numbers read 1-based on screen (the order badge, the Explain table).
    .replace(/\bseq (\d+)\b/g, (_m, n: string) => `step ${Number(n) + 1}`)
    // Delays print to one decimal, never as engine floats (6.666667 d).
    .replace(/(\d+\.\d{2,})(\s*d\b)/g, (_m, n: string, u: string) => `${Number(n).toFixed(1)}${u}`);
  return humaniseIds(out, graph);
}

/** Served analytical prose (scorecard methodology, caveats, miss
 *  explanations) is written about the machine-readable artifacts, so it
 *  names their fields and build codes. The facts are the product's
 *  honesty record and stay; the field/code tokens are spelled out in
 *  words for the screen. The verbatim prose remains in the API and in
 *  exports. */
export function humaniseProse(text: string, graph?: GraphData | null): string {
  return humaniseIds(text, graph)
    .replace(/\((?:[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+),\s*(\d{4}-\d{2}-\d{2})\)/g, '($1)')
    .replace(/\bobserved_delay_d\b/g, 'observed delay (d)')
    .replace(/\bexpected_duration_d\b/g, 'expected duration (d)')
    .replace(/\bmean_abs_error_d\b/g, 'mean absolute error (d)')
    .replace(/\bdelay_d\b/g, 'delay (d)')
    .replace(/\brun_id\b/g, 'run id')
    .replace(/\bnode_id\b/g, 'place')
    .replace(/\bqueue_sensitivity\b/g, 'queue sensitivity')
    .replace(/\bC4\b/g, 'causal-chain')
    .replace(/\bbyte-identical scorecard\.json\b/g, 'the scorecard file, identical byte for byte')
    .replace(/\bscorecard\.json\b/g, 'the scorecard file')
    .replace(/\bscorecard\.md\b/g, 'the scorecard notes')
    .replace(/\bcapacity_loss\b/g, 'capacity loss')
    .replace(/\bcapacity_share\b/g, 'capacity share')
    .replace(/\bduration_d\b/g, 'duration (d)')
    .replace(/\bevidence_url\b/g, 'source link')
    .replace(/\bevidence_source\b/g, 'evidence source')
    .replace(/\bevidence_rule\b/g, 'evidence rule');
}

/** Raw exception text a feed adapter can serve as its note. */
const RAW_FAILURE = /URLError|urlopen error|Errno \d+|Traceback|Exception\b|\bHTTP \d{3} after|after \d+ attempt/i;

/** Short reason code(s) for a served source note: a state label, never
 *  the exception text. Several codes join with a middle dot. Returns null
 *  when the note carries nothing the codes name. */
export function reasonCode(note: string | null | undefined): string | null {
  if (!note) return null;
  const codes: string[] = [];
  const http = /HTTP\s*(\d{3})/i.exec(note);
  if (/NEEDS-KEY/i.test(note)) codes.push('NEEDS-KEY');
  if (/NEEDS-CONFIG/i.test(note)) codes.push('NEEDS-CONFIG');
  if (http) codes.push(`HTTP ${http[1]}`);
  if (/timed? ?out|timeout/i.test(note)) codes.push('TIMEOUT');
  if (/Errno 104|reset by peer/i.test(note)) codes.push('CONNECTION RESET');
  else if (/URLError|urlopen|Errno \d+|connection|SSL|resolve/i.test(note)) codes.push('NETWORK ERROR');
  if (/HTML page, not CSV|refusing to parse/i.test(note)) codes.push('BAD PAYLOAD');
  if (/last-good snapshot/i.test(note)) codes.push('LAST-GOOD SNAPSHOT');
  if (/by design|static by nature/i.test(note)) codes.push('BY DESIGN');
  if (/\bsample\b/i.test(note)) codes.push('SAMPLE');
  if (/authenticated/i.test(note)) codes.push('AUTHENTICATED');
  return codes.length ? codes.join(' · ') : null;
}

/** Served diagnostic notes (source-health prose) carry a few machine
 *  tokens — build codes in parentheses, environment-variable names,
 *  snake_case class codes. The note's facts stay; those tokens are
 *  normalised for display. The verbatim note remains in the API. */
export function humaniseNote(note: string): string {
  if (RAW_FAILURE.test(note)) return reasonCode(note) ?? 'SOURCE ERROR';
  return vocab(note
    .replace(/\s*\([A-Z]\d+(?:-[A-Z]\d+)?\)/g, '')
    .replace(/\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b/g, 'an API key')
    .replace(/\b[a-z]+(?:_[a-z]+)+:/g, (m) => m.replace(/_/g, ' ')));
}

/** Node form fields accept a place NAME (what the user sees everywhere)
 *  or a raw id; resolve to the id the API needs. Unresolvable input
 *  passes through untouched so the server's own validation answers. */
export function resolveNodeInput(input: string, graph?: GraphData | null): string {
  const v = input.trim();
  if (!v) return v;
  const nodes = graphNodes(graph);
  if (nodes.some((n) => n.id === v) || NODE_NAMES[v]) return v;
  const low = v.toLowerCase();
  const byGraph = nodes.find((n) => n.name && n.name.toLowerCase() === low);
  if (byGraph) return byGraph.id;
  const byTable = Object.entries(NODE_NAMES).find(([, name]) => name.toLowerCase() === low);
  return byTable ? byTable[0] : v;
}

/** Name options for node pickers (datalists show names, never ids). */
export function nodeNameOptions(graph?: GraphData | null): string[] {
  const names = new Set<string>(Object.values(NODE_NAMES));
  for (const n of graphNodes(graph)) if (n.name) names.add(n.name);
  return [...names].sort();
}
