import { Chip, SectionLabel, Bullets, SectionAnchor } from './primitives.jsx';
import { CritiqueViews } from './CritiqueViews.jsx';
import { bandGloss, dedupeFlags, rubricLabel, paperTypeLabel, standardLabel } from './briefModel.js';

// Critique (red flags / overstatements) is the highest-value but most-BRITTLE
// signal — research on shipped tools shows the sentence/citation classifiers that
// power located critique are weak (AI2 Scim F1 0.53 vs 0.73 ceiling; scite's
// 'contrasting' class caught 0/17 in an independent audit), and because the
// signal is color-coded, errors visibly erode trust. So we ALWAYS frame it as a
// model judgment, and demote it to 'tentative' (verify) when the self-consistency
// runs disagreed, agreement was low, or model confidence was low. Named floors,
// not magic numbers (the operating point `localization_stats` helps calibrate).
const CRITIQUE_AGREE_MIN = 0.6;   // min self-consistency agreement to assert critique
const CRITIQUE_CONF_MIN = 0.5;    // min model confidence to assert critique
function critiqueIsTentative(q = {}) {
  const total = Number(q.passes_total) || 0;
  const agreed = Number(q.passes_agreed) || 0;
  const conf = Number(q.confidence) || 0;
  if (String(q.quality_band || '') === 'uncertain') return true;
  if (total > 0 && agreed / total < CRITIQUE_AGREE_MIN) return true;
  if (conf > 0 && conf < CRITIQUE_CONF_MIN) return true;
  return false;
}

function MissingCritical({ missing, missLoc }) {
  return (
    <div className="mt-1 text-[12px] leading-relaxed text-rose-700">
      Missing (critical):{' '}
      {missing.map((k, i) => {
        const loc = missLoc?.get(String(k).trim());
        return (
          <span key={k}>
            {i > 0 && ', '}
            {rubricLabel(k)}
            {loc && <span className="ml-1 align-middle"><SectionAnchor section={loc} /></span>}
          </span>
        );
      })}
    </div>
  );
}

// Coverage panel — the honest replacement for the unvalidated 1-5 scores: which
// recognized standard was applied to THIS paper type, how many applicable items
// it met, and which critical ones are missing (each is a grounded checklist item
// in the full checklist below).
function CoveragePanel({ quality, missLoc, critiqueViews }) {
  const standard = String(quality.coverage_standard || '');
  const met = Number(quality.coverage_met) || 0;
  const applicable = Number(quality.coverage_applicable) || 0;
  const missing = (quality.missing_critical || []).map((x) => String(x || '').trim()).filter(Boolean);
  if (!(applicable > 0 || missing.length > 0)) return null;
  return (
    <div>
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-[13px]">
        {/* The reading page names the checklist and paper type once, beside the full checklist. */}
        {!critiqueViews && standard && <span className="font-medium text-slate-900">{standardLabel(standard)}</span>}
        <span className="text-slate-700">{applicable > 0 ? `${met}/${applicable} applicable items met` : 'Checklist coverage unavailable'}</span>
        {!critiqueViews && <PaperTypeChip quality={quality} />}
      </div>
      {missing.length > 0 && <MissingCritical missing={missing} missLoc={missLoc} />}
    </div>
  );
}

function FlagList({ redFlags, flagLoc }) {
  return (
    <ul className="space-y-2.5 text-[13px] leading-relaxed text-slate-800">
      {redFlags.map((x, i) => {
        const loc = flagLoc?.get(String(x).trim());
        return (
          <li key={i}>
            {x}
            {loc && <span className="ml-1.5 align-middle"><SectionAnchor section={loc} /></span>}
          </li>
        );
      })}
    </ul>
  );
}

function RedFlagsBox({ quality, band, redFlags, flagLoc, critiqueViews }) {
  const tentative = critiqueIsTentative(quality);
  const flagged = band === 'flag';  // rule D: rose only when the band says flag
  return (
    <div className={`border-l-2 pl-4 ${flagged ? 'border-rose-300' : 'border-slate-300'}`}>
      <div className="mb-2 flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className={`text-[11px] font-medium uppercase tracking-[0.08em] ${flagged ? 'text-rose-700' : 'text-slate-600'}`}>
          {flagged ? 'Red flags' : `Notes to verify (${redFlags.length})`}
        </span>
        <span className={`text-[11px] ${flagged ? 'text-rose-700' : 'text-slate-600'}`} title="Model-generated critique — verify against the paper">· AI-generated, verify</span>
        {tentative && (
          <span className="text-[11px] text-amber-700" title="Self-consistency runs disagreed or confidence was low — treat as a prompt to check, not a finding">low confidence</span>
        )}
      </div>
      {critiqueViews
        ? <CritiqueViews flags={redFlags} locations={flagLoc} rubric={quality.rubric} />
        : <FlagList redFlags={redFlags} flagLoc={flagLoc} />}
    </div>
  );
}

// The DECISION half of the quality read, shown by default: the band gloss + the
// loud red-flags callout (the only semantic box). Everything that explains HOW the
// band was reached moves to QualityDetails, behind the one "Details" disclosure.
export function QualityHeadline({ quality, band, flagLoc, missLoc, critiqueViews = false }) {
  const redFlags = dedupeFlags((quality.red_flags || []).map((x) => String(x || '').trim()).filter(Boolean));
  // Derived so the gloss never says "No red flags" while the box below lists some.
  const gloss = bandGloss(band, redFlags.length > 0);
  return (
    <div className="space-y-2.5">
      {gloss && (
        <p className="text-[13px] leading-relaxed text-slate-700 max-w-[66ch]">
          <span className="font-medium text-slate-900">{gloss.lead}</span> {gloss.body}
        </p>
      )}
      <CoveragePanel quality={quality} missLoc={missLoc} critiqueViews={critiqueViews} />
      {redFlags.length > 0 && (
        <RedFlagsBox quality={quality} band={band} redFlags={redFlags} flagLoc={flagLoc} critiqueViews={critiqueViews} />
      )}
    </div>
  );
}

function PaperTypeChip({ quality }) {
  const ptype = String(quality.paper_type || '');
  if (!ptype) return null;
  const uncertain = ptype.startsWith('generic_');
  return (
    <Chip
      tone={uncertain ? 'amber' : 'slate'}
      title={uncertain
        ? 'Paper type was uncertain — judged against a safe generic checklist. Open the paper to verify.'
        : 'Detected paper type — the checklist is chosen for this type'}
    >
      {paperTypeLabel(ptype)}{uncertain ? ' ⚠' : ''}
    </Chip>
  );
}

// Which checklist judged the paper, and as what type — the reference line above the full checklist.
export function ChecklistBasis({ quality }) {
  const standard = String(quality.coverage_standard || '');
  if (!standard && !quality.paper_type) return null;
  return (
    <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-[13px]">
      {standard && <span className="font-medium text-slate-900">{standardLabel(standard)}</span>}
      <PaperTypeChip quality={quality} />
    </div>
  );
}

export function MaterialClaims({ quality }) {
  const items = (quality.overstatements || []).filter(Boolean);
  if (!items.length) return null;
  return <div className="mt-2">
    <SectionLabel level={3}>Possible overstatement (low reliability)</SectionLabel>
    <Bullets items={items} />
  </div>;
}
