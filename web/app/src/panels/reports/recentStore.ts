/* The recent-exports list, shared by the Export Centre and the Report
 * Centre: one list in safeStorage, with subscribers so both panels stay
 * in step. */
import { safeStorage } from '../../store';
import { loadRecent, pushRecent, saveRecent, type RecentItem } from './recent';

let cache: RecentItem[] | null = null;
const subs = new Set<() => void>();

export function getRecent(): RecentItem[] {
  if (cache === null) cache = loadRecent(safeStorage);
  return cache;
}
export function addRecent(item: Omit<RecentItem, 'key'>): void {
  cache = pushRecent(getRecent(), item);
  saveRecent(safeStorage, cache);
  subs.forEach((f) => f());
}
export function clearRecent(): void {
  cache = [];
  saveRecent(safeStorage, cache);
  subs.forEach((f) => f());
}
export function subscribeRecent(f: () => void): () => void {
  subs.add(f);
  return () => { subs.delete(f); };
}
