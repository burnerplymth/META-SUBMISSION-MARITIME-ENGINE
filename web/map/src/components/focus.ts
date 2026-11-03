import type { NodeKind } from "../types";

/**
 * PLACE FOCUS — the pure state machine behind the map's
 * focus box. Selecting a place (marker click, or a host focus request
 * from search / feed / the right-rail inspector) engages focus: the
 * camera flies to the place and the focus box opens. Closing the box
 * restores the camera captured at engage time.
 *
 * The machine is deliberately free of map/React types so it runs
 * on its own: engage from idle captures the restore camera;
 * retargeting while focused KEEPS the original restore camera (the
 * focus session ends where the user was before it started); release
 * yields the restore camera exactly once; drop ends the session with
 * no restore (the host took the camera over — AUTO DEMO, mode switch,
 * an external deselection).
 */

export interface CameraView {
  center: [number, number];
  zoom: number;
}

export type FocusState =
  | { phase: "idle" }
  | { phase: "focused"; nodeId: string; restore: CameraView };

export const IDLE_FOCUS: FocusState = { phase: "idle" };

/** Focus zoom per place kind: ports / air hubs / ground hubs close,
 *  chokepoints wider (a strait is a corridor, not a berth). */
export function focusZoomForKind(kind: NodeKind): number {
  return kind === "chokepoint" ? 4.5 : 6;
}

export function engageFocus(
  state: FocusState,
  nodeId: string,
  camera: CameraView,
): FocusState {
  if (state.phase === "focused") {
    return { phase: "focused", nodeId, restore: state.restore };
  }
  return { phase: "focused", nodeId, restore: camera };
}

export function releaseFocus(
  state: FocusState,
): { state: FocusState; restore: CameraView | null } {
  if (state.phase === "focused") {
    return { state: IDLE_FOCUS, restore: state.restore };
  }
  return { state: IDLE_FOCUS, restore: null };
}

export function dropFocus(_state: FocusState): FocusState {
  return IDLE_FOCUS;
}

/** One host-supplied goods-summary line for the focus box (the host
 *  derives these from its served IMPACT payload; the map renders them
 *  verbatim and never computes figures itself). */
export interface FocusGoodsLine {
  label: string;
  text: string;
  state?: string;
}

export const KIND_LABEL: Record<NodeKind, string> = {
  port: "Port",
  chokepoint: "Chokepoint",
  air_hub: "Air hub",
  ground_node: "Ground",
};

/** Place-kind legend: COLOUR carries the kind (plus a ring
 *  on chokepoints). The key renders one coloured point per row from
 *  the theme's place palette (theme/tokens.ts placeKindColors). */
export const KIND_LEGEND: ReadonlyArray<{
  kind: NodeKind;
  glyph: string;
  label: string;
}> = [
  { kind: "port", glyph: "●", label: "Port" },
  { kind: "air_hub", glyph: "●", label: "Air hub" },
  { kind: "ground_node", glyph: "●", label: "Ground" },
  { kind: "chokepoint", glyph: "◎", label: "Chokepoint" },
];

export const FOCUS_COPY = {
  placeDesk: "Place desk",
  closeLabel: "Close focus",
  notInChain: "Not in selected chain",
  goods: "Goods",
  sim: "Simulated",
  queue: "Queue",
  berthed: "Berthed",
  arrived: "Arrived",
  departed: "Departed",
  source: "Source",
} as const;

/** Clamp at a word boundary with an ellipsis. `truncated` is true only when text was cut. */
export function clampAtWord(text: string, max: number): { text: string; truncated: boolean } {
  const t = text.trim();
  if (t.length <= max) return { text: t, truncated: false };
  const head = t.slice(0, max - 1);
  const sp = head.lastIndexOf(" ");
  const cut = sp > 0 ? head.slice(0, sp) : head;
  return { text: `${cut.replace(/[\s,;:/\-]+$/, "")}\u2026`, truncated: true };
}

/** The place's first usable source as a short chip text; the full text rides as a title only
 *  when clamped. Null when the place serves no source (nothing is printed). */
export function sourceLine(node: { sources?: readonly string[] | null }, max = 44): { text: string; title: string | undefined } | null {
  const first = (node.sources ?? []).map((x) => (typeof x === "string" ? x.trim() : "")).find((x) => x.length > 0);
  if (!first) return null;
  const c = clampAtWord(first, max);
  return { text: c.text, title: c.truncated ? first : undefined };
}

const STATUS_WORDS: Record<string, string> = {
  queue: "Queued", congestion: "Congested", reroute: "Rerouted",
  "mode-shift": "Mode-shift", inventory: "Inventory", cost: "Cost",
};

/** Camera offset [x, y] in px that parks a flown-to place left of the floating place card (right-docked, 46 cqw up to 320 px).
 *  Narrow containers dock the card under the toolbar instead, so the place stays centred there. */
export function cardCameraOffset(containerWidth: number): [number, number] {
  if (containerWidth < 520) return [0, 0];
  const card = Math.min(320, containerWidth * 0.46);
  return [-Math.round((card + 56) / 2), 0];
}

const CHANNEL_WORDS: Record<string, string> = {
  queue: "Queue", congestion: "Congestion", reroute: "Reroute", "mode-shift": "Mode shift", inventory: "Inventory", cost: "Cost",
};

/** The chain state of a place as one line of words and units: "Step 1 · Congestion · Magnitude 0.54 · +6.7 d". */
export function runStateLine(r: { seq: number; channel: string; magnitude: number; delay_d: number }): string {
  const word = CHANNEL_WORDS[r.channel] ?? r.channel.charAt(0).toUpperCase() + r.channel.slice(1);
  return `Step ${r.seq + 1} \u00b7 ${word} \u00b7 Magnitude ${r.magnitude.toFixed(2)} \u00b7 +${r.delay_d.toFixed(1)} d`;
}

/** Popup heading: the place name, then the channel as a sentence-case state word. */
export function calloutHeading(name: string, channel: string | null | undefined): string {
  if (!channel) return name;
  const word = STATUS_WORDS[channel] ?? channel.charAt(0).toUpperCase() + channel.slice(1);
  return `${name} — ${word}`;
}

/** Vertical shift (px) that keeps a popup of height `popupH`, vertically
 *  centred on the point at `y`, inside a container of height `containerH`
 *  with an 8 px margin. 0 when it already fits. */
export function popupShiftY(y: number, containerH: number, popupH: number, margin = 8): number {
  const desiredTop = y - popupH / 2;
  const clampedTop = Math.min(Math.max(desiredTop, margin), Math.max(margin, containerH - popupH - margin));
  return clampedTop - desiredTop;
}

/** Commentary-prose patterns banned from user-visible copy: text that explains the app's own construction,
 *  loading mechanics or fallbacks. Data provenance chips and state
 *  labels are a different class and stay. The focus/legend copy above
 *  is held to this list. */
export const BANNED_COMMENTARY_PATTERNS: ReadonlyArray<RegExp> = [
  /loaded in the browser/i,
  /bundled with the app/i,
  /becomes visible only/i,
  /not the live basemap/i,
  /drawn beneath/i,
  /so the map never/i,
  /reference imagery, dates vary/i,
  /scenery/i,
];
