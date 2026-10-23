import { PRIORITY_LABELS } from './utils/priorityLabels.js';

const DB_NAME = 'zotero-summarizer-offline';
const EVENT = 'zs-sync-status';
let database;
let connection = 'unchecked';
let syncError = '';

function openDb() {
  database ||= new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = () => {
      req.result.createObjectStore('papers', { keyPath: 'item_key' });
      req.result.createObjectStore('mutations', { keyPath: 'mutation_id' });
      req.result.createObjectStore('meta', { keyPath: 'key' });
    };
    req.onsuccess = () => {
      req.result.onversionchange = () => req.result.close();
      resolve(req.result);
    };
    req.onerror = () => reject(req.error);
  });
  return database;
}

async function store(name, mode = 'readonly') {
  const db = await openDb();
  return db.transaction(name, mode).objectStore(name);
}

function request(req) {
  return new Promise((resolve, reject) => {
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

export async function getMeta(key, fallback = null) {
  const row = await request((await store('meta')).get(key));
  return row?.value ?? fallback;
}

export async function setMeta(key, value) {
  return request((await store('meta', 'readwrite')).put({ key, value }));
}

export async function cacheResponse(key, value) {
  return setMeta(`response:${key}`, value);
}

export async function cachedResponse(key) {
  return getMeta(`response:${key}`);
}

function orderedPending(rows) {
  return rows.filter((row) => row.status === 'pending').sort((a, b) => (
    Number.isInteger(a.sequence) && Number.isInteger(b.sequence)
      ? a.sequence - b.sequence || a.mutation_id.localeCompare(b.mutation_id)
      : a.created_at.localeCompare(b.created_at) || a.mutation_id.localeCompare(b.mutation_id)
  ));
}

function pendingFields(rows) {
  const fieldsByKey = new Map();
  for (const mutation of orderedPending(rows)) {
    if (!['verdict', 'review_note'].includes(mutation.field) || mutation.operation === 'keep_server') continue;
    const fields = fieldsByKey.get(mutation.item_key) || {};
    fields[mutation.field] = mutation.operation === 'delete' ? null
      : mutation.field === 'verdict'
        ? { user_priority: mutation.value, comment: mutation.comment || '', source: 'user' }
        : mutation.value;
    fieldsByKey.set(mutation.item_key, fields);
  }
  return fieldsByKey;
}

export async function cachedReviewDetail(itemKey) {
  const detail = await cachedResponse(`review:${itemKey}`);
  if (detail && detail.item_key !== itemKey) throw new Error('Cached review identity unavailable; refresh online. Device mutations are retained');
  return detail;
}

export async function cacheReviewDetail(itemKey, detail) {
  if (detail?.item_key !== itemKey) throw new Error('Review detail identity mismatch; device cache preserved');
  let merged = detail;
  try {
    const db = await openDb();
    await new Promise((resolve, reject) => {
      const tx = db.transaction(['mutations', 'meta'], 'readwrite');
      const queued = tx.objectStore('mutations').getAll();
      queued.onsuccess = () => {
        const fields = pendingFields(queued.result).get(itemKey) || {};
        merged = { ...detail,
          ...(Object.hasOwn(fields, 'verdict') ? { verdict: fields.verdict } : {}),
          ...(Object.hasOwn(fields, 'review_note') ? { user_note: fields.review_note } : {}),
        };
        tx.objectStore('meta').put({ key: `response:review:${itemKey}`, value: merged });
      };
      tx.oncomplete = resolve;
      tx.onerror = () => reject(tx.error);
      tx.onabort = () => reject(tx.error || new Error('Device detail cache transaction aborted'));
    });
  } catch (error) {
    if (typeof window !== 'undefined') {
      await publishStatus(`Device review cache unavailable: ${error.message || String(error)}; local change visibility is unconfirmed`, 'blocked');
    }
  }
  return merged;
}

export async function savePapers(rows, refreshDetails = false) {
  const db = await openDb();
  await new Promise((resolve, reject) => {
    const tx = db.transaction(refreshDetails ? ['papers', 'meta'] : ['papers'], 'readwrite');
    const papers = tx.objectStore('papers');
    const meta = refreshDetails ? tx.objectStore('meta') : null;
    for (const row of rows || []) {
      papers.put(row);
      if (meta) {
        const key = `response:review:${row.item_key}`;
        const cached = meta.get(key);
        cached.onsuccess = () => {
          if (!cached.result?.value) return;
          meta.put({ key, value: {
            ...cached.result.value, verdict: row.verdict, user_note: row.review_note,
          } });
        };
      }
    }
    tx.oncomplete = resolve;
    tx.onerror = () => reject(tx.error);
  });
}

export async function allPapers() {
  const [rows, keys] = await Promise.all([
    request((await store('papers')).getAll()), getMeta('paper_order', []),
  ]);
  const order = new Map(keys.map((key, index) => [key, index]));
  return rows.sort((a, b) => (order.get(a.item_key) ?? Infinity) - (order.get(b.item_key) ?? Infinity));
}

export function mutationError(row) {
  for (const [key, max, required] of [
    ['device_id', 100, true], ['item_key', 500, true], ['value', 50_000, false],
    ['comment', 10_000, false], ['model_priority', 50, false],
  ]) {
    const value = row[key];
    if (!required && value == null) continue;
    if (typeof value !== 'string' || (required && !value.trim()) || [...value].length > max) {
      return `${key} must be ${required ? 'a nonblank string' : 'text'} of at most ${max} characters`;
    }
  }
  if (!['verdict', 'review_note'].includes(row.field)) return 'Unknown mutation field';
  if (!['set', 'delete', 'keep_server'].includes(row.operation)) return 'Unknown mutation operation';
  if (row.operation === 'keep_server' && (!row.resolves_mutation_id || row.value !== null || row.comment != null)) {
    return 'keep_server requires a conflict reference and no new content';
  }
  if (row.operation === 'set' && row.field === 'verdict' && !Object.hasOwn(PRIORITY_LABELS, row.value)) {
    return 'verdict must be a reading priority';
  }
  if (row.operation === 'set' && row.field === 'review_note' && typeof row.value !== 'string') {
    return 'review_note must be text';
  }
  if (!Number.isSafeInteger(row.base_revision) || row.base_revision < 0) return 'Invalid base revision';
  const uuid = /^[\da-f]{8}-[\da-f]{4}-[\da-f]{4}-[\da-f]{4}-[\da-f]{12}$/i;
  if (!uuid.test(row.mutation_id) || (row.resolves_mutation_id != null && !uuid.test(row.resolves_mutation_id))) {
    return 'Invalid mutation UUID';
  }
  if (typeof row.created_at !== 'string' || !Number.isFinite(Date.parse(row.created_at))
      || new Date(row.created_at).toISOString() !== row.created_at) return 'Invalid creation date';
  return null;
}

function acknowledgementKey(row) {
  return `ack:${JSON.stringify([row.device_id, row.item_key, row.field])}`;
}

export async function pushPredecessors(mutations) {
  const receipts = await Promise.all(mutations.map((row) => getMeta(acknowledgementKey(row))));
  return [...new Set(receipts.filter(Boolean).map((row) => row.mutation_id))];
}

export async function queueMutation({ item_key, field, operation = 'set', value = null, comment = null }) {
  if (typeof item_key !== 'string' || !item_key.trim()) throw new Error('item_key must be nonblank');
  const db = await openDb();
  await new Promise((resolve, reject) => {
    const tx = db.transaction(['papers', 'mutations', 'meta'], 'readwrite');
    const papers = tx.objectStore('papers');
    const mutations = tx.objectStore('mutations');
    const meta = tx.objectStore('meta');
    Promise.all([
      request(papers.get(item_key)), request(meta.get('mutation_sequence')),
      request(meta.get('device_id')), request(meta.get(`response:review:${item_key}`)),
    ]).then(([storedPaper, storedSequence, storedDevice, storedDetail]) => {
      const paper = storedPaper || { item_key, title: item_key, revisions: {} };
      const sequence = (storedSequence?.value || 0) + 1;
      const device = storedDevice?.value || crypto.randomUUID();
      const mutation = {
        mutation_id: crypto.randomUUID(), device_id: device, item_key, field,
        operation, value, comment, model_priority: paper.model_priority || paper.reading_priority || null,
        base_revision: paper.revisions?.[field] || 0,
        created_at: new Date().toISOString(), sequence, status: 'pending',
      };
      const error = mutationError(mutation);
      if (error) throw new Error(error);
      if (field === 'verdict') {
        paper.verdict = operation === 'delete' ? null : { user_priority: value, comment: comment || '', source: 'user' };
      } else {
        paper.review_note = operation === 'delete' ? null : value;
      }
      const detail = storedDetail?.value;
      if (detail) {
        if (field === 'verdict') detail.verdict = paper.verdict;
        else detail.user_note = paper.review_note;
        meta.put({ key: `response:review:${item_key}`, value: detail });
      }
      meta.put({ key: 'mutation_sequence', value: sequence });
      meta.put({ key: 'device_id', value: device });
      mutations.put(mutation);
      papers.put(paper);
    }).catch((error) => { tx.abort(); reject(error); });
    tx.oncomplete = resolve;
    tx.onerror = () => reject(tx.error);
  });
  await publishStatus('Saved on device');
  window.dispatchEvent(new Event('zs-sync-request'));
  return { saved_offline: true, queued: true };
}

export async function pendingMutations() {
  const rows = await request((await store('mutations')).getAll());
  return orderedPending(rows);
}

export async function applyPushResults(results) {
  if (!Array.isArray(results) || results.some((r) => !r
      || !['applied', 'already_applied', 'conflict', 'rejected'].includes(r.status))) {
    throw new Error('Invalid sync acknowledgement status; device changes preserved');
  }
  if (results.some((r) => ['applied', 'already_applied'].includes(r.status)
      && (!Number.isSafeInteger(r.applied_revision) || r.applied_revision < 0))) {
    throw new Error('Invalid sync acknowledgement revision; device changes preserved');
  }
  const ids = new Set(results.map((r) => r.mutation_id));
  if (ids.size !== results.length) {
    throw new Error('Invalid sync acknowledgement IDs; device changes preserved');
  }
  if (results.some((r) => r.status === 'conflict'
      && (!Number.isSafeInteger(r.conflict_revision) || r.conflict_revision < 0
        || !r.canonical || typeof r.canonical !== 'object' || !Object.hasOwn(r.canonical, 'value')))) {
    throw new Error('Invalid sync conflict acknowledgement; device changes preserved');
  }
  if (results.some((r) => r.status === 'rejected' && typeof r.error !== 'string')) {
    throw new Error('Invalid sync rejection acknowledgement; device changes preserved');
  }
  const db = await openDb();
  const tx = db.transaction(['mutations', 'meta', 'papers'], 'readwrite');
  const mutationStore = tx.objectStore('mutations');
  const meta = tx.objectStore('meta');
  const projectedPapers = new Map();
  const projectedDetails = new Map();
  let remaining = new Map();
  const queued = mutationStore.getAll();
  queued.onsuccess = () => {
    remaining = pendingFields(queued.result.filter((row) => !ids.has(row.mutation_id)));
  };
  for (const result of results || []) {
    const pending = mutationStore.get(result.mutation_id);
    pending.onsuccess = () => {
      const mutation = pending.result;
      if (!mutation || mutation.status !== 'pending') { tx.abort(); return; }
      if (result.status === 'conflict') {
        const { value, comment } = result.canonical;
        const valid = value === null || (mutation.field === 'verdict'
          ? Object.hasOwn(PRIORITY_LABELS, value) : typeof value === 'string');
        if (!valid || (comment != null && typeof comment !== 'string')
            || (result.canonical.source != null && typeof result.canonical.source !== 'string')) { tx.abort(); return; }
        const optimistic = remaining.get(mutation.item_key) || {};
        const projected = Object.hasOwn(optimistic, mutation.field) ? optimistic[mutation.field]
          : mutation.field === 'verdict' && value !== null
            ? { user_priority: value, comment: comment ?? '', source: result.canonical.source ?? null } : value;
        const papers = tx.objectStore('papers');
        const paper = papers.get(mutation.item_key);
        paper.onsuccess = () => {
          const current = projectedPapers.get(mutation.item_key) || paper.result;
          if (!current) return;
          const updated = { ...current, [mutation.field]: projected,
            revisions: { ...current.revisions, [mutation.field]: result.conflict_revision } };
          projectedPapers.set(mutation.item_key, updated);
          papers.put(updated);
        };
        const key = `response:review:${mutation.item_key}`;
        const cached = meta.get(key);
        cached.onsuccess = () => {
          const current = projectedDetails.get(key) || cached.result?.value;
          if (!current) return;
          const value = { ...current, [mutation.field === 'verdict' ? 'verdict' : 'user_note']: projected };
          projectedDetails.set(key, value);
          meta.put({ key, value });
        };
      }
      if (result.status === 'conflict' || result.status === 'rejected') {
        mutationStore.put({ ...mutation, status: result.status, canonical: result.canonical,
          conflict_revision: result.conflict_revision, error: result.error });
        return;
      }
      const key = acknowledgementKey(mutation);
      const prior = meta.get(key);
      prior.onsuccess = () => {
        if ((prior.result?.value?.applied_revision ?? -1) > result.applied_revision) return;
        meta.put({ key, value: { mutation_id: mutation.mutation_id, applied_revision: result.applied_revision } });
      };
      mutationStore.delete(mutation.mutation_id);
    };
  }
  await new Promise((resolve, reject) => {
    tx.oncomplete = resolve;
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error || new Error('IndexedDB acknowledgement transaction aborted'));
  });
}

export async function applyPull(payload) {
  if (!Number.isSafeInteger(payload?.cursor) || payload.cursor < 0 || !Array.isArray(payload.papers)
      || payload.papers.some((row) => !row || typeof row.item_key !== 'string' || !row.item_key.trim())
      || new Set(payload.papers.map((row) => row.item_key)).size !== payload.papers.length) {
    throw new Error('Invalid sync snapshot; device state preserved');
  }
  const incoming = payload.papers;
  const db = await openDb();
  await new Promise((resolve, reject) => {
    const tx = db.transaction(['papers', 'mutations', 'meta'], 'readwrite');
    const papers = tx.objectStore('papers');
    const mutations = tx.objectStore('mutations');
    const meta = tx.objectStore('meta');
    const existing = papers.getAll();
    const queued = mutations.getAll();
    const cursor = meta.get('cursor');
    let reads = 0;
    const reconcile = () => {
      if (++reads !== 3) return;
      const current = cursor.result?.value ?? 0;
      if (!Number.isSafeInteger(current) || current < 0 || payload.cursor < current) { tx.abort(); return; }
      const incomingKeys = new Set(incoming.map((row) => row.item_key));
      const retainedKeys = new Set(queued.result.map((row) => row.item_key));
      const pendingByKey = pendingFields(queued.result);
      for (const row of existing.result) {
        if (!incomingKeys.has(row.item_key)) {
          if (retainedKeys.has(row.item_key)) continue;
          papers.delete(row.item_key);
        }
      }
      for (const row of incoming) {
        const optimistic = pendingByKey.get(row.item_key) || {};
        const merged = {
          ...row,
          ...(Object.hasOwn(optimistic, 'verdict') ? { verdict: optimistic.verdict } : {}),
          ...(Object.hasOwn(optimistic, 'review_note') ? { review_note: optimistic.review_note } : {}),
        };
        papers.put(merged);
        const key = `response:review:${row.item_key}`;
        const cached = meta.get(key);
        cached.onsuccess = () => {
          const previous = cached.result?.value;
          const detail = previous && !previous.offline_snapshot ? previous : {
            item_key: row.item_key, title: row.title, authors: row.authors, year: row.year,
            venue: row.venue, abstract: row.abstract_preview, has_pdf: row.has_pdf,
            deep_review: row.review_digest ? { digest: row.review_digest } : null,
            tags: [], collections: [], offline_snapshot: true,
          };
          meta.put({ key, value: {
            ...detail, verdict: merged.verdict, user_note: merged.review_note,
          } });
        };
      }
      meta.put({ key: 'cursor', value: payload.cursor });
      meta.put({ key: 'paper_order', value: incoming.map((row) => row.item_key) });
      meta.put({ key: 'last_synced_at', value: new Date().toISOString() });
    };
    existing.onsuccess = reconcile;
    queued.onsuccess = reconcile;
    cursor.onsuccess = reconcile;
    tx.oncomplete = resolve;
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error || new Error('IndexedDB snapshot transaction aborted'));
  });
  await publishStatus();
}

export async function resolveConflict(mutationId, keepLocal) {
  const db = await openDb();
  await new Promise((resolve, reject) => {
    const tx = db.transaction(['mutations', 'meta'], 'readwrite');
    const mutations = tx.objectStore('mutations');
    const meta = tx.objectStore('meta');
    Promise.all([request(mutations.get(mutationId)), request(meta.get('mutation_sequence'))])
      .then(([conflict, sequence]) => {
        if (!conflict || conflict.status !== 'conflict' || !conflict.canonical
            || !Object.hasOwn(conflict.canonical, 'value')) {
          throw new Error('Retained conflict unavailable; no resolution saved');
        }
        if (conflict.operation === 'keep_server' && !conflict.original_intent) {
          throw new Error('Original device intent unavailable; no resolution saved');
        }
        const intent = conflict.original_intent || conflict;
        const resolution = {
          ...conflict, mutation_id: crypto.randomUUID(),
          operation: keepLocal ? intent.operation : 'keep_server',
          value: keepLocal ? intent.value : null,
          comment: keepLocal ? intent.comment : null,
          base_revision: conflict.conflict_revision, resolves_mutation_id: conflict.mutation_id,
          created_at: new Date().toISOString(), sequence: (sequence?.value ?? 0) + 1, status: 'pending',
        };
        const error = mutationError(resolution);
        if (error || !Number.isSafeInteger(resolution.sequence) || resolution.sequence < 1) {
          throw new Error(error || 'Invalid device mutation sequence');
        }
        delete resolution.canonical;
        delete resolution.conflict_revision;
        delete resolution.original_intent;
        if (!keepLocal) resolution.original_intent = {
          operation: intent.operation, value: intent.value, comment: intent.comment,
        };
        mutations.delete(conflict.mutation_id);
        mutations.put(resolution);
        meta.put({ key: 'mutation_sequence', value: resolution.sequence });
      }).catch((error) => { tx.abort(); reject(error); });
    tx.oncomplete = resolve;
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error || new Error('Resolution transaction aborted; both versions retained'));
  });
  await publishStatus();
}

export async function publishStatus(message = '', phase) {
  if (phase) {
    connection = phase;
    if (phase !== 'syncing') syncError = ['unavailable', 'blocked'].includes(phase) ? message : '';
  }
  let local;
  try {
    const [rows, lastSyncedAt, papers] = await Promise.all([
      request((await store('mutations')).getAll()), getMeta('last_synced_at'),
      request((await store('papers')).count()),
    ]);
    local = { pending: rows.filter((row) => row.status === 'pending').length,
      conflicts: rows.filter((row) => row.status === 'conflict'),
      rejected: rows.filter((row) => row.status === 'rejected'),
      last_synced_at: lastSyncedAt, paper_count: papers };
  } catch (error) {
    local = { pending: null, conflicts: [], rejected: [], local_error: error.message || String(error) };
  }
  window.dispatchEvent(new CustomEvent(EVENT, { detail: {
    ...local, online: navigator.onLine, connection, sync_error: syncError, message,
  } }));
}

export function isServerAvailable() {
  return navigator.onLine && connection === 'connected';
}

export function transportUnavailable() {
  return connection === 'unavailable';
}

export const syncStatusEvent = EVENT;
