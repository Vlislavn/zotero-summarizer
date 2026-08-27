import { request } from './api/client.js';
import {
  applyPull, applyPushResults, getMeta, pendingMutations, publishStatus,
} from './offlineStore.js';

let running = null;

export function syncNow() {
  if (running) return running;
  running = (async () => {
    if (!navigator.onLine) {
      await publishStatus();
      return;
    }
    const mutations = await pendingMutations();
    if (mutations.length) {
      const pushed = await request('/api/sync/push', {
        method: 'POST', body: JSON.stringify({ protocol: 1, mutations }),
      });
      if (pushed.protocol !== 1) throw new Error('Sync protocol changed; refresh the app');
      await applyPushResults(pushed.results);
    }
    const since = await getMeta('cursor', 0);
    const pulled = await request(`/api/sync/pull?protocol=1&since=${since}`);
    if (pulled.protocol !== 1) throw new Error('Sync protocol changed; refresh the app');
    await applyPull(pulled);
  })().catch(() => publishStatus('Server unavailable')).finally(() => { running = null; });
  return running;
}

export function startSync() {
  window.addEventListener('online', syncNow);
  window.addEventListener('offline', () => publishStatus());
  window.addEventListener('focus', syncNow);
  window.addEventListener('zs-sync-request', syncNow);
  syncNow();
}
