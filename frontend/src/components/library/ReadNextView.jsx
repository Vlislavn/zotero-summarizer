import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import LibraryDetail from './LibraryDetail.jsx';
import BulkVerdictBar from './BulkVerdictBar.jsx';
import ReadNextRow from './ReadNextRow.jsx';
import UndoToast from './UndoToast.jsx';
import useLibraryVerdict, { nextUndecided } from '../../hooks/useLibraryVerdict.js';
import useKeyboardNav from '../../hooks/useKeyboardNav.js';
import { DECISIONS } from '../decisionVocab.js';
import { groupRows, presetsOffReason } from './readNextCopy.js';
import { CustomPanel, OrderCaption, OrderControl } from './OrderControls.jsx';
import { WhyHerePanel } from './WhyHere.jsx';
import { BALANCED_ORDER } from '../../utils/readNextOrder.js';
import { Disclosure } from '../paper/review/primitives.jsx';
import { DEFAULT_SORT, goalHighKeys } from '../../utils/relevanceBands.js';
import Skeleton from '../ui/Skeleton.jsx';
import { readStorage } from '../../utils/safeStorage.js';

// Stage-2 "Read next": the single Library surface. Ranked queue over the WHOLE
// library; a tap opens the paper's detail (LibraryDetail: a Sheet on phones, the right half
// on wide screens) over the still-mounted list. The open paper is `openState`
// (hooks/useOpenPaper, owned by the page so it survives a server-filter remount).
// Read/handled items are hidden unless toggled. An opt-in "Select" mode reveals
// checkboxes for bulk triage (the merged Browse/triage flow), kept secondary.
const REVEAL_STEP = 60;  // rows revealed initially and per "Show more" click
const MUST_PREVIEW = 3;  // your own Must read rows shown before "Show all" (2 on a phone: the first undecided paper stays near the top)
const MUST_PREVIEW_PHONE = 2;
const DESKTOP_QUERY = '(min-width: 1024px)';
const PHONE_QUERY = '(max-width: 639px)';  // Tailwind's `sm` starts at 640
// Keys (F-final-plan §3.4): 1–4 are the pills left to right; the rows and the pills keep the keys after a click focused them.
const KEY_PRIORITY = Object.fromEntries(DECISIONS.map(([priority], i) => [i + 1, priority]));
const KEYS_FROM = '[data-row-key] button, [data-verdict-bar] button';
const FOLLOW_MS = 250;  // the open paper follows j / k after this much calm: one review-detail read, not one per press
const KEY_HINT = 'j / k move · 1–4 verdict · z undo · Enter open · Esc close · / search';

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
const FLASH_MS = 2000;  // the row you came back from stays highlighted this long

export default function ReadNextView({
  items, loading, err,
  queueItems = items, onSaved, modelReady,
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
  // where the presets can act (no search, parts in the response); Library owns `order` and,
  // while no Zotero field is chosen, has already sorted `items` by it.
  order = BALANCED_ORDER, onOrderChange, orderMeta = null,
  // True while the list is the whole library (no collection / tag scope): only then is
  // «Goals strong» (top third of goal_sim in the loaded list) the same word for a paper everywhere.
  cohortFull = false,
  // The open paper: {key, open(key), close(), position, total, prev, next} from useOpenPaper.
  openState = NO_OPEN,
  // A verdict patches its row in place (the page owns the rows): onRowsChange([[key, fields]…]), onWrite(promise).
  onRowsChange = () => {}, onWrite = () => {},
  // `/` puts the cursor in the page's search box.
  onFocusSearch = null,
}) {
  const openKey = openState.key;
  const [notesDirty, setNotesDirty] = useState(false);
  const [leave, setLeave] = useState(null);  // the exit waiting for «Leave without saving?»
  const [returnKey, setReturnKey] = useState(null);  // row to bring back into view after the detail closes
  const [flashKey, setFlashKey] = useState(null);
  const [mustAll, setMustAll] = useState(false);  // «Show all N» of your own Must read (this visit)
  const [decidedOpen, setDecidedOpen] = useState(false);  // the «Decided by you» block
  const orderOn = Boolean(orderMeta && onOrderChange && sort.field === 'best');  // a Zotero field replaces the preset
  const listRef = useRef(null);
  const isDesktop = useMediaQuery(DESKTOP_QUERY);
  const phone = useMediaQuery(PHONE_QUERY);
  const mustPreview = phone ? MUST_PREVIEW_PHONE : MUST_PREVIEW;
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
  useEffect(() => { setNotesDirty(false); setLeave(null); clearTimeout(followTimer.current); }, [openKey]);
  const surface = isDesktop ? 'library_split' : 'library_sheet';
  const flow = useLibraryVerdict({ queueItems, items, openState, notesDirty, guarded, surface, onRowsChange, onWrite });
  // The toast rides inside the phone Sheet (it silences the page behind it); otherwise it sticks above the list.
  const toast = flow.toast && (
    <UndoToast toast={flow.toast} onUndo={flow.undo} onReason={flow.toggleReason} onDismiss={flow.dismiss} />
  );
  const priorOf = (key) => {
    const row = queueItems.find((it) => it.item_key === key);
    return row?.user_priority ? { user_priority: row.user_priority, comment: '' } : null;
  };
  const toastInSheet = Boolean(openKey && !isDesktop);

  // Keys act on `selKey`: the open paper, else the row last moved to with j / k. With a paper open j / k
  // move the outline at once and the paper follows FOLLOW_MS later; 1–4 wait until it has (they would hit the old one).
  const [selKey, setSelKey] = useState(null);
  const decideRef = useRef(null);  // VerdictBar's gate: the same quiet period and readiness as a pill tap
  const followTimer = useRef(null);
  const moved = useRef(false);  // the selection came from a key: bring its row into view
  const rowNodes = () => [...(listRef.current?.querySelectorAll('[data-row-key]') || [])];
  useEffect(() => { if (openKey) setSelKey(openKey); }, [openKey]);
  useEffect(() => () => clearTimeout(followTimer.current), []);
  useEffect(() => {
    if (!moved.current) return;
    moved.current = false;
    const row = rowNodes().find((n) => n.dataset.rowKey === selKey);
    row?.scrollIntoView?.({ block: 'nearest' });
    if (!openKey || isDesktop) row?.querySelector('button')?.focus({ preventScroll: true });  // a phone's Sheet makes the list inert
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selKey]);
  function select(key) {
    moved.current = true;
    setSelKey(key);
  }
  function moveSelection(delta) {
    const keys = rowNodes().map((n) => n.dataset.rowKey);
    if (!keys.length) return;
    const next = keys[Math.min(Math.max(keys.indexOf(selKey) + delta, 0), keys.length - 1)];
    select(next);
    clearTimeout(followTimer.current);
    if (openKey && next !== openKey) followTimer.current = setTimeout(() => guarded(() => openState.open(next)), FOLLOW_MS);
  }
  function decideByKey(priority) {
    if (openKey) {
      if (selKey === openKey) decideRef.current?.(priority);
      return;
    }
    flow.verdict(selKey, priority, { prior: priorOf(selKey) });
    const next = nextUndecided(items, selKey);
    if (next) select(next);
  }
  useKeyboardNav({
    onPrev: () => moveSelection(-1),
    onNext: () => moveSelection(1),
    onAction: decideByKey,
    actionKeys: KEY_PRIORITY,
    hasSelection: Boolean(selKey) && !selectMode,
    onEnter: selKey && selKey !== openKey ? () => toggleRef.current(selKey) : undefined,
    onEscape: openKey ? () => guarded(openState.close) : undefined,
    onUndo: flow.toast?.kind === 'saved' ? flow.undo : undefined,
    onSlash: onFocusSearch && (!openKey || isDesktop) ? onFocusSearch : undefined,
    allowFrom: KEYS_FROM,
    allowModal: '[data-testid="library-detail"]',  // the phone detail Sheet; any other modal (a confirmation) silences the list
  });

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
  // When the detail closes, the paper's row must be on screen: make sure it is rendered (the list
  // reveals in chunks and folds groups), then scroll to it, focus it and mark it for a moment.
  const lastOpen = useRef(openKey);
  useEffect(() => {
    const was = lastOpen.current;
    lastOpen.current = openKey;
    if (!was || openKey) return;
    const inMust = groups ? groups.must.findIndex((it) => it.item_key === was) : -1;
    const inTodo = (groups ? groups.todo : items).findIndex((it) => it.item_key === was);
    if (inMust >= mustPreview) setMustAll(true);
    if (inTodo >= 0) setVisibleCount((n) => Math.max(n, inTodo + 1));
    if (groups?.decided.some((it) => it.item_key === was)) setDecidedOpen(true);
    setReturnKey(was);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openKey]);
  useEffect(() => {
    if (!returnKey) return;
    const row = rowNodes().find((n) => n.dataset.rowKey === returnKey);
    setReturnKey(null);
    if (!row) return;
    const { top, bottom } = row.getBoundingClientRect();
    if (top < 0 || bottom > window.innerHeight) row.scrollIntoView?.({ block: 'center' });
    row.querySelector('button')?.focus({ preventScroll: true });
    setFlashKey(returnKey);
  }, [returnKey]);
  useEffect(() => {
    if (!flashKey) return undefined;
    const timer = setTimeout(() => setFlashKey(null), FLASH_MS);
    return () => clearTimeout(timer);
  }, [flashKey]);
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
      <li
        key={it.item_key}
        data-row-key={it.item_key}
        aria-current={selKey === it.item_key || undefined}
        className={`rounded-xl ${selKey === it.item_key ? 'outline outline-2 outline-teal-600' : ''} ${flashKey === it.item_key ? 'ring-2 ring-teal-300' : ''}`}
      >
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
          {/* ONE Order list: presets + Zotero columns (Jakob's Law: Zotero's own field names). */}
          {onSortChange && onOrderChange && (
            <OrderControl
              order={order}
              meta={orderMeta}
              sort={sort}
              reason={presetsOffReason({ offline, searching: Boolean(searchQuery.trim()), modelReady, hasMeta: Boolean(orderMeta) })}
              onOrderChange={onOrderChange}
              onSortChange={onSortChange}
              caption={phone && orderOn && sort.field === 'best' ? <OrderCaption order={order} meta={orderMeta} /> : null}
            />
          )}
          {sort.field === 'best' && (
            offline ? <span>· cached working set order</span> : semantic
              ? (
                // The backend caps meaning search at 50 rows; say so, and that filters cut within them.
                // While the reranker downloads the order is the first-pass fusion: said here, next to the results.
                <span data-testid="search-caption">
                  {rawCount >= SEARCH_TOP_N
                    ? `· Top ${SEARCH_TOP_N} by meaning for “${searchQuery}”${hasActiveFilters ? ` · ${items.length} of ${SEARCH_TOP_N} match filters` : ''}`
                    : `· ranked by similarity to “${searchQuery}”`}
                  {rerankerLoading && ' · quick mode while the ranking model downloads (first search only)'}
                </span>
              )
              : searchQuery.trim() && semanticUnavailable
                ? <span data-testid="search-caption">· exact text matches for “{searchQuery}” — meaning search is off</span>
              : (modelReady
                ? orderOn ? (phone ? null : <OrderCaption order={order} meta={orderMeta} />) : (
                  <span title="Best first: your own Must read papers, then model relevance + goal match + author/venue and quality evidence. Review suggestions do not affect this order.">
                    · relevance + goals + evidence
                  </span>
                )
                : <span>· ranked by recency (model not ready yet)</span>)
          )}
        </div>
        <div className="flex flex-wrap items-center gap-3">
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
            <BulkVerdictBar keys={[...(selected || [])]} disabled={!serverAvailable} onDone={onBulkDone} onVerdicts={flow.batchVerdict} priorOf={priorOf} />
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
        </div>
      </div>
      {orderOn && order.preset === 'custom' && (
        <CustomPanel order={order} meta={orderMeta} onChange={onOrderChange} />
      )}
      {loading && items.length === 0 && (
        <div role="status" aria-busy="true"><span className="sr-only">Loading your reading list…</span><Skeleton count={3} height="h-16" /></div>
      )}
      {!toastInSheet && toast && <div className="sticky top-2 z-20 mb-2">{toast}</div>}
      <div ref={listRef} className={openKey && isDesktop ? 'lg:flex lg:gap-3 lg:items-start' : ''}>
      <div className={openKey && isDesktop ? 'lg:flex-1 lg:min-w-0' : ''}>
        {groups && groups.must.length > 0 && (
          <section aria-label="Your must read">
            <GroupHeader
              action={groups.must.length > mustPreview && (
                <GroupAction expanded={mustAll} onClick={() => setMustAll((v) => !v)}>
                  {mustAll ? 'Show fewer' : `Show all${offline ? '' : ` ${groups.must.length}`}`}
                </GroupAction>
              )}
            >
              Your must read{count(groups.must.length)}
            </GroupHeader>
            {rows(mustAll ? groups.must : groups.must.slice(0, mustPreview), false)}
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
        {isDesktop && shown.length > 0 && (
          <p data-testid="key-hint" className="sticky bottom-0 z-10 mt-3 bg-white py-1.5 text-[11px] text-slate-500">{KEY_HINT}</p>
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
          onStay={() => { setLeave(null); setSelKey(openKey); }}
          onLeave={leaveNow}
          onSaved={saveOpen}
          onQueueRefresh={refreshOpen}
          onNotesDirtyChange={setNotesDirty}
          why={whyHere(detailItem)}
          goalsStrong={goalHigh.has(openKey)}
          modelReady={modelReady}
          surface={surface}
          onVerdict={(priority, prior) => flow.verdict(openKey, priority, { prior })}
          caughtUp={flow.caughtUp}
          decideRef={decideRef}
          toast={toastInSheet ? toast : null}
        />
      )}
      </div>
    </div>
  );
}
