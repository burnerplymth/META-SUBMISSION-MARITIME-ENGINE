/* The Arrange box's Meta AI task, start to finish, with a stated outcome.
 *
 * The task is a purpose session (one tool, scenario_organise) that gets the
 * shocks on the timeline with the sentence. The browser step it triggers puts
 * the server's answer in the builder as a pending diff. When the task ends the
 * outcome is read from what really happened; a task without a usable proposal
 * always yields a status and a reason, never silence. */
import type { ArrangeShock } from '../../agent/api';
import type { AgentSnapshot } from '../../agent/controller';
import { errorLabel } from '../../agent/timeline';
import { isTerminal } from '../../agent/types';
import { arrangeOutcome, cleanInstruction, type ArrangeOutcome } from './logic';
import type { BuilderStore } from './store';

export interface ArrangeAgent {
  send(text: string, origin?: 'typed' | 'voice', arrange?: { timeline: ArrangeShock[] }): Promise<boolean>;
  getSnapshot(): AgentSnapshot;
  readonly loopDone: Promise<void>;
}

export const timelineOf = (store: BuilderStore): ArrangeShock[] =>
  store.getState().doc.shocks.map((s) => ({ id: s.id, place_id: s.placeId, start_offset_d: s.startD, duration_d: s.durationD }));

export async function runArrange(agent: ArrangeAgent, store: BuilderStore, text: string): Promise<ArrangeOutcome> {
  const t = cleanInstruction(text);
  const timeline = timelineOf(store);
  if (!t) return { kind: 'no_rule', status: 'Enter an instruction.', reason: null };
  if (timeline.length === 0) return { kind: 'no_rule', status: 'Add shocks first.', reason: null };
  store.setPending(null);
  const started = await agent.send(t, 'typed', { timeline });
  const first = agent.getSnapshot().state;
  if (!started) {
    return first.error
      ? { kind: 'unavailable', status: 'Request refused.', reason: errorLabel(first.error) }
      : { kind: 'unavailable', status: 'Meta AI is busy.', reason: null };
  }
  const sid = first.sessionId;
  await agent.loopDone;
  const st = agent.getSnapshot().state;
  if (st.sessionId !== sid) return { kind: 'unavailable', status: 'Task replaced.', reason: null };
  if (!isTerminal(st.status)) {
    return { kind: 'unavailable', status: 'Meta AI unavailable.', reason: errorLabel(st.error ?? 'unreachable') };
  }
  return arrangeOutcome({ status: st.status, stopReason: st.stopReason, steps: st.steps, hasPending: store.getState().pending !== null });
}
