// @vitest-environment jsdom
import 'fake-indexeddb/auto';
import { IDBFactory } from 'fake-indexeddb';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

beforeEach(() => {
  vi.resetModules();
  vi.stubGlobal('indexedDB', new IDBFactory());
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Owned server stopped')));
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });
const key = '/api/library/reading-queue?include_read=false&limit=30&refresh=false';

it('overlays durable local edits rather than resurrecting exact-URL cached verdicts', async () => {
  const offline = await import('../offlineStore.js');
  await offline.savePapers([{ item_key: 'P1', title: 'Retained context', revisions: { verdict: 4 } }]);
  await offline.cacheResponse(key, { items: [{ item_key: 'P1', title: 'Retained context', verdict: { user_priority: 'must_read' } }] });
  await offline.queueMutation({ item_key: 'P1', field: 'verdict', value: 'dont_read' });
  const { fetchReadingQueue } = await import('./libraryApi.js');
  expect((await fetchReadingQueue()).items[0].verdict.user_priority).toBe('dont_read');
});

it.each([[[]], [[{ item_key: 'OLD', title: 'Obsolete cached member' }]]])('uses admitted unscoped membership instead of old cached members %j', async (items) => {
  const offline = await import('../offlineStore.js');
  await offline.cacheResponse(key, { items, status: 'computing', total_unread: 77, model_ready: true,
    distribution: { old: 77 }, computed_at: 'old cohort' });
  await offline.applyPull({ cursor: 9, papers: [{ item_key: 'NEW', title: 'New admitted member', revisions: {} }] });
  const { fetchReadingQueue } = await import('./libraryApi.js');
  const data = await fetchReadingQueue();
  expect(data.items.map(row => row.item_key)).toEqual(['NEW']);
  expect(data.total_unread).toBe(1);
  expect(data.distribution ?? null).toBeNull();
  expect(data.computed_at ?? null).toBeNull();
});

it('does not poll an old computing response as if the disconnected server were still working', async () => {
  const offline = await import('../offlineStore.js');
  await offline.cacheResponse(key, { status: 'computing', items: [] });
  const { fetchReadingQueue } = await import('./libraryApi.js');
  expect(await fetchReadingQueue()).toMatchObject({ status: 'offline', offline: true, items: [] });
});

it('does not silently substitute an unfiltered working set for an uncached server scope', async () => {
  const offline = await import('../offlineStore.js');
  await offline.savePapers([{ item_key: 'P1', title: 'Outside the requested scope' }]);
  const { fetchReadingQueue } = await import('./libraryApi.js');
  await expect(fetchReadingQueue({ collection: 'UNSEEN' })).rejects.toThrow();
});

it('refuses a malformed successful queue response rather than caching it as an empty working set', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, text: async () => '{"unexpected":true}' }));
  const { fetchReadingQueue } = await import('./libraryApi.js');
  await expect(fetchReadingQueue()).rejects.toThrow();
});

it('never hides an HTTP refusal behind the retained local working set', async () => {
  const offline = await import('../offlineStore.js');
  await offline.savePapers([{ item_key: 'P1', title: 'Retained paper' }]);
  fetch.mockResolvedValue({ ok: false, status: 403, statusText: 'Forbidden', text: async () => '{"detail":"Forbidden"}' });
  const { fetchReadingQueue } = await import('./libraryApi.js');
  await expect(fetchReadingQueue()).rejects.toMatchObject({ status: 403 });
});
