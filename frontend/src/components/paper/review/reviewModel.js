import { dedupeFlags, isSupportedGoal } from './briefModel.js';

// What PaperReview reads out of a cached deep_review payload ({digest, quality, goal_summaries}).
export function reviewSource(deep) {
  return {
    digest: deep.digest && Object.keys(deep.digest).length ? deep.digest : null,
    quality: deep.quality || null,
    goals: deep.goal_summaries || [],
    lost: deep.not_established_fields || [],
  };
}

// Shown in the glance card / the impact grid, so the digest rows below leave them out.
export const GLANCE_FIELDS = ['key_strength', 'key_weakness'];
export const IMPACT_FIELDS = ['impact', 'industry_impact', 'academy_impact', 'unknown_unknowns'];

// Legacy cache (pre-digest): only the old grade is stored.
export const isLegacyGradeOnly = ({ digest, quality, goals }) =>
  !digest && quality && quality.grade && quality.basis !== 'non_paper' && !goals.length;

export const isEmptyReview = ({ digest, quality, goals }) => !digest && !quality && !goals.length;

// The API projects cached decisions through the current policy; absence stays unknown.
function readingVerdict(digest) {
  const digestDecision = String(digest?.read_decision || '').toLowerCase();
  const hasReadingDecision = ['read', 'skim', 'skip'].includes(digestDecision);
  const verdict = hasReadingDecision
    ? {
      key: digestDecision === 'read' ? 'deep' : digestDecision,
      label: { read: 'READ', skim: 'SKIM', skip: 'DIGEST IS ENOUGH' }[digestDecision],
      reason: digest.read_why || '',
    }
    : { key: 'skip', label: 'REVIEW', reason: 'Reading decision unavailable; review the evidence.' };
  return { verdict, digestDecision, hasReadingDecision };
}

// Located findings (the story page passes the section overlay; the compact card
// does not → unchanged). Keyed by VALUE (goal/flag text, critical item) so the
// join survives the filter/reorder the render does below. Empty maps = no chips.
function locationMaps(sectionOverlay) {
  const ov = sectionOverlay && !sectionOverlay.degraded ? sectionOverlay : null;
  const located = (rows, key) => new Map((rows || []).filter((r) => r.section)
    .map((r) => [String(r[key] || '').trim(), { ...r.section, match: r.match }]));
  return {
    goalLoc: new Map((ov?.goals || []).map((o) => [o.goal, o.sections || []])),
    flagLoc: located(ov?.red_flags, 'text'),
    missLoc: located(ov?.missing_critical, 'item'),
  };
}

// Only a basis other than the full text is a caveat; the literal record is in the reference fold.
function partialBasisNotes(quality, digest) {
  return [['quality check', quality?.basis], ['digest', digest?.basis]]
    .filter(([, basis]) => basis && basis !== 'full_text')
    .map(([what, basis]) => `${what}: ${String(basis).replace(/_/g, ' ')}`);
}

// The server projects scientific grade with the reading action. A separate
// legacy quality-only review has no digest; raw model_digest never supplies a grade.
const readGrade = (digest, quality, isNonPaper) => {
  if (isNonPaper) return '';
  return digest ? digest.grade || '' : quality?.grade || '';
};

const cleaned = (list) => (list || []).map((x) => String(x || '').trim()).filter(Boolean);

// Non-paper (web article / blog / news): scientific quality criteria don't apply —
// reviewed for relevance only, so suppress the rigor panels + grade and lead with the
// digest's own read decision instead of the goals×rigor verdict.
function qualityFacts(quality, digest) {
  const isNonPaper = quality?.basis === 'non_paper';
  const scored = isNonPaper ? null : quality;
  const overstated = (scored?.overstatements || []).filter(Boolean);
  return {
    isNonPaper,
    redFlags: dedupeFlags(cleaned(scored?.red_flags)),
    band: String(scored?.quality_band || ''),
    findings: (digest?.key_findings || []).filter(text => !overstated.includes(text)),
    grade: readGrade(digest, quality, isNonPaper),
  };
}

// Everything the sections below derive from the source. Pure — no rendering.
export function deriveReview({ digest, quality, goals, lost }, sectionOverlay) {
  return {
    digest, quality, goals, lost,
    nHitGoals: goals.filter(isSupportedGoal).length,
    ...qualityFacts(quality, digest),
    ...readingVerdict(digest),
    ...locationMaps(sectionOverlay),
    partialBasis: partialBasisNotes(quality, digest),
  };
}
