// Thin fetch wrappers for the /api/zotero/*, /api/library/*, and
// /api/triage/run endpoints used by the Library and Annotate pages.

import { allPapers, cacheResponse, cachedResponse, getMeta, transportUnavailable } from '../offlineStore.js';
import { offlineRequest } from './offlineTransport.js';

async function request(path, options = {}, localFallback = false) {
  const init = { headers: { 'Content-Type': 'application/json', ...(options.headers || {}) }, ...options };
  return localFallback ? offlineRequest(path, init, readResponse) : readResponse(await fetch(path, init));
}

async function readResponse(res) {
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

export async function fetchRelatedPapers(itemKey, { signal } = {}) {
  return request(`/api/library/related/${encodeURIComponent(itemKey)}`, { signal });
}

export async function fetchDiscovery(itemKey, { signal } = {}) {
  const data = await request(`/api/library/related/${encodeURIComponent(itemKey)}/discover`, { signal });
  const hash = value => typeof value === 'string' && /^[a-f0-9]{64}$/i.test(value);
  if (data?.seed_key !== itemKey || !['saved', 'not_saved', 'unknown'].includes(data?.admission?.state)
      || (data.context_fingerprint !== null && !hash(data.context_fingerprint))) throw new Error('Invalid discovery metadata');
  if (data.admission.state === 'saved' && (!data.private_context || !data.boundary
      || !['observed', 'empty', 'partial', 'unavailable'].includes(data.private_context.status)
      || !['native', 'user_approved'].includes(data.admission.basis)
      || (data.private_context.record_count !== null && (!Number.isInteger(data.private_context.record_count) || data.private_context.record_count < 0))
      || typeof data.boundary.enabled !== 'boolean' || !hash(data.boundary.token)
      || (data.boundary.endpoint != null && typeof data.boundary.endpoint !== 'string')
      || typeof data.boundary.provider !== 'string' || typeof data.boundary.model !== 'string')) throw new Error('Invalid discovery boundary/context');
  return data;
}

export async function findNewRelevant(itemKey, { contextFingerprint, consentToken = null, retry = false, signal } = {}) {
  return request(`/api/library/related/${encodeURIComponent(itemKey)}/discover`, {
    method: 'POST', signal,
    body: JSON.stringify({ context_fingerprint: contextFingerprint, consent_token: consentToken, retry }),
  });
}

export async function assessRelatedPapers(itemKey, consentToken, retry = false) {
  return request(`/api/library/related/${encodeURIComponent(itemKey)}/assess`, {
    method: 'POST', body: JSON.stringify({ consent_token: consentToken, retry }),
  });
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
export async function updateItemTags(itemKey, { addTags = [], removeTags = [], force = false } = {}) {
  if (!itemKey) throw new Error('updateItemTags: itemKey is required');
  return request(`/api/zotero/items/${encodeURIComponent(itemKey)}/tags`, {
    method: 'POST',
    body: JSON.stringify({ add_tags: addTags, remove_tags: removeTags, force }),
  });
}

/** Direct link to a library item's stored PDF (streamed by the backend). */
export function itemPdfUrl(itemKey) {
  return `/api/library/pdf/${encodeURIComponent(itemKey)}`;
}

async function offlineReadingQueue(path, refresh, scoped) {
  if (refresh) throw new Error('Rescore needs the Mac; cached working set unchanged');
  const papers = await allPapers();
  if (!scoped && (await getMeta('paper_order')) !== null) {
    return { status: 'offline', offline: true, model_ready: false, total_unread: papers.length, items: papers };
  }
  const cached = await cachedResponse(path);
  if (!cached && scoped) throw new Error('This server filter needs the Mac. Clear the scope to open the cached working set.');
  if (cached && !Array.isArray(cached.items)) throw new Error('Invalid cached reading scope; device data preserved');
  const local = new Map(papers.map((row) => [row.item_key, row]));
  return { ...cached, status: 'offline', offline: true, model_ready: cached?.model_ready ?? false,
    items: cached ? cached.items.map((row) => ({ ...row, ...local.get(row.item_key) })) : papers,
  };
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
  const path = `/api/library/reading-queue?${qs.toString()}`;
  const scoped = Boolean(collection || tag || search.trim());
  if (transportUnavailable()) return offlineReadingQueue(path, refresh, scoped);
  try {
    const data = await request(path, {}, true);
    if (!data || !Array.isArray(data.items)) throw new Error('Invalid reading queue response');
    await cacheResponse(path, data).catch((err) => console.warn('offline queue cache unavailable', err));
    return data;
  } catch (err) {
    if (!err.transportFailure) throw err;
    return offlineReadingQueue(path, refresh, scoped);
  }
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
 * reviews that single paper (the per-paper "Run deeper review" button);
 * otherwise the top-N unread picks. Single-flight; returns { status, total,
 * completed, ... }.
 */
export async function runDeepReview({ topK = 5, itemKey = null, focusPrompt = '' } = {}) {
  const body = itemKey ? { item_key: itemKey } : { top_k: topK };
  if (focusPrompt) body.focus_prompt = focusPrompt;
  return request('/api/library/deep-review/run', {
    method: 'POST',
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(30_000),
  });
}

/**
 * GET /api/library/deep-review/status → { status, total, completed, error, started_at, progress }.
 * With `itemKey`, reports THAT paper's job (so each panel shows its own progress); without it,
 * an aggregate ("is any review running?").
 */
export async function fetchDeepReviewStatus(itemKey = null) {
  const q = itemKey ? `?item_key=${encodeURIComponent(itemKey)}` : '';
  return request(`/api/library/deep-review/status${q}`, { signal: AbortSignal.timeout(15_000) });
}

/**
 * POST /api/library/review-fleet/run { item_keys | top_k }
 * Pre-decides a reading verdict for the chosen Read-next picks in the background:
 * with `itemKeys`, EXACTLY those (the "Review cool papers" loop pins its cool set);
 * else the top-N undecided picks. The results surface on queue rows as
 * `proposed_verdict` SUGGESTIONS the human Confirms/Overrides — never auto-applied
 * labels. Single-flight: returns the in-flight status when a run is already going.
 * Resolves to { status, total, completed, proposed, error, started_at, progress }.
 */
export async function runReviewFleet({ topK = 5, itemKeys = null } = {}) {
  const body = itemKeys !== null ? { item_keys: itemKeys } : { top_k: topK };
  return request('/api/library/review-fleet/run', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

/** GET /api/library/review-fleet/status → { status, total, completed, error, started_at, progress }. */
export async function fetchReviewFleetStatus() {
  return request('/api/library/review-fleet/status');
}

/**
 * GET /api/library/university-access/status
 * → { enabled, browser_available, logged_in, login_url, ezproxy_prefix_set }.
 * Powers the Settings "University access" panel: whether the browser PDF-fetch
 * feature is on, the optional browser dep is installed, and a session is present.
 */
export async function fetchUniversityAccessStatus() {
  return request('/api/library/university-access/status');
}

/**
 * POST /api/library/university-login
 * Opens the one-time HEADED library login (background) so the review fleet's browser
 * PDF fetch can reuse the institutional session. Resolves to { started, reason? }.
 */
export async function runUniversityLogin() {
  return request('/api/library/university-login', { method: 'POST' });
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
 * Bulk: acquire arXiv/Unpaywall/PMC/OpenAlex/direct OA PDFs for library papers
 * without an attachment, then attach them natively to Zotero. Background job;
 * non-interactive, backup-first, connector-guarded. Resolves to
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

/**
 * POST /api/zotero/items/{itemKey}/collections { add, remove, force }
 * Change ONE existing library item's Zotero collection memberships in a single
 * local-sqlite write (backup-first, connector-guarded — same force handshake as
 * the syncs). `add`/`remove` are arrays of collection keys; the backend needs at
 * least one of them. Powers both the per-paper collection editor (expanded row)
 * and the bulk "Add to collection" action — so filing a Meaning-search result
 * never means leaving the app and re-finding the paper in Zotero.
 * Resolves to { updated, item_key, added, removed } | { requires_force, message }.
 */
export async function updateItemCollections(itemKey, { add = [], remove = [], force = false }) {
  return request(`/api/zotero/items/${encodeURIComponent(itemKey)}/collections`, {
    method: 'POST',
    body: JSON.stringify({
      add: add.map((collection_key) => ({ collection_key })),
      remove: remove.map((collection_key) => ({ collection_key })),
      force,
    }),
  });
}

/**
 * POST /api/zotero/items/{itemKey}/figures { force }
 * Attach the paper's already-extracted figures to its Zotero item as child image
 * attachments (so they render in Zotero next to the paper). Dedups by filename, so
 * a re-click never duplicates. Same connector force-handshake as the other writes.
 * Resolves to { updated, item_key, figures } | { updated: 0, message } | { requires_force, message }.
 */
export async function attachFiguresToZotero(itemKey, { force = false } = {}) {
  return request(`/api/zotero/items/${encodeURIComponent(itemKey)}/figures`, {
    method: 'POST',
    body: JSON.stringify({ force }),
  });
}

/** Thin "add to one collection" wrapper over {@link updateItemCollections} (bulk path). */
export async function addItemToCollection(itemKey, { collectionKey, force = false }) {
  return updateItemCollections(itemKey, { add: [collectionKey], force });
}

/**
 * GET /api/library/render/{itemKey} — paper-read artifact status + output paths.
 * Build via POST /api/library/render/{itemKey}/build.
 */
export async function fetchPaperRender(itemKey) {
  return request(`/api/library/render/${encodeURIComponent(itemKey)}`);
}

/** POST /api/library/render/{itemKey}/build { force, allow_arxiv_source, allow_acquire_missing }. */
export async function buildPaperRender(
  itemKey,
  { force = false, allowArxivSource = false, allowAcquireMissing = false } = {},
) {
  return request(`/api/library/render/${encodeURIComponent(itemKey)}/build`, {
    method: 'POST',
    body: JSON.stringify({
      force,
      allow_arxiv_source: allowArxivSource,
      allow_acquire_missing: allowAcquireMissing,
    }),
  });
}

/** URL of the generated single-file HTML brief (embedded inline in the reader
 * pane; figures load via relative URLs against the figure route). Pass the
 * render's `built_at` as `version` so the iframe src changes when the brief is
 * rebuilt (e.g. a deep review bakes in the digest) — without it the browser
 * keeps showing the cached, digest-less HTML. The backend ignores the extra
 * query param; it only busts the browser cache. */
export function paperPresentationUrl(itemKey, version) {
  const base = `/api/library/render/${encodeURIComponent(itemKey)}/presentation`;
  return version ? `${base}?v=${encodeURIComponent(version)}` : base;
}

/** URL of the PDF used to build the paper-read artifact. This may be Zotero's
 * local attachment or an Open Access/browser-acquired cache PDF. */
export function paperRenderPdfUrl(itemKey, version) {
  const base = `/api/library/render/${encodeURIComponent(itemKey)}/pdf`;
  return version ? `${base}?v=${encodeURIComponent(version)}` : base;
}

/** URL of one generated figure image (rendered natively as a thumbnail in the
 * reader pane — no iframe). `name` is a render artifact figure filename
 * (validated server-side against the per-item figures dir). */
export function paperFigureUrl(itemKey, name) {
  return `/api/library/render/${encodeURIComponent(itemKey)}/figures/${encodeURIComponent(name)}`;
}

/**
 * POST /api/library/ask { item_key, question, mode } — correctness-first Q&A
 * over structured cached review data + full text. Metadata/count questions are
 * answered deterministically; model answers require grounded evidence.
 * Resolves to the grounded answer plus citation verification and an evidence handle.
 */
export async function askPaper(itemKey, question, {
  submissionId, origin = 'typed', presetId = null, mode = 'comprehensive', history = [],
} = {}) {
  return request('/api/library/ask', {
    method: 'POST',
    body: JSON.stringify({ item_key: itemKey, question, submission_id: submissionId,
      origin, preset_id: presetId, mode, history }),
  });
}

export async function fetchReviewQuestions(itemKey) {
  return request(`/api/library/questions?${new URLSearchParams({ item_key: itemKey })}`);
}

export async function saveReviewQuestion(submission) {
  return request('/api/library/questions', {
    method: 'POST', body: JSON.stringify(submission),
  });
}
