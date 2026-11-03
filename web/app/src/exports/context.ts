import { useMemo } from 'react';
import { useStore } from '../store';
import type { ExportContext } from './types';

/** The run the app currently shows: the causal chain's run and the simulation run. */
export function useExportContext(): ExportContext {
  const s = useStore();
  const runId = s.chain?.run_id ?? null;
  const simRunId = s.simRun?.sim_run_id ?? null;
  return useMemo(() => ({ runId, simRunId, asOf: null }), [runId, simRunId]);
}
