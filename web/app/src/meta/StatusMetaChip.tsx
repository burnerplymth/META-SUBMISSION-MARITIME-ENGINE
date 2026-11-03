/**
 * STATUS-BAR AI INDICATOR. One indicator, never
 * more: `Meta AI · Recorded`, `Meta AI · Live` or `Meta AI · Unavailable`.
 * The mark beside the words is drawn (solid dot Live, ring Recorded, slashed
 * ring Unavailable), so state is never colour alone, and a recorded run is
 * never dotted or coloured as live.
 *
 * The popover holds the mode, the model, spend against the cap (moved here
 * from the top bar), calls this session and today, the Meta AI status rows, and a
 * link that opens the `aiactivity` window (manager only). Unreachable status
 * is `Unavailable` in the not-served form, never a fake $0.00.
 */
import React, { useSyncExternalStore } from 'react';
import { Icon } from '../ui/Icon';
import { Status } from '../ui/Status';
import { wm } from '../windows/controller';
import { sessionStats, useAiCalls } from './aiCalls';
import { useAiStatus } from './aiStatus';
import { ModeMark, PopButton, PopRows } from './AiBadge';
import { StatusPanelSection } from './StatusPanelSection';
import { NA } from './provenance';

export { META_CALL_EVENT, noteMetaCall } from './aiStatus';

export function StatusMetaChip() {
  const { doc, view } = useAiStatus();
  const calls = useAiCalls();
  const wmState = useSyncExternalStore(wm.subscribe, wm.getState, wm.getState);
  if (!view) return null;
  if (view.kind === 'na') return <Status state="notserved" word={view.compact} />;
  const stats = sessionStats(calls);
  const rows = [...view.rows];
  const at = rows.findIndex((r) => r.k === 'Calls today');
  rows.splice(at < 0 ? rows.length : at, 0, { k: 'Calls this session', v: stats.total ? `${stats.total} (${stats.live} live, ${stats.recorded} recorded)` : '0' });
  const body = (close: () => void) => (
    <>
      <PopRows rows={rows.map((r) => (r.v === '' ? { ...r, v: NA } : r))} />
      {wmState.attached && (
        <button type="button" className="ai-pop-link" onClick={() => { close(); wm.openWindow('aiactivity', 'float'); }}>
          <Icon name="arrow-up-right" size="xs" /><span>Meta AI activity</span>
        </button>)}
      <StatusPanelSection status={doc} />
    </>
  );
  return (
    <PopButton className={`ai-status ai-status--${view.tone}`} wrapClass={view.blocked ? 'metachip-wrap--blocked' : ''}
      ariaLabel={view.aria} head="Meta AI" body={body} wide data={{ 'data-transport': view.transport, 'data-ai-status': view.tone }}>
      <ModeMark mode={view.tone} />
      <span className="ai-status-w">{view.compact}</span>
    </PopButton>
  );
}
