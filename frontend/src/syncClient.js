import { request } from './api/client.js';
import { offlineRequestBudget } from './api/offlineTransport.js';
import {
  applyPull, applyPushResults, beginPull, getMeta, mutationError, pendingMutations, publishStatus, pushPredecessors,
  publishNoResponse, serverBusy,
} from './offlineStore.js';

let running = null;
let requestedAgain = false;
let activeQueryClient = null;
// 502/503/504 (incl. 503 database_busy) answer "alive but overloaded".
const BUSY_STATUSES = [502, 503, 504];
function failureMessage(error, busy, transport) {
  if (busy) return '';
  if (error?.status) return `Sync request failed (HTTP ${error.status}): ${error.message}`;
  if (error?.message?.startsWith('Sync protocol changed') || error?.message?.startsWith('Invalid sync')) return error.message;
  return transport ? 'Server unavailable' : `Sync unavailable: ${error?.message || String(error)}`;
}

export function syncNow(client = activeQueryClient) {
  if (running) { requestedAgain = true; return running; }
  running = (async () => {
    if (!navigator.onLine) {
      await publishStatus();
      await client?.invalidateQueries({ queryKey: ['review-detail'] });
      return;
    }
    const controller = new AbortController();
    let timeout;
    let boundary = 'local';
    try {
      timeout = setTimeout(() => controller.abort(), offlineRequestBudget());
      await publishStatus('', 'syncing');
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
        const wire = mutations.map((row) => {
          const payload = { ...row };
          delete payload.original_intent;
          return payload;
        });
        boundary = 'request';
        const pushed = await request('/api/sync/push', {
          method: 'POST', body: JSON.stringify({ protocol: 1, mutations: wire, predecessors }), signal: controller.signal,
          idempotent: true, // mutation ids make a replayed push a no-op
        });
        boundary = 'local';
        if (pushed?.protocol !== 1) throw new Error('Sync protocol changed; refresh the app');
        const ids = new Set(pushed.results?.map?.((row) => row?.mutation_id));
        if (!Array.isArray(pushed.results) || pushed.results.length !== mutations.length || ids.size !== mutations.length
            || mutations.some((row) => !ids.has(row.mutation_id))) {
          throw new Error('Invalid sync acknowledgement IDs; device changes preserved');
        }
        await applyPushResults(pushed.results);
      }
      const since = await getMeta('cursor', 0);
      const sequence = await beginPull();
      boundary = 'request';
      const pulled = await request(`/api/sync/pull?protocol=1&since=${since}`, { signal: controller.signal });
      boundary = 'local';
      if (pulled?.protocol !== 1) throw new Error('Sync protocol changed; refresh the app');
      await applyPull(pulled, sequence);
      const recovered = serverBusy();
      await publishStatus('', 'connected');
      if (recovered) await client?.invalidateQueries({ queryKey: ['deep-review-reachability'] });
      await client?.invalidateQueries({ queryKey: ['review-detail'] });
    } catch (error) {
      const received = Boolean(error.status || error.responseReceived);
      const transport = boundary === 'request' && error.transportFailure === true && !received;
      const silent = boundary === 'request' && !received && (controller.signal.aborted || error.noResponse === true);
      const busy = silent || (boundary === 'request' && BUSY_STATUSES.includes(error.status));
      if (silent) await publishNoResponse(error);
      else await publishStatus(failureMessage(error, busy, transport), busy ? 'busy' : transport ? 'unavailable' : 'blocked');
    } finally {
      clearTimeout(timeout);
    }
  })().finally(async () => {
    running = null;
    if (requestedAgain) {
      requestedAgain = false;
      await syncNow(client);
    }
  });
  return running;
}

export function startSync(client) {
  activeQueryClient = client;
  const onSync = () => syncNow();
  window.addEventListener('online', onSync);
  window.addEventListener('offline', () => publishStatus());
  window.addEventListener('focus', onSync);
  window.addEventListener('zs-sync-request', onSync);
  syncNow();
}
