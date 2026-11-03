import { getViewportForBounds } from '@xyflow/react';

/** The smallest zoom at which a chain step's text is read at about 8 px: below it the
 *  diagram stops shrinking to fit and shows the origin end at this zoom instead. */
export const CHAIN_MIN_FIT_ZOOM = 0.65;
const PAD = 0.14;

interface Rect { x: number; y: number; width: number; height: number }
export interface FlowViewport { x: number; y: number; zoom: number }

/** The viewport for a chain diagram: the whole chain when it fits at a legible zoom,
 *  else the top-left (origin) corner at the minimum legible zoom. Pure. */
export function chainFitViewport(bounds: Rect, paneW: number, paneH: number): FlowViewport {
  const fit = getViewportForBounds(bounds, paneW, paneH, 0.05, 1, PAD);
  if (fit.zoom >= CHAIN_MIN_FIT_ZOOM) return fit;
  const z = CHAIN_MIN_FIT_ZOOM;
  /* the left offset clears the zoom controls drawn over the pane's left edge */
  return { x: 54 - bounds.x * z, y: 20 - bounds.y * z, zoom: z };
}
