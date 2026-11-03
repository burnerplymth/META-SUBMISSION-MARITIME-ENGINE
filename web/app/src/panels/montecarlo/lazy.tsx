import React, { Suspense } from 'react';
import { MetaChip } from '../reports/chips';

/* The Monte Carlo window pulls in the chart drawing code and the workbook writer, so the shell loads it on demand. */
const Window = React.lazy(() => import('./MonteCarloPanel').then((m) => ({ default: m.MonteCarloPanel })));

const Fallback = () => <div className="rk-state" role="status"><MetaChip>Loading</MetaChip></div>;

export const LazyMonteCarlo = () => <Suspense fallback={<Fallback />}><Window /></Suspense>;
