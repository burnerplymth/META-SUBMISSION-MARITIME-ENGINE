/* The provenance drawer reference of one evidence entry of the research report. Values the note does not carry read
 * "Not exposed" (provRow turns an absent value into null). */
import { toolLabel } from '../../agent/timeline';
import type { ProvRef } from '../../provenance/registry';
import { provRow } from '../../provenance/registry';
import { caveatWord } from './vocab';
import { evidenceKindWord, evidenceTitle, MODE_WORD, type EvidenceRow, type ReportNote } from './reportModel';

export function evidenceProvenance(e: EvidenceRow, note: ReportNote): ProvRef {
  const mode = note.label.state;
  return {
    id: `research-evidence:${note.id}:${e.id}`,
    title: `Evidence ${e.id}`,
    value: evidenceTitle(e),
    word: mode === 'LIVE' ? 'LIVE' : 'RECORDED',
    asOf: null,
    formula: null,
    sections: [
      {
        title: 'Evidence',
        rows: [
          provRow('Entry', evidenceKindWord(e.kind)),
          provRow('Tool', toolLabel(e.tool)),
          provRow('Analysis', e.analysisId, true),
          provRow('Sample size', e.n),
          provRow('Caveats', e.caveats.length ? e.caveats.map(caveatWord).join(', ') : 'None listed'),
          provRow('Label', e.label),
          provRow('Arguments hash', e.argsSha, true),
          provRow('Result hash', e.resultSha, true),
        ],
      },
      {
        title: 'Report',
        rows: [
          provRow('Report', note.id, true),
          provRow('Written by', 'Meta AI'),
          provRow('Mode', MODE_WORD[mode]),
          provRow('Model', note.label.model),
          provRow('Prompt', note.label.promptId, true),
          provRow('Check', note.verification.status === 'VERIFIED' ? 'Verified' : 'Unverified'),
        ],
      },
    ],
  };
}
