import { useEffect, useRef } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { telegramFeedApi } from '../api/researchFeedApi.js';

// The server-side «Check channels» job: status polling + start. Until the collection consent is given the one
// button also grants it (the consent line is shown by <RunStatus>), afterwards it only runs the job.
export function useTelegramRun() {
  const cache = useQueryClient();
  const status = useQuery({ queryKey: ['telegram-status'], queryFn: telegramFeedApi.status, retry: false, networkMode: 'always',
    refetchInterval: (query) => (query.state.data?.running || query.state.status === 'error' ? 1500 : false) });
  const value = status.data;
  const running = Boolean(value?.running);
  const wasRunning = useRef(false);
  useEffect(() => {
    if (wasRunning.current && !running) cache.invalidateQueries({ queryKey: ['telegram-today'] });
    wasRunning.current = running;
  }, [running, cache]);
  const start = useMutation({
    mutationFn: async (grant) => { if (grant) await telegramFeedApi.collectConsent(true); return telegramFeedApi.run(); },
    onSuccess: (next) => cache.setQueryData(['telegram-status'], next),
    onError: () => cache.invalidateQueries({ queryKey: ['telegram-status'] }),  // a consent saved before a failed start
  });
  return { status, value, start, running, needsConsent: Boolean(value) && !value.consented && !running };
}
