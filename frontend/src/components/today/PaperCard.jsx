// Today slate card — Stage 1 of the two-stage reading flow: read the abstract,
// then tick the checkbox. Per the Laws of UX gate:
//   • Von Restorff   — a relevance BAND (color + word) drives the card's visual
//                      weight, so treasures pop and likely-trash is muted.
//   • Selective Attention — a plain-language "why it matters" row sits at the top
//                      (right under the band + title), where the eye lands.
//   • Cognitive Load / Working Memory — ONE ordinal scale on this cull card: the
//                      relevance band. Prestige and full-text Quality A–D are
//                      Stage-2 reading signals that live in the Library, not here.
//
// The relevance band uses cull-card vocabulary (top pick / strong / fair / weak),
// deliberately NOT the Stage-2 must/should/could action labels. The full-text
// Quality A–D assessment lives in the full brief ("Open full brief ↗"), not here —
// the old per-card QualityBlock expander was a second ordinal scale this header
// forbids, so it was subtracted (Occam's Razor / Cognitive Load).

import AuthorByline from '../AuthorByline.jsx';
import { scoreToBand, BAND_ACTIVE_CLS } from '../../utils/relevanceBands.js';
import { Link } from 'react-router-dom';

// Relevance-band → card accent + tint (Von Restorff): treasures full-weight,
// trash desaturated. Keyed by the same band names as relevanceBands.scoreToBand.
const BAND_CARD_CLS = {
  must_read: 'border border-slate-200 border-l-4 border-l-emerald-500 bg-emerald-50/40',
  should_read: 'border border-slate-200 border-l-4 border-l-sky-500 bg-white',
  could_read: 'border border-slate-200 border-l-4 border-l-amber-400 bg-white',
  dont_read: 'border border-slate-200 border-l-2 border-l-rose-300 bg-slate-50 opacity-70',
};
// Treasure→trash wording for THIS card. Deliberately NOT the must/should/could
// priority words (those are the Stage-2 action labels) nor the Quality A–D grade.
const RELEVANCE_WORD = {
  must_read: 'top pick',
  should_read: 'strong',
  could_read: 'fair',
  dont_read: 'weak',
};

function parseAuthorsString(s) {
  if (!s || typeof s !== 'string') return [];
  return s
    .split(',')
    .map((name) => name.trim())
    .filter(Boolean)
    .map((name) => ({ name, h_index: null }));
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
  if (authors.length > 0 && typeof paper.max_author_h_index === 'number' && paper.max_author_h_index > 0) {
    authors[0] = { ...authors[0], h_index: paper.max_author_h_index };
  }
  const band = scoreToBand(paper.composite_score);
  const cardCls = BAND_CARD_CLS[band] || 'border border-slate-200 bg-white';
  const compositeStr =
    typeof paper.composite_score === 'number' ? paper.composite_score.toFixed(2) : '—';

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
            {/* Band pill + title — the band sets the card's visual weight and is
                the ONE relevance scale on the cull card (raw value in the tooltip). */}
            <div className="flex items-start gap-2">
              <span
                className={`shrink-0 mt-0.5 px-2 py-0.5 rounded-full border text-[10px] font-bold uppercase tracking-wider ${
                  band ? BAND_ACTIVE_CLS[band] : 'bg-slate-100 text-slate-500 border-slate-200'
                }`}
                title={`Relevance to you ${compositeStr}/5 (model + corpus + prestige) — not a quality judgment`}
              >
                {band ? RELEVANCE_WORD[band] : 'unscored'}
              </span>
              <div className="leading-snug flex-1 min-w-0">{titleNode}</div>
            </div>

            {/* Why it matters — top of card, where the eye lands. */}
            <WhyRow why={paper.why} />

            <div className="mt-2">
              <AuthorByline authors={authors} source="feed" quiet />
            </div>
            <div className="text-[11px] text-slate-500 mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5">
              {paper.venue && <span className="italic">{paper.venue}</span>}
              {paper.pub_year && <span>{paper.pub_year}</span>}
              {paper.feed_name && <span className="text-slate-400">· {paper.feed_name}</span>}
            </div>
          </header>

          {paper.abstract && (
            <p className="mb-2 text-xs text-slate-600 line-clamp-3 leading-relaxed">
              {paper.abstract}
            </p>
          )}

          {/* Open the full review — the SAME library review page (/paper/:key), keyed by
              the durable stable_feed_key (the top feed papers carry a rendered brief +
              the in-place review; others show the review). Reuse, not a new view; one
              name per target (Jakob): "full review" = the interactive page, everywhere. */}
          {paper.stable_feed_key && (
            <Link
              to={`/paper/${encodeURIComponent(paper.stable_feed_key)}`}
              className="mt-2 inline-block text-[11px] font-medium text-teal-700 hover:text-teal-900 hover:underline underline-offset-2"
            >
              Open full review ↗
            </Link>
          )}
        </div>
      </div>
    </article>
  );
}
