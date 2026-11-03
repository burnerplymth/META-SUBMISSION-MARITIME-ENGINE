/**
 * The tour content. Sixteen steps in the Full tour; the Quick tour is
 * seven beats (about three minutes). One
 * storyline: a 14 day closure of the Suez Canal, followed from the canal to the
 * Shanghai to Rotterdam lane to the Rotterdam port. Only recorded fixtures and
 * existing actions are used. The tour needs the local API (the what-if run, the
 * simulation and the Agent task are served by it); each wait is bounded and a
 * step the API does not serve shows a Not served chip and carries on.
 *
 * Captions are the one place the product carries explanatory sentences: one
 * sentence of at most 14 words, institutional register (docs: COPY-STYLE). A caption
 * never says Live: the label chips under it come from the real state
 * (labels.ts, shell/truthLabel.ts).
 */
import type { TourMode } from './engine';
import type { QuickSlot, TourStep, TourTarget } from './types';

/** The place and chain step the chain beat selects: Cape of Good Hope, the reroute (step 3 of the Suez run). */
export const CAPE_PLACE = 'chk_capeofgoodhope';
export const CAPE_SEQ = 2;
/** The Rotterdam port place. */
export const ROTTERDAM = 'sea_rotterdam';
/** The closure the chain on screen is built from: Suez Canal, 14 days, full capacity loss. */
export const SUEZ_SPEC = { node_id: 'chk_suez', kind: 'closure', capacity_loss: 1.0, duration_d: 14, shape: 'step' } as const;
export const SUEZ_LABEL = 'SCENARIO — Suez Canal closure, 14 d';

const base = (t: Partial<TourTarget> & Pick<TourTarget, 'layout'>): TourTarget =>
  ({ agent: 'keep', run: 'keep', sim: 'none', ...t });

export const STEPS: readonly TourStep[] = [
  {
    id: 'overview', title: 'Overview', duration: 9000, quick: { at: 1, ms: 20000 },
    caption: 'Linked windows show disruptions, source health, the map and the causal chain.',
    target: base({ layout: 'overview', place: null, run: 'home', agent: 'none' }),
    labels: ['feeds', 'chain'],
  },
  {
    id: 'map', title: 'Map and lanes', duration: 8000,
    caption: 'Sea lanes follow stored routes between places, and each layer shows its source state.',
    target: base({ layout: 'tour-map', place: null, run: 'home', agent: 'none' }),
    spotlight: { window: 'map' },
    labels: ['feeds'],
  },
  {
    id: 'chain', title: 'Causal chain and sources', duration: 10000, quick: { at: 3, ms: 25000 },
    caption: 'Each step of the chain lists channel, delay, magnitude and source.',
    target: base({ layout: 'tour-chain', run: 'suez', place: CAPE_PLACE, seq: CAPE_SEQ, focus: 'chain' }),
    spotlight: { window: 'chain' },
    labels: ['chain'],
  },
  {
    id: 'explain', title: 'Explain', duration: 9000,
    caption: 'Meta AI explains the selected step and a post-check compares it with the record.',
    target: base({ layout: 'tour-chain', run: 'suez', place: CAPE_PLACE, seq: CAPE_SEQ, focus: 'explain' }),
    spotlight: { window: 'explain' },
    labels: ['metaai'],
  },
  {
    id: 'agent', title: 'Meta AI task', duration: 14000, quick: { at: 2, ms: 40000 },
    caption: 'A recorded task stops at a confirmation card, which the tour confirms.',
    target: base({ layout: 'demo', agent: 'run', focus: 'agent' }),
    spotlight: { window: 'agent' },
    labels: ['agent'],
  },
  {
    id: 'scenario', title: 'Scenario loop', duration: 10000,
    caption: 'A run is forked at one step, compared with its parent and verified.',
    target: base({ layout: 'tour-scenario', mode: 'sim', run: 'suez', actions: [{ t: 'fork' }, { t: 'scenario-tab', tab: 'CMP' }] }),
    spotlight: { window: 'tool-scenarios' },
    labels: ['simulated'],
  },
  {
    id: 'fleet', title: 'Simulation fleet', duration: 10000,
    caption: 'Simulated vessels sail the lanes, and a selected vehicle shows its own track.',
    target: base({ layout: 'tour-fleet', mode: 'sim', sim: 'vehicle', focus: 'sim' }),
    spotlight: { window: 'sim' },
    labels: ['simulated', 'modelled'],
  },
  {
    id: 'whatif', title: 'What-if and ensembles', duration: 10000,
    caption: 'A what-if changes one disruption, and ensembles replay named variants side by side.',
    target: base({ layout: 'tour-whatif', mode: 'sim', run: 'suez', focus: 'ensemble' }),
    spotlight: { window: 'ensemble' },
    labels: ['chain'],
  },
  {
    id: 'lanes', title: 'Lane planner', duration: 10000, quick: { at: 5, ms: 25000 },
    caption: 'Transit time is a range across named speed and queue variants.',
    target: base({ layout: 'lanes', run: 'suez', actions: [{ t: 'lane-plan' }] }),
    spotlight: { window: 'laneplanner' },
    labels: ['simulated'],
  },
  {
    id: 'porttwin', title: 'Port twin', duration: 12000, quick: { at: 6, ms: 30000 },
    caption: "A simulated strike at Rotterdam is compared with the port's own baseline.",
    target: base({ layout: 'port-twin', place: ROTTERDAM, actions: [{ t: 'port-strike' }] }),
    spotlight: { window: 'porttwin' },
    labels: ['simulated'],
  },
  {
    id: 'satellite', title: 'Satellite desk', duration: 12000, quick: { at: 4, ms: 25000 },
    caption: 'SAM outlines objects in a native crop, and Meta AI reads the same crop.',
    target: base({ layout: 'satellite', actions: [{ t: 'satellite', place: 'Rotterdam' }] }),
    spotlight: { window: 'satdesk' },
    labels: ['recorded', 'modelled'],
  },
  {
    id: 'reports', title: 'Reports and exports', duration: 9000,
    caption: 'Reports and data packs are built from the loaded run and carry a hash.',
    target: base({ layout: 'reports', run: 'suez' }),
    spotlight: { window: 'reports' },
    labels: ['chain'],
  },
  {
    id: 'activity', title: 'Meta AI activity', duration: 10000, quick: { at: 7, ms: 15000 },
    caption: 'Every model call is listed with surface, mode, model and cost.',
    target: base({ layout: 'demo', agent: 'done', open: [{ id: 'aiactivity', how: 'float' }], focus: 'aiactivity' }),
    spotlight: { window: 'aiactivity' },
    labels: ['metaai'],
  },
  {
    id: 'themes', title: 'Light and dark themes', duration: 8000,
    caption: 'Both themes carry the same data and labels.',
    target: base({ layout: 'overview', theme: 'opposite' }),
    spotlight: { selector: '.sw--theme' },
  },
  {
    id: 'ledger', title: 'Chain ledger', duration: 9000,
    caption: 'Chains are locked before the outcome is known and stay open until graded.',
    target: base({ layout: 'tour-ledger', run: 'suez', focus: 'ledger' }),
    spotlight: { window: 'ledger' },
    labels: ['ledger'],
  },
  {
    id: 'close', title: 'Provenance', duration: 10000,
    caption: 'Every figure names its source and basis, and Ctrl K opens any window.',
    target: base({ layout: 'overview', place: null }),
    spotlight: { selector: '.cmd-input' },
    labels: ['feeds'],
  },
];

/** The steps of a mode, each carrying the dwell that mode uses. */
export function stepsFor(mode: TourMode): readonly TourStep[] {
  if (mode === 'full') return STEPS;
  return STEPS.filter((s) => s.quick !== undefined)
    .sort((a, b) => (a.quick as QuickSlot).at - (b.quick as QuickSlot).at)
    .map((s) => ({ ...s, duration: (s.quick as QuickSlot).ms }));
}

export const QUICK_STEPS: readonly TourStep[] = stepsFor('quick');
export const FULL_STEPS: readonly TourStep[] = stepsFor('full');
export const STEPS_BY_MODE: Readonly<Record<TourMode, readonly TourStep[]>> = { quick: QUICK_STEPS, full: FULL_STEPS };

/** Total dwell of a mode in seconds at 1x (excludes the time steps take to apply). */
export const totalSeconds = (mode: TourMode): number => Math.round(STEPS_BY_MODE[mode].reduce((n, s) => n + s.duration, 0) / 1000);
