import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import {
  fetchProvenanceList,
  fetchReviewDetail,
  submitVerdict,
  deleteVerdict,
  fetchEffectiveLabels,
  fetchEffectiveLabelsSummary,
  fetchBorderSuggestions,
} from '../api/goldenApi.js';
import { fetchCollections, fetchTags } from '../api/libraryApi.js';
import PaperListItem from '../components/PaperListItem.jsx';
import ProvenanceBreakdown from '../components/ProvenanceBreakdown.jsx';
import AnnotationsList from '../components/AnnotationsList.jsx';
import NotesList from '../components/NotesList.jsx';
import VerdictPanel from '../components/VerdictPanel.jsx';
import AuthorByline from '../components/AuthorByline.jsx';
import PaperDetailLayout from '../components/PaperDetailLayout.jsx';
import LinksRow from '../components/paper/LinksRow.jsx';
import TagOfInterestEditor from '../components/paper/TagOfInterestEditor.jsx';
import DeepReviewSection from '../components/paper/DeepReviewSection.jsx';
import {
  PRIORITY_FILTERS,
  FLAG_FILTERS,
  PRIORITY_BY_KEY,
  FilterChip,
  ErrorBanner,
  AbstractBlock,
  GroundTruthOneLiner,
  EffectiveLabelsStrip,
  TriageProgress,
  AnnotateHintBanner,
  readAnnotateHintDismissed,
} from './AnnotationVerdict_helpers.jsx';

// Flatten the Zotero collection tree into indented <option>s for a compact
// <select> (annotate's left column is narrow — a dropdown beats Library's tree).
function flattenCollections(nodes, depth = 0) {
  const flat = [];
  for (const node of nodes || []) {
    flat.push({ key: node.key, name: `${' '.repeat(depth * 2)}${node.name}`, item_count: node.item_count || 0 });
    if (node.children?.length) flat.push(...flattenCollections(node.children, depth + 1));
  }
  return flat;
}

// Tightly-composed metadata strip for the sticky top zone. Stays ~60-90px.
function DetailTopStrip({ detail }) {
  return (
    <div>
      <h2
        className="text-base font-bold text-slate-900 leading-snug truncate flex items-center gap-2"
        title={detail.title || '(untitled)'}
      >
        <span className="truncate">{detail.title || '(untitled)'}</span>
        {detail.source === 'csv_stub' && (
          <span
            className="shrink-0 text-[10px] px-1.5 py-0.5 rounded-full bg-amber-100 text-amber-800 border border-amber-300 font-semibold"
            title="The live source (Zotero / feed DB) no longer has this row. Showing data from the golden CSV."
          >
            stub
          </span>
        )}
      </h2>
      <div className="mt-1">
        <AuthorByline authors={detail.authors} source={detail.source} />
      </div>
      <div className="text-[11px] text-slate-500 mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-0.5">
        {detail.venue && <span>{detail.venue}</span>}
        {detail.year && <span>{detail.year}</span>}
        {detail.date_added && (
          <span title="When this paper was added to your library">
            Added {String(detail.date_added).slice(0, 10)}
          </span>
        )}
        <span className="mono text-slate-400">{detail.item_key}</span>
      </div>
    </div>
  );
}

// Wrapper to keep the empty/loading right-column matching the lg:col-span-8
// width that PaperDetailLayout itself applies when it renders content.
function DetailShell({ children }) {
  return (
    <section className="glass rounded-2xl border border-slate-200 p-4 lg:col-span-8 max-h-[calc(100vh-7rem)]">
      {children}
    </section>
  );
}

export default function AnnotationVerdict() {
  const queryClient = useQueryClient();
  // Deep-link: the Library "Read next" queue opens a specific item via
  // ?item_key=<zoteroKey>. The detail panel loads it independently of the
  // provenance list (review_detail handles library keys), and the auto-select
  // effect below honors it until the user picks something else.
  const [searchParams] = useSearchParams();
  const deepLinkedKey = searchParams.get('item_key') || null;
  const [priorityFilter, setPriorityFilter] = useState('must_read');
  const [flagFilter, setFlagFilter] = useState('');
  const [selectedCollection, setSelectedCollection] = useState('');
  const [selectedTag, setSelectedTag] = useState('');
  const [search, setSearch] = useState('');
  const [selectedKey, setSelectedKey] = useState(deepLinkedKey);
  // Phase 1.18 batch-mode bundle: keyboard shortcuts (j/k navigate, 1-4 priority),
  // optimistic auto-advance on verdict save, flashStatus for keyboard feedback.
  const [flashStatus, setFlashStatus] = useState(null);
  const [sortOrder, setSortOrder] = useState('score_desc');
  const [hintDismissed, setHintDismissed] = useState(readAnnotateHintDismissed);
  const listRef = useRef(null);

  // ---------- List query ----------
  // The "border" priorityFilter is a special active-learning mode: instead
  // of filtering the provenance list, we replace its data source with the
  // /api/golden/border-suggestions endpoint and project the response into
  // the same shape PaperListItem expects. Backend re-trains the regressor
  // on every call (~30 s), so cache aggressively.
  const isBorderMode = priorityFilter === 'border';
  const listQuery = useQuery({
    queryKey: ['provenance-list', priorityFilter, flagFilter, selectedCollection, selectedTag],
    enabled: !isBorderMode,
    queryFn: () =>
      fetchProvenanceList({
        priority: priorityFilter || undefined,
        flag: flagFilter || undefined,
        collection: selectedCollection || undefined,
        tag: selectedTag || undefined,
        limit: 200,
      }),
  });
  const borderQuery = useQuery({
    queryKey: ['border-suggestions', 50],
    enabled: isBorderMode,
    queryFn: () => fetchBorderSuggestions({ topK: 50 }),
    staleTime: 5 * 60_000,
    // The endpoint computes in the background (scoring ~740 rows takes a
    // few minutes). While status==="computing" it returns no items; poll
    // every 4 s until the result is ready, then stop.
    refetchInterval: (query) =>
      query.state.data?.status === 'computing' ? 4000 : false,
  });
  const borderStatus = borderQuery.data?.status ?? null;

  // Collection/tag filter sources (same Zotero data Library's sidebar uses).
  const collectionsQuery = useQuery({
    queryKey: ['zotero-collections'], queryFn: fetchCollections, staleTime: 5 * 60_000,
  });
  const tagsQuery = useQuery({
    queryKey: ['zotero-tags', 300], queryFn: () => fetchTags({ limit: 300 }), staleTime: 5 * 60_000,
  });
  const flatCollections = useMemo(
    () => flattenCollections(collectionsQuery.data?.items || []),
    [collectionsQuery.data],
  );
  const topTags = tagsQuery.data?.items || [];

  const items = useMemo(() => {
    if (isBorderMode) {
      const raw = borderQuery.data?.items ?? [];
      return raw.map((s) => ({
        item_key: s.item_key,
        title: s.title,
        derived_priority: s.current_priority,
        persisted_priority: s.predicted_priority,
        flags: s.disagrees ? ['border', 'conflict'] : ['border'],
        border_distance: s.border_distance,
        predicted_score: s.predicted_score,
      }));
    }
    return listQuery.data?.items ?? [];
  }, [isBorderMode, borderQuery.data, listQuery.data]);
  const totalMatched = isBorderMode
    ? borderQuery.data?.total ?? 0
    : listQuery.data?.total_matched ?? 0;
  const flagCounts = listQuery.data?.flag_counts ?? {};

  const sortedItems = useMemo(() => {
    if (isBorderMode) return items;
    return [...items].sort((a, b) =>
      sortOrder === 'score_desc'
        ? (b.derived_score ?? 0) - (a.derived_score ?? 0)
        : (a.derived_score ?? 0) - (b.derived_score ?? 0),
    );
  }, [items, sortOrder, isBorderMode]);

  const filteredItems = useMemo(() => {
    if (!search.trim()) return sortedItems;
    const q = search.trim().toLowerCase();
    return sortedItems.filter((it) => (it.title || '').toLowerCase().includes(q));
  }, [sortedItems, search]);

  // ---------- Detail query ----------
  const detailQuery = useQuery({
    queryKey: ['review-detail', selectedKey],
    queryFn: () => fetchReviewDetail(selectedKey),
    enabled: Boolean(selectedKey),
  });

  // ---------- Effective-labels (hybrid GT) queries ----------
  // Cached once per session (60s staleTime) — both the list (for per-item
  // "Used as GT" badges) and the aggregate summary (for the top strip).
  // Invalidated on verdict save/delete so badges & counts refresh immediately.
  const effectiveLabelsQuery = useQuery({
    queryKey: ['effective-labels'],
    queryFn: fetchEffectiveLabels,
    staleTime: 60_000,
  });
  const effectiveLabelsSummaryQuery = useQuery({
    queryKey: ['effective-labels-summary'],
    queryFn: fetchEffectiveLabelsSummary,
    staleTime: 60_000,
  });

  // item_key -> source ('user' | 'derived'). Built once per data change.
  const effectiveSourceByKey = useMemo(() => {
    const m = new Map();
    const rows = effectiveLabelsQuery.data?.items ?? [];
    for (const r of rows) {
      if (r && r.item_key) m.set(r.item_key, r.source);
    }
    return m;
  }, [effectiveLabelsQuery.data]);

  // Index of the selected paper within the current filtered list.
  const selectedIdx = useMemo(
    () => filteredItems.findIndex((it) => it.item_key === selectedKey),
    [filteredItems, selectedKey],
  );

  // Goal-Gradient progress: how many of the visible pile already carry the
  // user's verdict. Mirrors PaperListItem's "★ yours" condition so the bar and
  // the badges agree. Refreshes with effective-labels after each save.
  const labeledCount = useMemo(
    () =>
      filteredItems.filter(
        (it) =>
          effectiveSourceByKey.get(it.item_key) === 'user'
          || it.is_user_override
          || it.is_direct_user_verdict
          || it.is_manual_override,
      ).length,
    [filteredItems, effectiveSourceByKey],
  );

  // Auto-select the first paper on load (and when the filter changes
  // such that the current selection drops out of the list). Skips the
  // "Select a paper" empty pane — the user lands directly in flow.
  useEffect(() => {
    if (filteredItems.length === 0) return;
    // Honor a deep-linked key (from Library Read-next) even though it isn't in
    // the provenance list — its detail loads via the detail query.
    if (selectedKey && selectedKey === deepLinkedKey) return;
    const stillVisible = selectedKey
      && filteredItems.some((it) => it.item_key === selectedKey);
    if (!stillVisible) {
      setSelectedKey(filteredItems[0].item_key);
    }
  }, [filteredItems, selectedKey, deepLinkedKey]);

  // Doherty-threshold-driven: advance UI BEFORE the network round-trip lands.
  const advance = useCallback(
    (direction = 'next') => {
      if (filteredItems.length === 0) return;
      let nextIdx;
      if (selectedIdx < 0) {
        nextIdx = 0;
      } else if (direction === 'next') {
        nextIdx = Math.min(selectedIdx + 1, filteredItems.length - 1);
      } else {
        nextIdx = Math.max(selectedIdx - 1, 0);
      }
      const nextKey = filteredItems[nextIdx]?.item_key;
      if (nextKey && nextKey !== selectedKey) {
        setSelectedKey(nextKey);
      }
    },
    [filteredItems, selectedIdx, selectedKey],
  );

  // ---------- Mutations ----------
  const submitMutation = useMutation({
    mutationFn: submitVerdict,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['provenance-list'] });
      // Refresh "Used as GT" badge + summary strip immediately.
      queryClient.invalidateQueries({ queryKey: ['effective-labels'] });
      queryClient.invalidateQueries({ queryKey: ['effective-labels-summary'] });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (itemKey) => deleteVerdict(itemKey),
    onSuccess: () => {
      if (selectedKey) {
        queryClient.invalidateQueries({ queryKey: ['review-detail', selectedKey] });
      }
      queryClient.invalidateQueries({ queryKey: ['provenance-list'] });
      queryClient.invalidateQueries({ queryKey: ['effective-labels'] });
      queryClient.invalidateQueries({ queryKey: ['effective-labels-summary'] });
    },
  });

  function handleSelect(item) {
    setSelectedKey(item.item_key);
    submitMutation.reset();
    deleteMutation.reset();
  }

  // Optimistic save: advance to next paper instantly, run the mutation in the
  // background. Doherty Threshold: keeps batch-mode flow under 400 ms perceived.
  const handleVerdictSubmit = useCallback(
    ({ user_priority, comment }) => {
      if (!selectedKey) return;
      const itemKey = selectedKey;
      setFlashStatus(`Saved ${itemKey} → ${user_priority}`);
      setTimeout(() => setFlashStatus(null), 1500);
      advance('next');
      submitMutation.mutate(
        { item_key: itemKey, user_priority, comment },
        {
          onSuccess: (data) => {
            // The verdict is mirrored to Zotero as a `label:<priority>` tag (the
            // ground truth) and the comment as a note. Flag a soft failure on
            // either (e.g. Zotero open) without undoing the saved verdict.
            const soft = data?.label_error || data?.note_error;
            if (soft) {
              const what = data?.label_error ? 'label not written' : 'note not written';
              setFlashStatus(`Saved ${itemKey} → ${user_priority} (${what}: ${soft})`);
              setTimeout(() => setFlashStatus(null), 4000);
            }
          },
          onError: (err) => {
            setSelectedKey(itemKey);
            setFlashStatus(`Save failed for ${itemKey}: ${err.message || err}`);
          },
        },
      );
    },
    [selectedKey, advance, submitMutation],
  );

  function handleVerdictDelete() {
    if (!selectedKey) return;
    deleteMutation.mutate(selectedKey);
  }

  // ---------- Keyboard shortcuts ----------
  // Disabled while the user is typing in the comment textarea or the search box.
  useEffect(() => {
    function onKey(e) {
      const t = e.target;
      const isTyping =
        t && (t.tagName === 'TEXTAREA' || (t.tagName === 'INPUT' && t.type !== 'checkbox'));
      if (isTyping) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;

      if (e.key === 'j') {
        e.preventDefault();
        advance('next');
        return;
      }
      if (e.key === 'k') {
        e.preventDefault();
        advance('prev');
        return;
      }
      if (PRIORITY_BY_KEY[e.key] && selectedKey) {
        e.preventDefault();
        const priority = PRIORITY_BY_KEY[e.key];
        const existing = detailQuery.data?.verdict ?? null;
        // Overwrite guard, relocated from the mouse path's "Edit" gate
        // (Tesler's Law): a number key commits instantly for a new or unchanged
        // verdict — the common batch case — but changing an existing verdict to
        // a different value asks first, so a fumbled key can't silently destroy
        // a deliberate label.
        if (
          existing
          && existing.user_priority
          && existing.user_priority !== priority
          && !window.confirm(
            `Overwrite your verdict (${existing.user_priority} → ${priority}) for this paper?`,
          )
        ) {
          return;
        }
        handleVerdictSubmit({ user_priority: priority, comment: existing?.comment ?? '' });
        return;
      }
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [advance, selectedKey, detailQuery.data, handleVerdictSubmit]);

  const detail = detailQuery.data;

  // Right column: empty / loading / loaded
  let detailContent = null;
  if (!selectedKey) {
    detailContent = (
      <PaperDetailLayout
        emptyState={
          <DetailShell>
            <div className="text-sm text-slate-500 p-4 rounded bg-slate-50 border border-slate-200">
              Select a paper on the left to see details.
            </div>
          </DetailShell>
        }
      />
    );
  } else if (detailQuery.isLoading) {
    detailContent = (
      <PaperDetailLayout
        emptyState={
          <DetailShell>
            <div className="text-sm text-slate-500 p-4">Loading paper detail…</div>
            <ErrorBanner error={detailQuery.error} title="Detail load failed" />
          </DetailShell>
        }
      />
    );
  } else if (detail) {
    detailContent = (
      <PaperDetailLayout
        topStrip={<DetailTopStrip detail={detail} />}
        bottomStrip={
          <div className="space-y-2">
            <GroundTruthOneLiner detail={detail} />
            {flashStatus && (
              <div
                role="status"
                aria-live="polite"
                className="px-3 py-2 rounded-lg bg-teal-50 border border-teal-300 text-sm text-teal-900"
              >
                {flashStatus}
              </div>
            )}
            <VerdictPanel
              itemKey={detail.item_key}
              derivedPriority={detail.provenance?.derived_priority}
              existingVerdict={detail.verdict}
              onSubmit={handleVerdictSubmit}
              onDelete={handleVerdictDelete}
              submitting={submitMutation.isPending}
              submitError={submitMutation.error?.message || null}
              deleting={deleteMutation.isPending}
              deleteError={deleteMutation.error?.message || null}
            />
          </div>
        }
      >
        <ErrorBanner error={detailQuery.error} title="Detail load failed" />

        {/* Links: abstract / DOI / full PDF (shared with Library) */}
        <LinksRow detail={detail} itemKey={detail.item_key} />

        {/* Deep review (full-text quality + relevance, shared with Library) */}
        <DeepReviewSection
          itemKey={detail.item_key}
          deep={detail.deep_review}
          hasPdf={detail.has_pdf}
          onDone={() => queryClient.invalidateQueries({ queryKey: ['review-detail', selectedKey] })}
        />

        {/* Abstract */}
        <AbstractBlock abstract={detail.abstract} />

        {/* Tags of interest: emoji signals + free text (shared with Library) */}
        <div>
          <h3 className="text-xs font-bold uppercase tracking-wider text-slate-500 mb-2">
            Tags
          </h3>
          <TagOfInterestEditor
            itemKey={detail.item_key}
            tags={detail.tags}
            onChanged={() => queryClient.invalidateQueries({ queryKey: ['review-detail', selectedKey] })}
          />
        </div>

        {/* Provenance */}
        <ProvenanceBreakdown provenance={detail.provenance} />

        {/* Annotations */}
        <AnnotationsList annotations={detail.annotations} />

        {/* Notes */}
        <NotesList notes={detail.notes} />
      </PaperDetailLayout>
    );
  } else {
    // selectedKey is set, not loading, but no detail => the detail query
    // errored. Selective Attention: surface the failure in the detail pane
    // where the user is looking, with a way out — never a silent blank column.
    detailContent = (
      <PaperDetailLayout
        emptyState={
          <DetailShell>
            <ErrorBanner
              error={detailQuery.error || new Error('This paper could not be loaded.')}
              title="Couldn't load this paper"
            />
            <button
              type="button"
              onClick={() => detailQuery.refetch()}
              className="mt-2 px-3 py-1.5 rounded-lg border border-slate-300 text-sm font-semibold hover:bg-slate-50"
            >
              Retry
            </button>
          </DetailShell>
        }
      />
    );
  }

  // Plain-language name of the active filter, for the empty state (Selective
  // Attention: tell the user which slice came up empty, not just "no match").
  const activeFilterLabel = isBorderMode
    ? '🎯 border'
    : (PRIORITY_FILTERS.find((p) => p.key === priorityFilter)?.label ?? 'these filters');

  return (
    <>
      {!hintDismissed && (
        <AnnotateHintBanner onDismiss={() => setHintDismissed(true)} />
      )}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-4">
      {/* ---------- Left column: paper list ---------- */}
      <aside
        ref={listRef}
        className="glass rounded-2xl border border-slate-200 p-3 lg:col-span-4 flex flex-col max-h-[calc(100vh-7rem)]"
      >
        <div className="flex items-center justify-between mb-2">
          <h2 className="text-xs font-bold uppercase tracking-wider text-slate-500">
            Papers
          </h2>
          <span className="text-[11px] text-slate-500">
            Showing {filteredItems.length} of {totalMatched}
          </span>
        </div>

        <TriageProgress labeled={labeledCount} total={filteredItems.length} />

        <EffectiveLabelsStrip summary={effectiveLabelsSummaryQuery.data} />

        <div className="space-y-2 mb-2">
          <div className="flex flex-wrap gap-1.5">
            {PRIORITY_FILTERS.map((p) => (
              <FilterChip
                key={p.key || 'all'}
                active={priorityFilter === p.key}
                activeCls={p.cls}
                onClick={() => {
                  // Clear the title search when switching filters, else a
                  // leftover query silently shrinks the new list (e.g.
                  // switching to 'border' showed "1 of 50" because an old
                  // search term still matched a single row).
                  setSearch('');
                  setPriorityFilter(p.key);
                }}
              >
                {p.label}
              </FilterChip>
            ))}
          </div>
          <details
            open={Boolean(flagFilter)}
            className="text-xs"
          >
            <summary className="cursor-pointer text-slate-500 hover:text-slate-800 select-none mb-1.5">
              Advanced filters
              {flagFilter && (
                <span className="ml-1.5 px-1.5 py-0.5 rounded bg-amber-100 text-amber-800">
                  {flagFilter}
                </span>
              )}
            </summary>
            <div className="flex flex-wrap gap-1.5">
              {FLAG_FILTERS.map((f) => {
                const count = f.key ? flagCounts[f.key] : null;
                return (
                  <FilterChip
                    key={f.key || 'any'}
                    active={flagFilter === f.key}
                    onClick={() => setFlagFilter(f.key)}
                  >
                    {f.label}
                    {typeof count === 'number' && (
                      <span className="ml-1 text-slate-400">({count})</span>
                    )}
                  </FilterChip>
                );
              })}
            </div>
          </details>
          <div className="flex flex-wrap gap-1.5">
            <select
              value={selectedCollection}
              onChange={(e) => setSelectedCollection(e.target.value)}
              title="Filter by Zotero collection"
              className="flex-1 min-w-0 text-xs px-2 py-1.5 rounded-lg border border-slate-300 bg-white focus:outline-none focus:ring-2 focus:ring-teal-500"
            >
              <option value="">All collections</option>
              {flatCollections.map((c) => (
                <option key={c.key} value={c.key}>{c.name} ({c.item_count})</option>
              ))}
            </select>
            <select
              value={selectedTag}
              onChange={(e) => setSelectedTag(e.target.value)}
              title="Filter by Zotero tag"
              className="flex-1 min-w-0 text-xs px-2 py-1.5 rounded-lg border border-slate-300 bg-white focus:outline-none focus:ring-2 focus:ring-teal-500"
            >
              <option value="">All tags</option>
              {topTags.map((t) => (
                <option key={t.tag} value={t.tag}>{t.tag} ({t.item_count})</option>
              ))}
            </select>
          </div>
          <div className="flex gap-1.5">
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search title…"
              className="flex-1 min-w-0 text-sm px-2.5 py-1.5 rounded-lg border border-slate-300 focus:outline-none focus:ring-2 focus:ring-teal-500"
            />
            {!isBorderMode && (
              <button
                type="button"
                title={sortOrder === 'score_desc' ? 'Sorted by score ↓ — click for ↑' : 'Sorted by score ↑ — click for ↓'}
                onClick={() => setSortOrder((s) => s === 'score_desc' ? 'score_asc' : 'score_desc')}
                className="shrink-0 text-[11px] px-2 py-1.5 rounded-lg border border-slate-300 bg-white hover:bg-slate-50 text-slate-600 font-semibold"
              >
                {sortOrder === 'score_desc' ? '↓ score' : '↑ score'}
              </button>
            )}
          </div>
        </div>

        <ErrorBanner error={listQuery.error || borderQuery.error} title="List load failed" />

        <ul className="space-y-1.5 slim-scroll overflow-y-auto flex-1 pr-1">
          {(listQuery.isLoading || borderQuery.isLoading) && (
            <li className="text-xs text-slate-500 p-3">
              {isBorderMode ? 'Loading border suggestions…' : 'Loading papers…'}
            </li>
          )}
          {isBorderMode && borderStatus === 'computing' && (
            <li className="text-xs text-slate-500 p-3">
              Scoring your library against the current model… this runs in the
              background (a few minutes the first time after re-labelling, then
              cached). The list will fill in automatically.
            </li>
          )}
          {isBorderMode && borderStatus === 'error' && (
            <li className="text-xs text-rose-700 p-3">
              Border computation failed: {borderQuery.data?.message || 'unknown error'}
            </li>
          )}
          {!(listQuery.isLoading || borderQuery.isLoading)
            && borderStatus !== 'computing'
            && filteredItems.length === 0 && (
            <li className="text-xs text-slate-500 p-3">
              No <span className="font-semibold">{activeFilterLabel}</span> papers
              {(search || selectedCollection || selectedTag || flagFilter) ? ' for this search/filter' : ''}.
              {' '}Try another filter above.
            </li>
          )}
          {filteredItems.map((it) => (
            <PaperListItem
              key={it.item_key}
              item={it}
              isSelected={selectedKey === it.item_key}
              onClick={handleSelect}
              effectiveSource={effectiveSourceByKey.get(it.item_key) ?? null}
            />
          ))}
        </ul>
      </aside>

      {/* ---------- Right column: paper detail ---------- */}
      {detailContent}
      </div>
    </>
  );
}
