import { Bullets, Fold } from './primitives.jsx';
import { bandGloss, shortGoal, isSupportedGoal } from './briefModel.js';
import { StudyFlow, ImpactGrid } from './StudyMap.jsx';
import { GoalBoard } from './GoalBoard.jsx';
import { QualityHeadline, ChecklistBasis, MaterialClaims } from './QualityHeadline.jsx';
import { QualityDetails } from './QualityDetails.jsx';
import { DigestRow, DigestRows } from './DigestRows.jsx';
import { GLANCE_FIELDS, IMPACT_FIELDS } from './reviewModel.js';
import { groundedField } from './grounded.jsx';

function clip(text, max) {
  const t = String(text || '').replace(/\s+/g, ' ').trim();
  return t.length > max ? `${t.slice(0, max).replace(/\s+\S*$/, '')}…` : t;
}

// The closed "Caveats and coverage" row: the model's notes first (clay only when the band is flag), then coverage and gaps.
function caveatsSummary(quality, band, isNonPaper, redFlags) {
  if (!quality || isNonPaper) return 'Source basis and coverage';
  const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
  const applicable = Number(quality.coverage_applicable) || 0;
  const gaps = (quality.missing_critical || []).filter(Boolean).length;
  const rest = [
    applicable > 0 && `${Number(quality.coverage_met) || 0}/${applicable} checklist items met`,
    gaps > 0 && plural(gaps, 'critical gap'),
  ].filter(Boolean);
  if (!redFlags.length && !rest.length) return bandGloss(band, false).lead || 'Source basis and coverage';
  return <>
    {redFlags.length > 0 && (band === 'flag'
      ? <span className="text-rose-700">{plural(redFlags.length, 'red flag')} to verify</span>
      : <span>Notes to verify ({redFlags.length})</span>)}
    {redFlags.length > 0 && rest.length > 0 && ' · '}{rest.join(' · ')}
  </>;
}

function HowItWorksFold({ m, onRegenerate, methodCard }) {
  const { digest, lost } = m;
  return (
    <Fold label="How it works" defaultOpen summary={clip(digest?.methods || digest?.limitations, 110)} feedbackField="methods_limits">
      {methodCard}
      <div className="mb-4"><StudyFlow parameters={digest?.parameters} /></div>
      <dl className="space-y-3">
        <DigestRow field="methods" label="Methods" lost={lost} onRegenerate={onRegenerate}>{digest?.methods}</DigestRow>
        <DigestRow field="limitations" label="Limitations" lost={lost} onRegenerate={onRegenerate}>{digest?.limitations}</DigestRow></dl>
      {!digest?.methods && !digest?.limitations && !lost.some(f => f === 'methods' || f === 'limitations') && <p>Methods and limitations not recorded in this saved review.</p>}
    </Fold>
  );
}

function AssessmentFold({ m, onRegenerate }) {
  const { digest, lost } = m;
  const grounded = (field) => groundedField(field, digest, lost, onRegenerate);
  return (
    <Fold label="Assessment" defaultOpen feedbackField="assessment">
      <div className="space-y-5">
        <ImpactGrid cells={[['Impact', grounded('impact')], ['Industry', grounded('industry_impact')],
          ['Academia', grounded('academy_impact')], ['Unknowns', grounded('unknown_unknowns')]]} />
        <DigestRows digest={{ ...digest, parameters: null }} lost={lost} onRegenerate={onRegenerate}
          exclude={[...GLANCE_FIELDS, ...IMPACT_FIELDS, 'key_findings', 'read_why', 'methods', 'limitations', 'executive_summary']} />
      </div>
    </Fold>
  );
}

function CaveatsFold({ m }) {
  const { quality, band, isNonPaper, redFlags, partialBasis, flagLoc, missLoc } = m;
  return (
    <Fold label="Caveats and coverage" defaultOpen feedbackField="quality" summary={caveatsSummary(quality, band, isNonPaper, redFlags)}>
      <div className="space-y-4">
        {partialBasis.length > 0 && <p className="!text-[14px] text-slate-600">Not based on the full text — {partialBasis.join('; ')}.</p>}
        {quality && !isNonPaper && <QualityHeadline quality={quality} band={band} flagLoc={flagLoc} missLoc={missLoc} critiqueViews />}
        {quality && !isNonPaper && <MaterialClaims quality={quality} />}
        {!quality && <p>Assessment and source coverage not recorded in this saved review.</p>}
      </div>
    </Fold>
  );
}

function RelevanceFold({ m }) {
  const { goals, nHitGoals, goalLoc } = m;
  return (
    <Fold label={`Relevance — ${nHitGoals} of ${goals.length} goals addressed`}
      summary={goals.filter(isSupportedGoal).map(g => shortGoal(g.goal)).join(' · ') || 'No supported goal match recorded'} feedbackField="relevance">
      <GoalBoard goals={goals} goalLoc={goalLoc} foldReasons />
    </Fold>
  );
}

function ReferenceFold({ m }) {
  const { quality, digest, band, isNonPaper } = m;
  return (
    <Fold label="Reference" summary="Full checklist and how it was judged">
      <div className="space-y-4">
        {quality && !isNonPaper && <ChecklistBasis quality={quality} />}
        {quality && !isNonPaper && <QualityDetails quality={quality} band={band} hideMaterial />}
        <p className="!text-[13px] text-slate-500">Saved source basis — quality: {quality?.basis || 'not recorded'}; digest: {digest?.basis || 'not recorded'}. Reviewed extent not recorded.</p>
      </div>
    </Fold>
  );
}

// The full story page: the review as a stack of folds, every section in reading order.
export function ReadingFolds({ model: m, onRegenerate, evidence, methodCard }) {
  return <>
    <HowItWorksFold m={m} onRegenerate={onRegenerate} methodCard={methodCard} />
    <Fold label="Findings and applicability" defaultOpen feedbackField="findings">
      {m.findings.length > 0 ? <Bullets items={m.findings} /> : <p>Key findings not recorded in this saved review.</p>}
    </Fold>
    {m.digest && <AssessmentFold m={m} onRegenerate={onRegenerate} />}
    <CaveatsFold m={m} />
    {evidence}
    {m.goals.length > 0 && <RelevanceFold m={m} />}
    <ReferenceFold m={m} />
  </>;
}
