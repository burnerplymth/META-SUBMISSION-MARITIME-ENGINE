/* Dynamic-import seam for the Chat window (boot performance, as agent/lazy.ts):
 * the window's code lands in its own chunk, fetched on first use. The
 * stylesheets stay in the entry stylesheet so cascade order never depends on
 * when the chunk arrives. The controller and its store (singleton.ts) are
 * small and eager, so a turn keeps running while the window is closed. */
import { lazyPanel } from '../components/LazyPanel';
import '../agent/agent.css';
import './chat.css';

export const LazyChatPanel = lazyPanel<Record<string, never>>(
  { code: 'AGT', title: 'Meta AI' },
  () => import('./ChatPanel').then((m) => ({ default: m.ChatPanel })),
);
