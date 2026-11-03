import { CloseMark } from "./CloseMark";
import type { GraphNode, NodeRunState, PortActivityRecord } from "../types";
import { FOCUS_COPY, calloutHeading, runStateLine, sourceLine } from "./focus";
import { capFirst } from "../theme/railWord";

/**
 * Map-side inspector: Bloomberg-style leader callout (no pins).
 * Positioned by the parent via map.project(); `flip` renders it on the
 * left of the point when the node is in the right half of the viewport.
 */
export interface InspectorProps {
  node: GraphNode;
  runState?: NodeRunState;
  activity?: PortActivityRecord;
  /** Unused since the card is corner-docked (kept so callers need no change). */
  x?: number;
  y?: number;
  flip?: boolean;
  /** Horizontal clamp shift (px) computed by the parent so the callout
   * panel never renders outside the map container.
   * 0 = unclamped. */
  shiftX?: number;
  /** Vertical clamp shift (px): a tall callout on a short map would lose
   * its title above the top edge. The leader line follows. */
  shiftY?: number;
  onClose: () => void;
}

const fmt = (v: number) =>
  v.toLocaleString("en-US", { maximumFractionDigits: 1 });

export function Inspector({ node, runState, activity, onClose }: InspectorProps) {
  const kindLabel =
    node.kind === "air_hub"
      ? "Air hub"
      : node.kind === "ground_node"
        ? "Ground"
        : node.kind.charAt(0).toUpperCase() + node.kind.slice(1);
  /* The place card is a compact floating card in the map's top-right corner (about 320 px wide,
   * scrolls inside, closable), not a callout hung off the place: the projected point, flip and clamp shifts
   * the parent still computes are not used for placement. */
  /* The head is a callout: severity-coloured
   * border + `NAME — STATUS` title + one-line sub (mag · mechanism ·
   * lands, all as served). The served kv body stays beneath it; the
   * clamp (shiftX) and the leader line apply as before. */
  const hot = !!runState && runState.magnitude >= 0.75;
  const subLine = runState
    ? `Magnitude ${runState.magnitude.toFixed(2)} · ${runState.mechanism} · Lands ${runState.at.replace("T", " ").replace("Z", " UTC")}`
    : `${kindLabel} · ${FOCUS_COPY.notInChain}`;
  const src = sourceLine(node);
  return (
    <div
      className={`sm-callout sm-card${hot ? " sev-hot" : ""}`}
      role="dialog"
      aria-label={`Inspector: ${node.name}`}
    >
      <div className={`sm-callout-inner${hot ? " sev-hot" : ""}`}>
        <div className="sm-callout-statusrow">
          <span className={`sm-callout-status${hot ? " hot" : ""}`}>
            {calloutHeading(node.name, runState?.channel)}
          </span>
          <button className="sm-callout-close" onClick={onClose} aria-label="Close inspector">
            <CloseMark />
          </button>
        </div>
        <div className="sm-callout-sub">{subLine}</div>
        <dl className="sm-callout-body">
          <dt>Mode / kind</dt>
          <dd>
            <span className="sm-tag">{capFirst(node.mode)}</span>{" "}
            <span className="sm-tag">{kindLabel}</span>
          </dd>
          <dt>Capacity</dt>
          <dd>
            {fmt(node.capacity)} {node.capacity_unit}
          </dd>
          <dt>Queue cap</dt>
          <dd>{fmt(node.queue_cap)}</dd>
          <dt>Inventory</dt>
          <dd>{node.inventory_days > 0 ? `${fmt(node.inventory_days)} d` : "—"}</dd>
          <dt>Coords</dt>
          <dd>
            {node.lat.toFixed(2)}°, {node.lon.toFixed(2)}°
          </dd>
          {activity && (
            <>
              <dt>PortWatch</dt>
              <dd>
                {fmt(activity.vessel_count_total)} vessels · Snapshot
              </dd>
            </>
          )}
          <dt>State</dt>
          <dd>
            {runState ? (
              <span className={`sm-run-state${runState.magnitude >= 0.75 ? " hot" : ""}`}>
                {runStateLine(runState)}
              </span>
            ) : (
              FOCUS_COPY.notInChain
            )}
          </dd>
          {runState && (
            <>
              <dt>Mechanism</dt>
              <dd>{runState.mechanism}</dd>
              <dt>Lands</dt>
              <dd>{runState.at.replace("T", " ").replace("Z", "Z")}</dd>
            </>
          )}
        </dl>
        {src && <div className="sm-callout-src" title={src.title}>{FOCUS_COPY.source} {src.text}</div>}
      </div>
    </div>
  );
}
