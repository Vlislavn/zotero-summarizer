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

it('retries a keep-server UUID without echoing canonical content or local recovery metadata', async () => {
  const offline = await import('./offlineStore.js');
  const canonical = 'Long server body '.repeat(4000);
  await offline.queueMutation({ item_key: 'P1', field: 'review_note', value: 'Private device intent' });
  const [original] = await offline.pendingMutations();
  await offline.applyPushResults([{ mutation_id: original.mutation_id, status: 'conflict', conflict_revision: 8,
    canonical: { value: canonical } }]);
  await offline.resolveConflict(original.mutation_id, false);
  const before = await offline.pendingMutations();
  const bodies = [];
  mocks.request.mockImplementation(async (url, options) => {
    if (!url.endsWith('/push')) return { protocol: 1, cursor: 8, papers: [{ item_key: 'P1', review_note: canonical,
      revisions: { review_note: 8 } }] };
    const body = JSON.parse(options.body);
    bodies.push(body);
    expect(body.mutations[0]).toMatchObject({ mutation_id: before[0].mutation_id, operation: 'keep_server', value: null, comment: null });
    expect(body.mutations[0].original_intent).toBeUndefined();
    if (bodies.length === 1) throw Object.assign(new TypeError('Owned response lost'), { transportFailure: true });
    return { protocol: 1, results: [{ mutation_id: before[0].mutation_id, status: 'already_applied', applied_revision: 8 }] };
  });
  await (await import('./syncClient.js')).syncNow();
  expect(await offline.pendingMutations()).toEqual(before);
  vi.resetModules();
  await (await import('./syncClient.js')).syncNow();
  expect(bodies).toHaveLength(2);
  expect(bodies[1]).toEqual(bodies[0]);
  expect(await (await import('./offlineStore.js')).pendingMutations()).toEqual([]);
});

it('keeps the latest durable pending edit visible after an earlier conflict batch and later transport failure', async () => {
  const offline = await import('./offlineStore.js');
  await offline.applyPull({ cursor: 4, papers: [{ item_key: 'P1', title: 'Paper', review_note: 'Initial', revisions: { review_note: 4 } }] });
  for (let i = 0; i < 101; i++) await offline.queueMutation({ item_key: 'P1', field: 'review_note', value: `device draft ${i}` });
  const original = await offline.pendingMutations();
  let attempt = 0;
  mocks.request.mockImplementation(async (url, options) => {
    expect(url).toBe('/api/sync/push');
    const body = JSON.parse(options.body);
    if (++attempt === 2) throw Object.assign(new TypeError('Owned second batch lost'), { transportFailure: true });
    return { protocol: 1, results: body.mutations.map(row => ({ mutation_id: row.mutation_id, status: 'conflict',
      conflict_revision: 8, canonical: { value: 'Changed on the server' } })) };
  });
  await (await import('./syncClient.js')).syncNow();
  expect(attempt).toBe(2);
  expect(await offline.pendingMutations()).toEqual([original[100]]);
  vi.resetModules();
  const reopened = await import('./offlineStore.js');
  expect((await reopened.allPapers())[0].review_note).toBe('device draft 100');
  expect((await reopened.cachedResponse('review:P1')).user_note).toBe('device draft 100');
  expect(await reopened.pendingMutations()).toEqual([original[100]]);
});

it.each([
  ['verdict', 'review_note', 'could_read', 'Remote note'],
  ['review_note', 'verdict', 'could_read', 'Remote note'],
  ['verdict', 'review_note', null, null],
  ['review_note', 'verdict', null, null],
])('retains both canonical fields/revisions after %s then %s conflicts and a lost pull', async (first, second, priority, note) => {
  const offline = await import('./offlineStore.js');
  await offline.applyPull({ cursor: 4, papers: [{ item_key: 'P1', title: 'Paper', review_note: 'Initial',
    verdict: { user_priority: 'should_read', comment: 'Initial', source: 'user' }, revisions: { verdict: 4, review_note: 4 } }] });
  for (const field of [first, second]) await offline.queueMutation({ item_key: 'P1', field,
    value: field === 'verdict' ? 'must_read' : 'Device note', comment: field === 'verdict' ? 'Device judgement' : null });
  const original = await offline.pendingMutations();
  const canonical = { verdict: { value: priority, comment: 'Remote judgement', source: 'machine' }, review_note: { value: note } };
  const failPull = vi.fn(() => { throw Object.assign(new TypeError('Owned pull lost'), { transportFailure: true }); });
  mocks.request.mockImplementation(async (url, options) => {
    if (new URL(url, 'http://owned.invalid').pathname === '/api/sync/pull') return failPull();
    expect(url).toBe('/api/sync/push');
    const body = JSON.parse(options.body);
    return { protocol: 1, results: body.mutations.map(row => ({ mutation_id: row.mutation_id, status: 'conflict',
      conflict_revision: row.field === 'verdict' ? 8 : 9, canonical: canonical[row.field] })) };
  });
  await (await import('./syncClient.js')).syncNow();
  expect(mocks.request).toHaveBeenCalledTimes(2);
  expect(failPull).toHaveBeenCalledOnce();
  vi.resetModules();
  const reopened = await import('./offlineStore.js');
  const verdict = priority === null ? null : { user_priority: priority, comment: 'Remote judgement', source: 'machine' };
  expect((await reopened.allPapers())[0]).toMatchObject({ verdict, review_note: note, revisions: { verdict: 8, review_note: 9 } });
  expect(await reopened.cachedResponse('review:P1')).toMatchObject({ verdict, user_note: note });
  let status;
  const listener = event => { status = event.detail; };
  window.addEventListener(reopened.syncStatusEvent, listener);
  try {
    await reopened.publishStatus();
    expect(status.conflicts).toHaveLength(2);
    for (const row of original) expect(status.conflicts.find(r => r.mutation_id === row.mutation_id))
      .toMatchObject({ ...row, status: 'conflict', canonical: canonical[row.field], conflict_revision: row.field === 'verdict' ? 8 : 9 });
  } finally { window.removeEventListener(reopened.syncStatusEvent, listener); }
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
