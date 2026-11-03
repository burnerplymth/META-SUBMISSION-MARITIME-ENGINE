import React from 'react';
import { Chip, MetaChip } from '../reports/chips';
import { detectorRows, type DetectorDocProvider } from './metaAi';
import type { DetectorsDoc } from './model';

/* DETECTOR SELECTOR. One row per registered detector with its
 * state exactly as the server states it (Awaiting key / Ready / Not served /
 * Offline test), the selected one marked, and for SAM 3.1 the daily cap, the
 * calls made today, the estimated spend and the price. Chips only. Ready is
 * a state, not a result: no detector chip here ever reads Live. */
export function DetectorStrip({ doc }: { doc: DetectorsDoc }) {
  const rows = detectorRows(doc.providers as unknown as DetectorDocProvider[]);
  return (
    <div className="sw2-dets" role="group" aria-label="Detector">
      <span className="rk-lbl">Detector</span>
      {doc.config_error && <Chip chip="DOWN" word="Config error" title={doc.config_error} />}
      {rows.map((r) => (
        <span key={r.id} className={`sw2-det-row${r.selected ? ' on' : ''}`} data-detector={r.id} data-state={r.state.word} aria-current={r.selected ? 'true' : undefined} title={r.title}>
          <span className="sw2-det-name">{r.label}</span>
          {r.selected && <MetaChip>Selected</MetaChip>}
          <Chip chip={r.state.chip} word={r.state.word} tone={r.state.tone} />
          {r.extras.map((x) => <MetaChip key={x}>{x}</MetaChip>)}
        </span>
      ))}
    </div>
  );
}
