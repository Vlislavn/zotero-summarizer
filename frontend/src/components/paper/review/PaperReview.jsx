import { Section, SectionLabel, Disclosure, Chip, KeyVal, Bullets } from './primitives.jsx';
import {
  gradeTone, bandTone, BAND_LABEL, VERDICT_ACCENT,
} from './tones.js';
import {
  bandGloss, METHOD_CLAUSE, LEGEND, rubricLabel, paperTypeLabel,
  summarizeGoals, readVerdict, decisiveRows, fullChecklist, shortGoal,
} from './briefModel.js';
import { formatShortDate } from '../../library/shared.jsx';

const STATE_LABEL = {
  hit: '● addressed', miss: '○ not addressed', not_retrieved: '⚠ not retrieved',
};

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
export default function PaperReview({ deep, compact = false, flat = false, sectionOverlay = null }) {
  if (!deep) return null;
  const digest = deep.digest || null;
  const quality = deep.quality || null;
  const goals = deep.goal_summaries || [];

  // Legacy cache (pre-digest): show the old grade, nudge a re-run.
  if (!digest && quality && quality.grade && !goals.length) {
    return (
      <div className="text-[13px] text-slate-600">
        <Chip tone={gradeTone(quality.grade)}>Quality {quality.grade}</Chip>
        {quality.verdict ? <span className="ml-2">{quality.verdict}</span> : null}
        <div className="mt-1 text-[11px] text-slate-400">Older review — re-run for the new digest.</div>
      </div>
    );
  }
  if (!digest && !quality && !goals.length) return null;

  const band = String(quality?.quality_band || '');
  const redFlags = (quality?.red_flags || []).map((x) => String(x || '').trim()).filter(Boolean);
  const { nFired } = summarizeGoals(goals);
  const nHitGoals = goals.filter((g) => String(g?.retrieval_state || '') === 'hit').length;
  const hasBrief = Boolean(quality || goals.length);

  // Lead verdict: the synthesized goals×rigor call when we have those layers;
  // otherwise fall back to the digest's own read decision.
  let verdict;
  if (hasBrief) {
    verdict = readVerdict({ nFired, band, redFlags });
  } else if (digest?.read_decision) {
    const d = String(digest.read_decision).toLowerCase();
    verdict = {
      key: d === 'read' ? 'deep' : d === 'skim' ? 'skim' : 'skip',
      label: d.toUpperCase(),
      reason: digest.verdict || '',
    };
  } else {
    verdict = { key: 'skip', label: 'REVIEW', reason: digest?.verdict || '' };
  }
  const grade = digest?.grade || quality?.grade || '';
  const tldr = digest?.tldr || '';

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
      {compact && quality && <QualityHeadline quality={quality} band={band} />}
      {tldr && (
        <p className="text-[14px] leading-relaxed text-slate-800 max-w-[66ch]">{tldr}</p>
      )}
      {quality && <QualityDetails quality={quality} band={band} />}
      {/* Decision-relevant findings stay visible; the long reference digest folds on
          the full page (Scim: keep the salient signal up top, collapse the rest —
          one click, not a wall). The compact card keeps it inline (it's already one
          disclosure deep, so a second fold would cost two clicks). */}
      {flat && digest && (digest.key_findings || []).filter(Boolean).length > 0 && (
        <div>
          <SectionLabel>Key findings</SectionLabel>
          <div className="mt-1.5"><Bullets items={digest.key_findings} /></div>
        </div>
      )}
      {digest && (flat ? (
        <Disclosure summary="Full digest — methods, limitations, impact…">
          <div className="mt-1.5"><DigestRows digest={digest} /></div>
        </Disclosure>
      ) : (
        <div>
          <SectionLabel>Full digest</SectionLabel>
          <div className="mt-1.5"><DigestRows digest={digest} /></div>
        </div>
      ))}
      {compact && (deep.reviewed_at || deep.zotero_note_written) && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-slate-400">
          {deep.reviewed_at && <span>reviewed {formatShortDate(deep.reviewed_at)}</span>}
          {deep.zotero_note_written && <span className="text-emerald-600">saved to Zotero ✓</span>}
        </div>
      )}
    </div>
  );

  return (
    <div className="review-prose text-slate-800">
      {/* Verdict banner — the single loud element (Von Restorff). In the compact
          Library card every signal is a chip on this ONE row (grade + band +
          red-flag count), each hide-when-empty, so the glance is pre-attentive
          (colour carries the verdict) and the gloss/coverage/flag-text all move
          into Details. Full surfaces keep the labelled "Quality {grade}" chip. */}
      <div className={`rounded-lg border-l-[3px] px-3.5 py-3 ${VERDICT_ACCENT[verdict.key] || VERDICT_ACCENT.skip}`}>
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-display text-[22px] font-light tracking-tight text-slate-900">{verdict.label}</span>
          {grade && (
            <Chip tone={gradeTone(grade)} title="Reference-free full-text quality grade">
              {compact ? grade : `Quality ${grade}`}
            </Chip>
          )}
          {compact && band && <Chip tone={bandTone(band)}>{BAND_LABEL[band] || '—'}</Chip>}
          {compact && redFlags.length > 0 && (
            <Chip tone="rose" title={redFlags.slice(0, 3).join('; ')}>⚠ {redFlags.length}</Chip>
          )}
        </div>
        {verdict.reason && (
          <p className="mt-1.5 text-[13px] leading-relaxed text-slate-700 max-w-[66ch]">{verdict.reason}</p>
        )}
      </div>

      {/* (The Rigor·Relevance summary spine was removed — it restated the two
          sections immediately below it, "Relevance to your goals" + "Quality —
          {band}", which carry the same numbers with their own labels.) */}
      <div className="mt-2 divide-y divide-slate-200/60">
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
        {quality && !compact && (
          <Section label={`Quality — ${BAND_LABEL[band] || '—'}`}>
            <QualityHeadline quality={quality} band={band} flagLoc={flagLoc} missLoc={missLoc} />
          </Section>
        )}

        {/* Decision-only by default (Cognitive Load / Miller): everything below the
            call — the TLDR, how the grade was reached, the full rubric, the
            structured digest — lives behind ONE disclosure. Quiet by default, deep
            on demand. In compact, the full quality headline lives here too. */}
        {(tldr || quality || digest) && (
          <Section label={flat ? 'Details' : undefined}>
            {flat ? detailsInner : <Disclosure summary="Details">{detailsInner}</Disclosure>}
          </Section>
        )}
      </div>

      {!compact && (deep.reviewed_at || deep.zotero_note_written || deep.zotero_note_error) && (
        <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-slate-400">
          {deep.reviewed_at && <span>reviewed {formatShortDate(deep.reviewed_at)}</span>}
          {deep.zotero_note_written && <span className="text-emerald-600">saved to Zotero ✓</span>}
          {deep.zotero_note_error && <span className="text-amber-600">note not written: {deep.zotero_note_error}</span>}
        </div>
      )}
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
      className={`inline-flex max-w-[12rem] items-center rounded px-1.5 py-0.5 align-middle text-[10px] font-semibold sm:max-w-[18rem] ${
        approx
          ? 'bg-slate-50 text-slate-400 hover:bg-slate-100'
          : 'bg-slate-100 text-slate-500 hover:bg-slate-200 hover:text-slate-700'
      }`}
    >
      <span className="truncate">{label}</span>
    </a>
  );
}

function GoalTile({ g, sections }) {
  const state = String(g?.retrieval_state || 'not_retrieved');
  const score = Number(g?.score) || 0;
  const width = Math.round(Math.max(0, Math.min(1, score / 3)) * 100);
  const secs = (g?.key_sections || []).filter(Boolean).join(', ');
  const quote = (g?.supporting_quotes || []).map((q) => String(q || '').trim()).find(Boolean);
  let why;
  if (state === 'hit') why = String(g?.summary || '').trim() || 'relevant — grounded summary withheld';
  else if (state === 'miss') why = 'not addressed in this paper';
  else why = 'retrieval degraded — not assessed';
  return (
    <div className={`rounded-md border border-slate-200/70 border-l-[3px] bg-white/50 p-2.5 ${TILE_STATE[state] || TILE_STATE.not_retrieved}`}>
      <div className="text-[12px] font-semibold text-slate-800 leading-snug">{shortGoal(g?.goal)}</div>
      <div className="mt-0.5 text-[11px] text-slate-400">{STATE_LABEL[state] || state}</div>
      <div className="my-1.5 h-1 rounded-full bg-slate-200/80 overflow-hidden">
        <span className="block h-full bg-teal-500" style={{ width: `${width}%` }} />
      </div>
      <div className="text-[12px] leading-relaxed text-slate-600">{why}</div>
      {state === 'hit' && (sections?.length ? (
        <div className="mt-1.5 flex flex-wrap items-center gap-1">
          <span className="text-[11px] text-slate-400">In</span>
          {sections.map((s) => <SectionAnchor key={s.id} section={s} />)}
        </div>
      ) : secs ? (
        <div className="mt-1.5 text-[11px] text-slate-400">Read for you: {secs}</div>
      ) : null)}
      {state === 'hit' && quote && (
        <details className="mt-1 group">
          <summary className="cursor-pointer list-none [&::-webkit-details-marker]:hidden rounded text-[11px] font-semibold text-teal-600 hover:text-teal-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-400 focus-visible:ring-offset-1">
            evidence
          </summary>
          <blockquote className="mt-1 border-l-2 border-teal-300 pl-2 text-[11px] italic text-slate-500 leading-relaxed">
            “{quote}”
          </blockquote>
        </details>
      )}
    </div>
  );
}

// Show only the goals this paper ADDRESSES (the tiles carrying a grounded summary
// worth reading). The not-addressed / not-retrieved goals are noise on the glance
// surface — fold them behind a quiet line (visual minimalism: one screen, the
// signal up front). If nothing is addressed, show all so the section isn't empty.
function GoalBoard({ goals, goalLoc }) {
  const isHit = (g) => String(g?.retrieval_state || 'not_retrieved') === 'hit';
  const addressed = goals.filter(isHit);
  const rest = goals.filter((g) => !isHit(g));
  const grid = (items) => (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2.5">
      {items.map((g, i) => <GoalTile key={i} g={g} sections={goalLoc?.get(g?.goal)} />)}
    </div>
  );
  if (!addressed.length) return grid(goals);
  return (
    <div className="space-y-2">
      {grid(addressed)}
      {rest.length > 0 && (
        <details className="group">
          <summary className="cursor-pointer list-none [&::-webkit-details-marker]:hidden text-[11px] font-semibold text-slate-400 hover:text-slate-600">
            {rest.length} other goal{rest.length > 1 ? 's' : ''} not addressed ▾
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
function QualityHeadline({ quality, band, flagLoc, missLoc }) {
  const redFlags = (quality.red_flags || []).map((x) => String(x || '').trim()).filter(Boolean);
  // Derived so the gloss never says "No red flags" while the box below lists some.
  const gloss = bandGloss(band, redFlags.length > 0);
  const standard = String(quality.coverage_standard || '');
  const met = Number(quality.coverage_met) || 0;
  const applicable = Number(quality.coverage_applicable) || 0;
  const missing = (quality.missing_critical || []).map((x) => String(x || '').trim()).filter(Boolean);
  const ptype = String(quality.paper_type || '');
  const uncertainType = ptype.startsWith('generic_');
  const tentative = critiqueIsTentative(quality);

  return (
    <div className="space-y-2.5">
      {gloss && (
        <p className="text-[13px] leading-relaxed text-slate-700 max-w-[66ch]">
          <span className="font-semibold text-slate-900">{gloss.lead}</span> {gloss.body}
        </p>
      )}
      {/* Coverage panel — the honest replacement for the unvalidated 1-5 scores: which
          recognized standard was applied to THIS paper type, how many applicable items
          it met, and which critical ones are missing (each is a grounded checklist item
          in the full checklist below). */}
      {applicable > 0 && (
        <div>
          <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-[13px]">
            {standard && <span className="font-semibold text-slate-900">{standard}</span>}
            <span className="text-slate-700">{met}/{applicable} applicable items met</span>
            {ptype && (
              <Chip
                tone={uncertainType ? 'amber' : 'slate'}
                title={uncertainType
                  ? 'Paper type was uncertain — judged against a safe generic checklist. Open the paper to verify.'
                  : 'Detected paper type — the checklist is chosen for this type'}
              >
                {paperTypeLabel(ptype)}{uncertainType ? ' ⚠' : ''}
              </Chip>
            )}
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
        <div className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2">
          <div className="mb-1 flex flex-wrap items-baseline gap-x-2">
            <span className="text-[11px] uppercase tracking-[0.06em] font-semibold text-rose-600">⚠ Red flags</span>
            <span className="text-[10px] text-rose-400" title="Model-generated critique — verify against the paper">· model judgment</span>
            {tentative && (
              <span className="text-[10px] font-semibold text-amber-600" title="Self-consistency runs disagreed or confidence was low — treat as a prompt to check, not a finding">· low confidence, verify</span>
            )}
          </div>
          <ul className="list-disc pl-5 text-[13px] leading-relaxed text-rose-800 space-y-0.5">
            {redFlags.slice(0, 3).map((x, i) => {
              const loc = flagLoc?.get(String(x).trim());
              return (
                <li key={i}>
                  {x}
                  {loc && <span className="ml-1.5 align-middle"><SectionAnchor section={loc} /></span>}
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}

// The EXPLANATION half, inside the "Details" disclosure: how the grade was reached
// (method clause), the decisive signals, overstated claims, the full rubric, and
// the legend. Flattened — no nested disclosure (we are already inside one).
function QualityDetails({ quality, band }) {
  const overs = (quality.overstatements || []).map((x) => String(x || '').trim()).filter(Boolean);
  const agreed = Number(quality.passes_agreed) || 0;
  const total = Number(quality.passes_total) || 0;
  const { heading, rows, caption } = decisiveRows(quality.rubric || {}, band);
  const checklist = fullChecklist(quality.rubric || {}, quality.evidence || {});

  return (
    <div className="space-y-2.5">
      <p className="text-[11px] leading-relaxed text-slate-400 max-w-[66ch]">
        {METHOD_CLAUSE}{total ? ` · ${agreed}/${total} passes agree` : ''}
      </p>

      {rows.length > 0 && (
        <div>
          <div className="text-[11px] uppercase tracking-[0.06em] font-semibold text-slate-400 mb-1">{heading}</div>
          <ul className="space-y-0.5 text-[13px] leading-relaxed">
            {rows.map((r, i) => (
              <li key={i} className="flex items-baseline gap-1.5">
                <span className={`font-bold ${r.ok ? 'text-emerald-600' : 'text-rose-600'}`}>{r.ok ? '✓' : '✗'}</span>
                <span className="text-slate-700">{r.label}</span>
              </li>
            ))}
          </ul>
          {caption && <div className="mt-1 text-[11px] text-slate-400">{caption}</div>}
        </div>
      )}

      {overs.length > 0 && (
        <div>
          <div className="mb-1 text-[11px] uppercase tracking-[0.06em] font-semibold text-amber-600">
            Overstated claims <span className="font-normal normal-case tracking-normal text-amber-500">· model judgment</span>
          </div>
          <ul className="list-disc pl-5 text-[13px] leading-relaxed text-slate-700 space-y-0.5">
            {overs.slice(0, 3).map((x, i) => <li key={i}>{x}</li>)}
          </ul>
        </div>
      )}

      {checklist.length > 0 && (
        <div>
          <div className="text-[11px] uppercase tracking-[0.06em] font-semibold text-slate-400 mb-1">
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
            <b className="text-slate-500">{l.term}</b> {l.gloss}
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
    <span className={`shrink-0 inline-block min-w-[2.2rem] text-center rounded text-[10px] font-bold uppercase px-1 py-0.5 ${RB_TONE[value] || RB_TONE.na}`}>
      {value}
    </span>
  );
}

// The structured digest, behind the "Full digest" disclosure. Rendered ONCE
// (the old DigestBlock + iframe both showed it). Reading-scale KeyVal rows.
function DigestRows({ digest: d }) {
  return (
    <dl className="space-y-2">
      <KeyVal label="Summary">{d.executive_summary}</KeyVal>
      {(d.key_findings || []).filter(Boolean).length > 0 && (
        <KeyVal label="Key findings"><Bullets items={d.key_findings} /></KeyVal>
      )}
      <KeyVal label="Why read">{d.read_why}</KeyVal>
      {(d.read_parts || []).filter(Boolean).length > 0 && (
        <KeyVal label="Read parts"><Bullets items={d.read_parts} /></KeyVal>
      )}
      <KeyVal label="Relevance">{d.relevance}</KeyVal>
      <KeyVal label="Methods">{d.methods}</KeyVal>
      <KeyVal label="Limitations">{d.limitations}</KeyVal>
      <KeyVal label="Controversies">{d.controversies}</KeyVal>
      <KeyVal label="Impact">{d.impact}</KeyVal>
      <KeyVal label="Industry">{d.industry_impact}</KeyVal>
      <KeyVal label="Academia">{d.academy_impact}</KeyVal>
      <KeyVal label="Unknowns">{d.unknown_unknowns}</KeyVal>
      {(d.implementation || []).filter(Boolean).length > 0 && (
        <KeyVal label="Implementation"><Bullets items={d.implementation} /></KeyVal>
      )}
      <KeyVal label="Strength" tone="pos">{d.key_strength}</KeyVal>
      <KeyVal label="Weakness" tone="neg">{d.key_weakness}</KeyVal>
      {/* The reviewer-LLM's self-reported 1-5 scores were removed: unvalidated
          opinion (the grade/band come from grounded checklist coverage, not these). */}
    </dl>
  );
}
