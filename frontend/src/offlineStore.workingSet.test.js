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

const snapshot = () => ({
  item_key: 'COLD', title: 'Synced unopened paper', authors: 'A. Author', year: 2026,
  venue: 'Test journal', abstract_preview: 'Available compact source context', has_pdf: false,
  review_digest: { tldr: 'Compact synced context', executive_summary: 'Retained summary', key_findings: ['Finding'] },
  verdict: { user_priority: 'should_read', comment: 'Original judgement' },
  review_note: 'Initial manual note', revisions: { verdict: 4, review_note: 3 },
});

it('opens a previously unopened synced paper offline without a detail request cache', async () => {
  const offline = await import('./offlineStore.js');
  await offline.applyPull({ cursor: 4, papers: [snapshot()] });
  vi.resetModules();
  const { fetchReviewDetail } = await import('./api/goldenApi.js');
  const detail = await fetchReviewDetail('COLD');
  expect(detail).toMatchObject({
    title: 'Synced unopened paper', abstract: 'Available compact source context',
    deep_review: { digest: snapshot().review_digest }, user_note: 'Initial manual note',
    verdict: snapshot().verdict, offline_snapshot: true,
  });
  expect(detail.tags).toEqual([]);
  expect(detail.collections).toEqual([]);
});

it('retains a full review and local field projection when a new snapshot arrives', async () => {
  const offline = await import('./offlineStore.js');
  await offline.applyPull({ cursor: 4, papers: [snapshot()] });
  await offline.cacheResponse('review:COLD', { title: 'Full review', deep_review: { digest: { tldr: 'Full evidence' } } });
  await offline.queueMutation({ item_key: 'COLD', field: 'review_note', value: 'Device note' });
  await offline.applyPull({ cursor: 5, papers: [{ ...snapshot(), review_note: 'Remote note' }] });
  expect(await offline.cachedResponse('review:COLD')).toMatchObject({
    title: 'Full review', deep_review: { digest: { tldr: 'Full evidence' } }, user_note: 'Device note',
  });
  expect((await offline.pendingMutations())[0].base_revision).toBe(3);
});

it.each([
  ['missing papers', { cursor: 9 }],
  ['null papers', { cursor: 9, papers: null }],
  ['bad cursor', { cursor: -1, papers: [] }],
  ['duplicate keys', { cursor: 9, papers: [snapshot(), snapshot()] }],
])('refuses %s without changing the working set or cursor', async (_name, payload) => {
  const offline = await import('./offlineStore.js');
  await offline.applyPull({ cursor: 4, papers: [snapshot()] });
  const before = await offline.allPapers();
  await expect(offline.applyPull(payload)).rejects.toThrow();
  expect(await offline.allPapers()).toEqual(before);
  expect(await offline.getMeta('cursor')).toBe(4);
});

it('refuses a stale full snapshot rather than reversing a durable cursor', async () => {
  const offline = await import('./offlineStore.js');
  await offline.applyPull({ cursor: 7, papers: [snapshot()] });
  await expect(offline.applyPull({ cursor: 6, papers: [] })).rejects.toThrow();
  expect(await offline.getMeta('cursor')).toBe(7);
  expect((await offline.allPapers())[0].item_key).toBe('COLD');
});

it.each(['missing revision', 'missing canonical'])('retains pending intent for a conflict ACK with %s', async (bad) => {
  const offline = await import('./offlineStore.js');
  await offline.queueMutation({ item_key: 'COLD', field: 'verdict', value: 'could_read' });
  const [row] = await offline.pendingMutations();
  const ack = { mutation_id: row.mutation_id, status: 'conflict', conflict_revision: 8,
    canonical: { value: 'must_read', comment: 'Remote judgement' } };
  delete ack[bad === 'missing revision' ? 'conflict_revision' : 'canonical'];
  await expect(offline.applyPushResults([ack])).rejects.toThrow();
  expect(await offline.pendingMutations()).toEqual([row]);
});

it.each([true, false])('retains valid large canonical note evidence and resolves without outbound echo when keepLocal=%s', async (keepLocal) => {
  const offline = await import('./offlineStore.js');
  const canonical = 'Canonical server note '.repeat(2500);
  await offline.applyPull({ cursor: 4, papers: [{ item_key: 'COLD', title: 'Paper', review_note: 'Old note', revisions: { review_note: 4 } }] });
  await offline.queueMutation({ item_key: 'COLD', field: 'review_note', value: 'Short device note' });
  const [row] = await offline.pendingMutations();
  await offline.applyPushResults([{ mutation_id: row.mutation_id, status: 'conflict', conflict_revision: 8, canonical: { value: canonical } }]);
  let state;
  window.addEventListener(offline.syncStatusEvent, event => { state = event.detail; }, { once: true });
  await offline.publishStatus();
  expect(state.conflicts[0].canonical.value).toBe(canonical);
  await offline.resolveConflict(row.mutation_id, keepLocal);
  const [resolution] = await offline.pendingMutations();
  expect(resolution).toMatchObject({ operation: keepLocal ? 'set' : 'keep_server',
    value: keepLocal ? 'Short device note' : null, resolves_mutation_id: row.mutation_id, base_revision: 8 });
  expect(offline.mutationError(resolution)).toBeNull();
  if (!keepLocal) expect((await offline.allPapers())[0].review_note).toBe(canonical);
});

it.each(['machine_add', null])('does not upgrade canonical verdict provenance %s to human intent', async (source) => {
  const offline = await import('./offlineStore.js');
  await offline.applyPull({ cursor: 1, papers: [{ item_key: 'COLD', title: 'Paper', revisions: { verdict: 1 } }] });
  await offline.queueMutation({ item_key: 'COLD', field: 'verdict', value: 'could_read' });
  const [row] = await offline.pendingMutations();
  await offline.applyPushResults([{ mutation_id: row.mutation_id, status: 'conflict', conflict_revision: 2,
    canonical: { value: 'must_read', comment: 'Server evidence', source } }]);
  expect((await offline.allPapers())[0].verdict.source).toBe(source);
  expect((await offline.cachedResponse('review:COLD')).verdict.source).toBe(source);
});

it('retains the original device intent if a keep-server choice conflicts again', async () => {
  const offline = await import('./offlineStore.js');
  await offline.queueMutation({ item_key: 'COLD', field: 'review_note', value: 'Original device note' });
  const [original] = await offline.pendingMutations();
  await offline.applyPushResults([{ mutation_id: original.mutation_id, status: 'conflict', conflict_revision: 8,
    canonical: { value: 'Compared server note' } }]);
  await offline.resolveConflict(original.mutation_id, false);
  const [choice] = await offline.pendingMutations();
  expect(choice.original_intent).toEqual({ operation: 'set', value: 'Original device note', comment: null });
  await offline.applyPushResults([{ mutation_id: choice.mutation_id, status: 'conflict', conflict_revision: 9,
    canonical: { value: 'Newer server note' } }]);
  await offline.resolveConflict(choice.mutation_id, true);
  const [retry] = await offline.pendingMutations();
  expect(retry).toMatchObject({ operation: 'set', value: 'Original device note', base_revision: 9,
    resolves_mutation_id: choice.mutation_id });
  expect(retry.mutation_id).not.toBe(choice.mutation_id);
  expect(offline.mutationError(retry)).toBeNull();
});

it('does not quarantine pending work using an unreadable rejection acknowledgement', async () => {
  const offline = await import('./offlineStore.js');
  await offline.queueMutation({ item_key: 'COLD', field: 'review_note', value: 'Recoverable text' });
  const [row] = await offline.pendingMutations();
  await expect(offline.applyPushResults([{ mutation_id: row.mutation_id, status: 'rejected', error: { detail: 'Wrong shape' } }]))
    .rejects.toThrow();
  expect(await offline.pendingMutations()).toEqual([row]);
});

it('refuses duplicate ACKs atomically instead of retiring the same row twice', async () => {
  const offline = await import('./offlineStore.js');
  await offline.queueMutation({ item_key: 'COLD', field: 'review_note', value: 'Local note' });
  const [row] = await offline.pendingMutations();
  const ack = { mutation_id: row.mutation_id, status: 'applied', applied_revision: 8 };
  await expect(offline.applyPushResults([ack, ack])).rejects.toThrow();
  expect(await offline.pendingMutations()).toEqual([row]);
  expect(await offline.pushPredecessors([row])).toEqual([]);
});

it('rolls back a mixed ACK batch containing a foreign ID', async () => {
  const offline = await import('./offlineStore.js');
  await offline.queueMutation({ item_key: 'COLD', field: 'review_note', value: 'Local note' });
  const [row] = await offline.pendingMutations();
  await expect(offline.applyPushResults([
    { mutation_id: row.mutation_id, status: 'applied', applied_revision: 8 },
    { mutation_id: crypto.randomUUID(), status: 'applied', applied_revision: 9 },
  ])).rejects.toThrow();
  expect(await offline.pendingMutations()).toEqual([row]);
  expect(await offline.pushPredecessors([row])).toEqual([]);
});

it('keeps a conflict when invalid canonical evidence cannot form a resolution', async () => {
  const offline = await import('./offlineStore.js');
  await offline.queueMutation({ item_key: 'COLD', field: 'verdict', value: 'could_read' });
  const [row] = await offline.pendingMutations();
  await offline.applyPushResults([{ mutation_id: row.mutation_id, status: 'conflict',
    conflict_revision: 8, canonical: { value: 'must_read', comment: 'Remote' } }]);
  await expect(offline.resolveConflict('missing-conflict', false)).rejects.toThrow();
  let status;
  window.addEventListener(offline.syncStatusEvent, (event) => { status = event.detail; }, { once: true });
  await offline.publishStatus();
  expect(status.conflicts).toHaveLength(1);
  expect(status.conflicts[0].mutation_id).toBe(row.mutation_id);
});
