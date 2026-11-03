/* Tooltip-less hint helpers. A hint is the control name
 * plus its shortcut, three words at most: `Float  Alt+Shift+F`. It rides on
 * the native `title` (and `aria-label` / `aria-keyshortcuts`); there is no
 * tooltip component to mount. Sentences do not belong in a hint. */

/** `hint('Float', 'Alt+Shift+F')` -> `Float  Alt+Shift+F` (two spaces). */
export const hint = (name: string, keys?: string): string => (keys ? `${name}  ${keys}` : name);

/** A hint is terse when its name is at most three words (shortcut excluded). */
export const isTerseHint = (name: string): boolean => name.trim().split(/\s+/).filter(Boolean).length <= 3;

/** Split a shortcut into key caps: `Alt+Shift+F` -> [`Alt`, `Shift`, `F`]. A
 *  trailing `+` is the plus key (`Ctrl++`). `Mod` reads as `Ctrl`. */
export function keyCaps(keys: string): string[] {
  const t = keys.trim();
  const plus = t.endsWith('++') ? ['+'] : [];
  const head = plus.length ? t.slice(0, -2) : t;
  return [...head.split('+').map((k) => k.trim()).filter(Boolean).map((k) => (k === 'Mod' ? 'Ctrl' : k)), ...plus];
}

/** Value for `aria-keyshortcuts`: `Alt+Shift+F`. */
export const ariaKeys = (keys: string): string => keyCaps(keys).join('+');
