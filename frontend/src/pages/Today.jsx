// /today route — Stage 1 of the two-stage reading flow: cull the feed firehose
// into the library. Each card shows provenance (bucket + source feed); the user
// reads the abstract, multi-selects, then commits ONE batch decision:
//   • Add to library  → materialize into the Zotero "Inbox" + positive training
//   • Trash           → strong negative training + mark read
// The fine must/should/could/don't priority is NOT chosen here — that happens in
// Stage 2 (Library "Read next") after actually reading.
//
// Laws of UX: Hick's Law (2 actions, not 7 buttons/card), Jakob's Law
// (checkbox multi-select + batch action, the familiar inbox pattern),
// Law of Common Region (provenance badges group origin).

import { useCallback, useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import PipelineFunnel from '../components/PipelineFunnel.jsx';
import PaperCard from '../components/today/PaperCard.jsx';
import NotConfiguredCard from '../components/setup/NotConfiguredCard.jsx';
import Spinner from '../components/ui/Spinner.jsx';
import HintBanner from '../components/ui/HintBanner.jsx';
import { humanizeError } from '../utils/humanizeError.js';
import { useSetupStatus } from '../hooks/useSetupStatus.js';
import {
  fetchDailySlate,
  addToLibrary,
  trashPapers,
  triggerTriageBacklog,
  getTriageStatus,
} from '../api/dailyApi.js';
import { fetchReview } from '../api/reviewApi.js';
import { reviewPaperUrl } from './reviewHelpers.js';

const HINT_STORAGE_KEY = 'today_hint_dismissed_v2';
const HINT_TEXT =
  'Today = cull. Read the abstract, tick the papers worth reading, then ' +
  'Add to library (or Trash). You give the real labels later, in Library → Read next.';

// ---------------------------------------------------------------------------
// Small shared building blocks
// ---------------------------------------------------------------------------

function ErrorBanner({ error, title = 'Error' }) {
  if (!error) return null;
  return (
    <div className="my-2 p-2 rounded-lg bg-rose-50 border border-rose-200 text-xs text-rose-800">
      <span className="font-semibold">{title}:</span>{' '}
      {humanizeError(error)}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Spot-check — a capped, clearly-labeled sample of papers the filter rejected,
// shown only when the cull queue is empty. Lets the user catch a wrong reject
// without the old degenerate "one audit card at a time, forever" stream.
// ---------------------------------------------------------------------------

const SPOTCHECK_LIMIT = 5;

function SpotCheckCard({ item, onAdd, onTrash, busy }) {
  const url = reviewPaperUrl(item);
  const scoreStr =
    item.composite_score != null ? Number(item.composite_score).toFixed(2) : '—';
  return (
    <article className="border border-slate-200 rounded-lg p-2.5 bg-white">
      <div className="text-sm font-semibold leading-snug">
        {url ? (
          <a href={url} target="_blank" rel="noreferrer noopener" className="hover:text-teal-700 hover:underline">
            {item.title || '(untitled)'}
          </a>
        ) : (
          <span>{item.title || '(untitled)'}</span>
        )}
      </div>
      <div className="mt-1 text-[11px] text-slate-500 flex flex-wrap items-center gap-x-2">
        <span className="text-slate-400">feed {item.feed_library_id ?? '?'}</span>
        <span className="mono">relevance {scoreStr}</span>
      </div>
      {item.summary?.abstract_preview && (
        <p className="mt-1 text-xs text-slate-600 line-clamp-2">{item.summary.abstract_preview}</p>
      )}
      <div className="mt-2 flex items-center gap-2">
        <button
          type="button"
          onClick={() => onAdd(item.id)}
          disabled={busy}
          className="px-2.5 py-1 rounded-lg text-xs font-semibold bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-40"
        >
          Add to library
        </button>
        <button
          type="button"
          onClick={() => onTrash(item.id)}
          disabled={busy}
          className="px-2.5 py-1 rounded-lg text-xs font-semibold border border-slate-300 hover:bg-slate-100 disabled:opacity-40"
        >
          Confirm reject
        </button>
      </div>
    </article>
  );
}

function SpotCheck({ onNavigate }) {
  const queryClient = useQueryClient();
  const [dismissed, setDismissed] = useState(() => new Set());
  const [msg, setMsg] = useState('');

  const query = useQuery({
    queryKey: ['spotcheck-gate-rejected', SPOTCHECK_LIMIT],
    queryFn: () => fetchReview({ state: 'gate_rejected', limit: SPOTCHECK_LIMIT }),
    staleTime: 30_000,
  });

  const addMut = useMutation({ mutationFn: addToLibrary });
  const trashMut = useMutation({ mutationFn: trashPapers });
  const busy = addMut.isPending || trashMut.isPending;

  const act = useCallback(
    (mutation, id, verb) => {
      mutation.mutate([id], {
        onSuccess: () => {
          setDismissed((prev) => new Set(prev).add(id));
          setMsg(`${verb} 1 paper.`);
          queryClient.invalidateQueries({ queryKey: ['daily-pipeline'] });
        },
      });
    },
    [queryClient],
  );

  const items = (query.data?.items || []).filter((it) => !dismissed.has(it.id));
  if (query.isLoading || items.length === 0) return null;

  return (
    <div className="mt-4">
      <div className="flex items-baseline justify-between gap-2 flex-wrap">
        <h3 className="text-sm font-semibold text-slate-800">Spot-check the filter</h3>
        <button
          type="button"
          onClick={() => onNavigate('/review?state=gate_rejected')}
          className="text-xs text-teal-700 hover:text-teal-900 underline"
        >
          Browse all filtered ↗
        </button>
      </div>
      <p className="text-[11px] text-slate-500 mt-0.5">
        Papers the model rejected (marked read in Zotero, not added). Add any it got wrong.
      </p>
      {msg && (
        <div className="my-2 p-2 rounded-lg bg-emerald-50 border border-emerald-200 text-xs text-emerald-800">
          {msg}
        </div>
      )}
      <div className="mt-2 space-y-2">
        {items.map((item) => (
          <SpotCheckCard
            key={item.id}
            item={item}
            busy={busy}
            onAdd={(id) => act(addMut, id, 'Added')}
            onTrash={(id) => act(trashMut, id, 'Confirmed reject of')}
          />
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Today page
// ---------------------------------------------------------------------------

export default function Today() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  // Gate the Zotero/scoring-backed slate on a connected reader — otherwise the
  // first-run "finish setup" card sits behind a "Slate load failed" error.
  const { status } = useSetupStatus();
  const zoteroReady = status?.zotero?.db_found === true;
  const zoteroKnownMissing = Boolean(status) && !status?.zotero?.db_found;
  const [selectedIds, setSelectedIds] = useState(() => new Set());
  const [actionMsg, setActionMsg] = useState('');
  const triageKicked = useRef(false);

  // K=15 (API ceiling 20): show MORE cards so the queue isn't drip-fed 5 at a
  // time. The model role quota scales with K server-side, so this actually
  // returns up to 15 (was capped at 5 by the fixed 3/1/1 role default).
  const slateQuery = useQuery({
    queryKey: ['daily-slate', { K: 15, lookback_hours: 168 }],
    queryFn: () => fetchDailySlate({ K: 15, lookback_hours: 168 }),
    enabled: zoteroReady,
  });

  const addMutation = useMutation({ mutationFn: addToLibrary });
  const trashMutation = useMutation({ mutationFn: trashPapers });

  // Always poll once on mount so the button reflects a drain already running
  // (e.g. started in another tab or before a reload); it self-stops when idle.
  const triageStatusQuery = useQuery({
    queryKey: ['triage-status'],
    queryFn: getTriageStatus,
    enabled: zoteroReady,
    refetchInterval: (q) => (q.state.data?.running ? 3000 : false),
  });
  const triageStatus = triageStatusQuery.data;

  const triageMutation = useMutation({
    mutationFn: triggerTriageBacklog,
    onSuccess: () => {
      triageKicked.current = true;
      triageStatusQuery.refetch();
    },
  });
  const draining = triageMutation.isPending || Boolean(triageStatus?.running);

  const slate = slateQuery.data;
  const papers = slate?.papers || [];

  const toggleSelect = useCallback((id) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const allSelected = papers.length > 0 && selectedIds.size === papers.length;
  const toggleSelectAll = useCallback(() => {
    setSelectedIds((prev) =>
      prev.size === papers.length ? new Set() : new Set(papers.map((p) => p.item_id)),
    );
  }, [papers]);

  const busy = addMutation.isPending || trashMutation.isPending;

  const commit = useCallback(
    (mutation, verb) => {
      const ids = [...selectedIds];
      if (ids.length === 0) return;
      mutation.mutate(ids, {
        onSuccess: (res) => {
          const n = res?.added ?? res?.trashed ?? ids.length;
          setActionMsg(`${verb} ${n} paper${n === 1 ? '' : 's'}.`);
          setSelectedIds(new Set());
          queryClient.invalidateQueries({ queryKey: ['daily-slate'] });
        },
      });
    },
    [selectedIds, queryClient],
  );

  // Backlog triage is now an explicit user action (the "Triage backlog"
  // button), not an auto-kick on mount — opening Today must not silently
  // launch a heavy full-backlog drain. A drain already running is still
  // reflected via the on-mount triage-status poll.

  const prevRunning = useRef(false);
  useEffect(() => {
    const running = Boolean(triageStatus?.running);
    if (prevRunning.current && !running) {
      queryClient.invalidateQueries({ queryKey: ['daily-slate'] });
    }
    prevRunning.current = running;
  }, [triageStatus?.running, queryClient]);

  const actionError = addMutation.error || trashMutation.error;
  const selectedCount = selectedIds.size;

  // Zotero not connected → the cull queue can't load. Show only the setup card.
  if (zoteroKnownMissing) {
    return (
      <section className="glass rounded-2xl border border-slate-200 p-4">
        <NotConfiguredCard />
      </section>
    );
  }

  return (
    <section className="glass rounded-2xl border border-slate-200 p-4">
      <NotConfiguredCard />
      <header className="mb-3 flex items-baseline justify-between flex-wrap gap-2">
        <div>
          <h2 className="text-lg font-bold text-slate-900">Today’s reading</h2>
          <p
            className="text-xs text-slate-500 mt-0.5"
            title="Best first: ranked by a blend of model relevance, goal match, and author/venue prestige — same order as the Library queue, so a card's displayed score may sit out of order. Surprise and off-track picks follow the model picks."
          >
            Cull the feed: keep what’s worth reading into your library, trash the rest.
          </p>
          {typeof slate?.awaiting_review_total === 'number' && (
            slate.awaiting_review_total > 0 ? (
              <p
                className="text-xs text-slate-600 mt-1"
                title="Feed papers waiting for your Add/Trash decision — the full backlog, not just what's shown"
              >
                📥 <strong>{slate.awaiting_review_total}</strong> awaiting your decision
                {papers.length > 0 && papers.length < slate.awaiting_review_total && (
                  <> · showing top {papers.length}</>
                )}
              </p>
            ) : (
              <p className="text-xs text-emerald-700 font-semibold mt-1">
                ✓ All caught up — nothing new to cull
              </p>
            )
          )}
          <PipelineFunnel lookbackHours={168} />
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => triageMutation.mutate()}
            disabled={draining}
            title="Rank the whole un-triaged feed backlog with the ML gate (no LLM). Runs in the background; processed items are marked read in Zotero."
            className="px-3 py-1.5 rounded-lg border border-slate-300 text-xs font-medium hover:bg-slate-100 disabled:opacity-50 inline-flex items-center gap-1.5"
          >
            {draining && <Spinner size="xs" color="teal" />}
            {draining
              ? `Triaging (ML)… ${triageStatus?.gate_onward ?? triageStatus?.triaged ?? 0} kept`
              : 'Triage backlog'}
          </button>
          <button
            type="button"
            onClick={() => slateQuery.refetch()}
            disabled={slateQuery.isFetching}
            className="px-3 py-1.5 rounded-lg border border-slate-300 text-xs font-medium hover:bg-slate-100 disabled:opacity-50"
          >
            {slateQuery.isFetching ? 'Refreshing…' : 'Refresh'}
          </button>
        </div>
      </header>

      <HintBanner storageKey={HINT_STORAGE_KEY}>{HINT_TEXT}</HintBanner>
      <ErrorBanner error={slateQuery.error} title="Slate load failed" />
      <ErrorBanner error={actionError} title="Action failed" />
      {actionMsg && (
        <div className="my-2 p-2 rounded-lg bg-emerald-50 border border-emerald-200 text-xs text-emerald-800">
          {actionMsg}
        </div>
      )}

      {slateQuery.isLoading && (
        <div role="status" aria-live="polite" className="flex items-center gap-2 p-4 text-sm text-slate-600">
          <Spinner size="sm" color="teal" />
          Loading today’s slate…
        </div>
      )}

      {/* Caught up: the cull queue is empty. Show a slim, non-blocking
          "fetching more" note when a backlog drain is running, and always
          offer the capped spot-check of filtered papers (no endless stream). */}
      {!slateQuery.isLoading && !slateQuery.error && papers.length === 0 && (
        <>
          {triageStatus?.running && (
            <div className="my-2 p-2 rounded-lg border border-slate-200 bg-slate-50 text-xs text-slate-600 flex items-center gap-2">
              <Spinner size="xs" color="teal" />
              Triaging via the ML gate — kept {triageStatus.gate_onward ?? triageStatus.triaged ?? 0}, filtered{' '}
              {triageStatus.gate_rejected || 0}
              {triageStatus.gate_reject_rate != null
                && ` (${Math.round(triageStatus.gate_reject_rate * 100)}% by ML)`}, tick{' '}
              {triageStatus.ticks || 0}. New picks fill in automatically.
              {triageStatus?.error && <span className="text-rose-700"> — {triageStatus.error}</span>}
            </div>
          )}
          <SpotCheck onNavigate={navigate} />
        </>
      )}

      {slate?.fellback_to_recent && papers.length > 0 && (
        <p className="mb-2 text-[11px] text-amber-700 italic">
          Showing older scored items (no fresh triage in the last 7 days).
          {triageStatus?.running ? ' Fresh triage is running…' : ''}
        </p>
      )}

      {!slateQuery.isLoading && !slateQuery.error
        && (slate?.weak_slate || (slate?.low_relevance_hidden ?? 0) > 0) && (
        <div className="mb-3 p-2.5 rounded-xl border border-amber-200 bg-amber-50 text-xs text-amber-900 flex items-start gap-2">
          <span aria-hidden="true">⚖️</span>
          <span className="flex-1 leading-snug">
            {slate?.weak_slate
              ? 'Light week — nothing in your feed strongly matches your goals in the last 7 days. '
              : ''}
            {(slate?.low_relevance_hidden ?? 0) > 0 && (
              <><strong>{slate.low_relevance_hidden}</strong> below-the-bar paper
                {slate.low_relevance_hidden === 1 ? '' : 's'} hidden. </>
            )}
            Run “Triage backlog” to pull in newer papers.
          </span>
        </div>
      )}

      {papers.length > 0 && (
        <>
          {/* Batch action bar */}
          <div className="sticky top-0 z-10 mb-3 flex items-center gap-2 flex-wrap p-2 rounded-xl border border-slate-200 bg-white/90 backdrop-blur">
            <label className="flex items-center gap-1.5 text-xs text-slate-600 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={allSelected}
                onChange={toggleSelectAll}
                className="h-4 w-4 rounded border-slate-300 text-teal-600 focus:ring-teal-500"
              />
              {selectedCount > 0 ? `${selectedCount} selected` : 'Select all'}
            </label>
            <div className="flex-1" />
            <button
              type="button"
              onClick={() => commit(addMutation, 'Added')}
              disabled={selectedCount === 0 || busy}
              className="px-3 py-1.5 rounded-lg border text-xs font-semibold bg-emerald-600 text-white border-emerald-600 hover:bg-emerald-700 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {addMutation.isPending ? 'Adding…' : `Add ${selectedCount || ''} to library`}
            </button>
            <button
              type="button"
              onClick={() => commit(trashMutation, 'Trashed')}
              disabled={selectedCount === 0 || busy}
              className="px-3 py-1.5 rounded-lg border text-xs font-semibold bg-rose-600 text-white border-rose-600 hover:bg-rose-700 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {trashMutation.isPending ? 'Trashing…' : `Trash ${selectedCount || ''}`}
            </button>
          </div>

          <div className="space-y-3">
            {papers.map((paper) => (
              <PaperCard
                key={paper.item_key}
                paper={paper}
                selected={selectedIds.has(paper.item_id)}
                onToggleSelect={toggleSelect}
              />
            ))}
          </div>
        </>
      )}

    </section>
  );
}
