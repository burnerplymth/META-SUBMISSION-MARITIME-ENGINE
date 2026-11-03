/* The one Research report controller of the page. A closed Research window never loses a running job. */
import { useSyncExternalStore } from 'react';
import { makeAgentApi } from '../../agent/api';
import { getJson } from '../reports/client';
import { ReportController, type ReportSnapshot } from './reportController';
import { pdfAvailableFrom } from './reportModel';

let controller: ReportController | null = null;
export function getReport(): ReportController {
  if (!controller) {
    controller = new ReportController({
      api: makeAgentApi(),
      pdf: async () => pdfAvailableFrom(await getJson<unknown>('/reports/templates')),
    });
  }
  return controller;
}

export function useReport(): ReportSnapshot {
  const c = getReport();
  return useSyncExternalStore(c.subscribe, c.getSnapshot, c.getSnapshot);
}
