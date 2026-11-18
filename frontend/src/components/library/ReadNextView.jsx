import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import LibraryDetail from './LibraryDetail.jsx';
import BulkVerdictBar from './BulkVerdictBar.jsx';
import ReadNextRow from './ReadNextRow.jsx';
import { StatusBanner, formatShortDate } from './shared.jsx';
import { groupRows, scoreStatus } from './readNextCopy.js';
import { CustomPanel, OrderCaption, OrderSelect } from './OrderControls.jsx';
import { WhyHerePanel } from './WhyHere.jsx';
import { BALANCED_ORDER } from '../../utils/readNextOrder.js';
import { Disclosure } from '../paper/review/primitives.jsx';
import { humanizeError } from '../../utils/humanizeError.js';
import { SORT_FIELDS, SORT_DEFAULT_DIR, DEFAULT_SORT, goalHighKeys } from '../../utils/relevanceBands.js';
import Spinner from '../ui/Spinner.jsx';
import { readStorage } from '../../utils/safeStorage.js';

// Stage-2 "Read next": the single Library surface. Ranked queue over the WHOLE
// library; a tap opens the paper's detail (LibraryDetail: a Sheet on phones, the right half
// on wide screens) over the still-mounted list. The open paper is `openState`
// (hooks/useOpenPaper, owned by the page so it survives a server-filter remount).
// Read/handled items are hidden unless toggled. An opt-in "Select" mode reveals
// checkboxes for bulk triage (the merged Browse/triage flow), kept secondary.
const REVEAL_STEP = 60;  // rows revealed initially and per "Show more" click
const MUST_PREVIEW = 3;  // your own Must read rows shown before "Show all"
const DESKTOP_QUERY = '(min-width: 1024px)';

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

// Backend cap of hybrid meaning search (services/library/_search.py _TOP_N).
const SEARCH_TOP_N = 50;
const NO_OPEN = { key: null, open() {}, close() {}, position: null, total: 0, prev: null, next: null };

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
  // True while the list is the whole library (no collection / tag scope): only then is
  // «Goals strong» (top third of goal_sim in the loaded list) the same word for a paper everywhere.
  cohortFull = false,
  // The open paper: {key, open(key), close(), position, total, prev, next} from useOpenPaper.
  openState = NO_OPEN,
}) {
  const computing = status === 'computing';
  // A failed read must leave Rescore available even with retained last-good rows.
  const errored = status === 'error';
  const rescoreActionable =
    computing || errored || err != null || scoresStale || !computedAt
    || items.some((it) => typeof it.relevance_score !== 'number');
  const openKey = openState.key;
  const [notesDirty, setNotesDirty] = useState(false);
  const [leave, setLeave] = useState(null);  // the exit waiting for «Leave without saving?»
  const [mustAll, setMustAll] = useState(false);  // «Show all N» of your own Must read (this visit)
  const [decidedOpen, setDecidedOpen] = useState(false);  // the «Decided by you» block
  const orderOn = Boolean(orderMeta && onOrderChange);
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
  // Every exit from the open paper (close, ‹ ›, another row, Select) goes through here: an
  // unsaved note turns it into a question inside the detail instead of losing the note.
  function guarded(action) {
    if (notesDirty) setLeave(() => action); else action();
  }
  function leaveNow() {
    const action = leave;
    setLeave(null);
    setNotesDirty(false);
    action?.();
  }
  useEffect(() => { setNotesDirty(false); setLeave(null); }, [openKey]);

  // Rows are memoised: they get one stable callback that reads the latest state. A tap opens
  // that paper's detail (the open one closes it); in Select mode it ticks the row instead.
  const toggleRef = useRef(null);
  const toggleRow = useCallback((key) => toggleRef.current(key), []);
  useEffect(() => {
    toggleRef.current = (key) => {
      if (selectMode) onToggleItem?.(key);
      else guarded(() => (key === openKey ? openState.close() : openState.open(key)));
    };
  });
  const goalHigh = useMemo(
    () => (cohortFull && modelReady && !offline && !searchQuery.trim() ? goalHighKeys(queueItems) : new Set()),
    [cohortFull, modelReady, offline, searchQuery, queueItems],
  );

  function saveOpen() {
    if (notesDirty) return;
    openState.close();
    onSaved?.();
  }

  function refreshOpen() {
    if (!notesDirty) onSaved?.();
  }

  // «Why it's here» lives inside the open paper (Order presets only).
  function whyHere(it) {
    if (!orderOn || !it?.rank_parts) return null;
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

  const detailItem = useMemo(
    () => (openKey ? queueItems.find((it) => it.item_key === openKey) || null : null),
    [openKey, queueItems],
  );
  const rows = (list, ownChip) => (
    <ol className="space-y-2">{list.map((it) => renderRow(it, ownChip))}</ol>
  );

  function renderRow(it, ownChip) {
    return (
      <li key={it.item_key}>
        <ReadNextRow
          item={it}
          expanded={openKey === it.item_key}
          ownChip={ownChip}
          goalsStrong={goalHigh.has(it.item_key)}
          modelReady={modelReady}
          selectMode={selectMode}
          checked={selectMode && (selected?.has(it.item_key) || false)}
          onToggleOpen={toggleRow}
          onToggleSelect={onToggleItem}
        />
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
              ? (
                // The backend caps meaning search at 50 rows; say so, and that filters cut within them.
                <span data-testid="search-caption">
                  {rawCount >= SEARCH_TOP_N
                    ? `· Top ${SEARCH_TOP_N} by meaning for “${searchQuery}”${hasActiveFilters ? ` · ${items.length} of ${SEARCH_TOP_N} match filters` : ''}`
                    : `· ranked by similarity to “${searchQuery}”`}
                </span>
              )
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
            onClick={() => (openKey && !selectMode ? guarded(() => { openState.close(); onToggleSelectMode(); }) : onToggleSelectMode())}
            className={`px-2 py-0.5 rounded-md text-[11px] font-medium border ${
              selectMode ? 'bg-teal-600 text-white border-teal-600' : 'border-slate-300 text-slate-700 hover:bg-slate-50'
            }`}
            title="Select multiple papers for a bulk action: add them all to a Zotero collection, or send to triage. (To file a single paper, just open it.)"
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
      <div className={openKey && isDesktop ? 'lg:flex lg:gap-3 lg:items-start' : ''}>
      <div className={openKey && isDesktop ? 'lg:flex-1 lg:min-w-0' : ''}>
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
      {openKey && (
        <LibraryDetail
          itemKey={openKey}
          item={detailItem}
          sheet={!isDesktop}
          inList={loading || Boolean(err) || items.some((it) => it.item_key === openKey)}
          nav={openState}
          collections={collections}
          onClose={() => guarded(openState.close)}
          onStep={(key) => guarded(() => openState.open(key))}
          leave={leave}
          onStay={() => setLeave(null)}
          onLeave={leaveNow}
          onSaved={saveOpen}
          onQueueRefresh={refreshOpen}
          onNotesDirtyChange={setNotesDirty}
          why={whyHere(detailItem)}
        />
      )}
      </div>
    </div>
  );
}
