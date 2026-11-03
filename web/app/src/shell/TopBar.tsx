import { layoutLabel } from '../windows/layoutCode';
import React, { useEffect, useRef, useState } from 'react';
import { useDeskStore } from '../store';
import { DENSITIES, DENSITY_LABEL, SKINS, SKIN_LABEL, type Density, type Skin } from './appearance';
import type { Appearance } from './useAppearance';
import { MODE_LABEL, type ShellMode } from './mode';
import { Chip } from './Chip';
import { Icon } from '../ui/Icon';
import { Logo, Wordmark } from '../ui/Logo';
import { SegmentedControl, Toggle } from '../ui/Controls';
import { hint } from '../ui/hint';
import { modeCellTone } from './truthLabel';
import { TourButton } from '../tour/TourButton';
import { openSources } from '../sources/sourcesStore';
import { LiveStatus } from '../ui/LiveStatus';

/** m:ss since a build was accepted (never negative). */
export const fmtElapsed = (ms: number): string => {
  const t = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, '0')}`;
};

/** LIVE | SIMULATION: two text cells over a rail. The selected cell is a
 *  mode selector, not a data label: it takes the state colour (and the solid
 *  dot) only while a feed is LIVE now (`seg-b--feed`), otherwise it is
 *  neutral. Simulation always takes the simulation colour. */
export function ModeSwitch() {
  const s = useDeskStore();
  /* LIVE is a mode selector, not a health claim: the segment only lights green while at least one feed is live.
   * With every feed a snapshot or not connected (a recorded or offline copy) it takes the neutral selected style. */
  return (
    <span className="seg seg--mode mono" role="group" aria-label="Mode">
      {(['live', 'sim'] as ShellMode[]).map((m) => {
        const feed = modeCellTone(m, s.mode, s.health, s.snapshotMode) === 'feed';
        /* Live is worn only while a feed is live now; otherwise the cell reads Now. */
        const word = m === 'live' && feed ? 'Live' : MODE_LABEL[m];
        return (
          <button key={m} type="button" className={`seg-b seg-b--${m}${s.mode === m ? ' on' : ''}${feed ? ' seg-b--feed' : ''}`} aria-pressed={s.mode === m}
            aria-label={`${word} mode`} title={hint(`${word} mode`, m === 'live' ? 'Alt+1' : 'Alt+2')}
            onClick={() => { if (s.mode !== m) s.setMode(m); }}>
            {m === 'sim' ? <Icon name="zap" size="xs" /> : feed ? <i className="ui-dot" aria-hidden="true" /> : <Icon name="radio" size="xs" />}{word}
          </button>
        );
      })}
    </span>
  );
}

/** Light and Dark: the rail switch, always top right. The marker sits at the
 *  Dark end when the scheme is dark. */
export function ThemeToggle({ a }: { a: Appearance }) {
  return <Toggle label="Theme" labelHidden className="sw--theme" words={['Light', 'Dark']} checked={a.scheme === 'dark'} onChange={(v) => a.setScheme(v ? 'dark' : 'light')} />;
}

/** Appearance popover: skin, density, follow-system and the Tour entry.
 *  It holds a switch, two segmented controls and a checkbox, so it is a dialog, not a menu. */
export function AppearanceMenu({ a }: { a: Appearance }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    ref.current?.querySelector<HTMLElement>('.appear-pop input, .appear-pop button')?.focus(); // focus moves into the popover; Esc returns it to the button
    const onDown = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { setOpen(false); trigger.current?.focus(); } };
    document.addEventListener('mousedown', onDown); document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('mousedown', onDown); document.removeEventListener('keydown', onKey); };
  }, [open]);
  return (
    <span className="menu-wrap appear-wrap" ref={ref}>
      <button ref={trigger} type="button" className="tb-btn mono" aria-haspopup="dialog" aria-expanded={open} title={hint('Appearance')}
        onClick={() => setOpen((o) => !o)}><Icon name="appearance" /><span className="tb-btn-w">View</span></button>
      {open && (
        <div className="pop appear-pop mono" role="dialog" aria-label="Appearance">
          <div className="pop-row pop-row--theme"><span className="pop-k">Theme</span>
            <Toggle label="Dark theme" labelHidden words={['Light', 'Dark']} checked={a.scheme === 'dark'} onChange={(v) => a.setScheme(v ? 'dark' : 'light')} /></div>
          <div className="pop-row"><span className="pop-k">Skin</span>
            <SegmentedControl label="Skin" value={a.skin} onChange={a.setSkin} options={SKINS.map((k: Skin) => ({ value: k, label: SKIN_LABEL[k] }))} /></div>
          <div className="pop-row"><span className="pop-k">Density</span>
            <SegmentedControl label="Density" value={a.density} onChange={a.setDensity} options={DENSITIES.map((d: Density) => ({ value: d, label: DENSITY_LABEL[d] }))} /></div>
          <label className="pop-row pop-check"><input type="checkbox" checked={a.follow} onChange={(e) => a.setFollow(e.target.checked)} />
            <span>Follow system</span></label>
          <div className="pop-row pop-sep"><TourButton onStart={() => setOpen(false)} /></div>
        </div>
      )}
    </span>
  );
}

export interface TopBarProps {
  layoutCode: string; now: Date; a: Appearance; onAlerts: () => void;
  /** Opens the Export window. */
  onExport?: () => void;
}

/** Row A (36 px): brand and workspace tab at left, the mode switch,
 *  META text, then LIGHT | DARK, the appearance menu and the user chip. */
export function TopBar({ layoutCode, now, a, onAlerts, onExport }: TopBarProps) {
  const s = useDeskStore();
  return (
    <div className={`topbar${s.mode === 'sim' ? ' topbar--sim' : ''}`}>
      <h1 className="ui-vh">SupermAP</h1>
      <span className="brand"><Logo size={24} /><Wordmark /></span>
      {/* A real browser tab on the same state; the close button only
          when the app opened this tab (chrome is functional or absent). */}
      <span className="wtab mono">{layoutCode} · {layoutLabel(s.layout)}
        {s.canCloseTab && <button type="button" title="Close this tab" aria-label="Close tab" onClick={s.closeTab}><Icon name="close" size="xs" /></button>}</span>
      <button type="button" className="tb-plus mono" title="New tab" onClick={s.openNewTab}>+</button>
      <ModeSwitch />
      {s.mode === 'sim' && <Chip kind="sim" />}
      {s.simInflight && (s.simInflight.state === 'building'
        ? <Chip kind="sim" word={`Simulation building ${fmtElapsed(now.getTime() - s.simInflight.since)}`} />
        : <button type="button" className="chip-btn" onClick={s.openSimInflight}>
          <Chip kind={s.simInflight.state === 'ready' ? 'sim' : 'down'} word={s.simInflight.state === 'ready' ? 'Simulation ready · open' : 'Simulation failed'} /></button>)}
      <LiveStatus text={s.simInflight ? (s.simInflight.state === 'building' ? 'Simulation building' : s.simInflight.state === 'ready' ? 'Simulation ready' : 'Simulation failed') : ''} />
      <span className="spacer" />
      {onExport && <button type="button" className="meta-btn tb-export" onClick={onExport} aria-label="Export"><Icon name="file-down" size="xs" /><span className="tb-btn-w"> Export</span></button>}
      <button type="button" className="meta-btn" onClick={openSources} aria-label="Sources" aria-haspopup="dialog" title={hint('Sources')}><Icon name="list" size="xs" /> Sources</button>
      <button type="button" className="meta-btn mono" onClick={onAlerts}>Alerts {s.alertsCount !== null ? s.alertsCount : '—'}</button>
      <span className="meta mono">{now.toISOString().slice(11, 16)}Z</span>
      <ThemeToggle a={a} />
      <TourButton />
      <AppearanceMenu a={a} />
      <span className="user-chip mono" role="img" aria-label="Guest session" title={hint('Guest session')}>G</span>
    </div>
  );
}
