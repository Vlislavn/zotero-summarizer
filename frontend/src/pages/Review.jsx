import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  fetchReview,
  reviewAction,
  reviewApplyAll,
  reviewConfirmAllGateRejected,
} from '../api/reviewApi.js';
import { priorityClass, reviewPaperUrl, reviewBarStyle } from './reviewHelpers.js';

// Feed Review page — port of the `activeTab === 'review'` block from
// zotero_summarizer/web/ui.html. Functional parity with the Alpine version
// (toggle queue state, approve/reject/relabel, bulk apply, bulk confirm).
// Helpers extracted to ./reviewHelpers.js to keep this file under budget.

function StatusBanner({ message, isError }) {
  if (!message) return null;
  const cls = isError
    ? 'bg-rose-50 border-rose-200 text-rose-800'
    : 'bg-emerald-50 border-emerald-200 text-emerald-800';
  return <div className={`my-2 p-2 rounded-lg border text-xs ${cls}`}>{message}</div>;
}

function ShapBars({ item, expanded, onToggle }) {
  const shap = item.shap || [];
  if (!shap.length) return null;
  const shown = expanded ? shap : shap.slice(0, 4);
  return (
    <div className="mt-2">
      <div className="text-[11px] uppercase tracking-wider text-slate-500 mb-1">
        Why this score
      </div>
      <div className="space-y-1">
        {shown.map((c) => {
          const positive = c.contribution >= 0;
          return (
            <div key={c.feature} className="flex items-center gap-2 text-[11px]">
              <span className="w-44 truncate">{c.feature}</span>
              <div className="flex-1 h-2 rounded bg-slate-100 relative">
                <div
                  className={`absolute top-0 h-2 rounded ${positive ? 'bg-emerald-500' : 'bg-rose-500'}`}
                  style={reviewBarStyle(item, c.contribution)}
                />
              </div>
              <span className={`mono w-14 text-right ${positive ? 'text-emerald-700' : 'text-rose-700'}`}>
                {(positive ? '+' : '') + Number(c.contribution).toFixed(3)}
              </span>
            </div>
          );
        })}
        {shap.length > 4 && (
          <button type="button" onClick={onToggle} className="text-[11px] text-teal-700 underline">
            {expanded ? 'show top 4' : `show all ${shap.length}`}
          </button>
        )}
      </div>
    </div>
  );
}

function AuxContext({ aux }) {
  if (!aux) return null;
  return (
    <div className="mt-2 text-[11px] text-slate-600 flex flex-wrap gap-3">
      {aux.max_author_h_index && (
        <span>Max author h-index: <span className="mono font-semibold">{aux.max_author_h_index}</span></span>
      )}
      {aux.venue_works_count && (
        <span>Venue works: <span className="mono font-semibold">{aux.venue_works_count}</span></span>
      )}
      {aux.cited_by_count && (
        <span>Cited: <span className="mono font-semibold">{aux.cited_by_count}</span></span>
      )}
    </div>
  );
}

function ReviewItem({ item, state, localState, expanded, onToggleShap, onAction }) {
  const url = reviewPaperUrl(item);
  const wrapperClass = localState === 'approved'
    ? 'bg-emerald-50 border-emerald-300'
    : localState === 'rejected'
      ? 'bg-rose-50 border-rose-300'
      : 'bg-white border-slate-200';
  return (
    <div className={`border rounded-xl p-3 ${wrapperClass}`}>
      <div className="flex items-baseline justify-between gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className={`font-bold uppercase tracking-wide px-2 py-0.5 rounded ${priorityClass(item.reading_priority)}`}>
              {item.reading_priority || '?'}
            </span>
            <span className="mono text-slate-500">
              {item.composite_score != null ? Number(item.composite_score).toFixed(2) : '—'}
            </span>
            <span className="text-slate-400">feed{item.feed_library_id ?? '?'}</span>
            {item.audit_pick && (
              <span className="px-2 py-0.5 rounded bg-violet-100 text-violet-800 border border-violet-300 font-semibold"
                title="Gate said dont_read — surfaced for audit. Your verdict here measures the gate's false-negative rate.">
                🎲 audit pick
              </span>
            )}
            {url && (
              <a href={url} target="_blank" rel="noopener noreferrer"
                className="text-teal-700 hover:text-teal-900 underline text-xs font-semibold">
                open ↗
              </a>
            )}
            {item.doi && <span className="text-slate-400 mono text-[10px]">{item.doi}</span>}
          </div>
          <div className="text-sm font-semibold mt-1">
            {url ? (
              <a href={url} target="_blank" rel="noopener noreferrer" className="hover:text-teal-800 hover:underline">
                {item.title || '(untitled)'}
              </a>
            ) : (
              <span>{item.title || '(untitled)'}</span>
            )}
          </div>
          <div className="text-xs text-slate-600 mt-1 line-clamp-2">
            {item.summary?.abstract_preview || ''}
          </div>
        </div>
      </div>

      <ShapBars item={item} expanded={expanded} onToggle={onToggleShap} />
      <AuxContext aux={item.aux_context} />

      {item.summary?.triage_rationale && (
        <div className="mt-2 text-xs text-slate-700 italic">
          <span className="text-slate-400">LLM:</span>{' '}
          <span>{item.summary.triage_rationale}</span>
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        {state === 'awaiting_review' && (
          <>
            <button type="button" onClick={() => onAction(item.id, 'approve')}
              disabled={Boolean(localState)}
              className="px-3 py-1 rounded-lg text-xs font-semibold bg-emerald-600 text-white hover:bg-emerald-700 disabled:bg-slate-200 disabled:text-slate-500">
              Approve
            </button>
            <button type="button" onClick={() => onAction(item.id, 'reject')}
              disabled={Boolean(localState)}
              className="px-3 py-1 rounded-lg text-xs font-semibold bg-rose-600 text-white hover:bg-rose-700 disabled:bg-slate-200 disabled:text-slate-500">
              Reject
            </button>
          </>
        )}
        <span className="text-xs text-slate-500 ml-1">Relabel:</span>
        <button type="button" onClick={() => onAction(item.id, 'relabel', 'must_read')}
          disabled={Boolean(localState)}
          className="px-2 py-1 rounded-lg text-xs border border-amber-300 bg-amber-50 hover:bg-amber-100 disabled:opacity-50">must</button>
        <button type="button" onClick={() => onAction(item.id, 'relabel', 'should_read')}
          disabled={Boolean(localState)}
          className="px-2 py-1 rounded-lg text-xs border border-sky-300 bg-sky-50 hover:bg-sky-100 disabled:opacity-50">should</button>
        <button type="button" onClick={() => onAction(item.id, 'relabel', 'could_read')}
          disabled={Boolean(localState)}
          className="px-2 py-1 rounded-lg text-xs border border-slate-300 bg-slate-50 hover:bg-slate-100 disabled:opacity-50">could</button>
        <button type="button" onClick={() => onAction(item.id, 'relabel', 'dont_read')}
          disabled={Boolean(localState)}
          className="px-2 py-1 rounded-lg text-xs border border-rose-300 bg-rose-50 hover:bg-rose-100 disabled:opacity-50">dont</button>
        {localState && (
          <span className={`text-xs ml-2 ${localState === 'approved' ? 'text-emerald-700' : 'text-rose-700'}`}>
            → {localState}
          </span>
        )}
      </div>
    </div>
  );
}

export default function Review() {
  const [state, setState] = useState('awaiting_review');
  const [sort, setSort] = useState('recent');
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);
  const [applying, setApplying] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [message, setMessage] = useState('');
  const [isError, setIsError] = useState(false);
  const [itemState, setItemState] = useState({});
  const [shapExpanded, setShapExpanded] = useState({});

  const load = useCallback(async (currentState = state, currentSort = sort) => {
    setLoading(true);
    setMessage('');
    setIsError(false);
    try {
      const data = await fetchReview({ state: currentState, limit: 500, sort: currentSort });
      const nextItems = data?.items || [];
      setItems(nextItems);
      const ids = new Set(nextItems.map((it) => it.id));
      setItemState((prev) => {
        const out = {};
        for (const [k, v] of Object.entries(prev)) {
          if (ids.has(Number(k))) out[k] = v;
        }
        return out;
      });
    } catch (err) {
      setMessage(`Failed to load review queue: ${err.message || err}`);
      setIsError(true);
    } finally {
      setLoading(false);
    }
  }, [state, sort]);

  useEffect(() => { load(state, sort); }, [load, state, sort]);

  const approvedCount = useMemo(
    () => Object.values(itemState).filter((v) => v === 'approved').length,
    [itemState],
  );

  const handleAction = useCallback(async (id, action, label = null) => {
    try {
      const result = await reviewAction(id, action, label);
      const wentApproved = action === 'approve' || (action === 'relabel' && label !== 'dont_read');
      setItemState((prev) => ({ ...prev, [id]: wentApproved ? 'approved' : 'rejected' }));
      const parts = [`Item ${id}: ${action}${label ? ` (${label})` : ''} OK`];
      if (result?.queued_pending_changes) parts.push(`queued ${result.queued_pending_changes} pending change(s)`);
      if (result?.golden_csv_row_added) parts.push('appended to golden CSV');
      setMessage(parts.join(' — '));
      setIsError(false);
    } catch (err) {
      setMessage(`Item ${id}: ${action} failed — ${err.message || err}`);
      setIsError(true);
    }
  }, []);

  const handleApplyAll = useCallback(async () => {
    setApplying(true);
    setMessage('');
    try {
      const result = await reviewApplyAll();
      const applied = result?.applied || 0;
      const failedCount = result?.failed_count || 0;
      let msg = `Materialized ${applied} item(s) to Zotero Inbox`;
      if (failedCount > 0) {
        msg += `; ${failedCount} failed`;
        const first = (result.failed || [])[0];
        if (first) msg += ` (e.g. "${first.title || first.id}": ${first.error || ''})`;
      }
      if (applied === 0 && failedCount === 0) msg = 'Nothing to apply (no user_approved rows in DB).';
      setMessage(msg);
      setIsError(failedCount > 0);
      await load(state);
    } catch (err) {
      setMessage(`Apply-all failed: ${err.message || err}`);
      setIsError(true);
    } finally {
      setApplying(false);
    }
  }, [load, state]);

  const handleConfirmGateRejected = useCallback(async () => {
    if (!window.confirm(
      `Confirm all ${items.length} unaltered gate_rejected items as dont_read?\n\n`
      + 'This appends them to zotero-summarizer-golden.csv as negative training rows. '
      + 'Already-relabelled items are skipped automatically. The next feeds run will retrain.',
    )) return;
    setConfirming(true);
    setMessage('');
    try {
      const result = await reviewConfirmAllGateRejected();
      setMessage(
        `Appended ${result?.appended || 0} dont_read row(s) to golden CSV; `
        + `${result?.skipped_duplicate || 0} already there.`,
      );
      setIsError(false);
    } catch (err) {
      setMessage(`Bulk-confirm failed: ${err.message || err}`);
      setIsError(true);
    } finally {
      setConfirming(false);
    }
  }, [items.length]);

  return (
    <div className="glass rounded-2xl border border-slate-200 p-4">
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <div className="flex items-center gap-2">
          <h2 className="text-lg font-bold">Feed Review</h2>
          <button
            type="button"
            onClick={() => setState('awaiting_review')}
            className={`px-3 py-1 rounded-lg border border-slate-300 text-xs font-semibold ${
              state === 'awaiting_review' ? 'bg-teal-700 text-white' : 'bg-white text-slate-700 hover:bg-slate-100'
            }`}
          >
            Awaiting review
          </button>
          <button
            type="button"
            onClick={() => setState('gate_rejected')}
            className={`px-3 py-1 rounded-lg border border-slate-300 text-xs font-semibold ${
              state === 'gate_rejected' ? 'bg-rose-700 text-white' : 'bg-white text-slate-700 hover:bg-slate-100'
            }`}
          >
            Gate-rejected
          </button>
          {/* Sprint-3+ active-learning sort: 'border' surfaces uncertain
              rows first (composite_score closest to a priority threshold)
              so triaging them maximises model lift per click. */}
          <button
            type="button"
            onClick={() => setSort(sort === 'border' ? 'recent' : 'border')}
            title="Sort by uncertainty (closest to class boundary)"
            className={`px-3 py-1 rounded-lg border border-slate-300 text-xs font-semibold ${
              sort === 'border' ? 'bg-violet-700 text-white' : 'bg-white text-slate-700 hover:bg-slate-100'
            }`}
          >
            🎯 border
          </button>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-slate-500">
            {loading ? 'Loading…' : `${items.length} in queue`}
          </span>
          <button type="button" onClick={() => load(state, sort)}
            className="px-3 py-1 rounded-lg border border-slate-300 text-xs hover:bg-slate-50">
            Refresh
          </button>
          <button
            type="button"
            onClick={handleApplyAll}
            disabled={approvedCount === 0 || applying}
            className={`px-3 py-1 rounded-lg text-xs font-semibold ${
              approvedCount === 0 ? 'bg-slate-200 text-slate-500' : 'bg-teal-700 text-white hover:bg-teal-800'
            }`}
          >
            {applying ? 'Applying…' : `Apply ${approvedCount} approved → Zotero`}
          </button>
        </div>
      </div>

      {state === 'gate_rejected' && (
        <div className="mb-3 p-2 rounded-lg bg-rose-50 border border-rose-200 text-xs text-rose-900">
          <div className="flex items-center justify-between gap-2 flex-wrap">
            <div>
              <strong>Gate-rejected pile</strong> — items the classifier dropped before LLM
              ({items.length} shown). Use <b>relabel</b> to either confirm the verdict (dont) or
              correct false negatives. Approve / Reject are hidden here — relabel does the right
              thing for both training and Zotero.
            </div>
            <button
              type="button"
              onClick={handleConfirmGateRejected}
              disabled={confirming}
              className="px-3 py-1 rounded-lg text-xs font-semibold bg-rose-700 text-white hover:bg-rose-800 disabled:bg-slate-300"
            >
              {confirming ? 'Writing…' : 'Confirm remaining as dont_read'}
            </button>
          </div>
        </div>
      )}

      <StatusBanner message={message} isError={isError} />

      {!loading && items.length === 0 && (
        <div className="text-sm text-slate-500">
          No items awaiting review. Run{' '}
          <span className="mono">zotero-summarizer feeds run --feeds &lt;name&gt;</span> first.
        </div>
      )}

      <div className="space-y-3">
        {items.map((item) => (
          <ReviewItem
            key={item.id}
            item={item}
            state={state}
            localState={itemState[item.id]}
            expanded={Boolean(shapExpanded[item.id])}
            onToggleShap={() => setShapExpanded((prev) => ({ ...prev, [item.id]: !prev[item.id] }))}
            onAction={handleAction}
          />
        ))}
      </div>
    </div>
  );
}
