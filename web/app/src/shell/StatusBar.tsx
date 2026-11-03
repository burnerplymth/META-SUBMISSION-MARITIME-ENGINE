import React, { useLayoutEffect, useRef, useState } from 'react';
import { adapterLabel } from '../format';
import { useDeskStore } from '../store';
import { Chip } from './Chip';
import { adapterRowView } from './feedState';
import { StatusMetaChip } from '../meta/StatusMetaChip';
import { PLANNED_TOOLS, type ProbeState, type ToolId } from './tools';
import { wm } from '../windows/controller';

/** What the store's busy token reads as in the bar. */
const BUSY_LABEL: Record<string, string> = { chain: 'Loading chain', run: 'Running scenario', fork: 'Forking run', sim: 'Building simulation' };

/** `LIVE` -> `Live`: the state word as the bar prints it (sentence case). */
const stateWord = (state: string): string => {
  const s = state.toLowerCase();
  return s === 'live' ? 'Live' : s === 'snapshot' ? 'Snapshot' : 'Down';
};

/** How many direct children of `el` wrapped onto a second line (hidden by the
 *  fixed height). The bar never clips half a chip: whole chips go, and the
 *  count shows as `+n`. Re-measured when the bar resizes or the list changes. */
function useWrapped(el: React.RefObject<HTMLElement>, dep: unknown): number {
  const [n, setN] = useState(0);
  useLayoutEffect(() => {
    const node = el.current;
    if (!node) return;
    const measure = () => {
      const kids = [...node.children].filter((c) => !c.classList.contains('src-more')) as HTMLElement[];
      const top = kids[0]?.offsetTop ?? 0;
      setN(kids.filter((k) => k.offsetTop > top + 2).length);
    };
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(measure);
    ro.observe(node);
    if (node.parentElement) ro.observe(node.parentElement);
    return () => ro.disconnect();
  }, [el, dep]);
  return n;
}

/** The status bar (24 px): source-health chips with a drawn mark and a word,
 *  the one Meta AI indicator, the engine and ledger words, the latency and the
 *  clock. A feed that is down keeps its chip here even when its section hides.
 *  Chips that do not fit are not cut in half: they drop off the end and a
 *  `+n` opens the Source health window. */
export function StatusBar({ now, probes }: { now: Date; probes: Partial<Record<ToolId, ProbeState>> }) {
  const s = useDeskStore();
  const downTools = PLANNED_TOOLS.filter((t) => probes[t.id] === 'down');
  const dots = useRef<HTMLSpanElement>(null);
  const adapters = s.health?.adapters ?? [];
  const wrapped = useWrapped(dots, `${adapters.length}:${downTools.length}:${s.health ? 1 : 0}`);
  const views = s.health ? adapters.map((a) => adapterRowView(s.health!, a.adapter, s.snapshotMode)) : [];
  /* A recorded or offline copy (no feed live, none failing) reads as one calm summary, not a row of per-feed chips. */
  const offlineCopy = views.length > 0 && views.every((v) => v.kind !== 'live' && v.kind !== 'down');
  const nSnap = views.filter((v) => v.kind === 'snapshot').length;
  const nNot = views.filter((v) => v.kind === 'notserved').length;
  return (
    <footer className="statusbar status-bar">
      {offlineCopy ? (
        <button type="button" className="src-sum" aria-label="Feeds: open Source health" title="Source health" onClick={() => wm.openWindow('health', 'dock')}>
          <Chip kind="snapshot" className="chip--offline" word="Offline copy" meta={`${nSnap} snapshot${nSnap === 1 ? '' : 's'}`} />
          {nNot > 0 && <Chip kind="notserved" word={`${nNot} not served`} />}
        </button>
      ) : (
      <span className="src-dots" ref={dots}>
        {adapters.map((a, i) => {
          /* red is for a feed that is configured and failing; a feed that is simply not connected reads calm */
          const v = views[i];
          return (
            <span key={a.adapter} data-adapter={a.adapter} data-calm={v.calm ? '1' : undefined}>
              <Chip kind={v.kind} word={`${adapterLabel(a.adapter)} ${v.label}`} className={v.className}
                meta={a.state === 'SNAPSHOT' && a.last_success_at ? a.last_success_at.slice(5, 10) : undefined} />
            </span>
          );
        })}
        {!s.health && <Chip kind="down" word="Source health down" />}
        {downTools.map((t) => <Chip key={t.id} kind="down" word={`${t.code} down`} />)}
      </span>
      )}
      {!offlineCopy && wrapped > 0 && (
        <button type="button" className="src-more" aria-label={`${wrapped} more sources`} onClick={() => wm.openWindow('health', 'dock')}>+{wrapped}</button>)}
      {s.busy && <span className="meta">{BUSY_LABEL[s.busy] ?? 'Working'}</span>}
      <span className="spacer" />
      {s.snapshotMode && <Chip kind="snapshot" word="Data snapshot" meta={s.capturedAt ? s.capturedAt.slice(0, 10) : undefined} />}
      <StatusMetaChip />
      {s.chain && <Chip kind="meta" word="Engine deterministic" />}
      <Chip kind="meta" word={s.ledgerSource === 'live' && s.ledger
        ? `Ledger ${s.ledger.length} locked · ${s.ledger.filter((e) => e.grade).length} graded`
        : s.ledgerSource === 'unreachable' ? 'Ledger down' : 'Ledger loading'} />
      <span className="meta">{s.latencyMs === null ? 'Latency –' : s.latencyMs > 999 ? 'API slow' : `Latency ${s.latencyMs} ms`}</span>
      <span className="meta">{now.toISOString().slice(11, 19)} UTC</span>
    </footer>
  );
}
