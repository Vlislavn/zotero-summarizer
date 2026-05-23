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
import AuthorByline from '../components/AuthorByline.jsx';
import PipelineFunnel from '../components/PipelineFunnel.jsx';
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

// Plain-language label + tooltip for why a card is in the slate (paper.role).
// The user should never have to decode internal allocation-role names.
const BUCKET_LABEL = {
  model: 'top match',
  model_fallback: 'top match',
  surprise: 'surprise',
  audit: 'spot-check',
  diversity: 'wildcard',
};
const ROLE_HINT = {
  model: 'Best match to your interests (model + corpus + author/venue prestige).',
  model_fallback: 'Best match to your interests (model + corpus + author/venue prestige).',
  surprise: 'A high-surprise pick outside your usual reading pattern.',
  audit: 'The filter rejected this — shown so you can rescue a wrong reject. '
    + 'Marked read in Zotero, not added to your library.',
  diversity: 'Deliberately different from your library (low corpus affinity).',
};

// ---------------------------------------------------------------------------
// Small shared building blocks
// ---------------------------------------------------------------------------

function ErrorBanner({ error, title = 'Error' }) {
  if (!error) return null;
  return (
    <div className="my-2 p-2 rounded-lg bg-rose-50 border border-rose-200 text-xs text-rose-800">
      <span className="font-semibold">{title}:</span>{' '}
      {error.message || String(error)}
    </div>
  );
}

function Badge({ label, value, tone = 'slate', title }) {
  const tones = {
    slate: 'bg-slate-100 text-slate-700 border-slate-200',
    teal: 'bg-teal-50 text-teal-800 border-teal-200',
    violet: 'bg-violet-50 text-violet-800 border-violet-200',
    amber: 'bg-amber-50 text-amber-800 border-amber-200',
    sky: 'bg-sky-50 text-sky-800 border-sky-200',
  };
  const cls = tones[tone] || tones.slate;
  return (
    <span
      title={title || label}
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full border text-[11px] ${cls}`}
    >
      <span className="uppercase tracking-wider font-semibold">{label}</span>
      {value !== '' && value != null && <span className="mono font-bold">{value}</span>}
    </span>
  );
}

function parseAuthorsString(s) {
  if (!s || typeof s !== 'string') return [];
  return s
    .split(',')
    .map((name) => name.trim())
    .filter(Boolean)
    .map((name) => ({ name, h_index: null }));
}

function readHintDismissed() {
  try {
    return window.localStorage.getItem(HINT_STORAGE_KEY) === '1';
  } catch {
    return false;
  }
}

function writeHintDismissed() {
  try {
    window.localStorage.setItem(HINT_STORAGE_KEY, '1');
  } catch {
    /* no-op: incognito / disabled storage */
  }
}

function HintBanner({ onDismiss }) {
  return (
    <div
      role="note"
      className="mb-4 flex items-start gap-3 p-3 rounded-xl border border-teal-200 bg-teal-50 text-sm text-teal-900"
    >
      <span className="flex-1 leading-snug">{HINT_TEXT}</span>
      <button
        type="button"
        onClick={onDismiss}
        aria-label="Dismiss hint"
        title="Dismiss"
        className="text-teal-700 hover:text-teal-900 leading-none px-1.5 py-0.5 rounded hover:bg-teal-100"
      >
        {'×'}
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Quality — full-text peer-review assessment (services.quality_review), shown
// SEPARATELY from relevance. Grade + verdict inline; rubric on expand.
// ---------------------------------------------------------------------------

const GRADE_CLS = {
  A: 'bg-emerald-100 text-emerald-800 border-emerald-300',
  B: 'bg-teal-100 text-teal-800 border-teal-300',
  C: 'bg-amber-100 text-amber-800 border-amber-300',
  D: 'bg-rose-100 text-rose-800 border-rose-300',
};

function QualityBar({ label, value }) {
  const v = Math.max(0, Math.min(5, Number(value) || 0));
  return (
    <div className="flex items-center gap-2 text-[11px]">
      <span className="w-28 shrink-0 text-slate-500">{label}</span>
      <span className="flex-1 h-1.5 rounded bg-slate-100 overflow-hidden">
        <span className="block h-full bg-teal-500" style={{ width: `${(v / 5) * 100}%` }} />
      </span>
      <span className="w-4 text-right text-slate-600 mono">{v}</span>
    </div>
  );
}

function QualityBlock({ quality }) {
  const q = quality || {};
  const grade = q.grade || '';
  if (!grade) {
    const why = q.basis === 'not_assessed' ? 'no open-access PDF' : 'not in the top-K reviewed set';
    return (
      <div className="mt-1.5 text-[11px] text-slate-400">
        <span className="uppercase tracking-wider font-semibold text-slate-500">Quality</span>{' '}
        not assessed <span className="text-slate-300">({why})</span>
      </div>
    );
  }
  const gradeCls = GRADE_CLS[grade] || 'bg-slate-100 text-slate-700 border-slate-300';
  return (
    <div className="mt-1.5">
      <div className="flex items-start gap-2 text-xs">
        <span className="uppercase tracking-wider font-semibold text-slate-500 mt-0.5">Quality</span>
        <span
          className={`shrink-0 px-1.5 py-0.5 rounded-md border text-[11px] font-bold ${gradeCls}`}
          title="Full-text peer-review grade (A best – D weak), independent of relevance to you"
        >
          {grade}
        </span>
        {q.verdict && <span className="text-slate-700 italic">{q.verdict}</span>}
      </div>
      <details className="group mt-1">
        <summary className="cursor-pointer text-[11px] uppercase tracking-wider font-semibold text-slate-500 hover:text-slate-700 select-none">
          Quality review <span className="text-slate-400 normal-case font-normal">· from full text</span>
        </summary>
        <div className="mt-1.5 space-y-1">
          <QualityBar label="soundness" value={q.soundness} />
          <QualityBar label="novelty" value={q.novelty} />
          <QualityBar label="significance" value={q.significance} />
          <QualityBar label="reproducibility" value={q.reproducibility} />
          <QualityBar label="clarity" value={q.clarity} />
          {q.key_strength && <p className="text-[11px] text-emerald-800 mt-1">＋ {q.key_strength}</p>}
          {q.key_weakness && <p className="text-[11px] text-rose-800">－ {q.key_weakness}</p>}
        </div>
      </details>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Paper card — read the abstract, then tick the checkbox.
// ---------------------------------------------------------------------------

function PaperCard({ paper, selected, onToggleSelect }) {
  const authors = parseAuthorsString(paper.authors);
  if (authors.length > 0 && typeof paper.max_author_h_index === 'number') {
    authors[0] = { ...authors[0], h_index: paper.max_author_h_index };
  }
  const compositeStr =
    typeof paper.composite_score === 'number' ? paper.composite_score.toFixed(2) : '—';
  const prestigeStr =
    typeof paper.prestige_score === 'number' ? paper.prestige_score.toFixed(2) : '—';
  const bucket = BUCKET_LABEL[paper.role] || paper.role || '—';

  const titleNode = paper.url || paper.doi
    ? (
      <a
        href={paper.url || `https://doi.org/${paper.doi}`}
        target="_blank"
        rel="noreferrer noopener"
        className="text-sm font-bold text-slate-900 hover:text-teal-700 underline-offset-2 hover:underline"
      >
        {paper.title || '(untitled)'}
      </a>
    )
    : <span className="text-sm font-bold text-slate-900">{paper.title || '(untitled)'}</span>;

  return (
    <article
      className={`border rounded-xl p-3 bg-white shadow-sm transition-colors ${
        selected ? 'border-teal-400 ring-1 ring-teal-300 bg-teal-50/30' : 'border-slate-200'
      }`}
    >
      <div className="flex items-start gap-3">
        <input
          type="checkbox"
          checked={selected}
          onChange={() => onToggleSelect(paper.item_id)}
          aria-label={`Select ${paper.title || 'paper'}`}
          className="mt-1 h-4 w-4 shrink-0 rounded border-slate-300 text-teal-600 focus:ring-teal-500 cursor-pointer"
        />
        <div className="min-w-0 flex-1">
          <header className="mb-2">
            <div className="leading-snug">{titleNode}</div>
            <div className="mt-1">
              <AuthorByline authors={authors} source="feed" quiet />
            </div>
            <div className="text-[11px] text-slate-500 mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5">
              {paper.venue && <span className="italic">{paper.venue}</span>}
              {paper.year && <span>{paper.year}</span>}
              {/* Provenance: why this pick is here + which feed it came from. */}
              <Badge label={bucket} tone="amber" title={ROLE_HINT[paper.role] || 'Why this paper is here'} />
              {paper.feed_name && (
                <Badge label="feed" value={paper.feed_name} tone="sky" title="Source RSS feed" />
              )}
              <Badge label="relevance" value={compositeStr} tone="teal" title="Relevance to you (model + corpus + prestige) — not a quality judgment" />
              <Badge label="prestige" value={prestigeStr} tone="violet" title="Author / venue reputation (0–1) — not paper quality" />
            </div>
          </header>

          <QualityBlock quality={paper.quality} />

          {paper.rationale && (
            <details className="mb-2 group">
              <summary className="cursor-pointer text-[11px] uppercase tracking-wider font-semibold text-slate-500 hover:text-slate-700 select-none">
                Triage rationale
              </summary>
              <p className="mt-1.5 text-xs text-slate-700 italic whitespace-pre-line">
                {paper.rationale}
              </p>
            </details>
          )}

        </div>
      </div>
    </article>
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
  const [hintDismissed, setHintDismissed] = useState(readHintDismissed);
  const [selectedIds, setSelectedIds] = useState(() => new Set());
  const [actionMsg, setActionMsg] = useState('');
  const triageKicked = useRef(false);

  const slateQuery = useQuery({
    queryKey: ['daily-slate', { K: 5, lookback_hours: 168 }],
    queryFn: () => fetchDailySlate({ K: 5, lookback_hours: 168 }),
  });

  const addMutation = useMutation({ mutationFn: addToLibrary });
  const trashMutation = useMutation({ mutationFn: trashPapers });

  // Always poll once on mount so the button reflects a drain already running
  // (e.g. started in another tab or before a reload); it self-stops when idle.
  const triageStatusQuery = useQuery({
    queryKey: ['triage-status'],
    queryFn: getTriageStatus,
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
  const isStale = Boolean(slate?.fellback_to_recent);

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

  const handleDismissHint = useCallback(() => {
    writeHintDismissed();
    setHintDismissed(true);
  }, []);

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

  return (
    <section className="glass rounded-2xl border border-slate-200 p-4">
      <header className="mb-3 flex items-baseline justify-between flex-wrap gap-2">
        <div>
          <h2 className="text-lg font-bold text-slate-900">Today’s reading</h2>
          <p className="text-xs text-slate-500 mt-0.5">
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
            title="Score the whole un-triaged feed backlog via sota. Runs in the background; processed items are marked read in Zotero."
            className="px-3 py-1.5 rounded-lg border border-slate-300 text-xs font-medium hover:bg-slate-100 disabled:opacity-50 inline-flex items-center gap-1.5"
          >
            {draining && (
              <span aria-hidden="true" className="inline-block h-3 w-3 rounded-full border-2 border-slate-300 border-t-teal-600 animate-spin" />
            )}
            {draining ? `Triaging… ${triageStatus?.triaged || 0}` : 'Triage backlog'}
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

      {!hintDismissed && <HintBanner onDismiss={handleDismissHint} />}
      <ErrorBanner error={slateQuery.error} title="Slate load failed" />
      <ErrorBanner error={actionError} title="Action failed" />
      {actionMsg && (
        <div className="my-2 p-2 rounded-lg bg-emerald-50 border border-emerald-200 text-xs text-emerald-800">
          {actionMsg}
        </div>
      )}

      {slateQuery.isLoading && (
        <div role="status" aria-live="polite" className="flex items-center gap-2 p-4 text-sm text-slate-600">
          <span aria-hidden="true" className="inline-block h-3.5 w-3.5 rounded-full border-2 border-slate-300 border-t-teal-600 animate-spin" />
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
              <span aria-hidden="true" className="inline-block h-3 w-3 rounded-full border-2 border-slate-300 border-t-teal-600 animate-spin" />
              Triaging your feeds via sota… scored {triageStatus.triaged || 0}, gate-rejected{' '}
              {triageStatus.gate_rejected || 0} (tick {triageStatus.ticks || 0}). New picks fill in automatically.
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
