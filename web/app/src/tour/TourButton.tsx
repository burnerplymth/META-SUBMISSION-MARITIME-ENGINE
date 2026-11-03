import React, { useSyncExternalStore } from 'react';
import { Button } from '../ui';
import { engine, startTour } from './runtime';

/** The Tour entry: starts the Quick tour; while a tour runs it shows as pressed and does nothing. */
export function TourButton({ onStart }: { onStart?: () => void }) {
  const st = useSyncExternalStore(engine.subscribe, engine.getState, engine.getState);
  const on = st.status !== 'idle';
  return (
    <Button size="sm" variant="secondary" icon="play" className="tour-entry" aria-pressed={on} disabled={on} onClick={() => { onStart?.(); startTour('quick'); }}>Tour</Button>
  );
}
