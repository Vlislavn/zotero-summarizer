import { Bullets, Disclosure, KeyVal } from './primitives.jsx';
import { BAND_LABEL } from './tones.js';
import { isSupportedGoal, paperTypeLabel } from './briefModel.js';

const POLICY_REASONS = {
  abstract_only: 'Abstract-only digest: full-paper reading is not supported by this review.',
  full_text_not_confirmed: 'Digest source basis is unknown or unconfirmed; full-paper reading is not supported.',
  writing_friction: 'High writing friction: preserve the idea without a full-paper read.',
  writing_not_assessed: 'Writing friction was not assessed.',
  active_goal_not_confirmed: 'A direct active-goal match was not confirmed.',
  idea_value_not_high: 'High idea value was not confirmed.',
  strong_evidence_not_confirmed: 'Strong scientific support was not confirmed.',
  weak_evidence: 'Scientific support has material concerns.',
  goals_not_assessed: 'Goal assessment is unavailable; the reading decision is withheld.',
};

function ideaLabel(digest) {
  const scores = [digest.novelty, digest.significance].filter(value => Number.isInteger(value) && value >= 1 && value <= 5);
  const score = Math.max(0, ...scores);
  return score >= 4 ? 'high' : score >= 3 ? 'moderate' : score > 0 ? 'low' : 'not assessed';
}

function goalLabel(goals) {
  if (goals.some(goal => isSupportedGoal(goal) && goal.abstained === false)) return 'Supported goal match recorded.';
  if (goals.length && goals.every(goal => goal.retrieval_state === 'miss' && goal.relevant === false)) {
    return 'No goal match recorded.';
  }
  return 'Goal match unknown.';
}

// Presentation only: preserve the separate cached signals and explain server policy, never score or rank.
export default function OrthogonalSignals({ deep }) {
  const digest = deep.digest || {};
  const quality = deep.quality || {};
  const nonPaper = quality.basis === 'non_paper';
  const band = quality.quality_band;
  const evidence = nonPaper ? 'not applicable' : band === 'flag' ? 'weak (Flag)' : BAND_LABEL[band] || 'not assessed';
  const friction = ['low', 'moderate', 'high'].includes(digest.writing_friction) ? digest.writing_friction : null;
  const weakEvidence = !nonPaper && (band === 'flag' || (quality.overstatements || []).some(value => String(value).trim()));
  const policyReasons = (deep.reading_policy_flags || []).map(flag => POLICY_REASONS[flag]).filter(Boolean);
  const capped = deep.model_read_decision === 'read' && digest.read_decision === 'skim';
  const writingReasons = (digest.writing_reasons || []).filter(Boolean);
  const scoreText = (value) => Number.isInteger(value) && value >= 1 && value <= 5 ? `${value}/5` : 'not assessed';

  return (
    <div role="group" aria-label="Idea, evidence and writing" className="mt-2 space-y-1.5 break-words text-[13px] leading-relaxed text-slate-700 max-w-[66ch] [&_summary]:text-slate-600 [&_dt]:text-slate-600">
      {weakEvidence && <p>Concept/hypothesis source — claims are not established convincingly; inspect the evidence gap before relying on them.</p>}
      {friction === 'high' && ['read', 'skim'].includes(digest.read_decision) && (
        <p>Idea preserved — high writing friction limits automatic advice to a targeted skim, not a judgment of bad science.</p>
      )}
      <Disclosure summary={`Idea: ${ideaLabel(digest)} · Evidence: ${evidence} · Writing: ${friction ? `${friction} friction` : 'not assessed'}`}>
        <dl className="space-y-2">
          <KeyVal label="Idea value">
            <p>Model assessments: Novelty {scoreText(digest.novelty)} · Significance {scoreText(digest.significance)}.</p>
            <p>{goalLabel(deep.goal_summaries || [])} {digest.relevance || ''}</p>
          </KeyVal>
          <KeyVal label="Scientific quality">
            <p>{nonPaper ? 'Not a research paper; scientific criteria do not apply.' : 'Independent type-aware QualityEval (model assessment); writing polish is excluded.'}</p>
            {!nonPaper && <p>{quality.paper_type ? paperTypeLabel(quality.paper_type) : 'Paper type not recorded'}
              {quality.coverage_standard ? ` · ${quality.coverage_standard}` : ''}.</p>}
            {!nonPaper && !BAND_LABEL[band] && <p>Scientific support was not assessed; a digest grade is not a substitute.</p>}
          </KeyVal>
          <KeyVal label={friction ? `Writing friction · ${friction}` : 'Writing friction'}>
            <p>Avoidable exposition friction, not topic difficulty or scientific validity.</p>
            {writingReasons.length ? <Bullets items={writingReasons} /> : <p>{friction === 'low'
              ? 'No concrete exposition failures were reported.'
              : friction ? 'Concrete writing reasons not recorded in this saved review.'
                : 'Missing writing signal is unknown, not clear writing.'}</p>}
          </KeyVal>
          {(capped || policyReasons.length > 0) && <KeyVal label="Reading policy">
            {capped && <p>Automatic policy capped the model&apos;s read recommendation to skim.</p>}
            <Bullets items={policyReasons} />
          </KeyVal>}
        </dl>
      </Disclosure>
    </div>
  );
}
