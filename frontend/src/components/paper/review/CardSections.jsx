import { Section, SectionLabel, Disclosure } from './primitives.jsx';
import { BAND_LABEL } from './tones.js';
import { formatShortDate, timeAgo } from '../../library/shared.jsx';
import { GoalBoard } from './GoalBoard.jsx';
import { QualityHeadline } from './QualityHeadline.jsx';
import { QualityDetails } from './QualityDetails.jsx';
import { DigestRows } from './DigestRows.jsx';
import { GLANCE_FIELDS } from './reviewModel.js';

// The "Details" tail: gated behind ONE disclosure on the card (Cognitive Load).
function DetailsBody({ m, compact, reviewedAt, onRegenerate }) {
  const { digest, quality, band, isNonPaper, lost } = m;
  return (
    <div className="space-y-4">
      {isNonPaper && (
        <p className="text-[12px] text-slate-500 border border-slate-200 rounded-md px-2 py-1 bg-slate-50">
          Not a research paper — reviewed for <span className="font-medium">relevance</span> only;
          scientific quality criteria (grade, rigor) don't apply.
        </p>
      )}
      {compact && quality && !isNonPaper && <QualityHeadline quality={quality} band={band} />}
      {quality && !isNonPaper && <QualityDetails quality={quality} band={band} />}
      {digest && (
        <div>
          <SectionLabel>Full digest</SectionLabel>
          <div className="mt-1.5"><DigestRows digest={{ ...digest, parameters: digest.parameters && { ...digest.parameters, metrics: [] } }}
            lost={lost} onRegenerate={onRegenerate} exclude={GLANCE_FIELDS} /></div>
        </div>
      )}
      {compact && reviewedAt && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-slate-500">
          <span title={formatShortDate(reviewedAt)}>reviewed {timeAgo(reviewedAt)}</span>
        </div>
      )}
    </div>
  );
}

// The Library card and the non-flat surfaces: the decision spine under the glance card.
export function CardSections({ model: m, compact, reviewedAt, onRegenerate }) {
  const { goals, quality, digest, band, isNonPaper, nHitGoals, goalLoc, flagLoc, missLoc } = m;
  return <>
    {/* compact (Library row card): the per-goal board is the biggest block and
        is reachable in the new-tab brief — drop it, keep the decision spine. */}
    {!compact && goals.length > 0 && (
      <Section label={`Relevance — ${nHitGoals} of ${goals.length} goals addressed`}>
        <GoalBoard goals={goals} goalLoc={goalLoc} />
      </Section>
    )}

    {/* Full quality headline on the full surfaces; in compact the band +
        red-flag count already live as chips in the banner row above, so the
        standalone section is dropped (gloss/coverage move into Details). */}
    {quality && !isNonPaper && !compact && (
      <Section label={`Quality — ${BAND_LABEL[band] || '—'}`}>
        <QualityHeadline quality={quality} band={band} flagLoc={flagLoc} missLoc={missLoc} />
      </Section>
    )}

    {/* Glance first (Cognitive Load / Miller): everything below it — how the
        grade was reached, the full rubric, the structured digest — lives
        behind ONE disclosure. Quiet by default, deep
        on demand. In compact, the full quality headline lives here too. */}
    {(quality || digest) && (
      <Section>
        <Disclosure summary="Details">
          <DetailsBody m={m} compact={compact} reviewedAt={reviewedAt} onRegenerate={onRegenerate} />
        </Disclosure>
      </Section>
    )}
  </>;
}
