import React from 'react';

/**
 * The SupermAP mark: three dominoes tipping along a
 * baseline, upright, leaning, fallen in the accent colour, on a square
 * tile. Original artwork drawn for this project;
 * it uses no third-party mark. Colours come from tokens through the
 * `ui-logo-*` classes (ui/ui.css), so every skin follows. The same
 * drawing ships as the favicon (web/app/public/favicon.svg, literal
 * colours because a favicon has no stylesheet).
 */
export function Logo({ size = 24, title, className = '' }: { size?: number; title?: string; className?: string }) {
  return (
    <svg className={`ui-logo${className ? ` ${className}` : ''}`} width={size} height={size} viewBox="0 0 24 24"
      role={title ? 'img' : undefined} aria-label={title} aria-hidden={title ? undefined : true} focusable="false" data-logo="dominoes">
      <rect className="ui-logo-tile" x=".5" y=".5" width="23" height="23" rx="3" strokeWidth="1" />
      <g><rect className="ui-logo-fg" x="3.4" y="9.2" width="3.4" height="8.4" rx=".8" /><rect className="ui-logo-cut" x="3.4" y="12.9" width="3.4" height=".8" /></g>
      <g transform="rotate(20 10.6 17.6)"><rect className="ui-logo-fg" x="7.2" y="9.2" width="3.4" height="8.4" rx=".8" /><rect className="ui-logo-cut" x="7.2" y="12.9" width="3.4" height=".8" /></g>
      <g transform="rotate(68 14.4 17.6)"><rect className="ui-logo-acc" x="11" y="9.2" width="3.4" height="8.4" rx=".8" /><rect className="ui-logo-cut" x="11" y="12.9" width="3.4" height=".8" /></g>
      <path className="ui-logo-base" d="M3 19.9h18" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}

/** Wordmark: Superm in the text colour, AP in the brand colour. */
export function Wordmark({ className = '' }: { className?: string }) {
  return <span className={`ui-wordmark${className ? ` ${className}` : ''}`}>Superm<b>AP</b></span>;
}
