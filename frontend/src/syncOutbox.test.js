// @vitest-environment jsdom
import 'fake-indexeddb/auto';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ request: vi.fn() }));
vi.mock('./api/client.js', () => ({ request: mocks.request }));

beforeEach(async () => {
  vi.resetModules();
  mocks.request.mockReset();
  await new Promise((resolve, reject) => {
    const req = indexedDB.deleteDatabase('zotero-summarizer-offline');
    req.onsuccess = resolve;
    req.onerror = () => reject(req.error);
  });
  let id = 0;
  vi.stubGlobal('crypto', { randomUUID: () => `00000000-0000-4000-8000-${String(++id).padStart(12, '0')}` });
  Object.defineProperty(navigator, 'onLine', { configurable: true, value: true });
});
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it('continues 101 ordered edits after an acknowledged batch and app restart', async () => {
  const offline = await import('./offlineStore.js');
  for (let i = 0; i < 101; i++) {
    await offline.queueMutation({ item_key: 'P1', field: 'review_note', value: `draft ${i}` });
  }
  const original = await offline.pendingMutations();
  let attempt = 0;
  const bodies = [];
  mocks.request.mockImplementation(async (url, options) => {
    if (!url.endsWith('/push')) return { protocol: 1, papers: [], cursor: 101 };
    const body = JSON.parse(options.body);
    bodies.push(body);
    if (++attempt === 2) throw Object.assign(new Error('deadline'), { name: 'AbortError' });
    return { protocol: 1, results: body.mutations.map((row, i) => ({
      mutation_id: row.mutation_id, status: 'applied', applied_revision: attempt === 1 ? i + 1 : 101,
    })) };
  });
  await (await import('./syncClient.js')).syncNow();
  expect((await offline.pendingMutations()).map((r) => r.mutation_id)).toEqual([original[100].mutation_id]);
  vi.resetModules();
  await (await import('./syncClient.js')).syncNow();
  expect(bodies.map((b) => b.mutations.length)).toEqual([100, 1, 1]);
  expect(bodies[2].predecessors).toEqual([original[99].mutation_id]);
  expect(bodies[2].mutations).toEqual([original[100]]);
  expect(await (await import('./offlineStore.js')).pendingMutations()).toEqual([]);
});

it('rejects oversized input before changing the optimistic paper or queue', async () => {
  const offline = await import('./offlineStore.js');
  await offline.savePapers([{ item_key: 'P1', review_note: 'saved', revisions: {} }]);
  await expect(offline.queueMutation({
    item_key: 'P1', field: 'review_note', value: 'x'.repeat(50_001),
  })).rejects.toThrow(/50000/);
  expect((await offline.allPapers())[0].review_note).toBe('saved');
  expect(await offline.pendingMutations()).toEqual([]);
});

it('never retires an outbox row for an unknown acknowledgement status', async () => {
  const offline = await import('./offlineStore.js');
  await offline.queueMutation({ item_key: 'P1', field: 'review_note', value: 'draft' });
  const [row] = await offline.pendingMutations();
  await expect(offline.applyPushResults([{ mutation_id: row.mutation_id, status: 'unknown' }]))
    .rejects.toThrow(/status/);
  expect(await offline.pendingMutations()).toEqual([row]);
});

it('quarantines an old oversized draft without blocking a later valid edit', async () => {
  const offline = await import('./offlineStore.js');
  await offline.queueMutation({ item_key: 'P1', field: 'review_note', value: 'draft' });
  const [old] = await offline.pendingMutations();
  old.value = 'x'.repeat(50_001);
  await new Promise((resolve, reject) => {
    const req = indexedDB.open('zotero-summarizer-offline', 1);
    req.onsuccess = () => {
      const db = req.result;
      const tx = db.transaction('mutations', 'readwrite');
      tx.objectStore('mutations').put(old);
      tx.oncomplete = () => { db.close(); resolve(); };
      tx.onerror = () => reject(tx.error);
    };
    req.onerror = () => reject(req.error);
  });
  await offline.queueMutation({ item_key: 'P2', field: 'review_note', value: 'healthy' });
  let status;
  const listener = (event) => { status = event.detail; };
  window.addEventListener(offline.syncStatusEvent, listener);
  mocks.request.mockImplementation(async (url, options) => {
    if (!url.endsWith('/push')) return { protocol: 1, papers: [], cursor: 1 };
    const body = JSON.parse(options.body);
    expect(body.mutations.map((row) => row.value)).toEqual(['healthy']);
    return { protocol: 1, results: body.mutations.map((row) => ({
      mutation_id: row.mutation_id, status: 'applied', applied_revision: 1,
    })) };
  });
  try {
    await (await import('./syncClient.js')).syncNow();
    expect(mocks.request).toHaveBeenCalledTimes(2);
    expect(await offline.pendingMutations()).toEqual([]);
    expect(status.rejected).toHaveLength(1);
    expect(status.rejected[0]).toMatchObject({ mutation_id: old.mutation_id, value: old.value });
    expect(status.rejected[0].error).toMatch(/50000/);
  } finally {
    window.removeEventListener(offline.syncStatusEvent, listener);
  }
});

it('rolls back row retirement when durable acknowledgement storage aborts', async () => {
  const offline = await import('./offlineStore.js');
  await offline.queueMutation({ item_key: 'P1', field: 'review_note', value: 'draft' });
  const [row] = await offline.pendingMutations();
  const put = IDBObjectStore.prototype.put;
  vi.spyOn(IDBObjectStore.prototype, 'put').mockImplementation(function (value, ...rest) {
    if (this.name === 'meta' && value.key.startsWith('ack:')) {
      this.transaction.abort();
      return;
    }
    return put.call(this, value, ...rest);
  });
  await expect(offline.applyPushResults([{
    mutation_id: row.mutation_id, status: 'applied', applied_revision: 1,
  }])).rejects.toThrow(/aborted/);
  expect(await offline.pendingMutations()).toEqual([row]);
  expect(await offline.pushPredecessors([row])).toEqual([]);
});
