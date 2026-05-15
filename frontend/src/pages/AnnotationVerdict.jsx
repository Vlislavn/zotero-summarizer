import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
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
import PaperListItem from '../components/PaperListItem.jsx';
import TagsRow from '../components/TagsRow.jsx';
import ProvenanceBreakdown from '../components/ProvenanceBreakdown.jsx';
import AnnotationsList from '../components/AnnotationsList.jsx';
import NotesList from '../components/NotesList.jsx';
import VerdictPanel from '../components/VerdictPanel.jsx';
import AuthorByline from '../components/AuthorByline.jsx';
import PrestigeWaterfall from '../components/PrestigeWaterfall.jsx';
import PaperDetailLayout from '../components/PaperDetailLayout.jsx';
import {
  PRIORITY_FILTERS,
  FLAG_FILTERS,
  PRIORITY_BY_KEY,
  FilterChip,
  ErrorBanner,
  AbstractBlock,
  PdfButton,
  GroundTruthOneLiner,
  EffectiveLabelsStrip,
} from './AnnotationVerdict_helpers.jsx';

// Tightly-composed metadata strip for the sticky top zone. Stays ~60-90px.
function DetailTopStrip({ detail }) {
  return (
    <div>
      <h2
        className="text-base font-bold text-slate-900 leading-snug truncate"
        title={detail.title || '(untitled)'}
      >
        {detail.title || '(untitled)'}
      </h2>
      <div className="mt-1">
        <AuthorByline authors={detail.authors} source={detail.source} />
      </div>
      <div className="text-[11px] text-slate-500 mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-0.5">
        {detail.venue && <span>{detail.venue}</span>}
        {detail.year && <span>{detail.year}</span>}
        {detail.doi && (
          <a
            href={`https://doi.org/${detail.doi}`}
            target="_blank"
            rel="noreferrer noopener"
            className="text-teal-700 hover:text-teal-900 underline"
          >
            DOI: {detail.doi}
          </a>
        )}
        {detail.url && (
          <a
            href={detail.url}
            target="_blank"
            rel="noreferrer noopener"
            className="text-teal-700 hover:text-teal-900 underline"
          >
            URL
          </a>
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
  const [priorityFilter, setPriorityFilter] = useState('must_read');
  const [flagFilter, setFlagFilter] = useState('');
  const [search, setSearch] = useState('');
  const [selectedKey, setSelectedKey] = useState(null);
  // Phase 1.18 batch-mode bundle: keyboard shortcuts (j/k navigate, 1-4 priority),
  // optimistic auto-advance on verdict save, flashStatus for keyboard feedback.
  const [flashStatus, setFlashStatus] = useState(null);
  const listRef = useRef(null);

  // ---------- List query ----------
  // The "border" priorityFilter is a special active-learning mode: instead
  // of filtering the provenance list, we replace its data source with the
  // /api/golden/border-suggestions endpoint and project the response into
  // the same shape PaperListItem expects. Backend re-trains the regressor
  // on every call (~30 s), so cache aggressively.
  const isBorderMode = priorityFilter === 'border';
  const listQuery = useQuery({
    queryKey: ['provenance-list', priorityFilter, flagFilter],
    enabled: !isBorderMode,
    queryFn: () =>
      fetchProvenanceList({
        priority: priorityFilter || undefined,
        flag: flagFilter || undefined,
        limit: 200,
      }),
  });
  const borderQuery = useQuery({
    queryKey: ['border-suggestions', 50],
    enabled: isBorderMode,
    queryFn: () => fetchBorderSuggestions({ topK: 50 }),
    staleTime: 5 * 60_000,
  });

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

  const filteredItems = useMemo(() => {
    if (!search.trim()) return items;
    const q = search.trim().toLowerCase();
    return items.filter((it) => (it.title || '').toLowerCase().includes(q));
  }, [items, search]);

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
        const existingComment = detailQuery.data?.verdict?.comment ?? '';
        handleVerdictSubmit({ user_priority: priority, comment: existingComment });
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

        {/* PDF button (kept out of the topStrip per design) */}
        {detail.has_pdf && detail.pdf_path && (
          <div>
            <PdfButton pdfPath={detail.pdf_path} hasPdf={detail.has_pdf} />
          </div>
        )}

        {/* Abstract */}
        <AbstractBlock abstract={detail.abstract} />

        {/* Tags */}
        <div>
          <h3 className="text-xs font-bold uppercase tracking-wider text-slate-500 mb-2">
            Tags
          </h3>
          <TagsRow tags={detail.tags} />
        </div>

        {/* Provenance */}
        <ProvenanceBreakdown provenance={detail.provenance} />

        {/* NEW: SHAP / prestige waterfall */}
        <PrestigeWaterfall scoring={detail.scoring} />

        {/* Annotations */}
        <AnnotationsList annotations={detail.annotations} />

        {/* Notes */}
        <NotesList notes={detail.notes} />
      </PaperDetailLayout>
    );
  }

  return (
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

        <EffectiveLabelsStrip summary={effectiveLabelsSummaryQuery.data} />

        <div className="space-y-2 mb-2">
          <div className="flex flex-wrap gap-1.5">
            {PRIORITY_FILTERS.map((p) => (
              <FilterChip
                key={p.key || 'all'}
                active={priorityFilter === p.key}
                activeCls={p.cls}
                onClick={() => setPriorityFilter(p.key)}
              >
                {p.label}
              </FilterChip>
            ))}
          </div>
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
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search title…"
            className="w-full text-sm px-2.5 py-1.5 rounded-lg border border-slate-300 focus:outline-none focus:ring-2 focus:ring-teal-500"
          />
        </div>

        <ErrorBanner error={listQuery.error} title="List load failed" />

        <ul className="space-y-1.5 slim-scroll overflow-y-auto flex-1 pr-1">
          {listQuery.isLoading && (
            <li className="text-xs text-slate-500 p-3">Loading papers…</li>
          )}
          {!listQuery.isLoading && filteredItems.length === 0 && (
            <li className="text-xs text-slate-500 p-3">No papers match these filters.</li>
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
  );
}
