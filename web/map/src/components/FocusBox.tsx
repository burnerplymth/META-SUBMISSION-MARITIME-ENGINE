import { CloseMark } from "./CloseMark";
import type { GraphNode, NodeRunState, PortActivityRecord } from "../types";
import type { SimNodeState } from "../sim/types";
import { FOCUS_COPY, KIND_LABEL, calloutHeading, runStateLine, sourceLine, type FocusGoodsLine } from "./focus";
import { capFirst, railWord } from "../theme/railWord";

/**
 * PLACE FOCUS box — the overlay card that opens when a
 * place is focused: the place's served facts (graph record, PortWatch
 * activity, the loaded chain's state at the place, the live SIM board
 * when a run is in force, and host-supplied goods lines derived from
 * the served IMPACT payload), with the PLACE DESK action and a close
 * that restores the pre-focus camera. Nothing here is computed: every
 * figure is rendered as served, and a fact that is not served is
 * omitted, never filled in.
 *
 * Anchoring mirrors the inspector callout (projected point + clamps
 * computed by the parent); on narrow containers the parent docks it
 * full-width under the HUD instead.
 */
export interface FocusBoxProps {
  node: GraphNode;
  runState?: NodeRunState;
  activity?: PortActivityRecord;
  simNode?: SimNodeState | null;
  goods?: FocusGoodsLine[] | null;
  /** Unused since the card is corner-docked (kept so callers need no change). */
  x?: number;
  y?: number;
  flip?: boolean;
  shiftX?: number;
  shiftY?: number;
  docked?: boolean;
  onClose: () => void;
  onOpenInspector?: (id: string) => void;
}

const fmt = (v: number) =>
  v.toLocaleString("en-US", { maximumFractionDigits: 1 });

export function FocusBox({
  node, runState, activity, simNode, goods,
  onClose, onOpenInspector,
}: FocusBoxProps) {
  const kindLabel = railWord(KIND_LABEL[node.kind]);
  const hot = !!runState && runState.magnitude >= 0.75;
  const src = sourceLine(node);
  return (
    <div
      className={`sm-focusbox sm-card${hot ? " sev-hot" : ""}`}
      role="dialog"
      aria-label={`Focus: ${node.name}`}
    >
      <div className="sm-focusbox-head">
        <span className={`sm-focusbox-status${hot ? " hot" : ""}`}>
          {calloutHeading(node.name, runState?.channel)}
        </span>
        <button className="sm-callout-close" onClick={onClose} aria-label={FOCUS_COPY.closeLabel}>
          <CloseMark />
        </button>
      </div>
      <div className="sm-focusbox-sub">
        <span className="sm-tag">{kindLabel}</span>{" "}
        {node.tier ? <span className="sm-tag">{capFirst(node.tier)}</span> : null}{" "}
        {node.country ? <span className="sm-focusbox-country">{node.country}</span> : null}
      </div>
      <dl className="sm-callout-body">
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
        <dt>Chain</dt>
        <dd>
          {runState ? (
            <span className={`sm-run-state${hot ? " hot" : ""}`}>
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
        {simNode && (
          <>
            <dt>{FOCUS_COPY.sim}</dt>
            <dd>
              {FOCUS_COPY.queue} {simNode.queue} · {FOCUS_COPY.berthed} {simNode.berthed} ·{" "}
              {FOCUS_COPY.arrived} {simNode.arrived} · {FOCUS_COPY.departed} {simNode.departed}
            </dd>
          </>
        )}
        {goods && goods.length > 0 && (
          <>
            <dt>{FOCUS_COPY.goods}</dt>
            <dd>
              {goods.map((g) => (
                <span key={g.label} className="sm-focusbox-goods">
                  {g.label} {g.text}
                  {g.state ? <span className="sm-tag">{railWord(g.state)}</span> : null}
                </span>
              ))}
            </dd>
          </>
        )}
      </dl>
      <div className="sm-focusbox-actions">
        {onOpenInspector && (
          <button className="sm-focusbox-desk" onClick={() => onOpenInspector(node.id)}>
            {FOCUS_COPY.placeDesk} →
          </button>
        )}
        {src && <span className="sm-callout-src" title={src.title}>{FOCUS_COPY.source} {src.text}</span>}
      </div>
    </div>
  );
}
