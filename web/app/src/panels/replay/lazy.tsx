import React, { Suspense } from 'react';
import { MetaChip } from '../reports/chips';

/* The Replay window pulls in the chart drawing code and the export machinery, so the shell loads it on demand and
 * the main chunk stays as it was. */
const Replay = React.lazy(() => import('./ReplayPanel').then((m) => ({ default: m.ReplayPanel })));

const Fallback = () => <div className="rk-state" role="status"><MetaChip>Loading</MetaChip></div>;

export const LazyReplay = ({ as }: { as?: 'proof' }) => <Suspense fallback={<Fallback />}><Replay as={as} /></Suspense>;
