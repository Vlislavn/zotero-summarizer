// Pure view-model helpers for the paper card (Todo #98, icon strip #124, English #127):
// map the card service's facts + AI fields onto small icon states and plain English
// labels. No fetching, no JSX — data → {icon, state, label}, so the component stays small.

// A fact has three honest states: `present`, `absent`, or `unknown` — the latter when
// no full text was read, so an absence is unproven and must not read as "no".
// Each row: [id, icon, name, label-if-present, label-if-absent, flag].
const PUBLISHED = [
  ['prompts', 'prompts', 'Prompts', 'Prompts published', 'No prompts found', (p) => p.prompts?.present],
  ['protocol', 'protocol', 'Eval protocol', 'Evaluation protocol described', 'No evaluation protocol found', (p) => p.evaluation_protocol?.present],
  ['judge', 'judge-vs-human', 'Judge vs humans', 'Judge validated against humans', 'No judge-vs-human check', (p) => p.judge_validation?.present],
  ['contracts', 'contracts', 'Contracts & schemas', 'Contracts and schemas published', 'No contracts or schemas found', (p) => p.agent_contracts?.present],
  ['data', 'data', 'Data', 'Data published', 'No data found', (p) => p.data_released?.present],
];
const METHOD = [
  ['baselines', 'baselines', 'Baselines', 'Baselines included', 'No baselines found', (c) => c.baselines?.present],
  ['ablation', 'ablation', 'Ablation', 'Ablation included', 'No ablation found', (c) => c.ablation?.present],
  ['uncertainty', 'uncertainty', 'Uncertainty', 'Uncertainty estimated', 'No uncertainty estimate', (c) => c.uncertainty?.present],
  ['external', 'external-validation', 'External validation', 'External validation included', 'No external validation found', (c) => c.external_validation?.present],
  ['repro', 'reproducibility', 'Reproducibility', 'Result reproduced', 'Reproducibility not shown', (c) => c.reproducibility?.present],
];

// The paper's own review checklist (`rubric`: key → yes/no/na) outranks the card's text scan:
// yes → present, no → absent; na or a missing key → null (the caller falls back to the scan).
const RUBRIC_STATE = { yes: 'present', no: 'absent' };
const rubricState = (rubric, id) => RUBRIC_STATE[String(rubric?.[NOT_FOUND[id]?.[0]] || '').toLowerCase()] || null;

function toFacts(rows, source = {}, unknown = false, extra = {}, rubric = null) {
  return rows.map(([id, icon, name, yes, no, flag]) => {
    const state = rubricState(rubric, id) || (unknown ? 'unknown' : flag(source) ? 'present' : 'absent');
    const label = state === 'present' ? `${yes}${extra[id] || ''}`
      : state === 'absent' ? no : `${name}: unknown (full text not read)`;
    return { id, icon, state, label };
  });
}

// “Published” group in the pilot order. judge-vs-human carries its number.
export function publishedFacts(published = {}, unknown = false) {
  const num = (published.judge_validation?.numbers || [])[0];
  return toFacts(PUBLISHED, published, unknown, { judge: num ? ` (${num})` : '' });
}

// “Method” group: the reusable-method checklist signals. With a review (`rubric`) its yes/no
// answers decide the icon — the card's own text scan misses what the review finds, and the
// icons must not contradict the review on the same page; the scan answers only where the review does not.
export const methodFacts = (checklist = {}, unknown = false, rubric = null) => toFacts(METHOD, checklist, unknown, {}, rubric);

// Code fact → {state, dot, label, url}. Mirrors the collector's `status`
// (own_live / promised / own_unverified / unclear / none / unknown). `dot` is the
// tiny ✓/✗ on the GitHub mark: `ok` (live own code), `bad` (promised or none),
// null (unverified or unknown — no verdict to show). `unknown` means no full text
// was read, so absence is unproven.
export function codeView(code = {}) {
  const url = code.url || '';
  switch (code.status) {
    case 'own_live': return { state: 'present', dot: 'ok', label: 'Own code published and reachable', url };
    case 'promised': return { state: 'absent', dot: 'bad', label: 'Code promised, but the repository does not open', url };
    case 'own_unverified': return { state: 'present', dot: null, label: 'Own code exists; not verified that it works', url };
    case 'unclear': return { state: 'present', dot: null, label: 'A code link exists; whose it is is unclear', url };
    case 'unknown': return { state: 'unknown', dot: null, label: 'Code: unknown (full text not read)', url: '' };
    default: return { state: 'absent', dot: 'bad', label: 'No code', url: '' };
  }
}

// The TL;DR's last line: what was looked for and not found — the complement of the icon
// row, which shows only what WAS found. The card's own text scan misses things, so an absence
// is said only when the paper's review checklist (`rubric`: key → yes/no/na) answers "no" (the
// method icons already follow that answer). Without a review, or when no full text was read,
// there is no line.
const NOT_FOUND = { baselines: ['baselines', 'baselines'], ablation: ['ablation', 'ablation'],
  uncertainty: ['uncertainty', 'uncertainty'], external: ['external_validation', 'external validation'],
  repro: ['repro_detail', 'reproducibility'], code: ['code_data_released', 'code'] };
export function notFoundLine(method, code, rubric) {
  if (!rubric || [...method, code].some((fact) => fact.state === 'unknown')) return '';
  return [...method, { ...code, id: 'code' }]
    .filter((fact) => fact.state === 'absent' && NOT_FOUND[fact.id])
    .filter((fact) => String(rubric[NOT_FOUND[fact.id][0]] || '').toLowerCase() === 'no')
    .map((fact) => NOT_FOUND[fact.id][1]).join(' · ');
}

// Who is behind it: affiliations + max h-index + a presentation-only confidence
// (NOT the owner's “depends on” compute, which is out of scope). High when a
// known-org affiliation is present or the h-index is solid.
export function authorsView(authors = {}) {
  const affiliations = authors.affiliations || [];
  const hIndex = typeof authors.max_h_index === 'number' ? authors.max_h_index : null;
  const confident = affiliations.length > 0 || (hIndex != null && hIndex >= 20);
  return { affiliations, hIndex, confident, confidence: confident ? 'high' : 'low' };
}

// Why the AI TL;DR is not shown → one plain English line (no “card” jargon).
export const AI_REASON_MESSAGE = {
  ai_disabled: 'AI is off in settings.',
  offline: 'Offline: AI TL;DR unavailable.',
  generation_failed: 'Could not make the AI TL;DR.',
  call_cap: 'Generation limit reached for this paper.',
  daily_token_cap: 'Daily token limit reached.',
  circuit_open: 'Generation paused for a while after errors.',
};

export const aiReasonMessage = (reason) => AI_REASON_MESSAGE[reason] || 'AI TL;DR is unavailable right now.';
