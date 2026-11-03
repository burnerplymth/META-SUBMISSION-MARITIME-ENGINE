import { createContext, useContext, useSyncExternalStore, type ReactNode } from 'react';
import type { ExportSpec } from '../components/Panel';
import type { ProbeResult } from '../shell/tools';
import type { ToolDef, ToolId } from '../shell/tools';
import type { Appearance } from '../shell/useAppearance';
import type { WindowId } from './registry';

/** What the shell hands every window: the tool probes
 *  (the rail tools became windows; their served / not served state is App's). */
export interface WorkspaceValue {
  probes: Partial<Record<ToolId, ProbeResult>>;
  reprobe: (t: ToolDef) => void;
  /** Appearance state (density, scheme) for the command bar's `>` actions. */
  appearance: Appearance | null;
}
export const WorkspaceCtx = createContext<WorkspaceValue>({ probes: {}, reprobe: () => undefined, appearance: null });

/** Set inside a dock window: Panel hides its own header and reports its stamp and export menu here. */
export const InWindowCtx = createContext<{ id: WindowId } | null>(null);
export const useInWindow = () => useContext(InWindowCtx);

/* ── per-window header meta (stamp, export spec) ─────────────────────── */
export interface WindowMeta { updated?: string; stamp?: ReactNode; exportSpec?: ExportSpec; code?: string; /** A header action node (a stable component that reads its own state). */ action?: ReactNode }
const metas = new Map<string, WindowMeta>();
const subs = new Set<() => void>();
let version = 0;
export const windowMeta = {
  get: (id: string): WindowMeta | undefined => metas.get(id),
  set(id: string, m: WindowMeta | null): void {
    const prev = metas.get(id);
    if (m === null) { if (prev) { metas.delete(id); version += 1; subs.forEach((f) => f()); } return; }
    metas.set(id, m);
    // only the visible parts wake subscribers; the export spec is read at click time
    if (!prev || prev.updated !== m.updated || prev.code !== m.code || (prev.exportSpec === undefined) !== (m.exportSpec === undefined) || (prev.action === undefined) !== (m.action === undefined)) { version += 1; subs.forEach((f) => f()); }
  },
  subscribe(f: () => void): () => void { subs.add(f); return () => { subs.delete(f); }; },
  version: () => version,
};
export function useWindowMeta(id: string): WindowMeta | undefined {
  useSyncExternalStore(windowMeta.subscribe, windowMeta.version, windowMeta.version);
  return windowMeta.get(id);
}
