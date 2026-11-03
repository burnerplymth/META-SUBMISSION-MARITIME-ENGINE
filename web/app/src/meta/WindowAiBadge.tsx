/**
 * WINDOW TITLE-BAR BADGE: the Meta AI mark a window that shows
 * model output carries on its title bar. Same face and popover as the
 * per-output AiBadge. It describes the window's most recent call (from the
 * session log, filtered to the surfaces the window shows); before any call
 * it shows the mode the status route reports and en dashes for the rest.
 * Status route down: `Meta AI, unavailable`, never a made-up mode.
 */
import React from 'react';
import { SURFACE_WORD, clockUtc, useAiCalls, type AiSurface } from './aiCalls';
import { useAiStatus } from './aiStatus';
import { BadgeFace, MODE_WORD, PopButton, PopRows, type AiMode } from './AiBadge';
import { NA, fmtUsd, type MetaRow } from './provenance';

/** Which model surfaces each window shows. */
export const WINDOW_AI_SURFACES: Readonly<Record<string, readonly AiSurface[]>> = {
  news: ['classify', 'query'],
  explain: ['explain', 'ask'],
  satdesk: ['brief', 'sam', 'vision'],
  agent: ['agent'],
  brief: ['brief'],
};

export function WindowAiBadge({ windowId }: { windowId: string }) {
  const surfaces = WINDOW_AI_SURFACES[windowId] ?? [];
  const { doc, view } = useAiStatus();
  const calls = useAiCalls();
  const last = calls.find((c) => surfaces.includes(c.surface));
  if (!last && !view) return null;
  const mode: AiMode = last ? (last.mode === 'Live' ? 'live' : 'recorded') : view!.kind === 'na' ? 'unavailable' : view!.tone;
  const rows: MetaRow[] = last
    ? [
      { k: 'Model', v: last.model ?? NA },
      { k: 'Mode', v: last.mode },
      { k: 'Prompt id', v: last.promptId ?? NA },
      { k: 'Cost', v: last.cost === null ? NA : fmtUsd(last.cost) },
      { k: 'Cached', v: last.cached === null ? NA : last.cached ? 'Yes' : 'No' },
      { k: 'Time', v: `${clockUtc(last.at)} UTC` },
      { k: 'Did', v: last.did },
    ]
    : [
      { k: 'Model', v: doc?.model ?? NA },
      { k: 'Mode', v: MODE_WORD[mode] },
      { k: 'Prompt id', v: NA },
      { k: 'Cost', v: NA },
      { k: 'Cached', v: NA },
      { k: 'Time', v: NA },
      { k: 'Did', v: surfaces.length ? `No ${SURFACE_WORD[surfaces[0]].toLowerCase()} calls yet` : NA },
    ];
  return (
    <PopButton className={`ai-badge ai-badge--${mode} wm-ai`} ariaLabel={`Meta AI, ${MODE_WORD[mode].toLowerCase()}`} head="Meta AI"
      data={{ 'data-ai-badge': mode, 'data-transport': mode === 'live' ? 'LIVE' : mode === 'recorded' ? 'RECORDED' : 'NONE' }}
      body={<PopRows rows={rows} />}>
      <BadgeFace mode={mode} />
    </PopButton>
  );
}
