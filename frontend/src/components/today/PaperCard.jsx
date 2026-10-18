// Today cull card: relevance is separate from review evidence. Selection
// observations explain the existing server order; rendering never rescores.

import AuthorByline from '../AuthorByline.jsx';
import SelectionEvidence from './SelectionEvidence.jsx';
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
// Display-only relevance words, not saved priorities or scientific grades.
const RELEVANCE_WORD = {
  must_read: 'top pick',
  should_read: 'strong',
  could_read: 'moderate',
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

export default function PaperCard({ paper, selected, onToggleSelect, llmEnabled = true, selectionPolicy }) {
  const authors = parseAuthorsString(paper.authors);
  if (authors.length > 0 && typeof paper.max_author_h_index === 'number' && paper.max_author_h_index > 0) {
    authors[0] = { ...authors[0], h_index: paper.max_author_h_index };
  }
  const displayedScore = paper.selection_evidence?.stored?.composite_recorded === false
    || !Number.isFinite(paper.composite_score) ? null : paper.composite_score;
  const band = scoreToBand(displayedScore);
  const cardCls = BAND_CARD_CLS[band] || 'border border-slate-200 bg-white';

  const titleNode = paper.url || paper.doi
    ? (
      <a
        href={paper.url || `https://doi.org/${paper.doi}`}
        target="_blank"
        rel="noreferrer noopener"
        className="text-sm font-medium text-slate-900 hover:text-teal-700 underline-offset-2 hover:underline"
      >
        {paper.title || '(untitled)'}
      </a>
    )
    : <span className="text-sm font-medium text-slate-900">{paper.title || '(untitled)'}</span>;

  return (
    <article
      className={`rounded-xl p-3 transition-colors ${cardCls} ${
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
            <div className="flex flex-wrap items-start gap-2">
              <span
                className={`mt-0.5 px-2 py-0.5 rounded-full border text-[11px] font-medium ${
                  band ? BAND_ACTIVE_CLS[band] : 'bg-slate-100 text-slate-600 border-slate-200'
                }`}
                title="Relevance to you, not a scientific-quality judgment"
              >
                Relevance: {band ? RELEVANCE_WORD[band] : 'unscored'}
              </span>
              <div className="leading-snug flex-1 min-w-0 break-words">{titleNode}</div>
            </div>

            <SelectionEvidence paper={paper} selectionPolicy={selectionPolicy} />

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
          {paper.stable_feed_key && !paper.review_ready && (
            <p className="mt-2 text-xs text-amber-800">
              {llmEnabled ? 'Generate a review before adding.' : <>
                AI review is off. <Link className="underline" to="/settings">Enable AI</Link> before adding,
                or import this paper manually in Zotero.
              </>}
            </p>
          )}
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
