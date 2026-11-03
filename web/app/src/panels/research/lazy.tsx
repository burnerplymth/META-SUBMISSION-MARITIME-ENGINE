import React, { Suspense } from 'react';
import { MetaChip } from '../reports/chips';

/* The Research window pulls in the chart kit and its recorded copies, so the shell loads it on demand. */
const Research = React.lazy(() => import('./ResearchPanel').then((m) => ({ default: m.ResearchPanel })));
const Fallback = () => <div className="rk-state" role="status"><MetaChip>Loading</MetaChip></div>;
export const LazyResearch = () => <Suspense fallback={<Fallback />}><Research /></Suspense>;
