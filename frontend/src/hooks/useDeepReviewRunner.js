import { useCallback, useEffect, useRef } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { runDeepReview, fetchDeepReviewStatus } from '../api/libraryApi.js';
import { fetchLlmReachability } from '../api/settingsApi.js';

// One per-paper poll for both review surfaces. Query keys isolate navigation;
// a failed status request ends the spinner and leaves an explicit retry action.
export default function useDeepReviewRunner(itemKey, { deep, onDone, autoRun = false } = {}) {
  const client = useQueryClient();
  const autoRunFiredFor = useRef(null);
  const lastRunning = useRef(null);
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
  const llm = (reachability.data?.stages || []).find((stage) => stage.stage === 'deep_review') || null;
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
    ? { status: 'error', error: `Could not refresh review status: ${statusQuery.error.message}` }
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
    if (reviewed || running || deep?.needs_pdf || status.status !== 'idle') return;
    if (llm?.enabled === false || llm?.reachable === false || autoRunFiredFor.current === itemKey) return;
    autoRunFiredFor.current = itemKey;
    run();
  }, [autoRun, itemKey, reachability.isPending, statusQuery.isSuccess, reviewed, running, deep, status.status, llm, run]);

  return { status, error: variables?.itemKey === itemKey ? runError?.message || null : null,
    llm, llmChecked: !reachability.isPending, running, run };
}
