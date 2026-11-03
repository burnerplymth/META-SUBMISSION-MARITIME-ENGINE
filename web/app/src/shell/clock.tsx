import React, { useEffect, useState } from 'react';

/** A 1-second ticking clock for the components that show the time. Held in the
 *  leaf, so only these components re-render each second, not the whole App
 *  (the dock host, tiles and map stay still). */
export function useNow(intervalMs = 1000): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), intervalMs);
    return () => window.clearInterval(id);
  }, [intervalMs]);
  return now;
}

/** Wrap a component that takes a `now` prop so it ticks on its own. */
export function withNow<P extends { now: Date }>(C: React.ComponentType<P>): React.ComponentType<Omit<P, 'now'>> {
  const Ticking = (props: Omit<P, 'now'>) => React.createElement(C, { ...(props as unknown as P), now: useNow() });
  Ticking.displayName = `withNow(${C.displayName ?? C.name ?? 'Component'})`;
  return Ticking;
}
