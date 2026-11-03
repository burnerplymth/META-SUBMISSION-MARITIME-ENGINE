/* PORT TWIN vessel placement: a frame row (id, class, state, location,
 * progress) becomes a position on the map. The simulation gives no
 * coordinates, so every position here is a VISUALISATION placement:
 *   working, ready to depart  at the berth slot, hull alongside the quay;
 *   moving in / out           on the berth's water-side normal, 450 m out
 *                             at progress 0, at the berth at progress 1;
 *   anchored                  on the anchorage slot when the anchorage is
 *                             mapped, otherwise counted in the tray;
 *   approach, waiting outside always counted in the tray (the model gives
 *                             them no slot);
 *   at a berth group the layout could not place: counted as unplaced.
 * Hull sizes are the class length of the simulation registry
 * (`vessel_classes`) scaled by the profile `loa_scale` and capped at the
 * berth length. */
import type { CargoKind, Frame, FramesDoc, VesselRow } from './types';
import type { Layout } from './berths';

/** Registry `vessel_classes` lengths in metres (GET /portsim/registry, status TEMPORARY). */
export const CLASS_LOA_M: Readonly<Record<string, number>> = {
  'container/feeder': 180, 'container/neopanamax': 300, 'container/ultra-large': 380,
  'bulk/handysize': 175, 'bulk/panamax': 225, 'bulk/capesize': 290,
  'tanker/coastal': 150, 'tanker/aframax': 245, 'tanker/vlcc': 330,
  'ro-ro/short-sea': 150, 'ro-ro/deep-sea': 200,
  'general/coaster': 120, 'general/multipurpose': 180,
};
export const BEAM_RATIO = 0.16;
export const MOVE_OUT_M = 450;

export const KINDS: readonly CargoKind[] = ['container', 'bulk', 'tanker', 'ro-ro', 'general', 'unknown'];
export function classKind(label: string): CargoKind {
  const k = label.split('/')[0] as CargoKind;
  return KINDS.includes(k) ? k : 'unknown';
}
export const classLoa = (label: string): number => CLASS_LOA_M[label] ?? 200;

export type VesselState = 'approach' | 'waiting_outside' | 'anchored' | 'moving_in' | 'working' | 'ready_to_depart' | 'moving_out';

export interface PlacedVessel {
  id: number; kind: CargoKind; cls: string; state: string;
  x: number; y: number; heading: number; loaM: number; beamM: number;
  group: string | null; berth: number | null; progress: number | null;
}
export interface Tray { approach: number; outside: number; anchoredUnmapped: number; unplaced: number }
export interface VesselSet { vessels: PlacedVessel[]; tray: Tray }

/** A heading the frame serves (compass degrees, clockwise from north) as the map's math heading
 *  (radians, counter-clockwise from east); null when the frame serves none. */
export function servedHeading(deg: number | null | undefined): number | null {
  return typeof deg === 'number' && Number.isFinite(deg) && deg >= 0 && deg <= 360 ? ((90 - deg) * Math.PI) / 180 : null;
}

const hash01 = (n: number): number => (((n * 2654435761) >>> 0) % 100000) / 100000;

export interface PlaceOpts { loaScale?: number; next?: Frame | null; frac?: number }

export function placeFrame(layout: Layout, doc: FramesDoc, frame: Frame, opts: PlaceOpts = {}): VesselSet {
  const states = doc.legend.states; const classes = doc.legend.classes;
  const scale = opts.loaScale && opts.loaScale > 0 ? opts.loaScale : 1;
  const nextById = new Map<number, VesselRow>();
  if (opts.next && (opts.frac ?? 0) > 0) for (const r of opts.next.vessels) nextById.set(r[0], r);
  const out: PlacedVessel[] = [];
  const tray: Tray = { approach: 0, outside: 0, anchoredUnmapped: 0, unplaced: 0 };
  for (const row of frame.vessels) {
    const [id, ci, si, loc, prog0] = row;
    const served = (row as readonly (number | null)[])[5];
    const cls = classes[ci] ?? 'unknown'; const state = states[si] ?? 'unknown';
    let prog = prog0;
    const nx = nextById.get(id);
    if (nx && nx[2] === si && prog0 !== null && nx[4] !== null && (state === 'moving_in' || state === 'moving_out' || state === 'approach')) {
      prog = prog0 + (nx[4] - prog0) * (opts.frac ?? 0);
    }
    const base = { id, kind: classKind(cls), cls, state, progress: prog };
    if (state === 'approach') { tray.approach += 1; continue; }
    if (state === 'waiting_outside') { tray.outside += 1; continue; }
    if (state === 'anchored') {
      const pos = loc !== null ? layout.anchorage?.positions[loc] : undefined;
      if (!pos) { tray.anchoredUnmapped += 1; continue; }
      const loa = classLoa(cls) * scale;
      out.push({ ...base, x: pos[0], y: pos[1], heading: servedHeading(served) ?? hash01(id) * Math.PI * 2, loaM: loa, beamM: loa * BEAM_RATIO, group: null, berth: null });
      continue;
    }
    // berth-bound states
    const slot = loc !== null ? layout.berths[loc] : null;
    if (!slot) { tray.unplaced += 1; continue; }
    const loa = Math.min(classLoa(cls) * scale, slot.lengthM * 0.95);
    const beam = loa * BEAM_RATIO;
    const side = beam / 2 + 3;
    let d = 0; let heading = slot.heading;
    if (state === 'moving_in') { d = (1 - (prog ?? 0)) * MOVE_OUT_M; heading = Math.atan2(-slot.ny, -slot.nx); }
    else if (state === 'moving_out') { d = (prog ?? 0) * MOVE_OUT_M; heading = Math.atan2(slot.ny, slot.nx); }
    const off = side + d;
    out.push({ ...base, x: slot.x + slot.nx * off, y: slot.y + slot.ny * off, heading: servedHeading(served) ?? heading, loaM: loa, beamM: beam, group: slot.group, berth: slot.index });
  }
  return { vessels: out, tray };
}

/** Frame index and fraction for a playhead in hours, on a frames doc. */
export function frameAt(doc: FramesDoc, hours: number): { i: number; frac: number } {
  const n = doc.frames.length;
  if (n === 0) return { i: 0, frac: 0 };
  const stepH = doc.step_s / 3600;
  const fromH = doc.from / 3600;
  const x = Math.max(0, Math.min(n - 1, (hours - fromH) / stepH));
  const i = Math.min(n - 2 < 0 ? 0 : n - 2, Math.floor(x));
  return { i, frac: n === 1 ? 0 : Math.min(1, x - i) };
}
