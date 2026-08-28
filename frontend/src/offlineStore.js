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
    req.onsuccess = () => resolve(req.result);
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

export async function savePapers(rows) {
  const db = await openDb();
  await new Promise((resolve, reject) => {
    const tx = db.transaction('papers', 'readwrite');
    for (const row of rows || []) tx.objectStore('papers').put(row);
    tx.oncomplete = resolve;
    tx.onerror = () => reject(tx.error);
  });
}

export async function allPapers() {
  return request((await store('papers')).getAll());
}

async function deviceId() {
  let id = await getMeta('device_id');
  if (!id) {
    id = crypto.randomUUID();
    await setMeta('device_id', id);
  }
  return id;
}

export async function queueMutation({ item_key, field, operation = 'set', value = null, comment = null }) {
  const paperStore = await store('papers');
  const paper = await request(paperStore.get(item_key)) || { item_key, title: item_key, revisions: {} };
  // ponytail: UI saves are serialized; use one meta+mutation transaction if callers enqueue concurrently.
  const sequence = (await getMeta('mutation_sequence', 0)) + 1;
  await setMeta('mutation_sequence', sequence);
  const mutation = {
    mutation_id: crypto.randomUUID(), device_id: await deviceId(), item_key, field,
    operation, value, comment, base_revision: paper.revisions?.[field] || 0,
    created_at: new Date().toISOString(), sequence, status: 'pending',
  };
  await request((await store('mutations', 'readwrite')).put(mutation));
  if (field === 'verdict') {
    paper.verdict = operation === 'delete' ? null : { user_priority: value, comment: comment || '', source: 'user' };
  } else {
    paper.review_note = operation === 'delete' ? null : value;
  }
  await request((await store('papers', 'readwrite')).put(paper));
  const detail = await cachedResponse(`review:${item_key}`);
  if (detail) {
    if (field === 'verdict') detail.verdict = paper.verdict;
    else detail.user_note = paper.review_note;
    await cacheResponse(`review:${item_key}`, detail);
  }
  await publishStatus('Saved on device');
  return { saved_offline: true, queued: true };
}

export async function pendingMutations() {
  const rows = await request((await store('mutations')).getAll());
  return rows.filter((row) => row.status === 'pending').sort((a, b) => (
    Number.isInteger(a.sequence) && Number.isInteger(b.sequence)
      ? a.sequence - b.sequence : a.created_at.localeCompare(b.created_at)
  ));
}

export async function applyPushResults(results) {
  const rows = await request((await store('mutations')).getAll());
  const byId = new Map(rows.map((row) => [row.mutation_id, row]));
  const db = await openDb();
  const tx = db.transaction('mutations', 'readwrite');
  const mutationStore = tx.objectStore('mutations');
  for (const result of results || []) {
    const mutation = byId.get(result.mutation_id);
    if (!mutation) continue;
    if (result.status === 'conflict' || result.status === 'rejected') {
      mutation.status = result.status;
      mutation.canonical = result.canonical;
      mutation.conflict_revision = result.conflict_revision;
      mutation.error = result.error;
      mutationStore.put(mutation);
      continue;
    }
    mutationStore.delete(mutation.mutation_id);
  }
  await new Promise((resolve, reject) => {
    tx.oncomplete = resolve;
    tx.onerror = () => reject(tx.error);
  });
}

export async function applyPull(payload) {
  await savePapers(payload.papers || []);
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
