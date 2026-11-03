import React from 'react';
import { Panel } from '../../components/Panel';
import { useStore } from '../../store';
import { ReportBuilder } from './ReportBuilder';
import { shortHash } from './model';
import { addRecent } from './recentStore';

/* REPORT CENTRE (built around the report builder): the
 * builder is a component of its own, so the Export Centre hosts the
 * same one in its REPORT tab. No prose: state chips and codes only. */
export function ReportCentrePanel() {
  const st = useStore();
  const runId = st.chain?.run_id ?? null;
  const stamp = runId ? `RUN ${shortHash(runId, 14)}` : 'NO RUN';
  return (
    <Panel id="reports" code="RPT" title="Report centre" updated={stamp} className="rk-panel"
      exportSpec={{ json: () => ({ run_id: runId }) }}>
      <ReportBuilder runId={runId} onExported={addRecent} />
    </Panel>
  );
}
