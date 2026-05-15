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
} from '../api/goldenApi.js';
import PaperListItem from '../components/PaperListItem.jsx';
import TagsRow from '../components/TagsRow.jsx';
import ProvenanceBreakdown from '../components/ProvenanceBreakdown.jsx';
import AnnotationsList from '../components/AnnotationsList.jsx';
import NotesList from '../components/NotesList.jsx';
import VerdictPanel from '../components/VerdictPanel.jsx';

const PRIORITY_FILTERS = [
  { key: 'must_read', label: 'must_read', cls: 'bg-emerald-600 text-white border-emerald-600' },
  { key: 'should_read', label: 'should_read', cls: 'bg-sky-600 text-white border-sky-600' },
  { key: 'could_read', label: 'could_read', cls: 'bg-amber-500 text-white border-amber-500' },
  { key: 'dont_read', label: 'dont_read', cls: 'bg-rose-600 text-white border-rose-600' },
  { key: '', label: 'all', cls: 'bg-slate-700 text-white border-slate-700' },
];

const FLAG_FILTERS = [
  { key: '', label: 'any' },
  { key: 'weak_must_read', label: 'weak_must_read' },
  { key: 'near_must_read', label: 'near_must_read' },
  { key: 'manual_override', label: 'manual_override' },
];

function FilterChip({ active, onClick, children, activeCls }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`px-2.5 py-1 rounded-full text-xs font-medium border transition-colors ${
        active
          ? activeCls || 'bg-slate-900 text-white border-slate-900'
          : 'bg-white text-slate-700 border-slate-300 hover:bg-slate-100'
      }`}
    >
      {children}
    </button>
  );
}

function ErrorBanner({ error, title = 'Error' }) {
  if (!error) return null;
  return (
    <div className="my-2 p-2 rounded-lg bg-rose-50 border border-rose-200 text-xs text-rose-800">
      <span className="font-semibold">{title}:</span> {error.message || String(error)}
    </div>
  );
}

function AbstractBlock({ abstract }) {
  const [expanded, setExpanded] = useState(false);
  if (!abstract) return null;
  return (
    <div>
      <h3 className="text-xs font-bold uppercase tracking-wider text-slate-500 mb-1">
        Abstract
      </h3>
      <div
        className={`text-sm text-slate-700 whitespace-pre-line ${
          expanded ? '' : 'line-clamp-5'
        }`}
      >
        {abstract}
      </div>
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="mt-1 text-[11px] text-teal-700 hover:text-teal-900 font-medium"
      >
        {expanded ? 'Show less' : 'Show more'}
      </button>
    </div>
  );
}

function PdfButton({ pdfPath, hasPdf }) {
  const [copied, setCopied] = useState(false);
  if (!hasPdf || !pdfPath) return null;
  async function handleClick() {
    try {
      await navigator.clipboard.writeText(pdfPath);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      // Fallback: prompt the user with the path so they can copy manually.
      window.prompt('Copy this PDF path:', pdfPath);
    }
  }
  return (
    <button
      type="button"
      onClick={handleClick}
      className="px-3 py-1.5 rounded-lg bg-slate-900 text-white text-xs font-semibold hover:bg-slate-700"
      title={pdfPath}
    >
      {copied ? 'PDF path copied to clipboard ✓' : 'Copy PDF path'}
    </button>
  );
}

function formatAuthors(authors) {
  if (!authors) return '';
  if (!Array.isArray(authors)) return String(authors);
  return authors
    .map((a) => {
      if (typeof a === 'string') return a;
      if (!a || typeof a !== 'object') return '';
      if (a.name) return a.name;
      const first = a.first_name || a.firstName || a.first || '';
      const last = a.last_name || a.lastName || a.last || '';
      return `${first} ${last}`.trim();
    })
    .filter(Boolean)
    .join(', ');
}

// Batch-mode keyboard map (Jakob's Law: Gmail/Vim conventions for j/k; inbox-triage 1-4)
const PRIORITY_BY_KEY = {
  1: 'must_read',
  2: 'should_read',
  3: 'could_read',
  4: 'dont_read',
};

export default function AnnotationVerdict() {
  const queryClient = useQueryClient();
  const [priorityFilter, setPriorityFilter] = useState('must_read');
  const [flagFilter, setFlagFilter] = useState('');
  const [search, setSearch] = useState('');
  const [selectedKey, setSelectedKey] = useState(null);
  // Phase 1.18 batch-mode bundle (Laws-of-UX gate selected; Playwright pre-audit confirmed friction):
  //   - keyboard shortcuts (j/k navigate, 1-4 priority, Enter save)
  //   - optimistic auto-advance on verdict save
  //   - flashStatus for visible feedback during keyboard-driven flow
  const [flashStatus, setFlashStatus] = useState(null);
  const listRef = useRef(null);

  // ---------- List query ----------
  const listQuery = useQuery({
    queryKey: ['provenance-list', priorityFilter, flagFilter],
    queryFn: () =>
      fetchProvenanceList({
        priority: priorityFilter || undefined,
        flag: flagFilter || undefined,
        limit: 200,
      }),
  });

  const items = listQuery.data?.items ?? [];
  const totalMatched = listQuery.data?.total_matched ?? 0;
  const flagCounts = listQuery.data?.flag_counts ?? {};

  const filteredItems = useMemo(() => {
    if (!search.trim()) return items;
    const q = search.trim().toLowerCase();
    return items.filter((it) =>
      (it.title || '').toLowerCase().includes(q),
    );
  }, [items, search]);

  // ---------- Detail query ----------
  const detailQuery = useQuery({
    queryKey: ['review-detail', selectedKey],
    queryFn: () => fetchReviewDetail(selectedKey),
    enabled: Boolean(selectedKey),
  });

  // Index of the selected paper within the current filtered list.
  const selectedIdx = useMemo(
    () => filteredItems.findIndex((it) => it.item_key === selectedKey),
    [filteredItems, selectedKey],
  );

  // Doherty-threshold-driven: advance UI BEFORE the network round-trip lands.
  // The mutation still runs; if it fails the user sees the error banner.
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
      // Background invalidation; the UI already advanced optimistically.
      queryClient.invalidateQueries({ queryKey: ['provenance-list'] });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (itemKey) => deleteVerdict(itemKey),
    onSuccess: () => {
      if (selectedKey) {
        queryClient.invalidateQueries({ queryKey: ['review-detail', selectedKey] });
      }
      queryClient.invalidateQueries({ queryKey: ['provenance-list'] });
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
      // Show a short-lived flash so keyboard-only users get feedback.
      setFlashStatus(`Saved ${itemKey} → ${user_priority}`);
      setTimeout(() => setFlashStatus(null), 1500);
      // 1. Advance UI immediately.
      advance('next');
      // 2. Fire the mutation; invalidation re-fetches list to mark this row as verdicted.
      submitMutation.mutate(
        { item_key: itemKey, user_priority, comment },
        {
          onError: (err) => {
            // On failure, jump back to the failed paper and surface the error.
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

  // ---------- Keyboard shortcuts (Jakob's Law: j/k from Vim/Gmail; 1-4 from inbox triage) ----------
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
        // Pull current comment from the detail if a verdict already exists,
        // else send empty string. The user types comments via mouse path; keyboard
        // path is for fast batch labeling without comments.
        const existingComment = detailQuery.data?.verdict?.comment ?? '';
        handleVerdictSubmit({ user_priority: priority, comment: existingComment });
        return;
      }
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [advance, selectedKey, detailQuery.data, handleVerdictSubmit]);

  const detail = detailQuery.data;

  return (
    <div className="grid grid-cols-1 lg:grid-cols-12 gap-4">
      {/* Transient status banner: keyboard-driven flow needs visible feedback. */}
      {flashStatus && (
        <div
          role="status"
          aria-live="polite"
          className="lg:col-span-12 px-3 py-2 rounded-lg bg-teal-50 border border-teal-300 text-sm text-teal-900"
        >
          {flashStatus}
        </div>
      )}

      {/* ---------- Left column: paper list ---------- */}
      <aside className="glass rounded-2xl border border-slate-200 p-3 lg:col-span-4 flex flex-col max-h-[calc(100vh-7rem)]">
        <div className="flex items-center justify-between mb-2">
          <h2 className="text-xs font-bold uppercase tracking-wider text-slate-500">
            Papers
          </h2>
          <span className="text-[11px] text-slate-500">
            Showing {filteredItems.length} of {totalMatched}
          </span>
        </div>

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
            />
          ))}
        </ul>
      </aside>

      {/* ---------- Right column: paper detail ---------- */}
      {/* Constrain height + scroll so the VerdictPanel can sticky-pin to the
          bottom (Fitts's Law: keep the verdict target within reach). */}
      <section className="glass rounded-2xl border border-slate-200 p-4 lg:col-span-8 overflow-y-auto max-h-[calc(100vh-7rem)] relative slim-scroll">

        {!selectedKey && (
          <div className="text-sm text-slate-500 p-4 rounded bg-slate-50 border border-slate-200">
            Select a paper on the left to see details.
          </div>
        )}

        {selectedKey && detailQuery.isLoading && (
          <div className="text-sm text-slate-500 p-4">Loading paper detail…</div>
        )}

        <ErrorBanner error={detailQuery.error} title="Detail load failed" />

        {selectedKey && detail && (
          <div className="space-y-5">
            {/* Title + metadata */}
            <header>
              <h2 className="text-lg font-bold text-slate-900 leading-snug">
                {detail.title || '(untitled)'}
              </h2>
              <div className="text-xs text-slate-600 mt-1">
                {formatAuthors(detail.authors)}
              </div>
              <div className="text-xs text-slate-500 mt-0.5 flex flex-wrap gap-x-3 gap-y-0.5">
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
            </header>

            {/* PDF button */}
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

            {/* Annotations */}
            <AnnotationsList annotations={detail.annotations} />

            {/* Notes */}
            <NotesList notes={detail.notes} />

            {/* Verdict — sticky-bottom so it stays in reach regardless of
                paper length (Fitts's Law). Backdrop blur + opaque-ish bg
                keeps content readable when text scrolls underneath. */}
            <div className="sticky bottom-0 -mx-4 -mb-4 px-4 pb-4 pt-3 bg-white/95 backdrop-blur border-t border-slate-200 z-10">
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
          </div>
        )}
      </section>
    </div>
  );
}
