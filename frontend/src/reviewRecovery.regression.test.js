// @vitest-environment jsdom
import 'fake-indexeddb/auto';
import { IDBFactory } from 'fake-indexeddb';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

beforeEach(() => {
  vi.resetModules();
  vi.stubGlobal('indexedDB', new IDBFactory());
  Object.defineProperty(navigator, 'onLine', { configurable: true, value: true });
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it.each(['verdict', 'review_note'])('projects an offline Keep mine %s resolution into both durable views', async (field) => {
  const offline = await import('./offlineStore.js');
  await offline.applyPull({ cursor: 1, papers: [{ item_key: 'P1', revisions: {} }] });
  const value = field === 'verdict' ? 'could_read' : 'Device note';
  await offline.queueMutation({ item_key: 'P1', field, value, comment: field === 'verdict' ? 'Device reason' : null });
  const [original] = await offline.pendingMutations();
  await offline.applyPushResults([{ mutation_id: original.mutation_id, status: 'conflict', conflict_revision: 2,
    canonical: { value: field === 'verdict' ? 'must_read' : 'Server note', source: 'machine' } }]);
  await offline.resolveConflict(original.mutation_id, true);
  const expected = field === 'verdict' ? { user_priority: value, comment: 'Device reason', source: 'user' } : value;
  expect((await offline.allPapers())[0][field]).toEqual(expected);
  expect((await offline.cachedReviewDetail('P1'))[field === 'verdict' ? 'verdict' : 'user_note']).toEqual(expected);
});

it('does not return stale server detail when pending-field visibility cannot be read', async () => {
  const offline = await import('./offlineStore.js');
  await offline.queueMutation({ item_key: 'P1', field: 'review_note', value: 'Durable device note' });
  vi.spyOn(IDBObjectStore.prototype, 'getAll').mockImplementation(() => { throw new DOMException('Read denied', 'UnknownError'); });
  await expect(offline.cacheReviewDetail('P1', { item_key: 'P1', user_note: 'Stale server note' })).rejects.toThrow('Read denied');
});

it('revokes stale connected authority when a queue fetch falls back after transport failure', async () => {
  const offline = await import('./offlineStore.js');
  await offline.applyPull({ cursor: 1, papers: [{ item_key: 'P1', revisions: {} }] });
  await offline.publishStatus('', 'connected');
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Owned listener stopped')));
  expect((await (await import('./api/libraryApi.js')).fetchReadingQueue()).offline).toBe(true);
  expect(offline.isServerAvailable()).toBe(false);
  expect(offline.transportUnavailable()).toBe(true);
});

it('projects verdict changes and retractions into the queue row contract', async () => {
  const offline = await import('./offlineStore.js');
  await offline.applyPull({ cursor: 1, papers: [{ item_key: 'P1', user_priority: 'must_read', revisions: {} }] });
  await offline.queueMutation({ item_key: 'P1', field: 'verdict', value: 'could_read' });
  expect((await offline.allPapers())[0].user_priority).toBe('could_read');
  await offline.queueMutation({ item_key: 'P1', field: 'verdict', operation: 'delete' });
  expect((await offline.allPapers())[0].user_priority).toBeNull();
});

it('does not let an older equal-cursor pull replace a later request across tabs', async () => {
  const offline = await import('./offlineStore.js');
  const first = await offline.beginPull();
  vi.resetModules();
  const second = await (await import('./offlineStore.js')).beginPull();
  expect(second).toBeGreaterThan(first);
  await offline.applyPull({ cursor: 4, papers: [{ item_key: 'NEW', revisions: {} }] }, second);
  await expect(offline.applyPull({ cursor: 4, papers: [{ item_key: 'OLD', revisions: {} }] }, first)).rejects.toThrow();
  expect((await offline.allPapers()).map(row => row.item_key)).toEqual(['NEW']);
  await offline.applyPull({ cursor: 5, papers: [{ item_key: 'NEWER_FIELD_REVISION', revisions: {} }] }, first);
  expect((await offline.allPapers())[0].item_key).toBe('NEWER_FIELD_REVISION');
});

it('drains a save signal received while the previous pull is still in flight', async () => {
  const offline = await import('./offlineStore.js');
  let release;
  const held = new Promise(resolve => { release = resolve; });
  const bodies = [];
  let pulls = 0;
  vi.stubGlobal('fetch', vi.fn(async (url, options) => {
    if (url.includes('/push')) {
      const body = JSON.parse(options.body);
      bodies.push(body);
      return { ok: true, text: async () => JSON.stringify({ protocol: 1, results: body.mutations.map(row => ({
        mutation_id: row.mutation_id, status: 'applied', applied_revision: 2,
      })) }) };
    }
    if (++pulls === 1) await held;
    return { ok: true, text: async () => JSON.stringify({ protocol: 1, cursor: 2, papers: [{ item_key: 'P1', revisions: {} }] }) };
  }));
  const { syncNow } = await import('./syncClient.js');
  const first = syncNow();
  await vi.waitFor(() => expect(pulls).toBe(1));
  try {
    await offline.queueMutation({ item_key: 'P1', field: 'review_note', value: 'Saved during pull' });
    syncNow(); // same event handler used by zs-sync-request
  } finally { release(); }
  await first;
  await vi.waitFor(() => expect(bodies).toHaveLength(1));
  expect(await offline.pendingMutations()).toEqual([]);
});
