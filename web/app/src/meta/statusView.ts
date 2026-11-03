/**
 * STATUS-BAR AI INDICATOR view-model: GET /llm/status
 * -> `Meta AI · Recorded` / `Meta AI · Live` / `Meta AI · Unavailable`. Data
 * is shown only as served:
 *   - source 'loading'      -> nothing;
 *   - source 'unreachable'  -> `Unavailable` in the not-served form (never a
 *     fake $0.00);
 *   - spend vs cap is listed in the popover as served; a recorded run made
 *     no live call, and its accrued estimate is labelled as an estimate;
 *   - cap is printed only when the server serves one (null = no cap).
 */
import { effectiveTransport, fmtUsd, NA, type LlmProvenance, type MetaRow } from './provenance';
import type { SpotcheckDoc } from './statusPanel';

export interface LlmStatusDoc {
  spent_usd?: number; accrued_usd?: number; cap_usd?: number | null;
  transport?: string; transport_label?: string; live?: boolean; model?: string | null;
  calls_today?: number; sam_calls_today?: number; cap_reached?: boolean; disabled?: boolean;
  /** Status section inputs: host only, never the key; cap_source
   *  env|default|constructor; the last saved classifier spot-check. */
  endpoint_host?: string | null; cap_source?: string | null; last_spotcheck?: SpotcheckDoc | null;
}

export type StatusSource = 'loading' | 'live' | 'unreachable';

export type StatusChipView =
  | { kind: 'na'; word: 'Unavailable'; compact: 'Meta AI \u00b7 Unavailable'; aria: 'Meta AI, unavailable' }
  | {
    kind: 'model'; tone: 'live' | 'recorded'; transport: 'LIVE' | 'RECORDED';
    /** Sentence-case mode word: Live | Recorded. */
    word: 'Live' | 'Recorded';
    /** `Meta AI · Recorded`, plus the blocked state when there is one. */
    compact: string; rows: MetaRow[]; aria: string;
    /** Spend has hit the cap, or the kill switch is on: a DOWN-grade state. */
    blocked: 'Cap reached' | 'Disabled' | null;
  };

const fin = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);

const NA_VIEW: StatusChipView = { kind: 'na', word: 'Unavailable', compact: 'Meta AI \u00b7 Unavailable', aria: 'Meta AI, unavailable' };

export function llmStatusView(doc: LlmStatusDoc | null | undefined, source: StatusSource): StatusChipView | null {
  if (source === 'loading') return null;
  if (source === 'unreachable' || !doc) return NA_VIEW;
  const label = (doc.transport_label ?? '').toUpperCase();
  // Same safe rule as the per-output badge: Live only if nothing contradicts it.
  const synth: LlmProvenance = {
    model: doc.model ?? null,
    transport: label === 'LIVE' || label === 'RECORDED' ? label
      : (doc.transport ?? '').toLowerCase().includes('fixture') ? 'RECORDED'
        : (doc.transport ?? '').toLowerCase() === 'http' ? 'LIVE' : 'NONE',
    transport_raw: doc.transport,
    live: doc.live,
  };
  const transport = effectiveTransport(synth);
  if (!transport) return NA_VIEW;
  const live = transport === 'LIVE';
  const word = live ? 'Live' : 'Recorded';
  const blocked = doc.disabled === true ? 'Disabled' : doc.cap_reached === true ? 'Cap reached' : null;
  const rows: MetaRow[] = [{ k: 'Mode', v: word }];
  if (doc.model) rows.push({ k: 'Model', v: doc.model });
  if (fin(doc.spent_usd)) {
    rows.push({ k: 'Spend', v: fin(doc.cap_usd) ? `${fmtUsd(doc.spent_usd)} of ${fmtUsd(doc.cap_usd)}` : `${fmtUsd(doc.spent_usd)} · No cap` });
  } else rows.push({ k: 'Spend', v: NA });
  if (!live && fin(doc.accrued_usd)) rows.push({ k: 'Estimated', v: fmtUsd(doc.accrued_usd) });
  if (fin(doc.calls_today)) rows.push({ k: 'Calls today', v: String(doc.calls_today) });
  if (fin(doc.sam_calls_today)) rows.push({ k: 'SAM calls today', v: String(doc.sam_calls_today) });
  if (blocked) rows.push({ k: 'State', v: blocked });
  const compact = `Meta AI \u00b7 ${word}${blocked ? ` \u00b7 ${blocked}` : ''}`;
  return { kind: 'model', tone: live ? 'live' : 'recorded', transport, word, compact, rows, aria: `Meta AI, ${word.toLowerCase()}${blocked ? `, ${blocked.toLowerCase()}` : ''}`, blocked };
}

/** Is the model transport live? null = not known (status not served). */
export function statusIsLive(doc: LlmStatusDoc | null | undefined): boolean | null {
  if (!doc) return null;
  const v = llmStatusView(doc, 'live');
  return v && v.kind === 'model' ? v.transport === 'LIVE' : null;
}
