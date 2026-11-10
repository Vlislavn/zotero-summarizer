import { useCallback, useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { runDeepReview, fetchDeepReviewStatus } from '../api/libraryApi.js';
import { fetchLlmReachability } from '../api/settingsApi.js';
import { fetchPaperPrefs } from '../api/paperPrefs.js';
import useServerAvailable from './useServerAvailable.js';

// One per-paper poll for both review surfaces. Query keys isolate navigation;
// a failed status request ends the spinner and leaves an explicit retry action.
export default function useDeepReviewRunner(itemKey, { deep, onDone, autoRun = false } = {}) {
  const client = useQueryClient();
  const serverAvailable = useServerAvailable();
  const reviewServerAvailable = useServerAvailable({ allowSyncFailure: true });
  const autoRunFiredFor = useRef(null);
  const lastRunning = useRef(null);
  const [online, setOnline] = useState(() => navigator.onLine);
  useEffect(() => {
    const disconnected = () => setOnline(false);
    const reconnected = () => {
      setOnline(true);
      client.invalidateQueries({ queryKey: ['deep-review-status', itemKey] });
      client.invalidateQueries({ queryKey: ['deep-review-reachability'] });
    };
    window.addEventListener('offline', disconnected);
    window.addEventListener('online', reconnected);
    return () => {
      window.removeEventListener('offline', disconnected);
      window.removeEventListener('online', reconnected);
    };
  }, [client, itemKey]);
  const statusQuery = useQuery({
    queryKey: ['deep-review-status', itemKey],
    queryFn: () => fetchDeepReviewStatus(itemKey),
    enabled: Boolean(itemKey),
    retry: false,
    refetchOnWindowFocus: false,
    refetchInterval: (query) => query.state.status !== 'error' && query.state.data?.status === 'running' ? 3000 : false,
  });
  const reachability = useQuery({
    queryKey: ['deep-review-reachability'],
    queryFn: fetchLlmReachability,
    retry: false,
    staleTime: 30_000,
  });
  // Auto review on open is opt-in (ZS_PAPER_AUTO_REVIEW, default OFF). Only probed
  // when the caller even wants auto-run, so the manual-Generate path costs nothing.
  const prefs = useQuery({
    queryKey: ['paper-auto-review'],
    queryFn: fetchPaperPrefs,
    enabled: autoRun,
    retry: false,
    staleTime: 300_000,
  });
  const llm = (reachability.data?.stages || []).find((stage) => stage.stage === 'deep_review') || null;
  // A rejected Zotero sync is not a transport outage. Native-write consumers
  // retain the strict serverAvailable gate; unknown job status stays blocked below.
  const llmAvailable = online && reviewServerAvailable && reachability.isSuccess && Boolean(llm)
    && llm.enabled !== false && llm.reachable === true;
  const { mutate, error: runError, isPending: starting, variables } = useMutation({
    mutationFn: runDeepReview,
    onMutate: (request) => client.cancelQueries({ queryKey: ['deep-review-status', request.itemKey] }),
    onSuccess: (data, request) => {
      client.setQueryData(['deep-review-status', request.itemKey], data);
      if (request.itemKey === itemKey && data.status !== 'running') onDone?.();
    },
  });
  const run = useCallback((options = {}) => mutate({ ...options, itemKey }), [mutate, itemKey]);
  const status = statusQuery.isError
    ? { status: 'unavailable', error: `Could not refresh review status: ${statusQuery.error.message}` }
    : statusQuery.data || { status: 'idle' };
  const running = status.status === 'running' || (starting && variables?.itemKey === itemKey);
  const reviewed = Boolean(deep && !deep.needs_pdf && (deep.digest || deep.quality || deep.goal_summaries?.length));

  useEffect(() => {
    if (status.status === 'running') {
      lastRunning.current = itemKey;
      autoRunFiredFor.current = itemKey;
    } else if (lastRunning.current === itemKey && !statusQuery.isError) {
      lastRunning.current = null;
      onDone?.();
    }
  }, [itemKey, status.status, statusQuery.isError, onDone]);

  useEffect(() => {
    if (!autoRun || !itemKey || reachability.isPending || !statusQuery.isSuccess) return;
    if (prefs.data?.auto_review !== true) return;  // Strict opt-in gate (GET /api/paper/prefs).
    if (reviewed || running || deep?.needs_pdf || status.status !== 'idle') return;
    if (!llmAvailable || autoRunFiredFor.current === itemKey) return;
    autoRunFiredFor.current = itemKey;
    run();
  }, [autoRun, itemKey, reachability.isPending, statusQuery.isSuccess, prefs.data, reviewed, running, deep, status.status, llmAvailable, run]);

  const retryStatus = useCallback(() => {
    autoRunFiredFor.current = itemKey;
    return statusQuery.refetch();
  }, [itemKey, statusQuery.refetch]);
  const retryModelCheck = useCallback(() => {
    autoRunFiredFor.current = itemKey; // Metadata recovery is not generation consent.
    return reachability.refetch();
  }, [itemKey, reachability.refetch]);

  return { status, error: variables?.itemKey === itemKey ? runError?.message || null : null,
    llm, llmAvailable, online, serverAvailable, llmChecked: reachability.isSuccess, running, run,
    retryStatus, checkingStatus: statusQuery.isFetching, retryModelCheck,
    modelCheckPending: reachability.isPending, modelCheckError: reachability.isError,
    checkingModel: reachability.isFetching };
}
