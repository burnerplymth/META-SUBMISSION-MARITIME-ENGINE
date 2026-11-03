import React, { useCallback, useEffect, useRef, useState } from 'react';
import { API_BASE } from '../api';
import { Chip } from './Chip';
import { useDeskStore } from '../store';
import { adapterFeedState } from './feedState';
import { toolProbeChip } from './truthLabel';
import { Icon } from '../ui/Icon';
import {
  PLANNED_TOOLS, probeTool, summariseDoc, type FetchLike, type ProbeResult, type ToolDef, type ToolId,
} from './tools';

export type ProbeMap = Record<ToolId, ProbeResult>;
const CHECKING: ProbeResult = { state: 'checking', doc: null };
const initial = (): ProbeMap => Object.fromEntries(PLANNED_TOOLS.map((t) => [t.id, CHECKING])) as ProbeMap;

/** GET-probe every planned tool's route once at mount (and again on
 *  demand). Never POSTs, never throws; a failure is a state. */
export function useToolProbes() {
  const [map, setMap] = useState<ProbeMap>(initial);
  const alive = useRef(true);
  const run = useCallback((tools: readonly ToolDef[]) => {
    if (typeof fetch !== 'function') return;
    for (const t of tools) {
      void probeTool(API_BASE, t.route, fetch as unknown as FetchLike).then((r) => {
        if (alive.current) setMap((m) => ({ ...m, [t.id]: r }));
      });
    }
  }, []);
  useEffect(() => { alive.current = true; run(PLANNED_TOOLS); return () => { alive.current = false; }; }, [run]);
  return { probes: map, reprobe: (t: ToolDef) => run([t]) };
}

/** Plain rows for a served answer: a sentence-case label for each key and only
 *  counts, numbers and yes or no. Server strings (ids, paths, hashes) and raw
 *  keys are never printed (COPY-STYLE). */
export function plainRows(doc: unknown): { key: string; label: string; value: string }[] {
  const out: { key: string; label: string; value: string }[] = [];
  for (const r of summariseDoc(doc)) {
    let value: string | null = null;
    if (/^-?\d+(\.\d+)?$/.test(r.value)) value = Number(r.value).toLocaleString('en-US');
    else if (/^\d+ (items|fields)$/.test(r.value)) value = r.value.replace(/^(\d+) items$/, (_m, n: string) => `${Number(n).toLocaleString('en-US')} ${n === '1' ? 'item' : 'items'}`).replace(/ fields$/, (m) => (r.value.startsWith('1 ') ? ' field' : m));
    else if (r.value === 'true') value = 'Yes';
    else if (r.value === 'false') value = 'No';
    if (value === null) continue;
    const words = r.key.replace(/[_-]+/g, ' ').replace(/([a-z])([A-Z])/g, '$1 $2').trim().toLowerCase();
    if (!words || /\b(id|ids|hash|sha|path|url|uri|key|token)\b/.test(words)) continue;
    out.push({ key: r.key, label: words.charAt(0).toUpperCase() + words.slice(1), value });
  }
  return out;
}

/** A tool panel whose backend route is provided separately. Served shows the
 *  real answer's own fields; Not served shows the hatched
 *  chip alone, never the route. A served route is not a live feed: the header
 *  chip comes from the feed state through shell/truthLabel.ts. */
export function ToolSlot({ tool, probe, onClose }: { tool: ToolDef; probe: ProbeResult; onClose: () => void }) {
  const s = useDeskStore();
  const feed = tool.adapters ? adapterFeedState(s.health, tool.adapters, s.snapshotMode).word : null;
  const head = toolProbeChip(probe.state, feed);
  const rows = probe.state === 'served' ? plainRows(probe.doc) : [];
  return (
    <section className="panel tool-slot" data-panel={`tool-${tool.id}`}>
      <header className="panel-head">
        <span className="code-chip">{tool.code}</span>
        <span className="panel-title">{tool.title}</span>
        <span className="panel-updated mono">
          <Chip kind={head.kind} word={head.word} />
        </span>
        <span className="panel-controls"><button type="button" className="icon-btn" title="Close" aria-label="Close" onClick={onClose}><Icon name="close" /></button></span>
      </header>
      <div className="panel-body">
        {probe.state === 'served' && (
          <dl className="kv mono">
            {rows.map((r) => <React.Fragment key={r.key}><dt>{r.label}</dt><dd>{r.value}</dd></React.Fragment>)}
          </dl>
        )}
        {probe.state !== 'served' && (
          <div className="empty-state"><Chip kind={probe.state === 'down' ? 'down' : probe.state === 'notserved' ? 'notserved' : 'meta'}
            word={probe.state === 'checking' ? 'Checking' : undefined} /></div>
        )}
      </div>
    </section>
  );
}
