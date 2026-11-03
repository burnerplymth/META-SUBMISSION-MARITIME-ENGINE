// PLACE LABEL PLACEMENT (pure, deterministic). Candidates are placed
// greedily by priority in WORLD-pixel space at the quantised zoom, so
// the result is translation-invariant: panning never reshuffles which
// labels survive, only zooming does. A label that would overlap an
// already placed label, or another place's marker, tries the opposite
// side of its own marker and is dropped when neither fits.

import { worldPx } from "./cluster";

export interface LabelCandidate {
  id: string;
  text: string;
  lon: number;
  lat: number;
  /** higher wins; ties break on id ascending */
  priority: number;
  /** marker core radius in px (the label sits clear of it) */
  radiusPx: number;
  /** placed first, before any collision (selection, chain members) */
  forced?: boolean;
  /** try the left side of the marker first (the selected place: the
   *  callout opens to its right) */
  preferEnd?: boolean;
}

export interface PlacedLabel {
  id: string;
  text: string;
  lon: number;
  lat: number;
  /** text anchor relative to the marker: "start" = label to the right */
  anchor: "start" | "end";
  /** pixel offset from the marker centre for deck's getPixelOffset */
  dx: number;
}

export interface LabelOptions {
  /** glyph advance in px (mono, ~10 px type) */
  charPx?: number;
  heightPx?: number;
  padPx?: number;
  gapPx?: number;
  /** cap on placed labels (forced labels do not count) */
  max?: number;
  /** extra markers labels must clear: [lon, lat, radiusPx] */
  obstacles?: ReadonlyArray<readonly [number, number, number]>;
}

interface Box {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

const overlap = (a: Box, b: Box): boolean =>
  a.x0 < b.x1 && a.x1 > b.x0 && a.y0 < b.y1 && a.y1 > b.y0;

/** Label text width estimate (mono caps). */
export function labelWidthPx(text: string, charPx = 6.1): number {
  return text.length * charPx;
}

class Grid {
  private cell = 64;
  private cells = new Map<string, Box[]>();
  private keys(b: Box): string[] {
    const out: string[] = [];
    for (let i = Math.floor(b.x0 / this.cell); i <= Math.floor(b.x1 / this.cell); i++) {
      for (let j = Math.floor(b.y0 / this.cell); j <= Math.floor(b.y1 / this.cell); j++) out.push(`${i}:${j}`);
    }
    return out;
  }
  hits(b: Box, ignore?: Box): boolean {
    for (const k of this.keys(b)) {
      const arr = this.cells.get(k);
      if (arr) for (const o of arr) if (o !== ignore && overlap(o, b)) return true;
    }
    return false;
  }
  add(b: Box): void {
    for (const k of this.keys(b)) {
      const arr = this.cells.get(k);
      if (arr) arr.push(b);
      else this.cells.set(k, [b]);
    }
  }
}

export function selectLabels(
  cands: ReadonlyArray<LabelCandidate>,
  zoom: number,
  opts: LabelOptions = {},
): PlacedLabel[] {
  const charPx = opts.charPx ?? 6.1;
  const h = opts.heightPx ?? 12;
  const pad = opts.padPx ?? 2;
  const gap = opts.gapPx ?? 4;
  const max = opts.max ?? 220;
  const grid = new Grid();
  const pos = new Map<string, [number, number]>();
  /* every candidate's marker is an obstacle for OTHER labels */
  const markerBox = (x: number, y: number, r: number): Box => ({ x0: x - r, y0: y - r, x1: x + r, y1: y + r });
  const own = new Map<string, Box>();
  for (const c of cands) {
    const p = worldPx(c.lon, c.lat, zoom);
    pos.set(c.id, p);
    own.set(c.id, markerBox(p[0], p[1], c.radiusPx + 1));
  }
  const obstacleBoxes: Box[] = (opts.obstacles ?? []).map(([lon, lat, r]) => {
    const p = worldPx(lon, lat, zoom);
    return markerBox(p[0], p[1], r + 1);
  });
  const markerGrid = new Grid();
  for (const b of own.values()) markerGrid.add(b);
  for (const b of obstacleBoxes) markerGrid.add(b);

  const ordered = [...cands].sort(
    (a, b) =>
      Number(!!b.forced) - Number(!!a.forced) ||
      b.priority - a.priority ||
      (a.id < b.id ? -1 : a.id > b.id ? 1 : 0),
  );
  const placed: PlacedLabel[] = [];
  let free = 0;
  for (const c of ordered) {
    if (!c.forced && free >= max) break;
    const [x, y] = pos.get(c.id) as [number, number];
    const w = labelWidthPx(c.text, charPx) + pad * 2;
    const mine = own.get(c.id) as Box;
    const tries: Array<{ anchor: "start" | "end"; box: Box; dx: number }> = [
      {
        anchor: "start",
        dx: c.radiusPx + gap,
        box: { x0: x + c.radiusPx + gap - pad, y0: y - h / 2, x1: x + c.radiusPx + gap - pad + w, y1: y + h / 2 },
      },
      {
        anchor: "end",
        dx: -(c.radiusPx + gap),
        box: { x0: x - c.radiusPx - gap + pad - w, y0: y - h / 2, x1: x - c.radiusPx - gap + pad, y1: y + h / 2 },
      },
    ];
    let chosen: (typeof tries)[number] | null = null;
    if (c.preferEnd) tries.reverse();
    for (const t of tries) {
      if (grid.hits(t.box)) continue;
      /* clear of OTHER markers (its own marker is beside it by design) */
      if (markerGrid.hits(t.box, mine) && !c.forced) continue;
      chosen = t;
      break;
    }
    if (!chosen) {
      if (!c.forced) continue;
      chosen = tries[0];
    }
    grid.add(chosen.box);
    placed.push({ id: c.id, text: c.text, lon: c.lon, lat: c.lat, anchor: chosen.anchor, dx: chosen.dx });
    if (!c.forced) free++;
  }
  return placed;
}
