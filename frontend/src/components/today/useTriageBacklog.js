import { useEffect, useRef } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { triggerTriageBacklog, getTriageStatus } from '../../api/dailyApi.js';

function triageLabel(query, draining, status) {
  if (query.isError) return 'Triage status unavailable';
  if (query.isPending) return 'Checking triage status…';
  return draining ? `Sorting the backlog… ${status?.gate_onward ?? status?.triaged ?? 0} kept` : 'Triage backlog';
}

// The optional feed-backlog triage: polled status, the trigger, and a members refresh when a run ends.
export default function useTriageBacklog(enabled, refresh) {
  const query = useQuery({
    queryKey: ['triage-status'], queryFn: getTriageStatus, enabled,
    refetchInterval: q => q.state.data?.running || q.state.status === 'error' ? 3000 : false,
  });
  const status = query.data;
  const unknown = query.isPending || query.isError;
  const running = !unknown && Boolean(status?.running);
  const mutation = useMutation({ mutationFn: triggerTriageBacklog, onSuccess: () => query.refetch() });
  const draining = mutation.isPending || running;
  const previousRunning = useRef(false);
  useEffect(() => {
    if (unknown) return;
    if (previousRunning.current && !running) refresh();
    previousRunning.current = running;
  }, [running, unknown, refresh]);
  return { query, status, unknown, running, mutation, draining, label: triageLabel(query, draining, status) };
}
