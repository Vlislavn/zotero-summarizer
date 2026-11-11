import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useIsMutating, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { addToLibrary, fetchDailyMembers, fetchDailySlate, submitDailyVerdict, trashPapers, undoDailyVerdict } from '../api/dailyApi.js';
import { readStorage } from '../utils/safeStorage.js';
import {
  TODAY_MEMBERS_QUERY, TODAY_STORAGE_KEY, TODAY_VISIT_QUERY, VISIT_LIMIT,
  actionReply, applyMemberFacts, createTodayVisit, memberNeedsCarry, memberPresentation, readTodayVisit, updateTodayVisit,
} from '../utils/todayVisit.js';

export default function useTodayVisit(enabled) {
  const client = useQueryClient();
  const activeActions = useIsMutating({ mutationKey: ['today-action'] });
  const activeSelections = useIsMutating({ mutationKey: ['today-selection'] });
  const [persistenceFailed, setPersistenceFailed] = useState(() => {
    const cached = client.getQueryData(TODAY_VISIT_QUERY);
    return Boolean(cached && !cached.invalid && readStorage(TODAY_STORAGE_KEY) !== JSON.stringify(cached));
  });
  const visitQuery = useQuery({
    queryKey: TODAY_VISIT_QUERY, queryFn: readTodayVisit, initialData: readTodayVisit,
    enabled: false, staleTime: Infinity, gcTime: Infinity,
  });
  const visit = visitQuery.data;
  const update = useCallback((transform, expectedId) => {
    const result = updateTodayVisit(client, transform, expectedId);
    if (result.saved !== undefined) setPersistenceFailed(!result.saved);
    return result;
  }, [client]);

  useEffect(() => {
    const changed = event => {
      if (event.key === TODAY_STORAGE_KEY || event.key === null) {
        client.setQueryData(TODAY_VISIT_QUERY, readTodayVisit());
      }
    };
    window.addEventListener('storage', changed);
    return () => window.removeEventListener('storage', changed);
  }, [client]);

  const membersQuery = useQuery({
    queryKey: [...TODAY_MEMBERS_QUERY, visit?.id],
    enabled: enabled && Boolean(visit && !visit.invalid && visit.members.length),
    queryFn: async ({ signal }) => {
      const response = await fetchDailyMembers(visit.members.map(member => member.paper.item_id), signal);
      applyMemberFacts(visit, response); // Reject incomplete/foreign response sets before cache publication.
      return response;
    },
    // No refetch on window focus: returning from Safari/another app must NOT flip
    // every card to "Status not current" and disable it (#37/T16). Status refreshes
    // on mount, reconnect, explicit Refresh, and while a carried action resolves.
    refetchOnMount: 'always', refetchOnWindowFocus: false, refetchOnReconnect: 'always',
    refetchInterval: visit?.members.some(memberNeedsCarry) ? 3000 : false,
    // Keep the last result across navigation (stale-while-revalidate): a return from the
    // paper page shows it at once while the mount refetch revalidates. An invalidated
    // entry (verdict/review finished elsewhere, explicit Refresh) is NOT trusted below.
    gcTime: 5 * 60 * 1000,
  });
  const membersInvalidated = Boolean(client.getQueryState([...TODAY_MEMBERS_QUERY, visit?.id])?.isInvalidated);
  useEffect(() => {
    if (membersQuery.data && visit?.id) update(current => applyMemberFacts(current, membersQuery.data), visit.id);
  }, [membersQuery.data, membersQuery.dataUpdatedAt, visit?.id, update]);
  const effectiveVisit = useMemo(() => membersQuery.data && visit && !visit.invalid
    ? applyMemberFacts(visit, membersQuery.data) : visit, [visit, membersQuery.data]);
  const fresh = Boolean(visit && !visit.invalid && (!visit.members.length
    || (membersQuery.isSuccess && (!membersQuery.isFetching || !membersInvalidated) && membersQuery.fetchStatus !== 'paused')));

  const selection = useMutation({
    mutationKey: ['today-selection'],
    mutationFn: previous => client.fetchQuery({
      queryKey: ['today-selection', previous?.id || 'initial'],
      queryFn: () => fetchDailySlate({ K: VISIT_LIMIT, lookback_hours: 168 }),
      staleTime: 0, gcTime: 0, retry: false,
    }),
    onSuccess: (response, previous) => {
      const result = update(current => createTodayVisit(response, current), previous?.id || null);
      if (!result.updated && result.saved === false) throw new Error('New shortlist could not be saved; the previous shortlist was kept.');
    },
    retry: false,
  });
  useEffect(() => {
    if (enabled && !visit && selection.isIdle) selection.mutate(null);
  }, [enabled, visit, selection]);

  const refresh = useCallback(() => client.invalidateQueries({ queryKey: TODAY_MEMBERS_QUERY }), [client]);
  const actionMutation = useMutation({
    mutationKey: ['today-action'],
    mutationFn: request => request.kind === 'add'
      ? addToLibrary(request.ids, request.collectionKey) : trashPapers(request.ids),
    onSuccess: (response, request) => {
      update(current => ({ ...current, members: current.members.map(member => member.action?.id === request.id
        ? { ...member, action: actionReply(member.action, response, member.paper.item_id) } : member) }));
    },
    onError: (error, request) => {
      let code;
      try { code = JSON.parse(error.body)?.error; } catch { /* A missing reply has unknown effects. */ }
      const refused = ['validation_error', 'not_found', 'invalid_collection', 'review_required'].includes(code);
      update(current => ({ ...current, members: current.members.map(member => member.action?.id === request.id
        ? { ...member, action: { ...member.action, phase: refused ? 'refused' : 'unconfirmed', error: error.message } }
        : member) }));
    },
    onSettled: refresh,
    retry: false,
  });
  const act = useCallback((kind, ids, collectionKey = '', retry = false) => {
    if (!fresh || activeActions || activeSelections || !ids.length) return;
    const selected = effectiveVisit.members.filter(member => ids.includes(member.paper.item_id));
    if (selected.length !== ids.length || selected.some(member => {
      const state = memberPresentation(member, fresh);
      return retry ? !state.canRetry || member.action.kind !== kind : !state.selectable;
    })) return;
    const request = { id: crypto.randomUUID(), kind, ids, collectionKey, phase: 'unconfirmed' };
    if (update(current => ({ ...current, members: current.members.map(member => ids.includes(member.paper.item_id)
      ? { ...member, action: request } : member) }), effectiveVisit.id).updated) actionMutation.mutate(request);
  }, [fresh, actionMutation, activeActions, activeSelections, effectiveVisit, update]);

  // Per-card decision (#37): one-tap must/should/could/skip + optional reason chips,
  // written through the Today verdict endpoint; undo retracts the row. The decision
  // persists in the DB (facts.user_priority, shown after the next members refresh)
  // and is reflected optimistically on the card by the mutation's pending state.
  const decisionMutation = useMutation({
    mutationKey: ['today-action'],
    mutationFn: request => request.kind === 'undo'
      ? undoDailyVerdict(request.itemId)
      : submitDailyVerdict({ itemId: request.itemId, priority: request.priority, chips: request.chips }),
    onSettled: refresh,
    retry: false,
  });
  const decide = useCallback((itemId, priority, chips = []) => {
    if (busyRef.current) return;
    decisionMutation.mutate({ kind: 'decide', itemId, priority, chips });
  }, [decisionMutation]);
  const undoDecision = useCallback(itemId => {
    if (busyRef.current) return;
    decisionMutation.mutate({ kind: 'undo', itemId });
  }, [decisionMutation]);

  // Selection (checkbox ticks) lives in the retained visit so it survives a round
  // trip to the paper page and a reload (#37/F07); a new shortlist starts empty.
  const selectedIds = useMemo(() => new Set(effectiveVisit?.selected || []), [effectiveVisit]);
  const setSelected = useCallback(ids => update(current => ({ ...current, selected: [...ids] }), effectiveVisit?.id),
    [update, effectiveVisit?.id]);
  const toggleSelect = useCallback(id => {
    const next = new Set(effectiveVisit?.selected || []);
    if (next.has(id)) next.delete(id); else next.add(id);
    setSelected(next);
  }, [effectiveVisit, setSelected]);

  const carryCount = effectiveVisit?.members.filter(memberNeedsCarry).length || 0;
  const busy = Boolean(activeActions || activeSelections);
  const busyRef = useRef(busy);
  busyRef.current = busy;
  return {
    visit: effectiveVisit, fresh, busy, carryCount, persistenceFailed,
    loading: !visit && selection.isPending, renewing: selection.isPending,
    selectionError: selection.error, statusError: membersQuery.error,
    actionError: actionMutation.error || decisionMutation.error,
    canRenew: enabled && !busy && carryCount < VISIT_LIMIT && (!visit || visit.invalid || fresh),
    renew: () => selection.mutate(effectiveVisit || null), refresh, act,
    retry: member => act(member.action.kind, [member.paper.item_id], member.action.collectionKey, true),
    decide, undoDecision, decisionPending: decisionMutation.isPending,
    selectedIds, toggleSelect, setSelected,
  };
}
