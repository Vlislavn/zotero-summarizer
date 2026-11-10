import { useQuery } from '@tanstack/react-query';
import { feedbackAnalysisApi } from '../api/feedbackAnalysisApi.js';

export const STATUS_KEY = ['feedback-analysis-status'];
const POLL_MS = 2000;

export function useFeedbackStatus() {
  return useQuery({ queryKey: STATUS_KEY, queryFn: feedbackAnalysisApi.status, retry: false,
    refetchInterval: (q) => (!q.state.error && q.state.data?.job?.state === 'running' ? POLL_MS : false) });
}
