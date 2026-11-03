import React, { useCallback, useMemo, useRef, useState } from 'react';
import { ExportMenu } from '../../charts/ExportMenu';
import type { ExportKind, ExportResult, ExportSkin } from '../../charts/exportDom';
import { Status } from '../../ui/Status';
import { Chip } from '../reports/chips';
import { ChartHost, useWidth } from './ChartHost';
import type { McChart } from './charts';
import { runMcExport, type ExportCtx } from './exportChart';
import { MC_LABEL } from './fmt';

/** The mandatory label as a MODELLED chip that opens the text. */
export function LabelChip({ label }: { label?: string }) {
  const [open, setOpen] = useState(false);
  return (
    <span className="mc-labelchip">
      <button type="button" className="rk-chip rk-chip--model mc-labelbtn" aria-expanded={open} aria-label="Modelled, show label" onClick={() => setOpen((o) => !o)}>
        <Status state="model" word="Modelled" />
      </button>
      {open && <span className="mc-labeltext" role="note" data-testid="mc-label">{label || MC_LABEL}</span>}
    </span>
  );
}

export function SourceChip({ recorded }: { recorded: boolean }) {
  return recorded ? <Chip chip="RECORDED" tone="snapshot" /> : <Chip chip="SIMULATED" />;
}

/** One chart with its title, label chip and export menu. `build` must be stable (useCallback) for the width it is given. */
export function ChartCard({ heading, kind, ctx, build, chips, testid, label }: {
  heading: string; kind: string; ctx: ExportCtx; build: (width: number) => McChart; chips?: React.ReactNode; testid?: string; label?: string;
}) {
  const box = useRef<HTMLDivElement>(null);
  const w = useWidth(box);
  const chart = useMemo(() => build(w), [build, w]);
  const onRun = useCallback((k: ExportKind, skin: ExportSkin): Promise<ExportResult> => runMcExport(k, skin, { ctx, title: heading, kind, chart }), [ctx, heading, kind, chart]);
  return (
    <section className="mc-card" aria-label={heading}>
      <div className="mc-sech">
        <span className="mc-st">{heading}</span>
        <LabelChip label={label} />
        {chips}
        <span className="mc-spacer" />
        <ExportMenu onRun={onRun} hasLink={false} />
      </div>
      <div ref={box} className="mc-chartbox"><ChartHost chart={chart} label={heading} testid={testid} /></div>
    </section>
  );
}

/** A card heading for a table, with the label chip. */
export function TableHead({ heading, children }: { heading: string; children?: React.ReactNode }) {
  return <div className="mc-sech"><span className="mc-st">{heading}</span><LabelChip />{children}</div>;
}
