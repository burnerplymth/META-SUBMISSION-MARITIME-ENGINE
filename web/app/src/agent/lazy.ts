/* Dynamic-import seam for the Agent window (boot performance, as panels/lazy.ts):
 * the window's code lands in its own chunk, fetched on first use. The
 * stylesheet stays in the entry stylesheet so cascade order never depends on
 * when the chunk arrives. The controller and its store (singleton.ts) are
 * small and eager, so a task keeps running while the window is closed. */
import { lazyPanel } from '../components/LazyPanel';
import './agent.css';

export const LazyAgentPanel = lazyPanel<Record<string, never>>(
  { code: 'AGT', title: 'Meta AI' },
  () => import('./AgentPanel').then((m) => ({ default: m.AgentPanel })),
);
