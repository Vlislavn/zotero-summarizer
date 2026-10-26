// Decision-aid derivation for the native review, ported 1:1 from the server
// brief so the in-app render and the standalone presentation.html read the SAME
// (Jakob's Law). Source of truth: services/library/_paper_read_brief.py and
// _quality_prompts.py — keep the verdict thresholds, band gloss / method /
// legend copy, and rubric labels in sync with those if they ever change.

// Reference-free band → plain-language gloss. {lead} is bolded; {body} follows.
// Mirrors _paper_read_brief._BAND_GLOSS (memory-protected decision-aid copy).
export const isSupportedGoal = (goal) => goal?.retrieval_state === 'hit'
  && goal?.relevant === true && goal?.abstained !== true;

export const BAND_GLOSS = {
  highlight: {
    lead: 'Strong checklist coverage.',
    body: 'Applicable evidence/reporting checks were met; this is not scientific certification.',
  },
  flag: {
    lead: 'Read critically.',
    body: 'Applicable checks or material concerns warrant checking the claims below.',
  },
  neutral: {
    lead: 'Mixed checklist coverage.',
    body: 'Judge the applicable evidence and remaining gaps on relevance, not writing polish.',
  },
  uncertain: {
    lead: 'Needs your eyes.',
    body: 'Assessment is unavailable or model passes disagreed; this is not a scientific failure.',
  },
};

// Band gloss DERIVED from the actual red-flag list so it never contradicts it: the
// plain neutral/highlight copy says "No red flags" — only honest when none fired.
// When some did, say so instead (mirrors server _paper_read_brief._gloss).
export function bandGloss(band, hasRedFlags = false) {
  if (hasRedFlags && (band === 'neutral' || band === 'highlight')) {
    return {
      lead: 'Checklist coverage with caveats.',
      body: 'Model-generated concerns below need checking before relying on the claims.',
    };
  }
  return BAND_GLOSS[band] || { lead: '', body: '' };
}

// The literal answer to "what did we apply?" (· N/M passes agree is appended).
export const METHOD_CLAUSE =
  'How we judged it: a reference-free rubric + red-flag scan — no citation counts, ' +
  'authors hidden, scored only from what the paper itself shows. Agreement below is ' +
  'self-consistency across runs, not yet human-validated.';

// Legend terms (rendered as <b>term</b> gloss, joined by ·).
export const LEGEND = [
  { term: 'FLAG', gloss: 'material concerns / checklist gaps' },
  { term: 'NEUTRAL', gloss: 'mixed coverage' },
  { term: 'HIGHLIGHT', gloss: 'strong coverage of applicable type-aware checks' },
];

// Plain-English labels for rubric keys (mirrors _paper_read_brief._RUBRIC_LABEL).
const RUBRIC_LABEL = {
  thesis: 'States a clear thesis / position',
  evidence_cited: 'Supports premises with cited evidence',
  scope_match: 'Matches claim scope to the evidence offered',
  external_validation: 'Validated on independent / held-out data',
  uncertainty: 'Reports uncertainty (CIs, error bars, seeds)',
  ablation: 'Ablations isolate what drives the result',
  baselines: 'Compared against fair, current baselines',
  dataset_provenance: 'States dataset source, version & license',
  repro_detail: 'Enough detail to reproduce',
  code_data_released: 'Code or data released',
  patient_level_split: 'Patient-level train/test split',
  clinical_calibration: 'Calibration / multi-site validation',
  determinism: 'Reports run-to-run determinism',
  eval_contamination: 'Addresses eval contamination',
};
export const rubricLabel = (k) => RUBRIC_LABEL[k] || String(k || '').replace(/_/g, ' ');

// Paper-type → display label (mirrors _paper_type_checklists labels). The two
// generic_* are the safe supertypes the detector falls back to (shown "type uncertain").
export const PAPER_TYPE_LABEL = {
  empirical_ml: 'Empirical ML', methods_system: 'Methods / systems',
  clinical_prediction: 'Clinical prediction model', diagnostic_accuracy: 'Diagnostic-accuracy / imaging',
  rct_ai: 'RCT of an AI intervention', dataset_benchmark: 'Dataset / benchmark',
  systematic_review: 'Systematic review / meta-analysis', narrative_review: 'Narrative review',
  survey: 'Survey', position: 'Position / perspective', policy: 'Policy / framework / guideline',
  theory: 'Theory / analysis', case_report: 'Case report', editorial: 'Editorial / commentary',
  generic_empirical: 'Empirical (type uncertain)', generic_review: 'Review (type uncertain)',
};
export const paperTypeLabel = (t) => PAPER_TYPE_LABEL[t] || String(t || '').replace(/_/g, ' ');

// Core rubric keys, prompt order (mirrors _quality_prompts.RUBRIC_ITEMS), plus
// the domain keys appended for the full checklist.
const CORE_KEYS = [
  'external_validation', 'uncertainty', 'ablation', 'baselines',
  'dataset_provenance', 'repro_detail', 'code_data_released',
];
const DOMAIN_KEYS = ['patient_level_split', 'clinical_calibration', 'determinism', 'eval_contamination'];

function shortGoal(goal, words = 4) {  // matches server _short_goal (words=4)
  const parts = String(goal || '').match(/[A-Za-z0-9/&-]+/g) || [];
  return parts.slice(0, words).join(' ') + (parts.length > words ? '…' : '');
}

// The 2-3 rubric items that moved the band, in plain English (ported from
// _decisive_rows). Returns { heading, rows:[{ok,label}], caption }.
export function decisiveRows(rubric = {}, band = '') {
  const val = Object.fromEntries(Object.entries(rubric).map(([key, value]) => [key, String(value || 'na').toLowerCase()]));
  const applicable = Object.keys(val).filter(key => ['yes', 'no'].includes(val[key]));
  const met = applicable.filter(key => val[key] === 'yes');
  const selected = band === 'highlight' ? met : band === 'uncertain' ? applicable
    : applicable.filter(key => val[key] === 'no');
  return {
    heading: { highlight: 'What earned it', uncertain: 'Assessment uncertain', flag: 'Checks to inspect' }[band] || "What's missing",
    rows: selected.slice(0, 3).map(key => ({ ok: val[key] === 'yes', label: rubricLabel(key) })),
    caption: applicable.length ? `${met.length}/${applicable.length} applicable checks met` : '',
  };
}

// Full rubric rows for the "show all checks" disclosure: core first, then domain,
// then any leftovers — each with its yes/no/na value + grounded quote.
export function fullChecklist(rubric = {}, evidence = {}) {
  const ordered = [...CORE_KEYS, ...DOMAIN_KEYS, ...Object.keys(rubric)];
  const seen = new Set();
  const rows = [];
  for (const k of ordered) {
    if (seen.has(k) || !(k in rubric)) continue;
    seen.add(k);
    rows.push({
      key: k,
      label: rubricLabel(k),
      value: String(rubric[k] || 'na').toLowerCase(),
      quote: String(evidence[k] || '').trim(),
    });
  }
  return rows;
}

export { shortGoal };
