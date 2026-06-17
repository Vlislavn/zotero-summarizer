import { startTransition, useEffect, useState } from 'react';
import InlineAnnotate from './InlineAnnotate.jsx';
import ProposedVerdictCard from './ProposedVerdictCard.jsx';
import ScoreHistogram from './ScoreHistogram.jsx';
import { StatusBanner, formatShortDate, truncateAuthors } from './shared.jsx';
import { isHighPrestige } from '../../utils/relevanceBands.js';
import { humanizeError } from '../../utils/humanizeError.js';
import Spinner from '../ui/Spinner.jsx';

// Stage-2 "Read next": the single Library surface. Ranked queue over the WHOLE
// library with an inline annotate panel (links, tags, per-paper deep review).
// Read/handled items are hidden unless toggled. An opt-in "Select" mode reveals
// checkboxes for bulk triage (the merged Browse/triage flow), kept secondary.
const REVEAL_STEP = 60;  // rows revealed initially and per "Show more" click

// Your explicit verdict label. A positive verdict pins the paper to the top of
// Read next (the backend sort) — this chip marks it so a labelled paper is
// instantly recognisable instead of vanishing (dont_read is handled-filtered,
// so it never reaches the queue and isn't in this map).
const USER_PRIORITY_LABEL = { must_read: 'must read', should_read: 'should read', could_read: 'could read' };

export default function ReadNextView({
  items, loading, err, includeRead, onToggleIncludeRead,
  readHidden, totalUnread, onSaved, status, modelReady, error, computedAt, scoresStale, distribution, onRescore, onReload,
  selectMode, onToggleSelectMode, selected, onToggleItem, onRunTriage, starting,
  // Bulk "Add to collection" (the Meaning-search → Zotero collection shortcut):
  // flat collection list + handler from Library; last target remembered so the
  // common "send to my reading collection" case is one click (Working Memory).
  collections = [], onAddToCollection, addingToCollection = false,
  // Client-side smart filters (Library owns the state; `items` arrives already
  // filtered). rawCount = pre-filter size, so we can tell "nothing fetched" apart
  // from "filtered to zero". filterSig = serialized filters, to reset the reveal.
  rawCount = 0, hasActiveFilters = false, onClearClientFilters, activeBands = [], onBandClick, filterSig = '',
  // Hybrid "Meaning" search: `semantic` = the list is ranked by similarity to
  // `searchQuery`; `rerankerLoading` = the cross-encoder is still downloading (we
  // show fusion order meanwhile); `semanticUnavailable` = corpus off (text match).
  semantic = false, searchQuery = '', rerankerLoading = false, semanticUnavailable = false,
  // Library quality floor (median of known prestige) — drives the per-card "top
  // author/venue" badge so the highest-quality papers are visibly marked.
  prestigeFloor = null,
}) {
  const computing = status === 'computing';
  const errored = status === 'error';
  const [expandedKey, setExpandedKey] = useState(null);
  // Target for the bulk "Add to collection" action. Defaults to the last-used
  // collection (validated against the current list) so the routine "send picks
  // to my reading collection" is select → one click.
  const [targetCollection, setTargetCollection] = useState(
    () => localStorage.getItem('zs:lastCollectionKey') || '',
  );
  const targetValid = collections.some((c) => c.key === targetCollection);
  // Incremental reveal: the backend returns the whole ranked library, but we only
  // mount a bounded slice so a ~2,400-item list paints fast (Flow/Doherty) and
  // stays scrollable (Miller's Law — chunk the long list rather than dump it).
  const [visibleCount, setVisibleCount] = useState(REVEAL_STEP);
  // A filter change should jump back to the top of the (now different) list — a
  // client filter doesn't remount the view (server filters do, via the key), so
  // reset the reveal here instead.
  useEffect(() => { setVisibleCount(REVEAL_STEP); }, [filterSig]);
  const shown = items.slice(0, visibleCount);
  // The expanded review renders in a RIGHT-SIDE panel (not full-width below the row),
  // so the list stays readable while you decide — "чуть правее, не размером с основную".
  const expandedItem = expandedKey ? items.find((it) => it.item_key === expandedKey) : null;
  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
        <div className="text-xs text-slate-600">
          <strong>{totalUnread}</strong> papers,{' '}
          {semantic
            ? <>ranked by similarity to “{searchQuery}”.</>
            : (modelReady
              ? (
                <span title="Best first: ranked by a blend of model relevance to you, goal match, and author/venue prestige — high-quality papers from strong authors/venues float to the top. Bands stay from the relevance score.">
                  ranked best-first — relevance &amp; quality.
                </span>
              )
              : 'ranked by recency (model not ready yet).')}
          {readHidden > 0 && !includeRead && (
            <span className="text-slate-400"> · {readHidden} read hidden</span>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-3">
          {modelReady && (
            <span className="flex items-center gap-2 text-[11px] text-slate-500">
              {computedAt && (
                <span title="When these relevance scores were last computed">
                  scores as of {formatShortDate(computedAt)}
                </span>
              )}
              {scoresStale && (
                <span className="text-amber-600" title="The model was retrained since these scores — Rescore for the latest ranking">
                  · model updated, rescore for latest
                </span>
              )}
              <button
                type="button"
                onClick={onRescore}
                disabled={computing}
                className="px-2 py-0.5 rounded-md border border-slate-300 text-slate-700 hover:bg-slate-50 disabled:opacity-50 disabled:cursor-not-allowed"
                title="Recompute relevance scores for your whole library (read + unread) against the current model. The first full scan covers ~2,400 papers and can take a few minutes; results appear as they compute and re-scans are fast."
              >
                {computing ? 'Scoring…' : 'Rescore'}
              </button>
            </span>
          )}
          <button
            type="button"
            onClick={onToggleSelectMode}
            className={`px-2 py-0.5 rounded-md text-[11px] font-medium border ${
              selectMode ? 'bg-teal-600 text-white border-teal-600' : 'border-slate-300 text-slate-700 hover:bg-slate-50'
            }`}
            title="Select multiple papers for a bulk action: add them all to a Zotero collection, or send to triage. (To file a single paper, just expand its row.)"
          >
            {selectMode ? 'Done selecting' : 'Select'}
          </button>
          {selectMode && (
            <button
              type="button"
              onClick={onRunTriage}
              disabled={!selected || selected.size === 0 || starting}
              className="px-3 py-0.5 rounded-md bg-teal-700 text-[11px] font-semibold text-white hover:bg-teal-800 disabled:bg-slate-300 disabled:text-slate-500"
            >
              {starting ? 'Starting…' : `Run triage (${selected?.size || 0})`}
            </button>
          )}
          {selectMode && collections.length > 0 && onAddToCollection && (
            <span className="inline-flex items-center gap-1">
              <select
                value={targetValid ? targetCollection : ''}
                onChange={(e) => setTargetCollection(e.target.value)}
                className="max-w-[180px] px-1.5 py-0.5 rounded-md border border-slate-300 text-[11px] text-slate-700 bg-white"
                title="Target Zotero collection for the selected papers"
              >
                <option value="">Collection…</option>
                {collections.map((c) => (
                  <option key={c.key} value={c.key}>
                    {`${' '.repeat(c.depth * 2)}${c.name}`}
                  </option>
                ))}
              </select>
              <button
                type="button"
                onClick={() => onAddToCollection(targetCollection)}
                disabled={!selected?.size || !targetValid || addingToCollection}
                className="px-3 py-0.5 rounded-md bg-slate-700 text-[11px] font-semibold text-white hover:bg-slate-800 disabled:bg-slate-300 disabled:text-slate-500"
                title="Add the selected papers to this Zotero collection (backup-first; no more memorizing titles and re-finding them in Zotero)"
              >
                {addingToCollection ? 'Adding…' : `Add to collection (${selected?.size || 0})`}
              </button>
            </span>
          )}
          <label className="flex items-center gap-1.5 text-xs text-slate-600 cursor-pointer select-none">
            <input type="checkbox" checked={includeRead} onChange={onToggleIncludeRead}
              className="h-4 w-4 rounded border-slate-300 text-teal-600 focus:ring-teal-500" />
            Show already-read (🧠/👀)
          </label>
        </div>
      </div>
      {modelReady && (
        <ScoreHistogram distribution={distribution} activeBands={activeBands} onBandClick={onBandClick} />
      )}
      {rerankerLoading && (
        <div className="mb-2 flex items-center gap-2 text-xs text-slate-600">
          <Spinner size="sm" color="teal" />
          Downloading the reranker model (first semantic search only) — showing BM25 + embedding results meanwhile; search again shortly for the reranked order.
        </div>
      )}
      {semanticUnavailable && (
        <div className="mb-2 text-xs text-amber-700">
          Semantic search needs the corpus enabled — showing exact text matches instead.
        </div>
      )}
      {computing && (
        <div className="mb-2 flex items-center gap-2 text-xs text-slate-600">
          <Spinner size="sm" color="teal" />
          Scoring your whole library… first full scan (~2,400 papers) can take a few minutes; results stream in as they compute, then re-scans are fast.
        </div>
      )}
      {errored && error && (
        <StatusBanner message={`Scoring failed: ${error}. Click Rescore to retry.`} isError />
      )}
      {err && (
        <div className="mb-2">
          <StatusBanner
            message={
              err.status === 422
                ? 'This view needs the updated backend — restart the app (the server has no auto-reload), then reload the page.'
                : err.status === 503
                  ? 'Zotero database was busy. Close Zotero if it’s open, then retry.'
                  : `Failed to load queue: ${humanizeError(err)}`
            }
            isError
          />
          {onReload && (
            <button
              type="button"
              onClick={onReload}
              className="mt-1 px-3 py-1 rounded-md border border-slate-300 text-xs text-slate-700 hover:bg-slate-50"
            >
              Retry
            </button>
          )}
        </div>
      )}
      {loading && items.length === 0 && <div className="p-4 text-center text-xs text-slate-500">Loading reading queue…</div>}
      {!loading && items.length === 0 && !computing && !errored && (
        hasActiveFilters && rawCount > 0 ? (
          <div className="p-6 text-center text-xs text-slate-500">
            No papers match your filters.
            <button
              type="button"
              onClick={onClearClientFilters}
              className="ml-2 px-2 py-0.5 rounded-md border border-slate-300 bg-white text-slate-700 hover:bg-slate-100 font-medium"
            >
              Clear filters
            </button>
          </div>
        ) : (
          <div className="p-6 text-center text-xs text-slate-500">
            Nothing to read — add papers from Today, adjust filters, or turn on “Show already-read”.
            {modelReady && !computedAt && <div className="mt-1">Click <strong>Rescore</strong> to rank by relevance.</div>}
          </div>
        )
      )}
      <div className={expandedItem ? 'lg:flex lg:gap-3 lg:items-start' : ''}>
      <ol className={`space-y-2 ${expandedItem ? 'lg:flex-1 lg:min-w-0' : ''}`}>
        {shown.map((it, idx) => (
          <li key={it.item_key}>
            <div
              className={`w-full flex items-start gap-2 p-2.5 rounded-xl border bg-white hover:border-teal-300 hover:bg-teal-50/30 ${
                expandedKey === it.item_key ? 'border-teal-400 ring-1 ring-teal-200' : 'border-slate-200'
              }`}
            >
              {selectMode && (
                <input
                  type="checkbox"
                  checked={selected?.has(it.item_key) || false}
                  onChange={() => onToggleItem(it.item_key)}
                  className="mt-1 h-4 w-4 rounded border-slate-300 text-teal-600 focus:ring-teal-500"
                />
              )}
              <button
                type="button"
                onClick={() => setExpandedKey(expandedKey === it.item_key ? null : it.item_key)}
                aria-expanded={expandedKey === it.item_key}
                className="min-w-0 flex-1 text-left flex items-start gap-3"
              >
                <span className="mono text-xs text-slate-400 mt-0.5 w-6 shrink-0 text-right">{idx + 1}</span>
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-medium text-slate-900 truncate">{it.title || '(untitled)'}</span>
                  <span className="block text-xs text-slate-500 truncate">{truncateAuthors(it.authors)}</span>
                  {(it.user_priority || typeof it.relevance_score === 'number' || it.why_reason || it.date_added) && (
                    <span className="mt-0.5 flex items-center gap-2 text-[11px]">
                      {/* Your label leads the row (Von Restorff): a paper you
                          marked is pinned to the top of Read next and flagged
                          here, so labelling makes it findable, never hidden. */}
                      {USER_PRIORITY_LABEL[it.user_priority] && (
                        <span
                          className="inline-flex items-center gap-1 px-1.5 py-0 rounded-full bg-amber-100 text-amber-900 border border-amber-300 font-semibold"
                          title="Your label — you set this paper's reading priority, so it's pinned to the top of Read next."
                        >
                          🏷 {USER_PRIORITY_LABEL[it.user_priority]}
                        </span>
                      )}
                      {typeof it.relevance_score === 'number' ? (
                        <span className="font-semibold text-teal-700" title="Model relevance score (1–5)">
                          ★ {it.relevance_score.toFixed(1)}
                        </span>
                      ) : (
                        <span className="text-slate-400" title="Not scored yet — run Rescore to rank this paper">
                          not scored yet
                        </span>
                      )}
                      {/* Von Restorff: mark the "best of the best" — high author/
                          venue prestige (≥ the library's quality floor) — so the
                          top-quality papers the blend floats up are identifiable.
                          Distinct ◆/violet from the ★/teal relevance score, and
                          from the full-text A–D Quality grade. */}
                      {isHighPrestige(it, prestigeFloor) && (
                        <span
                          className="inline-flex items-center gap-1 px-1.5 py-0 rounded-full bg-violet-50 text-violet-800 border border-violet-200 font-semibold"
                          title="High author/venue prestige — citation percentile at or above your library's median. A quality signal that floats this paper toward the top; distinct from the full-text Quality grade."
                        >
                          ◆ top author/venue
                        </span>
                      )}
                      {it.why_reason && (
                        <span className="px-1.5 py-0 rounded-full bg-slate-100 text-slate-600 border border-slate-200" title="Top reason this paper scored where it did">
                          {it.why_reason}
                        </span>
                      )}
                      {it.date_added && (
                        <span className="text-slate-400" title="When this paper was added to your library">
                          added {formatShortDate(it.date_added)}
                        </span>
                      )}
                    </span>
                  )}
                </span>
                <span className="flex items-center gap-2 shrink-0">
                  {it.read && <span title="already read" className="text-xs">🧠</span>}
                  <span className={`inline-block w-2.5 h-2.5 rounded-full ${it.has_pdf ? 'bg-emerald-500' : 'bg-slate-300'}`} title={it.has_pdf ? 'PDF attached' : 'No PDF'} />
                </span>
              </button>
            </div>
            {/* The fleet pre-decided a verdict → the Confirm/Override card (Phase
                2). One-tap ratify, or Override to expand the full editor below
                with the proposal pre-selected. Hidden once the row is expanded
                (the editor takes over) — exactly two paths, never both at once. */}
            {/* The fleet's Confirm/Override card stays inline on the row (small,
                one-tap). The FULL expanded editor moves to the right-side panel. */}
            {it.proposed_verdict && expandedKey !== it.item_key && (
              <ProposedVerdictCard
                itemKey={it.item_key}
                proposal={it.proposed_verdict}
                onSaved={() => { setExpandedKey(null); onSaved?.(); }}
                onOverride={() => setExpandedKey(it.item_key)}
              />
            )}
          </li>
        ))}
      </ol>
      {/* Right-side review panel — the expanded paper detail, sticky beside the list
          (stacks below on mobile). Narrower than the list, so the queue stays usable. */}
      {expandedItem && (
        <aside className="mt-2 lg:mt-0 lg:w-[44%] lg:shrink-0 lg:sticky lg:top-2 lg:self-start lg:max-h-[86vh] lg:overflow-auto rounded-xl border border-teal-200 bg-white shadow-sm">
          <div className="flex items-center justify-between gap-2 px-3 py-1.5 border-b border-slate-100">
            <span className="text-[11px] font-semibold uppercase tracking-[0.06em] text-slate-400 truncate">
              {expandedItem.title || '(untitled)'}
            </span>
            <button
              type="button"
              onClick={() => setExpandedKey(null)}
              title="Close review"
              className="shrink-0 px-1.5 rounded text-slate-400 hover:text-slate-700 hover:bg-slate-100"
            >
              ✕
            </button>
          </div>
          <div className="p-1">
            <InlineAnnotate
              itemKey={expandedItem.item_key}
              collections={collections}
              derivedPriorityOverride={expandedItem.proposed_verdict?.proposed || null}
              onSaved={() => { setExpandedKey(null); onSaved?.(); }}
              onQueueRefresh={() => onSaved?.()}
            />
          </div>
        </aside>
      )}
      </div>
      {items.length > shown.length && (
        <div className="mt-3 flex items-center justify-center gap-3 text-xs text-slate-500">
          <span>Showing {shown.length} of {items.length}</span>
          <button
            type="button"
            onClick={() => setVisibleCount((v) => v + REVEAL_STEP * 4)}
            className="px-3 py-1 rounded-md border border-slate-300 text-slate-700 hover:bg-slate-50"
          >
            Show more
          </button>
          <button
            type="button"
            onClick={() => startTransition(() => setVisibleCount(items.length))}
            className="px-3 py-1 rounded-md border border-slate-300 text-slate-700 hover:bg-slate-50"
          >
            Show all ({items.length})
          </button>
        </div>
      )}
    </div>
  );
}
