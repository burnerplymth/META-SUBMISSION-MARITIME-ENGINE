/**
 * META AI STATUS VIEW MODEL: the status popover's Meta AI status section. One pure function turns what the API already serves into labelled
 * rows:
 *
 *   GET /llm/status         endpoint HOST (never the key, path or query), cap
 *                           source, last classifier spot-check case count
 *                           (when one was saved)
 *   GET /satwatch/detectors the satellite SAM seam state
 *
 * Mode, model, spend against the cap and calls are the popover's own rows
 * (statusView.ts), so they are not repeated here. Values are shown as served;
 * a figure that is not served reads Not served, never a made-up zero. There
 * is no "run the check" action: every route that would exercise the model
 * spends money or needs the admin token.
 */
import { effectiveTransport, type LlmProvenance } from './provenance';
import type { LlmStatusDoc } from './statusView';

export interface SpotcheckDoc {
  ts?: string; cases?: number; severity_agreement?: number | null; kind_agreement?: number | null;
  node_agreement?: number | null; target_severity_agreement?: number | null; transport_label?: string; model?: string | null;
}

/** GET /satwatch/detectors, only the fields read here. */
export interface DetectorsDoc {
  selected?: string; config_error?: string | null;
  providers?: Array<{ id?: string; label?: string; state?: string; selected?: boolean }>;
}

export type StatusPanelTone = 'ok' | 'wait' | 'down' | 'plain';
export interface StatusPanelRow { k: string; v: string; tone: StatusPanelTone }

export type StatusPanelFlag = 'Awaiting key' | 'Live' | 'Not served';

export interface StatusPanelView {
  flag: StatusPanelFlag;
  flagTone: 'wait' | 'ready';
  rows: StatusPanelRow[];
}

/** A served seam token read as a sentence-case word: `AWAITING_META_KEY` -> `Awaiting key`. */
export function samWord(state: string): string {
  if (state === 'AWAITING_META_KEY') return 'Awaiting key';
  const t = state.replace(/_/g, ' ').toLowerCase();
  return t.charAt(0).toUpperCase() + t.slice(1);
}

const fin = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);

/** The satellite SAM seam row value, from the detector registry. */
export function samSeam(doc: DetectorsDoc | null | undefined): { state: string; tone: StatusPanelTone } {
  if (!doc || !Array.isArray(doc.providers)) return { state: 'NOT_SERVED', tone: 'wait' };
  if (doc.config_error) return { state: 'MISCONFIGURED', tone: 'down' };
  const sam = doc.providers.find((p) => p.id === 'meta-sam');
  const state = typeof sam?.state === 'string' && sam.state ? sam.state : null;
  if (!state) return { state: 'NOT_SERVED', tone: 'wait' };
  return { state, tone: state === 'READY' ? 'ok' : 'wait' };
}

export function statusPanelView(status: LlmStatusDoc | null | undefined, detectors: DetectorsDoc | null | undefined): StatusPanelView {
  if (!status) return { flag: 'Not served', flagTone: 'wait', rows: [{ k: 'Status', v: 'Not served', tone: 'wait' }] };
  const label = (status.transport_label ?? '').toUpperCase();
  const synth: LlmProvenance = {
    model: status.model ?? null,
    transport: label === 'LIVE' || label === 'RECORDED' ? label : (status.transport ?? '').toLowerCase() === 'http' ? 'LIVE' : 'RECORDED',
    transport_raw: status.transport, live: status.live,
  };
  const live = effectiveTransport(synth) === 'LIVE';
  const rows: StatusPanelRow[] = [];
  const host = typeof status.endpoint_host === 'string' && status.endpoint_host ? status.endpoint_host : null;
  rows.push({ k: 'Endpoint', v: host ?? (live ? 'Not set' : 'None'), tone: host ? 'plain' : live ? 'down' : 'wait' });
  if (live && status.cap_source) {
    rows.push({ k: 'Daily cap source', v: String(status.cap_source).replace(/^./, (c) => c.toUpperCase()), tone: status.cap_source === 'default' ? 'wait' : 'plain' });
  }
  // Last classifier spot-check (python -m llm.spotcheck --save): a count of
  // cases and how it was measured. No agreement percentages, no pass or fail tone.
  const sc = status.last_spotcheck;
  if (sc && fin(sc.cases)) {
    const scLive = (sc.transport_label ?? '').toUpperCase() === 'LIVE';
    rows.push({ k: 'Classifier', v: `${sc.cases} ${scLive ? 'live' : 'recorded'} cases`, tone: 'plain' });
    rows.push({ k: 'Measured on', v: `${scLive ? 'Live' : 'Recorded'}${typeof sc.ts === 'string' ? ` \u00b7 ${sc.ts.slice(0, 10)}` : ''}`, tone: scLive ? 'plain' : 'wait' });
  } else rows.push({ k: 'Classifier', v: 'Not served', tone: 'wait' });
  const sam = samSeam(detectors);
  rows.push({ k: 'SAM 3.1', v: samWord(sam.state), tone: sam.tone });
  const flag: StatusPanelFlag = !live ? 'Awaiting key' : 'Live';
  return { flag, flagTone: live && sam.tone === 'ok' ? 'ready' : 'wait', rows };
}
