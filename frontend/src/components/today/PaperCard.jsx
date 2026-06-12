// Today slate card — Stage 1 of the two-stage reading flow: read the abstract,
// then tick the checkbox. Redesigned per the Laws of UX gate:
//   • Von Restorff   — a relevance BAND (color + word) drives the card's visual
//                      weight, so treasures pop and likely-trash is muted.
//   • Selective Attention — a plain-language "why it matters" row sits at the top
//                      (right under the band + title), where the eye lands.
//   • Mental Model / Cognitive Load — raw scores (composite 1–5, prestige 0–1)
//                      become anchored words + bars, not bare decimals.
//
// The relevance band is DISTINCT from the full-text Quality A–D grade
// (QualityBlock): different vocabulary (top pick / strong / fair / weak), and a
// full-card accent vs. a small inline letter chip.

import AuthorByline from '../AuthorByline.jsx';
import { scoreToBand, BAND_ACTIVE_CLS } from '../../utils/relevanceBands.js';

// Plain-language label + tooltip for why a card is in the slate (paper.role).
// The user should never have to decode internal allocation-role names.
const BUCKET_LABEL = {
  model: 'top match',
  model_fallback: 'top match',
  surprise: 'surprise',
  diversity: 'wildcard',
};
const ROLE_HINT = {
  model: 'Best match to your interests (model + corpus + author/venue prestige).',
  model_fallback: 'Best match to your interests (model + corpus + author/venue prestige).',
  surprise: 'A high-surprise pick outside your usual reading pattern.',
  diversity: 'Deliberately different from your library (low corpus affinity).',
};

// Relevance-band → card accent + tint (Von Restorff): treasures full-weight,
// trash desaturated. Keyed by the same band names as relevanceBands.scoreToBand.
const BAND_CARD_CLS = {
  must_read: 'border border-slate-200 border-l-4 border-l-emerald-500 bg-emerald-50/40',
  should_read: 'border border-slate-200 border-l-4 border-l-sky-500 bg-white',
  could_read: 'border border-slate-200 border-l-4 border-l-amber-400 bg-white',
  dont_read: 'border border-slate-200 border-l-2 border-l-rose-300 bg-slate-50 opacity-70',
};
// Bar fill per band — shares the palette of relevanceBands / ScoreHistogram.
const BAND_BAR_CLS = {
  must_read: 'bg-emerald-500',
  should_read: 'bg-sky-500',
  could_read: 'bg-amber-400',
  dont_read: 'bg-rose-400',
};
// Treasure→trash wording for THIS card. Deliberately NOT the must/should/could
// priority words (those are the Stage-2 action labels) nor the Quality A–D grade.
const RELEVANCE_WORD = {
  must_read: 'top pick',
  should_read: 'strong',
  could_read: 'fair',
  dont_read: 'weak',
};

const GRADE_CLS = {
  A: 'bg-emerald-100 text-emerald-800 border-emerald-300',
  B: 'bg-teal-100 text-teal-800 border-teal-300',
  C: 'bg-amber-100 text-amber-800 border-amber-300',
  D: 'bg-rose-100 text-rose-800 border-rose-300',
};

function parseAuthorsString(s) {
  if (!s || typeof s !== 'string') return [];
  return s
    .split(',')
    .map((name) => name.trim())
    .filter(Boolean)
    .map((name) => ({ name, h_index: null }));
}

function prestigeWord(p) {
  if (typeof p !== 'number') return 'unknown';
  if (p >= 0.66) return 'high';
  if (p >= 0.33) return 'typical';
  if (p > 0) return 'modest';
  return 'new';
}

function Badge({ label, value, tone = 'slate', title }) {
  const tones = {
    slate: 'bg-slate-100 text-slate-700 border-slate-200',
    teal: 'bg-teal-50 text-teal-800 border-teal-200',
    violet: 'bg-violet-50 text-violet-800 border-violet-200',
    amber: 'bg-amber-50 text-amber-800 border-amber-200',
    sky: 'bg-sky-50 text-sky-800 border-sky-200',
  };
  const cls = tones[tone] || tones.slate;
  return (
    <span
      title={title || label}
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full border text-[11px] ${cls}`}
    >
      <span className="uppercase tracking-wider font-semibold">{label}</span>
      {value !== '' && value != null && <span className="mono font-bold">{value}</span>}
    </span>
  );
}

// Anchored signal bar (label · fill · trailing word) — same visual structure as
// QualityBar below, reused so every meter on the card reads the same.
function SignalBar({ label, fraction, fillCls, trailing, title }) {
  const w = Math.max(0, Math.min(1, Number(fraction) || 0)) * 100;
  return (
    <div className="flex items-center gap-2 text-[11px]" title={title}>
      <span className="w-16 shrink-0 text-slate-500">{label}</span>
      <span className="flex-1 h-1.5 rounded bg-slate-100 overflow-hidden">
        <span className={`block h-full ${fillCls}`} style={{ width: `${w}%` }} />
      </span>
      {trailing && <span className="w-16 text-right text-slate-600 font-medium">{trailing}</span>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Quality — full-text peer-review assessment (services.quality_review), shown
// SEPARATELY from relevance. Grade + verdict inline; rubric on expand.
// ---------------------------------------------------------------------------

function QualityBar({ label, value }) {
  const v = Math.max(0, Math.min(5, Number(value) || 0));
  return (
    <div className="flex items-center gap-2 text-[11px]">
      <span className="w-28 shrink-0 text-slate-500">{label}</span>
      <span className="flex-1 h-1.5 rounded bg-slate-100 overflow-hidden">
        <span className="block h-full bg-teal-500" style={{ width: `${(v / 5) * 100}%` }} />
      </span>
      <span className="w-4 text-right text-slate-600 mono">{v}</span>
    </div>
  );
}

function QualityBlock({ quality }) {
  const q = quality || {};
  const grade = q.grade || '';
  if (!grade) {
    const why = q.basis === 'not_assessed' ? 'no open-access PDF' : 'not in the top-K reviewed set';
    return (
      <div className="mt-1.5 text-[11px] text-slate-400">
        <span className="uppercase tracking-wider font-semibold text-slate-500">Quality</span>{' '}
        not assessed <span className="text-slate-300">({why})</span>
      </div>
    );
  }
  const gradeCls = GRADE_CLS[grade] || 'bg-slate-100 text-slate-700 border-slate-300';
  return (
    <div className="mt-1.5">
      <div className="flex items-start gap-2 text-xs">
        <span className="uppercase tracking-wider font-semibold text-slate-500 mt-0.5">Quality</span>
        <span
          className={`shrink-0 px-1.5 py-0.5 rounded-md border text-[11px] font-bold ${gradeCls}`}
          title="Full-text peer-review grade (A best – D weak), independent of relevance to you"
        >
          {grade}
        </span>
        {q.verdict && <span className="text-slate-700 italic">{q.verdict}</span>}
      </div>
      <details className="group mt-1">
        <summary className="cursor-pointer text-[11px] uppercase tracking-wider font-semibold text-slate-500 hover:text-slate-700 select-none">
          Quality review <span className="text-slate-400 normal-case font-normal">· from full text</span>
        </summary>
        <div className="mt-1.5 space-y-1">
          <QualityBar label="soundness" value={q.soundness} />
          <QualityBar label="novelty" value={q.novelty} />
          <QualityBar label="significance" value={q.significance} />
          <QualityBar label="reproducibility" value={q.reproducibility} />
          <QualityBar label="clarity" value={q.clarity} />
          {q.key_strength && <p className="text-[11px] text-emerald-800 mt-1">＋ {q.key_strength}</p>}
          {q.key_weakness && <p className="text-[11px] text-rose-800">－ {q.key_weakness}</p>}
        </div>
      </details>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Why it matters — heuristic, no-LLM reason chips (paper.why from the API). Sits
// at the top of the card so the relevance story is the first thing read.
// ---------------------------------------------------------------------------

function WhyRow({ why }) {
  if (!Array.isArray(why) || why.length === 0) return null;
  return (
    <div className="mt-2">
      <div className="text-[11px] uppercase tracking-wider font-semibold text-slate-400 mb-1">
        Why it matters
      </div>
      <div className="flex flex-wrap gap-1.5">
        {why.map((reason) => (
          <span
            key={reason}
            className="inline-flex items-center px-2 py-0.5 rounded-full border border-teal-200 bg-teal-50 text-teal-800 text-[11px] font-medium"
          >
            {reason}
          </span>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Paper card
// ---------------------------------------------------------------------------

export default function PaperCard({ paper, selected, onToggleSelect }) {
  const authors = parseAuthorsString(paper.authors);
  if (authors.length > 0 && typeof paper.max_author_h_index === 'number') {
    authors[0] = { ...authors[0], h_index: paper.max_author_h_index };
  }
  const band = scoreToBand(paper.composite_score);
  const cardCls = BAND_CARD_CLS[band] || 'border border-slate-200 bg-white';
  const compositeStr =
    typeof paper.composite_score === 'number' ? paper.composite_score.toFixed(2) : '—';
  const bucket = BUCKET_LABEL[paper.role] || paper.role || '—';

  const titleNode = paper.url || paper.doi
    ? (
      <a
        href={paper.url || `https://doi.org/${paper.doi}`}
        target="_blank"
        rel="noreferrer noopener"
        className="text-sm font-bold text-slate-900 hover:text-teal-700 underline-offset-2 hover:underline"
      >
        {paper.title || '(untitled)'}
      </a>
    )
    : <span className="text-sm font-bold text-slate-900">{paper.title || '(untitled)'}</span>;

  return (
    <article
      className={`rounded-xl p-3 shadow-sm transition-colors ${cardCls} ${
        selected ? 'ring-1 ring-teal-300' : ''
      }`}
    >
      <div className="flex items-start gap-3">
        <input
          type="checkbox"
          checked={selected}
          onChange={() => onToggleSelect(paper.item_id)}
          aria-label={`Select ${paper.title || 'paper'}`}
          className="mt-1 h-4 w-4 shrink-0 rounded border-slate-300 text-teal-600 focus:ring-teal-500 cursor-pointer"
        />
        <div className="min-w-0 flex-1">
          <header className="mb-2">
            {/* Band pill + title — the band sets the card's visual weight. */}
            <div className="flex items-start gap-2">
              <span
                className={`shrink-0 mt-0.5 px-2 py-0.5 rounded-full border text-[10px] font-bold uppercase tracking-wider ${
                  band ? BAND_ACTIVE_CLS[band] : 'bg-slate-100 text-slate-500 border-slate-200'
                }`}
                title="Relevance to you (model + corpus + prestige) — not a quality judgment"
              >
                {band ? RELEVANCE_WORD[band] : 'unscored'}
              </span>
              <div className="leading-snug flex-1 min-w-0">{titleNode}</div>
            </div>

            {/* Relevance magnitude bar (anchors the band; raw value in tooltip). */}
            {band && (
              <div className="mt-1.5">
                <SignalBar
                  label="relevance"
                  fraction={Number(paper.composite_score) / 5}
                  fillCls={BAND_BAR_CLS[band]}
                  title={`Relevance to you ${compositeStr}/5 (model + corpus + prestige) — not a quality judgment`}
                />
              </div>
            )}

            {/* Why it matters — top of card, where the eye lands. */}
            <WhyRow why={paper.why} />

            <div className="mt-2">
              <AuthorByline authors={authors} source="feed" quiet />
            </div>
            <div className="text-[11px] text-slate-500 mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5">
              {paper.venue && <span className="italic">{paper.venue}</span>}
              {paper.pub_year && <span>{paper.pub_year}</span>}
              <Badge label={bucket} tone="amber" title={ROLE_HINT[paper.role] || 'Why this paper is here'} />
              {paper.feed_name && (
                <Badge label="feed" value={paper.feed_name} tone="sky" title="Source RSS feed" />
              )}
            </div>

            {/* Prestige as an anchored word + bar, not a raw 0–1 decimal. */}
            {typeof paper.prestige_score === 'number' && (
              <div className="mt-1.5">
                <SignalBar
                  label="prestige"
                  fraction={paper.prestige_score}
                  fillCls="bg-violet-500"
                  trailing={prestigeWord(paper.prestige_score)}
                  title={`Author / venue reputation ${paper.prestige_score.toFixed(2)} (0–1) — not paper quality`}
                />
              </div>
            )}
          </header>

          {paper.abstract && (
            <p className="mb-2 text-xs text-slate-600 line-clamp-3 leading-relaxed">
              {paper.abstract}
            </p>
          )}

          <QualityBlock quality={paper.quality} />

          {paper.rationale && (
            <details className="mt-2 group">
              <summary className="cursor-pointer text-[11px] uppercase tracking-wider font-semibold text-slate-500 hover:text-slate-700 select-none">
                Triage rationale
              </summary>
              <p className="mt-1.5 text-xs text-slate-700 italic whitespace-pre-line">
                {paper.rationale}
              </p>
            </details>
          )}
        </div>
      </div>
    </article>
  );
}
