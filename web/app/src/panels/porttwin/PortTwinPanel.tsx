import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Panel } from '../../components/Panel';
import { useStore } from '../../store';
import { Button, InspectorSection, SegmentedControl } from '../../ui';
import { useAsync } from '../ports/useAsync';
import { Tag, Tags } from './chips';
import { ComparePanel, KpiSection } from './Kpis';
import { LayersSection, PortPicker, ScenarioSection, type LookState } from './Controls';
import { MapView } from './MapView';
import { Transport } from './Transport';
import { computeGroups, computeLayout } from './berths';
import {
  EMPTY_BUNDLE, feedWord, loadBaselineFrames, mergeBundle, runBundle, type SimBundle,
} from './bundle';
import {
  fetchGeo, fetchGeoIndex, fetchImagery, fetchProfile, fetchSimProfile, fetchVerify, imageryPngUrl,
} from './client';
import { DEFAULT_VIS, labelCandidates, prepareGeo, toggleLayer, type LayerKey, type LayerVis } from './geo';
import {
  NOT_SERVED, geometryChips, groupChips, imageryChips, simChips, statusCounts, structureChips, tierChips, type ChipSpec,
} from './labels';
import { compareLines } from './kpi';
import { createPlayhead } from './playhead';
import { ctxFrom, defaultForm, toItem, type ScenarioForm } from './scenario';
import type { SimRequest, VerifyDoc } from './types';

const DEFAULT_PLACE = 'sea_rotterdam';

export function PortTwinPanel() {
  const s = useStore();
  const linked = s.selectedNode && s.selectedNode.startsWith('sea_') ? s.selectedNode : null;
  const [placeId, setPlaceId] = useState<string>(linked ?? DEFAULT_PLACE);
  useEffect(() => { if (linked && linked !== placeId) setPlaceId(linked); /* eslint-disable-next-line */ }, [linked]);
  const pick = useCallback((id: string) => { setPlaceId(id); s.selectNode(id); /* eslint-disable-next-line */ }, [s.selectNode]);

  /* ── feeds ─────────────────────────────────────────────────────── */
  const index = useAsync((sig) => fetchGeoIndex(sig), 'index');
  const geo = useAsync((sig) => fetchGeo(placeId, sig), `geo:${placeId}`);
  const geoOk = !!geo.data && !geo.error;
  const imagery = useAsync((sig) => fetchImagery(placeId, sig), `img:${placeId}`, geoOk);
  const profile = useAsync((sig) => fetchProfile(placeId, sig), `prof:${placeId}`, geoOk);
  const simProfile = useAsync((sig) => fetchSimProfile(placeId, sig), `simprof:${placeId}`);

  const notServed = geo.error?.status === 404;
  const geoDown = !!geo.error && !notServed;

  /* ── geometry ──────────────────────────────────────────────────── */
  const prep = useMemo(() => (geo.data && !geo.error ? prepareGeo(geo.data) : null), [geo.data, geo.error]);
  const labelCands = useMemo(() => (prep ? labelCandidates(prep) : []), [prep]);
  const groups = useMemo(() => (prep ? computeGroups(prep) : null), [prep]);

  /* ── look ──────────────────────────────────────────────────────── */
  const [vis, setVis] = useState<LayerVis>(DEFAULT_VIS);
  const [look, setLook] = useState<LookState>({ colour: true, glow: true, labels: true, vessels: true });
  const [imageOn, setImageOn] = useState(false);
  const [imageOpacity, setImageOpacity] = useState(0.85);

  /* ── simulation ────────────────────────────────────────────────── */
  const playhead = useRef(createPlayhead(0)).current;
  const [days, setDays] = useState(7);
  const [withRange, setWithRange] = useState(true);
  const ctx = useMemo(() => ctxFrom(simProfile.data, days), [simProfile.data, days]);
  const [form, setForm] = useState<ScenarioForm>(() => defaultForm('surge', ctxFrom(null, 7)));
  const [bundle, setBundle] = useState<SimBundle>(EMPTY_BUNDLE);
  const abort = useRef<AbortController | null>(null);
  const [viewRun, setViewRun] = useState<'scenario' | 'baseline'>('scenario');
  const [verify, setVerify] = useState<VerifyDoc | 'down' | null>(null);

  // a new port resets the run; the form takes that port's defaults
  useEffect(() => {
    abort.current?.abort(); abort.current = null;
    setBundle(EMPTY_BUNDLE); setViewRun('scenario'); setVerify(null); playhead.set(0);
  }, [placeId, playhead]);
  useEffect(() => {
    if (!simProfile.data) return;
    setForm((f) => {
      const c = ctxFrom(simProfile.data, days);
      const still = c.groups.some((g) => g.id === f.group);
      return f.type === 'berth_closure' && !still ? defaultForm('berth_closure', c) : f;
    });
  }, [simProfile.data, days]);
  useEffect(() => () => abort.current?.abort(), []);

  const start = useCallback((scenario: SimRequest['scenario']) => {
    abort.current?.abort();
    const ctl = new AbortController(); abort.current = ctl;
    const req: SimRequest = { days, variant: 'base', arrival_shape: 'even', warmup_days: 2, scenario };
    playhead.set(0); setViewRun('scenario'); setVerify(null);
    void runBundle(placeId, req, (patch) => setBundle((b) => mergeBundle(b, patch)), { withVariants: withRange, signal: ctl.signal });
  }, [days, placeId, withRange, playhead]);
  const onRun = useCallback(() => start([toItem(form, ctx)]), [start, form, ctx]);
  const onBaseline = useCallback(() => start([]), [start]);

  // the baseline's frames load when the map is switched to it
  useEffect(() => {
    if (viewRun !== 'baseline' || !bundle.compare || bundle.baseFrames) return undefined;
    const ctl = new AbortController();
    loadBaselineFrames(placeId, bundle.compare.baseline_id, bundle.run?.journal.days ?? days, undefined, ctl.signal)
      .then((f) => setBundle((b) => ({ ...b, baseFrames: f })), () => setViewRun('scenario'));
    return () => ctl.abort();
  }, [viewRun, bundle.compare, bundle.baseFrames, placeId, bundle.run, days]);

  const frames = viewRun === 'baseline' && bundle.baseFrames ? bundle.baseFrames : bundle.frames;
  const layout = useMemo(() => (prep && frames ? computeLayout(prep, frames.berths, frames.anchorage_slots, groups ?? undefined) : null), [prep, frames, groups]);
  const endH = frames ? frames.to / 3600 : (bundle.run?.journal.days ?? days) * 24;
  const loaScale = 1; // registry class lengths as given; the profile's loa_scale rescales berth lengths, not hulls
  const scenario = bundle.run?.journal.scenario ?? [];
  const viewScenario = viewRun === 'baseline' ? [] : scenario;

  const doVerify = useCallback(() => {
    if (!bundle.run) return;
    fetchVerify(placeId, bundle.run.sim_id).then(setVerify, () => setVerify('down'));
  }, [bundle.run, placeId]);

  /* ── chips ─────────────────────────────────────────────────────── */
  const ports = index.data?.ports ?? [];
  const mapChips: ChipSpec[] = useMemo(() => {
    const out: ChipSpec[] = [];
    if (geo.data) out.push(...geometryChips(geo.data));
    if (bundle.run && frames) out.push({ state: 'sim', word: 'SIMULATED', title: bundle.run.sim_id }, { state: 'snapshot', word: 'PROXY', meta: 'slot placement' });
    if (layout && layout.unplacedGroups.length) out.push({ state: 'notserved', word: 'NOT PLACED', meta: `${layout.unplacedGroups.length} groups` });
    const gh = simProfile.data?.profile.geometry_hash;
    if (gh && geo.data && gh !== geo.data.geometry_hash) out.push({ state: 'down', word: 'HASH DIFFERS' });
    return out;
  }, [geo.data, bundle.run, frames, layout, simProfile.data]);
  const headChips: ChipSpec[] = useMemo(() => [
    ...simChips(bundle.run).filter((c) => c.word !== 'SIMULATED'),
    ...(!bundle.run ? [...tierChips(simProfile.data?.grade, simProfile.data?.tier), ...structureChips(profile.data, simProfile.data)] : []),
  ], [bundle.run, simProfile.data, profile.data]);
  const feedDown: ChipSpec[] = [];
  if (index.error) feedDown.push({ state: 'down', word: 'DOWN', meta: 'ports' });
  if (geoDown) feedDown.push({ state: 'down', word: 'DOWN', meta: 'geometry' });
  if (geoOk && imagery.error && imagery.error.status !== 404) feedDown.push({ state: 'down', word: 'DOWN', meta: 'imagery' });
  if (geoOk && imagery.error?.status === 404) feedDown.push({ state: 'notserved', word: 'NOT SERVED', meta: 'imagery' });
  if (geoOk && profile.error) feedDown.push({ state: profile.error.status === 404 ? 'notserved' : 'down', word: profile.error.status === 404 ? 'NOT SERVED' : 'DOWN', meta: 'profile' });
  if (simProfile.error) feedDown.push({ state: 'down', word: feedWord(simProfile.error), meta: 'scenarios' });
  for (const k of Object.keys(bundle.down) as Array<keyof SimBundle['down']>) feedDown.push({ state: 'down', word: bundle.down[k] ?? 'DOWN', meta: k });

  const stamp = bundle.run ? <Tag state="sim" word="Simulated" /> : geo.data ? <Tag state="snapshot" word="Sourced" /> : undefined;
  const params = statusCounts(bundle.run);

  return (
    <Panel id="porttwin" code="PTW" title="Port twin" updated={bundle.run ? 'SIMULATED' : geo.data ? 'SOURCED' : undefined} stamp={stamp}
      exportSpec={{
        json: () => ({ place_id: placeId, run: bundle.run, compare: bundle.compare, label: 'SIMULATED' }),
        csv: bundle.compare ? () => compareLines(bundle.compare!.table).map((l) => ({ measure: l.label, unit: l.unit, baseline: l.baseline, scenario: l.scenario, change: l.delta, direction: l.dir })) : undefined,
      }}>
      <div className="ptw-root" data-place={placeId}>
        <div className="ptw-top">
          <PortPicker placeId={placeId} ports={ports} onPick={pick} chips={headChips} name={simProfile.data?.profile.name} />
          {feedDown.length > 0 && <Tags chips={feedDown} className="ptw-feeds" />}
        </div>
        <div className="ptw-main">
          <div className="ptw-stage">
            {geo.loading && !geo.data && <div className="ptw-empty" aria-busy="true"><Tag state="meta" word="Loading" /></div>}
            {notServed && (
              <div className="ptw-empty" data-state="notserved"><Tag {...NOT_SERVED} meta="geometry" /><span className="ptw-empty-n">{simProfile.data?.profile.name ?? placeId}</span></div>
            )}
            {geoDown && <div className="ptw-empty" data-state="down"><Tag state="down" word="Down" meta="geometry" /></div>}
            {prep && (
              <MapView placeId={placeId} prep={prep} layout={layout} labelCands={labelCands} vis={vis} colour={look.colour} glow={look.glow} labels={look.labels}
                imageUrl={imagery.data ? imageryPngUrl(placeId) : null} imageBounds={imagery.data?.bounds} imageOn={imageOn && !!imagery.data} imageOpacity={imageOpacity}
                frames={frames} vesselsOn={look.vessels} loaScale={loaScale} scenario={viewScenario} playhead={playhead} chips={mapChips} />
            )}
            {bundle.status === 'running' && !bundle.run && <div className="ptw-busy" role="status"><Tag state="sim" word="Running" /></div>}
            {bundle.compare && frames && prep && (
              <div className="ptw-view">
                <SegmentedControl<'scenario' | 'baseline'> label="Map shows" size="sm" value={viewRun} onChange={setViewRun}
                  options={[{ value: 'scenario', label: 'Scenario' }, { value: 'baseline', label: 'Baseline' }]} />
              </div>
            )}
            {bundle.run && (frames || bundle.kpis) && (
              <Transport playhead={playhead} endH={endH} scenario={scenario} divergesH={bundle.compare?.diverges_at_h ?? null}
                kpis={bundle.kpis} baseKpis={bundle.baseKpis} showBaseline={!!bundle.baseKpis} />
            )}
            <div className="ptw-below">
              {bundle.run && !bundle.down.kpis && (
                <KpiSection summary={bundle.kpis?.summary ?? bundle.run.summary} variants={bundle.variants} groups={simProfile.data?.profile.groups ?? []}
                  compare={bundle.compare} rangeDown={!!bundle.down.variants} />
              )}
              {bundle.compare && !bundle.down.compare && <ComparePanel compare={bundle.compare} />}
            </div>
          </div>
          <aside className="ptw-side">
            {prep && (
              <LayersSection vis={vis} onVis={(k: LayerKey) => setVis((v) => toggleLayer(v, k))} counts={prep.counts} hasCoast={prep.hasCoast}
                look={look} onLook={(k, v) => setLook((o) => ({ ...o, [k]: v }))}
                imagery={imagery.data ? { available: true, on: imageOn, onToggle: setImageOn, opacity: imageOpacity, onOpacity: setImageOpacity, chips: imageryChips(imagery.data) } : null} />
            )}
            {simProfile.data && (
              <ScenarioSection ctx={ctx} form={form} onForm={setForm} days={days} onDays={setDays} withRange={withRange} onRange={setWithRange}
                busy={bundle.status === 'running'} error={bundle.error} onRun={onRun} onBaseline={onBaseline} canRun />
            )}
            {(profile.data || bundle.run) && (
              <InspectorSection title="Sources" defaultOpen={false}>
                <div className="ptw-src-list">
                  {params.length > 0 && <Tags chips={params.map((p) => ({ state: 'meta' as const, word: p.word, meta: String(p.n) }))} />}
                  {profile.data && profile.data.berth_groups.slice(0, 40).map((g) => (
                    <div className="ptw-grp" key={g.id}><span>{g.name}</span><small>{g.berth_count_est}</small><Tags chips={groupChips(g)} /></div>
                  ))}
                  {bundle.run && (
                    <div className="ptw-ver">
                      <Button size="sm" variant="secondary" icon="shield-check" onClick={doVerify}>Verify</Button>
                      {verify && verify !== 'down' && <Tag state={verify.identical ? 'model' : 'down'} word={verify.identical ? 'Replay identical' : 'Replay differs'} />}
                      {verify === 'down' && <Tag state="down" word="Down" meta="verify" />}
                    </div>
                  )}
                </div>
              </InspectorSection>
            )}
          </aside>
        </div>
      </div>
    </Panel>
  );
}


