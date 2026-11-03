/* Latest-wins tickets per key: a slow reply for an older request must not overwrite a newer one of the same key. */
export class Tickets {
  private n = new Map<string, number>();
  next(key: string): number { const t = (this.n.get(key) ?? 0) + 1; this.n.set(key, t); return t; }
  isCurrent(key: string, t: number): boolean { return this.n.get(key) === t; }
}
