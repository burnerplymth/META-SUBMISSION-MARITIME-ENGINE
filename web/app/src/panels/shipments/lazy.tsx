import React, { Suspense } from 'react';
import { MetaChip } from '../reports/chips';

/* The shipments window loads on demand, as the lane planner does: the main chunk stays small. */
const Shipments = React.lazy(() => import('./ShipmentsPanel').then((m) => ({ default: m.ShipmentsPanel })));

const Fallback = () => <div className="rk-state" role="status"><MetaChip>Loading</MetaChip></div>;

export const LazyShipments = () => <Suspense fallback={<Fallback />}><Shipments /></Suspense>;
