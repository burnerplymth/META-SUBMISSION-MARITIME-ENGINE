import React, { Suspense } from 'react';
import { MetaChip } from '../reports/chips';

/* The builder pulls in the timeline editor and the lane planner's place search,
 * so the shell loads it on demand and the main chunk stays as it was. */
const Builder = React.lazy(() => import('./ScenarioBuilderPanel').then((m) => ({ default: m.ScenarioBuilderPanel })));

const Fallback = () => <div className="rk-state" role="status"><MetaChip>Loading</MetaChip></div>;

export const LazyScenarioBuilder = () => <Suspense fallback={<Fallback />}><Builder /></Suspense>;
