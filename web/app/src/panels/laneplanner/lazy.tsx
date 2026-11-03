import React, { Suspense } from 'react';
import { MetaChip } from '../reports/chips';

/* The lane planner pulls in the chart drawing code and the land outline, so
 * the shell loads it on demand: the main chunk stays as it was. */
const Planner = React.lazy(() => import('./LanePlannerPanel').then((m) => ({ default: m.LanePlannerPanel })));

const Fallback = () => <div className="rk-state" role="status"><MetaChip>Loading</MetaChip></div>;

export const LazyLanePlanner = () => <Suspense fallback={<Fallback />}><Planner /></Suspense>;
