import { Bullets, Disclosure, KeyVal } from './primitives.jsx';
import { BAND_LABEL } from './tones.js';
import { isSupportedGoal, paperTypeLabel, standardLabel } from './briefModel.js';

const POLICY_REASONS = {
  abstract_only: 'Abstract-only digest: full-paper reading is not supported by this review.',
  full_text_not_confirmed: 'Digest source basis is unknown or unconfirmed; full-paper reading is not supported.',
  writing_friction: 'High writing friction: preserve the idea without a full-paper read.',
  writing_not_assessed: 'Writing friction was not assessed.',
  // Legacy: stored by older reviews (4 in the 11-09 copy); no longer emitted — the goal board is a badge.
  active_goal_not_confirmed: 'A direct active-goal match was not confirmed (legacy flag; not used for the decision).',
  idea_value_not_high: 'High idea value was not confirmed.',
  strong_evidence_not_confirmed: 'Strong scientific support was not confirmed.',
  weak_evidence: 'Scientific support has material concerns.',
  original_value_not_assessed: 'Original-only value was not assessed; the reading decision is withheld.',
  reading_targets_not_assessed: 'Specific reading targets were not recorded; the reading decision is withheld.',
};

function ideaLabel(digest) {
  const scores = [digest.novelty, digest.significance].filter(value => Number.isInteger(value) && value >= 1 && value <= 5);
  const score = Math.max(0, ...scores);
  return score >= 4 ? 'high' : score >= 3 ? 'moderate' : score > 0 ? 'low' : 'not assessed';
}

function goalLabel(goals) {
  if (goals.some(isSupportedGoal)) return 'Supported goal match recorded.';
  if (goals.length && goals.every(goal => goal.retrieval_state === 'miss' && goal.relevant === false)) {
    return 'No goal match recorded.';
  }
  return 'Goal match unknown.';
}

// Presentation only: preserve the separate cached signals and explain server policy, never score or rank.
// `folded` (the reading page's verdict card): the explanatory notes open with the
// disclosure instead of repeating the why line above it.
export default function OrthogonalSignals({ deep, folded = false }) {
  const digest = deep.digest || {};
  const quality = deep.quality || {};
  const nonPaper = quality.basis === 'non_paper';
  const band = quality.quality_band;
  const evidence = nonPaper ? 'not applicable' : band === 'flag' ? 'weak (Flag)' : BAND_LABEL[band] || 'not assessed';
  const friction = ['low', 'moderate', 'high'].includes(digest.writing_friction) ? digest.writing_friction : null;
  const policyFlags = deep.reading_policy_flags || [];
  const weakEvidence = !nonPaper && policyFlags.includes('weak_evidence');
  const policyReasons = policyFlags.map(flag => POLICY_REASONS[flag]).filter(Boolean);
  const capped = deep.model_read_decision === 'read' && digest.read_decision === 'skim';
  const writingReasons = (digest.writing_reasons || []).filter(Boolean);
  const scoreText = (value) => Number.isInteger(value) && value >= 1 && value <= 5 ? `${value}/5` : 'not assessed';

  const notes = <>
    {weakEvidence && <p>This is an idea paper; its claims are not yet backed by evidence. Check what is missing before relying on them.</p>}
    {friction === 'high' && ['read', 'skim'].includes(digest.read_decision) && (
      <p>Idea preserved — high writing friction limits automatic advice to a targeted skim, not a judgment of bad science.</p>
    )}
    {digest.read_decision === 'skim' && (capped || policyReasons.length > 0) && (
      <dl>
        <KeyVal label="Why SKIM">
          {capped && <p>Automatic policy capped the model&apos;s read recommendation to skim.</p>}
          {policyReasons.length ? <Bullets items={policyReasons} />
            : <p>Specific policy reasons are unavailable in this saved review.</p>}
        </KeyVal>
      </dl>
    )}
  </>;

  return (
    <div role="group" aria-label="Idea, evidence and writing" className="mt-3 max-w-[66ch] space-y-2 break-words border-t border-slate-200/70 pt-3 text-[14px] leading-relaxed text-slate-600 [&_dd]:!text-[14px] [&_dt]:text-slate-600 [&_li]:!text-[14px] [&_p]:!text-[14px] [&_summary]:text-slate-600 [&_summary]:!text-[13px] [&_summary]:normal-case [&_summary]:tracking-normal">
      {!folded && notes}
      <Disclosure summary={`Idea: ${ideaLabel(digest)} · Evidence: ${evidence} · Writing: ${friction ? `${friction} friction` : 'not assessed'}`}>
        {folded && <div className="mb-2 space-y-2">{notes}</div>}
        <dl className="space-y-2">
          <KeyVal label="Idea value">
            <p>Model assessments: Novelty {scoreText(digest.novelty)} · Significance {scoreText(digest.significance)}.</p>
            <p>{goalLabel(deep.goal_summaries || [])} {digest.relevance || ''}</p>
          </KeyVal>
          <KeyVal label="Scientific quality">
            <p>{nonPaper ? 'Not a research paper; scientific criteria do not apply.' : 'Independent type-aware QualityEval (model assessment); writing polish is excluded.'}</p>
            {!nonPaper && <p>{quality.paper_type ? paperTypeLabel(quality.paper_type) : 'Paper type not recorded'}
              {quality.coverage_standard ? ` · ${standardLabel(quality.coverage_standard)}` : ''}.</p>}
            {!nonPaper && !BAND_LABEL[band] && <p>Scientific support was not assessed; a digest grade is not a substitute.</p>}
          </KeyVal>
          <KeyVal label={friction ? `Writing friction · ${friction}` : 'Writing friction'}>
            <p>Avoidable exposition friction, not topic difficulty or scientific validity.</p>
            {writingReasons.length ? <Bullets items={writingReasons} /> : <p>{friction === 'low'
              ? 'No concrete exposition failures were reported.'
              : friction ? 'Concrete writing reasons not recorded in this saved review.'
                : 'Missing writing signal is unknown, not clear writing.'}</p>}
          </KeyVal>
          {digest.read_decision !== 'skim' && policyReasons.length > 0 && <KeyVal label="Reading policy">
            <Bullets items={policyReasons} />
          </KeyVal>}
        </dl>
      </Disclosure>
    </div>
  );
}
