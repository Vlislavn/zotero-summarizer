import { PRIORITY_LABELS } from './utils/priorityLabels.js';

const DB_NAME = 'zotero-summarizer-offline';
const EVENT = 'zs-sync-status';
let database;

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
  return request((await store('papers')).getAll());
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
  if (!['set', 'delete'].includes(row.operation)) return 'Unknown mutation operation';
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
  return { saved_offline: true, queued: true };
}

export async function pendingMutations() {
  const rows = await request((await store('mutations')).getAll());
  return rows.filter((row) => row.status === 'pending').sort((a, b) => (
    Number.isInteger(a.sequence) && Number.isInteger(b.sequence)
      ? a.sequence - b.sequence || a.mutation_id.localeCompare(b.mutation_id)
      : a.created_at.localeCompare(b.created_at) || a.mutation_id.localeCompare(b.mutation_id)
  ));
}

export async function applyPushResults(results) {
  if (!Array.isArray(results) || results.some((r) => !['applied', 'already_applied', 'conflict', 'rejected'].includes(r.status))) {
    throw new Error('Invalid sync acknowledgement status; device changes preserved');
  }
  if (results.some((r) => ['applied', 'already_applied'].includes(r.status)
      && (!Number.isSafeInteger(r.applied_revision) || r.applied_revision < 0))) {
    throw new Error('Invalid sync acknowledgement revision; device changes preserved');
  }
  const db = await openDb();
  const tx = db.transaction(['mutations', 'meta'], 'readwrite');
  const mutationStore = tx.objectStore('mutations');
  const meta = tx.objectStore('meta');
  for (const result of results || []) {
    const pending = mutationStore.get(result.mutation_id);
    pending.onsuccess = () => {
      const mutation = pending.result;
      if (!mutation) return;
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
  await savePapers(payload.papers || [], true);
  await setMeta('cursor', payload.cursor || 0);
  await publishStatus();
}

export async function resolveConflict(mutationId, keepLocal) {
  const conflict = await request((await store('mutations')).get(mutationId));
  if (!conflict || conflict.status !== 'conflict') return;
  const canonical = conflict.canonical || {};
  const resolution = {
    ...conflict,
    mutation_id: crypto.randomUUID(),
    operation: keepLocal ? conflict.operation : (canonical.value == null ? 'delete' : 'set'),
    value: keepLocal ? conflict.value : canonical.value,
    comment: keepLocal ? conflict.comment : canonical.comment,
    base_revision: conflict.conflict_revision,
    resolves_mutation_id: conflict.mutation_id,
    created_at: new Date().toISOString(),
    status: 'pending',
  };
  delete resolution.canonical;
  delete resolution.conflict_revision;
  const db = await openDb();
  await new Promise((resolve, reject) => {
    const tx = db.transaction('mutations', 'readwrite');
    tx.objectStore('mutations').delete(conflict.mutation_id);
    tx.objectStore('mutations').put(resolution);
    tx.oncomplete = resolve;
    tx.onerror = () => reject(tx.error);
  });
  await publishStatus();
}

export async function publishStatus(message = '') {
  const rows = await request((await store('mutations')).getAll());
  window.dispatchEvent(new CustomEvent(EVENT, { detail: {
    online: navigator.onLine,
    pending: rows.filter((row) => row.status === 'pending').length,
    conflicts: rows.filter((row) => row.status === 'conflict'),
    rejected: rows.filter((row) => row.status === 'rejected'),
    message,
  } }));
}

export const syncStatusEvent = EVENT;
