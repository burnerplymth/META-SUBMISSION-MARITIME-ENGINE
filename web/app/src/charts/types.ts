/* Chart specs (plain data in) and chart models (geometry out).
 * A spec never holds pixels; a model never holds the source rows. */
import type { Domain, LinearScale } from './scale';
import type { VNode } from './vnode';

/** Provenance caption carried by every chart and every export of it. */
export interface Provenance {
  /** SOURCED / SNAPSHOT / MODELLED / SIMULATED / LIVE / NOT SERVED / BACKTEST / LEDGER */
  chip: string;
  source?: string | null;
  asOf?: string | null;
  runId?: string | null;
  /** short state word, e.g. "FIRST 40 ROWS" */
  scope?: string | null;
}

export type XKind = 'time' | 'linear';

export interface Point { x: number; y: number | null; label?: string; id?: string; placeId?: string }
export interface SeriesIn { key: string; label: string; color?: string; points: Point[] }
export interface EventMarker { x: number; label: string; id?: string }

export interface Common {
  id: string;
  title: string;
  subtitle?: string | null;
  provenance: Provenance;
  xLabel?: string | null;
  yLabel?: string | null;
  xUnit?: string | null;
  yUnit?: string | null;
}

export interface LineSpec extends Common {
  type: 'line'; xKind: XKind; series: SeriesIn[]; events?: EventMarker[];
  step?: boolean; zeroBase?: boolean;
}
/** values: a number, null (the row exists, the value is NOT SERVED) or NaN (no row for this pair) */
export interface BarSeries { key: string; label: string; color?: string; values: Array<number | null> }
export interface BarCategory { key: string; label: string; placeId?: string }
export interface BarSpec extends Common {
  type: 'bar'; categories: BarCategory[]; series: BarSeries[];
  mode: 'grouped' | 'stacked'; horizontal?: boolean;
  /** categories coloured by series (a row sits in exactly one series) */
  colorBy?: boolean;
  /** the one class a stack is allowed to add up within (named in the title) */
  scope?: string | null;
}
export interface ScatterSpec extends Common { type: 'scatter'; xKind: XKind; series: SeriesIn[] }
export interface CascadeEvent {
  seq: number; parentSeq: number | null; at: number; place: string; placeId?: string;
  channel: string; mechanism?: string | null; delayD: number | null; magnitude: number | null;
}
export interface CascadeSpec extends Common { type: 'cascade'; events: CascadeEvent[] }
export interface SparkSpec extends Common { type: 'spark'; values: Array<number | null>; kind?: 'line' | 'bars' }
export interface FacetSpec { key: string; label: string; spec: LineSpec | BarSpec | ScatterSpec }
export interface MultiplesSpec extends Common {
  type: 'multiples'; facets: FacetSpec[]; minCellWidth?: number; cellHeight?: number; sharedX?: boolean;
}
export type ChartSpec = LineSpec | BarSpec | ScatterSpec | CascadeSpec | SparkSpec | MultiplesSpec;
export type ChartType = ChartSpec['type'];

export interface BuildOpts {
  width: number;
  /** preferred height; bar / cascade / multiples size themselves */
  height: number;
  /** x zoom window (time / linear charts) */
  domain?: Domain | null;
  /** series keys hidden by the legend */
  hidden?: ReadonlySet<string>;
}

export interface Rect { x: number; y: number; w: number; h: number }

export interface Hit {
  x: number; y: number;
  box?: Rect;
  facet?: string;
  seriesKey: string; seriesLabel: string; color: string;
  title: string;
  rows: Array<{ k: string; v: string }>;
  placeId?: string;
  seq?: number;
  /** data x (ms or number) for crosshair grouping */
  dx?: number;
}

export interface LegendItem { key: string; label: string; color: string }

export interface ExportTable { columns: string[]; rows: Array<Array<string | number | null>> }

export interface ChartModel {
  width: number; height: number;
  plot: Rect;
  nodes: VNode[];
  hits: Hit[];
  hitMode: 'x' | 'xy';
  xScale: LinearScale | null;
  fullDomain: Domain | null;
  domain: Domain | null;
  brushable: boolean;
  legend: LegendItem[];
  table: ExportTable;
}
