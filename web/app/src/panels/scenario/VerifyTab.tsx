import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Chip } from '../../shell/Chip';
import { useStore } from '../../store';
import { scn } from './client';
import { useScn } from './ctx';
import {
  chainHashRows, chainReplayState, plainLine, engineLabel, errorLine, shortHash, simReplayState, type ReplayState,
} from './logic';
import type { ChainVerify, SimVerify } from './types';
import { Building, CopyButton, Empty, Tag, type Tone } from './ui';
import { labelWord } from '../../copy';

const STATE_TONE: Record<ReplayState, Tone> = { 'NOT CHECKED': 'idle', BUILDING: 'busy', MATCH: 'ok', MISMATCH: 'bad', FAILED: 'bad' };

function Hash({ label, value, match }: { label: string; value: string; match?: boolean }) {
  return (
    <div className="scn-hash">
      <span className="scn-hash-k mono">{label}</span>
      <code className="scn-hash-v mono" data-testid={`hash-${label}`}>{value}</code>
      {match !== undefined && <Tag tone={match ? 'ok' : 'bad'} word={match ? 'Match' : 'Mismatch'} />}
      <CopyButton text={value} />
    </div>
  );
}

export function VerifyTab() {
  const c = useScn();
  return <div className="scn-tab" data-tab="VERIFY">{c.loop === 'chain' ? <ChainVerifyView /> : <SimVerifyView />}</div>;
}

/* ── chain: the GET replays from the journal alone, byte for byte ─────── */
function ChainVerifyView() {
  const c = useScn();
  const s = useStore();
  const id = c.chainId;
  const [v, setV] = useState<ChainVerify | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { setV(null); setErr(null); setBusy(null); }, [id]);
  if (!id || !s.chain) return <Empty word="No run" />;
  const st = busy !== null ? 'BUILDING' : chainReplayState(v);
  const stamp = c.chainJournal.doc;
  const run = async () => {
    setBusy(Date.now()); setErr(null);
    try { setV(await scn.verify(id)); } catch (e) { setErr(errorLine(e)); } finally { setBusy(null); }
  };
  const streamHash = v?.stored_stream_hash ?? stamp?.settings.stream_hash ?? s.chain.stream_hash;
  return (
    <>
      <div className="scn-row scn-row--wrap mono">
        <Chip kind="meta" word={engineLabel(v?.engine_rev ?? stamp?.engine_rev ?? 2)} />
        {stamp?.settings.plugin_registry_hash && <Chip kind="meta" word="Rules" meta={shortHash(stamp.settings.plugin_registry_hash, 10)} />}
        {stamp?.settings.map_version && <Chip kind="meta" word="Map" meta={stamp.settings.map_version} />}
        <Chip kind="meta" word={`${v?.event_count ?? s.chain.event_count} steps`} />
      </div>
      {!v && <Hash label="Stream SHA-256" value={streamHash} />}
      <div className="scn-row scn-row--wrap">
        <span className="scn-k mono">Replay check</span>
        {st === 'BUILDING' && busy !== null ? <Building since={busy} word="Replaying" /> : <Tag tone={STATE_TONE[st]} word={labelWord(st)} />}
        <button type="button" className="btn btn--amber scn-btn" disabled={busy !== null} onClick={() => void run()}>Verify by replay</button>
      </div>
      {err && <div className="scn-refusal mono" role="alert"><Tag tone="bad" word="Down" /><span className="scn-reason">{plainLine(err)}</span></div>}
      {v && (
        <>
          {chainHashRows(v).map((h) => <Hash key={h.label} label={h.label} value={h.value} />)}
          <div className="scn-row scn-row--wrap mono">
            <Tag tone={v.stored_hash_matches ? 'ok' : 'bad'} word="Stored = recomputed" />
            <Tag tone={v.replay_matches ? 'ok' : 'bad'} word="Replay = stored" />
            <Tag tone={v.byte_identical ? 'ok' : 'bad'} word="Byte identical" meta={`${v.canonical_bytes} B`} />
            {v.first_difference_seq !== null && <Tag tone="bad" word="First difference" meta={`Step ${v.first_difference_seq}`} />}
          </div>
        </>
      )}
    </>
  );
}

/* ── sim: replay is a pool job; POST starts it, GET reports it ────────── */
const POLL_MS = 3000;

function SimVerifyView() {
  const c = useScn();
  const id = c.simId;
  const [v, setV] = useState<SimVerify | null>(null);
  const [loading, setLoading] = useState<number | null>(null);
  const [posting, setPosting] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);

  const inflight = useRef(false);
  const load = useCallback(async (quiet: boolean) => {
    if (!id) return;
    if (quiet && inflight.current) return; // a poll tick waits for the call still in flight
    inflight.current = true;
    if (!quiet) setLoading(Date.now());
    try {
      const d = await scn.simVerify(id);
      if (alive.current) { setV(d); setErr(null); }
    } catch (e) { if (alive.current) setErr(errorLine(e)); } finally { inflight.current = false; if (alive.current && !quiet) setLoading(null); }
  }, [id]);
  useEffect(() => { setV(null); setErr(null); setPosting(null); void load(false); }, [load]);

  const jobBuilding = v?.replay_job?.status === 'building';
  useEffect(() => {
    if (!jobBuilding && posting === null) return;
    const t = window.setInterval(() => void load(true), POLL_MS);
    return () => window.clearInterval(t);
  }, [jobBuilding, posting, load]);
  useEffect(() => { if (posting !== null && v && v.replay_job && v.replay_job.status !== 'building') setPosting(null); }, [v, posting]);

  const replay = async () => {
    if (!id) return;
    setPosting(Date.now()); setErr(null);
    try { await scn.simVerifyReplay(id); await load(true); } catch (e) { setErr(errorLine(e)); setPosting(null); }
  };

  if (!id) return <Empty word="No run" />;
  const st = simReplayState(v, posting !== null);
  const since = posting ?? (v?.replay_job?.started_at ? Date.parse(v.replay_job.started_at) || Date.now() : Date.now());
  return (
    <>
      {loading !== null && !v && <div className="empty-state"><Building since={loading} word="Loading" meta="hashing record" /></div>}
      {err && <div className="scn-refusal mono" role="alert"><Tag tone="bad" word="Down" /><span className="scn-reason">{plainLine(err)}</span></div>}
      {v && (
        <>
          <div className="scn-row scn-row--wrap mono">
            <Chip kind="sim" />
            <Chip kind="meta" word="Rule version" meta={v.rule_version} />
            {v.stored_rule_version === null
              ? <Chip kind="notserved" word="Stored rule version" />
              : v.stored_rule_version !== v.rule_version
                ? <Tag tone="warn" word="Rule version changed" meta={v.stored_rule_version} />
                : <Chip kind="meta" word="Stored" meta="Same rules" />}
          </div>
          <Hash label="Stream SHA-256" value={v.stream_sha256} match={v.replay_detail?.stream_match} />
          <Hash label="Record SHA-256" value={v.record_sha256} match={v.replay_detail?.record_match} />
          <div className="scn-row scn-row--wrap">
            <span className="scn-k mono">Replay check</span>
            {st === 'BUILDING' ? <Building since={since} word="Replaying" /> : <Tag tone={STATE_TONE[st]} word={labelWord(st)} />}
            <button type="button" className="btn btn--amber scn-btn" disabled={st === 'BUILDING'} onClick={() => void replay()}>Verify by replay</button>
            {v.checked_utc && <Chip kind="meta" word="Checked" meta={v.checked_utc.replace('T', ' ').slice(0, 16)} />}
          </div>
          {v.replay_job?.status === 'failed' && (
            <div className="scn-refusal mono" role="alert">
              <Tag tone="bad" word="Failed" meta={v.replay_job.error?.code} /><span className="scn-reason">Replay did not complete.</span>
            </div>
          )}
          {v.replay_detail && st !== 'BUILDING' && (
            <div className="scn-row scn-row--wrap mono">
              <Tag tone={v.replay_detail.stream_match ? 'ok' : 'bad'} word={v.replay_detail.stream_match ? 'Stream = replay' : 'Stream ≠ replay'} />
              <Tag tone={v.replay_detail.record_match ? 'ok' : 'bad'} word={v.replay_detail.record_match ? 'Record = replay' : 'Record ≠ replay'} />
            </div>
          )}
        </>
      )}
    </>
  );
}
