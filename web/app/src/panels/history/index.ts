import type React from 'react';
import type { ToolId } from '../../shell/tools';
import { lazyPanel } from '../../components/LazyPanel';

/* Boot performance (S5): the three tool panels load on first open. The
 * registry shape is unchanged; only its values are dynamic-import seams. */
const HistoryPanel = lazyPanel<{ onClose: () => void }>({ code: 'HIS', title: 'History', className: 'tool-slot' },
  () => import('./HistoryPanel').then((m) => ({ default: m.HistoryPanel })));
const RealismPanel = lazyPanel<{ onClose: () => void }>({ code: 'RLS', title: 'Realism', className: 'tool-slot' },
  () => import('./RealismPanel').then((m) => ({ default: m.RealismPanel })));
const ExportTool = lazyPanel<{ onClose: () => void }>({ code: 'EXP', title: 'Export', className: 'tool-slot' },
  () => import('./ExportTool').then((m) => ({ default: m.ExportTool })));

/** Tool panels of the history tools, keyed by the shell's tool id. App.tsx
 *  renders one of these in place of the generic ToolSlot when its tool is
 *  open. */
export const HISTORY_TOOL_PANELS: Partial<Record<ToolId, React.ComponentType<{ onClose: () => void }>>> = {
  history: HistoryPanel,
  realism: RealismPanel,
  exports: ExportTool,
};

export { HistoryTransport } from './HistoryTransport';
export { ChainLineageChip, ForkedFromChip, SimLineageChip } from './lineageChip';
export { backplay, useBackplay } from './backplay';
