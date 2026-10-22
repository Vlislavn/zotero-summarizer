import { PRIORITY_LABELS } from './utils/priorityLabels.js';
import { mergeQuestions, questionSubmissionError, sameQuestion } from './questionSubmission.js';

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
    let blocked = false;
    req.onblocked = () => {
      blocked = true;
      reject(new Error('IndexedDB open blocked; close other app tabs and retry'));
    };
    req.onsuccess = () => {
      if (blocked) { req.result.close(); return; }
      req.result.onversionchange = () => req.result.close();
      resolve(req.result);
    };
    req.onerror = () => reject(req.error);
  }).catch((error) => { database = undefined; throw error; });
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

function localQuestion(mutation) {
  return { ...mutation.value, submitted_at: mutation.created_at, saved_offline: true, sync_status: mutation.status };
}

function overlayReviewQuestions(itemKey, detail, mutations) {
  const local = mutations.filter((row) => row.field === 'review_question' && row.item_key === itemKey)
    .map(localQuestion);
  return { ...detail, questions: mergeQuestions(detail.questions, local) };
}

export async function cacheReviewDetail(itemKey, detail) {
  if (!Array.isArray(detail?.questions) || detail.questions.some((row) => questionSubmissionError(row))) {
    throw new Error('Invalid review questions array or row');
  }
  let db;
  try {
    db = await openDb();
  } catch (cacheError) {
    return { detail, cacheError, stage: 'open', localQuestionsAvailable: false };
  }
  let tx;
  try {
    tx = db.transaction(['mutations', 'meta'], 'readwrite');
  } catch (cacheError) {
    let rows;
    try {
      rows = await request(db.transaction('mutations', 'readonly').objectStore('mutations').getAll());
    } catch (readError) {
      return { detail, cacheError: readError, stage: 'read', localQuestionsAvailable: false };
    }
    return { detail: overlayReviewQuestions(itemKey, detail, rows), cacheError,
      stage: 'transaction', localQuestionsAvailable: true };
  }
  return new Promise((resolve) => {
    let merged;
    const failed = (cacheError, stage) => resolve({ detail: merged || detail, cacheError, stage,
      localQuestionsAvailable: Boolean(merged) });
    tx.oncomplete = () => resolve({ detail: merged });
    tx.onerror = (event) => failed(event.target.error || tx.error
      || new Error('IndexedDB review cache transaction failed'), merged ? 'write' : 'read');
    tx.onabort = () => failed(tx.error || new Error('IndexedDB review cache transaction aborted'), merged ? 'write' : 'read');
    let queued;
    try {
      queued = tx.objectStore('mutations').getAll();
    } catch (cacheError) {
      failed(cacheError, 'read');
      tx.abort();
      return;
    }
    queued.onsuccess = () => {
      merged = overlayReviewQuestions(itemKey, detail, queued.result);
      try {
        tx.objectStore('meta').put({ key: `response:review:${itemKey}`, value: merged });
      } catch (cacheError) {
        failed(cacheError, 'write');
        tx.abort();
      }
    };
  });
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
            questions: row.questions || [],
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
  const question = row.field === 'review_question';
  if (question) {
    const error = questionSubmissionError(row.value);
    if (error) return error;
    if (row.operation !== 'create' || row.item_key !== row.value.item_key
        || row.mutation_id !== row.value.submission_id) return 'Invalid question create mutation';
  }
  for (const [key, max, required] of [
    ['device_id', 100, true], ...(!question ? [['item_key', 500, true], ['value', 50_000, false]] : []),
    ['comment', 10_000, false], ['model_priority', 50, false],
  ]) {
    const value = row[key];
    if (!required && value == null) continue;
    if (typeof value !== 'string' || (required && !value.trim()) || [...value].length > max) {
      return `${key} must be ${required ? 'a nonblank string' : 'text'} of at most ${max} characters`;
    }
  }
  if (!['verdict', 'review_note', 'review_question'].includes(row.field)) return 'Unknown mutation field';
  if (!question && !['set', 'delete'].includes(row.operation)) return 'Unknown mutation operation';
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

export async function queueQuestionSubmission(submission) {
  const error = questionSubmissionError(submission);
  if (error) throw new Error(error);
  const payload = structuredClone(submission);
  const db = await openDb();
  await new Promise((resolve, reject) => {
    const tx = db.transaction(['papers', 'mutations', 'meta'], 'readwrite');
    const papers = tx.objectStore('papers');
    const mutations = tx.objectStore('mutations');
    const meta = tx.objectStore('meta');
    const identityKey = `question:${payload.submission_id}`;
    Promise.all([
      request(meta.get(identityKey)), request(papers.get(payload.item_key)),
      request(meta.get('mutation_sequence')), request(meta.get('device_id')),
      request(meta.get(`response:review:${payload.item_key}`)),
    ]).then(([identity, storedPaper, storedSequence, storedDevice, storedDetail]) => {
      if (identity) {
        if (!sameQuestion(identity.value, payload)) throw new Error('Submission ID has a different immutable payload');
        return;
      }
      const sequence = (storedSequence?.value || 0) + 1;
      const device = storedDevice?.value || crypto.randomUUID();
      const mutation = {
        mutation_id: payload.submission_id, device_id: device, item_key: payload.item_key,
        field: 'review_question', operation: 'create', value: payload, comment: null,
        model_priority: null, base_revision: 0, created_at: new Date().toISOString(),
        sequence, status: 'pending',
      };
      const invalid = mutationError(mutation);
      if (invalid) throw new Error(invalid);
      const row = localQuestion(mutation);
      const paper = storedPaper || { item_key: payload.item_key, title: payload.item_key, revisions: {} };
      paper.questions = mergeQuestions(paper.questions, [row]);
      const detail = storedDetail?.value || { item_key: payload.item_key, user_note: paper.review_note || '' };
      meta.put({ key: `response:review:${payload.item_key}`, value: {
        ...detail, questions: mergeQuestions(detail.questions, [row]),
      } });
      meta.put({ key: identityKey, value: payload });
      meta.put({ key: 'mutation_sequence', value: sequence });
      meta.put({ key: 'device_id', value: device });
      mutations.put(mutation);
      papers.put(paper);
    }).catch((failure) => { tx.abort(); reject(failure); });
    tx.oncomplete = resolve;
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error || new Error('IndexedDB question transaction aborted'));
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
  if (new Set(results.map((row) => row.mutation_id)).size !== results.length) {
    throw new Error('Invalid sync acknowledgement IDs; device changes preserved');
  }
  const db = await openDb();
  const tx = db.transaction(['mutations', 'meta'], 'readwrite');
  const mutationStore = tx.objectStore('mutations');
  const meta = tx.objectStore('meta');
  for (const result of results || []) {
    const pending = mutationStore.get(result.mutation_id);
    pending.onsuccess = () => {
      const mutation = pending.result;
      if (!mutation || mutation.status !== 'pending') { tx.abort(); return; }
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
  const incoming = payload.papers || [];
  const db = await openDb();
  await new Promise((resolve, reject) => {
    const tx = db.transaction(['papers', 'mutations', 'meta'], 'readwrite');
    const papers = tx.objectStore('papers');
    const mutations = tx.objectStore('mutations');
    const meta = tx.objectStore('meta');
    const existing = papers.getAll();
    const queued = mutations.getAll();
    let reads = 0;
    const reconcile = () => {
      if (++reads !== 2) return;
      const incomingKeys = new Set(incoming.map((row) => row.item_key));
      const retainedKeys = new Set(queued.result.map((row) => row.item_key));
      const pendingByKey = new Map();
      const localQuestions = new Map();
      for (const mutation of queued.result.filter((row) => row.field === 'review_question')) {
        const rows = localQuestions.get(mutation.item_key) || [];
        rows.push(localQuestion(mutation));
        localQuestions.set(mutation.item_key, rows);
      }
      for (const mutation of queued.result
        .filter((row) => row.status === 'pending' && row.field !== 'review_question')
        .sort((a, b) => (a.sequence || 0) - (b.sequence || 0)
          || a.mutation_id.localeCompare(b.mutation_id))) {
        const fields = pendingByKey.get(mutation.item_key) || {};
        fields[mutation.field] = mutation.operation === 'delete' ? null
          : mutation.field === 'verdict'
            ? { user_priority: mutation.value, comment: mutation.comment || '', source: 'user' }
            : mutation.value;
        pendingByKey.set(mutation.item_key, fields);
      }
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
          questions: mergeQuestions(row.questions, localQuestions.get(row.item_key)),
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
            questions: merged.questions,
          } });
        };
      }
      meta.put({ key: 'cursor', value: payload.cursor || 0 });
      meta.put({ key: 'last_synced_at', value: new Date().toISOString() });
    };
    existing.onsuccess = reconcile;
    queued.onsuccess = reconcile;
    tx.oncomplete = resolve;
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error || new Error('IndexedDB snapshot transaction aborted'));
  });
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

export async function publishStatus(message = '', nextConnection) {
  if (nextConnection) {
    connection = nextConnection;
    if (nextConnection !== 'syncing') syncError = nextConnection === 'unavailable' ? message : '';
  }
  let detail;
  try {
    const [rows, lastSyncedAt, papers] = await Promise.all([
      request((await store('mutations')).getAll()), getMeta('last_synced_at'),
      request((await store('papers')).count()),
    ]);
    detail = {
      pending: rows.filter((row) => row.status === 'pending').length,
      conflicts: rows.filter((row) => row.status === 'conflict'),
      rejected: rows.filter((row) => row.status === 'rejected'),
      last_synced_at: lastSyncedAt, paper_count: papers,
    };
  } catch (error) {
    detail = { pending: null, conflicts: [], rejected: [], local_error: error.message || String(error) };
  }
  window.dispatchEvent(new CustomEvent(EVENT, { detail: {
    ...detail, online: navigator.onLine, connection, sync_error: syncError, message,
  } }));
}

export function isServerAvailable() {
  return navigator.onLine && !syncError;
}

export const syncStatusEvent = EVENT;
