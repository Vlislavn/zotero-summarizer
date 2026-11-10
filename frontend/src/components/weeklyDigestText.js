// Plain-English wording for a saved Weekly digest (pure, no I/O). Digests are stored as written by the
// generator (snake_case flags, raw gate numbers), so the page translates at display time.

const MONTHS = { month: 'short' };
const parts = (iso) => {
  const date = new Date(`${String(iso).slice(0, 10)}T12:00:00Z`);
  return Number.isNaN(date.getTime()) ? null : { d: date.getUTCDate(), m: date.toLocaleDateString('en-GB', { ...MONTHS, timeZone: 'UTC' }), y: date.getUTCFullYear() };
};

// «2026-10-14T00:00:00+00:00» → «2026-10-20T23:59:59.999999+00:00»  ⇒  «14–20 Oct 2026».
export function rangeText(from, to) {
  const a = parts(from);
  const b = parts(to);
  if (!a || !b) return 'Saved digest';
  if (a.y !== b.y) return `${a.d} ${a.m} ${a.y} – ${b.d} ${b.m} ${b.y}`;
  if (a.m !== b.m) return `${a.d} ${a.m} – ${b.d} ${b.m} ${b.y}`;
  return a.d === b.d ? `${a.d} ${a.m} ${a.y}` : `${a.d}–${b.d} ${b.m} ${b.y}`;
}

const GAPS = {
  idea_value_not_high: 'High idea value was not confirmed.',
  strong_evidence_not_confirmed: 'Strong evidence was not confirmed.',
  weak_evidence: 'The evidence has material weaknesses.',
  uncertainty: 'Results are reported without error bars or significance tests.',
  no_leakage: 'The paper does not show that test data was kept apart from training data.',
  baselines: 'No fair baselines to compare against.',
  ablation: 'No ablation study (which parts matter is unclear).',
  external_validation: 'Not tested on outside data.',
  repro_detail: 'Too little detail to reproduce the work.',
  code_data_released: 'Code and data are not released.',
  dataset_provenance: 'Where the data came from is not described.',
};
const SNAKE = /^[a-z0-9]+(?:_[a-z0-9]+)+$/;

// «weak_evidence» → a sentence; an unknown snake_case token is at least spelled out; real sentences pass through.
export function plainReason(value) {
  const text = String(value ?? '').trim();
  if (GAPS[text]) return GAPS[text];
  if (!SNAKE.test(text)) return text;
  const spaced = text.replace(/_/g, ' ');
  return `${spaced.charAt(0).toUpperCase()}${spaced.slice(1)}.`;
}

const GATE = /^Predicted by classifier gate/;
const GATE_REJECT = /^classifier_gate:(\w+)/;
const GATE_NO = new Set(['dont_read', 'skip']);

// Gate-only triage text carries raw scores and SHAP hints; say what it means instead.
export function rationaleText(text) {
  const value = String(text ?? '');
  if (GATE.test(value)) return 'Picked by the relevance model alone; no AI explanation was written.';
  const reject = GATE_REJECT.exec(value);
  if (reject) return GATE_NO.has(reject[1]) ? 'Judged not worth reading by the relevance model.' : 'Rated below the cut-off by the relevance model.';
  return plainReason(value);
}

export const WORTH = { read: 'Worth reading', skim: 'Worth a skim', skip: 'Not worth reading', unknown: 'Not rated' };
export const worthText = (value) => WORTH[value] || 'Not rated';

const BASIS = { full_text: 'Read in full text', abstract_only: 'Read from the abstract only', abstract: 'Read from the abstract only' };
export const basisText = (value) => BASIS[value] || 'Source of the reading not recorded';

export const listText = (values, empty) => (values?.length ? values.map((value) => String(value).replace(/_/g, ' ')).join(', ') : empty);

// «agent harness: Same sentence.» repeated per project → one «agent harness, biomedical agents: Same sentence.».
export function mergeProjectUse(notes = []) {
  const groups = new Map();
  for (const note of notes) {
    const at = note.indexOf(': ');
    const [project, sentence] = at > 0 && at < 80 ? [note.slice(0, at), note.slice(at + 2)] : ['', note];
    groups.set(sentence, [...(groups.get(sentence) || []), project].filter(Boolean));
  }
  return [...groups].map(([sentence, projects]) => (projects.length ? `${projects.join(', ')}: ${sentence}` : sentence));
}
