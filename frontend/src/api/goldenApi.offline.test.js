// @vitest-environment jsdom
import 'fake-indexeddb/auto';
import { IDBFactory } from 'fake-indexeddb';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

beforeEach(() => { vi.resetModules(); vi.stubGlobal('indexedDB', new IDBFactory()); });
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it('keeps pending local fields while adopting newly fetched paper context', async () => {
  const offline = await import('../offlineStore.js');
  await offline.savePapers([{ item_key: 'P1', revisions: { verdict: 4, review_note: 3 } }]);
  await offline.queueMutation({ item_key: 'P1', field: 'verdict', value: 'could_read', comment: 'Device judgement' });
  await offline.queueMutation({ item_key: 'P1', field: 'review_note', value: 'Device note' });
  const pending = await offline.pendingMutations();
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({
    item_key: 'P1', title: 'Fresh context', verdict: { user_priority: 'must_read' }, user_note: 'Old server note',
  }) }));
  const { fetchReviewDetail } = await import('./goldenApi.js');
  expect(await fetchReviewDetail('P1')).toMatchObject({ title: 'Fresh context', user_note: 'Device note',
    verdict: { user_priority: 'could_read', comment: 'Device judgement' } });
  expect(await offline.cachedResponse('review:P1')).toMatchObject({ title: 'Fresh context', user_note: 'Device note',
    verdict: { user_priority: 'could_read', comment: 'Device judgement' } });
  expect(await offline.pendingMutations()).toEqual(pending);
});

it('preserves a local save committed while the server detail response was in flight', async () => {
  const offline = await import('../offlineStore.js');
  await offline.savePapers([{ item_key: 'P1', revisions: { review_note: 3 } }]);
  let release;
  const response = new Promise((resolve) => { release = resolve; });
  vi.stubGlobal('fetch', vi.fn().mockReturnValue(response));
  const { fetchReviewDetail } = await import('./goldenApi.js');
  const reading = fetchReviewDetail('P1');
  try {
    await offline.queueMutation({ item_key: 'P1', field: 'review_note', value: 'Concurrent device note' });
  } finally {
    release({ ok: true, json: async () => ({ item_key: 'P1', title: 'Fresh context', user_note: 'Old server note' }) });
  }
  const detail = await reading;
  expect(detail.user_note).toBe('Concurrent device note');
  expect((await offline.cachedResponse('review:P1')).user_note).toBe('Concurrent device note');
});

it.each(['verdict', 'note'])('does not create an offline %s mutation after receiving a malformed server acknowledgement', async (field) => {
  const offline = await import('../offlineStore.js');
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => { throw new SyntaxError('Malformed acknowledgement'); } }));
  const api = await import('./goldenApi.js');
  const pending = field === 'verdict'
    ? api.submitVerdict({ item_key: 'P1', user_priority: 'should_read', comment: 'Intent' })
    : api.saveReviewNote({ item_key: 'P1', note: 'Intent' });
  await expect(pending).rejects.toThrow('Malformed acknowledgement');
  expect(await offline.pendingMutations()).toEqual([]);
});

it('refuses a received detail for a different paper without overwriting the owned cache', async () => {
  const offline = await import('../offlineStore.js');
  const owned = { item_key: 'P1', title: 'Retained owned context' };
  await offline.cacheResponse('review:P1', owned);
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ item_key: 'P2', title: 'Wrong paper' }) }));
  const { fetchReviewDetail } = await import('./goldenApi.js');
  await expect(fetchReviewDetail('P1')).rejects.toThrow();
  expect(await offline.cachedResponse('review:P1')).toEqual(owned);
});

it('captures explicit local intent without another blocking request after a known transport outage', async () => {
  const offline = await import('../offlineStore.js');
  await offline.publishStatus('Owned connection refused', 'unavailable');
  vi.stubGlobal('fetch', vi.fn());
  const api = await import('./goldenApi.js');
  expect(await api.submitVerdict({ item_key: 'P1', user_priority: 'could_read', comment: 'Device intent' }))
    .toMatchObject({ saved_offline: true, queued: true });
  expect(await api.saveReviewNote({ item_key: 'P1', note: 'Device note' }))
    .toMatchObject({ saved_offline: true, queued: true });
  expect(await api.deleteVerdict('P1')).toMatchObject({ saved_offline: true, queued: true });
  expect(fetch).not.toHaveBeenCalled();
  expect((await offline.pendingMutations()).map((row) => [row.field, row.operation]))
    .toEqual([['verdict', 'set'], ['review_note', 'set'], ['verdict', 'delete']]);
});

it('does not treat a protocol or HTTP block as transport-outage authority', async () => {
  const offline = await import('../offlineStore.js');
  await offline.publishStatus('HTTP refused', 'blocked');
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 403, statusText: 'Forbidden', json: async () => ({ detail: 'Forbidden' }) }));
  const api = await import('./goldenApi.js');
  await expect(api.saveReviewNote({ item_key: 'P1', note: 'Retain draft' })).rejects.toMatchObject({ status: 403 });
  expect(await offline.pendingMutations()).toEqual([]);
});

it('keeps the connected server view usable when device storage cannot open, without claiming an offline save', async () => {
  vi.spyOn(IDBFactory.prototype, 'open').mockImplementation(() => { throw new DOMException('Storage denied', 'SecurityError'); });
  const data = { item_key: 'P1', title: 'Server view', user_note: 'Server note' };
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => data }));
  const { fetchReviewDetail } = await import('./goldenApi.js');
  expect(await fetchReviewDetail('P1')).toEqual(data);
});
