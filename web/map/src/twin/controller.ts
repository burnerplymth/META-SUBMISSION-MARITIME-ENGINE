// TWIN controller: the non-React heart of the digital-twin renderer.
// Owns the two fleet engines (SIM = scripted/precomputed, LIVE = observed),
// the reusable draw/trail/heat buffers, the filter and selection state, and
// a requestAnimationFrame loop that re-draws ONLY while something is
// moving or dirty (a paused, settled map costs zero per-frame work).
// React components subscribe to its small immutable UI snapshot
// (useSyncExternalStore); the hot path never touches React state, so a
// 60 fps tween does not re-render the map component.

import type { Layer } from "@deck.gl/core";
import type { ThemeName } from "../types";
import type { SimEntity, SimLane, SimWeatherCell, SimState } from "../sim/types";
import {
  FILTER_ALL,
  FleetEngine,
  buildFleetContext,
  type DrawSet,
  type FleetContext,
  type FleetCounts,
  type FleetFilter,
} from "./fleet";
import { lodForZoom, heatCellDeg, LABEL_CAP, type TwinLod } from "./lod";
import { liveColors, paletteFor } from "./palette";
import { buildTwinLayers, type TwinPickInfo } from "./layers";
import { TrailBuffer, fillTrails, type TrailBounds } from "./trails";
import { HeatBuffer, binDensity } from "./density";
import { nightPolygon } from "./terminator";
import {
  liveChip,
  validLiveColumns,
  type LiveChip,
  type LiveFeed,
} from "./live";
import {
  filterActive,
  liveChips,
  routePreview,
  simChips,
  summariseRegion,
  toggleClass,
  toggleFlag,
  type EntityChips,
  type Pt,
  type RegionSummary,
} from "./select";
import { tokens } from "../theme/tokens";
import { prefersReducedMotion } from "../components/motion";

/* ---- minimal structural types for the map / overlay (testable) ------- */

export interface TwinMapLike {
  getZoom(): number;
  getBounds(): { getWest(): number; getEast(): number; getSouth(): number; getNorth(): number };
  project(lngLat: [number, number]): { x: number; y: number };
  unproject(xy: [number, number]): { lng: number; lat: number };
  on(type: string, fn: () => void): unknown;
  off(type: string, fn: () => void): unknown;
  getCanvas(): { style: { cursor: string } };
}
export interface TwinOverlayLike {
  setProps(p: Record<string, unknown>): void;
}

export interface TwinSimInput {
  runId: string;
  lanes: ReadonlyArray<SimLane>;
  state: SimState;
  seaOn: boolean;
  airOn: boolean;
  /** entity id selected by the host (undefined = map-owned selection) */
  hostSelectedId?: string | null;
}

export type SelectMode = "off" | "box" | "lasso";

export interface HoverState {
  src: "sim" | "live";
  chips: EntityChips;
  x: number;
  y: number;
}
export interface SelectedState {
  src: "sim" | "live";
  id: string;
  chips: EntityChips;
}
export interface RegionState {
  /** the drawn shape, as lon/lat vertices (re-projected on move) */
  lonlat: [number, number][];
  summary: Omit<RegionSummary, "slots">;
}

export interface TwinUi {
  filter: FleetFilter;
  filterOn: boolean;
  counts: FleetCounts | null;
  simulated: { shown: number; sea: number; air: number } | null;
  live: LiveChip | null;
  hover: HoverState | null;
  selected: SelectedState | null;
  region: RegionState | null;
  selectMode: SelectMode;
  night: boolean;
  lod: TwinLod["level"];
}

export interface TwinPerf {
  draws: number;
  /** JS ms per drawn frame (frame fill + layer build + setProps), ring */
  jsMs: Float32Array;
  ingestMs: Float32Array;
  ingests: number;
  lastTrailSegs: number;
  lastSeaN: number;
  lastAirN: number;
  lastLiveN: number;
}

type Env = {
  raf: (fn: () => void) => number;
  caf: (id: number) => void;
  now: () => number;
};
const defaultEnv = (): Env => ({
  raf: (fn) => requestAnimationFrame(fn),
  caf: (id) => cancelAnimationFrame(id),
  now: () => performance.now(),
});

const RING = 512;

export class TwinController {
  private env: Env;
  private map: TwinMapLike | null = null;
  private overlay: TwinOverlayLike | null = null;
  private rafId = 0;
  private listeners = new Set<() => void>();
  private base: Layer[] = [];
  private theme: ThemeName = "dark";
  private dirty = true;
  private reduceMotion = false;
  private lastDraw = -1;
  private lastLod: TwinLod["level"] = "dots";
  private frameNo = 0;
  private nightKey = -1;
  private nightPoly: [number, number][] | null = null;

  simEngine: FleetEngine | null = null;
  liveEngine: FleetEngine | null = null;
  private simRunId = "";
  private simCtx: FleetContext | null = null;
  private simStateRef: SimState | null = null;
  private liveRef: unknown = null;
  private liveLabel: "LIVE" | "SNAPSHOT" = "LIVE";
  private simVisible = { sea: true, air: true };
  private hostSelected: string | null | undefined = undefined;
  private internalSelected: string | null = null;
  private onPick: ((id: string | null) => void) | null = null;

  private trails = new TrailBuffer();
  private heat = new HeatBuffer();
  private regionPoly: Pt[] | null = null;

  ui: TwinUi = {
    filter: FILTER_ALL,
    filterOn: false,
    counts: null,
    simulated: null,
    live: null,
    hover: null,
    selected: null,
    region: null,
    selectMode: "off",
    night: false,
    lod: "dots",
  };
  perf: TwinPerf = {
    draws: 0,
    jsMs: new Float32Array(RING),
    ingestMs: new Float32Array(RING),
    ingests: 0,
    lastTrailSegs: 0,
    lastSeaN: 0,
    lastAirN: 0,
    lastLiveN: 0,
  };

  constructor(env?: Partial<Env>) {
    this.env = { ...defaultEnv(), ...env };
    const g = globalThis as unknown as { __twinDebug?: boolean; __twin?: TwinController };
    if (g.__twinDebug) g.__twin = this;
  }

  /* ---- store protocol (useSyncExternalStore) ---- */
  subscribe = (fn: () => void): (() => void) => {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  };
  getSnapshot = (): TwinUi => this.ui;
  private patch(p: Partial<TwinUi>): void {
    this.ui = { ...this.ui, ...p };
    for (const l of this.listeners) l();
  }

  /* ---- lifecycle ---- */
  attach(map: TwinMapLike, overlay: TwinOverlayLike): void {
    this.detach();
    this.map = map;
    this.overlay = overlay;
    this.reduceMotion = prefersReducedMotion();
    if (this.simEngine) this.simEngine.noTween = this.reduceMotion;
    if (this.liveEngine) this.liveEngine.noTween = this.reduceMotion;
    map.on("move", this.markDirty);
    map.on("resize", this.markDirty);
    overlay.setProps({
      /* 1-2 px dots need a forgiving pick target */
      pickingRadius: 5,
      onClick: (info: { picked?: boolean }) => {
        if (!info.picked) this.deselect();
      },
    });
    this.dirty = true;
    this.rafId = this.env.raf(this.tick);
  }

  detach(): void {
    if (this.rafId) this.env.caf(this.rafId);
    this.rafId = 0;
    if (this.map) {
      this.map.off("move", this.markDirty);
      this.map.off("resize", this.markDirty);
    }
    this.map = null;
    this.overlay = null;
  }

  markDirty = (): void => {
    this.dirty = true;
  };

  /* ---- inputs ---- */
  setBase(layers: Layer[]): void {
    this.base = layers;
    this.dirty = true;
    // no fleet to draw and the loop is idle: push straight through
    if (this.overlay && !this.simEngine && !this.liveEngine) this.overlay.setProps({ layers });
  }

  setTheme(theme: ThemeName): void {
    if (theme !== this.theme) {
      this.theme = theme;
      this.dirty = true;
      // LIVE marks carry a theme-derived colour: re-ingest the feed
      const feed = this.liveRef as LiveFeed | null | undefined;
      if (feed && this.liveEngine) {
        this.liveRef = null;
        this.setLive(feed);
      }
    }
  }

  setOnPick(fn: ((id: string | null) => void) | null): void {
    this.onPick = fn;
  }

  setSim(input: TwinSimInput | null): void {
    if (!input) {
      if (this.simEngine) {
        this.simEngine = null;
        this.simCtx = null;
        this.simRunId = "";
        this.simStateRef = null;
        this.patch({ counts: null, simulated: null });
        this.dirty = true;
      }
      return;
    }
    this.simVisible = { sea: input.seaOn, air: input.airOn };
    this.hostSelected = input.hostSelectedId;
    if (!this.simEngine || this.simRunId !== input.runId) {
      this.simCtx = buildFleetContext(input.lanes);
      this.simEngine = new FleetEngine("sim", this.simCtx);
      this.simEngine.noTween = this.reduceMotion;
      this.simRunId = input.runId;
      this.simStateRef = null;
    }
    if (this.simStateRef !== input.state) {
      this.simStateRef = input.state;
      const t0 = this.env.now();
      this.simEngine.ingestSim(
        input.state.entities as SimEntity[],
        t0,
        input.state.weather_cells as SimWeatherCell[] | undefined,
      );
      this.recordIngest(this.env.now() - t0);
      const c = this.simEngine.counts;
      this.patch({
        counts: { ...c, byFlag: c.byFlag.slice(), byClass: c.byClass.slice() },
        simulated: { shown: c.total, sea: c.sea, air: c.air },
      });
    }
    this.syncSelection();
    this.dirty = true;
  }

  setLive(feed: LiveFeed | null | undefined): void {
    if (feed === this.liveRef) return;
    this.liveRef = feed;
    const chip = liveChip(feed);
    this.liveLabel = chip?.label === "SNAPSHOT" ? "SNAPSHOT" : "LIVE";
    if (feed && chip?.visible && validLiveColumns(feed.columns)) {
      if (!this.liveEngine) {
        this.liveEngine = new FleetEngine("live");
        this.liveEngine.noTween = this.reduceMotion;
      }
      const t0 = this.env.now();
      const lc = liveColors(this.theme);
      this.liveEngine.ingestLive(feed.columns as never, t0, lc.core);
      this.recordIngest(this.env.now() - t0);
    } else {
      // NOT SERVED / DOWN / empty: the layer is hidden, never faked
      this.liveEngine = null;
    }
    this.patch({ live: chip });
    this.dirty = true;
  }

  setNight(on: boolean): void {
    if (on !== this.ui.night) {
      this.patch({ night: on });
      this.dirty = true;
    }
  }

  /* ---- filter / selection UI ---- */
  setFilter(f: FleetFilter): void {
    this.patch({ filter: f, filterOn: filterActive(f) });
    this.dirty = true;
  }
  toggleFlag(bit: number): void {
    this.setFilter(toggleFlag(this.ui.filter, bit));
  }
  toggleClass(cls: number): void {
    this.setFilter(toggleClass(this.ui.filter, cls));
  }
  clearFilter(): void {
    this.setFilter(FILTER_ALL);
  }

  setSelectMode(m: SelectMode): void {
    this.patch({ selectMode: this.ui.selectMode === m ? "off" : m });
  }

  /** Finish a box/lasso drag. `px` is the shape in container pixels. */
  finishRegion(px: Pt[]): void {
    const eng = this.simEngine;
    const map = this.map;
    if (!eng || !map || px.length < 3) {
      this.patch({ selectMode: "off" });
      return;
    }
    const now = this.env.now();
    const filter = this.effectiveFilter();
    const summary = summariseRegion(
      eng,
      now,
      filter,
      (lon, lat) => {
        const p = map.project([lon, lat]);
        return [p.x, p.y] as Pt;
      },
      px,
    );
    const lonlat = px.map((p) => {
      const ll = map.unproject([p[0], p[1]]);
      return [ll.lng, ll.lat] as [number, number];
    });
    const { slots: _slots, ...rest } = summary;
    void _slots;
    this.regionPoly = px;
    this.patch({ region: { lonlat, summary: rest }, selectMode: "off" });
  }

  clearRegion(): void {
    this.regionPoly = null;
    this.patch({ region: null });
  }

  deselect(): void {
    if (this.hostSelected === undefined) this.internalSelected = null;
    if (this.ui.selected) {
      this.patch({ selected: null });
      this.dirty = true;
    }
    this.onPick?.(null);
  }

  private selectedId(): string | null {
    return this.hostSelected !== undefined ? this.hostSelected : this.internalSelected;
  }

  private syncSelection(): void {
    const id = this.selectedId();
    const eng = this.simEngine;
    if (!id || !eng) {
      if (this.ui.selected && this.ui.selected.src === "sim") this.patch({ selected: null });
      return;
    }
    const slot = eng.slotOfId(id);
    const chips = slot >= 0 ? simChips(eng, slot) : null;
    if (!chips) {
      if (this.ui.selected) this.patch({ selected: null });
      return;
    }
    const cur = this.ui.selected;
    if (!cur || cur.id !== id || cur.chips.chips.join() !== chips.chips.join()) {
      this.patch({ selected: { src: "sim", id, chips } });
    }
  }

  private effectiveFilter(): FleetFilter {
    return { ...this.ui.filter, sea: this.ui.filter.sea && this.simVisible.sea, air: this.ui.filter.air && this.simVisible.air };
  }

  /* ---- deck pick callbacks ---- */
  private onHover = (src: "sim" | "live", set: DrawSet, info: TwinPickInfo | null): void => {
    const canvas = this.map?.getCanvas();
    if (!info) {
      if (this.ui.hover) {
        this.patch({ hover: null });
        if (canvas) canvas.style.cursor = "";
      }
      return;
    }
    const eng = src === "sim" ? this.simEngine : this.liveEngine;
    if (!eng) return;
    const slot = set.slot[info.index];
    const chips = src === "sim" ? simChips(eng, slot) : liveChips(eng, slot, this.liveLabel);
    if (!chips) return;
    const h = this.ui.hover;
    if (h && h.chips.id === chips.id && Math.abs(h.x - info.x) < 3 && Math.abs(h.y - info.y) < 3) return;
    this.patch({ hover: { src, chips, x: info.x, y: info.y } });
    if (canvas) canvas.style.cursor = "pointer";
  };

  private onClick = (src: "sim" | "live", set: DrawSet, info: TwinPickInfo | null): void => {
    if (!info) return;
    const eng = src === "sim" ? this.simEngine : this.liveEngine;
    if (!eng) return;
    const slot = set.slot[info.index];
    const id = eng.idOf(slot);
    if (!id) return;
    if (src === "sim") {
      if (this.hostSelected === undefined) this.internalSelected = id;
      const chips = simChips(eng, slot);
      if (chips) this.patch({ selected: { src, id, chips } });
      this.onPick?.(id);
    } else {
      const chips = liveChips(eng, slot, this.liveLabel);
      if (chips) this.patch({ selected: { src, id, chips } });
    }
    this.dirty = true;
  };

  /* ---- the loop ---- */
  private tick = (): void => {
    this.rafId = this.env.raf(this.tick);
    this.draw(this.env.now(), false);
  };

  /** Exposed for forced redraws. Returns true if it drew. */
  draw(now: number, force: boolean): boolean {
    const map = this.map;
    const overlay = this.overlay;
    if (!map || !overlay) return false;
    const sim = this.simEngine;
    const live = this.liveEngine;
    const zoom = map.getZoom();
    const lod = lodForZoom(zoom);
    const animating =
      (sim && (sim.moving(now) || sim.moving(this.lastDraw))) ||
      (live && (live.moving(now) || live.moving(this.lastDraw)));
    if (!force && !animating && !this.dirty && lod.level === this.lastLod) return false;
    this.dirty = false;
    this.lastLod = lod.level;
    if (this.ui.lod !== lod.level) this.patch({ lod: lod.level });
    this.lastDraw = now;
    const t0 = this.env.now();

    const pal = paletteFor(this.theme);
    const filter = this.effectiveFilter();
    if (sim) sim.frame(now, filter, pal);
    if (live) live.frame(now, FILTER_ALL, null);

    const b = map.getBounds();
    const bounds: TrailBounds = { west: b.getWest() - 5, east: b.getEast() + 5, south: b.getSouth() - 5, north: b.getNorth() + 5 };

    let trails: TrailBuffer | null = null;
    if (lod.trails && sim) {
      this.perf.lastTrailSegs = fillTrails(sim, [sim.sea, sim.air], now, this.trails, bounds);
      trails = this.trails;
    }
    let heat: HeatBuffer | null = null;
    if (lod.heat && sim) {
      const c = tokens[this.theme];
      const rgb = hex3(c.vessel);
      binDensity([sim.sea, sim.air], heatCellDeg(map.getZoom()), this.heat, rgb);
      heat = this.heat;
    }

    // selection: ring + full route preview
    let route = null;
    let selectedPos: [number, number] | null = null;
    const sel = this.ui.selected;
    if (sel && sel.src === "sim" && sim) {
      const slot = sim.slotOfId(sel.id);
      if (slot >= 0) {
        const tmp = this.tmp;
        sim.evalSlot(slot, now, tmp);
        selectedPos = [tmp[0], tmp[1]];
        route = routePreview(sim, slot, now);
      }
    } else if (sel && sel.src === "live" && live) {
      const slot = live.slotOfId(sel.id);
      if (slot >= 0) {
        live.evalSlot(slot, now, this.tmp);
        selectedPos = [this.tmp[0], this.tmp[1]];
      }
    }

    // SHIP-band labels: visible entities only, capped
    let labels: { position: [number, number]; text: string }[] | null = null;
    if (lod.labels && sim) labels = this.collectLabels(sim, bounds);

    // optional day/night shading from the sim clock
    let night: [number, number][] | null = null;
    if (this.ui.night) {
      const ms = this.simStateRef ? Date.parse(this.simStateRef.sim_time) : Date.now();
      const key = Math.floor(ms / 60000);
      if (key !== this.nightKey || !this.nightPoly) {
        this.nightKey = key;
        this.nightPoly = nightPolygon(ms);
      }
      night = this.nightPoly;
    }

    const twinLayers = buildTwinLayers({
      theme: this.theme,
      zoom,
      lod,
      frameNo: ++this.frameNo,
      simSea: sim && sim.sea.n ? sim.sea : null,
      simAir: sim && sim.air.n ? sim.air : null,
      liveSea: live && live.sea.n ? live.sea : null,
      liveAir: live && live.air.n ? live.air : null,
      trails,
      heat,
      night,
      route,
      selectedPos,
      labels,
      onHover: this.onHover,
      onClick: this.onClick,
    });
    overlay.setProps({ layers: [...this.base, ...twinLayers] });

    const p = this.perf;
    p.jsMs[p.draws % RING] = this.env.now() - t0;
    p.draws++;
    p.lastSeaN = sim?.sea.n ?? 0;
    p.lastAirN = sim?.air.n ?? 0;
    p.lastLiveN = (live?.sea.n ?? 0) + (live?.air.n ?? 0);
    return true;
  }

  private tmp = new Float64Array(3);

  /** SHIP-band labels: entities inside the padded viewport, ONE label per
   *  screen cell (a queue stacked on one place reads "NAME +N", not as an
   *  illegible pile of overprinted text), capped at LABEL_CAP. */
  private collectLabels(sim: FleetEngine, b: TrailBounds): { position: [number, number]; text: string }[] {
    const map = this.map;
    const cells = new Map<number, { position: [number, number]; text: string; n: number }>();
    for (const set of [sim.sea, sim.air]) {
      for (let k = 0; k < set.n; k++) {
        const lon = set.pos[k * 2];
        const lat = set.pos[k * 2 + 1];
        if (lat < b.south || lat > b.north || lon < b.west || lon > b.east) continue;
        const e = sim.simEntity(set.slot[k]);
        if (!e) continue;
        const p = map ? map.project([lon, lat]) : { x: k, y: 0 };
        const key = Math.floor(p.x / 72) * 4096 + Math.floor(p.y / 14);
        const hit = cells.get(key);
        if (hit) hit.n++;
        else if (cells.size < LABEL_CAP) cells.set(key, { position: [lon, lat], text: e.vessel_name ?? e.tail ?? e.id, n: 1 });
      }
    }
    return [...cells.values()].map((c) => ({ position: c.position, text: c.n > 1 ? `${c.text} +${c.n - 1}` : c.text }));
  }

  private recordIngest(ms: number): void {
    const p = this.perf;
    p.ingestMs[p.ingests % RING] = ms;
    p.ingests++;
  }

  /** Summary of recent per-frame JS cost (ms) for the standalone page. */
  perfSummary(): { draws: number; jsMsMean: number; jsMsP95: number; jsMsMax: number; ingestMsMean: number; ingests: number } {
    const p = this.perf;
    const n = Math.min(p.draws, RING);
    const a = Array.from(p.jsMs.subarray(0, n)).sort((x, y) => x - y);
    const m = Math.min(p.ingests, RING);
    const ig = Array.from(p.ingestMs.subarray(0, m));
    return {
      draws: p.draws,
      jsMsMean: n ? a.reduce((s, v) => s + v, 0) / n : 0,
      jsMsP95: n ? a[Math.min(n - 1, Math.floor(n * 0.95))] : 0,
      jsMsMax: n ? a[n - 1] : 0,
      ingestMsMean: m ? ig.reduce((s, v) => s + v, 0) / m : 0,
      ingests: p.ingests,
    };
  }
}

function hex3(h: string): [number, number, number] {
  const s = h.replace("#", "");
  return [parseInt(s.slice(0, 2), 16), parseInt(s.slice(2, 4), 16), parseInt(s.slice(4, 6), 16)];
}
