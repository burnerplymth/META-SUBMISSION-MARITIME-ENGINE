import React, { Suspense } from 'react';
import { MetaChip } from '../panels/reports/chips';

/* The Export window pulls in the chart kit and the pack writer, so the shell
 * loads it on first use and the main chunk stays as it was. */
const Win = React.lazy(() => import('./ExportWindow').then((m) => ({ default: m.ExportWindow })));

export const LazyExportWindow = () => (
  <Suspense fallback={<div className="rk-state" role="status"><MetaChip>Loading</MetaChip></div>}><Win /></Suspense>
);
