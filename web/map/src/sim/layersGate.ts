// Layer model. Pure gating
// logic: layer state is a VIEW concern only — it never alters a run, a
// state response, or determinism. Persistence lives in the app layout
// store (web/app store, `simLayers` record in localStorage); this module
// owns the key set, the per-mode defaults, and the FLOWS-requires-LANES
// rule so both the map HUD and the app panel drive the same logic

import type { Mode } from "../types";

export type SimLayerKey =
  | "SEA"
  | "AIR"
  | "GROUND"
  | "LANES"
  | "FLOWS"
  | "WEATHER"
  | "DISRUPTIONS"
  | "SATELLITE";

export const SIM_LAYER_KEYS: SimLayerKey[] = [
  "SEA",
  "AIR",
  "GROUND",
  "LANES",
  "FLOWS",
  "WEATHER",
  "DISRUPTIONS",
  "SATELLITE",
];

export type SimLayers = Record<SimLayerKey, boolean>;

/** Defaults — LIVE/CANNED column. */
export const LIVE_LAYER_DEFAULTS: SimLayers = {
  SEA: true,
  AIR: true,
  GROUND: true,
  LANES: true,
  FLOWS: false,
  WEATHER: false,
  DISRUPTIONS: true,
  SATELLITE: false,
};

/** Defaults — SIM column. WEATHER defaults on *when the run has
 *  weather*; callers pass `hasWeather` (false keeps it off and the layer
 *  renders its honest UNAVAILABLE state when forced on). */
export function simLayerDefaults(hasWeather: boolean): SimLayers {
  return {
    SEA: true,
    AIR: true,
    GROUND: true,
    LANES: true,
    FLOWS: true,
    WEATHER: hasWeather,
    DISRUPTIONS: true,
    SATELLITE: false,
  };
}

export interface ResolvedLayers {
  effective: SimLayers;
  /** true when FLOWS was requested while LANES was off and LANES was
   *  auto-enabled to satisfy the dependency (FLOWS requires
   *  LANES — auto-enable + note). */
  flowsAutoEnabledLanes: boolean;
}

/** Apply the FLOWS -> LANES dependency. Never mutates the input. */
export function resolveSimLayers(layers: SimLayers): ResolvedLayers {
  if (layers.FLOWS && !layers.LANES) {
    return {
      effective: { ...layers, LANES: true },
      flowsAutoEnabledLanes: true,
    };
  }
  return { effective: { ...layers }, flowsAutoEnabledLanes: false };
}

/**
 * Toggle helper encoding the dependency at write time (used by the
 * store action): turning FLOWS on auto-enables LANES; turning LANES
 * off also turns FLOWS off (its requirement is gone). All other keys
 * toggle independently.
 */
export function withLayerToggled(
  layers: SimLayers,
  key: SimLayerKey,
  on: boolean,
): SimLayers {
  const next = { ...layers, [key]: on };
  if (key === "FLOWS" && on) next.LANES = true;
  if (key === "LANES" && !on) next.FLOWS = false;
  return next;
}

/** Mode-layer gate: entities of a mode render only if
 *  their mode layer is on; ground has no entities — its layer gates the
 *  corridor geometry/flow instead. */
export function modeLayerOn(layers: SimLayers, mode: Mode): boolean {
  if (mode === "sea") return layers.SEA;
  if (mode === "air") return layers.AIR;
  return layers.GROUND;
}

/** Lane geometry gate: LANES controls geometry for all modes
 *  independently, AND the mode layer gates its lanes' emphasis. */
export function laneGeometryOn(layers: SimLayers, mode: Mode): boolean {
  return layers.LANES && modeLayerOn(layers, mode);
}

/** Sanitise an untrusted persisted record (localStorage) into a full
 *  SimLayers — unknown keys dropped, missing keys defaulted. */
export function sanitiseSimLayers(
  raw: unknown,
  defaults: SimLayers,
): SimLayers {
  const out = { ...defaults };
  if (raw && typeof raw === "object") {
    for (const k of SIM_LAYER_KEYS) {
      const v = (raw as Record<string, unknown>)[k];
      if (typeof v === "boolean") out[k] = v;
    }
  }
  return resolveSimLayers(out).effective;
}
