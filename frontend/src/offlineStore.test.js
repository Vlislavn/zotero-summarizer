// @vitest-environment jsdom
import 'fake-indexeddb/auto';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

const DB_NAME = 'zotero-summarizer-offline';
const NativeDate = Date;

function deleteDb() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.deleteDatabase(DB_NAME);
    request.onsuccess = resolve;
    request.onerror = () => reject(request.error);
    request.onblocked = resolve;
  });
}

beforeEach(async () => {
  vi.resetModules();
  await deleteDb();
  const ids = ['900000000000', '500000000000', '100000000000', '700000000000'];
  vi.stubGlobal('crypto', { randomUUID: () => `00000000-0000-4000-8000-${ids.shift()}` });
  vi.stubGlobal('Date', class extends NativeDate {
    constructor(...args) { super(...(args.length ? args : ['2026-08-29T00:00:00.000Z'])); }
    static now() { return NativeDate.parse('2026-08-29T00:00:00.000Z'); }
  });
});

afterEach(() => vi.unstubAllGlobals());

it('survives an app restart offline and preserves ordered verdicts until acknowledged', async () => {
  const firstSession = await import('./offlineStore.js');
  await firstSession.savePapers([{
    item_key: 'P1', title: 'Paper', model_priority: 'should_read', revisions: { verdict: 41 },
  }]);
  await firstSession.cacheResponse('review:P1', { title: 'Paper', digest: 'Evidence' });
  await firstSession.queueMutation({ item_key: 'P1', field: 'verdict', value: 'could_read' });
  await firstSession.queueMutation({ item_key: 'P1', field: 'verdict', value: 'dont_read' });

  vi.resetModules();
  const reopened = await import('./offlineStore.js');
  const pending = await reopened.pendingMutations();

  expect(pending.map((row) => row.value)).toEqual(['could_read', 'dont_read']);
  expect(pending.map((row) => row.sequence)).toEqual([1, 2]);
  expect(pending.every((row) => row.base_revision === 41)).toBe(true);
  expect(pending.every((row) => row.model_priority === 'should_read')).toBe(true);
  expect((await reopened.allPapers())[0].verdict.user_priority).toBe('dont_read');
  expect(await reopened.cachedResponse('review:P1')).toMatchObject({
    title: 'Paper', digest: 'Evidence', verdict: { user_priority: 'dont_read' },
  });

  await reopened.applyPushResults(pending.map((row, index) => ({
    mutation_id: row.mutation_id, status: index ? 'already_applied' : 'applied',
  })));
  expect(await reopened.pendingMutations()).toEqual([]);
});

it('allocates concurrent mutations atomically and refreshes cached detail on pull', async () => {
  const offline = await import('./offlineStore.js');
  await offline.savePapers([{ item_key: 'P1', title: 'Paper', revisions: { verdict: 1, review_note: 1 } }]);
  await offline.cacheResponse('review:P1', {
    title: 'Paper', verdict: { user_priority: 'could_read' }, user_note: 'old',
  });
  await offline.applyPull({
    cursor: 2,
    papers: [{
      item_key: 'P1', title: 'Paper', verdict: { user_priority: 'must_read' },
      review_note: 'remote', revisions: { verdict: 2, review_note: 2 },
    }],
  });

  expect(await offline.cachedResponse('review:P1')).toMatchObject({
    verdict: { user_priority: 'must_read' }, user_note: 'remote',
  });
  await Promise.all([
    offline.queueMutation({ item_key: 'P1', field: 'verdict', value: 'could_read' }),
    offline.queueMutation({ item_key: 'P1', field: 'review_note', value: 'local' }),
  ]);
  const pending = await offline.pendingMutations();
  expect(pending.map((row) => row.sequence)).toEqual([1, 2]);
  expect(pending.every((row) => row.base_revision === 2)).toBe(true);
});
