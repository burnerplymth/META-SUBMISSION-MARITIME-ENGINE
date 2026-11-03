/**
 * AGENT -> META AI ACTIVITY LOG. Every settled agent step (model turn or tool
 * call) becomes one row in the session log the AI activity window reads
 * (meta/aiCalls.ts). The mode comes from the session transport alone: a
 * RECORDED script is logged Recorded, never Live. A row carries its cost only
 * when the model was live.
 */
import { recordAiCall } from '../meta/aiCalls';
import { toolLabel } from './timeline';
import type { AgentStep, AgentTransport } from './types';

const clip = (s: string, n: number) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

export function logAgentStep(step: AgentStep, transport: AgentTransport): void {
  const live = transport === 'LIVE';
  const model = step.kind === 'model';
  const provenance = {
    model: model ? step.name : null,
    transport,
    transport_raw: live ? 'http' : 'fixture',
    live,
    prompt_id: step.prompt_id ?? undefined,
    ...(live && Number.isFinite(step.cost_usd) ? { cost_usd: step.cost_usd } : {}),
    estimated_cost_usd: step.estimated_cost_usd,
    cached: false,
  };
  const did = model
    ? `Meta AI turn: ${clip(step.result_summary, 90)}`
    : `Meta AI ${toolLabel(step.tool ?? step.name).toLowerCase()}: ${clip(step.result_summary, 90)}`;
  const at = Date.parse(step.ts);
  recordAiCall({ surface: 'agent', did, provenance, key: `agent:${step.id}`, at: Number.isFinite(at) ? at : undefined });
}
