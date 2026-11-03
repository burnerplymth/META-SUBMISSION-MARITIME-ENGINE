/* Open requests for the Data Viewer. The Export Centre, the report
 * builder and the command palette post a request; the viewer takes it
 * on mount or as it arrives. No React, no store: a window manager can
 * host the viewer anywhere and still receive the request. */
import type { ViewerLink } from './viewerLink';

type Listener = (r: ViewerLink) => void;
let pending: ViewerLink | null = null;
const listeners = new Set<Listener>();

export function requestOpen(r: ViewerLink): void {
  pending = r;
  listeners.forEach((l) => l(r));
}
/** Take (and clear) the request waiting for a viewer that was not mounted. */
export function takePending(): ViewerLink | null {
  const p = pending; pending = null; return p;
}
export function subscribeOpen(l: Listener): () => void {
  listeners.add(l);
  return () => { listeners.delete(l); };
}
export const viewerMounted = (): boolean => listeners.size > 0;

/** Open a dataset in the viewer; when no viewer is mounted, `showViewer`
 *  brings one up (the shell switches to the layout that holds it) and
 *  the request waits for it. */
export function openInViewer(r: ViewerLink, showViewer?: () => void): void {
  const mounted = viewerMounted();
  requestOpen(r);
  if (!mounted && showViewer) showViewer();
}
export function resetBus(): void { pending = null; listeners.clear(); }
