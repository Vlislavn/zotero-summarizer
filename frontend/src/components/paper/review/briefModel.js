// Decision-aid derivation for the native review, ported 1:1 from the server
// brief so the in-app render and the standalone presentation.html read the SAME
// (Jakob's Law). Source of truth: services/library/_paper_read_brief.py and
// _quality_prompts.py — keep the verdict thresholds, band gloss / method /
// legend copy, and rubric labels in sync with those if they ever change.

// Reference-free band → plain-language gloss. {lead} is bolded; {body} follows.
// Mirrors _paper_read_brief._BAND_GLOSS (memory-protected decision-aid copy).
export const BAND_GLOSS = {
  highlight: {
    lead: 'Rigorous enough to act on.',
    body: 'Passed our independent checks for evidence and reporting — read with confidence.',
  },
  flag: {
    lead: 'Read critically.',
    body: 'We found rigor problems that could make the headline result unreliable — see below.',
  },
  neutral: {
    lead: 'Sound but unremarkable.',
    body: "No red flags, but it doesn't clear the bar for a confident highlight — judge it on relevance.",
  },
  uncertain: {
    lead: 'Needs your eyes.',
    body: "Our independent passes disagreed on the verdict — don't trust the band alone.",
  },
};

// Band gloss DERIVED from the actual red-flag list so it never contradicts it: the
// plain neutral/highlight copy says "No red flags" — only honest when none fired.
// When some did, say so instead (mirrors server _paper_read_brief._gloss).
export function bandGloss(band, hasRedFlags = false) {
  if (hasRedFlags && (band === 'neutral' || band === 'highlight')) {
    return {
      lead: 'Mostly sound, with caveats.',
      body: 'It clears the bar overall, but our independent checks flagged the issues below — weigh them before relying on it.',
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
  { term: 'FLAG', gloss: 'a rigor red-flag fired' },
  { term: 'NEUTRAL', gloss: 'sound, unremarkable' },
  { term: 'HIGHLIGHT', gloss: '≥6 grounded checks, zero flags' },
];

// Plain-English labels for rubric keys (mirrors _paper_read_brief._RUBRIC_LABEL).
const RUBRIC_LABEL = {
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
const TRIAD = ['external_validation', 'uncertainty', 'ablation']; // the rigor floor

function shortGoal(goal, words = 4) {  // matches server _short_goal (words=4)
  const parts = String(goal || '').match(/[A-Za-z0-9/&-]+/g) || [];
  return parts.slice(0, words).join(' ') + (parts.length > words ? '…' : '');
}

// goal_summaries → the fired set + the loudest scores driving the verdict.
export function summarizeGoals(goals = []) {
  const fired = goals.filter((g) => g?.retrieval_state === 'hit' && g?.relevant);
  const maxScore = goals.reduce((m, g) => Math.max(m, Number(g?.score) || 0), 0);
  return { fired, nFired: fired.length, maxScore };
}

// (key, label, reason) for the read decision — ported from _read_verdict, with
// the flagged-but-relevant red-flag inlined (the brief_html override).
export function readVerdict({ nFired, band, redFlags = [] }) {
  if (!nFired) return { key: 'skip', label: 'SKIP', reason: 'none of your research goals are addressed' };
  if (band === 'flag') {
    const rf = redFlags.map((x) => String(x || '').trim()).filter(Boolean);
    const reason = rf.length
      ? `relevant, but rigor is FLAGGED — ${rf[0]}. Read critically.`
      : 'relevant to your goals but rigor is flagged — read critically';
    return { key: 'skim', label: 'SKIM', reason };
  }
  if (band === 'highlight') return { key: 'deep', label: 'DEEP-READ', reason: 'relevant to your goals and rigorous' };
  return { key: 'deep', label: 'DEEP-READ', reason: 'relevant to your goals; quality is acceptable' };
}

export function relevanceVerdict({ nFired, maxScore }) {
  if (nFired && maxScore >= 2.3) return 'MUST READ';
  if (nFired && maxScore >= 1.5) return 'SHOULD READ';
  if (nFired) return 'COULD READ';
  return 'SKIP';
}

// The 2-3 rubric items that moved the band, in plain English (ported from
// _decisive_rows). Returns { heading, rows:[{ok,label}], caption }.
export function decisiveRows(rubric = {}, band = '') {
  const val = {};
  for (const k of new Set([...CORE_KEYS, ...TRIAD, ...Object.keys(rubric)])) {
    val[k] = String(rubric[k] || 'na').toLowerCase();
  }
  const yesCount = CORE_KEYS.filter((k) => val[k] === 'yes').length;
  if (band === 'highlight') {
    const ordered = [...TRIAD, ...CORE_KEYS.filter((k) => !TRIAD.includes(k))];
    const earned = ordered.filter((k) => val[k] === 'yes').slice(0, 3);
    return {
      heading: 'What earned it',
      rows: earned.map((k) => ({ ok: true, label: rubricLabel(k) })),
      caption: `${yesCount}/${CORE_KEYS.length} rigor checks met`,
    };
  }
  if (band === 'flag') {
    const failed = TRIAD.filter((k) => val[k] !== 'yes').slice(0, 3);
    return { heading: 'Why it sank', rows: failed.map((k) => ({ ok: false, label: rubricLabel(k) })), caption: '' };
  }
  if (band === 'uncertain') {
    return {
      heading: 'Split decision',
      rows: TRIAD.map((k) => ({ ok: val[k] === 'yes', label: rubricLabel(k) })),
      caption: '',
    };
  }
  const gaps = CORE_KEYS.filter((k) => val[k] === 'no').slice(0, 3);
  return { heading: "What's missing", rows: gaps.map((k) => ({ ok: false, label: rubricLabel(k) })), caption: '' };
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
