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
    applied_revision: 42 + index,
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

it('replaces absent snapshot rows while retaining papers with unsent mutations', async () => {
  const offline = await import('./offlineStore.js');
  await offline.savePapers([
    { item_key: 'REMOVED', title: 'Removed paper' },
    { item_key: 'PENDING', title: 'Pending paper' },
  ]);
  await offline.queueMutation({ item_key: 'PENDING', field: 'verdict', value: 'must_read' });

  await offline.applyPull({ cursor: 8, papers: [] });

  expect((await offline.allPapers()).map((paper) => paper.item_key)).toEqual(['PENDING']);
  expect((await offline.pendingMutations()).map((mutation) => mutation.item_key)).toEqual(['PENDING']);
  expect(await offline.getMeta('cursor')).toBe(8);
});

it('projects pending edits over snapshots, then accepts canonical values after conflict', async () => {
  const offline = await import('./offlineStore.js');
  await offline.savePapers([{
    item_key: 'PENDING', title: 'Paper', verdict: { user_priority: 'must_read' },
    review_note: 'server note', revisions: { verdict: 3, review_note: 2 },
  }]);
  await offline.cacheResponse('review:PENDING', {
    title: 'Paper', verdict: { user_priority: 'must_read' }, user_note: 'server note',
  });
  await offline.queueMutation({ item_key: 'PENDING', field: 'verdict', value: 'dont_read' });
  await offline.queueMutation({ item_key: 'PENDING', field: 'review_note', value: 'local note' });
  const [verdict, note] = await offline.pendingMutations();

  await offline.applyPull({ cursor: 5, papers: [{
    item_key: 'PENDING', title: 'Paper', verdict: { user_priority: 'could_read' },
    review_note: 'other device note', revisions: { verdict: 4, review_note: 3 },
  }] });

  expect((await offline.allPapers())[0]).toMatchObject({
    verdict: { user_priority: 'dont_read' }, review_note: 'local note',
    revisions: { verdict: 4, review_note: 3 },
  });
  expect(await offline.cachedResponse('review:PENDING')).toMatchObject({
    verdict: { user_priority: 'dont_read' }, user_note: 'local note',
  });

  let syncStatus;
  window.addEventListener('zs-sync-status', (event) => { syncStatus = event.detail; }, { once: true });
  await offline.applyPushResults([
    { mutation_id: verdict.mutation_id, status: 'conflict', conflict_revision: 5,
      canonical: { value: 'should_read', comment: 'remote' } },
    { mutation_id: note.mutation_id, status: 'applied', applied_revision: 4 },
  ]);
  await offline.applyPull({ cursor: 6, papers: [{
    item_key: 'PENDING', title: 'Paper', verdict: { user_priority: 'should_read', comment: 'remote' },
    review_note: 'local note', revisions: { verdict: 5, review_note: 4 },
  }] });

  expect((await offline.allPapers())[0]).toMatchObject({
    verdict: { user_priority: 'should_read' }, review_note: 'local note',
  });
  expect(await offline.cachedResponse('review:PENDING')).toMatchObject({
    verdict: { user_priority: 'should_read' }, user_note: 'local note',
  });
  expect(await offline.pendingMutations()).toEqual([]);
  expect(syncStatus.conflicts[0].status).toBe('conflict');
});
