/* PORT TWIN honest labelling, as data. Every chip word below is either a
 * fixed doctrine word (SIMULATED, NOT SERVED, the two source credits) or is
 * copied from what the API reported (profile kind, tier grade, parameter
 * status, berth-group flag, length source). Nothing is softened or
 * re-worded; the UI prints `word` as given. */
import type { StateKind } from '../../shell/stateChips';
import type { BaselineState, GeoDoc, Imagery, PortProfile, ProfileGroup, SimProfileDoc, SimRun } from './types';

export interface ChipSpec { state: StateKind; word: string; meta?: string; title?: string }

export const SRC_OSM = '© OpenStreetMap contributors';
export const SRC_COPERNICUS = 'Contains modified Copernicus Sentinel data';
export const NO_SHIPS = 'CANNOT SHOW SHIPS OR BERTH SLOTS';

export const NOT_SERVED: ChipSpec = { state: 'notserved', word: 'NOT SERVED' };
export const SIMULATED: ChipSpec = { state: 'sim', word: 'SIMULATED' };
export const MODELLED: ChipSpec = { state: 'model', word: 'MODELLED' };

/** Source credits for the map: OSM always, Copernicus while the underlay shows. */
export function sourceChips(underlayOn: boolean): ChipSpec[] {
  return [{ state: 'meta', word: SRC_OSM }, ...(underlayOn ? [{ state: 'meta' as StateKind, word: SRC_COPERNICUS }] : [])];
}

export function geometryChips(geo: GeoDoc): ChipSpec[] {
  const date = geo.geojson.properties.provenance?.osm_retrieved;
  return [{ state: 'snapshot', word: geo.basis, meta: date ? `OSM ${date}` : undefined, title: geo.geometry_hash.slice(0, 12) }];
}

export function imageryChips(img: Imagery): ChipSpec[] {
  return [
    { state: 'snapshot', word: img.basis, meta: `Sentinel-2 ${img.resolution_m} m · ${img.scene_date}` },
    { state: 'notserved', word: NO_SHIPS },
  ];
}

/** `SHELL place: VISUALISATION ONLY` -> two chips; `CORE place` -> one. */
export function tierChips(grade: string | undefined, tier: string | undefined): ChipSpec[] {
  const g = (grade ?? '').trim();
  if (!g) return tier ? [{ state: 'meta', word: tier.toUpperCase() }] : [];
  const [head, tail] = g.split(':').map((s) => s.trim());
  const first = head.replace(/\s+place$/i, '');
  return [{ state: 'meta', word: first }, ...(tail ? [{ state: 'notserved' as StateKind, word: tail }] : [])];
}

export function profileKindChip(kind: string | undefined): ChipSpec | null {
  if (!kind) return null;
  return { state: kind === 'PROFILE' ? 'model' : 'snapshot', word: kind };
}

/** Parameter status counts as reported in the run's `plugins_used` (SOURCED, CAL, LINKED, TEMPORARY, NUMERICAL). */
export function statusCounts(run: Pick<SimRun, 'provenance'> | null): Array<{ word: string; n: number }> {
  const list = run?.provenance.plugins_used ?? [];
  const m = new Map<string, number>();
  for (const p of list) m.set(p.status, (m.get(p.status) ?? 0) + 1);
  const order = ['LINKED', 'TEMPORARY', 'CAL', 'SOURCED', 'NUMERICAL'];
  const rank = (w: string) => { const i = order.indexOf(w); return i < 0 ? 99 : i; };
  return [...m.entries()].map(([word, n]) => ({ word, n })).sort((a, b) => rank(a.word) - rank(b.word) || (a.word < b.word ? -1 : 1));
}

export function simChips(run: SimRun | null): ChipSpec[] {
  if (!run) return [];
  const out: ChipSpec[] = [{ ...SIMULATED, title: run.sim_id }, { state: 'model', word: run.provenance.basis }];
  const pk = profileKindChip(run.provenance.profile.kind);
  if (pk) out.push(pk);
  out.push(...tierChips(run.provenance.grade, run.provenance.tier));
  return out;
}

/** Chips for one berth group: its profile flag (TEMPORARY, ...) and PROXY when its length is a shoreline proxy. */
export function groupChips(g: Pick<ProfileGroup, 'flag' | 'length_source'> | undefined): ChipSpec[] {
  if (!g) return [];
  const out: ChipSpec[] = [];
  if (g.length_source && /^PROXY/.test(g.length_source)) out.push({ state: 'snapshot', word: 'PROXY', title: g.length_source });
  if (g.flag) out.push({ state: 'meta', word: g.flag });
  return out;
}

/** Layout-level chips: the profile flags seen on any group, the anchorage flag when it is not a number. */
export function structureChips(profile: PortProfile | null, sim: SimProfileDoc | null): ChipSpec[] {
  const out: ChipSpec[] = [];
  const k = profileKindChip(sim?.profile.profile_kind);
  if (k) out.push(k);
  if (profile) {
    if (profile.berth_groups.some((g) => g.length_source && /^PROXY/.test(g.length_source))) out.push({ state: 'snapshot', word: 'PROXY', meta: 'berth length' });
    const flags = new Set(profile.berth_groups.map((g) => g.flag).filter(Boolean) as string[]);
    for (const f of [...flags].sort()) out.push({ state: 'meta', word: f });
    if (profile.anchorage.flag === 'NOT SERVED') out.push({ state: 'notserved', word: 'NOT SERVED', meta: 'anchorage' });
  }
  return out;
}

/** The visualisation-only words for the placement of berth slots and vessels. */
export const PLACEMENT_CHIPS: ChipSpec[] = [
  { state: 'sim', word: 'SIMULATED' },
  { state: 'snapshot', word: 'PROXY', meta: 'slot placement' },
];

/** The undisturbed run's own state, as the API reports it (never tuned, never softened): a profile whose mapped
 *  berths cannot serve its calls says `Baseline congested` with the kinds; a clear one says `Baseline clear` with
 *  the highest implied utilisation; a server that does not send it says NOT SERVED. The API's reading is the hover text. */
export function baselineStateChip(bs: BaselineState | null | undefined): ChipSpec {
  if (!bs || typeof bs.structurally_saturated !== 'boolean') return { state: 'notserved', word: 'NOT SERVED', meta: 'baseline state' };
  const util = typeof bs.max_implied_utilisation === 'number' && Number.isFinite(bs.max_implied_utilisation)
    ? `max utilisation ${Math.round(bs.max_implied_utilisation * 100)}%` : undefined;
  if (bs.structurally_saturated) {
    const kinds = (bs.kinds_over_capacity ?? []).join(', ');
    return { state: 'down', word: 'Baseline congested', meta: kinds || util, title: bs.reading };
  }
  return { state: 'meta', word: 'Baseline clear', meta: util, title: bs.reading };
}
