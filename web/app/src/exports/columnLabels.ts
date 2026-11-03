/* User-visible column labels for the Export preview. The files keep their raw
 * column names (node_id, parent_id, delay_d ...); only the preview speaks the
 * product vocabulary (places, steps; never node). Pure. */

const LABELS: Record<string, string> = {
  node_id: 'Place id',
  place_id: 'Place id',
  /* parent_id is the parent step's sequence number in the chain, not a place */
  parent_id: 'Parent step',
  parentSeq: 'Parent step',
  run_id: 'Run id',
  seq: 'Step',
  tick: 'Tick',
  at: 'At (UTC)',
  delay_d: 'Delay (d)',
  magnitude: 'Magnitude',
  mechanism: 'Mechanism',
  channel: 'Channel',
  from_step: 'From step',
  to_step: 'To step',
  from_place: 'From place',
  to_place: 'To place',
};

/** The label a column wears in the preview. Unknown snake_case names read as
 *  sentence case with a trailing unit tag in brackets (`x_d` -> `X (d)`). */
export function columnLabel(raw: string): string {
  const hit = LABELS[raw];
  if (hit) return hit;
  if (!/^[a-z][a-z0-9]*(_[a-z0-9]+)+$/.test(raw)) return raw;
  const unit = raw.match(/_(d|km|nm|kn|usd|teu)$/);
  const words = (unit ? raw.slice(0, -unit[0].length) : raw).split('_').join(' ');
  const base = words.charAt(0).toUpperCase() + words.slice(1);
  return unit ? `${base} (${unit[1] === 'usd' ? 'USD' : unit[1] === 'teu' ? 'TEU' : unit[1]})` : base;
}

/** "run_015454e18c45d68e" -> "015454e1" (the short form shown beside a title carrying the full id). */
export function shortRunId(id: string | null | undefined, n = 8): string | null {
  if (!id) return null;
  const bare = id.replace(/^(run|sim|brf)_/, '');
  return bare.length > n ? bare.slice(0, n) : bare;
}
