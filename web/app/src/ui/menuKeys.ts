import { useCallback, useEffect, useRef, type KeyboardEvent as ReactKeyboardEvent, type RefObject } from 'react';

/* Keyboard pattern for a role="menu" popover (WAI-ARIA menu): focus moves to
 * the first item when it opens, Arrow Up and Down (and Home, End) move between
 * items, Escape closes and puts focus back on the control that opened it. Items
 * are the enabled elements with role menuitem, menuitemradio or menuitemcheckbox.
 * Text inputs inside the popover keep their own arrow keys. */

const ITEM = '[role="menuitem"],[role="menuitemradio"],[role="menuitemcheckbox"]';

export const menuItems = (root: ParentNode | null | undefined): HTMLElement[] =>
  Array.from(root?.querySelectorAll<HTMLElement>(ITEM) ?? []).filter((el) => !(el as HTMLButtonElement).disabled);

/** Move focus along `items` for a navigation key. Returns true when the key was handled. */
export function navigateMenu(key: string, items: readonly HTMLElement[], active: Element | null): boolean {
  if (!items.length) return false;
  const at = items.indexOf(active as HTMLElement);
  let to: number;
  if (key === 'ArrowDown') to = at < 0 ? 0 : (at + 1) % items.length;
  else if (key === 'ArrowUp') to = at < 0 ? items.length - 1 : (at - 1 + items.length) % items.length;
  else if (key === 'Home') to = 0;
  else if (key === 'End') to = items.length - 1;
  else return false;
  items[to].focus();
  return true;
}

/**
 * Wire a menu popover. `rootRef` is the element carrying role="menu";
 * `openerRef` the control to refocus on close; `open` whether it is shown.
 * Returns an onKeyDown for the menu element. Escape is handled here too, so a
 * parent only supplies `onClose`.
 */
export function useMenuKeys(open: boolean, rootRef: RefObject<HTMLElement | null>, openerRef: RefObject<HTMLElement | null>, onClose: () => void) {
  const was = useRef(false);
  useEffect(() => {
    if (open && !was.current) {
      const id = window.requestAnimationFrame(() => menuItems(rootRef.current)[0]?.focus());
      was.current = true;
      return () => window.cancelAnimationFrame(id);
    }
    if (!open) was.current = false;
    return undefined;
  }, [open, rootRef]);
  return useCallback((e: ReactKeyboardEvent) => {
    if (e.key === 'Escape') {
      e.preventDefault(); e.stopPropagation();
      onClose();
      openerRef.current?.focus();
      return;
    }
    const t = e.target as HTMLElement;
    if (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT') return;
    if (navigateMenu(e.key, menuItems(rootRef.current), document.activeElement)) e.preventDefault();
  }, [onClose, openerRef, rootRef]);
}
