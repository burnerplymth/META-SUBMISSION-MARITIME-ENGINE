/**
 * Small DOM helpers the real host uses to drive windows through their own
 * controls (the same buttons a person presses). Every wait is bounded and
 * gives up quietly when the step is stale.
 */
import type { ApplyCtx } from './engine';

const sleepMs = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

/** Poll until `pred` is truthy, the timeout passes or the step goes stale. Resolves to the last value. */
export async function waitFor<T>(pred: () => T | null | undefined | false, timeoutMs: number, ctx?: ApplyCtx, everyMs = 80): Promise<T | null> {
  const end = Date.now() + timeoutMs;
  for (;;) {
    let v: T | null | undefined | false = null;
    try { v = pred(); } catch { v = null; }
    if (v) return v as T;
    if (Date.now() >= end || ctx?.isStale()) return null;
    await sleepMs(everyMs);
  }
}

export const frames = (n = 2): Promise<void> => new Promise((resolve) => {
  const step = (k: number) => { if (k <= 0) resolve(); else requestAnimationFrame(() => step(k - 1)); };
  if (typeof requestAnimationFrame === 'undefined') { resolve(); return; }
  step(n);
});

const visible = (el: Element): boolean => {
  const r = (el as HTMLElement).getBoundingClientRect();
  return r.width > 0 && r.height > 0;
};

/** The first element matching `sel` whose text contains `text` (case-insensitive). */
export function findByText(sel: string, text: string, root: ParentNode = document): HTMLElement | null {
  const t = text.toLowerCase();
  for (const el of Array.from(root.querySelectorAll<HTMLElement>(sel))) {
    if ((el.textContent ?? '').toLowerCase().includes(t) && visible(el)) return el;
  }
  return null;
}

export function click(el: HTMLElement | null): boolean {
  if (!el || (el as HTMLButtonElement).disabled) return false;
  el.click();
  return true;
}

/** Set a controlled input's value the way a person typing would (React listens to `input`). */
export function setInputValue(el: HTMLInputElement, value: string): void {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
  setter?.call(el, value);
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
  el.dispatchEvent(new Event('blur', { bubbles: true }));
}

/** The element a window id lives in (the dock content wrapper). */
export const windowEl = (id: string): HTMLElement | null => document.querySelector<HTMLElement>(`.wm-win[data-wid="${id}"]`);
export const panelText = (id: string): string => windowEl(id)?.textContent ?? '';
