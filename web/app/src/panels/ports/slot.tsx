import React from 'react';
import { useStore } from '../../store';
import type { PanelId } from '../../types';
import { PortsPanel } from './PortsPanel';
import { LiveContextPanel, LiveFeedPanel, LiveTapePanel } from '../live/LivePanels';
import { useLiveData } from '../live/useLive';

/* The single mount point App.tsx renders (one JSX line in the right
 * column). It owns which of these panels are visible; the shell
 * only passes its own show() predicate (layout membership + window
 * state).
 *
 * LIVE TWIN flag law: the live panels exist only while the store says
 * the flag is on (GET /livetwin/feeds answered enabled) AND /live/health
 * does not answer the flag-off 404. Otherwise LiveGroup returns null and
 * issues no request at all: nothing from the live terminal is in the DOM. */
function LiveGroup({ show }: { show: (p: PanelId) => boolean }) {
  const s = useStore();
  const wanted = show('livefeed') || show('livetape') || show('livectx');
  const data = useLiveData(s.liveTwinEnabled && wanted);
  if (!s.liveTwinEnabled || !wanted || data.gate === 'off') return null;
  return (
    <>
      {show('livefeed') && <LiveFeedPanel data={data} />}
      {show('livetape') && data.gate !== 'checking' && <LiveTapePanel data={data} />}
      {show('livectx') && data.gate !== 'checking' && <LiveContextPanel data={data} />}
    </>
  );
}

export function PortsLivePanels({ show }: { show: (p: PanelId) => boolean }) {
  return <>{show('ports') && <PortsPanel />}<LiveGroup show={show} /></>;
}
