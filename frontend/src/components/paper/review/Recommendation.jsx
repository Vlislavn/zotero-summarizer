import { Chip } from './primitives.jsx';
import { formatShortDate, timeAgo } from '../../library/shared.jsx';
import { VERDICT_ACCENT } from './tones.js';
import Glance from './Glance.jsx';
import ReadingPlan from './ReadingPlan.jsx';
import OrthogonalSignals from './OrthogonalSignals.jsx';
import CodeLink from './CodeLink.jsx';

// Verdict chip colour: Forest solid = read it, ochre tint = skim, neutral = digest suffices.
const VERDICT_CHIP = {
  deep: 'border-forest-800 bg-forest-800 text-white',
  skim: 'border-amber-200 bg-amber-50 text-amber-800',
  skip: 'border-slate-200 bg-slate-100 text-slate-600',
};

// First sentence leads (the "why"); any remainder follows quieter in the same paragraph.
function WhyText({ text }) {
  const at = text.search(/[.!?](\s|$)/);
  if (at < 0 || at >= text.length - 1) return <>{text}</>;
  return <><span className="text-slate-900">{text.slice(0, at + 1)}</span>{' '}<span className="text-slate-600">{text.slice(at + 1).trim()}</span></>;
}

// The paper at a glance + the reading recommendation, ONE card on every surface:
// verdict chip → what the paper does (TL;DR) → the one-sentence assessment → quality
// and reported metrics → strength vs weakness → why, what to read first, the code row.
// `flat` (the full paper page) is the calm tinted card; the compact Library card and
// the other surfaces keep the left-rule banner with the same content at UI size.
export default function Recommendation({
  verdict, digest, deep, grade, band, redFlags, digestDecision, hasReadingDecision,
  isNonPaper, flat, strength = null, weakness = null,
}) {
  const minutes = hasReadingDecision && (digest.estimated_read_minutes != null
    ? <span className="text-[13px] text-slate-600" title="Saved reading-time estimate">{digest.estimated_read_minutes} min</span>
    : <span className="text-[12px] text-slate-500">Time not estimated</span>);
  // Non-paper (blog/news/web): no scientific grade applies — say so (ochre) so a
  // non-peer-reviewed source is never mistaken for a graded paper.
  const nonPaperChip = isNonPaper && (
    <Chip tone="amber" title="Web article / blog / news — not peer-reviewed. Reviewed for relevance only; no scientific quality grade applies.">
      Not peer-reviewed
    </Chip>
  );
  const tldr = digest?.tldr || '';
  const what = (
    <div className="mt-3 space-y-1.5">
      {tldr
        ? <p className={flat ? 'max-w-[66ch] font-display !text-[18px] font-light !leading-[1.3] text-slate-900 sm:!text-[20px]' : 'max-w-[66ch] text-[15px] leading-snug text-slate-900'}>{tldr}</p>
        : flat && <p>Contribution not recorded in this saved review.</p>}
      {digest?.verdict && <p className={`max-w-[66ch] text-slate-700 ${flat ? '!text-[15px] !leading-snug' : 'text-[13px] leading-snug'}`}>{digest.verdict}</p>}
      {flat && isNonPaper && <p className="!text-[14px] text-slate-600">Not a research paper — reviewed for relevance only; scientific quality criteria don&apos;t apply.</p>}
    </div>
  );
  const glance = <Glance digest={digest} quality={deep.quality} grade={grade} band={band} redFlags={redFlags}
    isNonPaper={isNonPaper} strength={strength} weakness={weakness} />;
  const why = verdict.reason && (
    <p className={`mt-3 max-w-[66ch] border-t border-slate-200/70 pt-3 leading-snug text-slate-700 ${flat ? '!text-[15px]' : 'text-[13px]'}`}>
      {flat ? <WhyText text={verdict.reason} /> : verdict.reason}
    </p>
  );
  const plan = hasReadingDecision && <ReadingPlan digest={digest} decision={digestDecision} />;
  const signals = digest && <OrthogonalSignals deep={deep} folded={flat} />;

  if (flat) {
    return (
      <div className="rounded-xl border border-slate-200 bg-forest-50/60 px-4 py-4 sm:px-5">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <span className={`inline-flex items-center rounded-full border px-3 py-0.5 text-[12px] font-medium uppercase tracking-[0.1em] ${VERDICT_CHIP[verdict.key] || VERDICT_CHIP.skip}`}>
            {verdict.label}
          </span>
          {minutes}
          {nonPaperChip}
          {deep.reviewed_at && <span className="text-[12px] text-slate-500" title={formatShortDate(deep.reviewed_at)}>reviewed {timeAgo(deep.reviewed_at)}</span>}
        </div>
        {what}
        {glance}
        {why}
        {plan}
        {signals}
        {deep.code_link && <div className="mt-3 border-t border-slate-200/70 pt-3"><CodeLink codeLink={deep.code_link} /></div>}
      </div>
    );
  }
  return (
    <div className={`rounded-lg border-l-[3px] px-3.5 py-3 ${VERDICT_ACCENT[verdict.key] || VERDICT_ACCENT.skip}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-display text-[22px] font-light tracking-tight text-slate-900">{verdict.label}</span>
        {hasReadingDecision && (digest.estimated_read_minutes != null
          ? <Chip tone="slate" title="Saved reading-time estimate">{digest.estimated_read_minutes} min</Chip>
          : <span className="text-[12px] text-slate-500">Time not estimated</span>)}
        {nonPaperChip}
      </div>
      {what}
      {glance}
      {why}
      {plan}
      {signals}
    </div>
  );
}
