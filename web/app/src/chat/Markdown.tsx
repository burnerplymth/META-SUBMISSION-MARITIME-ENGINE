/**
 * Renders the safe markdown AST (markdown.ts) as React elements. Every string
 * goes in as a text node; links carry rel="noopener noreferrer", open in a new
 * tab and show their domain; citation markers are numbered buttons.
 */
import React from 'react';
import { Status } from '../ui';
import { parseMarkdown, type Block, type Inline } from './markdown';

export interface MarkdownProps {
  text: string;
  nCites?: number;
  /** Paragraph indices (server split) that state web facts without a citation. */
  uncited?: readonly number[];
  onCite?: (n: number) => void;
  className?: string;
}

function Inl({ nodes, onCite }: { nodes: Inline[]; onCite?: (n: number) => void }) {
  return (
    <>
      {nodes.map((n, k) => {
        switch (n.t) {
          case 'text': return <React.Fragment key={k}>{n.s}</React.Fragment>;
          case 'bold': return <strong key={k}><Inl nodes={n.c} onCite={onCite} /></strong>;
          case 'code': return <code key={k} className="ch-code">{n.s}</code>;
          case 'cite':
            return (
              <button key={k} type="button" className="ch-cite" data-cite={n.n} aria-label={`Source ${n.n}`}
                onClick={() => onCite?.(n.n)}>{n.n}</button>
            );
          case 'link':
            return (
              <a key={k} className="ch-link" href={n.href} target="_blank" rel="noopener noreferrer">
                {n.text}<span className="ch-link-d"> ({n.domain})</span>
              </a>
            );
        }
      })}
    </>
  );
}

function Lines({ lines, onCite }: { lines: Inline[][]; onCite?: (n: number) => void }) {
  return <>{lines.map((l, k) => <React.Fragment key={k}>{k > 0 && <br />}<Inl nodes={l} onCite={onCite} /></React.Fragment>)}</>;
}

export function Markdown({ text, nCites = 0, uncited = [], onCite, className = '' }: MarkdownProps) {
  const blocks: Block[] = React.useMemo(() => parseMarkdown(text, nCites), [text, nCites]);
  // the flag follows the last block of a flagged paragraph
  const lastOf = new Map<number, number>();
  blocks.forEach((b, k) => lastOf.set(b.i, k));
  return (
    <div className={`ch-md${className ? ` ${className}` : ''}`}>
      {blocks.map((b, k) => {
        let el: React.ReactNode;
        switch (b.t) {
          case 'p': el = <p><Lines lines={b.lines} onCite={onCite} /></p>; break;
          case 'ul': el = <ul>{b.items.map((it, j) => <li key={j}><Inl nodes={it} onCite={onCite} /></li>)}</ul>; break;
          case 'ol': el = <ol>{b.items.map((it, j) => <li key={j}><Inl nodes={it} onCite={onCite} /></li>)}</ol>; break;
          case 'table':
            el = (
              <div className="ch-table-wrap">
                <table className="ch-table">
                  <thead><tr>{b.head.map((c, j) => <th key={j} scope="col"><Inl nodes={c} onCite={onCite} /></th>)}</tr></thead>
                  <tbody>{b.rows.map((r, j) => <tr key={j}>{r.map((c, x) => <td key={x}><Inl nodes={c} onCite={onCite} /></td>)}</tr>)}</tbody>
                </table>
              </div>
            );
            break;
          case 'pre': el = <pre className="ch-pre">{b.s}</pre>; break;
        }
        return (
          <React.Fragment key={k}>
            {el}
            {lastOf.get(b.i) === k && uncited.includes(b.i) && (
              <div className="ch-uncited"><Status state="snapshot" word="Uncited" /></div>
            )}
          </React.Fragment>
        );
      })}
    </div>
  );
}
