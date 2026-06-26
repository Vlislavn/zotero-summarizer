import { useEffect, useRef, useState } from 'react';
import InlineAnnotate from './InlineAnnotate.jsx';
import OpenBriefButton from './OpenBriefButton.jsx';
import ScoreHistogram from './ScoreHistogram.jsx';
import { StatusBanner, formatShortDate, truncateAuthors } from './shared.jsx';
import { pretty } from '../../utils/priorityLabels.js';
import { CHIP_TONE, bandTone, gradeTone, BAND_LABEL } from '../paper/review/tones.js';
import { Disclosure } from '../paper/review/primitives.jsx';
import { humanizeError } from '../../utils/humanizeError.js';
import Spinner from '../ui/Spinner.jsx';

// Stage-2 "Read next": the single Library surface. Ranked queue over the WHOLE
// library with an inline annotate panel (links, tags, per-paper deep review).
// Read/handled items are hidden unless toggled. An opt-in "Select" mode reveals
// checkboxes for bulk triage (the merged Browse/triage flow), kept secondary.
const REVEAL_STEP = 60;  // rows revealed initially and per "Show more" click
const DESKTOP_QUERY = '(min-width: 1024px)';

// Your explicit verdict label. A positive verdict pins the paper to the top of
// Read next (the backend sort) — this chip marks it so a labelled paper is
// instantly recognisable instead of vanishing (dont_read is handled-filtered,
// so it never reaches the queue and isn't in this map).
const USER_PRIORITY_LABEL = { must_read: 'must read', should_read: 'should read', could_read: 'could read' };

function useMediaQuery(query) {
  const getMatches = () => (
    typeof window !== 'undefined'
    && typeof window.matchMedia === 'function'
    && window.matchMedia(query).matches
  );
  const [matches, setMatches] = useState(getMatches);

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return undefined;
    const mql = window.matchMedia(query);
    const onChange = (event) => setMatches(event.matches);
    setMatches(mql.matches);
    if (typeof mql.addEventListener === 'function') {
      mql.addEventListener('change', onChange);
      return () => mql.removeEventListener('change', onChange);
    }
    mql.addListener(onChange);
    return () => mql.removeListener(onChange);
  }, [query]);

  return matches;
}

function ExpandedPaperPanel({
  item, collections, onClose, onSaved, onQueueRefresh, panelRef, variant = 'mobile',
}) {
  const desktop = variant === 'desktop';
  return (
    <aside
      ref={panelRef}
      data-testid="expanded-paper-panel"
      aria-label={`Review ${item.title || item.item_key}`}
      className={
        desktop
          ? 'mt-2 lg:mt-0 lg:w-[44%] lg:shrink-0 lg:sticky lg:top-2 lg:self-start lg:max-h-[86vh] lg:overflow-auto rounded-xl border border-teal-200 bg-white shadow-sm'
          : 'mt-2 rounded-xl border border-teal-200 bg-white'
      }
    >
      <div className="flex items-center justify-between gap-2 px-3 py-2 border-b border-slate-100">
        <span className="min-w-0 text-[11px] font-semibold uppercase tracking-[0.06em] text-slate-400 truncate">
          {item.title || '(untitled)'}
        </span>
        <button
          type="button"
          onClick={onClose}
          title="Close review"
          aria-label="Close review"
          className="shrink-0 px-2 rounded text-slate-400 hover:text-slate-700 hover:bg-slate-100"
        >
          ✕
        </button>
      </div>
      <div className={desktop ? 'p-1' : 'p-2 overflow-x-hidden'}>
        <InlineAnnotate
          itemKey={item.item_key}
          collections={collections}
          derivedPriorityOverride={item.proposed_verdict?.proposed || null}
          onSaved={onSaved}
          onQueueRefresh={onQueueRefresh}
        />
      </div>
    </aside>
  );
}

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
}) {
  const computing = status === 'computing';
  const errored = status === 'error';
  const [expandedKey, setExpandedKey] = useState(null);
  const expandedPanelRef = useRef(null);
  const isDesktop = useMediaQuery(DESKTOP_QUERY);
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
  // Desktop keeps the review beside the list. Mobile renders it directly under
  // the tapped row; otherwise the panel lands after the whole revealed list.
  const expandedItem = expandedKey ? items.find((it) => it.item_key === expandedKey) : null;
  useEffect(() => {
    if (!expandedKey || isDesktop) return;
    const node = expandedPanelRef.current;
    if (node && typeof node.scrollIntoView === 'function') {
      const schedule = typeof window.requestAnimationFrame === 'function'
        ? window.requestAnimationFrame
        : (fn) => setTimeout(fn, 0);
      schedule(() => node.scrollIntoView({ block: 'start', behavior: 'smooth' }));
    }
  }, [expandedKey, isDesktop]);

  function closeExpanded() {
    setExpandedKey(null);
  }

  function saveExpanded() {
    setExpandedKey(null);
    onSaved?.();
  }

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
            // One control, not three. The score date is reference (→ title); the
            // only thing worth saying out loud is "the model changed, rescore" —
            // so the stale state IS the button (amber + named), the single accent
            // (Von Restorff). Quiet when current.
            <button
              type="button"
              onClick={onRescore}
              disabled={computing}
              title={`Recompute relevance scores for your whole library against the current model${computedAt ? ` · scores as of ${formatShortDate(computedAt)}` : ''}. First full scan (~2,400 papers) can take a few minutes; re-scans are fast.`}
              className={`px-2 py-0.5 rounded-md border text-[11px] disabled:opacity-50 disabled:cursor-not-allowed ${
                scoresStale
                  ? 'border-amber-300 bg-amber-50 text-amber-700 hover:bg-amber-100'
                  : 'border-slate-300 text-slate-700 hover:bg-slate-50'
              }`}
            >
              {computing ? 'Scoring…' : scoresStale ? 'Rescore — model updated' : 'Rescore'}
            </button>
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
            Show already-read
          </label>
        </div>
      </div>
      {/* Score distribution is reference, not the task — fold it behind the shared
          Disclosure (closed by default) so the ranked queue owns the top of the
          surface (Serial Position). Active band-filters surface in the summary. */}
      {modelReady && distribution && (
        <div className="mb-3">
          <Disclosure
            summary="Score distribution"
            count={activeBands?.length ? `${activeBands.length} band filter${activeBands.length === 1 ? '' : 's'}` : null}
          >
            <ScoreHistogram distribution={distribution} activeBands={activeBands} onBandClick={onBandClick} />
          </Disclosure>
        </div>
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
            tone={err.status === 503 ? 'warn' : 'error'}
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
      <div className={expandedItem && isDesktop ? 'lg:flex lg:gap-3 lg:items-start' : ''}>
      <ol className={`space-y-2 ${expandedItem && isDesktop ? 'lg:flex-1 lg:min-w-0' : ''}`}>
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
                className="min-w-0 flex-1 text-left flex items-start gap-2 sm:gap-3"
              >
                <span className="mono text-xs text-slate-400 mt-0.5 w-6 shrink-0 text-right">{idx + 1}</span>
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-medium text-slate-900 line-clamp-2 sm:truncate">{it.title || '(untitled)'}</span>
                  <span className="block text-xs text-slate-500 line-clamp-1 sm:truncate">{truncateAuthors(it.authors)}</span>
                  {(it.user_priority || it.proposed_verdict?.proposed || typeof it.relevance_score === 'number') && (
                    <span className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] leading-5">
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
                      {/* The fleet's pre-decided verdict — quiet, info-only (one
                          chip, not a card). Open the row to confirm/change it (it's
                          pre-selected there). Hidden once you've set your own label. */}
                      {!it.user_priority && it.proposed_verdict?.proposed && (
                        <span
                          className="inline-flex items-center gap-1 px-1.5 py-0 rounded-full bg-indigo-50 text-indigo-700 border border-indigo-200"
                          title="The review fleet's suggested reading verdict (from cached deep-review signals). Open the paper to confirm or change it — it's pre-selected."
                        >
                          ◇ {pretty(it.proposed_verdict.proposed)}
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
                      {/* Author/venue prestige is NOT a row chip — it's already
                          baked into the best-first order (position encodes it), so
                          a per-row violet "◆ top author/venue" pill was the 4th
                          colour for a signal the rank already carries. Subtracted;
                          it stays in the paper's detail. */}
                      {/* The deep-review QUALITY cause for the lift — ONE word per
                          card: the decisive verdict (Highlight/Flag) when present,
                          else the A–D grade. "Quality", never "band" (band = the
                          relevance tier). Reuses the shared review tones so a
                          Highlight reads the same emerald everywhere. */}
                      {(() => {
                        const band = String(it.quality_band || '').toLowerCase();
                        const grade = String(it.quality_grade || '').toUpperCase();
                        if (band === 'highlight' || band === 'flag') {
                          return (
                            <span
                              className={`inline-flex items-center px-1.5 py-0 rounded-full border font-semibold ${CHIP_TONE[bandTone(band)]}`}
                              title="Deep-review quality verdict — floats high-quality papers up (and sinks weak ones) WITHIN their relevance band, never across it."
                            >
                              {BAND_LABEL[band]}
                            </span>
                          );
                        }
                        if (grade) {
                          return (
                            <span
                              className={`inline-flex items-center px-1.5 py-0 rounded-full border font-semibold ${CHIP_TONE[gradeTone(grade)]}`}
                              title="Full-text deep-review quality grade (A–D) — floats high-quality papers up within their relevance band."
                            >
                              {grade}
                            </span>
                          );
                        }
                        return null;
                      })()}
                    </span>
                  )}
                </span>
                <span className="flex flex-col sm:flex-row items-center gap-1 sm:gap-2 shrink-0">
                  {it.read && <span title="already read" className="text-xs">🧠</span>}
                  <span className={`inline-block w-2.5 h-2.5 rounded-full ${it.has_pdf ? 'bg-emerald-500' : 'bg-slate-300'}`} title={it.has_pdf ? 'PDF attached' : 'No PDF'} />
                </span>
              </button>
              {/* Direct shortcut to the standalone HTML brief — a sibling of the
                  expand button (not nested inside it). Builds on demand if the
                  brief hasn't been generated yet, then opens it in a new tab. */}
              <OpenBriefButton itemKey={it.item_key} hasPdf={Boolean(it.has_pdf)} />
            </div>
            {expandedKey === it.item_key && !isDesktop && (
              <ExpandedPaperPanel
                item={it}
                collections={collections}
                panelRef={expandedPanelRef}
                onClose={closeExpanded}
                onSaved={saveExpanded}
                onQueueRefresh={() => onSaved?.()}
              />
            )}
          </li>
        ))}
      </ol>
      {/* Right-side review panel — the expanded paper detail, sticky beside the list
          on desktop. Narrower than the list, so the queue stays usable. */}
      {expandedItem && isDesktop && (
        <ExpandedPaperPanel
          item={expandedItem}
          collections={collections}
          onClose={closeExpanded}
          onSaved={saveExpanded}
          onQueueRefresh={() => onSaved?.()}
          variant="desktop"
        />
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
        </div>
      )}
    </div>
  );
}
