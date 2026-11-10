import { Chip } from './primitives.jsx';
import { formatShortDate, timeAgo } from '../../library/shared.jsx';
import { gradeTone, qualityBasis, bandTone, BAND_LABEL, VERDICT_ACCENT } from './tones.js';
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

// The reading recommendation. `flat` (the full paper page) is ONE calm summary
// card: verdict chip + quiet facts, the one-sentence why, what to read first, the
// code row. The compact Library card and the non-flat surfaces keep the left-rule
// banner, as before.
export default function Recommendation({
  verdict, digest, deep, grade, band, redFlags, digestDecision, hasReadingDecision,
  isNonPaper, compact, flat,
}) {
  const minutes = hasReadingDecision && (digest.estimated_read_minutes != null
    ? <span className="text-[13px] text-slate-600" title="Saved reading-time estimate">{digest.estimated_read_minutes} min</span>
    : <span className="text-[12px] text-slate-500">Time not estimated</span>);
  const gradeChip = grade && (
    <Chip tone={gradeTone(grade)} title="Saved quality grade">{[compact ? grade : `Quality ${grade}`, qualityBasis(deep.quality)].filter(Boolean).join(' · ')}</Chip>
  );
  // Non-paper (blog/news/web): no scientific grade applies — say so (ochre) so a
  // non-peer-reviewed source is never mistaken for a graded paper.
  const nonPaperChip = isNonPaper && (
    <Chip tone="amber" title="Web article / blog / news — not peer-reviewed. Reviewed for relevance only; no scientific quality grade applies.">
      Not peer-reviewed
    </Chip>
  );
  const why = verdict.reason && (
    <p className={`max-w-[66ch] leading-relaxed text-slate-700 ${flat ? 'mt-3 text-[17px] !leading-[1.55]' : 'mt-1.5 text-[13px]'}`}>
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
          {gradeChip}
          {nonPaperChip}
          {deep.reviewed_at && <span className="text-[12px] text-slate-500" title={formatShortDate(deep.reviewed_at)}>reviewed {timeAgo(deep.reviewed_at)}</span>}
        </div>
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
        {gradeChip}
        {nonPaperChip}
        {compact && band && <Chip tone={bandTone(band)}>{BAND_LABEL[band] || '—'}</Chip>}
        {compact && redFlags.length > 0 && (
          <Chip tone="rose" title={redFlags.slice(0, 3).join('; ')}>⚠ {redFlags.length}</Chip>
        )}
      </div>
      {why}
      {plan}
      {signals}
    </div>
  );
}
