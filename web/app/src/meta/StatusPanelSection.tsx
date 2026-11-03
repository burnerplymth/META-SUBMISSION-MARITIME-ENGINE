/**
 * Meta AI status section of the status popover.
 * Rows only, no prose: endpoint HOST, cap source, last classifier case count,
 * the satellite SAM state. It is mounted only while the popover is open, so
 * its one read-only fetch (GET /satwatch/detectors) happens on open and
 * never otherwise. Every value is as served; what is not served reads
 * Not served.
 */
import React, { useEffect, useState } from 'react';
import { getDetectors } from './client';
import { statusPanelView, type DetectorsDoc, type StatusPanelView, type StatusPanelRow } from './statusPanel';
import type { LlmStatusDoc } from './statusView';

export const STATUS_PANEL_TITLE = 'Meta AI status';

export function StatusPanelRows({ view }: { view: StatusPanelView }) {
  return (
    <section className="kd" aria-label={STATUS_PANEL_TITLE} data-flag={view.flag}>
      <div className="kd-head">
        <span>{STATUS_PANEL_TITLE}</span>
        <span className={`kd-flag kd-flag--${view.flagTone}`}>{view.flag}</span>
      </div>
      <dl className="metachip-rows kd-rows">
        {view.rows.map((r: StatusPanelRow) => (
          <React.Fragment key={r.k}>
            <dt>{r.k}</dt>
            <dd className={r.tone === 'plain' ? undefined : `kd-v--${r.tone}`} data-tone={r.tone}>{r.v}</dd>
          </React.Fragment>
        ))}
      </dl>
    </section>
  );
}

export function StatusPanelSection({ status }: { status: LlmStatusDoc | null }) {
  const [det, setDet] = useState<DetectorsDoc | null>(null);
  useEffect(() => {
    const ctrl = new AbortController();
    getDetectors(ctrl.signal).then((d) => setDet(d as DetectorsDoc)).catch(() => setDet(null));
    return () => ctrl.abort();
  }, []);
  return <StatusPanelRows view={statusPanelView(status, det)} />;
}
