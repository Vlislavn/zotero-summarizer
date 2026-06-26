import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  fetchCollections,
  fetchTags,
  startTriage,
  fetchReadingQueue,
  fetchReadingQueueStatus,
  addItemToCollection,
  fetchFulltext,
  fetchFulltextStatus,
  syncRelTags,
  syncScoreRanks,
} from '../api/libraryApi.js';
import ReadNextView from '../components/library/ReadNextView.jsx';
import LibraryFilterBar from '../components/library/LibraryFilterBar.jsx';
import PredictionsBar from '../components/library/PredictionsBar.jsx';
import NotConfiguredCard from '../components/setup/NotConfiguredCard.jsx';
import { useSetupStatus } from '../hooks/useSetupStatus.js';
import { useReviewCoolLoop } from '../hooks/useReviewCoolLoop.js';
import ZoteroActionsMenu from '../components/library/ZoteroActionsMenu.jsx';
import { StatusBanner, formatShortDate } from '../components/library/shared.jsx';
import { Section } from '../components/paper/review/primitives.jsx';
import {
  EMPTY_FILTERS, buildPredicate, goalHighKeys, isFilterActive,
  serializeFilters, hydrateFilters,
} from '../utils/relevanceBands.js';
import { isMachineTag } from '../utils/tags.js';

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

export default function LibraryReadNext() {
  const navigate = useNavigate();
  // Only hit the Zotero-backed sidebar/queue endpoints once we KNOW the reader is
  // up. When Zotero isn't connected those calls 500/503; gating on db_found keeps
  // a first-run user on the clean "finish setup" card instead of a wall of errors.
  const { status } = useSetupStatus();
  const zoteroReady = status?.zotero?.db_found === true;
  const zoteroKnownMissing = Boolean(status) && !status?.zotero?.db_found;
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
  // "Browse & filter" drawer (collections + tags + smart filters). Default
  // COLLAPSED everywhere so the ranked queue — the task — owns the fold (Serial
  // Position); the collapsed summary still shows the active scope, so nothing is
  // hidden. Power-users who open it are remembered (localStorage).
  const [browseOpen, setBrowseOpen] = useState(() => {
    const saved = typeof localStorage !== 'undefined' ? localStorage.getItem('zs:libraryBrowseOpen') : null;
    return saved === '1';
  });
  function toggleBrowse(open) {
    setBrowseOpen(open);
    try { localStorage.setItem('zs:libraryBrowseOpen', open ? '1' : '0'); } catch { /* storage unavailable — keep in-memory */ }
  }
  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');
  const [selectMode, setSelectMode] = useState(false);
  const [selected, setSelected] = useState(() => new Set());
  const [starting, setStarting] = useState(false);
  const [addingToCollection, setAddingToCollection] = useState(false);
  const [syncingAll, setSyncingAll] = useState(false);
  // When each Zotero export last ran (tags / Call-Number ranks) — local-only
  // convenience so "did I already sync after that retrain?" never depends on
  // the user's memory. Recorded only on a run that actually synced (or
  // confirmed everything up to date), never on a stale/cancelled attempt.
  const [zoteroSyncedAt, setZoteroSyncedAt] = useState(() => ({
    tags: localStorage.getItem('zs:lastTagSyncAt') || '',
    ranks: localStorage.getItem('zs:lastRankSyncAt') || '',
  }));

  function recordZoteroSync(kind) {
    const now = new Date().toISOString();
    localStorage.setItem(kind === 'tags' ? 'zs:lastTagSyncAt' : 'zs:lastRankSyncAt', now);
    setZoteroSyncedAt((prev) => ({ ...prev, [kind]: now }));
  }
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
    const FILTER_KEYS = ['b', 'pr', 'g', 'w', 'q'];
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
  const qualityEnabled = useMemo(() => queue.some((i) => i.quality_grade), [queue]);
  // Proposals already on the rows (e.g. from the startup prewarm) — lets the
  // predictions bar say "N ready below" instead of implying nothing happened.
  const proposedCount = useMemo(() => queue.filter((i) => i.proposed_verdict).length, [queue]);
  const filtersActive = isFilterActive(clientFilters);
  const filterSig = useMemo(() => JSON.stringify(serializeFilters(clientFilters)), [clientFilters]);

  const clearClientFilters = useCallback(() => setClientFilters(EMPTY_FILTERS), []);
  const toggleBand = useCallback((band) => setClientFilters((f) => ({
    ...f,
    bands: f.bands.includes(band) ? f.bands.filter((b) => b !== band) : [...f.bands, band],
  })), []);

  // ------ Initial sidebar load ------
  useEffect(() => {
    if (!zoteroReady) return undefined;
    let cancelled = false;
    (async () => {
      try {
        const [cData, tData] = await Promise.all([fetchCollections(), fetchTags({ limit: 300 })]);
        if (cancelled) return;
        setCollections(cData?.items || []);
        // Drop the app's own machine tags from the human-facing "Top Tags"
        // browse filter (see utils/tags.js) — the biggest "tags" are internal
        // relevance/feed bookkeeping no one browses by.
        setTags((tData?.items || []).filter((t) => !isMachineTag(t.tag)));
      } catch (err) {
        if (!cancelled) {
          setMessage(`Failed to load sidebar: ${err.message || err}`);
          setIsError(true);
        }
      }
    })();
    return () => { cancelled = true; };
  }, [zoteroReady]);

  // ------ Read-next queue load (Stage 2) ------
  // Shared query + state-mapping (used by loadQueue AND the auto-review loop's
  // streaming reloads), so both paths render identical meta. Search is always
  // semantic (hybrid BM25 + embeddings + local reranker); the backend degrades to
  // literal text match when the corpus is off.
  const queueArgs = useCallback((force = false) => ({
    includeRead, limit: QUEUE_LIMIT, refresh: force,
    collection: selectedCollection, tag: selectedTag, search, semantic: true,
  }), [includeRead, selectedCollection, selectedTag, search]);

  const applyQueueData = useCallback((data) => {
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
  }, []);

  const loadQueue = useCallback(async (force = false) => {
    setQueueLoading(true);
    setQueueErr(null);
    try {
      const data = await fetchReadingQueue(queueArgs(force));
      applyQueueData(data);
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
  }, [queueArgs, applyQueueData]);

  useEffect(() => {
    if (zoteroReady) loadQueue();
    return () => {
      if (pollRef.current) { clearTimeout(pollRef.current); pollRef.current = null; }
      if (ftPollRef.current) { clearTimeout(ftPollRef.current); ftPollRef.current = null; }
    };
  }, [loadQueue, zoteroReady]);

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

  // Bulk "Add to collection": send every selected (e.g. Meaning-search) result
  // into a Zotero collection without leaving the app — the old flow forced the
  // user to MEMORIZE titles and re-find them inside Zotero (Working Memory).
  // Per-item POSTs to the existing backup-first, connector-guarded write route;
  // one force-confirm covers the whole batch. Fails loud on the first error
  // (reports how many landed), never silently skips.
  async function handleAddToCollection(collectionKey, force = false) {
    if (!selected.size || !collectionKey) return;
    const name = flatCollections.find((c) => c.key === collectionKey)?.name || collectionKey;
    setAddingToCollection(true);
    setMessage('');
    let added = 0;
    try {
      for (const key of selected) {
        const data = await addItemToCollection(key, { collectionKey, force });
        if (data?.requires_force) {
          setAddingToCollection(false);
          if (window.confirm('Zotero appears to be running. Add anyway? (a backup is taken first)')) {
            return await handleAddToCollection(collectionKey, true);
          }
          setMessage(`Cancelled — ${added} of ${selected.size} added to “${name}”. Close Zotero, then retry.`);
          setIsError(false);
          return;
        }
        added += 1;
      }
      localStorage.setItem('zs:lastCollectionKey', collectionKey);
      setMessage(`Added ${added} paper${added === 1 ? '' : 's'} to “${name}” in Zotero.`);
      setIsError(false);
      setSelected(new Set());
      setSelectMode(false);
    } catch (err) {
      setMessage(`Add to “${name}” failed after ${added} of ${selected.size}: ${err.message || err}`);
      setIsError(true);
    } finally {
      setAddingToCollection(false);
    }
  }

  async function handleRunTriage() {
    if (!selected.size) return;
    setStarting(true);
    setMessage('');
    try {
      const data = await startTriage([...selected], { queueChanges: true });
      setMessage(`Triage job ${data?.job_id || 'started'}. Opening Triage Monitor…`);
      setIsError(false);
      navigate('/ops?tab=triage');
    } catch (err) {
      setMessage(`Failed to start triage: ${err.message || err}`);
      setIsError(true);
    } finally {
      setStarting(false);
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

  // ------ "Review cool papers" auto-review loop ------
  // The fleet orchestration — pin the cool keys, dedup via an attempted-ledger, drain
  // a foreign prewarm, settle a Stop honestly, resume a running fleet on mount — lives
  // in a unit-tested hook (`useReviewCoolLoop`), so this page only wires its queue in.
  const {
    fleetStatus, autoReview, coolUndecided, handleReviewCool, stopReviewCool,
  } = useReviewCoolLoop({
    queue, queueArgs, applyQueueData, loadQueue, zoteroReady, setMessage, setIsError,
  });

  // One-click Zotero export chain (Tesler: the system owns the rescore→tags→
  // ranks sequence the user previously had to remember and order correctly):
  // 1. rescore the library IF the cached scores predate the current model (or
  //    were never computed), waiting on the cheap status poll;
  // 2. write zs:rel/<band> tags;  3. stamp Call-Number ranks.
  // Staleness is re-checked from the SERVER at every entry (not the closure),
  // so the force-retry recursion never re-runs a rescore that just finished.
  async function handleSyncAll(force = false) {
    setSyncingAll(true);
    setMessage('');
    try {
      const meta = await fetchReadingQueue({ limit: 1, refresh: false });
      if (meta?.scores_stale || !meta?.computed_at) {
        setMessage('Step 1/3 — rescoring the library against the current model (a few minutes; progress streams in)…');
        await fetchReadingQueue({ limit: 1, refresh: true });
        let running = true;
        for (let i = 0; running && i < 120; i += 1) {  // bail after ~16 min
          await new Promise((r) => setTimeout(r, 8000));
          running = Boolean((await fetchReadingQueueStatus())?.running);
        }
        if (running) throw new Error('rescore did not finish — check the server log, then retry');
      }
      setMessage('Step 2/3 — writing relevance tags to Zotero…');
      const tags = await syncRelTags({ force });
      if (tags?.requires_force) {
        setSyncingAll(false);
        if (window.confirm('Zotero appears to be running. Sync anyway? (a backup is taken first)')) {
          return await handleSyncAll(true);
        }
        setMessage('Sync cancelled — close Zotero, then retry.');
        setIsError(false);
        return;
      }
      if (tags?.stale) { setMessage(tags.message); setIsError(false); return; }  // raced a retrain
      recordZoteroSync('tags');
      setMessage('Step 3/3 — stamping Call-Number ranks…');
      const ranks = await syncScoreRanks({ force });
      if (ranks?.requires_force) {
        // Zotero opened BETWEEN the steps; the tag step is idempotent, so a
        // forced rerun of the whole chain is safe and keeps the code one path.
        setSyncingAll(false);
        if (window.confirm('Zotero opened mid-sync. Finish anyway? (a backup is taken first)')) {
          return await handleSyncAll(true);
        }
        setMessage('Tags synced; rank stamping cancelled — close Zotero, then run “Sync all” again.');
        setIsError(false);
        return;
      }
      if (ranks?.stale) { setMessage(ranks.message); setIsError(false); return; }
      recordZoteroSync('ranks');
      setMessage(
        `Zotero is up to date — ${tags?.tagged || 0} relevance tag(s) refreshed, `
        + `${ranks?.ranked || 0} papers ranked into Call Number. `
        + 'In Zotero, sort the Call Number column ascending to read best-first.',
      );
      setIsError(false);
      loadQueue(false);
    } catch (err) {
      setMessage(`Sync-all failed: ${err.message || err}`);
      setIsError(true);
    } finally {
      setSyncingAll(false);
    }
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

  // Zotero not connected → show ONLY the friendly setup card. The data-backed
  // sidebar/queue (and their errors) would just be noise behind it.
  if (zoteroKnownMissing) {
    return (
      <div className="max-w-3xl mx-auto">
        <NotConfiguredCard />
      </div>
    );
  }

  // Active scope shown in the collapsed drawer summary so folding it never hides
  // what's applied (Working Memory). Collection name (not key), tag, filters-on.
  const browseScope = [
    flatCollections.find((c) => c.key === selectedCollection)?.name,
    selectedTag && `# ${selectedTag}`,
    filtersActive && 'filters on',
  ].filter(Boolean);

  return (
    <div>
      {/* ----- Single full-width Read-next surface (Stage 2). Collections, tags,
          and smart filters fold into ONE "Browse & filter" drawer so the ranked
          queue + decision card own the whole width. Three labelled regions keep
          read-only FIND, suggestion-making REVIEW QUEUE, and whole-library WRITE
          EXPORT distinct; one outer border, hairline rhythm between. ----- */}
      <div className="glass rounded-2xl border border-slate-200 p-3 divide-y divide-slate-200/60">
        {/* Region A — FIND: read-only, instant scoping (search + browse drawer). */}
        <Section label="Find">
          <div className="flex flex-wrap gap-2 items-center">
            <input
              type="text"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              onKeyUp={(e) => { if (e.key === 'Enter') applySearch(); }}
              placeholder="Search by meaning… (e.g. hallucination mitigation)"
              className="flex-1 min-w-0 px-3 py-2 rounded-lg border border-slate-300 text-sm focus:outline-none focus:ring-2 focus:ring-teal-500"
            />
            {/* Search is semantic by default (hybrid); Enter submits — no Meaning/
                Exact toggle, no separate Search button (the input IS the control). */}
            {(selectedCollection || selectedTag || search) && (
              <button
                type="button"
                onClick={clearFilters}
                className="px-3 py-2 rounded-lg bg-slate-200 text-slate-700 text-sm hover:bg-slate-300"
              >
                Clear
              </button>
            )}
          </div>

          {/* Collections + tags + smart filters in ONE collapsible drawer. The
              summary carries the active scope (Working Memory), so a collapsed
              drawer never hides what's filtering the queue. */}
          <details
            open={browseOpen}
            onToggle={(e) => toggleBrowse(e.currentTarget.open)}
            className="mt-3 rounded-xl border border-slate-200 bg-slate-50/60"
          >
            <summary className="cursor-pointer select-none list-none [&::-webkit-details-marker]:hidden px-3 py-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-slate-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-400 rounded-xl">
              <span className="font-semibold uppercase tracking-wider text-slate-400">Browse &amp; filter</span>
              {browseScope.length === 0 ? (
                <span className="text-slate-400">all items</span>
              ) : (
                browseScope.map((s) => (
                  <span key={s} className="inline-flex items-center px-1.5 py-0 rounded-full bg-white border border-slate-200 text-slate-600">{s}</span>
                ))
              )}
              <span className="ml-auto text-slate-400" aria-hidden="true">{browseOpen ? '▾' : '▸'}</span>
            </summary>

            <div className="px-3 pb-3 pt-1 grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-3">
              <div>
                <h2 className="text-[11px] font-semibold uppercase tracking-wider text-slate-400 mb-2">Collections</h2>
                <div className="max-h-56 overflow-auto pr-1 slim-scroll">
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
              </div>

              <div>
                <h2 className="text-[11px] font-semibold uppercase tracking-wider text-slate-400 mb-2">Top Tags</h2>
                <div className="max-h-56 overflow-auto pr-1 slim-scroll">
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
              </div>
            </div>

            {/* Smart client-side filters — only meaningful once the model has scored
                the library (bands/prestige/goal all key off the relevance score). */}
            {queueMeta.model_ready && queue.length > 0 && (
              <div className="px-3 pb-3">
                <LibraryFilterBar
                  filters={clientFilters}
                  onChange={setClientFilters}
                  whyOptions={whyOptions}
                  goalEnabled={goalEnabled}
                  qualityEnabled={qualityEnabled}
                  onClear={clearClientFilters}
                />
              </div>
            )}
          </details>
        </Section>

        {/* Region B — REVIEW QUEUE: the work surface. Predict lives HERE, atop the
            rows whose Confirm/Override cards it produces, with its own feedback. */}
        <Section label="Review queue">
          <PredictionsBar
            fleetStatus={fleetStatus}
            onRun={handleReviewCool}
            onStop={stopReviewCool}
            autoActive={autoReview.active}
            stopping={autoReview.stopping}
            coolCount={coolUndecided}
            proposedCount={proposedCount}
          />
          <div className="mt-3">
            <ReadNextView
          // Remount on a deliberate SERVER filter change so the incremental-reveal
          // count and any expanded row reset — but NOT on the 4s status poll, and
          // NOT on a client filter (that resets the reveal via filterSig instead).
          key={`${selectedCollection}|${selectedTag}|${search}|${includeRead}`}
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
          collections={flatCollections}
          onAddToCollection={handleAddToCollection}
          addingToCollection={addingToCollection}
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
        </Section>

        {/* Region C — EXPORT TO ZOTERO: the heavy, occasional whole-library WRITES
            and their last-synced status, which belongs HERE beside Sync — never
            under Predict (the conflation that made Predict look like it should
            change a sync timestamp). Hick's/Miller's Law: the four actions stay in
            one grouped disclosure. */}
        <Section label="Export to Zotero">
          <div className="flex flex-wrap gap-2 items-center">
            <ZoteroActionsMenu
              disabled={syncingAll}
              actions={[
                {
                  label: 'Sync all → Zotero', busy: syncingAll, busyLabel: 'Syncing…',
                  onClick: () => handleSyncAll(false),
                  title: 'One click, whole chain: rescore the library if the model changed since, then write zs:rel/<band> relevance tags AND stamp the Call-Number ranks (zr0001…) into Zotero. Backup-first; needs Zotero closed (you\'ll be asked to force otherwise). Then just sort the Call Number column in Zotero.',
                },
                {
                  label: 'Fetch full text', busy: fetchingFulltext, busyLabel: 'Fetching…',
                  disabled: fetchingFulltext, onClick: () => handleFetchFulltext(false),
                  title: 'Download the arXiv full-text PDF for every library paper that has an arXiv link but no PDF, and attach it natively to Zotero. Skips papers that already have a PDF. Backs up first; runs with Zotero closed; PDFs upload to zotero.org on the next sync.',
                },
              ]}
            />
          </div>
          {(zoteroSyncedAt.tags || zoteroSyncedAt.ranks) && (
            <p className="mt-2 text-[11px] text-slate-400">
              Last synced — relevance tags: {formatShortDate(zoteroSyncedAt.tags) || 'never'} ·
              Call-Number ranks: {formatShortDate(zoteroSyncedAt.ranks) || 'never'}
            </p>
          )}
        </Section>

        {/* One shared transient slot for action results (sync / add-to-collection /
            triage). Self-gates to null when empty, so no stray hairline. */}
        <StatusBanner message={message} isError={isError} />
      </div>
    </div>
  );
}
