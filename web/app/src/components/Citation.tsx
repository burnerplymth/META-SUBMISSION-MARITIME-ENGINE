import type { MouseEvent } from 'react';
import { citationDate, citationKindLabel, shorten, type Citation as CitationData } from '../citation';

/**
 * A quiet source citation: a tiny kind word (Alert / Report / Article /
 * Portal), the publisher and a shortened headline as ONE link to the actual
 * page (new tab, rel=noopener noreferrer), then the domain and the date.
 * Renders nothing without a citation: an item with no human-readable page
 * shows only its provenance chip. The href is the already-validated https URL
 * (parseCitation); the full headline rides in the tooltip.
 */
export function Citation({ c, titleMax = 84, onClick }: {
  c: CitationData | null | undefined;
  titleMax?: number;
  onClick?: (e: MouseEvent) => void;
}) {
  if (!c) return null;
  const when = citationDate(c.published_at);
  return (
    <div className="cite">
      <a className="cite-link" href={c.url} target="_blank" rel="noopener noreferrer" title={`${c.publisher} \u2014 ${c.title}`} onClick={onClick}>
        <span className="cite-pub">{c.publisher}</span>
        <span className="cite-sep"> {'\u00b7'} </span>
        <span className="cite-title">{shorten(c.title, titleMax)}</span>
      </a>
      <div className="cite-meta">
        <span className="cite-kind">{citationKindLabel(c.kind)}</span>
        <span className="cite-dom">{c.domain}</span>
        {when && <span className="cite-date">{when}</span>}
      </div>
    </div>
  );
}
