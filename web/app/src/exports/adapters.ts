/* Registrations for windows that already exist, written as hooks so the panels
 * change by one line each. Each hook registers entries on
 * top of what `Panel` lifts from the panel's exportSpec. */
import { useMemo } from 'react';
import { nodeName } from '../seed';
import type { Chain } from '../types';
import { planTitle } from '../panels/laneplanner/logic';
import { frameChart, provenanceOf } from '../panels/laneplanner/exportChart';
import type { PlanDoc } from '../panels/laneplanner/types';
import type { Overlay, SatPlace } from '../panels/satwatch2/model';
import { placeRows } from '../panels/satwatch2/model';
import { staticLookup } from '../charts/serialize';
import { toSvgString } from '../charts/vnode';
import { svgToPng } from '../charts/exportDom';
import type { ExportTable, Provenance } from '../charts/types';
import type { VNode } from '../charts/vnode';
import type { ChartSpec } from '../charts/types';
import { chartFile, mkFile, objectsToTable, provOfChart, rowsFile, textFile, type ItemMeta } from './builders';
import { useExportContext } from './context';
import { count, dataSha256, headerEntries, headerObject, shownEntries } from './header';
import { useExportable } from './registry';
import { DEFAULT_OPTIONS, ExportError, type ExportableDef, type ExportFile, type ExportFormat, type ExportOptions } from './types';
import { tableToCsv } from '../charts/serialize';

/* ── drawn charts (vnode trees) ─────────────────────────────────────── */
export interface DrawnChart { nodes: VNode[]; width: number; height: number; table: ExportTable }

/** Export entry for a chart the panel draws itself as a vnode tree (the lane planner's two drawings). */
export function drawnChartDef(o: {
  id: string; windowId: string; title: string; prov: Provenance; chart: () => DrawnChart | null; frame?: typeof frameChart;
}): ExportableDef {
  const frame = o.frame ?? frameChart;
  const base = provOfChart(o.prov);
  const meta: ItemMeta = { id: o.id, windowId: o.windowId, title: o.title, kind: 'chart', prov: base };
  const draw = (opt: ExportOptions) => {
    const c = o.chart();
    if (!c) throw new ExportError('empty');
    const framed = frame(o.title, c, o.prov);
    const svg = toSvgString(framed.nodes, framed, staticLookup(opt.theme), { role: 'img', 'aria-label': o.title });
    return { c, framed, svg };
  };
  return {
    id: o.id, windowId: o.windowId, title: o.title, kind: 'chart', formats: ['svg', 'png', 'csv', 'json'],
    describe: () => { const c = o.chart(); return { summary: c ? `${count(c.table.rows.length, 'plotted row')}` : 'No chart', rows: c?.table.rows.length, columns: c?.table.columns, provenance: base, state: c ? 'ready' : 'empty' }; },
    preview: () => { try { return { kind: 'svg', svg: draw(DEFAULT_OPTIONS).svg }; } catch { return null; } },
    build: async (f: ExportFormat, opt: ExportOptions): Promise<ExportFile> => {
      const { c, framed, svg } = draw(opt);
      if (f === 'svg') return mkFile(meta, 'svg', opt, svg);
      if (f === 'png') return mkFile(meta, 'png', opt, await svgToPng(svg, framed.width, framed.height, 2));
      const entries = headerEntries({ title: o.title, kind: 'chart', windowId: o.windowId, prov: base, rows: c.table.rows.length, columns: c.table.columns.length, dataHash: await dataSha256(c.table.columns, c.table.rows), note: o.prov.scope ?? null });
      if (f === 'csv') return mkFile(meta, 'csv', opt, `${shownEntries(opt.csvHeader, entries).map(([k, v]) => `# ${k}: ${v}`).join('\n') + '\n'}${tableToCsv(c.table, o.prov)}`);
      if (f === 'json') return mkFile(meta, 'json', opt, `${JSON.stringify({ export: headerObject(shownEntries(opt.provenanceSheet, entries)), label: base.label, columns: c.table.columns, rows: c.table.rows }, null, 2)}\n`);
      throw new ExportError('unsupported');
    },
  };
}

/* ── lane planner: the range chart and the voyage timeline ──────────── */
export function useLaneExportables(doc: PlanDoc | null, range: DrawnChart | null, timeline: DrawnChart | null): void {
  const prov = useMemo(() => (doc ? provenanceOf(doc) : null), [doc]);
  const title = doc ? planTitle(doc) : '';
  useExportable(useMemo(() => (doc && range && prov ? drawnChartDef({ id: 'laneplanner:range', windowId: 'laneplanner', title: `${title} transit time range`, prov, chart: () => range }) : null), [doc, range, prov, title]));
  useExportable(useMemo(() => (doc && timeline && prov ? drawnChartDef({ id: 'laneplanner:timeline', windowId: 'laneplanner', title: `${title} voyage`, prov, chart: () => timeline }) : null), [doc, timeline, prov, title]));
}

/* ── causal chain: the cascade as a chart ───────────────────────────── */
export function chainCascadeSpec(chain: Chain, asOf: string | null): ChartSpec | null {
  if (!chain.events.length) return null;
  const events = chain.events.map((e) => ({
    seq: e.seq, parentSeq: e.parent_id, at: Date.parse(e.at), place: nodeName(e.node_id), placeId: e.node_id, channel: e.channel,
    mechanism: e.mechanism ?? null, delayD: e.delay_d, magnitude: e.magnitude,
  })).filter((e) => Number.isFinite(e.at));
  if (!events.length) return null;
  return {
    type: 'cascade', id: `chain-${chain.run_id}`, title: 'Causal chain timeline', yLabel: 'Causal chain',
    provenance: { chip: 'MODELLED', source: 'Stored run record (causal chain)', asOf, runId: chain.run_id }, events,
  };
}

export function useChainExportables(chain: Chain | null): void {
  const ctx = useExportContext();
  const spec = useMemo(() => (chain ? chainCascadeSpec(chain, ctx.asOf) : null), [chain, ctx.asOf]);
  useExportable(useMemo((): ExportableDef | null => {
    if (!chain || !spec) return null;
    const prov = { ...provOfChart(spec.provenance), hash: chain.stream_hash };
    const meta: ItemMeta = { id: 'chain:cascade', windowId: 'chain', title: spec.title, kind: 'chart', prov };
    return {
      id: meta.id, windowId: 'chain', title: spec.title, kind: 'chart', formats: ['svg', 'png', 'csv', 'json', 'html'],
      describe: () => ({ summary: `${count(chain.events.length, 'event')} by arrival time`, rows: chain.events.length, provenance: prov }),
      build: (f, o) => chartFile(meta, f, spec, o, { width: 960, height: 360 }),
    };
  }, [chain, spec]));
  useExportable(useMemo((): ExportableDef | null => {
    if (!chain || chain.edges.length === 0) return null;
    const prov = { label: 'Modelled' as const, source: 'Stored run record (causal chain)', run_id: chain.run_id, hash: chain.stream_hash, as_of: ctx.asOf };
    const meta: ItemMeta = { id: 'chain:legs', windowId: 'chain', title: 'Chain legs', kind: 'table', prov };
    const cols = ['from_step', 'to_step', 'from_place', 'to_place', 'channel'];
    const rows = () => chain.edges.map((e) => [e.from_seq, e.to_seq, nodeName(e.from_node), nodeName(e.to_node), e.channel]);
    return {
      id: meta.id, windowId: 'chain', title: meta.title, kind: 'table', formats: ['csv', 'xlsx', 'json', 'html'],
      describe: () => ({ summary: `${count(chain.edges.length, 'leg')}, ${count(cols.length, 'column')}`, rows: chain.edges.length, columns: cols, provenance: prov }),
      preview: () => ({ kind: 'table', columns: cols, rows: rows().slice(0, 8), total: chain.edges.length }),
      build: (f, o) => rowsFile(meta, f, cols, rows(), o),
    };
  }, [chain, ctx.asOf]));
}

/* ── satellite desk: detections and anchorages ──────────────────────── */
const DET_COLS = ['id', 'role', 'scene_date', 'detector', 'zone', 'score', 'localization', 'x', 'y', 'width', 'height', 'coord_space'];

export function detectionRows(ov: Overlay): Array<Array<string | number | null>> {
  return ov.detections.map((d) => {
    const b = d.bbox && d.bbox.length >= 4 ? d.bbox : null;
    const p = d.point && d.point.length >= 2 ? d.point : null;
    return [d.id, d.role, d.scene_date ?? null, d.detector_id ?? null, d.zone ?? null, d.score, d.localization,
      b ? b[0] : p ? p[0] : null, b ? b[1] : p ? p[1] : null, b ? b[2] : null, b ? b[3] : null, d.coord_space ?? ov.coord_space];
  });
}

/** Anchorage areas as GeoJSON polygons ([west, south, east, north] core boxes). */
export function anchorageGeoJson(places: readonly SatPlace[]): { type: 'FeatureCollection'; features: unknown[] } {
  const rows = new Map(placeRows(places).map((r) => [r.id, r]));
  return {
    type: 'FeatureCollection',
    features: places.filter((p) => p.core_bbox && p.core_bbox.length === 4).map((p) => {
      const [w, s, e, n] = p.core_bbox as number[];
      const r = rows.get(p.node_id);
      return {
        type: 'Feature', id: p.node_id,
        properties: { place_id: p.node_id, name: p.name, placement: p.placement, scan: r?.scan ?? null, detection: r?.detection ?? null, imagery_now: r?.now ?? null, imagery_baseline: r?.baseline ?? null },
        geometry: { type: 'Polygon', coordinates: [[[w, s], [e, s], [e, n], [w, n], [w, s]]] },
      };
    }),
  };
}

export function useSatDeskExportables(places: readonly SatPlace[] | null, overlay: Overlay | null): void {
  const ctx = useExportContext();
  useExportable(useMemo((): ExportableDef | null => {
    if (!overlay || !overlay.served) return null;
    const prov = { label: 'Modelled' as const, source: `Satellite detector, ${overlay.provider_label ?? 'Sentinel-2'}, pixel coordinates of the crop`, as_of: overlay.imagery_dates?.now ?? ctx.asOf };
    const meta: ItemMeta = { id: 'satdesk:detections', windowId: 'satdesk', title: `Detections, ${overlay.name}`, kind: 'table', prov };
    return {
      id: meta.id, windowId: 'satdesk', title: meta.title, kind: 'table', formats: ['csv', 'xlsx', 'json', 'html'],
      describe: () => ({ summary: `${count(overlay.detections.length, 'detection')}, pixel coordinates`, rows: overlay.detections.length, columns: DET_COLS, provenance: prov, state: overlay.detections.length ? 'ready' : 'empty' }),
      preview: () => ({ kind: 'table', columns: DET_COLS, rows: detectionRows(overlay).slice(0, 8), total: overlay.detections.length }),
      build: (f, o) => rowsFile(meta, f, DET_COLS, detectionRows(overlay), o, { note: `Imagery now ${overlay.imagery_dates?.now ?? 'not served'}, baseline ${overlay.imagery_dates?.baseline ?? 'not served'}` }),
    };
  }, [overlay, ctx.asOf]));
  useExportable(useMemo((): ExportableDef | null => {
    if (!places || places.length === 0) return null;
    const prov = { label: 'Snapshot' as const, source: 'Satellite desk anchorage areas', as_of: ctx.asOf };
    const meta: ItemMeta = { id: 'satdesk:anchorages', windowId: 'satdesk', title: 'Anchorage areas', kind: 'table', prov };
    const rows = () => placeRows(places).map((r) => ({ place_id: r.id, name: r.name, placement: r.curated ? 'curated' : 'port point', scan: r.scan, detection: r.detection, imagery_now: r.now, imagery_baseline: r.baseline }));
    return {
      id: meta.id, windowId: 'satdesk', title: meta.title, kind: 'table', formats: ['csv', 'xlsx', 'json', 'geojson'],
      describe: () => ({ summary: `${count(places.length, 'area')}`, rows: places.length, provenance: prov }),
      preview: () => { const t = objectsToTable(rows().slice(0, 8)); return { kind: 'table', columns: t.columns, rows: t.rows, total: places.length }; },
      build: async (f, o) => {
        if (f === 'geojson') return textFile(meta, 'geojson', `${JSON.stringify({ ...anchorageGeoJson(places), properties: { label: prov.label, source: prov.source } }, null, 2)}\n`, o);
        const t = objectsToTable(rows());
        return rowsFile(meta, f, t.columns, t.rows, o);
      },
    };
  }, [places, ctx.asOf]));
}
