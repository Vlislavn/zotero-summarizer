import { request } from './api/client.js';
import {
  applyPull, applyPushResults, getMeta, mutationError, pendingMutations, publishStatus, pushPredecessors,
} from './offlineStore.js';

let running = null;
const SYNC_TIMEOUT_MS = 15_000;

function failureMessage(error) {
  if (error?.message?.startsWith('Sync protocol changed')) return error.message;
  if (error?.name === 'AbortError') return 'Sync timed out';
  if (error?.status) return `Sync request failed (HTTP ${error.status}): ${error.message}`;
  if (error?.message?.startsWith('Invalid sync')) return error.message;
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
      const pending = await pendingMutations();
      const invalid = pending.map((row) => ({ mutation_id: row.mutation_id, status: 'rejected', error: mutationError(row) }))
        .filter((row) => row.error);
      if (invalid.length) await applyPushResults(invalid);
      const rejected = new Set(invalid.map((row) => row.mutation_id));
      const valid = pending.filter((row) => !rejected.has(row.mutation_id));
      for (let offset = 0; offset < valid.length; offset += 100) {
        if (controller.signal.aborted) throw new DOMException('Sync deadline reached', 'AbortError');
        const mutations = valid.slice(offset, offset + 100);
        const predecessors = await pushPredecessors(mutations);
        const pushed = await request('/api/sync/push', {
          method: 'POST', body: JSON.stringify({ protocol: 1, mutations, predecessors }),
          signal: controller.signal,
        });
        if (pushed.protocol !== 1) throw new Error('Sync protocol changed; refresh the app');
        const ids = new Set(pushed.results?.map((row) => row.mutation_id));
        if (pushed.results?.length !== mutations.length || ids.size !== mutations.length
            || mutations.some((row) => !ids.has(row.mutation_id))) {
          throw new Error('Invalid sync acknowledgement IDs; device changes preserved');
        }
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
