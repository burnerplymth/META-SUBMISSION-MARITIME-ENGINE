/* Dynamic-import seam for the Brief window (boot performance, as the agent's):
 * the window code lands in its own chunk. The controller is small and eager so
 * a brief keeps loading while the window is closed; the stylesheet stays in the
 * entry stylesheet so cascade order never depends on when the chunk arrives. */
import { lazyPanel } from '../components/LazyPanel';
import './brief.css';

export const LazyBriefPanel = lazyPanel<Record<string, never>>(
  { code: 'BRF', title: 'DISRUPTION BRIEF' },
  () => import('./BriefPanel').then((m) => ({ default: m.BriefPanel })),
);
