/* ---- PLACE PAINT (host-supplied per-place paint override) ----
 * A generic map capability: the host (web/app) supplies a colour —
 * and optionally an opacity — per place id, plus legend entries.
 * Listed places take the supplied colour for their core + halo FILL
 * only: state rings, severity rings, order badges and pulse halos are
 * separate paint and keep rendering over the painted fill, so a
 * painted place never loses its disruption state. Places NOT listed
 * render exactly as they do without the prop. The map reads the
 * values verbatim — it never derives a colour, class or label (CRIT
 * heat bands, COVER tiers, LENS emphasis are host semantics expressed
 * through this one primitive). */
import type { RGBA } from "./color";

export interface PlacePaintEntry {
  color: string;
  opacity?: number;
}

export interface PlacePaintLegendEntry {
  label: string;
  color: string;
}

export interface PlacePaint {
  entries: Record<string, PlacePaintEntry>;
  legend: PlacePaintLegendEntry[];
}

const clamp01 = (v: number): number =>
  Number.isFinite(v) ? Math.min(1, Math.max(0, v)) : 1;

/** The paint entry for a place, or undefined when the place is
 *  unlisted (or no paint is supplied) — unlisted = normal paint. */
export function placePaintEntryFor(
  paint: PlacePaint | null | undefined,
  id: string,
): PlacePaintEntry | undefined {
  return paint ? paint.entries[id] : undefined;
}

/** Legend entries to render alongside the kind legend ([] when no
 *  paint is supplied). The host's entries pass through verbatim. */
export function placePaintLegend(
  paint: PlacePaint | null | undefined,
): PlacePaintLegendEntry[] {
  return paint?.legend ?? [];
}

/** Parse a paint colour (#rgb / #rrggbb / #rrggbbaa / rgb() /
 *  rgba()) to [r, g, b, alpha01]; null when unparseable — callers
 *  fall back to the normal paint, so a bad value can never blank a
 *  place off the map. */
export function parsePaintColor(color: string): [number, number, number, number] | null {
  const s = color.trim();
  const hex = /^#([0-9a-f]{3}|[0-9a-f]{6}|[0-9a-f]{8})$/i.exec(s);
  if (hex) {
    const h = hex[1];
    if (h.length === 3) {
      return [
        parseInt(h[0] + h[0], 16),
        parseInt(h[1] + h[1], 16),
        parseInt(h[2] + h[2], 16),
        1,
      ];
    }
    return [
      parseInt(h.slice(0, 2), 16),
      parseInt(h.slice(2, 4), 16),
      parseInt(h.slice(4, 6), 16),
      h.length === 8 ? parseInt(h.slice(6, 8), 16) / 255 : 1,
    ];
  }
  const fn =
    /^rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*(?:,\s*([0-9]*\.?[0-9]+)\s*)?\)$/i.exec(s);
  if (fn) {
    return [
      Math.min(255, Number(fn[1])),
      Math.min(255, Number(fn[2])),
      Math.min(255, Number(fn[3])),
      fn[4] === undefined ? 1 : clamp01(Number(fn[4])),
    ];
  }
  return null;
}

/** A paint entry as an RGBA fill: the colour's own alpha scaled by
 *  the entry opacity (absent opacity = full strength, alpha 255).
 *  Null when the colour is unparseable. */
export function placePaintRgba(entry: PlacePaintEntry): RGBA | null {
  const parsed = parsePaintColor(entry.color);
  if (!parsed) return null;
  const [r, g, b, a] = parsed;
  const opacity = entry.opacity === undefined ? 1 : clamp01(entry.opacity);
  return [r, g, b, Math.round(a * opacity * 255)];
}
