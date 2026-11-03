import React, { useMemo } from 'react';
import { VNodes } from './VNodeView';
import { buildSpark } from './spark';
import './charts.css';

/** A fixed-size sparkline for a list row. Decorative: the row carries
 *  the numbers; the svg is hidden from assistive tech. */
export function SparkView({ values, kind = 'line', width = 96, height = 24, label }: {
  values: Array<number | null>; kind?: 'line' | 'bars'; width?: number; height?: number; label?: string;
}) {
  const m = useMemo(() => buildSpark({ type: 'spark', id: 'sp', title: label ?? 'spark', provenance: { chip: 'NOTE' }, values, kind }, { width, height }), [values, kind, width, height, label]);
  return (
    <svg className="cx-spark" width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden="true" focusable="false">
      <VNodes nodes={m.nodes} />
    </svg>
  );
}
