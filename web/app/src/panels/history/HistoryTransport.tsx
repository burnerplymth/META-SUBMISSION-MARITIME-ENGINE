import React from 'react';
import { Chip } from '../../shell/Chip';
import { backplay, useBackplay, type Backplay } from './backplay';
import { SPEEDS, addDays, daysBetween } from './logic';
import './history.css';
import { Icon } from '../../ui/Icon';

/** HISTORY transport (the shell's bottom bar):
 *  play or pause, step, speed, a scrubber with event ticks, GO TO, the
 *  as-of stamp and NOW. It drives the shared backplay cursor; the history
 *  panel reads the cursor and selects the places on the map. */
export function HistoryTransport({ onNow, store = backplay }: { onNow?: () => void; store?: Backplay }) {
  const bp = useBackplay(store);
  const win = bp.win;
  if (!win || !bp.cursor) {
    return (
      <div className="transport transport--history" role="group" aria-label="History transport">
        <div className="hx-tr mono"><Chip kind="snapshot" word="History" /><Chip kind="notserved" word="No window" />
          {onNow && <button type="button" className="hx-btn" onClick={onNow}>Now</button>}</div>
      </div>
    );
  }
  const span = Math.max(1, daysBetween(win.from, win.to));
  const idx = daysBetween(win.from, bp.cursor);
  return (
    <div className="transport transport--history" role="group" aria-label="History transport">
      <div className="hx-tr mono">
        <Chip kind="snapshot" word="History" />
        <span className="hx-tr-row">
          <button type="button" className="hx-btn" title="Start" aria-label="Go to start" onClick={() => store.goStart()}><Icon name="skip-back" /></button>
          <button type="button" className="hx-btn" title="Step back  ," aria-label="Step back one day" onClick={() => store.step(-1)}><Icon name="step-back" /></button>
          <button type="button" className="hx-btn hx-btn--play" aria-pressed={bp.playing} aria-label={bp.playing ? 'Pause' : 'Play'} onClick={() => store.toggle()}><Icon name={bp.playing ? 'pause' : 'play'} /> {bp.playing ? 'Pause' : 'Play'}</button>
          <button type="button" className="hx-btn" title="Step forward  ." aria-label="Step forward one day" onClick={() => store.step(1)}><Icon name="step-forward" /></button>
          <button type="button" className="hx-btn" title="End" aria-label="Go to end" onClick={() => store.goEnd()}><Icon name="skip-forward" /></button>
        </span>
        <span className="hx-tr-row" role="group" aria-label="Speed">
          {SPEEDS.map((s) => <button key={s} type="button" className={`hx-btn${bp.speed === s ? ' on' : ''}`} aria-pressed={bp.speed === s} onClick={() => store.setSpeed(s)}>{s}x</button>)}
        </span>
        <span className="hx-tr-row">{win.from.slice(5)}</span>
        <div className="hx-scrub">
          <input type="range" min={0} max={span} step={1} value={idx} aria-label="History date" aria-valuetext={bp.cursor}
            onChange={(e) => store.setCursor(addDays(win.from, Number(e.target.value)))} />
          <div className="hx-ticks" aria-hidden="true">
            {bp.ticks.map((t) => <i key={t.id} className={t.mode === 'MODELED' ? 'm' : ''} style={{ left: `${t.frac * 100}%` }} title={`${t.date} ${t.place}`} />)}
          </div>
        </div>
        <span className="hx-tr-row">{win.to.slice(5)}</span>
        <label className="hx-tr-row">Go to
          <input type="date" value={bp.cursor} min={win.from} max={win.to} aria-label="Go to date" onChange={(e) => { if (e.target.value) store.setCursor(e.target.value); }} />
        </label>
        <Chip kind="snapshot" word="Snapshot as-of" meta={bp.cursor} className="hx-asof-chip" />
        {onNow && <button type="button" className="hx-btn" onClick={onNow}>Now</button>}
      </div>
    </div>
  );
}
