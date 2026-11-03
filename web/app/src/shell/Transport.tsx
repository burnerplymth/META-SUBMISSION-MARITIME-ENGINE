import React from 'react';
import { useDeskStore, useSimStore } from '../store';
import { SimTimeline } from '../panels/sim';
import { HistoryTransport } from '../panels/history/HistoryTransport';
import { useBackplay } from '../panels/history/backplay';
import { Chip } from './Chip';
import type { ProbeState } from './tools';
import { nowStripWord } from './truthLabel';

function SimBar() {
  const sim = useSimStore();
  return sim.simActive ? <SimTimeline /> : <div className="transport-now mono"><Chip kind="sim" /><Chip kind="notserved" word="NO RUN" /></div>;
}

/** Bottom transport bar. SIMULATION: the full
 *  transport on the sim slice's own state (play, step, speed, ruler
 *  with event ticks, clock, SIMULATED chip). LIVE: collapsed to a NOW
 *  strip; HISTORY backplay is shown only as the honest state of its
 *  route (NOT SERVED until the history route exists). */
export function Transport({ now, history, onHistory, onNow }: { now: Date; history: ProbeState; onHistory: () => void; onNow?: () => void }) {
  const s = useDeskStore();
  const bp = useBackplay();
  /* TRUTH: NOW wears Live only while every active feed record earns LIVE now
   * (feed state, age and stored mode); one live feed is not enough. */
  const nowLive = nowStripWord(s.feed, s.health, s.snapshotMode, now.getTime()) === 'LIVE';
  if (s.mode !== 'sim' && bp.active) return <HistoryTransport onNow={onNow} />;
  if (s.mode === 'sim') {
    return (
      <div className="transport transport--sim" role="group" aria-label="Simulation transport">
        <SimBar />
      </div>
    );
  }
  return (
    <div className="transport transport--now" role="group" aria-label="Now">
      <div className="transport-now mono">
        <Chip kind={nowLive ? 'live' : 'snapshot'} word={nowLive ? undefined : 'Recorded'} />
        <b>NOW</b>
        <span className="meta">{now.toISOString().slice(0, 10)} {now.toISOString().slice(11, 16)}Z</span>
        <span className="spacer" />
        {history === 'served'
          ? <button type="button" className="chip-btn" onClick={onHistory}><Chip kind="snapshot" word="HISTORY" /></button>
          : history === 'notserved' ? <button type="button" className="chip-btn" onClick={onHistory}><Chip kind="notserved" word="HISTORY NOT SERVED" /></button>
            : null}
      </div>
    </div>
  );
}
