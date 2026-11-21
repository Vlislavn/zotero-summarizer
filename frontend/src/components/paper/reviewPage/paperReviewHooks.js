import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useBeforeUnload, useBlocker } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { fetchCollections, fetchPaperRender, fetchSectionSummaries, attachFiguresToZotero } from '../../../api/libraryApi.js';
import usePaperReview from '../../../hooks/usePaperReview.js';
import useKeyboardNav from '../../../hooks/useKeyboardNav.js';
import { readStoredJson } from '../../../utils/safeStorage.js';
import { setTodayReviewContext } from '../../../utils/todayEvents.js';
import { TODAY_MEMBERS_QUERY, TODAY_VISIT_QUERY, finishTodayReviewAction, readTodayVisit, updateTodayVisit,
  startTodayReviewAction, todayPaperUrl } from '../../../utils/todayVisit.js';

// The run just ended but the detail refetch has not delivered the new review: the old, outdated
// one must not flash its "Outdated review" banner meanwhile. Clears when the review changes (or after 5 s).
export function useSettlingAfterRun(running, deep) {
  const [settling, setSettling] = useState(false);
  const was = useRef(false);
  const seen = useRef(deep);
  useEffect(() => {
    if (running) { was.current = true; return undefined; }
    if (was.current) { was.current = false; seen.current = deep; setSettling(true); }
    return undefined;
  }, [running, deep]);
  useEffect(() => {
    if (!settling) return undefined;
    if (deep !== seen.current) { setSettling(false); return undefined; }
    const timer = setTimeout(() => setSettling(false), 5000);
    return () => clearTimeout(timer);
  }, [settling, deep]);
  return settling;
}

export function useUnsavedNotesGuard(dirty) {
  const blocker = useBlocker(useCallback(({ currentLocation, nextLocation }) =>
    dirty && currentLocation.pathname !== nextLocation.pathname, [dirty]));
  useEffect(() => {
    if (blocker.state !== 'blocked') return;
    if (window.confirm('You have unsaved notes on this paper. Leave without saving them?')) blocker.proceed();
    else blocker.reset();
  }, [blocker]);
  useBeforeUnload(useCallback(event => {
    if (!dirty) return;
    event.preventDefault();
    event.returnValue = '';
  }, [dirty]));
}

// Link opens on this page belong to the Today slate it was opened from.
export function useTodayReviewContext(fromToday, visitId, itemKey) {
  useEffect(() => {
    setTodayReviewContext(fromToday && visitId ? { slateId: visitId, itemKey } : null);
    return () => setTodayReviewContext(null);
  }, [fromToday, visitId, itemKey]);
}

// Prev/Next through the ranked list the originating page (Read next) stored in
// localStorage — same tab (useParams re-reads the key, usePaperReview refetches,
// auto-run fires). Re-read per paper so a list refreshed in the other tab is
// picked up. Key not in the order (opened from elsewhere) → idx -1, buttons hide.
export function useReviewOrder(itemKey, fromToday, visitId, queryClient) {
  return useMemo(() => {
    if (fromToday) {
      const visit = queryClient.getQueryData(TODAY_VISIT_QUERY) || readTodayVisit();
      return visit?.id === visitId && !visit.invalid
        ? visit.members.map(member => member.paper.stable_feed_key).filter(Boolean) : [];
    }
    return readStoredJson(
      'zs.reviewOrder',
      [],
      (value) => Array.isArray(value) && value.every((key) => typeof key === 'string'),
    );
    // itemKey dep is deliberate: re-read the stored order on each paper so a list
    // the Read-next tab refreshed mid-session is picked up.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [itemKey, fromToday, visitId, queryClient]);
}

// Bumps on a verdict/note change so the Similar-papers fold re-keys and Today members refetch.
export function useRelatedFence(queryClient) {
  const [relatedRevision, setRelatedRevision] = useState(0);
  const fenceRelatedContext = useCallback(() => {
    setRelatedRevision(value => value + 1);
    queryClient.invalidateQueries({ queryKey: TODAY_MEMBERS_QUERY });
  }, [queryClient]);
  return { relatedRevision, fenceRelatedContext };
}

// j = next, k = prev (auto-disabled while typing in the notes / Ask fields).
export function useReviewNav({ navigate, itemKey, reviewOrder, fromToday, visitId, autoRun }) {
  const navIdx = reviewOrder.indexOf(itemKey);
  const prevKey = navIdx > 0 ? reviewOrder[navIdx - 1] : null;
  const nextKey = navIdx >= 0 && navIdx < reviewOrder.length - 1 ? reviewOrder[navIdx + 1] : null;
  const goToKey = useCallback(
    (key) => {
      if (!key) return;
      navigate(fromToday ? todayPaperUrl(key, visitId) : `/paper/${encodeURIComponent(key)}${autoRun ? '' : '?autoRun=false'}`);
    },
    [navigate, autoRun, fromToday, visitId],
  );
  useKeyboardNav({
    onPrev: () => goToKey(prevKey),
    onNext: () => goToKey(nextKey),
    deps: [prevKey, nextKey],
  });
  return { navIdx, prevKey, nextKey, goToKey };
}

// Attach the extracted figures to the Zotero item (Zotero renders images in
// notes' sibling attachments). One retry through the connector force-gate, like
// the other writes; a forced write only appears after a Zotero restart.
export function useAttachFigures(itemKey) {
  const [attachMsg, setAttachMsg] = useState('');
  const [attaching, setAttaching] = useState(false);
  const onAttachFigures = useCallback(async () => {
    setAttaching(true); setAttachMsg('');
    try {
      let data = await attachFiguresToZotero(itemKey, { force: false });
      if (data?.requires_force) {
        if (!window.confirm("Zotero is open — attach anyway? The figures won't appear until you restart Zotero.")) {
          setAttaching(false); return;
        }
        data = await attachFiguresToZotero(itemKey, { force: true });
      }
      setAttachMsg(data?.updated
        ? `Attached ${data.updated} figure${data.updated > 1 ? 's' : ''} to Zotero ✓`
        : (data?.message || 'Nothing to attach.'));
    } catch (e) {
      setAttachMsg(`Failed: ${e?.message || e}`);
    } finally {
      setAttaching(false);
    }
  }, [itemKey]);
  return { attachMsg, attaching, onAttachFigures };
}

export function useCollectionsQuery() {
  return useQuery({
    queryKey: ['zotero-collections'], queryFn: fetchCollections, staleTime: 5 * 60_000,
  });
}

export function useRenderQuery(itemKey) {
  const renderQuery = useQuery({
    queryKey: ['paper-render', itemKey],
    queryFn: () => fetchPaperRender(itemKey),
    enabled: Boolean(itemKey),
    staleTime: 15_000,
  });
  return renderQuery.data || null;
}

// Paper-map one-liners are built on open, not at review time; poll only while the job runs
// (the interval stops on unmount, on ready/unavailable and on a failed poll).
export function useSectionSummaries(itemKey, mapOverlay) {
  return useQuery({
    queryKey: ['section-summaries', itemKey],
    queryFn: () => fetchSectionSummaries(itemKey),
    enabled: Boolean(itemKey && mapOverlay && !mapOverlay.degraded && (mapOverlay.sections || []).length),
    staleTime: 60_000, retry: false, refetchOnWindowFocus: false,
    refetchInterval: (query) => !query.state.error && query.state.data?.status === 'running' ? 3000 : false,
  });
}

// The paper detail plus the verdict, wired to the Today slate it was opened from.
export function useReviewDetail(itemKey, { queryClient, fromToday, visitId, fenceRelatedContext }) {
  const onVerdictStarted = useCallback((key, priority) => fromToday
    ? startTodayReviewAction(queryClient, visitId, key, priority) : undefined, [fromToday, queryClient, visitId]);
  const onVerdictSettled = useCallback((actionId, result, error) => {
    finishTodayReviewAction(queryClient, actionId, result, error);
  }, [queryClient]);
  return usePaperReview(itemKey, { onVerdictAcknowledged: fenceRelatedContext,
    onVerdictStarted: fromToday ? onVerdictStarted : undefined, onVerdictSettled });
}

// After a run: refetch the detail and drop the paper from the "Not reviewed yet" shelf of the saved visit.
export function useReviewDone(itemKey, refreshDetail, queryClient) {
  return useCallback(() => {
    refreshDetail();
    queryClient.invalidateQueries({ queryKey: TODAY_MEMBERS_QUERY });
    // A paper that now has a review leaves the "Not reviewed yet" shelf of the saved visit.
    updateTodayVisit(queryClient, visit => visit?.unreviewed?.some(row => row.stable_feed_key === itemKey)
      ? { ...visit, unreviewed: visit.unreviewed.filter(row => row.stable_feed_key !== itemKey) } : null);
  }, [refreshDetail, queryClient, itemKey]);
}
