/* A tiny SVG tree. Every chart builder returns VNodes; the React view
 * paints the SAME tree live, and toSvgString() serialises it for the
 * SVG / PNG exports, so what is exported is what is on screen (minus
 * the hover layer). Paint attributes may carry var(--token): the live
 * view lets the cascade resolve them, the serialiser resolves them
 * against a lookup so the file stands alone. */

export type Attr = string | number | undefined | null | false;
export interface VNode {
  t: string;
  a: Record<string, Attr>;
  c: Array<VNode | string>;
}

export function h(t: string, a: Record<string, Attr> | null, ...c: Array<VNode | string | null | false | undefined | Array<VNode | string | null | false>>): VNode {
  const kids: Array<VNode | string> = [];
  const push = (x: unknown) => {
    if (x == null || x === false) return;
    if (Array.isArray(x)) { x.forEach(push); return; }
    kids.push(x as VNode | string);
  };
  c.forEach(push);
  return { t, a: a ?? {}, c: kids };
}

const KEEP = new Set(['viewBox', 'preserveAspectRatio', 'gradientUnits', 'patternUnits', 'patternTransform', 'clipPathUnits', 'xmlns']);
const kebab = (k: string): string => (KEEP.has(k) || k.startsWith('data-') || k.startsWith('aria-') ? k : k.replace(/[A-Z]/g, (m) => `-${m.toLowerCase()}`));

export const escXml = (s: string): string =>
  s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const escAttr = (s: string): string => escXml(s).replace(/"/g, '&quot;');

/** Attributes that exist only for the live view (focus, handlers). */
const LIVE_ONLY = new Set(['tabIndex', 'role', 'focusable']);

export type VarLookup = (name: string) => string | undefined;

/** Replace var(--x) / var(--x, fallback) with resolved values. */
export function resolveVars(s: string, lookup: VarLookup): string {
  let out = s;
  for (let i = 0; i < 4 && out.includes('var('); i++) {
    out = out.replace(/var\(\s*(--[\w-]+)\s*(?:,\s*([^)]*))?\)/g, (_m, name: string, fb?: string) => {
      const v = lookup(name);
      return v !== undefined && v !== '' ? v : (fb?.trim() ?? 'currentColor');
    });
  }
  return out;
}

export function nodeToString(n: VNode | string, lookup?: VarLookup): string {
  if (typeof n === 'string') return escXml(n);
  const attrs = Object.entries(n.a)
    .filter(([k, v]) => v !== undefined && v !== null && v !== false && !LIVE_ONLY.has(k))
    .map(([k, v]) => {
      let val = String(v);
      if (lookup && val.includes('var(')) val = resolveVars(val, lookup);
      return `${kebab(k)}="${escAttr(val)}"`;
    })
    .join(' ');
  const open = attrs ? `<${n.t} ${attrs}` : `<${n.t}`;
  if (n.c.length === 0) return `${open}/>`;
  return `${open}>${n.c.map((c) => nodeToString(c, lookup)).join('')}</${n.t}>`;
}

/** A standalone SVG document string. */
export function toSvgString(nodes: Array<VNode | string>, size: { width: number; height: number }, lookup?: VarLookup, extra: Record<string, Attr> = {}): string {
  const root = h('svg', {
    xmlns: 'http://www.w3.org/2000/svg', width: size.width, height: size.height,
    viewBox: `0 0 ${size.width} ${size.height}`, ...extra,
  }, ...nodes);
  return `<?xml version="1.0" encoding="UTF-8"?>\n${nodeToString(root, lookup)}`;
}

/** Translate a node list into a group (small multiples). */
export const translate = (x: number, y: number, nodes: Array<VNode | string>, extra: Record<string, Attr> = {}): VNode =>
  h('g', { transform: `translate(${round(x)} ${round(y)})`, ...extra }, ...nodes);

/** Two decimals max, so path strings stay short and deterministic. */
export const round = (v: number): number => Math.round(v * 100) / 100;

/** Polyline path with gaps: null / non-finite y breaks the line. */
export function linePath(pts: ReadonlyArray<{ x: number; y: number | null }>, step = false): string {
  let d = ''; let pen = false;
  for (const p of pts) {
    if (p.y == null || !Number.isFinite(p.y) || !Number.isFinite(p.x)) { pen = false; continue; }
    if (!pen) d += `M${round(p.x)} ${round(p.y)}`;
    else d += step ? `H${round(p.x)}V${round(p.y)}` : `L${round(p.x)} ${round(p.y)}`;
    pen = true;
  }
  return d;
}
