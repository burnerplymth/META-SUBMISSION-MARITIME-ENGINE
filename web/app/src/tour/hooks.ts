/**
 * The elements the tour host drives, in one place. Selectors here use stable
 * hooks (ids, data-* attributes, stable selectors), never visible copy, so a copy
 * rename cannot break a step.
 */
export const HOOKS = {
  lanePlanResult: '[data-testid="lp-result"]',
  satRoot: '.sw2-root',
  satPlace: '.sw2-place',
  satSource: '.sw2-src',
  satAi: '.sw2-ai',
  portStrike: 'button.ptw-scn-b[data-type="strike"]:not([disabled])',
  portFactor: '[data-field="factor"] input',
  portRun: 'button[data-testid="run"]:not([disabled])',
  portCompare: '[data-tour="compare"]',
  portKpis: '[data-section="kpis"]',
} as const;

/** The scenario window's tab button (stable id, see panels/scenario/index.tsx). */
export const scenarioTab = (tab: string): string => `#scn-tab-${tab}`;
