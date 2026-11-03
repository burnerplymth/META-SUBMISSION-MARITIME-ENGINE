import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Panel } from '../../components/Panel';
import { Chip, MetaChip, StateLine } from '../reports/chips';
import { CentreError } from '../reports/client';
import { failureState } from '../reports/model';
import {
  fetchAvailability, fetchBrief, fetchDetectors, fetchJob, fetchOverlay, fetchPlaces, fetchSignals, fetchSources, probeScanCapability, startScan,
} from './client';
import {
  JOB_POLL_MS, briefLines, cloudBasis, countsView, deltaChip, detectionWord,
  displayLabel, jobActive, jobProgress, layerState, metaChip, nowClip, placeRows, providerChip,
  signalRows, clampSlider,
  type BriefDoc, type CountsView, type DetectorsDoc, type Overlay, type OverlayCrop, type OverlayDetection, type PerCrop,
  type PlacesDoc, type ScanJob, type SignalsDoc,
} from './model';
import { FrameImage, SynthChip } from './EnhancedImage';
import { useNoData } from './useNoData';
import { changeGate, noDataChipWord, showNoData, type CropNoData, type NoDataState } from './noDataGate';
import { Layers } from './overlayLayers';
import { DetectorStrip } from './DetectorStrip';
import { MetaAiCard } from './MetaAiCard';
import { AerialDesk } from './aerial/AerialDesk';
import { fetchScenes as fetchAerialScenes } from './aerial/aerialClient';
import { preferredPlace, sceneForPlace, type ScenesDoc } from './aerial/aerialModel';
import { VERIFICATION_WORD } from './aerial/aerialModel';
import { SourceStrip, SourceViewer } from './SourceDesk';
import { sourceChips, type AvailabilityDoc, type SourceId, type SourcesDoc } from './sources';
import './satwatch2.css';
import { AiBadge } from '../../meta/AiBadge';
import { recordAiCall } from '../../meta/aiCalls';
import { metaChipView, parseProvenance } from '../../meta/provenance';
import { labelWord } from '../../copy';
import { useSatDeskExportables } from '../../exports/adapters';
import { scrollBehavior } from '../../ui/motion';

/* SATELLITE DESK. Anchorages list, baseline vs now crops
 * side by side or behind a slider, detection overlays per
 * satwatch-overlay/1 (points now; bbox / polygon / COCO-RLE masks when
 * the contract carries them), delta chips with the cloud basis, the
 * detector status chip, an admin-only SCAN control that exists only when
 * the API lets the caller scan, the brief with its META chip, signals.
 * Counts the API serves as null are NOT SERVED, never 0; the reference-counts
 * provider is always REFERENCE COUNTS · OFFLINE. */

type Feed<T> = { s: 'loading' } | { s: 'ok'; v: T } | { s: 'err'; code: string };
type View = 'side' | 'slider';
type OverlayState = { s: 'loading' } | { s: 'ok'; ov: Overlay | null } | { s: 'err'; code: string };

const NO_CHECK: CropNoData = { now: { s: 'err' }, baseline: { s: 'err' } };

function CropFrame({ crop, alt, dets, show, ratio, enhanced, noData }: { crop: OverlayCrop; alt: string; dets: OverlayDetection[]; show: boolean; ratio: string; enhanced: boolean; noData: NoDataState }) {
  return (
    <div className="sw2-frame" style={{ aspectRatio: ratio }}>
      <FrameImage url={crop.url} alt={alt} kind="optical" enhanced={enhanced} />
      <Layers dets={dets} show={show} />
    </div>
  );
}

/* ── chips ───────────────────────────────────────────────────────── */
const snap = (date: string) => <Chip chip="SNAPSHOT" word={`Snapshot ${date}`} title="Stored scene" />;

export function CropCaption({ role, crop, pc, layer, cv, noData = { s: 'err' } }: { role: 'now' | 'baseline'; crop: OverlayCrop; pc: PerCrop | null | undefined; layer: ReturnType<typeof layerState>; cv: CountsView; noData?: NoDataState }) {
  const cb = cloudBasis(crop, pc);
  const count = role === 'now' ? cv.now : cv.baseline;
  return (
    <div className="sw2-cap">
      <span className="rk-lbl">{role === 'now' ? 'Now' : 'Baseline'}</span>
      {snap(crop.scene_date)}
      {cb.aoi && <MetaChip title="Cloud, anchorage">Cloud AOI {cb.aoi}</MetaChip>}
      {cb.tile && <MetaChip title="Cloud, tile">Tile cloud {cb.tile}</MetaChip>}
      {cb.visibility && <MetaChip title={cb.visibilityFull ?? undefined}>Visibility {labelWord(cb.visibility)}</MetaChip>}
      {showNoData(noData) && <Chip chip="NOT SERVED" word={noDataChipWord(noData.stats.pct)} title="Pixels without data" />}
      {count == null ? <Chip chip="NOT SERVED" word="Count not served" /> : <MetaChip>Vessels {count}</MetaChip>}
      {layer.state === 'COUNT ONLY' && <Chip chip="NOT SERVED" word="Count only · No positions" />}
      {layer.state === 'DRAWN' && <MetaChip>{layer.summary.drawable} drawn</MetaChip>}
    </div>
  );
}

/* ── the compare view ────────────────────────────────────────────── */
function CompareView({ ov, view, pos, setPos, markers, enhanced, noData }: { ov: Overlay; view: View; pos: number; setPos: (n: number) => void; markers: boolean; enhanced: boolean; noData: CropNoData }) {
  const crops = ov.crops;
  if (!crops) return <StateLine chip="NOT SERVED" word="No crops" />;
  const cpx = ov.detections.find((d) => d.crop_px)?.crop_px ?? [1200, 1200];
  const ratio = `${cpx[0]} / ${cpx[1]}`;
  const dNow = ov.detections.filter((d) => d.role === 'now');
  const dBase = ov.detections.filter((d) => d.role === 'baseline');
  const cv = countsView(ov);
  const lNow = layerState(ov, 'now'); const lBase = layerState(ov, 'baseline');
  const altNow = `${ov.name} now, Sentinel-2 ${crops.now.scene_date}`;
  const altBase = `${ov.name} baseline, Sentinel-2 ${crops.baseline.scene_date}`;
  return (
    <div className="sw2-compare">
      {view === 'side' ? (
        <div className="sw2-side">
          <figure><CropFrame crop={crops.baseline} alt={altBase} dets={dBase} show={markers} ratio={ratio} enhanced={enhanced} noData={noData.baseline} />
            <figcaption><CropCaption role="baseline" crop={crops.baseline} pc={ov.per_crop?.baseline} layer={lBase} cv={cv} noData={noData.baseline} /></figcaption></figure>
          <figure><CropFrame crop={crops.now} alt={altNow} dets={dNow} show={markers} ratio={ratio} enhanced={enhanced} noData={noData.now} />
            <figcaption><CropCaption role="now" crop={crops.now} pc={ov.per_crop?.now} layer={lNow} cv={cv} noData={noData.now} /></figcaption></figure>
        </div>
      ) : (
        <div className="sw2-slide">
          <div className="sw2-stack" style={{ aspectRatio: ratio }}>
            <div className="sw2-layer">
              <FrameImage url={crops.baseline.url} alt={altBase} kind="optical" enhanced={enhanced} showChip={false} />
              <Layers dets={dBase} show={markers} />
            </div>
            <div className="sw2-layer sw2-layer--top" style={{ clipPath: nowClip(pos) }}>
              <FrameImage url={crops.now.url} alt={altNow} kind="optical" enhanced={enhanced} showChip={false} />
              <Layers dets={dNow} show={markers} />
            </div>
            <div className="sw2-divider" style={{ left: `${clampSlider(pos)}%` }} aria-hidden="true" />
            {enhanced && <SynthChip />}
            <span className="sw2-tag sw2-tag--l">Baseline {crops.baseline.scene_date}</span>
            <span className="sw2-tag sw2-tag--r">Now {crops.now.scene_date}</span>
          </div>
          <input className="sw2-range" type="range" min={0} max={100} step={1} value={pos} aria-label="Compare baseline and now"
            aria-valuetext={`${pos}% baseline`} onChange={(e) => setPos(clampSlider(Number(e.target.value)))} />
          <div className="sw2-capgrid">
            <CropCaption role="baseline" crop={crops.baseline} pc={ov.per_crop?.baseline} layer={lBase} cv={cv} noData={noData.baseline} />
            <CropCaption role="now" crop={crops.now} pc={ov.per_crop?.now} layer={lNow} cv={cv} noData={noData.now} />
          </div>
        </div>
      )}
    </div>
  );
}

export function DeltaRow({ ov, noData = NO_CHECK }: { ov: Overlay; noData?: CropNoData }) {
  const cv = countsView(ov);
  const gate = changeGate(noData.now, noData.baseline);
  const pc = providerChip(ov.provider, ov.provider_label);
  const dc = deltaChip(cv);
  if (!ov.served || cv.now == null && cv.baseline == null) {
    return (
      <div className="sw2-row">
        <Chip chip="NOT SERVED" word="Counts not served" title={ov.not_served_reason ?? undefined} />
        <Chip chip="NOT SERVED" word={detectionWord(ov.status.replace(/ /g, '_'))} />
      </div>
    );
  }
  return (
    <div className="sw2-row">
      {pc && <Chip chip="INFO" tone={pc.test ? 'test' : 'meta'} word={pc.text} title={ov.provider_label ?? undefined} />}
      <Chip chip="MODELLED" word="Modelled" title="Not ground truth" />
      <Chip chip="FLAG" word={VERIFICATION_WORD} title="Not reviewed" />
      {cv.status !== 'OK' && <Chip chip="NOT SERVED" word={detectionWord(cv.status)} />}
      {cv.now != null ? <MetaChip>Now {cv.now}</MetaChip> : <Chip chip="NOT SERVED" word="Now not served" />}
      {cv.baseline != null ? <MetaChip>Baseline {cv.baseline}</MetaChip> : <Chip chip="NOT SERVED" word="Baseline not served" />}
      {gate === 'withheld' && dc ? <Chip chip="NOT SERVED" word="Change withheld" title="Crop has no-data" />
        : gate === 'pending' && dc ? null
        : dc ? (
          <>
            <span className={`sw2-delta sw2-delta--${dc.direction}`}>{dc.label}</span>
            {dc.pct && <MetaChip>{dc.pct} vs baseline</MetaChip>}
            {dc.flagged && <Chip chip="FLAG" word="Change ≥10%" />}
          </>
        ) : <Chip chip="NOT SERVED" word="Δ Not served" />}
      {cv.basisLabel && <MetaChip title="Count basis">{cv.basisLabel}</MetaChip>}
      {cv.frameNow != null && <MetaChip>Frame now {cv.frameNow}</MetaChip>}
      {cv.frameBaseline != null && <MetaChip>Frame baseline {cv.frameBaseline}</MetaChip>}
      {cv.countable && <MetaChip title={cv.countable}>Floor · ≥150 m LOA</MetaChip>}
    </div>
  );
}

const BRIEF_DID = 'Brief written';
function BriefBlock({ brief, withheld }: { brief: BriefDoc; withheld: boolean }) {
  const mc = metaChip(brief.provenance);
  useEffect(() => {
    recordAiCall({ surface: 'brief', did: BRIEF_DID, provenance: brief.provenance, key: `brief:${brief.node_id}:${brief.provenance?.prompt_id ?? ''}:${brief.intel.imagery_dates.now}` });
  }, [brief]);
  const i = brief.intel;
  const pc = providerChip(i.provider, i.provider_label);
  return (
    <div className="sw2-brief">
      <div className="sw2-row">
        <span className="rk-lbl">Brief</span>
        {metaChipView(parseProvenance(brief.provenance)) ? <AiBadge provenance={brief.provenance} did={BRIEF_DID} />
          : <Chip chip="INFO" word={mc.text} tone={mc.tone === 'live' ? 'live' : 'meta'} />}
        {pc && <Chip chip="INFO" tone={pc.test ? 'test' : 'meta'} word={pc.text} />}
        {snap(i.imagery_dates.now)}
        {withheld && i.delta != null ? <Chip chip="NOT SERVED" word="Change withheld" title="Crop has no-data" />
          : i.delta == null ? <Chip chip="NOT SERVED" word="Δ Not served" /> : <MetaChip>Δ {i.delta > 0 ? '+' : ''}{i.delta}</MetaChip>}
      </div>
      <details className="sw2-details">
        <summary>Brief text</summary>
        <pre className="mono">{briefLines(brief).join('\n')}</pre>
      </details>
    </div>
  );
}

/* ── the panel ───────────────────────────────────────────────────── */
export function SatelliteDeskPanel() {
  const [places, setPlaces] = useState<Feed<PlacesDoc>>({ s: 'loading' });
  const [detectors, setDetectors] = useState<Feed<DetectorsDoc>>({ s: 'loading' });
  const [signals, setSignals] = useState<Feed<SignalsDoc>>({ s: 'loading' });
  const [sel, setSel] = useState<string | null>(null);
  const [overlay, setOverlay] = useState<OverlayState>({ s: 'loading' });
  const [brief, setBrief] = useState<Feed<BriefDoc | null>>({ s: 'loading' });
  const [view, setView] = useState<View>('side');
  const [pos, setPos] = useState(50);
  const [markers, setMarkers] = useState(true);
  const [enhanced, setEnhanced] = useState(false);
  const [sources, setSources] = useState<Feed<SourcesDoc>>({ s: 'loading' });
  const [aerial, setAerial] = useState<Feed<ScenesDoc>>({ s: 'loading' });
  const aerialRef = useRef<HTMLDivElement>(null);
  const [avail, setAvail] = useState<AvailabilityDoc | 'loading' | 'err'>('loading');
  const [src, setSrc] = useState<SourceId>('sentinel-2');
  const [availTick, setAvailTick] = useState(0);
  const [canScan, setCanScan] = useState(false);
  const [scanTarget, setScanTarget] = useState<'one' | 'all'>('one');
  const [job, setJob] = useState<ScanJob | null>(null);
  const [scanErr, setScanErr] = useState<string | null>(null);

  const loadAll = useCallback(() => {
    const ctrl = new AbortController();
    const wrap = <T,>(p: Promise<T>, set: (f: Feed<T>) => void) =>
      p.then((v) => set({ s: 'ok', v })).catch((e: unknown) => { if ((e as CentreError).code !== 'aborted') set({ s: 'err', code: (e as CentreError).code ?? 'error' }); });
    void wrap(fetchPlaces(ctrl.signal), setPlaces);
    void wrap(fetchAerialScenes(ctrl.signal), setAerial);
    void wrap(fetchDetectors(ctrl.signal), setDetectors);
    void wrap(fetchSources(ctrl.signal), setSources);
    void wrap(fetchSignals(ctrl.signal), setSignals);
    return () => ctrl.abort();
  }, []);
  useEffect(() => loadAll(), [loadAll]);
  /* Default anchorage: one that owns a recorded aerial scene (its SAM boxes show at once); decided once both lists have settled, never over a user pick. */
  useEffect(() => {
    if (sel || places.s !== 'ok' || aerial.s === 'loading') return;
    const ids = placeRows(places.v.places).map((r) => r.id);
    setSel(preferredPlace(ids, aerial.s === 'ok' ? aerial.v.scenes : []));
  }, [sel, places, aerial]);
  useEffect(() => {
    const ctrl = new AbortController();
    void probeScanCapability(ctrl.signal).then((c) => setCanScan(c === 'allowed')).catch(() => setCanScan(false));
    return () => ctrl.abort();
  }, []);

  /* overlay + brief follow the selected anchorage */
  /* a place the places list reports without a stored scan has no overlay or brief to request (the server would answer 404) */
  const unscanned = places.s === 'ok' && !!sel && !places.v.places.find((p) => p.node_id === sel)?.last_scan && job?.status !== 'done';
  useEffect(() => {
    if (!sel) return;
    if (unscanned) { setOverlay({ s: 'ok', ov: null }); setBrief({ s: 'ok', v: null }); return; }
    const ctrl = new AbortController();
    setOverlay({ s: 'loading' }); setBrief({ s: 'loading' });
    fetchOverlay(sel, ctrl.signal).then((ov) => setOverlay({ s: 'ok', ov }))
      .catch((e: unknown) => { if ((e as CentreError).code !== 'aborted') setOverlay({ s: 'err', code: (e as CentreError).code ?? 'error' }); });
    fetchBrief(sel, ctrl.signal).then((v) => setBrief({ s: 'ok', v }))
      .catch((e: unknown) => {
        const code = (e as CentreError).code ?? 'error';
        if (code === 'aborted') return;
        setBrief(code === 'scan_not_found' ? { s: 'ok', v: null } : { s: 'err', code });
      });
    return () => ctrl.abort();
  }, [sel, unscanned, job?.status === 'done' ? job?.job_id : null]);

  /* per-place source availability (live catalogue lookup; stored snapshots ride along) */
  useEffect(() => {
    if (!sel) return;
    const ctrl = new AbortController();
    /* Stored products first (answers at once), then the live catalogue lookup replaces them; a lookup that fails keeps the stored answer. */
    setAvail('loading');
    let stored = false;
    fetchAvailability(sel, ctrl.signal, false).then((d) => { stored = true; setAvail((cur) => (cur === 'loading' || cur === 'err' ? d : cur)); })
      .catch(() => { /* the live lookup below still settles the state */ });
    fetchAvailability(sel, ctrl.signal, true).then(setAvail)
      .catch((e: unknown) => { if ((e as CentreError).code !== 'aborted' && !stored) setAvail('err'); });
    return () => ctrl.abort();
  }, [sel, availTick]);
  const refreshAvail = useCallback(() => setAvailTick((t) => t + 1), []);

  /* scan job polling: every JOB_POLL_MS while queued / running */
  const jobId = job?.job_id ?? null; const active = jobActive(job);
  useEffect(() => {
    if (!jobId || !active) return;
    const ctrl = new AbortController();
    const t = window.setTimeout(() => {
      fetchJob(jobId, ctrl.signal).then((j) => {
        setJob(j);
        if (!jobActive(j)) loadAll();
      }).catch((e: unknown) => { if ((e as CentreError).code !== 'aborted') setScanErr((e as CentreError).code ?? 'error'); });
    }, JOB_POLL_MS);
    return () => { window.clearTimeout(t); ctrl.abort(); };
  }, [jobId, active, job, loadAll]);

  const scan = async () => {
    if (!sel && scanTarget === 'one') return;
    setScanErr(null);
    try { setJob(await startScan(scanTarget === 'all' ? 'all' : (sel as string))); } catch (e) { setScanErr((e as CentreError).code ?? 'error'); }
  };

  const rows = useMemo(() => (places.s === 'ok' ? placeRows(places.v.places) : []), [places]);
  const aerialScenes = aerial.s === 'ok' ? aerial.v.scenes : [];
  const hasAerial = !!sceneForPlace(aerialScenes, sel);
  /* A place that owns an aerial scene brings its section into view (the section sits directly under the title row; this covers a scrolled panel). */
  const pickPlace = (id: string) => {
    setSel(id);
    if (sceneForPlace(aerialScenes, id)) window.setTimeout(() => aerialRef.current?.scrollIntoView?.({ block: 'nearest', behavior: scrollBehavior() }), 0);
  };
  useSatDeskExportables(places.s === 'ok' ? places.v.places : null, overlay.s === 'ok' ? overlay.ov : null);
  const selRow = rows.find((r) => r.id === sel) ?? null;
    const sigRows = useMemo(() => (signals.s === 'ok' ? signalRows(signals.v) : []), [signals]);
  const prog = jobProgress(job);
  const scanFail = scanErr ? failureState(scanErr) : null;
  const stamp = places.s === 'ok' ? `${rows.length} anchorages · Snapshot` : places.s === 'err' ? 'Down' : 'Loading';
  const ov = overlay.s === 'ok' ? overlay.ov : null;
  const noData: CropNoData = { now: useNoData(ov?.crops?.now.url), baseline: useNoData(ov?.crops?.baseline.url) };
  const srcChips = useMemo(() => (sources.s === 'ok' ? sourceChips(sources.v, avail) : []), [sources, avail]);
  /* a source the place cannot serve falls back to Sentinel-2 */
  const activeSrc: SourceId = srcChips.find((c) => c.id === src)?.selectable ? src : 'sentinel-2';
  const activeSpec = sources.s === 'ok' ? sources.v.sources.find((x) => x.id === activeSrc) ?? null : null;

  return (
    <Panel id="satdesk" code="SAT" title="Satellite desk" updated={stamp} className="sw2-panel"
      exportSpec={{ json: () => ({ places: places.s === 'ok' ? places.v : null, overlay: ov, detectors: detectors.s === 'ok' ? detectors.v : null }) }}>
      {places.s === 'loading' && <StateLine chip="INFO" word="Loading" />}
      {places.s === 'err' && <StateLine chip={failureState(places.code).chip} word={failureState(places.code).word} code={places.code} onRetry={loadAll} />}
      {places.s === 'ok' && (
        <div className="sw2-root">
          <div className="sw2-head">
            {detectors.s === 'ok' ? <DetectorStrip doc={detectors.v} />
              : detectors.s === 'err' ? <><span className="rk-lbl">Detector</span><Chip chip="NOT SERVED" word="Detector state not served" title={detectors.code} /></> : null}
            <MetaChip>Sentinel-2 L2A · 10 m/px</MetaChip>
            {activeSrc === 'sentinel-2' && <MetaChip>Contains modified Copernicus Sentinel data</MetaChip>}
            <span className="rk-spacer" />
            {canScan && (
              <span className="sw2-scan" role="group" aria-label="Scan control">
                <select className="rk-input" value={scanTarget} onChange={(e) => setScanTarget(e.target.value as 'one' | 'all')} aria-label="Scan target" disabled={active}>
                  <option value="one">This anchorage</option>
                  <option value="all">All {rows.length}</option>
                </select>
                <button type="button" className="rk-btn rk-btn--primary" onClick={scan} disabled={active || (scanTarget === 'one' && !sel)}>
                  {active ? 'Scanning' : 'Scan'}
                </button>
              </span>
            )}
          </div>
          {job && (
            <div className="sw2-job" role="status">
              <div className="sw2-bar" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={prog.pct} aria-label="Scan progress">
                <i style={{ width: `${prog.pct}%` }} />
              </div>
              <MetaChip title={job.job_id}>Scan job</MetaChip>
              <span className="mono sw2-count">{prog.label}</span>
              {job.items.map((it) => (
                <Chip key={it.node_id} chip={it.status === 'failed' ? 'DOWN' : it.status === 'degraded' ? 'NOT SERVED' : it.status === 'done' ? 'SNAPSHOT' : 'INFO'}
                  word={`${rows.find((x) => x.id === it.node_id)?.name ?? labelWord(it.node_id.replace(/^(sea|chk)_/, ''))} · ${labelWord(it.status)}`} title={it.error ?? undefined} tone={it.status === 'done' ? 'meta' : undefined} />
              ))}
            </div>
          )}
          {scanFail && <StateLine chip={scanFail.chip} word={scanFail.word} code={scanFail.code} />}

          <div className="sw2-body">
            <nav className="sw2-places" aria-label="Anchorages">
              <ul>
                {rows.map((r) => (
                  <li key={r.id}>
                    <button type="button" className={`sw2-place${sel === r.id ? ' on' : ''}`} aria-pressed={sel === r.id} onClick={() => pickPlace(r.id)}>
                      <span className="sw2-pname">{r.name}</span>
                      <span className="sw2-pmeta">
                        {r.scan === 'NOT SCANNED' ? <Chip chip="NOT SERVED" word="Not scanned" /> : <Chip chip="SNAPSHOT" word={r.now ? `Snapshot ${r.now.slice(5)}` : 'Snapshot'} />}
                        {r.scan === 'PAIR NOT USABLE' && <Chip chip="NOT SERVED" word="Pair not usable" />}
                        {!r.curated && <Chip chip="NOT SERVED" word="Derived crop · Not verified" tone="meta" />}
                        {r.detection && <span className="sw2-det">{detectionWord(r.detection)}</span>}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </nav>

            <div className="sw2-main">
              <div className="sw2-row sw2-title">
                <b>{selRow?.name ?? '—'}</b>
                {selRow && (selRow.curated ? <MetaChip>Curated</MetaChip> : <Chip chip="NOT SERVED" tone="meta" word="Derived crop · Not verified" />)}
                <span className="rk-spacer" />
                <span className="rk-tabs" role="group" aria-label="Compare mode">
                  <button type="button" className={`rk-tab${view === 'side' ? ' on' : ''}`} aria-pressed={view === 'side'} onClick={() => setView('side')}>Side by side</button>
                  <button type="button" className={`rk-tab${view === 'slider' ? ' on' : ''}`} aria-pressed={view === 'slider'} onClick={() => setView('slider')}>Slider</button>
                </span>
                <button type="button" className={`rk-btn${markers ? ' rk-btn--primary' : ''}`} aria-pressed={markers} onClick={() => setMarkers((m) => !m)}>
                  {markers ? 'Detections on' : 'Detections off'}
                </button>
                <button type="button" className={`rk-btn${enhanced ? ' rk-btn--primary' : ''}`} aria-pressed={enhanced} onClick={() => setEnhanced((e) => !e)}>
                  {enhanced ? 'Enhanced view on' : 'Enhanced view off'}
                </button>
                {enhanced && <><SynthChip inline /><MetaChip>Counts · Original pixels</MetaChip></>}
              </div>

              {hasAerial && <div ref={aerialRef} className="sw2-av-anchor"><AerialDesk doc={aerial} place={sel} onRetry={loadAll} /></div>}

              {sources.s === 'ok' && (
                <SourceStrip chips={srcChips} specs={sources.v} active={activeSrc} onSelect={setSrc} />
              )}

              {activeSrc !== 'sentinel-2' && sel && (
                <SourceViewer source={activeSrc} place={sel} spec={activeSpec} avail={avail} canScan={canScan} markers={markers} enhanced={enhanced} onChanged={refreshAvail} />
              )}

              {activeSrc === 'sentinel-2' && overlay.s === 'loading' && <StateLine chip="INFO" word="Loading" />}
              {activeSrc === 'sentinel-2' && overlay.s === 'err' && <StateLine chip={failureState(overlay.code).chip} word={failureState(overlay.code).word} code={overlay.code} />}
              {activeSrc === 'sentinel-2' && overlay.s === 'ok' && !ov && <StateLine chip="NOT SERVED" word="Not scanned" code="scan_not_found" />}
              {activeSrc === 'sentinel-2' && ov && (
                <>
                  <DeltaRow ov={ov} />
                  {ov.served && ov.detections.length === 0 && <StateLine chip="NOT SERVED" word="No positions served" />}
                  {!ov.served && <div className="sw2-row"><Chip chip="NOT SERVED" word="Detection overlay not served" title={ov.not_served_reason ?? undefined} /></div>}
                  {!ov.usable_pair && <div className="sw2-row"><Chip chip="NOT SERVED" word="Pair not usable" /></div>}
                  <CompareView ov={ov} view={view} pos={pos} setPos={setPos} markers={markers && ov.served} enhanced={enhanced} noData={noData} />
                </>
              )}
              {activeSrc === 'sentinel-2' && ov?.crops && sel && ov.node_id === sel && (
                <MetaAiCard source="sentinel-2" place={sel} hasNow={!!ov.crops.now} hasBaseline={!!ov.crops.baseline} />
              )}
              {activeSrc !== 'sentinel-2' && sel && activeSrc !== 'sentinel-1' && <MetaAiCard source={activeSrc} place={sel} />}
              {activeSrc === 'sentinel-2' && brief.s === 'ok' && brief.v && <BriefBlock brief={brief.v} withheld={changeGate(noData.now, noData.baseline) === 'withheld'} />}
              {activeSrc === 'sentinel-2' && brief.s === 'err' && <StateLine chip={failureState(brief.code).chip} word="Brief not served" code={brief.code} />}
            </div>
          </div>

          {!hasAerial && <AerialDesk doc={aerial} place={sel} onRetry={loadAll} />}

          {signals.s === 'ok' && sigRows.length > 0 && (
            <div className="sw2-signals">
              <div className="sw2-row"><span className="rk-lbl">Signals</span><MetaChip>Dry run</MetaChip></div>
              <table className="rk-table">
                <thead><tr><th scope="col">Anchorage</th><th scope="col">Signal</th><th scope="col">Reasons</th><th scope="col">Provider</th></tr></thead>
                <tbody>
                  {sigRows.map((r) => (
                    <tr key={r.nodeId} className={sel === r.nodeId ? 'on' : ''}>
                      <td><button type="button" className="sw2-link" onClick={() => pickPlace(r.nodeId)}>{rows.find((x) => x.id === r.nodeId)?.name ?? r.nodeId}</button></td>
                      <td>{r.wouldMint ? <Chip chip="MODELLED" word="Would raise" /> : <Chip chip="NOT SERVED" word="None" />}</td>
                      <td className="sw2-reasons">{r.reasons.length === 0 ? <span className="mono">—</span> : r.reasons.map((x) => <MetaChip key={x}>{x}</MetaChip>)}</td>
                      <td>{r.providerLabel ? <Chip chip="INFO" tone={r.providerLabel.startsWith('REFERENCE COUNTS') ? 'test' : 'meta'} word={displayLabel(r.providerLabel)} /> : <Chip chip="NOT SERVED" />}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </Panel>
  );
}
