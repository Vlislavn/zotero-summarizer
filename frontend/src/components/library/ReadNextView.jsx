import { useEffect, useRef, useState } from 'react';
import InlineAnnotate from './InlineAnnotate.jsx';
import BulkVerdictBar from './BulkVerdictBar.jsx';
import { StatusBanner, formatShortDate, truncateAuthors } from './shared.jsx';
import { pretty } from '../../utils/priorityLabels.js';
import { groupRows, isDecided, qualityHint, scoreStatus } from './readNextCopy.js';
import { CustomPanel, OrderCaption, OrderSelect } from './OrderControls.jsx';
import { WhyHerePanel } from './WhyHere.jsx';
import { BALANCED_ORDER } from '../../utils/readNextOrder.js';
import { CHIP_TONE, bandTone, gradeTone, gradeBasis, letterMuted, mutedReason, BAND_LABEL } from '../paper/review/tones.js';
import { Disclosure } from '../paper/review/primitives.jsx';
import { humanizeError } from '../../utils/humanizeError.js';
import { cleanAbstract } from '../../utils/feedText.js';
import { SORT_FIELDS, SORT_DEFAULT_DIR, DEFAULT_SORT } from '../../utils/relevanceBands.js';
import Spinner from '../ui/Spinner.jsx';
import { readStorage } from '../../utils/safeStorage.js';

// Stage-2 "Read next": the single Library surface. Ranked queue over the WHOLE
// library with an inline annotate panel (links, tags, per-paper deep review).
// Read/handled items are hidden unless toggled. An opt-in "Select" mode reveals
// checkboxes for bulk triage (the merged Browse/triage flow), kept secondary.
const REVEAL_STEP = 60;  // rows revealed initially and per "Show more" click
const MUST_PREVIEW = 3;  // your own Must read rows shown before "Show all"
const DESKTOP_QUERY = '(min-width: 1024px)';

// Your own verdict. A decided paper carries this chip, so it stays findable instead
// of vanishing (dont_read is handled-filtered, so it never reaches the queue). In the
// default order the list is grouped (`groupRows`); the chip names the verdict wherever
// the group header does not (the Must group, by its header).
const USER_PRIORITY_LABEL = { must_read: 'must read', should_read: 'should read', could_read: 'could read' };

// The fleet's proposal never writes anything: the chip says whether it is flagged,
// else how sure it is. Acting on it is the row's own verdict panel.
function proposalNote(proposal) {
  if ((proposal.flags || []).some(Boolean)) return 'flagged';
  return `${Math.round((typeof proposal.confidence === 'number' ? proposal.confidence : 0) * 100)}%`;
}

function GroupHeader({ children, action = null }) {
  return (
    <div className="mb-2 mt-3 flex items-center justify-between gap-2">
      <h3 className="text-[11px] font-semibold uppercase tracking-[0.06em] text-slate-500">{children}</h3>
      {action}
    </div>
  );
}

function GroupAction({ expanded, onClick, children }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-expanded={expanded}
      className="shrink-0 px-2 py-1.5 -my-1 rounded-md text-xs font-medium text-teal-700 hover:bg-teal-50"
    >
      {children}
    </button>
  );
}

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
  item, collections, onClose, onSaved, onQueueRefresh, onNotesDirtyChange, panelRef, why = null, variant = 'mobile',
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
          onSaved={onSaved}
          onQueueRefresh={onQueueRefresh}
          onNotesDirtyChange={onNotesDirtyChange}
        />
        {why && <div className="px-1 pb-2 pt-1">{why}</div>}
      </div>
    </aside>
  );
}

// Feed abstracts arrive as clipped HTML ("<p>Nature, Published online… <a hr"): show the
// text, without the tags or a tag cut off by the clip.
const previewText = (text) => cleanAbstract(text).replace(/<\/?[a-z][^>]*$/i, '').trim();

export default function ReadNextView({
  items, loading, err, includeRead, onToggleIncludeRead,
  queueItems = items, onSaved, status, modelReady, error, computedAt, scoresStale, onRescore, onReload,
  selectMode, onToggleSelectMode, selected, onToggleItem, onRunTriage, onBulkDone, starting,
  // Bulk "Add to collection" (the Meaning-search → Zotero collection shortcut):
  // flat collection list + handler from Library; last target remembered so the
  // common "send to my reading collection" case is one click (Working Memory).
  collections = [], onAddToCollection, addingToCollection = false, serverAvailable = true, offline = false,
  // Client-side smart filters (Library owns the state; `items` arrives already
  // filtered). rawCount = pre-filter size, so we can tell "nothing fetched" apart
  // from "filtered to zero". filterSig = serialized filters, to reset the reveal.
  rawCount = 0, hasActiveFilters = false, filterSig = '',
  // Hybrid "Meaning" search: `semantic` = the list is ranked by similarity to
  // `searchQuery`; `rerankerLoading` = the cross-encoder is still downloading (we
  // show fusion order meanwhile); `semanticUnavailable` = corpus off (text match).
  semantic = false, searchQuery = '', rerankerLoading = false, semanticUnavailable = false,
  // Explicit Zotero-column sort (Library owns the state; `items` arrive already
  // sorted). 'best' = the blended server order.
  sort = DEFAULT_SORT, onSortChange,
  // Read-next order presets: `orderMeta` ({coverage, fill, balancedWeights}) is set only
  // where the Order control exists (Best match, no search, parts in the response);
  // Library owns `order` and has already sorted `items` by it.
  order = BALANCED_ORDER, onOrderChange, orderMeta = null,
}) {
  const computing = status === 'computing';
  // A failed read must leave Rescore available even with retained last-good rows.
  const errored = status === 'error';
  const rescoreActionable =
    computing || errored || err != null || scoresStale || !computedAt
    || items.some((it) => typeof it.relevance_score !== 'number');
  const [expandedKey, setExpandedKey] = useState(null);
  const [notesDirty, setNotesDirty] = useState(false);
  const [mustAll, setMustAll] = useState(false);  // «Show all N» of your own Must read (this visit)
  const [decidedOpen, setDecidedOpen] = useState(false);  // the «Decided by you» block
  const orderOn = Boolean(orderMeta && onOrderChange);
  const activeOrder = orderOn ? order : BALANCED_ORDER;
  const expandedPanelRef = useRef(null);
  const isDesktop = useMediaQuery(DESKTOP_QUERY);
  // Target for the bulk "Add to collection" action. Defaults to the last-used
  // collection (validated against the current list) so the routine "send picks
  // to my reading collection" is select → one click.
  const [targetCollection, setTargetCollection] = useState(
    () => readStorage('zs:lastCollectionKey') || '',
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
  // Default order without a search: three groups, computed from each row's own verdict
  // (so the offline working set groups too). Any other sort, or a search, is one flat
  // list where a decided row says so itself. The reveal pages through the undecided rows.
  const groups = sort.field === 'best' && !semantic && !searchQuery.trim() ? groupRows(items) : null;
  const shown = (groups ? groups.todo : items).slice(0, visibleCount);
  const pagedTotal = groups ? groups.todo.length : items.length;
  // Counts in the headers are a live answer; the offline copy shows none.
  const count = (n) => (offline ? '' : ` · ${n}`);
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

  function chooseExpanded(key) {
    if (notesDirty && !window.confirm('You have unsaved notes on this paper. Leave without saving them?')) return;
    setNotesDirty(false);
    setExpandedKey(key);
  }

  function closeExpanded() {
    chooseExpanded(null);
  }

  function refreshExpanded() {
    if (!notesDirty) onSaved?.();
  }

  function saveExpanded() {
    if (notesDirty) return;
    setExpandedKey(null);
    onSaved?.();
  }

  // «Why it's here» lives inside the expanded paper (Order presets only).
  function whyHere(it) {
    if (!orderOn || !it.rank_parts) return null;
    return (
      <Disclosure summary="Why it's here">
        <WhyHerePanel
          item={it}
          order={order}
          meta={orderMeta}
          mustCount={queueItems.filter((r) => r.rank_group === 0).length}
        />
      </Disclosure>
    );
  }

  const rows = (list, ownChip) => (
    <ol className="space-y-2">{list.map((it, idx) => renderRow(it, idx + 1, ownChip))}</ol>
  );

  function renderRow(it, number, ownChip) {
    return (
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
            onClick={() => chooseExpanded(expandedKey === it.item_key ? null : it.item_key)}
            aria-expanded={expandedKey === it.item_key}
            className="min-w-0 flex-1 text-left flex items-start gap-2 sm:gap-3"
          >
            <span className="mono text-xs text-slate-400 mt-0.5 w-6 shrink-0 text-right">{number}</span>
            <span className="min-w-0 flex-1">
              <span className="text-sm font-medium text-slate-900 line-clamp-2 sm:line-clamp-1">{it.title || '(untitled)'}</span>
              <span className="text-xs text-slate-500 line-clamp-1">{truncateAuthors(it.authors)}</span>
              {(it.review_digest?.tldr || it.abstract_preview) && (
                <span className="mt-1 text-xs text-slate-600 line-clamp-2">
                  {it.review_digest?.tldr || previewText(it.abstract_preview)}
                </span>
              )}
              {(it.user_priority || it.proposed_verdict?.proposed || typeof it.relevance_score === 'number') && (
                <span className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] leading-5">
                  {/* Your own verdict leads the row (Von Restorff): a decided
                      paper is flagged here, so deciding makes it findable,
                      never hidden. */}
                  {ownChip && isDecided(it) && USER_PRIORITY_LABEL[it.user_priority] && (
                    <span
                      className="inline-flex items-center gap-1 px-1.5 py-0 rounded-full bg-amber-100 text-amber-900 border border-amber-300 font-semibold"
                      title="Your own verdict — you already decided this paper."
                    >
                      You: {USER_PRIORITY_LABEL[it.user_priority]}
                    </span>
                  )}
                  {/* The fleet's pre-decided verdict — a quiet in-row marker, never
                      an action. Hidden once you've set your own label. */}
                  {!it.user_priority && it.proposed_verdict?.proposed && (
                    <span
                      className="inline-flex items-center gap-1 px-1.5 py-0 rounded-full bg-indigo-50 text-indigo-700 border border-indigo-200"
                      title="Full-text review suggestion — it does not affect list order. Set your own verdict by opening the paper."
                    >
                      ◇ Review suggests: {pretty(it.proposed_verdict.proposed)} · {proposalNote(it.proposed_verdict)}
                    </span>
                  )}
                  {typeof it.relevance_score === 'number' ? (
                    <span className="font-semibold text-teal-700" title="Model relevance score (1–5)">
                      ★ {it.relevance_score.toFixed(1)}
                    </span>
                  ) : (
                    <span className="text-slate-500" title="Not scored yet — run Rescore to rank this paper">
                      not scored yet
                    </span>
                  )}
                  {/* Why it surfaced — the top signal behind its rank (topic
                      match / library affinity / prestige). Answers "why did I
                      get this paper?" at a glance without opening the row. */}
                  {it.why_reason && !(orderOn && it.rank_parts) && (
                    <span className="min-w-0 max-w-full truncate text-slate-600" title={`Main model reason — the strongest signal behind its score: ${it.why_reason}`}>
                      · {it.why_reason}
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
                          title={qualityHint(activeOrder.preset === 'balanced', 'Deep-review quality verdict')}
                        >
                          {BAND_LABEL[band]}
                        </span>
                      );
                    }
                    if (grade) {
                      return (
                        <span
                          className={`inline-flex items-center px-1.5 py-0 rounded-full border font-semibold ${CHIP_TONE[gradeTone(grade, band)]}`}
                          title={`${qualityHint(activeOrder.preset === 'balanced', 'Full-text deep-review rigor letter (A–D)')}${letterMuted(grade, band) ? ` Grey A: checklist only — ${mutedReason(band)}.` : ''}`}
                        >
                          {[grade, gradeBasis(it.quality_met, it.quality_applicable)].filter(Boolean).join(' · ')}
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
              <span className="inline-flex items-center gap-1" title={it.has_pdf ? 'PDF attached' : 'No PDF'}>
                <span className={`inline-block w-2.5 h-2.5 rounded-full ${it.has_pdf ? 'bg-emerald-500' : 'bg-slate-300'}`} />
                {it.has_pdf && <span className="text-[10px] font-medium text-emerald-700">PDF</span>}
              </span>
            </span>
          </button>
        </div>
        {expandedKey === it.item_key && !isDesktop && (
          <ExpandedPaperPanel
            item={it}
            collections={collections}
            panelRef={expandedPanelRef}
            onClose={closeExpanded}
            onSaved={saveExpanded}
            onNotesDirtyChange={setNotesDirty}
            onQueueRefresh={refreshExpanded}
            why={whyHere(it)}
          />
        )}
      </li>
    );
  }

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
        <div className="text-xs text-slate-600 flex flex-wrap items-center gap-x-2 gap-y-1">
          {/* Zotero-column sort (Jakob's Law: same field names as Zotero's item
              table). One select + one direction toggle; 'Best match' restores the
              blended server order and hides the toggle. */}
          {onSortChange && (
            <span className="inline-flex items-center gap-1">
              <select
                value={sort.field}
                onChange={(e) => {
                  const field = e.target.value;
                  onSortChange(field === 'best' ? DEFAULT_SORT : { field, dir: SORT_DEFAULT_DIR[field] });
                }}
                className="px-1.5 py-0.5 rounded-md border border-slate-300 text-[11px] text-slate-700 bg-white"
                title="Sort the list — same columns as Zotero. Best match puts your own Must read first, then uses relevance, goals, and evidence; review suggestions do not affect order."
                aria-label="Sort by"
              >
                {SORT_FIELDS.map((f) => (
                  <option key={f.key} value={f.key}>{f.label}</option>
                ))}
              </select>
              {sort.field !== 'best' && (
                <button
                  type="button"
                  onClick={() => onSortChange({ ...sort, dir: sort.dir === 'asc' ? 'desc' : 'asc' })}
                  className="px-1.5 py-0.5 rounded-md border border-slate-300 text-[11px] text-slate-700 bg-white hover:bg-slate-50"
                  title={sort.dir === 'asc' ? 'Ascending — click for descending' : 'Descending — click for ascending'}
                  aria-label="Toggle sort direction"
                >
                  {sort.dir === 'asc' ? '↑' : '↓'}
                </button>
              )}
            </span>
          )}
          {orderOn && <OrderSelect order={order} meta={orderMeta} onChange={onOrderChange} />}
          {sort.field === 'best' && (
            offline ? <span>· cached working set order</span> : semantic
              ? <span>· ranked by similarity to “{searchQuery}”</span>
              : (modelReady
                ? orderOn ? <OrderCaption order={order} meta={orderMeta} /> : (
                  <span title="Best first: your own Must read papers, then model relevance + goal match + author/venue and quality evidence. Review suggestions do not affect this order.">
                    · relevance + goals + evidence
                  </span>
                )
                : <span>· ranked by recency (model not ready yet)</span>)
          )}
        </div>
        <div className="flex flex-wrap items-center gap-3">
          {modelReady && !offline && (
            // Plainly: how old the ranking is and whether the model changed since.
            <span
              className={`text-[11px] ${scoresStale ? 'text-amber-800' : 'text-slate-500'}`}
              data-testid="score-status"
            >
              {scoreStatus({
                computedAt, scoresStale,
                unscored: items.filter((it) => typeof it.relevance_score !== 'number').length,
              })}
            </span>
          )}
          {modelReady && rescoreActionable && (
            // One control, only when actionable; the stale state IS the button
            // (amber), the single accent (Von Restorff). Current + fully scored →
            // no button at all.
            <button
              type="button"
              onClick={onRescore}
              disabled={computing || !serverAvailable}
              title={`Recompute relevance scores for your whole library against the current model${computedAt ? ` · scores as of ${formatShortDate(computedAt)}` : ''}. First full scan (~2,400 papers) can take a few minutes; re-scans are fast.`}
              className={`px-2 py-0.5 rounded-md border text-[11px] disabled:opacity-50 disabled:cursor-not-allowed ${
                scoresStale
                  ? 'border-amber-300 bg-amber-50 text-amber-700 hover:bg-amber-100'
                  : 'border-slate-300 text-slate-700 hover:bg-slate-50'
              }`}
            >
              {computing ? 'Scoring…' : 'Rescore'}
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
              disabled={!serverAvailable || !selected || selected.size === 0 || starting}
              className="px-3 py-0.5 rounded-md bg-teal-700 text-[11px] font-semibold text-white hover:bg-teal-800 disabled:bg-slate-300 disabled:text-slate-500"
            >
              {starting ? 'Starting…' : `Run triage (${selected?.size || 0})`}
            </button>
          )}
          {selectMode && onBulkDone && (
            <BulkVerdictBar keys={[...(selected || [])]} disabled={!serverAvailable} onDone={onBulkDone} />
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
                disabled={!serverAvailable || !selected?.size || !targetValid || addingToCollection}
                className="px-3 py-0.5 rounded-md bg-slate-700 text-[11px] font-semibold text-white hover:bg-slate-800 disabled:bg-slate-300 disabled:text-slate-500"
                title="Add the selected papers to this Zotero collection (backup-first; no more memorizing titles and re-finding them in Zotero)"
              >
                {addingToCollection ? 'Adding…' : `Add to collection (${selected?.size || 0})`}
              </button>
            </span>
          )}
          <label className="flex items-center gap-1.5 text-xs text-slate-600 cursor-pointer select-none">
            <input type="checkbox" disabled={!serverAvailable} checked={includeRead} onChange={onToggleIncludeRead}
              className="h-4 w-4 rounded border-slate-300 text-teal-600 focus:ring-teal-500" />
            Show already-read
          </label>
        </div>
      </div>
      {orderOn && order.preset === 'custom' && (
        <CustomPanel order={order} meta={orderMeta} onChange={onOrderChange} />
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
          Scoring your whole library… the complete result appears when scoring finishes. The previous cache is preserved if scoring fails.
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
      {!loading && items.length === 0 && !computing && !errored && !err && (
        hasActiveFilters && rawCount > 0 ? (
          <div className="p-6 text-center text-xs text-slate-500">
            No papers match your filters — Clear all, above, shows the whole list again.
          </div>
        ) : (
          <div className="p-6 text-center text-xs text-slate-500">
            Nothing to read — add papers from Today, adjust filters, or turn on “Show already-read”.
            {modelReady && !computedAt && <div className="mt-1">Click <strong>Rescore</strong> to rank by relevance.</div>}
          </div>
        )
      )}
      <div className={expandedItem && isDesktop ? 'lg:flex lg:gap-3 lg:items-start' : ''}>
      <div className={expandedItem && isDesktop ? 'lg:flex-1 lg:min-w-0' : ''}>
        {groups && groups.must.length > 0 && (
          <section aria-label="Your must read">
            <GroupHeader
              action={groups.must.length > MUST_PREVIEW && (
                <GroupAction expanded={mustAll} onClick={() => setMustAll((v) => !v)}>
                  {mustAll ? 'Show fewer' : `Show all${offline ? '' : ` ${groups.must.length}`}`}
                </GroupAction>
              )}
            >
              Your must read{count(groups.must.length)}
            </GroupHeader>
            {rows(mustAll ? groups.must : groups.must.slice(0, MUST_PREVIEW), false)}
          </section>
        )}
        {groups && <GroupHeader>To decide{count(groups.todo.length)}</GroupHeader>}
        {rows(shown, true)}
        {pagedTotal > shown.length && (
          <div className="mt-3 flex items-center justify-center gap-3 text-xs text-slate-500">
            <span>Showing {shown.length} of {pagedTotal}</span>
            <button
              type="button"
              onClick={() => setVisibleCount((v) => v + REVEAL_STEP * 4)}
              className="px-3 py-1 rounded-md border border-slate-300 text-slate-700 hover:bg-slate-50"
            >
              Show more
            </button>
          </div>
        )}
        {groups && groups.decided.length > 0 && (
          <section aria-label="Decided by you" className="mt-3">
            <GroupAction expanded={decidedOpen} onClick={() => setDecidedOpen((v) => !v)}>
              Decided by you{count(groups.decided.length)} · {decidedOpen ? 'Hide' : 'Show'}
            </GroupAction>
            {decidedOpen && <div className="mt-2">{rows(groups.decided, true)}</div>}
          </section>
        )}
      </div>
      {/* Right-side review panel — the expanded paper detail, sticky beside the list
          on desktop. Narrower than the list, so the queue stays usable. */}
      {expandedItem && isDesktop && (
        <ExpandedPaperPanel
          item={expandedItem}
          collections={collections}
          onClose={closeExpanded}
          onSaved={saveExpanded}
          onNotesDirtyChange={setNotesDirty}
          onQueueRefresh={refreshExpanded}
          why={whyHere(expandedItem)}
          variant="desktop"
        />
      )}
      </div>
    </div>
  );
}
