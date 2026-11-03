/**
 * ADVANCED SEARCH — the cmdbar search box and
 * its dropdown, wired to the search engine. One bar over the whole
 * terminal, summoned globally with Ctrl/Cmd+K, rendered in
 * panel chrome — square corners, 1px borders, no shadow, no rounding —
 * with grouped category headers carrying counts in the menubar's type
 * style, result rows in the LSEG anatomy (type chip · title ·
 * one-line description · code right-aligned), and a summary card
 * beside the results for the highlighted row, drawn only from the
 * engine's served preview facts (omitted when absent, never filled).
 *
 * Every activation dispatches the result's action descriptor
 * through the store's existing actions — selectNode / openChain /
 * showMoney / setFocus / setLayout / setLens / setLayerVis /
 * setBasemap — plus the map module's flyToNode for "View on map".
 * There is no parallel navigation and no new data path: the index is
 * built from what the store already holds (see ./sources.ts).
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useDeskStore, useStore } from '../store';

type OpenSimRun = ReturnType<typeof useStore>['openSimRun'];
import type { LayerVis } from '../store';
import type { PanelId } from '../types';
import { focusPlaceOnMap } from '../mapFlyTo';
import {
  CATEGORY_LABELS, CATEGORY_ORDER, buildSearchIndex, flattenResults, search,
  type SearchAction, type SearchCategory, type SearchResult,
} from './engine';
import { PANEL_FEATURES } from './registry';
import { buildSearchSources } from './sources';
import { resolveQuery, type FacetState } from './query';
import { requestDesk, requestExploration, requestReplay, requestTool, requestWatchlist } from './navBridge';
import { closeToolSlot, planToolOpen } from './toolOpen';
import { SEARCH_COPY as COPY, cardEnter, chipFor, footerShowing } from './copy';
import { useNlSearch } from '../meta/useNlSearch';
import { NlStrip } from '../meta/NlStrip';
import { statusIsLive } from '../meta/statusView';
import { safeStorage, themeForBasemap } from '../store';
import { wmBridge } from '../windows/bridge';
import { wm } from '../windows/controller';
import { useWmState } from '../windows/chrome';
import { usePhone } from '../windows/PhoneWorkspace';
import { WorkspaceCtx } from '../windows/context';
import { requestLayoutOp } from '../windows/LayoutsMenu';
import { BUILTIN_PRESETS, builtinFor } from '../windows/presets';
import { Icon } from '../ui/Icon';
import { Kbd } from '../ui/Controls';
import { KEY_SHEET, RECENTS_KEY, parseCommand, parseRecents, pushRecent, suggestCommands, type CommandRow } from './command';
import { runIntent, type CommandDeps } from './commandRun';
import { startTour } from '../tour/runtime';

export const DROP_ID = 'cmd-drop';
export const CMD_ID = 'cmd-options';
export const LIST_ID = 'cmd-results';
/** Id of the option at a flat index (commands first, then results); aria-activedescendant points at it. */
export const optId = (i: number): string => `cmd-opt-${i}`;

type ModeKey = 'sea' | 'air' | 'ground';
const MODE_CHIPS: Array<{ key: ModeKey; label: string }> = [
  { key: 'sea', label: 'Sea' }, { key: 'air', label: 'Air' }, { key: 'ground', label: 'Ground' },
];

/**
 * The facet chip row: All + one chip per category (with its
 * indexed count; empty categories are not offered), the mode toggles
 * and the two event toggles the data supports (SEV 4+, Last 7 days).
 * Chips are mouse/tap targets only — they are never focus stops, so
 * the keyboard flow (arrows / Enter / Esc / Ctrl+K) is untouched.
 * Pure and hook-free.
 */
export function FacetBar({ counts, activeCategories, onCategory, modes, onToggleMode, sev4, onToggleSev, last7, onToggleLast7 }: {
  counts: Partial<Record<SearchCategory, number>>;
  activeCategories: SearchCategory[] | null;
  onCategory: (c: SearchCategory | null) => void;
  modes: ModeKey[];
  onToggleMode: (m: ModeKey) => void;
  sev4: boolean; onToggleSev: () => void;
  last7: boolean; onToggleLast7: () => void;
}) {
  const chip = (label: string, on: boolean, onClick: () => void) => (
    <button key={label} type="button" tabIndex={-1} aria-pressed={on}
      className={`search-facet mono${on ? ' search-facet--on' : ''}`} onClick={onClick}>{label}</button>
  );
  return (
    <div className="search-facets">
      {chip(COPY.facetAll, activeCategories === null, () => onCategory(null))}
      {CATEGORY_ORDER.filter((c) => (counts[c] ?? 0) > 0).map((c) =>
        chip(`${CATEGORY_LABELS[c]} ${counts[c]}`,
          activeCategories?.length === 1 && activeCategories[0] === c,
          () => onCategory(c)))}
      <span className="search-facet-sep" />
      {MODE_CHIPS.map((m) => chip(m.label, modes.includes(m.key), () => onToggleMode(m.key)))}
      {chip(COPY.facetSev, sev4, onToggleSev)}
      {chip(COPY.facetLast7, last7, onToggleLast7)}
    </div>
  );
}

/** Hands the latest `openSimRun` to the search box through a ref. This tiny leaf holds the full store so the
 *  SearchBox itself stays on the desk slice and does not re-render on every playback tick. */
function SimOpener({ into }: { into: React.MutableRefObject<OpenSimRun | null> }) {
  into.current = useStore().openSimRun;
  return null;
}

/** Quiet time after the last store change before a closed search box rebuilds its index. */
export const SEARCH_SETTLE_MS = 1200;

export function SearchBox({ fnChip }: { fnChip: string }) {
  const s = useDeskStore();
  const openSimRun = useRef<OpenSimRun | null>(null);
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const [highlight, setHighlight] = useState(0);
  const [anchor, setAnchor] = useState<{ left: number; top: number; width: number } | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const wrapRef = useRef<HTMLSpanElement | null>(null);
  const dropRef = useRef<HTMLDivElement | null>(null);

  // The index spans what the store already holds; rebuilt only when a
  // source slice changes (boot loads, fetches), never per keystroke.
  const liveInputs = useMemo(() => ({
    graph: s.graph, feed: s.feed, ledger: s.ledger, moneyExposures: s.moneyExposures,
    lenses: s.lenses, satScans: s.satScans, chain: s.chain, runMeta: s.runMeta,
    runsCatalogue: s.runsCatalogue, explorations: s.explorations, watchlists: s.watchlists,
    goodsClasses: s.goodsClasses, replays: s.replays,
  }), [s.graph, s.feed, s.ledger, s.moneyExposures, s.lenses, s.satScans, s.chain, s.runMeta,
    s.runsCatalogue, s.explorations, s.watchlists, s.goodsClasses, s.replays]);
  /* Boot loads a dozen of these slices one after another, and each change rebuilt the whole index (about 1,000 places, the
   * feed, the panel registry): ten rebuilds of 0.1 to 0.3 s each on the main thread while the first screen was loading.
   * While the box is closed nobody reads the index, so it follows the slices once they have been quiet for
   * SEARCH_SETTLE_MS; while it is open it follows them at once, so a result is never older than the store. */
  const [settledInputs, setSettledInputs] = useState(liveInputs);
  useEffect(() => {
    if (open) { setSettledInputs(liveInputs); return undefined; }
    const t = window.setTimeout(() => setSettledInputs(liveInputs), SEARCH_SETTLE_MS);
    return () => window.clearTimeout(t);
  }, [liveInputs, open]);
  const inputs = open ? liveInputs : settledInputs;
  const sources = useMemo(() => buildSearchSources(inputs), [inputs]);
  const index = useMemo(() => buildSearchIndex(sources), [sources]);
  // Facets: chip/toggle state, reset whenever the box closes.
  const [facetCats, setFacetCats] = useState<SearchCategory[] | null>(null);
  const [facetModes, setFacetModes] = useState<ModeKey[]>([]);
  const [sev4, setSev4] = useState(false);
  const [last7, setLast7] = useState(false);
  useEffect(() => {
    if (!open) { setFacetCats(null); setFacetModes([]); setSev4(false); setLast7(false); }
  }, [open ]);
  const facets: FacetState = useMemo(() => ({
    categories: facetCats ?? undefined,
    modes: facetModes,
    severities: sev4 ? [4, 5] : undefined,
    dateFloor: last7 ? new Date(Date.now() - 7 * 86400000).toISOString() : undefined,
  }), [facetCats, facetModes, sev4, last7]);
  // The query seam (query.ts): today qualifier + facet merge; the NL
  // front-end slots in there, never here.
  const resolved = useMemo(() => resolveQuery(query, facets), [query, facets]);
  const structOutcome = useMemo(() => search(index, resolved.text, resolved.filters), [index, resolved]);
  // META edge: consulted only when the structured path found
  // nothing and the input reads as language; its validated filters feed
  // the SAME engine, so the lexical search stays the only ranker.
  const nl = useNlSearch({
    query, index, structOutcome, facetFilters: resolved.filters,
    statusLive: statusIsLive(s.llmStatusSource === 'live' ? s.llmStatus : null),
  });
  /* ── command bar ─────────────────────────────
   * With the window manager attached, command rows (CODE, verbs, `>`, `?`,
   * recents, layouts) lead the dropdown and the grouped search below it is
   * untouched. With the old grid there are no command rows at all. */
  const wmSt = useWmState();
  /* Phone: the dock is not mounted, but the command bar still works (layout and open act on the store). */
  const phone = usePhone();
  const cmdOn = wmSt.attached || phone;
  const ws = React.useContext(WorkspaceCtx);
  const [recents, setRecents] = useState<string[]>(() => parseRecents(safeStorage.get(RECENTS_KEY)));
  const cmdCtx = useMemo(() => ({ gate: { liveTwin: s.liveTwinEnabled }, userPresets: wmSt.userPresets }), [s.liveTwinEnabled, wmSt.userPresets]);
  const intentNow = useMemo(() => (cmdOn ? parseCommand(query, cmdCtx) : null), [cmdOn, query, cmdCtx]);
  const cmdRows: CommandRow[] = useMemo(() => (cmdOn && intentNow?.t !== 'keys' ? suggestCommands(query, cmdCtx, recents) : []), [cmdOn, intentNow, query, cmdCtx, recents]);
  const keysOpen = intentNow?.t === 'keys';
  const nCmd = cmdRows.length;
  const hotCmd: CommandRow | null = highlight < nCmd ? cmdRows[highlight] : null;

  const outcome = nl.searchOutcome ?? structOutcome;
  const flat = useMemo(() => flattenResults(outcome), [outcome]);
  const shownPrimary = useMemo(
    () => outcome.groups.reduce((n, g) => n + g.results.filter((r) => !r.companion).length, 0),
    [outcome]);
  const hotIdx = highlight - nCmd;
  const hot: SearchResult | undefined = hotIdx >= 0 ? flat[Math.min(hotIdx, Math.max(0, flat.length - 1))] : undefined;
  const hasQuery = query.trim().length > 0;

  useEffect(() => { setHighlight(0); }, [query, index, facets]);

  // Ctrl/Cmd+K summon.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
        e.preventDefault();
        setOpen(true);
        inputRef.current?.focus();
        inputRef.current?.select();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  // Slice recovery: opening search re-asks the store
  // for any recovered catalogue slice that never landed at boot, so a
  // missed boot fetch is repaired here instead of the category
  // staying absent for the whole session. The store bounds each
  // slice's retries; opening during a running cycle is a no-op.
  const ensureSearchSlices = s.ensureSearchSlices;
  useEffect(() => { if (open) ensureSearchSlices(); }, [open, ensureSearchSlices]);

  // Anchor the portalled dropdown to the input (the cmdbar clips
  // overflow, so the dropdown cannot live inside it).
  useEffect(() => {
    if (!open) return;
    const update = () => {
      const r = wrapRef.current?.getBoundingClientRect();
      if (!r) return;
      const width = Math.min(920, window.innerWidth - 16);
      const left = Math.max(8, Math.min(r.left, window.innerWidth - width - 8));
      setAnchor({ left, top: r.bottom + 4, width });
    };
    update();
    window.addEventListener('resize', update);
    window.addEventListener('scroll', update, true);
    return () => { window.removeEventListener('resize', update); window.removeEventListener('scroll', update, true); };
  }, [open ]);

  // Click outside closes.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      const t = e.target as Node;
      if (wrapRef.current?.contains(t) || dropRef.current?.contains(t)) return;
      setOpen(false);
    };
    window.addEventListener('pointerdown', onDown);
    return () => window.removeEventListener('pointerdown', onDown);
  }, [open ]);

  // Keep the highlighted row in view while arrowing.
  useEffect(() => {
    if (!open) return;
    dropRef.current?.querySelector('[data-hot="true"]')?.scrollIntoView({ block: 'nearest' });
  }, [highlight, open, outcome]);

  const ensurePanel = (panelId: PanelId, layouts: string[]) => {
    closeToolSlot();
    /* The window manager opens the window where it belongs; no layout switch. */
    if (wmBridge.handlers) { wmBridge.handlers.openWindow(panelId, 'dock'); s.setFocus(panelId); return; }
    if (!s.visible.includes(panelId)) {
      // setLayout clears the closed set — re-selecting the active
      // layout reopens a panel the user had closed within it.
      if (!layouts.includes(s.layout)) s.setLayout(layouts[0]);
      else s.setLayout(s.layout);
    }
    s.setFocus(panelId);
  };
  /* "view on map" also restores a closed map (the fly-to bridge
   * in mapFlyTo.ts asks for the same restore) — a hidden map must
   * never silently swallow the flight. */
  const ensureMap = () => { if (!wmBridge.handlers && !s.visible.includes('map')) s.setLayout('SupermAP'); s.restoreMap(); };

  const dispatch = (a: SearchAction) => {
    switch (a.type) {
      case 'select-place': s.selectNode(a.placeId); break;
      case 'view-on-map': ensureMap(); s.selectNode(a.placeId); focusPlaceOnMap(a.placeId); break;
      case 'open-panel': {
        // The engine carries the id as a string; the registry (its
        // source) types it PanelId — the cast is that seam, nothing more.
        const panelId = a.panelId as PanelId;
        const f = PANEL_FEATURES.find((p) => p.panelId === panelId);
        ensurePanel(panelId, f?.layouts ?? ['SupermAP']);
        break;
      }
      case 'open-layout': closeToolSlot(); s.setLayout(a.layout); break;
      case 'open-tool': {
        // A tool belongs to a shell mode: from the other mode, switch to
        // the tool's first mode, then hand the slot request to the shell.
        const plan = planToolOpen(a, s.mode);
        if (plan.switchTo) s.setMode(plan.switchTo);
        requestTool(plan.tool);
        break;
      }
      case 'toggle-layer': {
        ensureMap();
        const key = a.layer as keyof LayerVis;
        s.setLayerVis({ [key]: !s.layerVis[key] } as Partial<LayerVis>);
        break;
      }
      case 'set-basemap': ensureMap(); s.setBasemap(a.basemap); break;
      case 'apply-lens': ensureMap(); s.setLens(a.lensId); break;
      case 'open-event':
        if (a.placeId) s.selectNode(a.placeId);
        ensurePanel('news', ['SupermAP', 'Market Desk']);
        break;
      case 'open-company': s.showMoney(a.placeId); break;
      case 'open-ledger': ensurePanel('ledger', ['Replay', 'Proof']); break;
      case 'open-run': void s.openChain(a.runId); break;
      case 'open-sim-run': void openSimRun.current?.(a.runId); break;
      case 'open-scan': s.selectNode(a.placeId); ensurePanel('satwatch', ['Market Desk']); break;
      case 'open-desk': requestDesk(a.desk); ensurePanel('desks', ['Analysis Desks']); break;
      case 'open-exploration': requestExploration(a.id); ensurePanel('ensemble', ['SupermAP', 'Domino Room']); break;
      case 'open-watchlist': requestWatchlist(a.id); ensurePanel('alerts', ['SupermAP', 'Place Desk']); break;
      case 'open-goods': ensurePanel('impact', ['Market Desk']); break;
      case 'open-replay': requestReplay(a.eventId); requestDesk('replay'); ensurePanel('desks', ['Analysis Desks']); break;
    }
  };

  /** `CODE arg`: a place name selects and shows it, a run id opens the chain. */
  const focusArg = (_w: unknown, arg: string) => {
    if (/^run_[A-Za-z0-9_.:-]{1,120}$/.test(arg)) { void s.openChain(arg); return; }
    const first = search(index, arg, { categories: ['places'], limitPerGroup: 3 }).groups[0]?.results.find((r) => !r.companion);
    if (first) dispatch(first.action);
  };
  const cmdDeps: CommandDeps = {
    openWindow: (id, how, w) => { if (wmBridge.handlers) wmBridge.handlers.openWindow(id, how, w); else s.setFocus(id as PanelId); },
    closeWindow: (id) => wm.closeWindow(id),
    floatWindow: (id) => { if (wm.isOpen(id)) wm.floatWindow(id); else wm.openWindow(id, 'float'); },
    dockWindow: (id, side) => { if (wm.isOpen(id)) wm.dockWindow(id, side); else wm.openWindow(id, 'dock'); },
    tabWindow: (id, w) => { if (wm.isOpen(id)) wm.tabWindow(id, w); else wm.openWindow(id, 'tab', w); },
    maximizeWindow: (id, on) => { if (!wm.isOpen(id)) wm.openWindow(id, 'dock'); if (wm.isMaximized(id) !== on) wm.toggleMaximize(id); },
    activeWindow: () => wm.activeWindowId(),
    applyLayout: (id, user) => { if (!wmSt.attached) { const b = builtinFor(id); if (b) s.setLayout(b.legacy); return; } if (user) wm.applyUser(id); else { const b = builtinFor(id) ?? BUILTIN_PRESETS[0]; wm.apply(b); } },
    layoutOp: (op, name) => requestLayoutOp(op, name),
    setTheme: (v) => s.setTheme(themeForBasemap(s.theme, v)),
    setDensity: (v) => ws.appearance?.setDensity(v),
    setMode: (v) => s.setMode(v),
    exportDataset: () => wm.openWindow('exports', 'dock'),
    openMetaAi: () => wm.openWindow('aiactivity', 'float'),
    startTour: (mode) => startTour(mode),
    focusArg,
  };
  const runRow = (row: CommandRow, mods: { shift?: boolean; alt?: boolean } = {}) => {
    closeToolSlot();
    runIntent(row.intent, cmdDeps, mods);
    const text = query.trim() || row.code || row.label;
    if (row.intent.t !== 'empty') { const next = pushRecent(recents, text); setRecents(next); safeStorage.set(RECENTS_KEY, JSON.stringify(next)); }
    setQuery(''); setOpen(false);
    inputRef.current?.blur();
  };

  const activate = (r: SearchResult) => {
    dispatch(r.action);
    setOpen(false);
    inputRef.current?.blur();
  };

  const returnRef = useRef<HTMLElement | null>(null);
  const onInputKey = (e: React.KeyboardEvent) => {
    if (e.altKey && e.key === 'Backspace' && nl.removeLast()) { e.preventDefault(); return; }
    /* Esc clears the text first, then closes and leaves the bar. */
    if (e.key === 'Escape') {
      if (query) { e.preventDefault(); setQuery(''); return; }
      if (open) { e.preventDefault(); setOpen(false); }
      /* focus goes back to the control the bar was opened from, not to the top of the page */
      const back = returnRef.current;
      returnRef.current = null;
      if (back && back.isConnected) back.focus(); else inputRef.current?.blur();
      return;
    }
    if (e.key === 'Tab' && !e.shiftKey && nCmd > 0 && query.trim()) {
      const c = cmdRows[Math.min(highlight, nCmd - 1)];
      const completion = c.intent.t === 'open' && !c.intent.arg ? c.code : null;
      if (completion && completion.toLowerCase() !== query.trim().toLowerCase()) { e.preventDefault(); setQuery(`${completion} `); return; }
    }
    const total = nCmd + flat.length;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      if (!open) { setOpen(true); return; }
      if (!total) return;
      e.preventDefault();
      const d = e.key === 'ArrowDown' ? 1 : -1;
      setHighlight((h) => (h + d + total) % total);
      return;
    }
    if (e.key === 'Enter') {
      if (open && hotCmd) { e.preventDefault(); runRow(hotCmd, { shift: e.shiftKey, alt: e.altKey }); }
      else if (open && hot) { e.preventDefault(); activate(hot); }
      else if (open && nl.canRun) { e.preventDefault(); nl.run(); }
      else if (!open) setOpen(true);
    }
  };

  /* Options actually in the DOM: command rows, then results (hidden while the key sheet is open). */
  const listed = nCmd + (keysOpen ? 0 : flat.length);
  const controls = [nCmd > 0 ? CMD_ID : '', !keysOpen && outcome.totalResults > 0 ? LIST_ID : ''].filter(Boolean).join(' ');
  let flatIdx = nCmd - 1;
  const dropdown = open && anchor ? createPortal(
    <div className="search-drop" ref={dropRef} style={{ left: anchor.left, top: anchor.top, width: anchor.width }}
      id={DROP_ID}>
      {!keysOpen && (
        <FacetBar counts={index.counts} activeCategories={facetCats}
          onCategory={(c) => setFacetCats(c ? [c] : null)}
          modes={facetModes}
          onToggleMode={(m) => setFacetModes((prev) => (prev.includes(m) ? prev.filter((x) => x !== m) : [...prev, m]))}
          sev4={sev4} onToggleSev={() => setSev4((v) => !v)}
          last7={last7} onToggleLast7={() => setLast7((v) => !v)} />
      )}
      {!keysOpen && <NlStrip nl={nl} />}
      {keysOpen && (
        <div className="keysheet" role="document" aria-label="Key sheet">
          {KEY_SHEET.map((g) => (
            <div key={g.group}>
              <h4 aria-level={2}>{g.group}</h4>
              {g.rows.map((r) => <div key={`${r.label}${r.keys}`} className="ks-row"><span>{r.label}</span><span className="ks-keys">{r.keys}</span></div>)}
            </div>
          ))}
        </div>
      )}
      {nCmd > 0 && (
        <div className="cmd-group" id={CMD_ID} role="listbox" aria-label={COPY.commandsLabel}>
          <div className="cmd-ghead"><span>{hasQuery ? 'Commands' : recents.length ? 'Recent and layouts' : 'Layouts'}</span></div>
          {cmdRows.map((r, i) => (
            <button key={r.key} id={optId(i)} type="button" role="option" aria-selected={highlight === i} data-hot={highlight === i || undefined}
              className={`cmd-row${highlight === i ? ' cmd-row--hot' : ''}`} onMouseEnter={() => setHighlight(i)}
              onClick={(e) => runRow(r, { shift: e.shiftKey, alt: e.altKey })}>
              <Icon name={r.icon} size="sm" className="cmd-ico" /><span className="cmd-label">{r.label}</span>{r.code && <span className="cmd-code">{r.code}</span>}
            </button>
          ))}
        </div>
      )}
      {keysOpen ? null : outcome.totalResults > 0 ? (
        <div className="search-main">
          <div className="search-list" id={LIST_ID} role="listbox" aria-label={COPY.resultsLabel}>
            {outcome.groups.map((g) => (
              <div key={g.category} className="search-group" role="group" aria-label={g.label}>
                <div className="search-ghead mono">
                  <span>{g.label}</span>
                  <span className="search-gcount">{g.total}</span>
                </div>
                {g.results.map((r) => {
                  flatIdx += 1;
                  const idx = flatIdx;
                  const isHot = hot === r;
                  return (
                    <button key={`${g.category}-${r.kind}-${r.title}-${idx}`} id={optId(idx)} type="button" role="option"
                      aria-selected={isHot} data-hot={isHot || undefined}
                      className={`search-row${isHot ? ' search-row--hot' : ''}${r.companion ? ' search-row--comp' : ''}`}
                      onMouseEnter={() => setHighlight(idx)} onClick={() => activate(r)}>
                      <span className="search-chip mono">{chipFor(r)}</span>
                      <span className="search-rowmain">
                        <span className="search-title">
                          {r.companion ? <><span className="dim">View on map —</span> {r.title}</> : r.title}
                        </span>
                        <span className="search-sub">{r.subtitle}</span>
                      </span>
                      {isHot && <span className="search-act mono">{r.actionLabel} <Kbd keys="Enter" /></span>}
                      {r.code && r.code !== chipFor(r) && <span className="search-code mono">{r.code}</span>}
                    </button>
                  );
                })}
              </div>
            ))}
          </div>
          {hot && (
            <aside className="search-card">
              <div className="search-card-head mono">
                <span>{CATEGORY_LABELS[hot.category]}</span>
                {hot.code && <span className="search-code">{hot.code}</span>}
              </div>
              <div className="search-card-title">{hot.title}</div>
              <div className="search-card-sub">{hot.subtitle}</div>
              {hot.facts && hot.facts.length > 0 && (
                <dl className="kv search-facts mono">
                  {hot.facts.map((f) => (
                    <React.Fragment key={f.label}><dt>{f.label}</dt><dd>{f.value}</dd></React.Fragment>
                  ))}
                </dl>
              )}
              <div className="search-card-foot mono">{cardEnter(hot.actionLabel)}</div>
            </aside>
          )}
        </div>
      ) : nCmd > 0 ? null : hasQuery ? (
        <div className="search-hint">
          <div className="search-hint-title">{COPY.noResults} · “{query.trim()}”</div>
        </div>
      ) : (
        <div className="search-hint">
          <div className="search-hint-title mono">{COPY.hintTitle}</div>
          <p className="search-chips">{COPY.hintScope.map((w) => <span key={w} className="search-chip mono">{w}</span>)}</p>
          <div className="search-hint-title mono">{COPY.hintQualifiersTitle}</div>
          <p className="search-chips">{COPY.hintQualifiers.map((w) => <span key={w} className="search-chip mono">{w}</span>)}</p>
        </div>
      )}
      <div className="search-foot mono">
        <span>{outcome.totalResults > 0
          ? <>{footerShowing(shownPrimary, outcome.totalResults)}
              {shownPrimary < outcome.totalResults ? <span className="search-narrow"> · {COPY.narrowNote}</span> : null}</>
          : ''}</span>
        <span className="search-keys">
          <Icon name="arrow-up" size="xs" /><Icon name="arrow-down" size="xs" /> {COPY.footerSelect} <Kbd keys="Enter" /> {COPY.footerOpen} <Kbd keys="Esc" /> {COPY.footerClose}
        </span>
      </div>
    </div>, document.body) : null;

  return (
    <span className="cmd-input" ref={wrapRef}>
      <SimOpener into={openSimRun} />
      <span className="fn-chip mono">{fnChip}</span>
      <input ref={inputRef} value={query} role="combobox" aria-label={COPY.ariaLabel} aria-haspopup="listbox" aria-autocomplete="list"
        aria-expanded={open} aria-controls={open ? controls || undefined : undefined}
        aria-activedescendant={open && listed > 0 ? optId(Math.min(highlight, listed - 1)) : undefined} placeholder={COPY.placeholder}
        onChange={(e) => { setQuery(e.target.value); setOpen(true); }}
        onFocus={(e) => { setOpen(true); const from = e.relatedTarget as HTMLElement | null; if (from && from !== document.body) returnRef.current = from; }} onKeyDown={onInputKey} />
      {dropdown}
    </span>
  );
}
