/**
 * The one chat controller of the page. Kept apart from the React panel so a
 * closed Chat window never loses a running turn. The view deps (window manager,
 * store) come from the same registry the agent used (agent/singleton.ts).
 */
import { useSyncExternalStore } from 'react';
import { logAgentStep } from '../agent/aiFeed';
import { agentView } from '../agent/singleton';
import { makeChatApi } from './api';
import { ChatController, downloadFile, type ChatSnapshot } from './controller';

let controller: ChatController | null = null;
export function getChat(): ChatController {
  if (!controller) {
    controller = new ChatController({
      api: makeChatApi(),
      view: () => agentView.get(),
      onStep: logAgentStep,
      save: downloadFile,
    });
  }
  return controller;
}

export function useChat(): ChatSnapshot {
  const c = getChat();
  return useSyncExternalStore(c.subscribe, c.getSnapshot, c.getSnapshot);
}
