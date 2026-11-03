/* Hand-off from the Scenario builder to the Monte Carlo and My shipments windows.
 *
 * Both windows run ONE disruption: the chain engine takes a single shock per run, the exposure route a single
 * disruption. A builder scenario with several shocks therefore hands over its earliest shock (ties keep the order
 * in the builder) and the receiving window says so with a chip. Nothing is combined or approximated here. */
import type { HandoffScenario } from '../panels/scenarios/bridge';

export interface PickedDisruption {
  /** bumps on every hand-off so a window can tell two identical hand-offs apart */
  seq: number;
  scenarioName: string;
  placeId: string;
  kind: string;
  durationD: number;
  /** 0 to 1; a closure carries 1 */
  capacityLoss: number;
  engineRev: 1 | 2;
  /** which shock was taken, 1 based, and how many the scenario holds */
  index: number;
  of: number;
}

/** The earliest shock by start offset, or null when the scenario holds none. */
export function pickDisruption(s: HandoffScenario, seq = 0): PickedDisruption | null {
  if (!Array.isArray(s.shocks) || s.shocks.length === 0) return null;
  let at = 0;
  s.shocks.forEach((x, i) => { if (x.start_offset_d < s.shocks[at].start_offset_d) at = i; });
  const x = s.shocks[at];
  return {
    seq, scenarioName: s.name, placeId: x.place_id, kind: x.kind, durationD: x.duration_d,
    capacityLoss: x.kind === 'closure' ? 1 : x.capacity_loss, engineRev: s.engineRev === 2 ? 2 : 1, index: at + 1, of: s.shocks.length,
  };
}

/** The chip words that say what was taken, empty when the scenario is a single shock. */
export function pickNote(p: PickedDisruption): string | null {
  return p.of > 1 ? `Earliest of ${p.of} shocks` : null;
}

/* One pending hand-off per kind. A window applies it when it mounts and on every later hand-off. */
export interface Slot<T> { put(v: T): void; take(): T | null; peek(): T | null; subscribe(l: () => void): () => void }
export function makeSlot<T>(): Slot<T> {
  let v: T | null = null;
  const ls = new Set<() => void>();
  return {
    put(x) { v = x; ls.forEach((l) => l()); },
    take() { const x = v; v = null; return x; },
    peek() { return v; },
    subscribe(l) { ls.add(l); return () => { ls.delete(l); }; },
  };
}
export const mcSlot = makeSlot<PickedDisruption>();

/** A finished Monte Carlo run handed to My shipments: the job id plus the disruption it was run for. */
export interface McRunHandoff {
  seq: number;
  jobId: string;
  placeId: string;
  kind: string;
  durationD: number;
  /** 0 to 1; a closure carries 1 */
  capacityLoss: number;
}
/** The job id shape the exposure route accepts. */
export const MC_JOB_RE = /^mc_[A-Za-z0-9_-]{1,64}$/;

/** The hand-off for a finished, served run; null for a recorded run or an id the route would refuse. */
export function mcRunHandoff(
  job: { job_id: string; request: { disruption: { node_id: string; kind: string; capacity_loss?: number; duration_d: number } } },
  source: 'server' | 'recorded', seq = 0,
): McRunHandoff | null {
  if (source !== 'server' || !MC_JOB_RE.test(job.job_id)) return null;
  const d = job.request.disruption;
  return { seq, jobId: job.job_id, placeId: d.node_id, kind: d.kind, durationD: d.duration_d, capacityLoss: d.kind === 'closure' ? 1 : (d.capacity_loss ?? 1) };
}
export const shipRunSlot = makeSlot<McRunHandoff>();
export const shipSlot = makeSlot<PickedDisruption>();
