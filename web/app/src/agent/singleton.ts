/**
 * The one agent controller of the page, and the registry the shell uses to
 * hand it live view deps. Kept apart from the React panel so a closed Agent
 * window never loses a running task.
 */
import { useSyncExternalStore } from 'react';
import { logAgentStep } from './aiFeed';
import { makeAgentApi } from './api';
import type { ViewDeps } from './clientActions';
import { AgentController, type AgentSnapshot } from './controller';

let factory: (() => ViewDeps) | null = null;
/** DockWorkspace registers (and clears) the factory that builds the live view deps. */
export const agentView = {
  set(f: (() => ViewDeps) | null): void { factory = f; },
  get(): ViewDeps | null { return factory ? factory() : null; },
};

let controller: AgentController | null = null;
export function getAgent(): AgentController {
  if (!controller) {
    controller = new AgentController({
      api: makeAgentApi(),
      view: () => agentView.get(),
      onStep: logAgentStep,
    });
  }
  return controller;
}

export function useAgent(): AgentSnapshot {
  const c = getAgent();
  return useSyncExternalStore(c.subscribe, c.getSnapshot, c.getSnapshot);
}
