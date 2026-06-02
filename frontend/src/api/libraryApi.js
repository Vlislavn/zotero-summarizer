// Thin fetch wrappers for the /api/zotero/*, /api/library/*, and
// /api/triage/run endpoints used by the Library and Annotate pages.

async function request(path, options = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  });
  const text = await res.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { _raw: text };
    }
  }
  if (!res.ok) {
    const message = (data && (data.message || data.detail))
      || `HTTP ${res.status} ${res.statusText}`;
    const err = new Error(message);
    err.status = res.status;
    err.body = data;
    throw err;
  }
  return data;
}

/** GET /api/zotero/collections — collection tree. */
export async function fetchCollections() {
  return request('/api/zotero/collections');
}

/** GET /api/zotero/tags?limit=300 — top tags by item_count. */
export async function fetchTags({ limit = 300 } = {}) {
  return request(`/api/zotero/tags?limit=${encodeURIComponent(limit)}`);
}

/** POST /api/triage/run { item_keys, queue_changes } */
export async function startTriage(itemKeys, { queueChanges = true } = {}) {
  return request('/api/triage/run', {
    method: 'POST',
    body: JSON.stringify({ item_keys: itemKeys, queue_changes: queueChanges }),
  });
}

/**
 * POST /api/zotero/items/{key}/tags { add_tags, remove_tags }
 * Apply user tags-of-interest (emoji signals like 👀/🧪/🧠, or free text)
 * straight to the Zotero item. Engagement tags also mark the paper "handled",
 * so it drops out of the reading queue on the next refresh.
 */
export async function updateItemTags(itemKey, { addTags = [], removeTags = [] } = {}) {
  if (!itemKey) throw new Error('updateItemTags: itemKey is required');
  return request(`/api/zotero/items/${encodeURIComponent(itemKey)}/tags`, {
    method: 'POST',
    body: JSON.stringify({ add_tags: addTags, remove_tags: removeTags }),
  });
}

/** Direct link to a library item's stored PDF (streamed by the backend). */
export function itemPdfUrl(itemKey) {
  return `/api/library/pdf/${encodeURIComponent(itemKey)}`;
}

/**
 * GET /api/library/reading-queue?include_read=&limit=&refresh=&collection=&tag=&search=
 * Ranked "what to read next" from the library (Stage 2). Read items (engagement
 * / veto emoji tag) are hidden unless include_read=true. Opening never rescans;
 * refresh=true is the only thing that recomputes (the "Rescore" button).
 * collection/tag/search filter the displayed rows.
 * Returns { status, items, total_unread, read_hidden, model_ready, error, computed_at, scores_stale }.
 */
export async function fetchReadingQueue({
  includeRead = false, limit = 30, refresh = false,
  collection = '', tag = '', search = '', semantic = false,
} = {}) {
  const qs = new URLSearchParams({
    include_read: String(includeRead),
    limit: String(limit),
    refresh: String(refresh),
  });
  if (collection) qs.set('collection', collection);
  if (tag) qs.set('tag', tag);
  if (search.trim()) {
    qs.set('search', search.trim());
    // "Meaning" mode → hybrid (BM25 + dense + cross-encoder rerank) on the server.
    if (semantic) qs.set('semantic', 'true');
  }
  return request(`/api/library/reading-queue?${qs.toString()}`);
}

/**
 * GET /api/library/reading-queue/status → { running, error }.
 * In-memory scoring-job state only (no Zotero read), so it's cheap to poll while
 * a Rescore is computing — instead of re-fetching the whole-library queue.
 */
export async function fetchReadingQueueStatus() {
  return request('/api/library/reading-queue/status');
}

/**
 * POST /api/library/deep-review/run { top_k | item_key }
 * Starts an on-demand full-text deep review (quality + relevance). With itemKey,
 * reviews that single paper (the per-paper "Run deeper разбор" button);
 * otherwise the top-N unread picks. Single-flight; returns { status, total,
 * completed, ... }.
 */
export async function runDeepReview({ topK = 5, itemKey = null } = {}) {
  const body = itemKey ? { item_key: itemKey } : { top_k: topK };
  return request('/api/library/deep-review/run', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

/** GET /api/library/deep-review/status → { status, total, completed, error, started_at }. */
export async function fetchDeepReviewStatus() {
  return request('/api/library/deep-review/status');
}

/**
 * POST /api/library/reject-tag { item_key }
 * Queues a Pending ❌ tag for the item (Deep Review "Remove"). The dont_read
 * verdict (drop + training) is a separate submitVerdict call.
 */
export async function queueRejectTag(itemKey) {
  if (!itemKey) throw new Error('queueRejectTag: itemKey is required');
  return request('/api/library/reject-tag', {
    method: 'POST',
    body: JSON.stringify({ item_key: itemKey }),
  });
}

/**
 * POST /api/library/sync-rel-tags { force }
 * Writes zs:rel/<band> relevance tags onto scored library items (backs up
 * first; never touches priority/manual tags). Resolves to
 *   { tagged, by_band, backup_path, failed_count } | { requires_force, message }.
 */
export async function syncRelTags({ force = false } = {}) {
  return request('/api/library/sync-rel-tags', {
    method: 'POST',
    body: JSON.stringify({ force }),
  });
}

/**
 * POST /api/library/fetch-fulltext { force }
 * Bulk: download arXiv full-text PDFs for every library paper that has an arXiv
 * link but no PDF, and attach them natively to Zotero (imported_url; uploads on
 * next sync). Background job; backup-first + connector-guarded. Resolves to
 * { status: 'started'|'running' } or { requires_force, message }.
 */
export async function fetchFulltext({ force = false } = {}) {
  return request('/api/library/fetch-fulltext', {
    method: 'POST',
    body: JSON.stringify({ force }),
  });
}

/** GET /api/library/fetch-fulltext/status → { running, progress:{done,total}, result }. */
export async function fetchFulltextStatus() {
  return request('/api/library/fetch-fulltext/status');
}

/**
 * POST /api/library/sync-score-ranks { force }
 * Stamps a WHOLE-LIBRARY goal-blended rank into every paper's Zotero Call Number
 * (zr0001…) — scorable papers first, no-abstract papers last — so you can SORT your
 * entire library by relevance in Zotero (tags only filter). Backs up first.
 * Resolves to { ranked, scored, unscored, field, backup_path, failed_count }
 * | { requires_force, message }.
 */
export async function syncScoreRanks({ force = false } = {}) {
  return request('/api/library/sync-score-ranks', {
    method: 'POST',
    body: JSON.stringify({ force }),
  });
}
