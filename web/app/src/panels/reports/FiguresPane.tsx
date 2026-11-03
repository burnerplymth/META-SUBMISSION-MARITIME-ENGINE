import React, { useMemo, useState } from 'react';
import { API_BASE } from '../../api';
import { Chip, MetaChip, StateLine } from './chips';
import { CentreError } from './client';
import { saveFigure, type Done } from './exportActions';
import { failureState } from './model';
import { figurePath, figureReady, type FigureExt, type ServerFigure } from './serverCharts';
import type { ServerChartsState } from './useServerCharts';
import { labelWord } from '../../copy';
import { Icon } from '../../ui/Icon';

/* FIGURES (chart studio): the server's own figures for this run, drawn
 * by the exports route, each with its chip, a thumbnail and one-click
 * SVG / PNG / CSV / JSON. A PNG the server cannot render is drawn here
 * from the server's SVG and says so. */

const skinOf = (): 'light' | 'dark' => (typeof document !== 'undefined' && /dark/.test(document.documentElement.dataset.theme ?? 'dark') ? 'dark' : 'light');

export function FiguresPane({ state, runId, simRunId, onDone, onOpenData }: {
  state: ServerChartsState; runId: string | null; simRunId: string | null; onDone: (d: Done) => void; onOpenData: (datasetId: string) => void;
}) {
  const [theme, setTheme] = useState<'light' | 'dark' | 'auto'>(skinOf);
  const [busy, setBusy] = useState<string | null>(null);
  const [word, setWord] = useState<{ ok: boolean; text: string } | null>(null);
  const cat = state.s === 'served' ? state.cat : null;
  const flash = (ok: boolean, text: string) => { setWord({ ok, text }); setTimeout(() => setWord(null), 4200); };
  const groups = useMemo(() => {
    const out = new Map<string, ServerFigure[]>();
    for (const f of cat?.figures ?? []) { const k = f.dataset ?? 'other'; out.set(k, [...(out.get(k) ?? []), f]); }
    return [...out.entries()];
  }, [cat]);

  if (state.s === 'checking') return <StateLine chip="INFO" word="Loading" />;
  if (state.s !== 'served') return <div className="rk-state"><Chip chip={state.s === 'down' ? 'DOWN' : 'NOT SERVED'} word={state.s === 'down' ? 'Figures down' : 'Server figures not served'} /><span className="rk-code mono">{state.code}</span></div>;

  const get = async (f: ServerFigure, ext: FigureExt) => {
    const key = `${f.id}:${ext}`;
    setBusy(key);
    try {
      const d = await saveFigure(f, ext, { runId, simRunId, theme, scale: 2 }, cat?.pngAvailable ?? true);
      onDone(d);
      flash(true, d.via === 'browser' ? `SAVED ${d.file} · PNG RENDERED HERE` : `SAVED ${d.file}`);
    } catch (e) { flash(false, `${failureState((e as CentreError).code ?? 'error').word} ${(e as CentreError).code ?? ''}`.trim()); } finally { setBusy(null); }
  };
  const link = async (f: ServerFigure) => {
    try { await navigator.clipboard.writeText(`${location.origin}${API_BASE}${figurePath(f.id, 'svg', { runId, simRunId, theme })}`); flash(true, 'LINK COPIED'); } catch { flash(false, 'COPY BLOCKED'); }
  };

  return (
    <div className="xc-figs">
      <div className="xc-snapbar">
        <MetaChip>{labelWord(cat!.counter)}</MetaChip>
        <label className="rk-row" htmlFor="xc-theme"><span className="rk-lbl">Theme</span>
          <select id="xc-theme" className="rk-input rk-input--num" value={theme} onChange={(e) => setTheme(e.target.value as 'light' | 'dark' | 'auto')}>
            {cat!.themes.map((t) => <option key={t} value={t}>{labelWord(t)}</option>)}
          </select></label>
        {cat!.pngAvailable ? <Chip chip="LIVE" word="PNG served" /> : <Chip chip="NOT SERVED" word="PNG not served · Rendered here" title={cat!.pngReason ?? undefined} />}
        {runId ? <MetaChip title={runId}>Run {runId}</MetaChip> : <Chip chip="NOT SERVED" word="No run" />}
        {word && <span className={`cx-status ${word.ok ? 'ok' : 'bad'}`} role="status">{word.text}</span>}
      </div>
      {groups.map(([dataset, list]) => (
        <section key={dataset} className="xc-figgroup" aria-label={dataset}>
          <h3 className="xc-figh mono">{dataset}</h3>
          <ul className="xc-figlist">
            {list.map((f) => {
              const ok = figureReady(f);
              return (
                <li key={f.id} className="xc-fig" data-figure={f.id}>
                  <div className="xc-thumb">
                    {ok
                      ? <img src={`${API_BASE}${figurePath(f.id, 'svg', { runId, simRunId, theme })}`} alt={`${f.title}`} loading="lazy" width={280} />
                      : <div className="xc-thumb-off"><Chip chip={f.state} /></div>}
                  </div>
                  <div className="xc-figbody">
                    <span className="xc-title" title={f.what ?? undefined}>{f.title}</span>
                    <div className="rk-row"><Chip chip={f.chip} /><MetaChip>{labelWord(f.type).replace('_', ' ')}</MetaChip>{!ok && f.reason && <span className="rk-code mono" title={f.reason}>{f.reason.slice(0, 40)}</span>}</div>
                    <div className="rk-row" role="group" aria-label={`${f.title} downloads`}>
                      {(['svg', 'png', 'csv', 'json'] as const).map((ext) => (
                        <button key={ext} type="button" className="rk-btn" disabled={!ok || busy === `${f.id}:${ext}`} onClick={() => get(f, ext)}>
                          {busy === `${f.id}:${ext}` ? '…' : labelWord(ext)}{ext === 'png' && !cat!.pngAvailable ? <Icon name="ban" size="xs" label="Not served" /> : null}
                        </button>
                      ))}
                      <button type="button" className="rk-btn" disabled={!ok} onClick={() => link(f)}>Link</button>
                      {f.dataset && <button type="button" className="rk-btn" disabled={!ok} onClick={() => onOpenData(f.dataset as string)}>Data</button>}
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
        </section>
      ))}
    </div>
  );
}
