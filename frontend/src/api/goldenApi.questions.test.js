// @vitest-environment jsdom
import 'fake-indexeddb/auto';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

const submission = {
  submission_id: '00000000-0000-4000-8000-000000000048', item_key: 'feed:group:paper',
  question: 'What was the cohort size?', origin: 'typed', preset_id: null,
};

beforeEach(async () => {
  vi.resetModules();
  await new Promise((resolve, reject) => {
    const req = indexedDB.deleteDatabase('zotero-summarizer-offline');
    req.onsuccess = resolve;
    req.onerror = () => reject(req.error);
  });
});
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it('overlays unresolved local questions on online refetch without changing any manual/server fields', async () => {
  const offline = await import('../offlineStore.js');
  await offline.queueQuestionSubmission(submission);
  const server = { item_key: submission.item_key, user_note: 'Concurrent remote manual note',
    title: 'Paper', deep_review: { digest: 'Evidence' }, questions: [] };
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => structuredClone(server) }));
  const api = await import('./goldenApi.js');

  const detail = await api.fetchReviewDetail(submission.item_key);
  expect(detail).toEqual({ ...server, questions: [expect.objectContaining(submission)] });
  expect(await offline.cachedResponse(`review:${submission.item_key}`)).toEqual(detail);
  expect(await offline.pendingMutations()).toHaveLength(1);

  await offline.applyPushResults([{ mutation_id: submission.submission_id, status: 'rejected', error: 'payload_conflict' }]);
  expect((await api.fetchReviewDetail(submission.item_key)).questions).toEqual([
    { ...detail.questions[0], sync_status: 'rejected' },
  ]);
});

it('enriches compatible rows without acknowledging them and protects incompatible same-ID payloads', async () => {
  const offline = await import('../offlineStore.js');
  await offline.queueQuestionSubmission(submission);
  const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({
    item_key: submission.item_key, user_note: 'Keep manual', questions: [{ ...submission, submitted_at: '2026-10-08T12:00:00Z' }],
  }) });
  vi.stubGlobal('fetch', fetch);
  const api = await import('./goldenApi.js');
  const detail = await api.fetchReviewDetail(submission.item_key);
  expect(detail.questions).toEqual([{ ...submission, submitted_at: '2026-10-08T12:00:00Z' }]);
  expect(await offline.pendingMutations()).toHaveLength(1);

  fetch.mockResolvedValue({ ok: true, json: async () => ({
    item_key: submission.item_key, user_note: 'Keep manual', questions: [{ ...submission, question: 'Incompatible server text' }],
  }) });
  expect((await api.fetchReviewDetail(submission.item_key)).questions[0]).toMatchObject(submission);
  expect((await offline.pendingMutations())[0].value).toEqual(submission);
});

it('does not overwrite a concurrent durable local question between detail read and cache write', async () => {
  const offline = await import('../offlineStore.js');
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ item_key: submission.item_key, user_note: 'Manual', questions: [] }) }));
  const getAll = IDBObjectStore.prototype.getAll;
  let commit;
  let armed = true;
  vi.spyOn(IDBObjectStore.prototype, 'getAll').mockImplementation(function (...args) {
    const req = getAll.apply(this, args);
    if (this.name === 'mutations' && armed) {
      armed = false;
      req.addEventListener('success', () => { commit = offline.queueQuestionSubmission(submission); }, { once: true });
    }
    return req;
  });
  await (await import('./goldenApi.js')).fetchReviewDetail(submission.item_key);
  await commit;
  expect(await offline.cachedResponse(`review:${submission.item_key}`)).toMatchObject({
    user_note: 'Manual', questions: [expect.objectContaining(submission)],
  });
});

const serverDetail = { item_key: submission.item_key, user_note: 'Remote manual note',
  title: 'Available paper', deep_review: { digest: 'Server evidence' }, questions: [] };

it.each(['denied', 'failed', 'blocked'])('keeps valid HTTP detail readable when IndexedDB open is %s', async (failure) => {
  const error = new DOMException('IndexedDB unavailable', 'SecurityError');
  const open = vi.fn(() => {
    if (failure === 'denied') throw error;
    const req = { error };
    queueMicrotask(() => failure === 'blocked' ? req.onblocked() : req.onerror());
    return req;
  });
  vi.stubGlobal('indexedDB', { open });
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => structuredClone(serverDetail) }));
  const warning = vi.spyOn(console, 'warn').mockImplementation(() => {});

  const detail = await (await import('./goldenApi.js')).fetchReviewDetail(submission.item_key);
  expect(detail).toEqual({ ...serverDetail, offline_cache_error: {
    stage: 'open', message: failure === 'blocked'
      ? 'IndexedDB open blocked; close other app tabs and retry' : error.message,
    local_questions_available: false,
  } });
  expect(warning).toHaveBeenCalledWith('offline detail cache unavailable', expect.objectContaining({ stage: 'open' }));
  expect(detail.saved_offline).toBeUndefined();
});

it.each(['quota', 'abort', 'request'])('keeps already-read pending questions when the cache write fails: %s', async (failure) => {
  const offline = await import('../offlineStore.js');
  await offline.queueQuestionSubmission(submission);
  const before = await offline.cachedResponse(`review:${submission.item_key}`);
  const pending = await offline.pendingMutations();
  const put = IDBObjectStore.prototype.put;
  vi.spyOn(IDBObjectStore.prototype, 'put').mockImplementation(function (value, ...args) {
    if (this.name === 'meta' && value.key === `response:review:${submission.item_key}`) {
      if (failure === 'quota') throw new DOMException('Cache quota exhausted', 'QuotaExceededError');
      if (failure === 'request') return this.add(value);
      this.transaction.abort();
      return;
    }
    return put.call(this, value, ...args);
  });
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => structuredClone(serverDetail) }));
  const warning = vi.spyOn(console, 'warn').mockImplementation(() => {});

  const detail = await (await import('./goldenApi.js')).fetchReviewDetail(submission.item_key);
  expect(detail).toMatchObject({ ...serverDetail,
    questions: [expect.objectContaining(submission)],
    offline_cache_error: { stage: 'write', local_questions_available: true },
  });
  expect(warning).toHaveBeenCalledWith('offline detail cache unavailable', expect.objectContaining({ stage: 'write' }));
  expect(await offline.cachedResponse(`review:${submission.item_key}`)).toEqual(before);
  expect(await offline.pendingMutations()).toEqual(pending);
  expect(JSON.stringify(warning.mock.calls)).not.toContain(submission.question);
  expect(JSON.stringify(warning.mock.calls)).not.toContain(serverDetail.user_note);
});

it('uses a readonly question overlay when a readwrite cache transaction cannot start', async () => {
  const offline = await import('../offlineStore.js');
  await offline.queueQuestionSubmission(submission);
  const pending = await offline.pendingMutations();
  const transaction = IDBDatabase.prototype.transaction;
  vi.spyOn(IDBDatabase.prototype, 'transaction').mockImplementation(function (stores, mode, ...rest) {
    if (mode === 'readwrite' && Array.isArray(stores) && stores.includes('mutations') && stores.includes('meta')) {
      throw new DOMException('Readonly storage', 'ReadOnlyError');
    }
    return transaction.call(this, stores, mode, ...rest);
  });
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => structuredClone(serverDetail) }));
  vi.spyOn(console, 'warn').mockImplementation(() => {});

  const detail = await (await import('./goldenApi.js')).fetchReviewDetail(submission.item_key);
  expect(detail).toMatchObject({ ...serverDetail,
    questions: [expect.objectContaining(submission)],
    offline_cache_error: { stage: 'transaction', message: 'Readonly storage', local_questions_available: true },
  });
  expect(await offline.pendingMutations()).toEqual(pending);
});

it('returns server questions with explicit unavailable-overlay diagnostics when local questions cannot be read', async () => {
  const getAll = IDBObjectStore.prototype.getAll;
  vi.spyOn(IDBObjectStore.prototype, 'getAll').mockImplementation(function (...args) {
    if (this.name === 'mutations') throw new DOMException('Local question read denied', 'SecurityError');
    return getAll.apply(this, args);
  });
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({
    ...serverDetail, questions: [submission],
  }) }));
  vi.spyOn(console, 'warn').mockImplementation(() => {});

  const detail = await (await import('./goldenApi.js')).fetchReviewDetail(submission.item_key);
  expect(detail).toEqual({ ...serverDetail, questions: [submission], offline_cache_error: {
    stage: 'read', message: 'Local question read denied', local_questions_available: false,
  } });
});

it.each([undefined, null, {}, 'not an array', [null], [{}], [{ ...submission, origin: 'invalid' }]]
  .map((questions) => [questions]))(
  'rejects malformed server questions before any storage fallback: %j', async (questions) => {
    const open = vi.fn(() => { throw new DOMException('Storage denied', 'SecurityError'); });
    vi.stubGlobal('indexedDB', { open });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ...serverDetail, questions }) }));
    const warning = vi.spyOn(console, 'warn').mockImplementation(() => {});

    await expect((await import('./goldenApi.js')).fetchReviewDetail(submission.item_key))
      .rejects.toThrow(/Invalid review questions/);
    expect(open).not.toHaveBeenCalled();
    expect(warning).not.toHaveBeenCalled();
  },
);

it('does not disguise malformed HTTP-200 JSON as a network outage with cached data', async () => {
  const offline = await import('../offlineStore.js');
  await offline.queueQuestionSubmission(submission);
  const parseError = new SyntaxError('Malformed server JSON');
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => { throw parseError; } }));
  const warning = vi.spyOn(console, 'warn').mockImplementation(() => {});

  await expect((await import('./goldenApi.js')).fetchReviewDetail(submission.item_key)).rejects.toBe(parseError);
  expect(warning).not.toHaveBeenCalled();
  expect(await offline.pendingMutations()).toHaveLength(1);
});

it('keeps the original network error when the offline cache is also unavailable', async () => {
  const networkError = new TypeError('Server unreachable');
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(networkError));
  vi.stubGlobal('indexedDB', { open: () => { throw new DOMException('Storage denied', 'SecurityError'); } });
  const warning = vi.spyOn(console, 'warn').mockImplementation(() => {});

  await expect((await import('./goldenApi.js')).fetchReviewDetail(submission.item_key))
    .rejects.toMatchObject({ cause: networkError, transportFailure: true });
  expect(warning).toHaveBeenCalledWith('offline detail cache unavailable', expect.objectContaining({ stage: 'read' }));
});

it('keeps the established cached network fallback but does not hide HTTP rejections', async () => {
  const offline = await import('../offlineStore.js');
  await offline.queueQuestionSubmission(submission);
  const cached = await offline.cachedResponse(`review:${submission.item_key}`);
  const fetch = vi.fn().mockRejectedValue(new TypeError('offline'));
  vi.stubGlobal('fetch', fetch);
  const api = await import('./goldenApi.js');
  expect(await api.fetchReviewDetail(submission.item_key)).toEqual(cached);

  fetch.mockResolvedValue({ ok: false, status: 403, json: async () => ({ detail: 'Forbidden' }) });
  await expect(api.fetchReviewDetail(submission.item_key)).rejects.toMatchObject({ status: 403 });
});
