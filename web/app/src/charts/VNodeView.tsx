import React from 'react';
import type { VNode } from './vnode';

/** Presentation attributes that may carry var(--token): React writes an
 *  SVG attribute verbatim, so they move to inline style, where the
 *  cascade resolves them. */
const PAINT = new Set(['fill', 'stroke', 'stopColor', 'color']);

function styleObject(s: string): React.CSSProperties {
  const o: Record<string, string> = {};
  for (const decl of s.split(';')) {
    const i = decl.indexOf(':');
    if (i < 0) continue;
    const k = decl.slice(0, i).trim().replace(/-([a-z])/g, (_m, c: string) => c.toUpperCase());
    if (k) o[k] = decl.slice(i + 1).trim();
  }
  return o as React.CSSProperties;
}

export function toElement(n: VNode | string, key?: number): React.ReactNode {
  if (typeof n === 'string') return n;
  const props: Record<string, unknown> = { key };
  const style: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(n.a)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === 'style' && typeof v === 'string') { Object.assign(style, styleObject(v)); continue; }
    if (PAINT.has(k) && typeof v === 'string' && v.includes('var(')) { style[k] = v; continue; }
    props[k] = v;
  }
  if (Object.keys(style).length) props.style = style;
  return React.createElement(n.t, props, ...n.c.map((c, i) => toElement(c, i)));
}

/** Paints a node list; memoised on the array identity, so a hover
 *  re-render does not rebuild thousands of elements. */
export const VNodes = React.memo(function VNodes({ nodes }: { nodes: Array<VNode | string> }) {
  return <>{nodes.map((n, i) => toElement(n, i))}</>;
});
