/* The one brief controller of the page: what is being written, the served
 * document, the estimate and the export state. A page singleton (as the
 * agent's), so a brief keeps loading when its window is closed. Framework
 * free; the window reads it with useSyncExternalStore. */
import { useSyncExternalStore } from 'react';
import { recordAiCall } from '../meta/aiCalls';
import { printHtmlAsPdf } from '../exports/printPdf';
import { makeBriefApi, type BriefApi } from './client';
import type { BriefDoc, BriefEstimate, BriefRequest, BriefStatus } from './types';

export type BriefPhase = 'idle' | 'loading' | 'ready' | 'notserved' | 'refused' | 'down';
export type ExportKind = 'html' | 'pdf' | 'json';

export interface BriefState {
  req: BriefRequest | null;
  phase: BriefPhase;
  doc: BriefDoc | null;
  error: string | null;
  status: BriefStatus | null;
  estimate: BriefEstimate | null;
  exporting: { kind: ExportKind; state: 'working' | 'error'; code?: string } | null;
}

const INITIAL: BriefState = { req: null, phase: 'idle', doc: null, error: null, status: null, estimate: null, exporting: null };

export const reqKey = (r: BriefRequest): string => JSON.stringify([r.run_id, r.mc_job_id ?? null, r.lane_plan?.origin ?? null, r.lane_plan?.destination ?? null, r.research_episode_id ?? null]);

export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

export interface BriefDeps {
  api: BriefApi;
  save?: (blob: Blob, filename: string) => void;
  /** Browser print of the HTML document (Save as PDF); defaults to the shared helper. */
  print?: (html: string, label: string) => boolean;
  /** Called once per newly written brief (not for a cached one). */
  onCall?: (doc: BriefDoc) => void;
}

export class BriefController {
  private s: BriefState = INITIAL;
  private subs = new Set<() => void>();
  private token = 0;
  constructor(private deps: BriefDeps) {}

  subscribe = (f: () => void): (() => void) => { this.subs.add(f); return () => { this.subs.delete(f); }; };
  getSnapshot = (): BriefState => this.s;
  private set(p: Partial<BriefState>): void { this.s = { ...this.s, ...p }; this.subs.forEach((f) => f()); }

  async refreshStatus(): Promise<void> {
    const r = await this.deps.api.status();
    this.set({ status: r.kind === 'ok' ? r.doc : null });
  }

  /** Write (or fetch) the brief for a request. The same request is not repeated while it is ready or loading. */
  async request(req: BriefRequest, opts: { regenerate?: boolean } = {}): Promise<void> {
    const same = this.s.req !== null && reqKey(this.s.req) === reqKey(req);
    if (same && !opts.regenerate && (this.s.phase === 'ready' || this.s.phase === 'loading')) return;
    const my = ++this.token;
    this.set({ req, phase: 'loading', error: null, exporting: null, doc: same ? this.s.doc : null, estimate: same ? this.s.estimate : null });
    const r = await this.deps.api.write(req, opts.regenerate === true);
    if (my !== this.token) return;
    if (r.kind === 'ok') {
      this.set({ phase: 'ready', doc: r.doc, error: null });
      if (r.doc.cached !== true) this.deps.onCall?.(r.doc);
      void this.refreshEstimate();
    } else if (r.kind === 'notserved') this.set({ phase: 'notserved', doc: null });
    else if (r.kind === 'refused') this.set({ phase: 'refused', error: r.code, doc: null });
    else this.set({ phase: 'down', error: r.code, doc: null });
  }

  regenerate(): Promise<void> {
    return this.s.req ? this.request(this.s.req, { regenerate: true }) : Promise.resolve();
  }

  /** Open a stored brief by id (the agent's open_brief). */
  async loadById(id: string): Promise<boolean> {
    const my = ++this.token;
    this.set({ phase: 'loading', error: null, exporting: null, doc: null, estimate: null });
    const r = await this.deps.api.get(id);
    if (my !== this.token) return false;
    if (r.kind !== 'ok') {
      if (r.kind === 'notserved') this.set({ phase: 'notserved', error: null, doc: null });
      else this.set({ phase: r.kind === 'refused' ? 'refused' : 'down', error: r.code, doc: null });
      return false;
    }
    this.set({ phase: 'ready', doc: r.doc, req: { run_id: r.doc.run.run_id }, error: null });
    return true;
  }

  async refreshEstimate(): Promise<void> {
    const req = this.s.req;
    if (!req) return;
    const r = await this.deps.api.estimate(req);
    if (this.s.req !== req) return;
    this.set({ estimate: r.kind === 'ok' ? r.doc : null });
  }

  async export(kind: ExportKind): Promise<void> {
    const doc = this.s.doc;
    if (!doc) return;
    const url = kind === 'html' ? doc.exports.html_url : kind === 'pdf' ? doc.exports.pdf_url : doc.exports.json_url;
    this.set({ exporting: { kind, state: 'working' } });
    const r = await this.deps.api.download(url);
    if (r.kind !== 'ok') {
      this.set({ exporting: { kind, state: 'error', code: r.kind === 'refused' || r.kind === 'down' ? r.code : 'renderer_unavailable' } });
      return;
    }
    (this.deps.save ?? saveBlob)(r.doc, `${doc.brief_id}.${kind}`);
    this.set({ exporting: null });
  }

  /** Save as PDF without a server renderer: the HTML export is printed by the browser. */
  async printPdf(): Promise<void> {
    const doc = this.s.doc;
    if (!doc) return;
    this.set({ exporting: { kind: 'pdf', state: 'working' } });
    const r = await this.deps.api.download(doc.exports.html_url);
    if (r.kind !== 'ok') {
      this.set({ exporting: { kind: 'pdf', state: 'error', code: r.kind === 'refused' || r.kind === 'down' ? r.code : 'renderer_unavailable' } });
      return;
    }
    const ok = (this.deps.print ?? ((h, l) => printHtmlAsPdf(h, { label: l })))(await r.doc.text(), doc.brief_id);
    this.set({ exporting: ok ? null : { kind: 'pdf', state: 'error', code: 'print_blocked' } });
  }

  reset(): void { this.token += 1; this.s = INITIAL; this.subs.forEach((f) => f()); }
}

let controller: BriefController | null = null;
export function getBrief(): BriefController {
  if (!controller) {
    controller = new BriefController({
      api: makeBriefApi(),
      onCall: (d) => { recordAiCall({ surface: 'brief', did: 'Wrote a disruption brief', provenance: d.provenance, key: `${d.brief_id}:${d.generated_at}:${d.sha256}` }); },
    });
  }
  return controller;
}
export function useBrief(): BriefState {
  const c = getBrief();
  return useSyncExternalStore(c.subscribe, c.getSnapshot, c.getSnapshot);
}
