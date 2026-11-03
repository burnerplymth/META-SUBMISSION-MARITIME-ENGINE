/* Placement of a chart menu that is drawn at the page root (a portal), so a window's scroller or clip never cuts it.
 * Pure: the anchor rectangle and the viewport in, CSS out. The menu hangs from the anchor's right edge; it opens
 * downward unless there is clearly more room above. */
export interface PopStyle { right: number; top?: number; bottom?: number; maxHeight: number }

const GAP = 2;
const MARGIN = 8;
/** Room below that is enough for a menu of about eight rows. */
const ENOUGH = 240;

export function placePop(anchor: { top: number; bottom: number; right: number }, vw: number, vh: number): PopStyle {
  const below = vh - anchor.bottom - GAP - MARGIN;
  const above = anchor.top - GAP - MARGIN;
  const right = Math.max(MARGIN, Math.min(vw - MARGIN, vw - anchor.right));
  if (below >= ENOUGH || below >= above) return { right, top: anchor.bottom + GAP, maxHeight: Math.max(120, below) };
  return { right, bottom: vh - anchor.top + GAP, maxHeight: Math.max(120, above) };
}
