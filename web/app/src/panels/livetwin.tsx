import React, { useMemo, useState } from 'react';
import { Panel } from '../components/Panel';
import { fmtDayTime, humaniseIds, nodeLabel, prettyKind } from '../format';
import { noteWords } from './display';
import { defaultSimRequest, useStore } from '../store';
import { Chip } from '../shell/Chip';
import { provenanceLabel, truthWordText } from '../shell/truthLabel';
import type { FeedEvent, GraphData, Health, LiveTwinDoc } from '../types';
import type { SimRunRequest } from '../../../map/src/sim/types';

/* LIVE TWIN — the flag-gated section's only panel. Everything
 * on it is a served live fact rendered as served: feed states and
 * notes, per-place air aggregates with their provenance, the vessel
 * state and its note, and the disruption feed's active board. The one
 * action, SIMULATE FROM LIVE, seeds a run in the SIMULATION section
 * from the checked live disruptions — it is the panel's only bridge
 * out, and nothing on this panel is itself simulated. */

/** The board's active set: every feed record whose status is not
 *  'resolved', worst first — severity descending, then most recently
 *  detected first. Pure, so the order is testable off the store. */
export function activeDisruptions(feed: FeedEvent[]): FeedEvent[] {
  return feed
    .filter((f) => f.status !== 'resolved')
    .sort((a, b) => (b.severity - a.severity)
      || (a.detected_at < b.detected_at ? 1 : a.detected_at > b.detected_at ? -1 : 0));
}

/** The seed request for the bridge: the default run's shape with the
 *  checked live disruptions in feed-reference form (exactly
 *  {disruption_id} per entry), weather pinned, day tick × 30 kept
 *  from the default, and the start re-anchored to the current UTC
 *  hour — the default's own anchoring idiom. */
export function buildLiveSeedRequest(ids: string[], now = new Date()): SimRunRequest {
  const base = defaultSimRequest();
  const hour = new Date(now);
  hour.setUTCMinutes(0, 0, 0);
  const start_at = hour.toISOString().replace(/\.\d{3}Z$/, 'Z');
  return {
    ...base,
    disruptions: ids.map((id) => ({ disruption_id: id })),
    weather: true,
    start_at,
  };
}

/** The seed note the SIM panel carries for a bridged run. */
export function seedNoteFor(count: number, at: Date): string {
  return `Seeded from the feed · ${count} disruptions · ${at.toISOString().slice(11, 16)} UTC`;
}

export interface LiveSeedStore {
  createSimRun: (req: SimRunRequest) => Promise<string | null>;
  setSimSeedNote: (note: string | null) => void;
  setLayout: (name: string) => void;
  setFocus: (p: 'sim') => void;
}

/** The bridge itself, hook-free so it is testable against the real
 *  store: create the run from the checked live disruptions; on
 *  success stamp the seed note (after creation resolves, so the SIM
 *  panel's own clearing paths can never eat it), switch the layout
 *  back to the desk and focus the SIM panel. A failure returns the
 *  store's error line for the panel to show — never a silent no-op. */
export async function simulateFromLive(store: LiveSeedStore, ids: string[], now = new Date()): Promise<string | null> {
  const err = await store.createSimRun(buildLiveSeedRequest(ids, now));
  if (err) return err;
  store.setSimSeedNote(seedNoteFor(ids.length, now));
  store.setLayout('SupermAP');
  store.setFocus('sim');
  return null;
}

/** Rows of the disruptions board shown before the +N chip. The checked set
 *  still covers every active disruption; the chip only folds the rows. */
export const LT_BOARD_ROWS = 8;

const fmtEpochTime = (epochSeconds: number): string =>
  fmtDayTime(new Date(epochSeconds * 1000).toISOString());

/** The word an air aggregate's provenance wears: LIVE only when the stored
 *  mode is LIVE, the feed is live now and the record is fresh; else the
 *  Snapshot or Recorded word with the retrieval date. Pure. */
export function airProvenanceText(p: { source: string; mode: string; retrieved_at: string }, health: Health | null | undefined, snapshotMode: boolean, now: number): string {
  const l = provenanceLabel(p, health, snapshotMode, now);
  if (l?.live) return `${p.source} · Live · retrieved ${fmtDayTime(p.retrieved_at)}`;
  return `${p.source} · ${truthWordText(l?.word ?? 'RECORDED')} · retrieved ${fmtDayTime(p.retrieved_at)}`;
}

export function LiveTwinPanelView({ doc, feed, graph, onSimulate, health = null, snapshotMode = false, now = Date.now() }: {
  doc: LiveTwinDoc;
  health?: Health | null;
  snapshotMode?: boolean;
  now?: number;
  feed: FeedEvent[];
  graph: GraphData;
  onSimulate: (ids: string[]) => Promise<string | null>;
}) {
  const active = useMemo(() => activeDisruptions(feed), [feed]);
  const activeKey = active.map((f) => f.id).join('|');
  const [checked, setChecked] = useState<ReadonlySet<string>>(() => new Set(active.map((f) => f.id)));
  const [checkedFor, setCheckedFor] = useState(activeKey);
  /* The selection is keyed on the joined active ids: a feed refresh
   * that changes the active set resets to all-checked, and one that
   * does not leaves a selection in progress exactly as it was. */
  if (checkedFor !== activeKey) {
    setCheckedFor(activeKey);
    setChecked(new Set(active.map((f) => f.id)));
  }
  const [expanded, setExpanded] = useState(false);
  const shown = expanded ? active : active.slice(0, LT_BOARD_ROWS);
  const hidden = active.length - Math.min(active.length, LT_BOARD_ROWS);
  const [pending, setPending] = useState(false);
  const [bridgeError, setBridgeError] = useState<string | null>(null);
  const checkedActive = active.filter((f) => checked.has(f.id));
  const toggle = (id: string, on: boolean) => {
    setChecked((prev) => {
      const next = new Set(prev);
      if (on) next.add(id); else next.delete(id);
      return next;
    });
  };
  const simulate = async () => {
    setBridgeError(null);
    setPending(true);
    const err = await onSimulate(checkedActive.map((f) => f.id));
    setPending(false);
    if (err) setBridgeError(err);
  };
  return (
    <>
      <h5>Feeds</h5>
      {doc.feeds.map((f) => (
        <p key={f.id} className="health-row">
          <span className={`dot dot--${f.state.toLowerCase()}`} />
          <b>{f.label}</b>
          <span className="mono">{f.state}</span>
          <span className="dim mono small">{f.retrieved_at ? fmtDayTime(f.retrieved_at) : 'Never'}</span>
          {f.records != null && <span className="mono small">{f.records} records</span>}
          {noteWords(f.note) && <span className="dim small">{noteWords(f.note)}</span>}
        </p>
      ))}
      {!doc.feeds.length && <p className="dim">No feeds</p>}
      <h5>Air · Aircraft near places</h5>
      {doc.air.map((r) => (
        <React.Fragment key={`${r.node_id}-${r.observed_at_epoch}`}>
          <p className="board-row">
            <span>{nodeLabel(r.node_id, graph)}</span>
            <span className="mono amber">{r.aircraft_in_bbox} aircraft</span>
          </p>
          <p className="fineprint mono">Observed {fmtEpochTime(r.observed_at_epoch)} · {airProvenanceText(r.provenance, health, snapshotMode, now)}</p>
        </React.Fragment>
      ))}
      {!doc.air.length && <p className="dim">No aggregates</p>}
      <h5>Vessels</h5>
      <p className="board-row">
        <span><span className={`dot dot--${doc.vessels.state.toLowerCase()}`} /> {doc.vessels.state}</span>
        <span className="mono amber">{doc.vessels.records.length} vessels</span>
      </p>
      {noteWords(doc.vessels.note) && <p className="fineprint">{noteWords(doc.vessels.note)}</p>}
      <h5>Active disruptions</h5>
      <div className={`lt-board${expanded ? ' lt-board--open' : ''}`} data-lt-board>
        {shown.map((f) => (
          <label key={f.id} className="lt-row">
            <input type="checkbox" checked={checked.has(f.id)}
              onChange={(e) => toggle(f.id, e.target.checked)} />
            <span className="lt-head">{humaniseIds(f.headline, graph)}</span>
            <span className="lt-meta dim mono">{nodeLabel(f.node_id, graph)} · <span className={`sev-tag ${f.severity >= 4 ? 'sev-tag--hot' : ''}`}>Sev {f.severity}</span> · {prettyKind(f.kind)} · Detected {fmtDayTime(f.detected_at)}</span>
          </label>
        ))}
      </div>
      {hidden > 0 && (
        <button type="button" className="lt-more" aria-expanded={expanded} onClick={() => setExpanded((x) => !x)}>
          <Chip kind="meta" word={expanded ? 'Show fewer' : `+${hidden}`} />
        </button>
      )}
      {!active.length && <p className="dim">No active disruptions</p>}
      <p className="fineprint">{checkedActive.length} active disruptions · Weather pinned · Day tick × 30</p>
      <button className="btn btn--amber" disabled={pending || checkedActive.length === 0}
        onClick={() => void simulate()}>
        {pending ? 'Creating run' : 'Simulate from live'}</button>
      {bridgeError && <p className="alr-err mono">{bridgeError}</p>}
    </>
  );
}

export function LiveTwinPanel() {
  const s = useStore();
  const doc = s.liveTwin;
  return (
    <Panel id="livetwin" code="LTV" title="Live twin"
      updated={doc ? fmtDayTime(doc.generated_at) : undefined}
      exportSpec={{ json: () => s.liveTwin, source: 'OpenSky Network; NOAA / NWS; GDACS; AISStream' }}>
      {doc
        ? <LiveTwinPanelView doc={doc} feed={s.feed} graph={s.graph} health={s.health} snapshotMode={s.snapshotMode} now={Date.now()}
            onSimulate={(ids) => simulateFromLive(s, ids)} />
        : <p className="dim">Not served</p>}
    </Panel>
  );
}
