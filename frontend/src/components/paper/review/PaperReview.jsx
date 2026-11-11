import { Section, SectionLabel, Disclosure, Chip, KeyVal, Bullets, Fold } from './primitives.jsx';
import { gradeTone, qualityBasis, BAND_LABEL } from './tones.js';
import {
  bandGloss, dedupeFlags, METHOD_CLAUSE, LEGEND, rubricLabel, paperTypeLabel, standardLabel,
  decisiveRows, fullChecklist, shortGoal, isSupportedGoal,
} from './briefModel.js';
import { formatShortDate, timeAgo } from '../../library/shared.jsx';
import CodeLink from './CodeLink.jsx';
import Recommendation from './Recommendation.jsx';
import { StudyFlow, ImpactGrid } from './StudyMap.jsx';

const STATE_LABEL = {
  hit: '● addressed', miss: '○ not addressed', not_retrieved: '⚠ not retrieved',
};

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

function clip(text, max) {
  const t = String(text || '').replace(/\s+/g, ' ').trim();
  return t.length > max ? `${t.slice(0, max).replace(/\s+\S*$/, '')}…` : t;
}

// The closed "Caveats and coverage" row: red flags first (clay), then coverage and gaps.
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
    {redFlags.length > 0 && <span className="text-rose-700">{plural(redFlags.length, 'red flag')} to verify</span>}
    {redFlags.length > 0 && rest.length > 0 && ' · '}{rest.join(' · ')}
  </>;
}

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

// The native paper review: ONE reading column rendered from the cached
// deep_review payload ({digest, quality, goal_summaries}). It replaces both the
// old indigo DigestBlock and the iframe brief — same decision data, one design
// language, reading-grade type, flat hierarchy (Common Region / Uniform
// Connectedness: hairline dividers + whitespace, not nested boxes). Reads the
// SAME as the standalone presentation.html (briefModel mirrors the server).
export default function PaperReview({ deep, compact = false, flat: requestedFlat = false, sectionOverlay = null, onRegenerate = null, evidence = null, facts = null }) {
  if (!deep) return null;
  const flat = requestedFlat && !compact;
  const digest = deep.digest && Object.keys(deep.digest).length ? deep.digest : null;
  const quality = deep.quality || null;
  const goals = deep.goal_summaries || [];
  const lost = deep.not_established_fields || [];

  // Legacy cache (pre-digest): show the old grade, nudge a re-run.
  if (!digest && quality && quality.grade && quality.basis !== 'non_paper' && !goals.length) {
    return (
      <div className={`text-[13px] text-slate-600 ${flat ? 'review-reading' : ''}`}>
        <Chip tone={gradeTone(quality.grade)}>{['Quality ' + quality.grade, qualityBasis(quality)].filter(Boolean).join(' · ')}</Chip>
        {quality.verdict ? <span className="ml-2">{quality.verdict}</span> : null}
        <div className="mt-1 text-[11px] text-slate-400">Older review — re-run for the new digest.</div>
      </div>
    );
  }
  if (!digest && !quality && !goals.length) return null;

  // Non-paper (web article / blog / news): scientific quality criteria don't apply —
  // reviewed for relevance only, so suppress the rigor panels + grade and lead with the
  // digest's own read decision instead of the goals×rigor verdict.
  const isNonPaper = quality?.basis === 'non_paper';
  const band = isNonPaper ? '' : String(quality?.quality_band || '');
  const redFlags = isNonPaper ? [] : dedupeFlags((quality?.red_flags || []).map((x) => String(x || '').trim()).filter(Boolean));
  const nHitGoals = goals.filter(isSupportedGoal).length;
  const overstated = quality && !isNonPaper ? (quality.overstatements || []).filter(Boolean) : [];
  const findings = (digest?.key_findings || []).filter(text => !overstated.includes(text));

  // The API projects cached decisions through the current policy; absence stays unknown.
  let verdict;
  const digestDecision = String(digest?.read_decision || '').toLowerCase();
  const hasReadingDecision = ['read', 'skim', 'skip'].includes(digestDecision);
  if (hasReadingDecision) {
    verdict = {
      key: digestDecision === 'read' ? 'deep' : digestDecision,
      label: { read: 'READ', skim: 'SKIM', skip: 'DIGEST IS ENOUGH' }[digestDecision],
      reason: digest.read_why || '',
    };
  } else {
    verdict = { key: 'skip', label: 'REVIEW', reason: 'Reading decision unavailable; review the evidence.' };
  }
  // The server projects scientific grade with the reading action. A separate
  // legacy quality-only review has no digest; raw model_digest never supplies a grade.
  const grade = isNonPaper ? '' : (digest ? digest.grade || '' : quality?.grade || '');

  // Located findings (the story page passes the section overlay; the compact card
  // does not → unchanged). Keyed by VALUE (goal/flag text, critical item) so the
  // join survives the filter/reorder the render does below. Empty maps = no chips.
  const ov = sectionOverlay && !sectionOverlay.degraded ? sectionOverlay : null;
  const goalLoc = new Map((ov?.goals || []).map((o) => [o.goal, o.sections || []]));
  const flagLoc = new Map((ov?.red_flags || []).filter((r) => r.section).map((r) => [String(r.text || '').trim(), { ...r.section, match: r.match }]));
  const missLoc = new Map((ov?.missing_critical || []).filter((r) => r.section).map((r) => [String(r.item || '').trim(), { ...r.section, match: r.match }]));

  // The "Details" tail: gated behind ONE disclosure on the card (Cognitive Load),
  // rendered inline on the full story page (`flat` — the whole point is no clicks
  // to read the paper's information).
  const detailsInner = (
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
      {compact && deep.reviewed_at && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-slate-500">
          <span title={formatShortDate(deep.reviewed_at)}>reviewed {timeAgo(deep.reviewed_at)}</span>
        </div>
      )}
    </div>
  );

  // A field that could not be grounded is said so in its place, never shown as verified.
  const grounded = (field) => (lost.includes(field) ? <Ungrounded onRegenerate={onRegenerate} /> : digest?.[field] || null);
  const recommendation = (
    <Recommendation verdict={verdict} digest={digest} deep={deep} grade={grade} band={band} redFlags={redFlags}
      digestDecision={digestDecision} hasReadingDecision={hasReadingDecision} isNonPaper={isNonPaper}
      flat={flat} strength={grounded('key_strength')} weakness={grounded('key_weakness')} />
  );

  // Reading-size type applies to the review itself; the `facts` slot between the
  // verdict card and the folds keeps its own compact scale, so it sits outside it.
  const reading = flat ? 'review-reading' : undefined;
  // Only a basis other than the full text is a caveat; the literal record is in the reference fold.
  const partialBasis = [['quality check', quality?.basis], ['digest', digest?.basis]]
    .filter(([, basis]) => basis && basis !== 'full_text')
    .map(([what, basis]) => `${what}: ${String(basis).replace(/_/g, ' ')}`);

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
      {!flat && recommendation}

      {/* (The Rigor·Relevance summary spine was removed — it restated the two
          sections immediately below it, "Relevance to your goals" + "Quality —
          {band}", which carry the same numbers with their own labels.) */}
      {flat && (
        <div className="mb-5">{recommendation}</div>
      )}
      </div>
      {flat && facts && <div className="mb-5 max-w-[66ch]">{facts}</div>}
      <div className={reading}>
      <div className={flat ? '' : 'mt-2 divide-y divide-slate-200/60'}>
        {flat ? <>
          <Fold label="Findings and applicability" defaultOpen>
            {findings.length > 0 ? <Bullets items={findings} /> : <p>Key findings not recorded in this saved review.</p>}
          </Fold>
          {digest && (
            <Fold label="Assessment" defaultOpen>
              <div className="space-y-5">
                <StudyFlow parameters={digest.parameters} />
                <ImpactGrid cells={[['Impact', grounded('impact')], ['Industry', grounded('industry_impact')],
                  ['Academia', grounded('academy_impact')], ['Unknowns', grounded('unknown_unknowns')]]} />
                <DigestRows digest={{ ...digest, parameters: null }} lost={lost} onRegenerate={onRegenerate}
                  exclude={[...GLANCE_FIELDS, ...IMPACT_FIELDS, 'key_findings', 'read_why', 'methods', 'limitations', 'executive_summary']} />
              </div>
            </Fold>
          )}
          <Fold label="Caveats and coverage" defaultOpen summary={caveatsSummary(quality, band, isNonPaper, redFlags)}>
            <div className="space-y-4">
              {partialBasis.length > 0 && <p className="!text-[14px] text-slate-600">Not based on the full text — {partialBasis.join('; ')}.</p>}
              {quality && !isNonPaper && <QualityHeadline quality={quality} band={band} flagLoc={flagLoc} missLoc={missLoc} critiqueViews />}
              {quality && !isNonPaper && <MaterialClaims quality={quality} />}
              {!quality && <p>Assessment and source coverage not recorded in this saved review.</p>}
            </div>
          </Fold>
          {evidence}
          {goals.length > 0 && (
            <Fold label={`Relevance — ${nHitGoals} of ${goals.length} goals addressed`}
              summary={goals.filter(isSupportedGoal).map(g => shortGoal(g.goal)).join(' · ') || 'No supported goal match recorded'}>
              <GoalBoard goals={goals} goalLoc={goalLoc} foldReasons />
            </Fold>
          )}
          <Fold label="Methods and limitations" summary={clip(digest?.methods || digest?.limitations, 110)}>
            <dl className="space-y-3">
              <DigestRow field="methods" label="Methods" lost={lost} onRegenerate={onRegenerate}>{digest?.methods}</DigestRow>
              <DigestRow field="limitations" label="Limitations" lost={lost} onRegenerate={onRegenerate}>{digest?.limitations}</DigestRow></dl>
            {!digest?.methods && !digest?.limitations && !lost.some(f => f === 'methods' || f === 'limitations') && <p>Methods and limitations not recorded in this saved review.</p>}
          </Fold>
          <Fold label="Reference" summary="Summary, full checklist and how it was judged">
            <div className="space-y-4">
              <dl><DigestRow field="executive_summary" label="Summary" lost={lost} onRegenerate={onRegenerate}>{digest?.executive_summary}</DigestRow></dl>
              {quality && !isNonPaper && <ChecklistBasis quality={quality} />}
              {quality && !isNonPaper && <QualityDetails quality={quality} band={band} hideMaterial />}
              <p className="!text-[13px] text-slate-500">Saved source basis — quality: {quality?.basis || 'not recorded'}; digest: {digest?.basis || 'not recorded'}. Reviewed extent not recorded.</p>
            </div>
          </Fold>
        </> : <>

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
          <Section label={flat ? 'Details' : undefined}>
            {flat ? detailsInner : <Disclosure summary="Details">{detailsInner}</Disclosure>}
          </Section>
        )}
        </>}
      </div>

      {!compact && ((deep.reviewed_at && !flat) || deep.zotero_note_error) && (
        <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-slate-500">
          {deep.reviewed_at && !flat && <span title={formatShortDate(deep.reviewed_at)}>reviewed {timeAgo(deep.reviewed_at)}</span>}
          {deep.zotero_note_error && <Disclosure summary="Zotero note not written"><span className="text-amber-800">{deep.zotero_note_error}</span></Disclosure>}
        </div>
      )}
      </div>
    </div>
  );
}

// 1-3 col grid of goal tiles — the single home of per-goal relevance. Tiles are
// peers in a grid (the one sanctioned use of light framing), keyed by state with
// a left accent; a fired tile carries its grounded summary, sections to read,
// and the supporting quote on demand.
const TILE_STATE = {
  hit: 'border-l-emerald-500',
  miss: 'border-l-slate-300 opacity-80',
  not_retrieved: 'border-l-amber-400 opacity-75',
};

// A neutral, clickable location pointer for a located finding: "§ Methods · p.4"
// jumps to that section in the Paper map (one-code-one-meaning — the location is
// slate, the problem/match keeps its own tone). Only rendered on the story page,
// where the overlay supplies the section + the SectionMap renders the target.
function SectionAnchor({ section }) {
  if (!section) return null;
  // `approx` = the section-level fallback (no exact/fuzzy span grounded it). Render
  // it conservatively (≈ prefix, muted) so an uncertain location never reads as an
  // asserted one — the brittle-localization warning from the research.
  const approx = section.match === 'approx';
  const pg = section.page ? ` · p.${section.page}` : '';
  const label = `${approx ? '≈ ' : ''}§ ${section.title}${pg}`;
  return (
    <a
      href={`#secmap-${section.id}`}
      title={`${approx ? 'approximate location — ' : ''}${section.title}${pg} — jump to the paper map`}
      className={`inline-flex max-w-[12rem] items-center rounded px-1.5 py-0.5 align-middle text-[10px] font-medium sm:max-w-[18rem] ${
        approx
          ? 'bg-slate-50 text-slate-400 hover:bg-slate-100'
          : 'bg-slate-100 text-slate-500 hover:bg-slate-200 hover:text-slate-700'
      }`}
    >
      <span className="truncate">{label}</span>
    </a>
  );
}

function GoalTile({ g, sections, foldReason }) {
  const state = String(g?.retrieval_state || 'not_retrieved');
  const supported = isSupportedGoal(g);
  const score = Number(g?.score) || 0;
  const width = Math.round(Math.max(0, Math.min(1, score / 3)) * 100);
  const secs = (g?.key_sections || []).filter(Boolean).join(', ');
  const quotes = (g?.supporting_quotes || []).map((q) => String(q || '').trim()).filter(Boolean);
  let why;
  if (state === 'hit') why = g?.abstained || !String(g?.summary || '').trim() ? 'grounded summary withheld'
    : supported ? 'Relevant to this goal' : 'Evidence did not support this goal';
  else if (state === 'miss') why = 'not addressed in this paper';
  else why = 'retrieval degraded — not assessed';
  const tone = state === 'hit' && !supported ? TILE_STATE.miss : TILE_STATE[state] || TILE_STATE.not_retrieved;
  return (
    <div className={`border-l-2 py-0.5 pl-3 ${tone}`}>
      <div className="text-[12px] font-medium text-slate-800 leading-snug">{g?.goal}</div>
      <div className="mt-0.5 text-[11px] text-slate-400">{state === 'hit' && g?.abstained ? '○ abstained' : state === 'hit' && !supported ? '○ not supported' : STATE_LABEL[state] || state}</div>
      <div className="my-1.5 h-1 rounded-full bg-slate-200/80 overflow-hidden" role="meter"
        aria-label={`${shortGoal(g?.goal)} relevance`} aria-valuemin={0} aria-valuemax={3}
        aria-valuenow={Math.max(0, Math.min(3, score))}>
        <span className="block h-full bg-teal-500" style={{ width: `${width}%` }} />
      </div>
      {!foldReason && <div className="text-[12px] leading-relaxed text-slate-600">{why}</div>}
      {state === 'hit' && (sections?.length ? (
        <div className="mt-1.5 flex flex-wrap items-center gap-1">
          <span className="text-[11px] text-slate-400">In</span>
          {sections.map((s) => <SectionAnchor key={s.id} section={s} />)}
        </div>
      ) : secs ? (
        <div className="mt-1.5 text-[11px] text-slate-400">Read for you: {secs}</div>
      ) : null)}
      {(foldReason || (state === 'hit' && quotes.length > 0)) && (
        <details className="mt-1 group">
          <summary className="cursor-pointer list-none [&::-webkit-details-marker]:hidden rounded text-[11px] font-medium text-teal-600 hover:text-teal-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-400 focus-visible:ring-offset-1">
            evidence
          </summary>
          {foldReason && <div className="text-[12px] leading-relaxed text-slate-600">{why}</div>}
          {quotes.map((quote, i) => <blockquote key={`${i}-${quote}`}
            className="mt-1 border-l-2 border-teal-300 pl-2 text-[11px] italic text-slate-500 leading-relaxed">
            “{quote}”
          </blockquote>)}
        </details>
      )}
    </div>
  );
}

function uniqueGoalFindings(goals) {
  const findings = new Map();
  for (const goal of goals) {
    if (goal?.retrieval_state !== 'hit') continue;
    for (const sentence of String(goal.summary || '').trim().split(/(?<=[.!?])\s+/)) {
      const text = sentence.trim();
      let key = text.toLocaleLowerCase().replace(/\s+/g, ' ').trim();
      const symbols = text.match(/\b[A-Za-z]{1,5}(?:\d+|\s*=\s*\d[\d.,]*)/g) || [];
      if (text !== text.toLocaleUpperCase()) symbols.push(...(text.match(/\b[A-Z]{2,}\b/g) || []));
      if (symbols.length) key += `|${symbols.map((s) => s.replace(/\s+/g, '')).join('|')}`;
      if (!key) continue;
      if (!findings.has(key)) findings.set(key, { text, goals: [] });
      const labels = findings.get(key).goals;
      if (goal.goal && !labels.includes(goal.goal)) labels.push(goal.goal);
    }
  }
  return [...findings.values()];
}

function GoalFindings({ goals }) {
  const findings = uniqueGoalFindings(goals);
  if (!findings.length) return null;
  const items = (selected) => {
    const groups = new Map();
    for (const finding of selected) {
      const key = JSON.stringify(finding.goals);
      if (!groups.has(key)) groups.set(key, { goals: finding.goals, findings: [] });
      groups.get(key).findings.push(finding);
    }
    return [...groups.entries()].map(([key, group]) => <div key={key} className="mb-2 break-words">
      <strong className="font-medium text-slate-700">For: {group.goals.join('; ')}</strong>
      <ul className="list-disc pl-5">{group.findings.map(finding => {
        const words = finding.text.split(/\s+/);
        return <li key={finding.text} className="mb-2">
          <p className="text-slate-600">{words.slice(0, 30).join(' ')}{words.length > 30 ? '…' : ''}</p>
          {words.length > 30 && <details><summary className="cursor-pointer text-teal-700 focus-visible:ring-2">Full finding</summary>
            <p>{finding.text}</p></details>}
        </li>;
      })}</ul>
    </div>);
  };
  return <div className="text-xs">{items(findings.slice(0, 3))}
    {findings.length > 3 && <details><summary className="cursor-pointer text-teal-700 focus-visible:ring-2">
      {findings.length - 3} more findings</summary>{items(findings.slice(3))}</details>}
  </div>;
}

// Show located evidence first, including evidence against relevance. Retrieval
// misses and degraded goals stay behind one disclosure; if none are located,
// show them all so the section isn't empty.
function GoalBoard({ goals, goalLoc, foldReasons = false }) {
  const isHit = (g) => String(g?.retrieval_state || 'not_retrieved') === 'hit';
  const addressed = goals.filter(isHit);
  const rest = goals.filter((g) => !isHit(g));
  const findingTexts = new Set(uniqueGoalFindings(addressed).map(f => f.text));
  const originals = [...new Set(goals.map(g => String(g.summary || '').trim()))]
    .filter(text => text && !findingTexts.has(text));
  const grid = (items) => (
    <div className="goal-board-grid grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-x-5 gap-y-4">
      {items.map((g, i) => <GoalTile key={i} g={g} sections={goalLoc?.get(g?.goal)} foldReason={foldReasons} />)}
    </div>
  );
  return (
    <div className="space-y-2">
      {grid(addressed.length ? addressed : goals)}
      <Disclosure summary="Goal findings"><GoalFindings goals={addressed} /></Disclosure>
      {originals.length > 0 && <Disclosure summary="Original goal summaries">
        <dl>{originals.map((text, i) => <KeyVal key={i}
          label={goals.filter(g => String(g.summary || '').trim() === text).map(g => g.goal).join('; ')}>{text}</KeyVal>)}</dl>
      </Disclosure>}
      {addressed.length > 0 && rest.length > 0 && (
        <details className="group">
          <summary className="cursor-pointer list-none [&::-webkit-details-marker]:hidden text-[11px] font-medium text-slate-400 hover:text-slate-600">
            {rest.length} other goal{rest.length > 1 ? 's' : ''} ▾
          </summary>
          <div className="mt-2 opacity-75">{grid(rest)}</div>
        </details>
      )}
    </div>
  );
}

// The DECISION half of the quality read, shown by default: the band gloss + the
// loud red-flags callout (the only semantic box). Everything that explains HOW the
// band was reached moves to QualityDetails, behind the one "Details" disclosure.
function QualityHeadline({ quality, band, flagLoc, missLoc, critiqueViews = false }) {
  const redFlags = dedupeFlags((quality.red_flags || []).map((x) => String(x || '').trim()).filter(Boolean));
  // Derived so the gloss never says "No red flags" while the box below lists some.
  const gloss = bandGloss(band, redFlags.length > 0);
  const standard = String(quality.coverage_standard || '');
  const met = Number(quality.coverage_met) || 0;
  const applicable = Number(quality.coverage_applicable) || 0;
  const missing = (quality.missing_critical || []).map((x) => String(x || '').trim()).filter(Boolean);
  const tentative = critiqueIsTentative(quality);

  return (
    <div className="space-y-2.5">
      {gloss && (
        <p className="text-[13px] leading-relaxed text-slate-700 max-w-[66ch]">
          <span className="font-medium text-slate-900">{gloss.lead}</span> {gloss.body}
        </p>
      )}
      {/* Coverage panel — the honest replacement for the unvalidated 1-5 scores: which
          recognized standard was applied to THIS paper type, how many applicable items
          it met, and which critical ones are missing (each is a grounded checklist item
          in the full checklist below). */}
      {(applicable > 0 || missing.length > 0) && (
        <div>
          <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-[13px]">
            {/* The reading page names the checklist and paper type once, beside the full checklist. */}
            {!critiqueViews && standard && <span className="font-medium text-slate-900">{standardLabel(standard)}</span>}
            <span className="text-slate-700">{applicable > 0 ? `${met}/${applicable} applicable items met` : 'Checklist coverage unavailable'}</span>
            {!critiqueViews && <PaperTypeChip quality={quality} />}
          </div>
          {missing.length > 0 && (
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
          )}
        </div>
      )}
      {redFlags.length > 0 && (
        <div className="border-l-2 border-rose-300 pl-4">
          <div className="mb-2 flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="text-[11px] font-medium uppercase tracking-[0.08em] text-rose-700">Red flags</span>
            <span className="text-[11px] text-rose-700" title="Model-generated critique — verify against the paper">· AI-generated, verify</span>
            {tentative && (
              <span className="text-[11px] text-amber-700" title="Self-consistency runs disagreed or confidence was low — treat as a prompt to check, not a finding">low confidence</span>
            )}
          </div>
          {critiqueViews ? <CritiqueViews flags={redFlags} locations={flagLoc} rubric={quality.rubric} /> : <ul className="space-y-2.5 text-[13px] leading-relaxed text-slate-800">
            {redFlags.map((x, i) => {
              const loc = flagLoc?.get(String(x).trim());
              return (
                <li key={i}>
                  {x}
                  {loc && <span className="ml-1.5 align-middle"><SectionAnchor section={loc} /></span>}
                </li>
              );
            })}
          </ul>}
        </div>
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
function ChecklistBasis({ quality }) {
  const standard = String(quality.coverage_standard || '');
  if (!standard && !quality.paper_type) return null;
  return (
    <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-[13px]">
      {standard && <span className="font-medium text-slate-900">{standardLabel(standard)}</span>}
      <PaperTypeChip quality={quality} />
    </div>
  );
}

function sharedPrefix(left, right) {
  const first = left.split(/\s+/);
  const second = right.split(/\s+/);
  let size = 0;
  while (size < first.length && first[size] === second[size]) size++;
  return size > 1 ? first.slice(0, size).join(' ') : '';
}

function sharedWordFrame(prefix, texts, rubric) {
  const intro = prefix.split(/\s+/);
  for (const term of Object.keys(rubric || {})) {
    const rows = texts.map(text => {
      const words = text.split(/\s+/);
      const position = words.indexOf(term, intro.length);
      if (position < 0 || words.indexOf(term, position + 1) >= 0) return null;
      return { modifier: words.slice(intro.length, position).join(' '),
        suffix: words.slice(position + 1).join(' ') };
    });
    if (rows.every(Boolean)) return { term, rows };
  }
  return null;
}

// A shared opening is a heading only when each remainder reads on its own; "The judge model used"
// + "for direct-framing…" is a sentence cut in half, so such a group is shown in full instead.
const DANGLING_START = /^(for|of|to|in|on|at|by|as|with|from|into|and|or|but|that|which|than|the|a|an)$/i;
const readsOnItsOwn = (prefix, text) => !DANGLING_START.test(text.slice(prefix.length).trim().split(/\s+/)[0] || '');

function CritiqueViews({ flags, locations, rubric }) {
  const groups = [];
  for (const text of flags) {
    const previous = groups.at(-1);
    const prefix = previous && sharedPrefix(previous.prefix, text);
    if (prefix && text !== prefix && !previous.texts.includes(prefix)) {
      previous.prefix = prefix;
      previous.texts.push(text);
    } else groups.push({ prefix: text, texts: [text] });
  }
  for (let i = groups.length - 1; i >= 0; i--) {
    const { prefix, texts } = groups[i];
    if (texts.length > 1 && !texts.every(text => readsOnItsOwn(prefix, text))) {
      groups.splice(i, 1, ...texts.map(text => ({ prefix: text, texts: [text] })));
    }
  }
  const link = text => {
    const section = locations?.get(text);
    return section && <span className="ml-1.5 align-middle"><SectionAnchor section={section} /></span>;
  };
  return <ul className="space-y-3 text-[13px] leading-relaxed text-slate-800">
    {groups.map(({ prefix, texts }, index) => {
      const frame = texts.length > 1 && sharedWordFrame(prefix, texts, rubric);
      return <li key={index}>
      {prefix}{frame && ` … ${frame.term}`}{texts.length === 1 && link(texts[0])}
      {texts.length > 1 && <>
        <div className="mt-1 space-y-2 pl-2 border-l border-rose-200">
          {frame && <p className="text-[12px] font-medium text-slate-500">Scope / context · original wording</p>}
          {texts.map((text, i) => <p key={i}>
            {frame ? <><span data-frame-modifier>{frame.rows[i].modifier}</span>{frame.rows[i].modifier && ' '}
              <span aria-label={`shared term: ${frame.term}`}>…</span>{' '}<span data-frame-suffix>{frame.rows[i].suffix}</span></>
              : text.split(/\s+/).slice(prefix.split(/\s+/).length).join(' ')}{link(text)}</p>)}
        </div>
        <Disclosure summary="Original wording"><ul className="list-disc pl-5">
          {texts.map((text, i) => <li key={i}>{text}</li>)}
        </ul></Disclosure>
      </>}
    </li>; })}
  </ul>;
}

// The EXPLANATION half, inside the "Details" disclosure: how the grade was reached
// (method clause), the decisive signals, overstated claims, the full rubric, and
// the legend. Flattened — no nested disclosure (we are already inside one).
function MaterialClaims({ quality }) {
  const items = (quality.overstatements || []).filter(Boolean);
  if (!items.length) return null;
  return <div className="mt-2">
    <SectionLabel level={3}>Possible overstatement (low reliability)</SectionLabel>
    <Bullets items={items} />
  </div>;
}

function QualityDetails({ quality, band, hideMaterial = false }) {
  const overs = (quality.overstatements || []).map((x) => String(x || '').trim()).filter(Boolean);
  const agreed = Number(quality.passes_agreed) || 0;
  const total = Number(quality.passes_total) || 0;
  const { heading, rows, caption } = decisiveRows(quality.rubric || {}, band);
  const checklist = fullChecklist(quality.rubric || {}, quality.evidence || {});

  return (
    <div className="space-y-2.5">
      <p className="text-[11px] leading-relaxed text-slate-400 max-w-[66ch]">
        {METHOD_CLAUSE}{total > 1 ? ` · ${agreed}/${total} passes agree` : total === 1 ? ' · 1 pass (no agreement check)' : ''}
      </p>

      {rows.length > 0 && (
        <div>
          <div className="text-[11px] uppercase tracking-[0.06em] font-medium text-slate-400 mb-1">{heading}</div>
          <ul className="space-y-0.5 text-[13px] leading-relaxed">
            {rows.map((r, i) => (
              <li key={i} className="flex items-baseline gap-1.5">
                <span className={`font-medium ${r.ok ? 'text-emerald-600' : 'text-rose-600'}`}>{r.ok ? '✓' : '✗'}</span>
                <span className="text-slate-700">{r.label}</span>
              </li>
            ))}
          </ul>
          {caption && <div className="mt-1 text-[11px] text-slate-400">{caption}</div>}
        </div>
      )}

      {!hideMaterial && overs.length > 0 && (
        <div>
          <div className="mb-1 text-[11px] uppercase tracking-[0.06em] font-medium text-amber-600">
            Possible overstatement (low reliability)
          </div>
          <ul className="list-disc pl-5 text-[13px] leading-relaxed text-slate-700 space-y-0.5">
            {overs.map((x, i) => <li key={i}>{x}</li>)}
          </ul>
        </div>
      )}

      {checklist.length > 0 && (
        <div>
          <div className="text-[11px] uppercase tracking-[0.06em] font-medium text-slate-400 mb-1">
            Full {checklist.length}-point checklist
          </div>
          <div className="space-y-1">
            {checklist.map((r) => (
              <div key={r.key} className="flex items-baseline gap-2 text-[12px] leading-relaxed">
                <RubricMark value={r.value} />
                <span className="text-slate-700">
                  {r.label}
                  {r.quote && <span className="block text-[11px] italic text-slate-400 mt-0.5">“{r.quote}”</span>}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="pt-2 border-t border-slate-200/60 text-[11px] leading-relaxed text-slate-400 max-w-[66ch]">
        {LEGEND.map((l, i) => (
          <span key={l.term}>
            {i > 0 && ' · '}
            <b className="font-medium text-slate-500">{l.term}</b> {l.gloss}
          </span>
        ))}
      </div>
    </div>
  );
}

const RB_TONE = {
  yes: 'bg-emerald-100 text-emerald-700',
  no: 'bg-rose-100 text-rose-700',
  na: 'bg-slate-100 text-slate-400',
};
function RubricMark({ value }) {
  return (
    <span className={`shrink-0 inline-block min-w-[2.2rem] text-center rounded text-[10px] font-medium uppercase px-1 py-0.5 ${RB_TONE[value] || RB_TONE.na}`}>
      {value}
    </span>
  );
}

// A field whose text could not be grounded is stored empty (never shown as
// verified); one quiet line + the existing Regenerate action stands in its place.
function Ungrounded({ onRegenerate }) {
  return <>
    <span className="text-slate-500">Could not be grounded this time</span>
    {onRegenerate && <>
      {' · '}
      <button type="button" onClick={onRegenerate} className="font-medium text-forest-800 underline underline-offset-2 hover:text-forest-700">Regenerate</button>
    </>}
  </>;
}

function DigestRow({ field, lost, onRegenerate, children, ...rest }) {
  if (!lost.includes(field)) return <KeyVal {...rest}>{children}</KeyVal>;
  return <KeyVal {...rest} tone="default"><Ungrounded onRegenerate={onRegenerate} /></KeyVal>;
}

// Shown in the glance card / the impact grid, so the digest rows below leave them out.
const GLANCE_FIELDS = ['key_strength', 'key_weakness'];
const IMPACT_FIELDS = ['impact', 'industry_impact', 'academy_impact', 'unknown_unknowns'];

const LOST_ONLY = { relevance: 'Relevance', skip_parts: 'Skip', writing_reasons: 'Writing', read_parts: 'Read parts', verdict: 'Verdict' };

// The structured digest, behind the "Full digest" disclosure. Rendered ONCE
// (the old DigestBlock + iframe both showed it). Reading-scale KeyVal rows.
function DigestRows({ digest, exclude = [], lost = [], onRegenerate = null }) {
  const d = Object.fromEntries(Object.entries(digest).filter(([key]) => !exclude.includes(key)));
  const p = d.parameters || {};
  const r = { lost: lost.filter((field) => !exclude.includes(field)), onRegenerate };
  return (
    <dl className="space-y-2">
      <DigestRow {...r} field="executive_summary" label="Summary">{d.executive_summary}</DigestRow>
      {(d.key_findings || []).filter(Boolean).length > 0 && (
        <KeyVal label="Key findings"><Bullets items={d.key_findings} /></KeyVal>
      )}
      <DigestRow {...r} field="methods" label="Methods">{d.methods}</DigestRow>
      <DigestRow {...r} field="parameters" label="Dataset / sample">{[p.dataset, p.sample_size].filter(Boolean).join(' · ')}</DigestRow>
      <KeyVal label="Architecture">{p.architecture}</KeyVal>
      {(p.baselines || []).filter(Boolean).length > 0 && <KeyVal label="Baselines"><Bullets items={p.baselines} /></KeyVal>}
      {(p.metrics || []).filter(Boolean).length > 0 && <KeyVal label="Metrics"><Bullets items={p.metrics} /></KeyVal>}
      {p.external_validation != null && <KeyVal label="External validation">{p.external_validation ? 'Yes' : 'No'}</KeyVal>}
      <DigestRow {...r} field="limitations" label="Limitations">{d.limitations}</DigestRow>
      <DigestRow {...r} field="controversies" label="Controversies">{d.controversies}</DigestRow>
      <DigestRow {...r} field="impact" label="Impact">{d.impact}</DigestRow>
      <DigestRow {...r} field="industry_impact" label="Industry">{d.industry_impact}</DigestRow>
      <DigestRow {...r} field="academy_impact" label="Academia">{d.academy_impact}</DigestRow>
      <DigestRow {...r} field="unknown_unknowns" label="Unknowns">{d.unknown_unknowns}</DigestRow>
      {(d.implementation || []).filter(Boolean).length > 0 && (
        <KeyVal label="Implementation"><Bullets items={d.implementation} /></KeyVal>
      )}
      <DigestRow {...r} field="implementation" label="Implementation" />
      <DigestRow {...r} field="key_strength" label="Strength" tone="pos">{d.key_strength}</DigestRow>
      <DigestRow {...r} field="key_weakness" label="Weakness" tone="neg">{d.key_weakness}</DigestRow>
      {Object.entries(LOST_ONLY).map(([field, label]) => <DigestRow key={field} {...r} field={field} label={label} />)}
      {/* The reviewer-LLM's self-reported 1-5 scores were removed: unvalidated
          opinion (the grade/band come from grounded checklist coverage, not these). */}
    </dl>
  );
}
