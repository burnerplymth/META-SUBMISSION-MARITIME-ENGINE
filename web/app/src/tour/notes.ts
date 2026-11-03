/**
 * What the current step could not show. The host marks it when a bounded wait
 * gives up (the local API did not serve the data in time); TourRoot shows a
 * "Not served" chip beside the caption. Cleared when a step starts to apply.
 */
type Fn = () => void;
const subs = new Set<Fn>();
let flag = false;
let epoch = 0;

export const notServed = (): boolean => flag;
export const subscribeNotes = (f: Fn): (() => void) => { subs.add(f); return () => { subs.delete(f); }; };
const set = (v: boolean) => { if (flag === v) return; flag = v; subs.forEach((f) => f()); };
/** Marks the current step; with `since` (an earlier `noteEpoch()`), only while that step is still the current one. */
export const markNotServed = (since?: number): void => { if (since === undefined || since === epoch) set(true); };
export const clearNotes = (): void => { epoch += 1; set(false); };
export const noteEpoch = (): number => epoch;
