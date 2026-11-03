// TWIN map furniture: filter chips, status chips, hover tooltip,
// selection chip, box / lasso capture. Chips and state labels only, no
// explanatory prose. Everything reads the controller's immutable UI
// snapshot (useSyncExternalStore), so the 60 fps render loop never
// re-renders the map component.

import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import type { ThemeName } from "../types";
import {
  CLASS_LABEL,
  F_DELAYED,
  F_QUEUED,
  F_WX,
  FLAG_BITS,
  FLAG_LABEL,
} from "./fleet";
import { speedRampCss } from "./palette";
import type { Pt } from "./select";
import type { TwinController, TwinMapLike } from "./controller";
import { railWord } from "../theme/railWord";

export function useTwinUi(ctrl: TwinController) {
  return useSyncExternalStore(ctrl.subscribe, ctrl.getSnapshot, ctrl.getSnapshot);
}

const fmt = (n: number): string => n.toLocaleString("en-US");

/* ---- filter chips (top-left stack) ----------------------------------- */

const STATE_CHIPS: Array<{ bit: number; label: string }> = [
  { bit: FLAG_BITS[0], label: FLAG_LABEL[FLAG_BITS[0]] },
  { bit: F_QUEUED, label: FLAG_LABEL[F_QUEUED] },
  { bit: F_DELAYED, label: FLAG_LABEL[F_DELAYED] },
  { bit: F_WX, label: FLAG_LABEL[F_WX] },
];

export function TwinHud({ ctrl, hasSim }: { ctrl: TwinController; hasSim: boolean }) {
  const ui = useTwinUi(ctrl);
  if (!hasSim || !ui.counts) return null;
  const c = ui.counts;
  const flagCount = (bit: number): number => c.byFlag[FLAG_BITS.indexOf(bit as (typeof FLAG_BITS)[number])] ?? 0;
  const classOn = (cls: number): boolean => (ui.filter.classMask & (1 << cls)) !== 0;
  const allClasses = ui.filter.classMask === (1 << CLASS_LABEL.length) - 1;
  return (
    <div className="sm-twin-hud" role="group" aria-label="Fleet filter">
      <div className="sm-twin-row">
        {STATE_CHIPS.map((s) => {
          const n = flagCount(s.bit);
          const pressed = (ui.filter.flagMask & s.bit) !== 0;
          return (
            <button
              key={s.bit}
              className="sm-twin-chip"
              aria-pressed={pressed}
              disabled={n === 0 && !pressed}
              onClick={() => ctrl.toggleFlag(s.bit)}
            >
              {railWord(s.label)} <i>{fmt(n)}</i>
            </button>
          );
        })}
      </div>
      <div className="sm-twin-row">
        {CLASS_LABEL.map((label, cls) => {
          const n = c.byClass[cls] ?? 0;
          if (n === 0) return null;
          return (
            <button
              key={label}
              className="sm-twin-chip"
              aria-pressed={!allClasses && classOn(cls)}
              onClick={() => ctrl.toggleClass(cls)}
            >
              {railWord(label)} <i>{fmt(n)}</i>
            </button>
          );
        })}
      </div>
      <div className="sm-twin-row">
        <button className="sm-twin-chip" aria-pressed={ui.selectMode === "box"} onClick={() => ctrl.setSelectMode("box")}>
          Box
        </button>
        <button className="sm-twin-chip" aria-pressed={ui.selectMode === "lasso"} onClick={() => ctrl.setSelectMode("lasso")}>
          Lasso
        </button>
        <button className="sm-twin-chip" aria-pressed={ui.night} onClick={() => ctrl.setNight(!ui.night)}>
          Night
        </button>
        {ui.filterOn && (
          <button className="sm-twin-chip sm-twin-clear" onClick={() => ctrl.clearFilter()}>
            Clear
          </button>
        )}
      </div>
      {ui.region && (
        <div className="sm-twin-row">
          <span className="sm-chip sm-chip-sim">
            <b>Region {fmt(ui.region.summary.total)}</b>
            <span>
              Sea {fmt(ui.region.summary.sea)} · Air {fmt(ui.region.summary.air)} · Queued{" "}
              {fmt(ui.region.summary.byFlag[FLAG_BITS.indexOf(F_QUEUED)])} · Delayed{" "}
              {fmt(ui.region.summary.byFlag[FLAG_BITS.indexOf(F_DELAYED)])} · Weather-held{" "}
              {fmt(ui.region.summary.byFlag[FLAG_BITS.indexOf(F_WX)])}
            </span>
          </span>
          <button className="sm-twin-chip sm-twin-clear" onClick={() => ctrl.clearRegion()}>
            Clear
          </button>
        </div>
      )}
    </div>
  );
}

/* ---- status chips (bottom-left stack) -------------------------------- */

export function TwinChips({
  ctrl,
  theme,
  runEntityCount,
  groundOn,
  hasSim,
}: {
  ctrl: TwinController;
  theme: ThemeName;
  runEntityCount: number | null;
  groundOn: boolean;
  hasSim: boolean;
}) {
  const ui = useTwinUi(ctrl);
  const ramp = useMemo(() => speedRampCss(theme, 0), [theme]);
  return (
    <>
      {hasSim && (
        <span className="sm-chip sm-chip-sim">
          <b>SIMULATED</b> scripted
          {ui.simulated ? ` · ${fmt(ui.simulated.shown)}${runEntityCount ? ` / ${fmt(runEntityCount)}` : ""}` : ""}
        </span>
      )}
      {hasSim && (
        <span className="sm-chip" role="group" aria-label="Speed colour">
          Speed
          <span className="sm-twin-ramp" aria-hidden>
            {ramp.map((c, i) => (
              <span key={i} style={{ background: c }} />
            ))}
          </span>
        </span>
      )}
      {hasSim && groundOn && (
        <span className="sm-chip">Ground · flow only</span>
      )}
      {ui.live && (
        <span className={`sm-chip sm-chip-live${ui.live.visible ? "" : " sm-chip-idle"}`}>
          <b>{ui.live.label}</b>
          {ui.live.count !== null ? ` ${fmt(ui.live.count)}` : ""}
          {ui.live.medianAgeS !== null ? ` · age ${Math.round(ui.live.medianAgeS)} s` : ""}
        </span>
      )}
    </>
  );
}

/* ---- tooltip + selection + box/lasso capture ------------------------- */

export function TwinSurface({ ctrl, map }: { ctrl: TwinController; map: () => TwinMapLike | null }) {
  const ui = useTwinUi(ctrl);
  const root = useRef<HTMLDivElement>(null);
  const [drag, setDrag] = useState<Pt[] | null>(null);
  const [view, setView] = useState(0);

  // re-project the persisted region shape while the map moves
  useEffect(() => {
    const m = map();
    if (!m || !ui.region) return;
    let raf = 0;
    const bump = () => {
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        setView((v) => v + 1);
      });
    };
    m.on("move", bump);
    return () => {
      m.off("move", bump);
      if (raf) cancelAnimationFrame(raf);
    };
  }, [ui.region, map]);

  const local = (e: React.PointerEvent): Pt => {
    const r = root.current!.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top];
  };

  const capturing = ui.selectMode !== "off";
  const onDown = (e: React.PointerEvent) => {
    (e.target as Element).setPointerCapture?.(e.pointerId);
    const p = local(e);
    setDrag([p, p]);
  };
  const onMove = (e: React.PointerEvent) => {
    if (!drag) return;
    const p = local(e);
    if (ui.selectMode === "box") setDrag([drag[0], p]);
    else setDrag([...drag, p]);
  };
  const onUp = () => {
    if (!drag) return;
    let poly: Pt[];
    if (ui.selectMode === "box") {
      const [a, b] = [drag[0], drag[drag.length - 1]];
      if (Math.abs(a[0] - b[0]) < 6 || Math.abs(a[1] - b[1]) < 6) {
        setDrag(null);
        return;
      }
      poly = [
        [Math.min(a[0], b[0]), Math.min(a[1], b[1])],
        [Math.max(a[0], b[0]), Math.min(a[1], b[1])],
        [Math.max(a[0], b[0]), Math.max(a[1], b[1])],
        [Math.min(a[0], b[0]), Math.max(a[1], b[1])],
      ];
    } else {
      poly = drag;
    }
    setDrag(null);
    ctrl.finishRegion(poly);
  };

  const shape: Pt[] | null = (() => {
    if (drag) {
      if (ui.selectMode === "box") {
        const [a, b] = [drag[0], drag[drag.length - 1]];
        return [
          [a[0], a[1]],
          [b[0], a[1]],
          [b[0], b[1]],
          [a[0], b[1]],
        ];
      }
      return drag;
    }
    const m = map();
    if (m && ui.region) {
      void view;
      return ui.region.lonlat.map((ll) => {
        const p = m.project(ll);
        return [p.x, p.y] as Pt;
      });
    }
    return null;
  })();

  const h = ui.hover;
  const w = root.current?.clientWidth ?? 800;
  const flip = h ? h.x > w - 260 : false;

  return (
    <div className="sm-twin-surface" ref={root}>
      {shape && (
        <svg className="sm-twin-shape" aria-hidden>
          <polygon points={shape.map((p) => p.join(",")).join(" ")} />
        </svg>
      )}
      {capturing && (
        <svg
          className="sm-twin-capture"
          onPointerDown={onDown}
          onPointerMove={onMove}
          onPointerUp={onUp}
          onPointerCancel={() => setDrag(null)}
        />
      )}
      {h && !capturing && (
        <div
          className={`sm-twin-tip${h.src === "live" ? " sm-twin-tip-live" : ""}`}
          style={{ left: h.x + (flip ? -12 : 12), top: h.y + 12, transform: flip ? "translateX(-100%)" : undefined }}
        >
          <b>{h.chips.id}</b>
          {h.chips.chips.map((c) => (
            <span key={c} className="sm-twin-tag">
              {railWord(c)}
            </span>
          ))}
          {h.chips.simulated && <span className="sm-twin-tag sm-twin-tag-sim">Simulated</span>}
        </div>
      )}
      {ui.selected && (
        <div className="sm-twin-sel">
          <b>{ui.selected.chips.id}</b>
          {ui.selected.chips.chips.map((c) => (
            <span key={c} className="sm-twin-tag">
              {railWord(c)}
            </span>
          ))}
          {ui.selected.chips.simulated && <span className="sm-twin-tag sm-twin-tag-sim">Simulated</span>}
          <button className="sm-twin-chip sm-twin-clear" onClick={() => ctrl.deselect()} aria-label="Clear selection">
            Clear
          </button>
        </div>
      )}
    </div>
  );
}
