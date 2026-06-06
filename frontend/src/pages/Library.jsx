import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  fetchCollections,
  fetchTags,
  startTriage,
  fetchReadingQueue,
  fetchReadingQueueStatus,
  fetchFulltext,
  fetchFulltextStatus,
  syncRelTags,
  syncScoreRanks,
} from '../api/libraryApi.js';
import ReadNextView from '../components/library/ReadNextView.jsx';
import LibraryFilterBar from '../components/library/LibraryFilterBar.jsx';
import ZoteroActionsMenu from '../components/library/ZoteroActionsMenu.jsx';
import { StatusBanner } from '../components/library/shared.jsx';
import {
  EMPTY_FILTERS, buildPredicate, goalHighKeys, isFilterActive,
  serializeFilters, hydrateFilters,
} from '../utils/relevanceBands.js';

// Library page — a single "Read next" surface (Stage 2). The former Browse tab
// and Triage monitor are merged in: the sidebar collection/tag filters + a
// search box scope the ranked queue, and an opt-in "Select" mode sends papers to
// triage. Scoring never runs on open; the Rescore button owns recompute.

// Fetch the WHOLE ranked library in one request (the backend ranks every item);
// ReadNextView reveals it incrementally so the DOM stays light. High cap, not
// truly unbounded, so a pathological library can't build a multi-MB response.
const QUEUE_LIMIT = 5000;

function flattenCollections(nodes, depth = 0) {
  const flat = [];
  for (const node of nodes || []) {
    flat.push({ key: node.key, name: node.name, item_count: node.item_count || 0, depth });
    if (node.children?.length) {
      flat.push(...flattenCollections(node.children, depth + 1));
    }
  }
  return flat;
}

export default function Library() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  // Client-side smart filters (Phase 1) — hydrated from the URL on mount so a
  // filtered view is shareable / survives reload, then mirrored back on change.
  const [clientFilters, setClientFilters] = useState(() => hydrateFilters(searchParams));
  const [queue, setQueue] = useState([]);
  const [queueMeta, setQueueMeta] = useState({
    read_hidden: 0, total_unread: 0, status: 'ready', model_ready: true,
    error: null, computed_at: null, scores_stale: false, distribution: null,
    semantic: false, reranker_loading: false, semantic_unavailable: false,
  });
  const pollRef = useRef(null);
  const [queueLoading, setQueueLoading] = useState(false);
  const [queueErr, setQueueErr] = useState(null);
  const [includeRead, setIncludeRead] = useState(false);
  const [collections, setCollections] = useState([]);
  const [tags, setTags] = useState([]);
  const [selectedCollection, setSelectedCollection] = useState('');
  const [selectedTag, setSelectedTag] = useState('');
  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');
  // 'meaning' = hybrid semantic search (default); 'exact' = substring (legacy).
  const [searchMode, setSearchMode] = useState('meaning');
  const [selectMode, setSelectMode] = useState(false);
  const [selected, setSelected] = useState(() => new Set());
  const [starting, setStarting] = useState(false);
  const [syncingTags, setSyncingTags] = useState(false);
  const [syncingRanks, setSyncingRanks] = useState(false);
  const [fetchingFulltext, setFetchingFulltext] = useState(false);
  const ftPollRef = useRef(null);
  const [message, setMessage] = useState('');
  const [isError, setIsError] = useState(false);

  const flatCollections = useMemo(() => flattenCollections(collections), [collections]);

  // ------ Client-side smart filters (over the already-loaded queue) ------
  // Mirror the active filters into the URL (compact keys, defaults omitted) so a
  // filtered view is a shareable link; `replace` keeps filter clicks out of history.
  // Guard the write: only navigate when our filter params actually changed (a bare
  // setSearchParams every render can loop, since its identity changes with the
  // location it just updated). Non-filter params are preserved.
  useEffect(() => {
    const FILTER_KEYS = ['b', 'pr', 'g', 's', 'w', 'sc'];
    const target = serializeFilters(clientFilters);
    const current = new URLSearchParams();
    for (const [k, v] of searchParams.entries()) {
      if (FILTER_KEYS.includes(k)) current.append(k, v);
    }
    if (current.toString() !== new URLSearchParams(target).toString()) {
      const merged = new URLSearchParams(searchParams);
      FILTER_KEYS.forEach((k) => merged.delete(k));
      Object.entries(target).forEach(([k, v]) => merged.set(k, v));
      setSearchParams(merged, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clientFilters]);

  const filterCtx = useMemo(() => ({
    goalHigh: goalHighKeys(queue),
    prestigeFloor: queueMeta.distribution?.prestige_floor ?? null,
  }), [queue, queueMeta.distribution]);
  const filteredQueue = useMemo(
    () => queue.filter(buildPredicate(clientFilters, filterCtx)),
    [queue, clientFilters, filterCtx],
  );
  const whyOptions = useMemo(
    () => [...new Set(queue.map((i) => i.why_reason).filter(Boolean))].sort(),
    [queue],
  );
  const goalEnabled = useMemo(() => queue.some((i) => typeof i.goal_sim === 'number'), [queue]);
  const filtersActive = isFilterActive(clientFilters);
  const filterSig = useMemo(() => JSON.stringify(serializeFilters(clientFilters)), [clientFilters]);

  const clearClientFilters = useCallback(() => setClientFilters(EMPTY_FILTERS), []);
  const toggleBand = useCallback((band) => setClientFilters((f) => ({
    ...f,
    bands: f.bands.includes(band) ? f.bands.filter((b) => b !== band) : [...f.bands, band],
  })), []);

  // ------ Initial sidebar load ------
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [cData, tData] = await Promise.all([fetchCollections(), fetchTags({ limit: 300 })]);
        if (cancelled) return;
        setCollections(cData?.items || []);
        setTags(tData?.items || []);
      } catch (err) {
        if (!cancelled) {
          setMessage(`Failed to load sidebar: ${err.message || err}`);
          setIsError(true);
        }
      }
    })();
    return () => { cancelled = true; };
  }, []);

  // ------ Read-next queue load (Stage 2) ------
  const loadQueue = useCallback(async (force = false) => {
    setQueueLoading(true);
    setQueueErr(null);
    try {
      const data = await fetchReadingQueue({
        includeRead, limit: QUEUE_LIMIT, refresh: force,
        collection: selectedCollection, tag: selectedTag, search,
        semantic: searchMode === 'meaning',
      });
      setQueue(data?.items || []);
      setQueueMeta({
        read_hidden: data?.read_hidden || 0,
        total_unread: data?.total_unread || 0,
        status: data?.status || 'ready',
        model_ready: data?.model_ready !== false,
        error: data?.error || null,
        computed_at: data?.computed_at || null,
        scores_stale: Boolean(data?.scores_stale),
        distribution: data?.distribution || null,
        semantic: Boolean(data?.semantic),
        reranker_loading: Boolean(data?.reranker_loading),
        semantic_unavailable: Boolean(data?.semantic_unavailable),
      });
      if (pollRef.current) { clearTimeout(pollRef.current); pollRef.current = null; }
      // Background scoring in progress → poll the CHEAP status endpoint (no
      // whole-library Zotero read) and reload ONCE when it finishes. (The old
      // path re-fetched the entire library every 4s — a multi-second read storm.)
      if (data?.status === 'computing') {
        const tick = async () => {
          let running = true;
          try {
            running = Boolean((await fetchReadingQueueStatus())?.running);
          } catch {
            running = true;  // transient — keep waiting, don't false-finish
          }
          if (running) {
            pollRef.current = setTimeout(tick, 8000);
          } else {
            loadQueue(false);  // scoring done → one full reload
          }
        };
        pollRef.current = setTimeout(tick, 8000);
      }
    } catch (err) {
      // Preserve the last-good list on a transient failure (don't blank the page);
      // the banner surfaces the error. err.status drives the message in ReadNextView.
      setQueueErr(err);
    } finally {
      setQueueLoading(false);
    }
  }, [includeRead, selectedCollection, selectedTag, search, searchMode]);

  useEffect(() => {
    loadQueue();
    return () => {
      if (pollRef.current) { clearTimeout(pollRef.current); pollRef.current = null; }
      if (ftPollRef.current) { clearTimeout(ftPollRef.current); ftPollRef.current = null; }
    };
  }, [loadQueue]);

  function selectCollection(key) {
    setSelectedCollection(key);
    setSelected(new Set());
  }

  function selectTag(tag) {
    setSelectedTag(tag);
    setSelected(new Set());
  }

  function applySearch() {
    setSearch(searchInput.trim());
  }

  function clearFilters() {
    setSelectedCollection('');
    setSelectedTag('');
    setSearchInput('');
    setSearch('');
  }

  function toggleItem(key) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  }

  async function handleRunTriage() {
    if (!selected.size) return;
    setStarting(true);
    setMessage('');
    try {
      const data = await startTriage([...selected], { queueChanges: true });
      setMessage(`Triage job ${data?.job_id || 'started'}. Opening Triage Monitor…`);
      setIsError(false);
      navigate('/triage');
    } catch (err) {
      setMessage(`Failed to start triage: ${err.message || err}`);
      setIsError(true);
    } finally {
      setStarting(false);
    }
  }

  async function handleSyncRelTags(force = false) {
    setSyncingTags(true);
    setMessage('');
    try {
      const data = await syncRelTags({ force });
      if (data?.requires_force) {
        if (window.confirm('Zotero appears to be running. Apply anyway? (a backup is taken first)')) {
          return await handleSyncRelTags(true);
        }
        setMessage('Sync cancelled — close Zotero, then retry.');
        setIsError(false);
        return;
      }
      const bands = Object.entries(data?.by_band || {}).map(([b, n]) => `${b}: ${n}`).join(', ');
      setMessage(
        `Tagged ${data?.tagged || 0} item(s)${bands ? ` (${bands})` : ''}.`
        + (data?.backup_path ? ` Backup: ${data.backup_path}.` : '')
        + (data?.message ? ` ${data.message}` : ''),
      );
      setIsError(false);
    } catch (err) {
      setMessage(`Failed to sync relevance tags: ${err.message || err}`);
      setIsError(true);
    } finally {
      setSyncingTags(false);
    }
  }

  async function handleSyncScoreRanks(force = false) {
    setSyncingRanks(true);
    setMessage('');
    try {
      const data = await syncScoreRanks({ force });
      if (data?.requires_force) {
        if (window.confirm('Zotero appears to be running. Apply anyway? (a backup is taken first)')) {
          return await handleSyncScoreRanks(true);
        }
        setMessage('Sync cancelled — close Zotero, then retry.');
        setIsError(false);
        return;
      }
      setMessage(
        `Ranked ${data?.ranked || 0} papers across your whole library into Zotero "Call Number" (zr0001…)`
        + (data?.unscored ? ` — ${data.scored} scored, ${data.unscored} not-yet-scored at the bottom (Rescore for a complete ranking)` : '')
        + '. Add the Call Number column in Zotero and sort it ascending to get this order.'
        + (data?.backup_path ? ` Backup: ${data.backup_path}.` : '')
        + (data?.message ? ` ${data.message}` : ''),
      );
      setIsError(false);
    } catch (err) {
      setMessage(`Failed to sync score ranks: ${err.message || err}`);
      setIsError(true);
    } finally {
      setSyncingRanks(false);
    }
  }

  // ------ Bulk: fetch arXiv full-text PDFs → Zotero (background job) ------
  function pollFulltext() {
    if (ftPollRef.current) { clearTimeout(ftPollRef.current); ftPollRef.current = null; }
    fetchFulltextStatus().then((s) => {
      const p = s?.progress || {};
      if (s?.running) {
        setMessage(`Fetching arXiv full text… ${p.done || 0}/${p.total || 0} downloaded (this can take several minutes).`);
        ftPollRef.current = setTimeout(pollFulltext, 4000);
        return;
      }
      const r = s?.result || {};
      setFetchingFulltext(false);
      if (r.error) { setMessage(`Full-text fetch failed: ${r.error}`); setIsError(true); return; }
      setMessage(
        `Attached ${r.attached || 0} arXiv PDF(s) to Zotero`
        + ` (skipped ${r.skipped_has_pdf || 0} that already had a PDF, ${r.no_arxiv || 0} without an arXiv link, ${r.failed_count || 0} failed).`
        + (r.attached ? ' They upload to zotero.org on the next sync.' : '')
        + (r.backup_path ? ` Backup: ${r.backup_path}.` : ''),
      );
      setIsError(false);
    }).catch(() => { ftPollRef.current = setTimeout(pollFulltext, 6000); });  // transient — keep polling
  }

  async function handleFetchFulltext(force = false) {
    setFetchingFulltext(true);
    setMessage('');
    try {
      const data = await fetchFulltext({ force });
      if (data?.requires_force) {
        setFetchingFulltext(false);
        if (window.confirm('Zotero appears to be running. Fetch + attach anyway? (a backup is taken first)')) {
          return await handleFetchFulltext(true);
        }
        setMessage('Full-text fetch cancelled — close Zotero, then retry.');
        setIsError(false);
        return;
      }
      // 'started' or 'running' → poll the cheap status endpoint for progress.
      setMessage('Fetching arXiv full text… scanning the library.');
      setIsError(false);
      pollFulltext();
    } catch (err) {
      setFetchingFulltext(false);
      setMessage(`Failed to start full-text fetch: ${err.message || err}`);
      setIsError(true);
    }
  }

  return (
    <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
      {/* ----- Sidebar: collections + top tags ----- */}
      <aside className="glass rounded-2xl border border-slate-200 p-3 lg:col-span-1">
        <h2 className="text-xs font-bold uppercase tracking-wider text-slate-500 mb-2">Collections</h2>
        <div className="max-h-72 overflow-auto pr-1 slim-scroll">
          <button
            type="button"
            onClick={() => selectCollection('')}
            className={`w-full text-left text-xs px-2 py-1 rounded mb-1 ${
              selectedCollection === ''
                ? 'bg-teal-600 text-white'
                : 'bg-slate-100 text-slate-700 hover:bg-slate-200'
            }`}
          >
            All items
          </button>
          {flatCollections.map((entry) => (
            <button
              type="button"
              key={entry.key}
              onClick={() => selectCollection(entry.key)}
              style={{ paddingLeft: 8 + entry.depth * 14 }}
              className={`w-full text-left text-xs px-2 py-1 rounded mb-1 ${
                selectedCollection === entry.key
                  ? 'bg-teal-600 text-white'
                  : 'bg-slate-100 text-slate-700 hover:bg-slate-200'
              }`}
            >
              <span>{entry.name}</span>
              <span className="mono opacity-70"> ({entry.item_count})</span>
            </button>
          ))}
        </div>

        <h2 className="text-xs font-bold uppercase tracking-wider text-slate-500 mt-4 mb-2">Top Tags</h2>
        <div className="max-h-64 overflow-auto pr-1 slim-scroll">
          <button
            type="button"
            onClick={() => selectTag('')}
            className={`w-full text-left text-xs px-2 py-1 rounded mb-1 ${
              selectedTag === ''
                ? 'bg-amber-600 text-white'
                : 'bg-amber-50 text-amber-800 hover:bg-amber-100'
            }`}
          >
            All tags
          </button>
          {tags.slice(0, 100).map((tagEntry) => (
            <button
              type="button"
              key={tagEntry.tag}
              onClick={() => selectTag(tagEntry.tag)}
              className={`w-full text-left text-xs px-2 py-1 rounded mb-1 ${
                selectedTag === tagEntry.tag
                  ? 'bg-amber-600 text-white'
                  : 'bg-amber-50 text-amber-800 hover:bg-amber-100'
              }`}
            >
              <span>{tagEntry.tag}</span>
              <span className="mono opacity-70"> ({tagEntry.item_count})</span>
            </button>
          ))}
        </div>
      </aside>

      {/* ----- Main: the single Read-next surface ----- */}
      <div className="glass rounded-2xl border border-slate-200 p-3 lg:col-span-3">
        <div className="flex flex-wrap gap-2 items-center mb-3">
          <input
            type="text"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            onKeyUp={(e) => { if (e.key === 'Enter') applySearch(); }}
            placeholder={searchMode === 'meaning'
              ? 'Search by meaning… (e.g. hallucination mitigation)'
              : 'Search title, abstract, tags'}
            className="flex-1 min-w-0 px-3 py-2 rounded-lg border border-slate-300 text-sm focus:outline-none focus:ring-2 focus:ring-teal-500"
          />
          {/* Meaning (hybrid semantic) vs Exact (substring). Default Meaning so a
              plain query finds relevant papers, not just literal matches. */}
          <div className="inline-flex rounded-lg border border-slate-300 overflow-hidden text-sm shrink-0">
            <button
              type="button"
              onClick={() => setSearchMode('meaning')}
              title="Semantic search — ranks by meaning (BM25 + embeddings + local reranker)"
              className={`px-3 py-2 ${searchMode === 'meaning' ? 'bg-teal-600 text-white' : 'bg-white text-slate-700 hover:bg-slate-50'}`}
            >
              Meaning
            </button>
            <button
              type="button"
              onClick={() => setSearchMode('exact')}
              title="Exact text — substring match on title / abstract / tags"
              className={`px-3 py-2 border-l border-slate-300 ${searchMode === 'exact' ? 'bg-slate-700 text-white' : 'bg-white text-slate-700 hover:bg-slate-50'}`}
            >
              Exact
            </button>
          </div>
          <button
            type="button"
            onClick={applySearch}
            className="px-3 py-2 rounded-lg bg-slate-900 text-white text-sm hover:bg-slate-700"
          >
            Search
          </button>
          {(selectedCollection || selectedTag || search) && (
            <button
              type="button"
              onClick={clearFilters}
              className="px-3 py-2 rounded-lg bg-slate-200 text-slate-700 text-sm hover:bg-slate-300"
            >
              Clear
            </button>
          )}
          {/* Hick's/Miller's Law: the three heavy whole-library Zotero WRITE
              actions live in one grouped disclosure instead of crowding the
              search row. */}
          <ZoteroActionsMenu
            disabled={syncingTags || syncingRanks}
            actions={[
              {
                label: 'Fetch full text', busy: fetchingFulltext, busyLabel: 'Fetching…',
                disabled: fetchingFulltext, onClick: () => handleFetchFulltext(false),
                title: 'Download the arXiv full-text PDF for every library paper that has an arXiv link but no PDF, and attach it natively to Zotero. Skips papers that already have a PDF. Backs up first; runs with Zotero closed; PDFs upload to zotero.org on the next sync.',
              },
              {
                label: 'Sync relevance tags', busy: syncingTags, busyLabel: 'Syncing…',
                onClick: () => handleSyncRelTags(false),
                title: 'Write zs:rel/<band> tags onto scored library items so you can FILTER by ML relevance in Zotero. Backs up first; doesn\'t touch your priority/manual tags.',
              },
              {
                label: 'Sort ranks (Call Number)', busy: syncingRanks, busyLabel: 'Writing…',
                onClick: () => handleSyncScoreRanks(false),
                title: 'Stamp a whole-library rank into every paper\'s Zotero Call Number (zr0001…) — scorable papers first, no-abstract ones last — so you can SORT your ENTIRE library by relevance in Zotero. Run Rescore first for complete scores. Add the Call Number column and sort ascending. Backs up first.',
              },
            ]}
          />
        </div>

        <StatusBanner message={message} isError={isError} />

        {/* Smart client-side filters — only meaningful once the model has scored
            the library (bands/prestige/goal all key off the relevance score). */}
        {queueMeta.model_ready && queue.length > 0 && (
          <LibraryFilterBar
            filters={clientFilters}
            onChange={setClientFilters}
            whyOptions={whyOptions}
            goalEnabled={goalEnabled}
            rawCount={queue.length}
            shownCount={filteredQueue.length}
            onClear={clearClientFilters}
          />
        )}

        <ReadNextView
          // Remount on a deliberate SERVER filter change so the incremental-reveal
          // count and any expanded row reset — but NOT on the 4s status poll, and
          // NOT on a client filter (that resets the reveal via filterSig instead).
          key={`${selectedCollection}|${selectedTag}|${search}|${searchMode}|${includeRead}`}
          // Client filters only apply while the bar is shown (model ready); when
          // the model isn't ready the bar is hidden, so don't silently filter.
          items={queueMeta.model_ready ? filteredQueue : queue}
          loading={queueLoading}
          err={queueErr}
          includeRead={includeRead}
          onToggleIncludeRead={() => setIncludeRead((v) => !v)}
          readHidden={queueMeta.read_hidden}
          totalUnread={queueMeta.total_unread}
          status={queueMeta.status}
          modelReady={queueMeta.model_ready}
          error={queueMeta.error}
          computedAt={queueMeta.computed_at}
          scoresStale={queueMeta.scores_stale}
          distribution={queueMeta.distribution}
          onRescore={() => loadQueue(true)}
          onReload={() => loadQueue(false)}
          onSaved={() => loadQueue()}
          selectMode={selectMode}
          onToggleSelectMode={() => { setSelectMode((v) => !v); setSelected(new Set()); }}
          selected={selected}
          onToggleItem={toggleItem}
          onRunTriage={handleRunTriage}
          starting={starting}
          rawCount={queue.length}
          hasActiveFilters={queueMeta.model_ready && filtersActive}
          onClearClientFilters={clearClientFilters}
          activeBands={clientFilters.bands}
          onBandClick={toggleBand}
          filterSig={filterSig}
          semantic={queueMeta.semantic}
          searchQuery={search}
          rerankerLoading={queueMeta.reranker_loading}
          semanticUnavailable={queueMeta.semantic_unavailable}
          prestigeFloor={filterCtx.prestigeFloor}
        />
      </div>
    </div>
  );
}
