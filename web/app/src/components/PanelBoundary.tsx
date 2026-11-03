import React from 'react';

/** Panel-level error boundary. Without
 *  one, a single panel's render throw would unmount the entire app.
 *  A boundary per panel degrades THAT panel to
 *  a plain error note in the desk's own idiom: it says the panel failed
 *  to render, names an error class only, and shows no data in
 *  the failed panel's place — nothing is substituted or invented.
 *  resetKeys let the panel retry when its inputs change (a new run, a
 *  new tab): a boundary that tripped on one payload must not wedge the
 *  panel for the rest of the page's life. */
/** A short code for the failed render: the error class name, never its message. */
export function errorCode(e: Error): string {
  const n = /^[A-Z][A-Za-z]{2,24}$/.test(e.name) ? e.name : 'Error';
  const words = n.replace(/([a-z])([A-Z])/g, '$1 $2').toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export class PanelBoundary extends React.Component<
  { panelLabel: string; resetKeys?: unknown[]; children: React.ReactNode },
  { error: Error | null }
> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error): { error: Error } {
    return { error };
  }

  componentDidUpdate(prevProps: { resetKeys?: unknown[] }): void {
    if (!this.state.error) return;
    const a = prevProps.resetKeys ?? [];
    const b = this.props.resetKeys ?? [];
    if (a.length !== b.length || a.some((v, i) => v !== b[i])) this.setState({ error: null });
  }

  render(): React.ReactNode {
    if (this.state.error) {
      return (
        <div className="honest" role="alert">
          <b>Panel error</b> · {this.props.panelLabel} · Not rendered
          <span className="mono small"> · {errorCode(this.state.error)}</span>
        </div>
      );
    }
    return this.props.children;
  }
}
