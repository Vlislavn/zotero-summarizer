import { request } from './api/client.js';
import {
  applyPull, applyPushResults, getMeta, pendingMutations, publishStatus,
} from './offlineStore.js';

let running = null;
const SYNC_TIMEOUT_MS = 15_000;

function failureMessage(error) {
  if (error?.message?.startsWith('Sync protocol changed')) return error.message;
  if (error?.name === 'AbortError') return 'Sync timed out';
  return 'Server unavailable';
}

export function syncNow() {
  if (running) return running;
  running = (async () => {
    if (!navigator.onLine) {
      await publishStatus();
      return;
    }
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), SYNC_TIMEOUT_MS);
    try {
      const mutations = await pendingMutations();
      if (mutations.length) {
        const pushed = await request('/api/sync/push', {
          method: 'POST', body: JSON.stringify({ protocol: 1, mutations }),
          signal: controller.signal,
        });
        if (pushed.protocol !== 1) throw new Error('Sync protocol changed; refresh the app');
        await applyPushResults(pushed.results);
      }
      const since = await getMeta('cursor', 0);
      const pulled = await request(`/api/sync/pull?protocol=1&since=${since}`, {
        signal: controller.signal,
      });
      if (pulled.protocol !== 1) throw new Error('Sync protocol changed; refresh the app');
      await applyPull(pulled);
    } finally {
      clearTimeout(timeout);
    }
  })().catch((error) => publishStatus(failureMessage(error))).finally(() => { running = null; });
  return running;
}

export function startSync() {
  window.addEventListener('online', syncNow);
  window.addEventListener('offline', () => publishStatus());
  window.addEventListener('focus', syncNow);
  window.addEventListener('zs-sync-request', syncNow);
  syncNow();
}
