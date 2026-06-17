// ONE source of truth for the colour vocabulary of the review surfaces.
//
// Before this file, the same semantic colours were hand-rolled in three places
// (GRADE_CLS in library/shared.jsx, DECISION_CLS in DeepReviewSection,
// PROPOSAL_CLS in ProposedVerdictCard) and drifted. Consolidating them is the
// Occam's-Razor / Law-of-Similarity win: a B-grade reads the same blue, a FLAG
// reads the same rose, everywhere. `Chip` (primitives.jsx) is the only consumer
// of CHIP_TONE; the helper maps translate a domain value → a tone name.

// Pill palette. Soft tint + readable ink + matching hairline border — calm
// enough to sit several-to-a-row without any one shouting (Von Restorff is
// reserved for the verdict banner, which owns the loud tint).
export const CHIP_TONE = {
  emerald: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  sky: 'bg-sky-50 text-sky-700 border-sky-200',
  amber: 'bg-amber-50 text-amber-800 border-amber-200',
  rose: 'bg-rose-50 text-rose-700 border-rose-200',
  slate: 'bg-slate-100 text-slate-600 border-slate-200',
  indigo: 'bg-indigo-50 text-indigo-700 border-indigo-200',
  violet: 'bg-violet-50 text-violet-700 border-violet-200',
  teal: 'bg-teal-50 text-teal-700 border-teal-200',
};

// A-D full-text quality grade.
const GRADE_TONE = { A: 'emerald', B: 'sky', C: 'amber', D: 'rose' };
export const gradeTone = (grade) => GRADE_TONE[grade] || 'slate';

// read / skim / skip digest recommendation.
const DECISION_TONE = { read: 'emerald', skim: 'amber', skip: 'slate' };
export const decisionTone = (d) => DECISION_TONE[String(d || '').toLowerCase()] || 'slate';

// Reference-free rigor band (mirrors services/library quality_eval bands).
const BAND_TONE = { flag: 'rose', highlight: 'emerald', uncertain: 'amber', neutral: 'slate' };
export const bandTone = (b) => BAND_TONE[String(b || '').toLowerCase()] || 'slate';
export const BAND_LABEL = {
  flag: 'Flag', highlight: 'Highlight', neutral: 'Neutral', uncertain: 'Uncertain',
};

// Verdict banner accent — the ONE loud surface. Left-rule + soft wash, keyed to
// the read decision so DEEP-READ / SKIM / SKIP are distinguishable at a glance.
export const VERDICT_ACCENT = {
  deep: 'border-emerald-400 bg-emerald-50/70',
  skim: 'border-amber-400 bg-amber-50/70',
  skip: 'border-slate-300 bg-slate-50',
};
