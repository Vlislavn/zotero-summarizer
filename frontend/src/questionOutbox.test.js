// @vitest-environment jsdom
import 'fake-indexeddb/auto';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ request: vi.fn() }));
vi.mock('./api/client.js', () => ({ request: mocks.request }));

const DB_NAME = 'zotero-summarizer-offline';
function deleteDb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.deleteDatabase(DB_NAME);
    req.onsuccess = resolve;
    req.onerror = () => reject(req.error);
    req.onblocked = resolve;
  });
}

beforeEach(async () => {
  vi.resetModules();
  mocks.request.mockReset();
  await deleteDb();
  vi.stubGlobal('crypto', { randomUUID: () => '00000000-0000-4000-8000-000000000099' });
  Object.defineProperty(navigator, 'onLine', { configurable: true, value: true });
});
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it('replays a lost-response question after restart with its immutable UUID and never reruns QA', async () => {
  const submission = {
    submission_id: '00000000-0000-4000-8000-000000000048',
    item_key: 'feed:group:paper', question: 'What was the cohort size?',
    origin: 'preset', preset_id: 'cohort_sample_size',
  };
  const firstSession = await import('./offlineStore.js');
  await firstSession.queueQuestionSubmission(submission);
  const [original] = await firstSession.pendingMutations();
  expect(original).toMatchObject({
    mutation_id: submission.submission_id, item_key: submission.item_key,
    field: 'review_question', operation: 'create', value: submission, status: 'pending',
  });

  await firstSession.queueQuestionSubmission(submission);
  await expect(firstSession.queueQuestionSubmission({ ...submission, question: 'changed' }))
    .rejects.toThrow(/different payload|immutable/i);
  expect(await firstSession.pendingMutations()).toEqual([original]);
  expect(await firstSession.cachedResponse('review:feed:group:paper')).toMatchObject({
    questions: [expect.objectContaining({ submission_id: submission.submission_id, question: submission.question })],
  });

  // The prior Ask response was lost: no receipt was applied before the app restarted.
  vi.resetModules();
  const reopened = await import('./offlineStore.js');
  mocks.request.mockImplementation(async (path, options) => {
    if (path === '/api/sync/push') {
      const body = JSON.parse(options.body);
      return { protocol: 1, results: body.mutations.map((row) => ({
        mutation_id: row.mutation_id, status: 'already_applied', applied_revision: 1,
      })) };
    }
    if (path.startsWith('/api/sync/pull')) {
      return { protocol: 1, cursor: 1, papers: [{
        item_key: submission.item_key, questions: [{ ...submission, submitted_at: '2026-10-08T12:00:00Z' }],
      }] };
    }
    throw new Error(`Unexpected network path: ${path}`);
  });
  const client = { invalidateQueries: vi.fn().mockResolvedValue() };

  await (await import('./syncClient.js')).syncNow(client);

  expect(mocks.request.mock.calls.map(([path]) => path)).toEqual([
    '/api/sync/push', expect.stringMatching(/^\/api\/sync\/pull\?/),
  ]);
  expect(JSON.parse(mocks.request.mock.calls[0][1].body).mutations).toEqual([original]);
  expect(await reopened.pendingMutations()).toEqual([]);
  expect(await reopened.cachedResponse('review:feed:group:paper')).toMatchObject({
    questions: [expect.objectContaining({ submission_id: submission.submission_id })],
  });
  await reopened.queueQuestionSubmission(submission);
  expect(await reopened.pendingMutations()).toEqual([]);
});

const submission = {
  submission_id: '00000000-0000-4000-8000-000000000048',
  item_key: 'feed:group:paper', question: 'What was the cohort size?', origin: 'typed', preset_id: null,
};

it('atomically commits distinct concurrent questions without replacing the manual note', async () => {
  const offline = await import('./offlineStore.js');
  await offline.savePapers([{ item_key: submission.item_key, review_note: 'Manual note' }]);
  await offline.cacheResponse(`review:${submission.item_key}`, { user_note: 'Manual note', digest: 'Evidence' });
  await Promise.all([
    offline.queueQuestionSubmission(submission),
    offline.queueQuestionSubmission({ ...submission, submission_id: '00000000-0000-4000-8000-000000000049' }),
  ]);

  expect((await offline.pendingMutations()).map((row) => row.sequence)).toEqual([1, 2]);
  const detail = await offline.cachedResponse(`review:${submission.item_key}`);
  expect(detail.user_note).toBe('Manual note');
  expect(detail.digest).toBe('Evidence');
  expect(detail.questions).toHaveLength(2);
});

it('rolls back the question, identity, sequence and outbox on transaction abort', async () => {
  const offline = await import('./offlineStore.js');
  await offline.savePapers([{ item_key: submission.item_key, review_note: 'Manual note' }]);
  await offline.cacheResponse(`review:${submission.item_key}`, { user_note: 'Manual note', questions: [] });
  const put = IDBObjectStore.prototype.put;
  vi.spyOn(IDBObjectStore.prototype, 'put').mockImplementation(function (value, ...rest) {
    if (this.name === 'papers') { this.transaction.abort(); return; }
    return put.call(this, value, ...rest);
  });

  await expect(offline.queueQuestionSubmission(submission)).rejects.toThrow(/aborted/);
  expect(await offline.pendingMutations()).toEqual([]);
  expect(await offline.getMeta(`question:${submission.submission_id}`)).toBeNull();
  expect(await offline.getMeta('mutation_sequence')).toBeNull();
  expect(await offline.cachedResponse(`review:${submission.item_key}`)).toEqual({ user_note: 'Manual note', questions: [] });
  expect((await offline.allPapers())[0]).toEqual({ item_key: submission.item_key, review_note: 'Manual note' });
});

it.each(['.', '..', 'a/b', 'a\\b', 'nul\u0000key', '   '])('rejects the invalid component key %j before commit', async (item_key) => {
  const offline = await import('./offlineStore.js');
  await expect(offline.queueQuestionSubmission({ ...submission, item_key })).rejects.toThrow(/paper key/);
  expect(await offline.pendingMutations()).toEqual([]);
});

it('retains whitespace, Unicode and unbounded component/preset keys and accepts 2000 Unicode code points', async () => {
  const offline = await import('./offlineStore.js');
  const row = { ...submission, item_key: `  feed:группа:${'x'.repeat(501)}  `,
    question: '😀'.repeat(2000), origin: 'preset', preset_id: 'p'.repeat(501) };
  await offline.queueQuestionSubmission(row);
  expect((await offline.pendingMutations())[0].value).toEqual(row);
  expect(offline.mutationError((await offline.pendingMutations())[0])).toBeNull();
});

it('preserves local question identities through canonical/alias snapshots and rejected receipts', async () => {
  const offline = await import('./offlineStore.js');
  await offline.queueQuestionSubmission(submission);
  const [original] = await offline.pendingMutations();
  await offline.applyPull({ cursor: 1, papers: [
    { item_key: submission.item_key, review_note: 'Remote note', questions: [] },
    { item_key: 'CANONICAL', questions: [{ ...submission, item_key: 'CANONICAL' }] },
  ] });
  expect(await offline.pendingMutations()).toEqual([original]);
  expect(await offline.cachedResponse(`review:${submission.item_key}`)).toMatchObject({
    user_note: 'Remote note', questions: [expect.objectContaining(submission)],
  });
  await offline.applyPushResults([{ mutation_id: submission.submission_id, status: 'rejected', error: 'payload_conflict' }]);
  await offline.applyPull({ cursor: 2, papers: [
    { item_key: submission.item_key, review_note: 'Another note', questions: [{ ...submission, question: 'Changed server payload' }] },
  ] });
  expect(await offline.cachedResponse(`review:${submission.item_key}`)).toMatchObject({
    user_note: 'Another note', questions: [expect.objectContaining(submission)],
  });
});

it.each([
  [{ mutation_id: submission.submission_id, status: 'unknown', applied_revision: 1 }],
  [{ mutation_id: submission.submission_id, status: 'applied', applied_revision: null }],
  [{ mutation_id: '00000000-0000-4000-8000-000000000050', status: 'applied', applied_revision: 1 }],
])('does not retire a question for malformed sync results %j', async (results) => {
  const offline = await import('./offlineStore.js');
  await offline.queueQuestionSubmission(submission);
  const [original] = await offline.pendingMutations();
  mocks.request.mockResolvedValue({ protocol: 1, results });

  await (await import('./syncClient.js')).syncNow();
  expect(await offline.pendingMutations()).toEqual([original]);
  expect(mocks.request).toHaveBeenCalledOnce();
});

it('rejects a blocked IndexedDB open instead of claiming a saved question', async () => {
  vi.stubGlobal('indexedDB', { open: () => {
    const req = {};
    queueMicrotask(() => req.onblocked());
    return req;
  } });
  const offline = await import('./offlineStore.js');
  await expect(offline.queueQuestionSubmission(submission)).rejects.toThrow(/blocked/);
});
