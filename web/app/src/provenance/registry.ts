import { useSyncExternalStore } from 'react';
import type { CausalEvent, Chain, DisruptionSpec, ImpactFigure } from '../types';
import type { TruthWord } from '../shell/truthLabel';
import { labelWord } from '../copy';
import type { CheckRow, PlanDoc, RouteDoc } from '../panels/laneplanner/types';
import type { McJob, MetricStats } from '../panels/montecarlo/types';
import type { EvalDoc, EvalShipment } from '../panels/shipments/types';
import { sourceLineFor } from '../sources/credits';

/* PROVENANCE REGISTRY. A reference describes where one displayed number
 * comes from: inputs, engine profile and revision, run id and input hash, the
 * data source with publisher and as-of date, the label, and the formula when
 * the engine states one. A value the served document does not carry reads
 * "Not exposed" (value === null); nothing is derived or filled in here. The
 * module is pure apart from the one open/close store at the bottom. */

export const NOT_EXPOSED = 'Not exposed';

/** The five standing words. SIMULATED is the simulator's output; MODELLED the engine's model. */
export type ProvWord = TruthWord | 'SIMULATED';
export const PROV_WORDS: readonly ProvWord[] = ['MODELLED', 'SIMULATED', 'RECORDED', 'SNAPSHOT', 'LIVE'];
export const WORD_TEXT: Record<ProvWord, string> = { MODELLED: 'Modelled', SIMULATED: 'Simulated', RECORDED: 'Recorded', SNAPSHOT: 'Snapshot', LIVE: 'Live' };

export interface ProvRow { label: string; value: string | null; mono?: boolean }
export interface ProvSection { title: string; rows: ProvRow[] }

export interface ProvRef {
  /** Stable key (used for re-open). */
  id: string;
  /** What the number is, a noun phrase. */
  title: string;
  /** The number as displayed, with unit. */
  value: string;
  word: ProvWord | null;
  asOf: string | null;
  sections: ProvSection[];
  /** Plain-symbol formula, only where the engine states one. */
  formula: string | null;
  /** Late rows (a run journal), merged when it resolves; a failure leaves the sections as they are. */
  load?: () => Promise<ProvSection[]>;
}

const row = (label: string, value: string | number | null | undefined, mono = false): ProvRow => {
  const v = value === undefined || value === null || value === '' || (typeof value === 'number' && !Number.isFinite(value)) ? null : String(value);
  return { label, value: v, mono };
};
export { row as provRow };

/** A served scenario mode read as a label: `none` is "Not recorded", other
 *  lowercase codes read sentence case. */
export function scenarioModeWord(mode: string | null | undefined): string | null {
  const t = String(mode ?? '').trim();
  if (!t) return null;
  if (t.toLowerCase() === 'none') return 'Not recorded';
  return labelWord(t);
}

const num = (v: number | null | undefined, d = 1): string | null => (v == null || !Number.isFinite(v) ? null : v.toFixed(d));
const rng = (r: { low: number; median: number; high: number } | null | undefined, unit = 'd'): string | null =>
  r ? `${r.low.toFixed(1)} to ${r.high.toFixed(1)} ${unit}, median ${r.median.toFixed(1)} ${unit}` : null;
const wordOf = (label: string | null | undefined): ProvWord | null => {
  const l = String(label ?? '').toUpperCase();
  return l === 'SIMULATED' ? 'SIMULATED' : l === 'MODELLED' || l === 'MODELED' ? 'MODELLED' : l === 'RECORDED' || l === 'SOURCED' ? 'RECORDED' : null;
};

/* ── chain steps ────────────────────────────────────────────────────── */

export interface ChainProvCtx {
  chain: Chain; event: CausalEvent; spec?: DisruptionSpec | null; engineLabel?: string | null;
  placeName: (id: string) => string; runSource: string | null;
  loadJournal?: () => Promise<ProvSection[]>;
}
export function chainStepProvenance(c: ChainProvCtx, field: 'delay' | 'magnitude'): ProvRef {
  const e = c.event;
  const parent = e.parent_id == null ? null : c.chain.events.find((p) => p.seq === e.parent_id) ?? null;
  const value = field === 'delay' ? `${e.delay_d.toFixed(1)} d` : e.magnitude.toFixed(2);
  const sp = c.spec;
  return {
    id: `chain:${c.chain.run_id}:${e.seq}:${field}`,
    title: field === 'delay' ? `Delay at ${c.placeName(e.node_id)}` : `Magnitude at ${c.placeName(e.node_id)}`,
    value, word: 'MODELLED', asOf: null,
    formula: null,
    sections: [
      { title: 'Inputs', rows: [
        row('Place', c.placeName(e.node_id)),
        row('Step', e.seq + 1),
        row('Channel', e.channel),
        row('Reached from', parent ? c.placeName(parent.node_id) : e.parent_id == null ? 'Disruption origin' : null),
        row('Mechanism', e.mechanism),
        row('Lands', e.at ? e.at.replace('T', ' ').slice(0, 16) + ' UTC' : null),
        row('Disruption', sp ? `${sp.kind}, ${Math.round(sp.capacity_loss * 100)} % capacity, ${sp.duration_d} d, ${sp.shape}` : null),
      ] },
      { title: 'Engine', rows: [
        row('Engine', c.engineLabel ?? null),
        row('Run', c.chain.run_id, true),
        row('Stream hash', c.chain.stream_hash, true),
        row('Input hash', null),
      ] },
      { title: 'Source', rows: [
        row('Basis', c.runSource),
        row('Publisher', null),
        row('As of', null),
      ] },
    ],
    load: c.loadJournal,
  };
}

/* ── lane planner ───────────────────────────────────────────────────── */

export type LaneField = 'median' | 'range' | 'vs' | 'queue' | 'wait' | 'km';
export interface LaneProvCtx { doc: PlanDoc; route: RouteDoc | null; label: string; field: LaneField; display: string; word: ProvWord | null }
const LANE_TITLE: Record<LaneField, string> = {
  median: 'Median transit', range: 'Transit range', vs: 'Change against baseline', queue: 'Queue peak', wait: 'Wait at arrival', km: 'Route length',
};
export function lanePlanProvenance(c: LaneProvCtx): ProvRef {
  const { doc, route: r } = c;
  const lane = r?.lane;
  const speed = lane?.speed_kn;
  const formulaByField: Partial<Record<LaneField, string | null>> = {
    median: r ? 'total = sail + queue' : null,
    range: r ? 'total = sail + queue, taken per named variant' : null,
    vs: r ? 'change = total(route) - total(baseline)' : null,
    km: lane ? 'km = sum of leg lengths' : null,
  };
  const sources = (r?.km.sources ?? []).join(', ');
  return {
    id: `lane:${doc.plan_id}:${r?.id ?? 'baseline'}:${c.field}`,
    title: `${LANE_TITLE[c.field]}, ${c.label}`,
    value: c.display, word: c.word, asOf: null,
    formula: formulaByField[c.field] ?? null,
    sections: [
      { title: 'Inputs', rows: [
        row('Origin', doc.places.origin.name), row('Destination', doc.places.destination.name),
        row('Route', r?.label ?? c.label), row('Legs', r ? r.legs.length : null),
        row('Disruption', doc.disruption ? `${doc.disruption.name}, ${doc.disruption.duration_d} d, ${Math.round(doc.disruption.capacity_loss * 100)} % capacity` : 'None'),
        row('Sail', r ? rng(r.sail) : null),
        row('Queue', r?.queue ? rng(r.queue) : r ? '0.0 d' : null),
        row('Total', r ? rng(r.total) : null),
        row('Speeds', speed ? `${speed.slow} / ${speed.base} / ${speed.fast} kn` : null),
        row('Queue variants', doc.variants.queue.length ? doc.variants.queue.map((q) => q.profile).join(', ') : null),
      ] },
      { title: 'Engine', rows: [
        row('Engine', doc.engine_label), row('Plan', doc.plan_id, true),
        row('Input hash', doc.journal.inputs_sha256, true),
        row('Registry hash', doc.journal.engine2_registry_hash, true),
        row('Network hash', doc.journal.graph_hash, true),
      ] },
      { title: 'Source', rows: [
        row('Basis', r?.basis ?? null), row('Length sources', sources || null),
        row('Publisher', null), row('As of', null),
      ] },
    ],
  };
}
export const laneWord = (label: string | null | undefined): ProvWord | null => wordOf(label);


/* ── lane planner distance checks ───────────────────────────────────── */

export type CheckField = 'suez' | 'cape' | 'detour' | 'cut' | 'cited' | 'floor';
export interface CheckProvCtx { doc: PlanDoc; check: CheckRow; field: CheckField; display: string; /** the lane-kilometre pair row rather than the engine row */ lanePair?: boolean }
const CHECK_TITLE: Record<CheckField, string> = {
  suez: 'Suez route length', cape: 'Cape route length', detour: 'Cape detour length', cut: 'Distance cut, Suez against Cape', cited: 'Cited distance cut', floor: 'Great circle floor',
};
export function laneCheckProvenance(c: CheckProvCtx): ProvRef {
  const { doc, check: k } = c;
  const ref = c.lanePair ? k.lane?.reference ?? null : k.reference ?? null;
  const basis = c.lanePair ? k.lane?.label ?? null : k.engine?.label ?? null;
  const srcs = (c.lanePair ? k.lane?.sources : k.sources) ?? [];
  return {
    id: `lane-check:${doc.plan_id}:${k.id}${c.lanePair ? ':lane' : ''}:${c.field}`,
    title: `${CHECK_TITLE[c.field]}, ${k.id === 'great_circle_floor' ? 'floor check' : 'Cape against Suez'}`,
    value: c.display,
    word: c.field === 'cited' ? null : 'MODELLED',
    asOf: null,
    formula: c.field === 'detour' ? 'detour = Cape length - Suez length' : null,
    sections: [
      { title: 'Inputs', rows: [
        row('Origin', doc.places.origin.name), row('Destination', doc.places.destination.name),
        row('Check', k.id.replace(/_/g, ' ')), row('State', k.state),
        row('Basis', basis),
        row('Reference', c.field === 'cited' || c.field === 'cut' ? ref?.label ?? null : null),
        row('Reference state', c.field === 'cited' ? ref?.label_state ?? null : null),
      ] },
      { title: 'Engine', rows: [
        row('Engine', doc.engine_label), row('Plan', doc.plan_id, true),
        row('Input hash', doc.journal.inputs_sha256, true), row('Network hash', doc.journal.graph_hash, true),
      ] },
      { title: 'Source', rows: [
        row('Length sources', srcs.length ? srcs.join(', ') : null),
        row('Reference source', c.field === 'cited' ? ref?.source ?? null : null),
        row('Publisher', null), row('As of', null),
      ] },
    ],
  };
}

/* ── impact figures ─────────────────────────────────────────────────── */

export interface ImpactProvCtx { fig: ImpactFigure; title: string; display: string; scope?: string | null; runId?: string | null; /** derivation as display text */ derivation?: string | null }
export function impactFigureProvenance(c: ImpactProvCtx): ProvRef {
  const f = c.fig;
  return {
    id: `impact:${c.runId ?? 'static'}:${c.scope ?? ''}:${c.title}`,
    title: c.scope ? `${c.title}, ${c.scope}` : c.title,
    value: c.display,
    word: f.class === 'MODELLED' ? 'MODELLED' : null,
    asOf: f.as_of ?? null,
    formula: null,
    sections: [
      { title: 'Inputs', rows: [
        row('Scope', c.scope ?? null), row('Unit', f.unit), row('Figure class', f.class),
        row('Derivation', c.derivation ?? (typeof f.derivation === 'string' ? f.derivation : null)),
      ] },
      { title: 'Engine', rows: [row('Engine', null), row('Run', c.runId ?? null, true), row('Input hash', null)] },
      { title: 'Source', rows: [row('Data source', f.source), row('Publisher', null), row('As of', f.as_of ?? null)] },
    ],
  };
}

/* ── shipments ──────────────────────────────────────────────────────── */

export type ShipField = 'shipments' | 'affected' | 'added' | 'value' | 'row-added';
export interface ShipProvCtx { doc: EvalDoc; field: ShipField; display: string; shipment?: EvalShipment | null }
const SHIP_TITLE: Record<ShipField, string> = {
  shipments: 'Shipments read', affected: 'Affected shipments', added: 'Added transit, median per shipment', value: 'Value in affected shipments', 'row-added': 'Added transit',
};
export function shipmentProvenance(c: ShipProvCtx): ProvRef {
  const { doc, shipment: sh } = c; const s = doc.summary;
  const formula: Partial<Record<ShipField, string>> = {
    affected: 'affected = delayed + rerouted',
    value: 'value = sum of declared value over affected shipments',
    'row-added': 'added = disrupted transit - baseline transit',
  };
  return {
    id: `ship:${doc.schema}:${sh?.id ?? 'all'}:${c.field}`,
    title: SHIP_TITLE[c.field] + (sh ? `, ${sh.id}` : ''),
    value: c.display, word: 'MODELLED', asOf: null,
    formula: formula[c.field] ?? null,
    sections: [
      { title: 'Inputs', rows: [
        row('Scenario', doc.scenario.label), row('Mode', scenarioModeWord(doc.scenario.mode)),
        row('Place', doc.scenario.place?.name ?? null), row('Duration', doc.scenario.duration_d == null ? null : `${doc.scenario.duration_d} d`),
        row('Shipments', s.n), row('Served', s.n_served), row('Not served', s.n_not_served), row('Unresolved', s.n_unresolved),
        ...(sh ? [row('Route', sh.route_label), row('Baseline transit', rng(sh.baseline_days)), row('Disrupted transit', rng(sh.disrupted_days)), row('Added transit', rng(sh.added_days)), row('Declared value', sh.value_usd == null ? null : `${Math.round(sh.value_usd).toLocaleString('en-US')} USD`)] : [row('Added transit, range of medians', s.added_days_median_range ? `${num(s.added_days_median_range.low)} to ${num(s.added_days_median_range.high)} d` : null)]),
      ] },
      { title: 'Engine', rows: [row('Engine', null), row('Run', null), row('Input hash', null)] },
      { title: 'Source', rows: [row('Basis', 'Shipment list held in this browser'), row('Publisher', null), row('As of', null)] },
    ],
  };
}

/* ── Monte Carlo ────────────────────────────────────────────────────── */

export type McStat = 'p10' | 'p50' | 'p90' | 'mean' | 'range';
export interface McProvCtx { job: McJob; metric: string; metricLabel: string; stat: McStat; display: string; recorded: boolean; m: MetricStats }
export function monteCarloProvenance(c: McProvCtx): ProvRef {
  const { job, m } = c; const q = job.request; const eng = job.engine;
  const d = q.disruption;
  return {
    id: `mc:${job.job_id}:${c.metric}:${c.stat}`,
    title: `${c.metricLabel}, ${c.stat === 'range' ? 'range' : c.stat === 'mean' ? 'mean' : c.stat.toUpperCase()}`,
    value: c.display, word: c.recorded ? 'RECORDED' : 'SIMULATED', asOf: null,
    formula: c.stat === 'mean' ? 'mean = sum of sample values / n' : c.stat === 'range' ? 'range = lowest to highest sample value' : `${c.stat.toUpperCase()} = ${c.stat.slice(1)}th percentile of the n sample values`,
    sections: [
      { title: 'Inputs', rows: [
        row('Disruption', `${d.kind} at ${d.node_id}, ${d.duration_d} d${d.capacity_loss == null ? '' : `, ${Math.round(d.capacity_loss * 100)} % capacity`}`),
        row('Samples', m.n), row('Seed', q.seed),
        row('Uncertain inputs', job.inputs.length ? job.inputs.map((i) => i.name.replace(/_/g, ' ')).join(', ') : null),
        row('Overrides', q.distributions && Object.keys(q.distributions).length ? Object.keys(q.distributions).join(', ') : 'None'),
      ] },
      { title: 'Engine', rows: [
        row('Engine', eng?.engine_label ?? `Rev ${q.engine_rev}`), row('Profile', eng?.profile ?? q.profile ?? null),
        row('Run', job.job_id, true), row('Input hash', null),
      ] },
      { title: 'Source', rows: [row('Basis', c.recorded ? 'Recorded job document' : 'Simulator run on this server'), row('Publisher', null), row('As of', null)] },
    ],
  };
}

/* ── open/close store ───────────────────────────────────────────────── */

let current: ProvRef | null = null;
const subs = new Set<() => void>();
const emit = () => subs.forEach((f) => f());
export function openProvenance(ref: ProvRef): void { current = ref; emit(); }
export function closeProvenance(): void { if (current) { current = null; emit(); } }
export function currentProvenance(): ProvRef | null { return current; }
export function useProvenance(): ProvRef | null {
  return useSyncExternalStore((f) => { subs.add(f); return () => { subs.delete(f); }; }, () => current, () => null);
}

/** The journal rows for a chain run (engine label, parameters, map and registry versions), from GET /run/{id}/journal. */
export function journalSections(j: { engine_label?: string; settings?: Record<string, unknown> } | null | undefined): ProvSection[] {
  if (!j) return [];
  const st = (j.settings ?? {}) as Record<string, unknown>;
  const p = (st.params ?? {}) as Record<string, unknown>;
  const pr = Object.entries(p).map(([k, v]) => `${k.replace(/_/g, ' ')} ${String(v)}`).join(', ');
  return [{ title: 'Run journal', rows: [
    row('Engine', j.engine_label ?? null), row('Parameters', pr || null),
    row('Map version', st.map_version == null ? null : String(st.map_version)),
    row('Registry hash', st.plugin_registry_hash == null ? null : String(st.plugin_registry_hash), true),
  ] }];
}

/** The credit line the drawer ends with: the registry credits for every source the reference names
 *  (title, section titles and row values), else the stated publisher, else the stored-data line. */
export function provenanceSourceLine(ref: ProvRef, sections: readonly ProvSection[] = ref.sections): string {
  const texts = [ref.title, ...sections.flatMap((s) => [s.title, ...s.rows.map((r) => r.value)])];
  const publisher = sections.flatMap((s) => s.rows).find((r) => r.label === 'Publisher' && r.value)?.value ?? null;
  const hits = sourceLineFor(...texts);
  return hits.startsWith('Source: SupermAP stored data') && publisher ? `Source: ${publisher}` : hits;
}
