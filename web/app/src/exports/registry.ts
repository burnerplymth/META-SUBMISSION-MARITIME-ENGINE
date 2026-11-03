/* The export registry: a module-level store of what the
 * open windows can export, in the same pattern as windows/context.ts
 * windowMeta (Map + version + useSyncExternalStore). A panel registers on
 * mount, so the list is exactly what is available now. */
import { useEffect, useRef, useSyncExternalStore } from 'react';
import type { ExportContext, ExportableDef, ExportProvider } from './types';

const defs = new Map<string, ExportableDef>();
const providers = new Map<string, ExportProvider>();
const subs = new Set<() => void>();
let version = 0;
let provVersion = 0;
const bump = (): void => { version += 1; subs.forEach((f) => f()); };

/** Register one exportable. A later registration with the same id replaces the earlier one.
 *  Returns the unregister function (it only removes the entry it added). */
export function registerExportable(def: ExportableDef): () => void {
  defs.set(def.id, def);
  bump();
  return () => { if (defs.get(def.id) === def) { defs.delete(def.id); bump(); } };
}

export function registerExportProvider(p: ExportProvider): () => void {
  providers.set(p.id, p);
  provVersion += 1; bump();
  return () => { if (providers.get(p.id) === p) { providers.delete(p.id); provVersion += 1; bump(); } };
}

export const exportProviders = (): ExportProvider[] => [...providers.values()];

export interface ExportFilter { windowId?: string }

/** Registered (mounted) exportables, in registration order. Provider items come from `resolveProviders`. */
export function listExportables(filter: ExportFilter = {}): ExportableDef[] {
  const out = [...defs.values()];
  return filter.windowId ? out.filter((d) => d.windowId === filter.windowId) : out;
}
export const getExportable = (id: string): ExportableDef | undefined => defs.get(id);

/** Ask every provider for its items. A provider that throws contributes nothing. */
export async function resolveProviders(ctx: ExportContext, signal?: { aborted: boolean }): Promise<ExportableDef[]> {
  const lists = await Promise.all(exportProviders().map((p) => p.list(ctx).catch(() => [] as ExportableDef[])));
  return signal?.aborted ? [] : lists.flat();
}

export const registryVersion = (): number => version;
/** Changes only when a provider is added or removed (providers are asked again then, not on every panel mount). */
export const providerVersion = (): number => provVersion;
export function subscribeRegistry(f: () => void): () => void { subs.add(f); return () => { subs.delete(f); }; }

/** Mounted exportables, re-read when the registry changes. */
export function useExportables(filter: ExportFilter = {}): ExportableDef[] {
  useSyncExternalStore(subscribeRegistry, registryVersion, registryVersion);
  return listExportables(filter);
}

/** Hook for panels: registers on mount, keeps the latest closure in a ref (describe, preview and
 *  build read current data at call time), and re-registers only when the visible identity changes. */
export function useExportable(def: ExportableDef | null | undefined): void {
  const ref = useRef<ExportableDef | null | undefined>(def);
  ref.current = def;
  const id = def?.id; const windowId = def?.windowId; const title = def?.title; const kind = def?.kind;
  const formats = def ? def.formats.join(',') : '';
  useEffect(() => {
    const cur = ref.current;
    if (!cur) return;
    const live: ExportableDef = {
      id: cur.id, windowId: cur.windowId, title: cur.title, kind: cur.kind, formats: cur.formats, group: cur.group,
      describe: () => (ref.current ?? cur).describe(),
      preview: () => (ref.current ?? cur).preview?.() ?? null,
      loadPreview: cur.loadPreview ? () => (ref.current ?? cur).loadPreview?.() ?? Promise.resolve(null) : undefined,
      build: (f, o) => (ref.current ?? cur).build(f, o),
      report: cur.report ? (o) => (ref.current ?? cur).report?.(o) ?? Promise.resolve([]) : undefined,
      print: cur.print ? (o) => (ref.current ?? cur).print?.(o) ?? Promise.resolve(false) : undefined,
    };
    return registerExportable(live);
  }, [id, windowId, title, kind, formats]);
}
