/**
 * Tour content types. A step declares its COMPLETE target state: the layout,
 * the open windows, the selected place, the theme, the basemap, the agent
 * state and the data it needs. Nothing relies on the previous step having run
 * (apply.ts puts the product in the target state from wherever it is), so a
 * jump forward or back lands in the right visual state.
 */
import type { TourStepLike } from './engine';
import type { StateKind } from '../shell/stateChips';
import type { WindowId } from '../windows/registry';

export type TourShellMode = 'live' | 'sim';

/** A window opened on top of the layout. */
export interface TourOpen { id: WindowId; how?: 'dock' | 'tab' | 'float'; with?: WindowId }

/** Recorded Agent task state. `run` plays the scripted task: confirmation card, the tour's confirmation, the answer.
 *  `done` ensures the answered state without the pause; `none` clears any task; `keep` leaves it. */
export type TourAgent = 'none' | 'run' | 'done' | 'keep';
/** The causal chain on screen: the stored home run, the Suez closure run (14 days), or whatever is loaded. */
export type TourRun = 'home' | 'suez' | 'keep';
/** The simulation slice: no run loaded, a fleet run loaded, or a fleet run with one vehicle selected. */
export type TourSim = 'none' | 'fleet' | 'vehicle';

/** Named UI actions a step may need beyond state. Each one checks first and skips when already satisfied. */
export type TourAction =
  | { t: 'satellite'; place: string; source?: string }
  | { t: 'port-strike' }
  | { t: 'lane-plan' }
  | { t: 'fork' }
  | { t: 'scenario-tab'; tab: 'RUN' | 'SHOCK' | 'JRNL' | 'RUNS' | 'CMP' | 'VERIFY' }
  | { t: 'wait'; ms: number };

export interface TourTarget {
  /** Shell mode. Default live. */
  mode?: TourShellMode;
  /** Layout: a built-in preset id or a tour layout id (layouts.ts). */
  layout: string;
  /** Windows opened beyond the layout. */
  open?: TourOpen[];
  /** The window raised in its group. */
  focus?: WindowId;
  /** Selected place id; null clears the selection (closes the map card); omitted leaves it. */
  place?: string | null;
  /** Chain step selected with the place. */
  seq?: number | null;
  /** `user` keeps the viewer's theme; `opposite` shows the other one. */
  theme?: 'user' | 'opposite';
  /** `user` keeps the viewer's basemap. */
  basemap?: 'user' | 'dark' | 'light' | 'sat';
  agent: TourAgent;
  run: TourRun;
  sim: TourSim;
  actions?: TourAction[];
}

/** What the featured element is: a window id or a CSS selector. */
export interface TourSpotlight { window?: WindowId; selector?: string }

/** Which labels the step shows, computed from the live state (labels.ts). */
export type TourLabelKey = 'feeds' | 'chain' | 'metaai' | 'agent' | 'simulated' | 'modelled' | 'recorded' | 'snapshot' | 'ledger';

/** A step's place in the Quick tour (the 3-minute path): beat position (1 to 7) and dwell in ms at 1x. */
export interface QuickSlot { at: number; ms: number }

export interface TourStep extends TourStepLike {
  id: string;
  title: string;
  /** One sentence, at most 14 words. */
  caption: string;
  /** Dwell in ms at 1x in the Full tour. */
  duration: number;
  /** The step is part of the Quick tour when set: its position there and its dwell. */
  quick?: QuickSlot;
  target: TourTarget;
  spotlight?: TourSpotlight;
  labels?: TourLabelKey[];
}

export interface TourLabel { kind: StateKind; word: string; meta?: string }
