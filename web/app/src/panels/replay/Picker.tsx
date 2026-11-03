import React from 'react';
import { Toggle } from '../../ui';
import { Chip } from '../reports/chips';
import { fmtRange, safeText, sourceLabel, splitWord } from './logic';
import type { EpisodeCard } from './types';

/** The episode picker: one card per curated past event (title, dates, one factual line, source badges), and the
 *  switch that adds the route-restriction episodes to the list (off by default). */
export function Picker({ cards, selected, onSelect, restricted, onRestricted }: {
  cards: readonly EpisodeCard[]; selected: string | null; onSelect: (id: string) => void; restricted: boolean; onRestricted: (v: boolean) => void;
}) {
  return (
    <section className="rp-picker" aria-label="Episodes" data-testid="rp-picker">
      <ul className="rp-cards">
        {cards.map((c) => {
          const on = c.id === selected;
          return (
            <li key={c.id}>
              <button type="button" className={`rp-card${on ? ' on' : ''}`} aria-pressed={on} data-episode={c.id} onClick={() => onSelect(c.id)}>
                <span className="rp-card-t">{safeText(c.title)}</span>
                <span className="rp-card-d mono">{fmtRange(c.date_range.start, c.date_range.end)}</span>
                <span className="rp-card-l">{safeText(c.one_liner)}</span>
                <span className="rp-badges">
                  <span className="rp-badge-text">{c.badges.map(sourceLabel).join(' · ')}</span>
                  {c.status.split && <span className="rp-badge-text">{splitWord(c.status.split)}</span>}
                  {c.status.not_blind && <Chip chip="FLAG" word="Not blind" />}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
      <div className="rp-pick-tools"><Toggle checked={restricted} onChange={onRestricted} label="Include route-restriction episodes" /></div>
    </section>
  );
}
