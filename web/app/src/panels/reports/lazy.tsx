import React, { Suspense } from 'react';
import { MetaChip } from './chips';

/* The export / report / viewer panels pull in the chart library, so the
 * shell loads them on demand: the main chunk stays as it was. */
const Export = React.lazy(() => import('./ExportCentrePanel').then((m) => ({ default: m.ExportCentrePanel })));
const Report = React.lazy(() => import('./ReportCentrePanel').then((m) => ({ default: m.ReportCentrePanel })));
const Viewer = React.lazy(() => import('../dataviewer/DataViewerPanel').then((m) => ({ default: m.DataViewerPanel })));

const Fallback = () => <div className="rk-state" role="status"><MetaChip>Loading</MetaChip></div>;

export const LazyExportCentre = () => <Suspense fallback={<Fallback />}><Export /></Suspense>;
export const LazyReportCentre = () => <Suspense fallback={<Fallback />}><Report /></Suspense>;
export const LazyDataViewer = () => <Suspense fallback={<Fallback />}><Viewer /></Suspense>;
