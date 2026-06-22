import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  fetchPending,
  applyPending,
  rejectPending,
  savePendingChangeEdit,
  fetchCollections,
} from '../api/pendingApi.js';
import {
  STATUS_TABS,
  flattenCollections,
  buildDraft,
  buildPayloadFromDraft,
} from './pendingHelpers.js';
import { StatusBanner, ChangeGroup } from './pendingComponents.jsx';
import { humanizeError } from '../utils/humanizeError.js';
import Async from '../components/ui/Async.jsx';
import { useKeyboardNav } from '../hooks/useKeyboardNav.js';
import { useOptimisticAction } from '../hooks/useOptimisticAction.js';
import { useFocusOnChange } from '../hooks/useFocusOnChange.js';

// Pending Changes page — port of the `activeTab === 'pending'` block from
// zotero_summarizer/web/ui.html. Provides functional parity:
//   - filter by status (pending / applied / rejected / failed)
//   - select / apply / reject batches of pending rows
//   - inline edit drafts for tag_changes, add/remove_from_collection, add_note
// Plus power-tool interaction parity with the Annotate surface (Phase C):
//   - j/k move the active change-group, space/enter toggle its checkboxes,
//     `a` apply selected, `r` reject selected (the shared useKeyboardNav hook
//     owns the typing/modifier guards)
//   - optimistic status flip on apply/reject, rolled back on error
//   - focus follows the active group so keyboard nav stays anchored
// Helpers extracted to ./pendingHelpers.js and the presentational
// subcomponents (StatusBanner / ChangeEditor / ChangeGroup) to
// ./pendingComponents.jsx to keep this file under budget.

export default function Pending() {
  const [status, setStatus] = useState('pending');
  const [pending, setPending] = useState([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState(null);
  const [selected, setSelected] = useState(() => new Set());
  const [drafts, setDrafts] = useState({});
  const [saving, setSaving] = useState({});
  const [message, setMessage] = useState('');
  const [isError, setIsError] = useState(false);
  const [flatCollections, setFlatCollections] = useState([]);
  // Index of the keyboard-active change-group within `groupedPending`.
  const [activeIdx, setActiveIdx] = useState(0);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await fetchCollections();
        if (!cancelled) setFlatCollections(flattenCollections(data?.items || []));
      } catch {
        // Non-fatal — only affects the collection-edit dropdown.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const load = useCallback(async (currentStatus = status) => {
    setLoading(true);
    setLoadError(null);
    try {
      const data = await fetchPending({ status: currentStatus, limit: 500 });
      const items = data?.items || [];
      setPending(items);
      setSelected((prev) => {
        const ids = new Set(items.map((i) => i.id));
        const next = new Set();
        for (const id of prev) if (ids.has(id)) next.add(id);
        return next;
      });
    } catch (err) {
      setLoadError(err);
      setPending([]);
    } finally {
      setLoading(false);
    }
  }, [status]);

  useEffect(() => { load(status); }, [load, status]);

  const setStatusAndReset = useCallback((next) => {
    setStatus(next);
    setSelected(new Set());
    setDrafts({});
    setMessage('');
    setIsError(false);
    setActiveIdx(0);
  }, []);

  const groupedPending = useMemo(() => {
    const map = new Map();
    for (const item of pending) {
      if (!map.has(item.item_key)) {
        map.set(item.item_key, {
          item_key: item.item_key,
          item_title: item.item_title,
          changes: [],
        });
      }
      map.get(item.item_key).changes.push(item);
    }
    return [...map.values()];
  }, [pending]);

  // Keep the active index inside the (possibly shrunk) list after a reload.
  useEffect(() => {
    setActiveIdx((idx) => {
      if (groupedPending.length === 0) return 0;
      return Math.min(idx, groupedPending.length - 1);
    });
  }, [groupedPending.length]);

  const selectedLabel = useMemo(() => {
    const row = STATUS_TABS.find((t) => t.value === status);
    return row ? row.label.toLowerCase() : status;
  }, [status]);

  const isPending = status === 'pending';

  // Focus follows the active group so the keyboard user's attention and the
  // browser focus stay together. Keyed on the active group's IDENTITY (its
  // item_key), not just the index: j/k change the index → identity changes; an
  // apply/reject drops that group from the list so the same index now points at
  // the next group → identity changes again, moving focus onto it.
  const activeGroupKey = groupedPending[activeIdx]?.item_key ?? null;
  const activeGroupRef = useFocusOnChange(activeGroupKey);

  function toggleOne(id) {
    if (!isPending) return;
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  function selectAll(all) {
    if (!isPending) return;
    setSelected(all ? new Set(pending.map((i) => i.id)) : new Set());
  }

  // Toggle every change in the active group: select them all unless they are
  // already all selected, in which case clear them (a single space/enter chord
  // both checks and unchecks the active card).
  const toggleActiveGroup = useCallback(() => {
    if (!isPending) return;
    const group = groupedPending[activeIdx];
    if (!group) return;
    const ids = group.changes.map((c) => c.id);
    setSelected((prev) => {
      const allOn = ids.every((id) => prev.has(id));
      const next = new Set(prev);
      for (const id of ids) {
        if (allOn) next.delete(id); else next.add(id);
      }
      return next;
    });
  }, [isPending, groupedPending, activeIdx]);

  async function handleSaveEdit(change) {
    if (!isPending) {
      setMessage('Switch to Pending status before editing.');
      setIsError(true);
      return;
    }
    const payload = buildPayloadFromDraft(change, drafts[change.id], flatCollections);
    if (!payload) {
      setMessage('This change type cannot be edited here.');
      setIsError(true);
      return;
    }
    setSaving((prev) => ({ ...prev, [change.id]: true }));
    try {
      const data = await savePendingChangeEdit(change.id, payload);
      setMessage('Pending change updated.');
      setIsError(false);
      setDrafts((prev) => ({
        ...prev,
        [change.id]: buildDraft({ ...change, payload_json: data?.payload }),
      }));
      await load(status);
    } catch (err) {
      setMessage(err?.message ? humanizeError(err) : 'Failed to save pending change.');
      setIsError(true);
    } finally {
      setSaving((prev) => ({ ...prev, [change.id]: false }));
    }
  }

  // ---------- Optimistic apply / reject ----------
  // The shared useOptimisticAction hook wants a React-Query-style
  // `mutate(vars, {onSuccess, onError})`; adapt the plain async API call into
  // that shape. The optimistic step flips the affected rows' status locally and
  // advances the active group so the next pending item is ready under the hand;
  // rollback restores the previous rows + selection and surfaces the error.

  const flipStatus = useCallback((ids, nextStatus) => {
    const idSet = new Set(ids);
    setPending((prev) =>
      prev.map((row) => (idSet.has(row.id) ? { ...row, status: nextStatus } : row)));
  }, []);

  const makeMutate = useCallback(
    (apiCall) => (variables, { onSuccess, onError }) => {
      apiCall(variables).then(onSuccess).catch(onError);
    },
    [],
  );

  const runApply = useOptimisticAction({
    mutate: makeMutate(({ ids, force }) => applyPending(ids, { force })),
    optimisticUpdate: (_vars, ctx) => {
      flipStatus(ctx.ids, 'applied');
      setSelected(new Set());
      setMessage('Applying selected changes…');
      setIsError(false);
    },
    onSuccess: (data, vars, ctx) => {
      if (data?.error === 'zotero_running' && !ctx.force) {
        if (window.confirm('Zotero appears to be running. Apply anyway?')) {
          // Re-run with force; the rows already read "applied" optimistically.
          runApply({ ids: ctx.ids, force: true }, { context: { ids: ctx.ids, force: true } });
        } else {
          flipStatus(ctx.ids, 'pending');
          setSelected(new Set(ctx.ids));
          setMessage('Apply cancelled — Zotero is running.');
        }
        return;
      }
      const inboxRemoved = Number(data?.inbox_removed || 0);
      setMessage(
        inboxRemoved > 0
          ? `Selected changes applied. Removed from Inbox: ${inboxRemoved}.`
          : 'Selected changes applied.',
      );
      setIsError(false);
      load(status);
    },
    rollback: (err, _vars, ctx) => {
      flipStatus(ctx.ids, 'pending');
      setSelected(new Set(ctx.ids));
      setMessage(humanizeError(err));
      setIsError(true);
    },
  });

  const runReject = useOptimisticAction({
    mutate: makeMutate(({ ids }) => rejectPending(ids)),
    optimisticUpdate: (_vars, ctx) => {
      flipStatus(ctx.ids, 'rejected');
      setSelected(new Set());
      setMessage('Rejecting selected changes…');
      setIsError(false);
    },
    onSuccess: () => {
      setMessage('Selected changes rejected.');
      setIsError(false);
      load(status);
    },
    rollback: (err, _vars, ctx) => {
      flipStatus(ctx.ids, 'pending');
      setSelected(new Set(ctx.ids));
      setMessage(humanizeError(err));
      setIsError(true);
    },
  });

  const handleApply = useCallback(() => {
    if (!isPending) {
      setMessage('Only pending rows can be applied.');
      setIsError(true);
      return;
    }
    if (!selected.size) return;
    const ids = [...selected];
    runApply({ ids, force: false }, { context: { ids, force: false } });
  }, [isPending, selected, runApply]);

  const handleReject = useCallback(() => {
    if (!isPending) {
      setMessage('Only pending rows can be rejected.');
      setIsError(true);
      return;
    }
    if (!selected.size) return;
    const ids = [...selected];
    runReject({ ids }, { context: { ids } });
  }, [isPending, selected, runReject]);

  // ---------- Keyboard shortcuts ----------
  // j/k move the active group; space/enter toggle its checkboxes; a/r apply or
  // reject the current selection. The shared useKeyboardNav hook disables itself
  // while the user is typing in an input/textarea and ignores meta/ctrl/alt
  // chords, so the inline edit fields keep their own keys.
  const ACTION_KEYS = useMemo(
    () => ({ ' ': 'toggle', Enter: 'toggle', a: 'apply', r: 'reject' }),
    [],
  );
  const onAction = useCallback(
    (mapped) => {
      if (mapped === 'toggle') toggleActiveGroup();
      else if (mapped === 'apply') handleApply();
      else if (mapped === 'reject') handleReject();
    },
    [toggleActiveGroup, handleApply, handleReject],
  );
  useKeyboardNav({
    onPrev: () => setActiveIdx((i) => Math.max(0, i - 1)),
    onNext: () =>
      setActiveIdx((i) => Math.min(Math.max(0, groupedPending.length - 1), i + 1)),
    onAction,
    actionKeys: ACTION_KEYS,
    hasSelection: isPending && groupedPending.length > 0,
    enabled: isPending,
    deps: [groupedPending.length, onAction, isPending],
  });

  return (
    <div className="glass rounded-2xl border border-slate-200 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
        <div>
          <h2 className="text-lg font-bold">Pending Changes</h2>
          <p className="text-xs text-slate-500">Review, edit, and apply queued updates to Zotero.</p>
          {isPending && (
            <p className="text-[11px] text-slate-500 mt-0.5">
              <kbd>j</kbd>/<kbd>k</kbd> move · <kbd>space</kbd> select · <kbd>a</kbd> apply · <kbd>r</kbd> reject
            </p>
          )}
        </div>
        {/* Batch controls only on the Pending tab — Applied/Rejected/Failed are
            pure read-only history, so no inert affordances + no scold banner. */}
        {isPending && (
          <div className="flex items-center gap-2 text-sm">
            <button type="button" onClick={() => selectAll(true)}
              className="px-2 py-1 rounded bg-slate-100 hover:bg-slate-200">Select all</button>
            <button type="button" onClick={() => selectAll(false)}
              className="px-2 py-1 rounded bg-slate-100 hover:bg-slate-200">Clear</button>
            <button
              type="button"
              onClick={handleApply}
              disabled={selected.size === 0}
              className="px-3 py-1.5 rounded bg-green-700 text-white hover:bg-green-800 disabled:bg-slate-300 disabled:text-slate-500"
            >
              Apply selected
            </button>
            <button
              type="button"
              onClick={handleReject}
              disabled={selected.size === 0}
              className="px-3 py-1.5 rounded bg-amber-600 text-white hover:bg-amber-700 disabled:bg-slate-300 disabled:text-slate-500"
            >
              Reject selected
            </button>
          </div>
        )}
      </div>

      <div className="flex flex-wrap gap-2 mb-3">
        {STATUS_TABS.map((tab) => (
          <button
            type="button"
            key={tab.value}
            onClick={() => setStatusAndReset(tab.value)}
            className={`px-3 py-1.5 rounded-lg border text-xs font-semibold ${
              status === tab.value
                ? 'bg-teal-700 text-white border-teal-700'
                : 'bg-white text-slate-700 border-slate-200 hover:bg-slate-50'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <StatusBanner message={message} isError={isError} />

      <Async
        loading={loading}
        error={loadError}
        empty={!loading && !loadError && groupedPending.length === 0}
        loadingText="Loading changes…"
        emptyMessage={`No ${selectedLabel} changes.`}
      >
        {groupedPending.map((group, idx) => (
          <ChangeGroup
            key={group.item_key}
            group={group}
            isActive={isPending && idx === activeIdx}
            groupRef={activeGroupRef}
            isPending={isPending}
            selected={selected}
            toggleOne={toggleOne}
            drafts={drafts}
            setDrafts={setDrafts}
            flatCollections={flatCollections}
            saving={saving}
            onSaveEdit={handleSaveEdit}
          />
        ))}
      </Async>
    </div>
  );
}
