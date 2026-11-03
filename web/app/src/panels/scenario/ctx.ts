import { createContext, useContext } from 'react';
import type { Chain } from '../../types';
import type { BookRow, NameOf, PlaceOption } from './logic';
import type { CatalogueRun, ChainJournal, EngineRev, Loop, ShockRules, SimJournal } from './types';

export type TabId = 'RUN' | 'SHOCK' | 'JRNL' | 'RUNS' | 'CMP' | 'VERIFY';
/** The words a person reads on the tabs; the ids stay short keys. */
export const TAB_LABEL: Record<TabId, string> = {
  RUN: 'Run', SHOCK: 'Shock', JRNL: 'Journal', RUNS: 'Runs', CMP: 'Compare', VERIFY: 'Verify',
};
export const TAB_TITLE: Record<TabId, string> = {
  RUN: 'Run control', SHOCK: 'Shock desk', JRNL: 'Journal', RUNS: 'Runs', CMP: 'Compare', VERIFY: 'Verify',
};

export interface Loaded<T> { id: string | null; status: 'idle' | 'loading' | 'ready' | 'error'; doc: T | null; error: string | null }
export interface BuildTrack {
  simRunId: string; parentId: string; atTick: number; since: number;
  state: 'building' | 'ready' | 'failed'; error: string | null;
}
export interface CmpPair { a: string | null; b: string | null }

/** Everything the six tabs share. Provided once by ScenarioLoopPanel. */
export interface LoopCtx {
  loop: Loop; setLoop: (l: Loop) => void; simServed: boolean;
  go: (t: TabId) => void;
  places: PlaceOption[]; nameOf: NameOf; rules: ShockRules | null;
  rev: EngineRev; setRev: (r: EngineRev) => void;
  /** The loaded chain (desk slice) and the loaded sim run id (when SIM is active). */
  chain: Chain | null; chainId: string | null; simId: string | null; subjectId: string | null;
  chainJournal: Loaded<ChainJournal>; simJournal: Loaded<SimJournal>; refreshJournal: () => void;
  catalogue: { runs: CatalogueRun[] | null; status: 'idle' | 'loading' | 'error'; error: string | null }; refreshCatalogue: () => void;
  session: BookRow[]; addSession: (r: BookRow) => void;
  build: BuildTrack | null; beginBuild: (b: BuildTrack) => void; dismissBuild: () => void;
  cmp: CmpPair; setCmp: (p: CmpPair) => void;
  openChainRun: (id: string) => Promise<Chain | null>;
  openSimRunId: (id: string) => Promise<void>;
  /** Called after a run was created or forked, so the run lists refresh. */
  noteRunsChanged: () => void;
}

export const ScnCtx = createContext<LoopCtx | null>(null);
export const useScn = (): LoopCtx => {
  const c = useContext(ScnCtx);
  if (!c) throw new Error('scenario loop context');
  return c;
};
