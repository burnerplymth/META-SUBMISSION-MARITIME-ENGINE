import React, { Suspense } from 'react';
import { MetaChip } from '../reports/chips';

/* The watchlists window loads on demand, as the shipments window does: the main chunk stays small. */
const Watch = React.lazy(() => import('./WatchPanel').then((m) => ({ default: m.WatchPanel })));

const Fallback = () => <div className="rk-state" role="status"><MetaChip>Loading</MetaChip></div>;

export const LazyWatch = () => <Suspense fallback={<Fallback />}><Watch /></Suspense>;
