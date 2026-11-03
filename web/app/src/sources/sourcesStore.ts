import { useSyncExternalStore } from 'react';

/* Open/close state of the Sources list (one dialog for the whole app). */
let open = false;
const subs = new Set<() => void>();
const emit = () => subs.forEach((f) => f());
export function openSources(): void { if (!open) { open = true; emit(); } }
export function closeSources(): void { if (open) { open = false; emit(); } }
export function toggleSources(): void { open = !open; emit(); }
export const sourcesOpen = (): boolean => open;
export function useSourcesOpen(): boolean {
  return useSyncExternalStore((f) => { subs.add(f); return () => { subs.delete(f); }; }, () => open, () => false);
}
