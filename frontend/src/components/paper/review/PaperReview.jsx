import { Chip, Disclosure } from './primitives.jsx';
import { gradeTone, qualityBasis } from './tones.js';
import { formatShortDate, timeAgo } from '../../library/shared.jsx';
import CodeLink from './CodeLink.jsx';
import Recommendation from './Recommendation.jsx';
import { reviewSource, deriveReview, isLegacyGradeOnly, isEmptyReview } from './reviewModel.js';
import { groundedField } from './grounded.jsx';
import { ReadingFolds } from './ReadingFolds.jsx';
import { CardSections } from './CardSections.jsx';

// Why a stored review is outdated, in plain language (the identity/audit reason
// codes come from the server's review_freshness). A stale review is SHOWN with
// this badge — never silently hidden — so you can read it and refresh when ready.
const FRESHNESS_REASON = {
  generation_updated: 'model or prompts changed',
  pdf_changed: 'the PDF changed',
  source_changed: 'the source changed',
  source_unresolved: 'source could not be re-checked',
  focus_changed: 'review focus changed',
  unverified: 'written before the current checks',
  verification_outdated: 'checks need refreshing',
  metadata_changed: 'inputs changed',
};

// One quiet line + the action: a stale review stays readable, refresh is one tap.
function FreshnessBadge({ freshness, onRegenerate }) {
  if (!freshness || freshness.status !== 'outdated') return null;
  const why = FRESHNESS_REASON[freshness.reason] || FRESHNESS_REASON.metadata_changed;
  return (
    <p
      role="status"
      className="mb-4 flex flex-wrap items-center gap-x-3 gap-y-0.5 border-l-2 border-amber-300 pl-3 !text-[13px] !leading-snug text-amber-800"
    >
      <span><span className="font-medium">Outdated review</span>: {why}.</span>
      {onRegenerate && (
        <button type="button" onClick={onRegenerate} className="-my-3 inline-flex min-h-11 items-center font-medium text-forest-800 underline underline-offset-2 hover:text-forest-700">
          Regenerate
        </button>
      )}
    </p>
  );
}

// Legacy cache (pre-digest): show the old grade, nudge a re-run.
function LegacyGrade({ quality, flat }) {
  return (
    <div className={`text-[13px] text-slate-600 ${flat ? 'review-reading' : ''}`}>
      <Chip tone={gradeTone(quality.grade, quality.quality_band)}>{['Rigor ' + quality.grade, qualityBasis(quality)].filter(Boolean).join(' · ')}</Chip>
      {quality.verdict ? <span className="ml-2">{quality.verdict}</span> : null}
      <div className="mt-1 text-[11px] text-slate-400">Older review — re-run for the new digest.</div>
    </div>
  );
}

function ReviewFooter({ deep, compact, flat }) {
  if (compact || !((deep.reviewed_at && !flat) || deep.zotero_note_error)) return null;
  return (
    <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-slate-500">
      {deep.reviewed_at && !flat && <span title={formatShortDate(deep.reviewed_at)}>reviewed {timeAgo(deep.reviewed_at)}</span>}
      {deep.zotero_note_error && <Disclosure summary="Zotero note not written"><span className="text-amber-800">{deep.zotero_note_error}</span></Disclosure>}
    </div>
  );
}

// The glance card + the sections under it. The verdict chip stays the single loud element.
function ReviewColumn({ deep, model: m, flat, compact, facts, evidence, methodCard, onRegenerate }) {
  // A field that could not be grounded is said so in its place, never shown as verified.
  const grounded = (field) => groundedField(field, m.digest, m.lost, onRegenerate);
  const recommendation = (
    <Recommendation verdict={m.verdict} digest={m.digest} deep={deep} grade={m.grade} band={m.band} redFlags={m.redFlags}
      digestDecision={m.digestDecision} hasReadingDecision={m.hasReadingDecision} isNonPaper={m.isNonPaper}
      flat={flat} strength={grounded('key_strength')} weakness={grounded('key_weakness')} summary={grounded('executive_summary')} />
  );
  // Reading-size type applies to the review itself; the `facts` slot between the
  // verdict card and the folds keeps its own compact scale, so it sits outside it.
  const reading = flat ? 'review-reading' : undefined;

  return (
    <div className="review-prose text-slate-800">
      <div className={reading}>
        <FreshnessBadge freshness={deep.review_freshness} onRegenerate={onRegenerate} />
        {/* Code repository, pulled to the very top (the first thing you want when
            deciding to reproduce). Full surfaces only — the compact Library card
            stays clean. Older cached reviews have no code_link → renders nothing. */}
        {!compact && !flat && deep.code_link && <CodeLink codeLink={deep.code_link} />}

        {/* The glance card — verdict, what the paper does, quality, reported metrics,
            strength vs weakness — leads every surface; the verdict chip stays the single
            loud element (Von Restorff). The gloss, flag texts and checklist follow below
            (in Details on the compact Library card). */}
        {flat ? <div className="mb-5">{recommendation}</div> : recommendation}
      </div>
      {flat && facts && <div className="mb-5 max-w-[66ch]">{facts}</div>}
      <div className={reading}>
        <div className={flat ? '' : 'mt-2 divide-y divide-slate-200/60'}>
          {flat
            ? <ReadingFolds model={m} onRegenerate={onRegenerate} evidence={evidence} methodCard={methodCard} />
            : <CardSections model={m} compact={compact} reviewedAt={deep.reviewed_at} onRegenerate={onRegenerate} />}
        </div>
        <ReviewFooter deep={deep} compact={compact} flat={flat} />
      </div>
    </div>
  );
}

// The native paper review: ONE reading column rendered from the cached
// deep_review payload ({digest, quality, goal_summaries}). It replaces both the
// old indigo DigestBlock and the iframe brief — same decision data, one design
// language, reading-grade type, flat hierarchy (Common Region / Uniform
// Connectedness: hairline dividers + whitespace, not nested boxes). Reads the
// SAME as the standalone presentation.html (briefModel mirrors the server).
export default function PaperReview({ deep, compact = false, flat: requestedFlat = false, sectionOverlay = null, onRegenerate = null, evidence = null, facts = null, methodCard = null }) {
  if (!deep) return null;
  const flat = requestedFlat && !compact;
  const source = reviewSource(deep);
  if (isLegacyGradeOnly(source)) return <LegacyGrade quality={source.quality} flat={flat} />;
  if (isEmptyReview(source)) return null;
  return <ReviewColumn deep={deep} model={deriveReview(source, sectionOverlay)} flat={flat} compact={compact}
    facts={facts} evidence={evidence} methodCard={methodCard} onRegenerate={onRegenerate} />;
}
