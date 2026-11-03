/* Run report of the Research workbench: the episode and its dated events, the aligned series with event markers, each
 * analysis with its sample size and caveat chips, and the scenes with their "Detected, not verified" label. Descriptive
 * statistics on historical data: associations, not causes. A recorded copy says Recorded. Portrait. */
import type { ChartSpec } from '../../charts/types';
import { figureFromSpec } from '../../exports/report/figure';
import { CONTENT_PX, chipOf, type Block, type ReportChip, type ReportDoc } from '../../exports/report/model';
import { chartSpecFor, seriesSpec } from './chartSpecs';
import { datasetLabel, type AnalysisCtx } from './plan';
import { displayDay, toMs } from './time';
import { ANALYSIS_KINDS, DESCRIPTIVE_LABEL, KIND_LABEL, type AnalysisKind, type AnalysisResult, type Assembly, type Episode, type ScanDoc } from './types';
import { caveatWord, errorWord, SCENE_REASON, stateWord, summaryRows } from './vocab';

const W = CONTENT_PX.portrait;
const SNAP = (recorded: boolean): ReportChip => (recorded ? chipOf('Recorded', 'recorded') : chipOf('Snapshot', 'snapshot'));
const DESC = chipOf('Descriptive', 'descriptive');

export interface ResearchReportInput {
  episode: Episode | null; assembly: Assembly; recorded: boolean; ctx: AnalysisCtx;
  results: Partial<Record<AnalysisKind, AnalysisResult | null>>; scan: ScanDoc | null;
}

const seriesLabel = (a: Assembly, key: string): string => {
  const s = a.series.find((x) => x.key === key);
  return s ? `${datasetLabel(s.dataset)}, ${a.places.find((p) => p.id === s.place)?.name ?? (s.place === 'global' ? 'Global' : s.place)}` : key;
};

function analysisBlocks(r: AnalysisResult, recorded: boolean): Block[] {
  const out: Block[] = [];
  const chips: ReportChip[] = [
    chipOf(`n ${r.n ?? 'not served'}${r.nUnit ? ` ${r.nUnit}` : ''}`, 'info'),
    ...(r.kind === 'model_overlay' ? [chipOf('Simulated beside observed', 'simulated')] : []),
    ...r.caveats.map((c) => chipOf(caveatWord(c), 'info')),
    ...(r.ci && r.ci.low !== null && r.ci.high !== null ? [chipOf(`${Math.round((r.ci.level ?? 0.95) * 100)}% interval ${r.ci.low.toFixed(1)} to ${r.ci.high.toFixed(1)}`, 'info')] : []),
    ...(r.hasPointwiseCi ? [chipOf('Interval band', 'info')] : []),
    ...(recorded ? [chipOf('Recorded', 'recorded')] : []),
  ];
  out.push({ t: 'heading', level: 2, text: KIND_LABEL[r.kind], chips: [DESC] });
  if (r.error) { out.push({ t: 'chips', chips: [chipOf(errorWord(r.error.code), 'notserved')] }); return out; }
  out.push({ t: 'chips', chips });
  const rows = summaryRows(r);
  if (rows.length) out.push({ t: 'kv', rows: rows.map((x) => [x.label, x.value]) });
  const spec = chartSpecFor(r, recorded);
  if (spec) out.push(figureFromSpec(spec as ChartSpec, W, 210, { title: KIND_LABEL[r.kind], chips: [SNAP(recorded)] }));
  /* the cover carries the descriptive label; the label each result repeats is not printed again */
  return out;
}

export function researchReport(inp: ResearchReportInput): ReportDoc {
  const { assembly: a, episode, recorded } = inp;
  const blocks: Block[] = [];
  const title = episode?.title ?? a.label ?? 'Research episode';

  blocks.push({ t: 'heading', level: 1, text: 'Episode', chips: [DESC, SNAP(recorded)] });
  if (a.events.length) {
    blocks.push({
      t: 'table', title: 'Dated events', columns: [{ label: '#', align: 'r', w: 0.4 }, { label: 'Date', w: 1.1 }, { label: 'Category', w: 1.2 }, { label: 'Event', w: 4 }, { label: 'Source', w: 1.8 }],
      rows: a.events.map((e, i) => [i + 1, e.t.slice(0, 10), e.category.replace(/_/g, ' '), e.title, e.source.publisher ?? 'Source']),
    });
  } else blocks.push({ t: 'chips', chips: [chipOf('No events in library', 'notserved')] });

  const shown = a.series.filter((s) => s.values.some((v) => v !== null)).slice(0, 6);
  blocks.push({ t: 'heading', level: 1, text: 'Aligned series', chips: [chipOf(`${a.days} d`, 'info'), SNAP(recorded)] });
  for (const s of shown) {
    const spec = seriesSpec(s.key, seriesLabel(a, s.key), s.unit, a.t, s.values, a.events.filter((e) => e.place === s.place || s.place === 'global'), recorded);
    blocks.push(figureFromSpec(spec, W, 150, { title: seriesLabel(a, s.key), chips: [chipOf(stateWord(s.state), s.state === 'not_served' ? 'notserved' : 'info'), SNAP(recorded)],
      caption: `${s.source ?? 'Source not stated'}${s.unit ? `, ${s.unit}` : ''} · Marks are the dated events` }));
  }
  const missing = a.series.filter((s) => s.state === 'not_served');
  if (missing.length) blocks.push({ t: 'chips', chips: missing.map((s) => chipOf(`${seriesLabel(a, s.key)}: not served`, 'notserved')) });

  blocks.push({ t: 'heading', level: 1, text: 'Analyses', chips: [DESC] });
  let any = false;
  for (const k of ANALYSIS_KINDS) {
    const r = inp.results[k];
    if (!r) continue;
    any = true;
    blocks.push(...analysisBlocks(r, recorded));
  }
  if (!any) blocks.push({ t: 'chips', chips: [chipOf('No result', 'notserved')] });

  blocks.push({ t: 'heading', level: 1, text: 'Scenes', chips: [chipOf(String(a.scenes.length), 'info'), chipOf('Detected, not verified', 'detected')] });
  if (a.scenes.length === 0) blocks.push({ t: 'chips', chips: [chipOf('No scenes', 'notserved')] });
  else {
    blocks.push({
      t: 'table', columns: [{ label: 'Date', w: 1.1 }, { label: 'Source', w: 1 }, { label: 'Cloud %', align: 'r', w: 0.9 }, { label: 'Scene', w: 1.3 }, { label: 'SAM result', w: 3 }],
      rows: a.scenes.map((s) => {
        const sam = inp.scan?.results[s.sceneId]; const nc = inp.scan?.notCounted[s.sceneId];
        return [s.t, s.source ?? '', s.cloudPct === null ? 'Not served' : s.cloudPct.toFixed(1), s.usable ? { text: 'Usable', chip: 'sourced' as const } : { text: 'Not counted', chip: 'notserved' as const },
          sam ? `${sam.text}. Modelled, ${recorded || sam.recorded ? 'recorded' : 'detected'}` : !s.usable ? 'Cloud above limit' : nc ? SCENE_REASON[nc] ?? 'Not counted' : inp.scan ? 'No recorded reply' : 'Not served'];
      }),
      note: 'Counts are detected, not verified.',
    });
  }
  if (a.tracks.length) blocks.push({ t: 'chips', chips: [chipOf(`AIS sample, ${a.tracks.length} vessels`, 'info')] });

  const win = episode ? `${displayDay(toMs(episode.from) as number)} to ${displayDay(toMs(episode.to) as number)}` : `${a.from} to ${a.to}`;
  return {
    id: 'research:report', title: `Research: ${title}`, windowTitle: 'Research workbench', orientation: 'portrait', label: recorded ? 'Recorded' : 'Snapshot',
    chips: [SNAP(recorded), DESC, chipOf('Detected, not verified', 'detected')], source: 'Historical archive, descriptive analyses', runId: a.id, asOf: a.to,
    labelText: DESCRIPTIVE_LABEL,
    meta: [['Episode', title], ['Window', win], ['Places', a.places.map((p) => p.name).join(', ') || 'Not served'], ['Series', String(a.series.length)], ['Events', String(a.events.length)], ['Scenes', String(a.scenes.length)], ['Assembly', a.id], ['Copy', recorded ? 'Recorded' : 'Cached']],
    blocks,
  };
}

export { ANALYSIS_KINDS };
