/* PORT TWIN scenario model: the six what-ifs a person can pick, the form
 * they fill, the API item it becomes, the
 * checks that mirror the server's refusals (the server stays the authority:
 * a refusal is shown, never clamped here), and which of them is in force at
 * a given hour of the run (the map shows closures and outages while they
 * last). Times are hours from t = 0, the end of the warm-up. */
import type { ScenarioItem, SimGroup, SimProfileDoc } from './types';

export type ScenarioType = 'surge' | 'berth_closure' | 'crane_scale' | 'strike' | 'channel_closure' | 'yard_outage';
export const SCENARIO_TYPES: readonly ScenarioType[] = ['surge', 'berth_closure', 'crane_scale', 'strike', 'channel_closure', 'yard_outage'];
export const SCENARIO_LABEL: Record<ScenarioType, string> = {
  surge: 'Surge', berth_closure: 'Berth closure', crane_scale: 'Crane scale', strike: 'Strike', channel_closure: 'Channel closure', yard_outage: 'Yard outage',
};

export interface ScenarioForm {
  type: ScenarioType;
  factor: number;
  startH: number;
  durationH: number;
  /** berth_closure: the group; crane_scale, strike: '' = every group. */
  group: string;
  /** berth_closure: berths closed. 0 = all. */
  berths: number;
}

export interface FieldSpec { factor?: { min: number; max: number; step: number; minExclusive?: boolean; maxExclusive?: boolean }; window: boolean; group: 'none' | 'one' | 'optional'; berths: boolean }
export const FIELDS: Record<ScenarioType, FieldSpec> = {
  surge: { factor: { min: 0.1, max: 5, step: 0.1 }, window: true, group: 'none', berths: false },
  berth_closure: { window: true, group: 'one', berths: true },
  crane_scale: { factor: { min: 0, max: 2, step: 0.1, minExclusive: true }, window: true, group: 'optional', berths: false },
  strike: { factor: { min: 0, max: 1, step: 0.1, maxExclusive: true }, window: true, group: 'optional', berths: false },
  channel_closure: { window: true, group: 'none', berths: false },
  yard_outage: { factor: { min: 0, max: 1, step: 0.1, maxExclusive: true }, window: true, group: 'none', berths: false },
};

const DEFAULT_FACTOR: Record<ScenarioType, number> = { surge: 2, berth_closure: 1, crane_scale: 0.5, strike: 0, channel_closure: 1, yard_outage: 0.3 };

export interface ScenarioCtx { days: number; groups: readonly SimGroup[]; channelPresent: boolean; hasYard: boolean }
export function ctxFrom(sp: SimProfileDoc | null, days: number): ScenarioCtx {
  const g = sp?.profile.groups ?? [];
  return {
    days, groups: g, channelPresent: sp?.profile.channel.present ?? false,
    hasYard: g.some((x) => x.kind === 'container') && (sp?.profile.yard.capacity_teu ?? 0) > 0,
  };
}

/** Why a scenario cannot be offered for this port, or null. Words only. */
export function unavailable(type: ScenarioType, ctx: ScenarioCtx): string | null {
  if (ctx.groups.length === 0) return 'No berth groups';
  if (type === 'channel_closure' && !ctx.channelPresent) return 'No channel';
  if (type === 'yard_outage' && !ctx.hasYard) return 'No container yard';
  return null;
}

export function defaultForm(type: ScenarioType, ctx: ScenarioCtx): ScenarioForm {
  const startH = Math.min(24, Math.max(0, Math.floor(ctx.days * 24 * 0.2)));
  const durationH = Math.max(1, Math.min(48, Math.floor(ctx.days * 24 * 0.3)));
  const biggest = [...ctx.groups].sort((a, b) => b.berths - a.berths || (a.id < b.id ? -1 : 1))[0];
  return {
    type, factor: DEFAULT_FACTOR[type], startH, durationH,
    group: type === 'berth_closure' ? biggest?.id ?? '' : '',
    berths: type === 'berth_closure' ? Math.max(1, Math.ceil((biggest?.berths ?? 1) / 2)) : 0,
  };
}

export interface FormIssue { field: 'factor' | 'startH' | 'durationH' | 'group' | 'berths'; word: string }
/** Mirrors the server's range checks; returns state words only. */
export function checkForm(f: ScenarioForm, ctx: ScenarioCtx): FormIssue[] {
  const out: FormIssue[] = [];
  const spec = FIELDS[f.type];
  if (spec.factor) {
    const s = spec.factor;
    const lo = s.minExclusive ? f.factor <= s.min : f.factor < s.min;
    const hi = s.maxExclusive ? f.factor >= s.max : f.factor > s.max;
    if (!Number.isFinite(f.factor) || lo || hi) out.push({ field: 'factor', word: 'Out of range' });
  }
  if (spec.window) {
    if (!(f.startH >= 0)) out.push({ field: 'startH', word: 'Out of range' });
    if (!(f.durationH > 0)) out.push({ field: 'durationH', word: 'Out of range' });
    else if (f.startH + f.durationH > ctx.days * 24) out.push({ field: 'durationH', word: 'Ends after run' });
  }
  if (spec.group === 'one') {
    const g = ctx.groups.find((x) => x.id === f.group);
    if (!g) out.push({ field: 'group', word: 'Pick a group' });
    else if (spec.berths && (f.berths < 0 || f.berths > g.berths)) out.push({ field: 'berths', word: 'Out of range' });
  }
  return out;
}

export function toItem(f: ScenarioForm, ctx: ScenarioCtx): ScenarioItem {
  const w = { start_h: f.startH, duration_h: f.durationH };
  switch (f.type) {
    case 'surge': return { type: 'surge', factor: f.factor, ...w };
    case 'berth_closure': {
      const g = ctx.groups.find((x) => x.id === f.group);
      return { type: 'berth_closure', group: f.group, berths: f.berths === 0 || (g && f.berths >= g.berths) ? 'all' : f.berths, ...w };
    }
    case 'crane_scale': return { type: 'crane_scale', factor: f.factor, ...(f.group ? { groups: [f.group] } : {}), ...w };
    case 'strike': return { type: 'strike', factor: f.factor, ...(f.group ? { groups: [f.group] } : {}), ...w };
    case 'channel_closure': return { type: 'channel_closure', ...w };
    case 'yard_outage': return { type: 'yard_outage', factor: f.factor, ...w };
  }
}

/** One-line chip text for a scenario item, e.g. `Surge x2 · D+01 00 to D+02 00`. */
export function itemLabel(it: ScenarioItem): string {
  const t = it.type as ScenarioType;
  const name = SCENARIO_LABEL[t] ?? String(it.type);
  const fx = typeof it.factor === 'number' ? ` ×${it.factor}` : '';
  return `${name}${fx}`;
}

export interface Window { start: number; end: number; item: ScenarioItem }
export function windows(items: readonly ScenarioItem[], horizonH: number): Window[] {
  return items.flatMap((it) => {
    if (typeof it.start_h !== 'number' || typeof it.duration_h !== 'number') return it.type === 'crane_scale' || it.type === 'strike' ? [{ start: 0, end: horizonH, item: it }] : [];
    return [{ start: it.start_h, end: it.start_h + it.duration_h, item: it }];
  });
}

export interface Effects {
  surge: number;
  /** group id to number of berths closed (the highest-numbered). 'all' = -1. */
  closedBerths: Map<string, number>;
  channelClosed: boolean;
  yardFactor: number;
  craneFactor: Map<string, number>;
  strikeFactor: Map<string, number>;
}
/** The items in force at hour `t` (windows are [start, end)). Overlapping windows multiply, closures add. */
export function effectsAt(items: readonly ScenarioItem[], t: number, groupIds: readonly string[]): Effects {
  const e: Effects = { surge: 1, closedBerths: new Map(), channelClosed: false, yardFactor: 1, craneFactor: new Map(), strikeFactor: new Map() };
  for (const it of items) {
    const s = typeof it.start_h === 'number' ? it.start_h : 0;
    const en = typeof it.duration_h === 'number' ? s + it.duration_h : Infinity;
    if (t < s || t >= en) continue;
    const factor = typeof it.factor === 'number' ? it.factor : 1;
    const groups = Array.isArray(it.groups) && it.groups.length ? (it.groups as string[]) : groupIds;
    switch (it.type) {
      case 'surge': e.surge *= factor; break;
      case 'berth_closure': {
        const g = String(it.group); const n = it.berths === 'all' ? -1 : Number(it.berths);
        const prev = e.closedBerths.get(g) ?? 0;
        e.closedBerths.set(g, prev === -1 || n === -1 ? -1 : prev + n);
        break;
      }
      case 'channel_closure': e.channelClosed = true; break;
      case 'yard_outage': e.yardFactor *= factor; break;
      case 'crane_scale': for (const g of groups) e.craneFactor.set(g, (e.craneFactor.get(g) ?? 1) * factor); break;
      case 'strike': for (const g of groups) e.strikeFactor.set(g, (e.strikeFactor.get(g) ?? 1) * factor); break;
      default: break;
    }
  }
  return e;
}

/** True when anything is in force (the map and the timeline mark the window). */
export const anyEffect = (e: Effects): boolean =>
  e.surge !== 1 || e.closedBerths.size > 0 || e.channelClosed || e.yardFactor !== 1 || e.craneFactor.size > 0 || e.strikeFactor.size > 0;

/** Number of berths closed in a group, given its berth count. */
export function closedCount(e: Effects, group: string, berths: number): number {
  const n = e.closedBerths.get(group); if (n === undefined) return 0;
  return n === -1 ? berths : Math.min(berths, n);
}

/** The frames window to request for a run of `days`, as hours and a step in minutes: at most 400 frames. */
export function framePlan(days: number): { fromH: number; toH: number; stepMin: number } {
  const total = days * 24 * 60;
  const stepMin = Math.max(15, Math.ceil(total / 400 / 15) * 15);
  return { fromH: 0, toH: days * 24, stepMin };
}
