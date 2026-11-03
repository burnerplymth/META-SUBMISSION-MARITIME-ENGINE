/**
 * Layouts the tour applies. A step names a built-in preset id (windows/presets.ts)
 * or one of the tour layouts below. Tour layouts are ordinary presets built with
 * the window model; they are never registered with the Layouts menu and never
 * saved, so the viewer's own presets are untouched.
 */
import { mapVisible, normaliseDock, split, tabs, viewsOf, type DockModel } from '../windows/model';
import { BUILTIN_PRESETS, PRESET_SCHEMA, builtinFor, phoneOrderOf, type Preset, type WindowParams } from '../windows/presets';

const T = tabs;
const col = (size: number, ...c: ReturnType<typeof tabs>[]) => (c.length === 1 ? { ...c[0], size } : split('v', size, ...c));
const row = (...c: Array<ReturnType<typeof tabs> | ReturnType<typeof split>>) => split('h', 1, ...c);

interface TourLayoutDef { id: string; name: string; legacy: string; dock: DockModel }

const DEFS: TourLayoutDef[] = [
  /* map wide, the chain beside it */
  { id: 'tour-map', name: 'Tour map', legacy: 'SupermAP', dock: {
    root: row(col(0.68, T(['map'], 1)), col(0.32, T(['chain', 'chaintrace'], 0.5, 'chain'), T(['mapcontent'], 0.5))), floats: [] } },
  /* chain and Explain stacked, the map beside them */
  { id: 'tour-chain', name: 'Tour chain', legacy: 'Domino Room', dock: {
    root: row(col(0.46, T(['map'], 1)), col(0.54, T(['chain', 'chaintrace'], 0.56, 'chain'), T(['explain'], 0.44))), floats: [] } },
  /* scenario loop beside the map and the chain (SIMULATION) */
  { id: 'tour-scenario', name: 'Tour scenario loop', legacy: 'Simulation', dock: {
    root: row(col(0.38, T(['map'], 0.5), T(['chain', 'chaintrace'], 0.5, 'chain')), col(0.62, T(['tool-scenarios'], 1))), floats: [] } },
  /* fleet: the map with its vessels, the Simulation window with the vehicle inspector */
  { id: 'tour-fleet', name: 'Tour fleet', legacy: 'Simulation', dock: {
    root: row(col(0.5, T(['map'], 1)), col(0.5, T(['sim'], 1))), floats: [] } },
  /* what-if and ensembles side by side */
  { id: 'tour-whatif', name: 'Tour what-if', legacy: 'Simulation', dock: {
    root: row(col(0.36, T(['map'], 0.5), T(['chain', 'chaintrace'], 0.5, 'chain')), col(0.64, T(['whatif'], 0.46), T(['ensemble'], 0.54))), floats: [] } },
  /* the chain ledger beside the map */
  { id: 'tour-ledger', name: 'Tour ledger', legacy: 'Replay', dock: {
    root: row(col(0.56, T(['ledger'], 1)), col(0.44, T(['map'], 0.5), T(['chain', 'chaintrace'], 0.5, 'chain'))), floats: [] } },
];

const links = (m: DockModel): Partial<Record<string, WindowParams>> =>
  Object.fromEntries(viewsOf(m).map((id) => [id, { link: 'A' as const }]));

const TOUR_PRESETS: readonly Preset[] = DEFS.map((d) => {
  const norm = normaliseDock(d.dock, { liveTwin: true })!;
  return {
    schema: PRESET_SCHEMA, id: d.id, name: d.name, builtin: false, legacy: d.legacy, mode: 'both' as const, flags: [],
    phone: phoneOrderOf(norm.dock), dock: norm.dock, windows: links(norm.dock), map: { visible: mapVisible(norm.dock) }, created: '2026-01-01T00:00:00Z',
  };
});

export const TOUR_LAYOUT_IDS: readonly string[] = TOUR_PRESETS.map((p) => p.id);

/** The preset for a tour layout id: a tour layout, else a built-in; null when unknown. */
export function tourPreset(id: string): Preset | null {
  return TOUR_PRESETS.find((p) => p.id === id) ?? builtinFor(id) ?? null;
}
/** True when `id` names a layout the tour can apply. */
export const isTourLayout = (id: string): boolean => tourPreset(id) !== null;
/** The built-in ids, for the tour steps. */
export const BUILTIN_IDS: readonly string[] = BUILTIN_PRESETS.map((p) => p.id);
